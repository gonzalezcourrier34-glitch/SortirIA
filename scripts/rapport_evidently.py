"""Rapport Evidently mensuel : le mois écoulé comparé au mois précédent (27/10/2026).

**Provenance** : nouveau script écrit pour SortirIA. Le projet 5 (scoring
crédit) suivait la dérive avec Evidently
(`reference/p5_monitoring/app/services/analysis_services/evidently_service.py`,
même API : `Report([DataDriftPreset()])`). SortirIA suit déjà la dérive
chaque nuit par le PSI (`agent/drift.py`, semaine contre 28 jours) ; ce
rapport mensuel le complète par un rapport Evidently complet, lisible dans un
navigateur, sur deux jeux :

- **questions** (une ligne par question, sans mes questions de test) : zone,
  public, catégorie, moment, gratuit, type de réponse (mêmes règles que
  `agent/drift.py` et le tableau Grafana « Recherche »), réponse sans Mistral,
  latence ;
- **collecte** (une ligne par nuit) : événements extraits, taux de rejet,
  score du juge de qualité, coût, taille de la base.

Evidently n'est **ni dans l'image de production, ni dans `uv.lock`** : ses
dépendances (plotly < 6, statsmodels...) ne doivent pas peser sur celles du
service. Il s'installe à part, d'après `monitoring/requirements-evidently.txt`,
dans le workflow `.github/workflows/evidently.yml` (le 1er de chaque mois) ou
dans un environnement dédié sur le poste. Pour la même raison, ce script
n'importe rien du reste du projet (pandas, psycopg2 et python-dotenv seulement).

Garde-fou faible trafic, comme le PSI : sous `MIN_QUESTIONS` questions (ou
`MIN_NIGHTS` nuits) dans l'un des deux mois, le jeu n'est pas comparé ; le
rapport le dit au lieu de produire une dérive qui ne veut rien dire.

Utilisation (lecture seule ; `DATABASE_URL` : le compte `grafana_lecture`),
dans un environnement à part :

    python -m venv .venv-evidently
    .venv-evidently/Scripts/pip install -r monitoring/requirements-evidently.txt
    .venv-evidently/Scripts/python -m scripts.rapport_evidently
    .venv-evidently/Scripts/python -m scripts.rapport_evidently --mois 2026-10

Sorties dans le dossier choisi : `questions.html`, `collecte.html` (rapports
Evidently, ou une page « pas assez de données »), `resume.json` et
`resume.md` (affiché dans le résumé du workflow GitHub).
"""

from __future__ import annotations

import argparse
import html
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

PARIS = ZoneInfo("Europe/Paris")
# Copie de src/event/event_repository.py (NOT_A_TEST_QUESTION) : le script
# n'importe pas le projet ; un test vérifie que les deux restent identiques.
NOT_A_TEST_QUESTION = "btrim(question) NOT LIKE '[t%%'"

MIN_QUESTIONS = 100      # questions par mois en dessous desquelles on ne compare pas
MIN_NIGHTS = 14          # nuits de collecte par mois en dessous desquelles on ne compare pas
DEFAULT_OUTPUT = Path("rapports_evidently")

QUESTION_CATEGORIES = ["zone", "public", "categorie", "moment", "gratuit", "type_de_reponse", "sans_mistral"]
QUESTION_NUMBERS = ["latence_ms"]
COLLECT_NUMBERS = ["evenements_extraits", "taux_rejet", "score_juge", "cout_usd", "taille_base"]


# ---------------------------------------------------------------------------
# Les deux mois comparés
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Windows:
    """Les deux fenêtres comparées, en jours civils (heure de Paris).

    Attributes
    ----------
    reference_start : date
        Premier jour du mois de référence (le mois d'avant).
    current_start : date
        Premier jour du mois comparé.
    current_end : date
        Premier jour du mois suivant (borne exclue).
    """

    reference_start: date
    current_start: date
    current_end: date

    @property
    def label(self) -> str:
        """Le mois comparé et sa référence, en clair (« 2026-10 contre 2026-09 »)."""
        return f"{self.current_start:%Y-%m} contre {self.reference_start:%Y-%m}"


def first_of_next_month(day: date) -> date:
    """Premier jour du mois qui suit `day`.

    Parameters
    ----------
    day : date
        Un jour quelconque.

    Returns
    -------
    date
        Le 1er du mois suivant.
    """

    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def month_windows(month: str | None = None, today: date | None = None) -> Windows:
    """Le mois comparé (par défaut : le mois écoulé) et le mois d'avant.

    Lancé le 1er novembre, le rapport compare octobre à septembre.

    Parameters
    ----------
    month : str | None
        Le mois comparé, « AAAA-MM » ; `None` pour le mois écoulé.
    today : date | None
        Jour de référence ; par défaut aujourd'hui à Paris.

    Returns
    -------
    Windows
        Les bornes des deux mois.

    Raises
    ------
    ValueError
        Si `month` n'est pas au format « AAAA-MM ».
    """

    if month:
        current_start = datetime.strptime(month, "%Y-%m").date()
    else:
        today = today or datetime.now(PARIS).date()
        this_month = today.replace(day=1)
        current_start = (this_month.replace(year=this_month.year - 1, month=12) if this_month.month == 1
                         else this_month.replace(month=this_month.month - 1))
    reference_start = (current_start.replace(year=current_start.year - 1, month=12) if current_start.month == 1
                       else current_start.replace(month=current_start.month - 1))
    return Windows(reference_start, current_start, first_of_next_month(current_start))


# ---------------------------------------------------------------------------
# Lecture en base (une ligne par question, une ligne par nuit)
# ---------------------------------------------------------------------------

# Mêmes règles que agent/drift.py (QUESTION_DISTRIBUTIONS_SQL), ligne à ligne.
QUESTIONS_SQL = f"""
WITH q AS (
    SELECT asked_at, status, latency_ms, from_cache, degraded,
           constraints AS c,
           COALESCE(constraints -> 'compris', '{{}}'::jsonb) AS p,
           COALESCE(constraints -> 'recherche', '{{}}'::jsonb) AS r
    FROM sortiria.rag_requests
    WHERE asked_at >= %(start)s AND asked_at < %(end)s
      AND status <> 'refuse' AND {NOT_A_TEST_QUESTION}
)
SELECT CASE WHEN asked_at >= %(split)s THEN 'courant' ELSE 'reference' END AS fenetre,
       COALESCE(NULLIF(p ->> 'zone', ''), NULLIF(c ->> 'zone', ''), 'non_precisee') AS zone,
       COALESCE(NULLIF(p ->> 'public', ''), NULLIF(c ->> 'public', ''), 'non_precise') AS public,
       CASE WHEN jsonb_typeof(p -> 'categories') = 'array'
            THEN COALESCE(NULLIF(p -> 'categories' ->> 0, ''), 'aucune') ELSE 'aucune' END AS categorie,
       COALESCE(NULLIF(p ->> 'moment', ''), CASE WHEN p ->> 'soir' = 'true' THEN 'soir' END,
                'non_precise') AS moment,
       CASE WHEN COALESCE(p ->> 'gratuit', c ->> 'free_only', 'false') = 'true'
            THEN 'oui' ELSE 'non' END AS gratuit,
       CASE WHEN status = 'erreur' THEN 'erreur'
            WHEN status = 'aucun_resultat' OR r ->> 'sujet_introuvable' = 'true' THEN 'introuvable'
            WHEN jsonb_typeof(r -> 'elargi') = 'array' AND jsonb_array_length(r -> 'elargi') > 0 THEN 'elargie'
            WHEN r ->> 'exact' = 'true' THEN 'exacte'
            ELSE 'idees_voisines' END AS type_de_reponse,
       CASE WHEN degraded THEN 'oui' ELSE 'non' END AS sans_mistral,
       CASE WHEN from_cache THEN NULL ELSE latency_ms END AS latence_ms
FROM q
"""

COLLECT_SQL = """
SELECT CASE WHEN started_at >= %(split)s THEN 'courant' ELSE 'reference' END AS fenetre,
       events_extracted AS evenements_extraits,
       events_rejected::numeric / NULLIF(events_extracted, 0) AS taux_rejet,
       quality_score AS score_juge,
       cost_usd AS cout_usd,
       events_total AS taille_base
FROM sortiria.collect_runs
WHERE started_at >= %(start)s AND started_at < %(end)s AND status <> 'running'
"""


def query_params(windows: Windows) -> dict[str, datetime]:
    """Bornes des requêtes : début de la référence, début du mois comparé, fin.

    Parameters
    ----------
    windows : Windows
        Les deux mois.

    Returns
    -------
    dict[str, datetime]
        `start`, `split` et `end`, à minuit heure de Paris.
    """

    def midnight(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=PARIS)

    return {"start": midnight(windows.reference_start), "split": midnight(windows.current_start),
            "end": midnight(windows.current_end)}


def fetch_frame(connection: Any, sql: str, params: Mapping[str, Any]) -> pd.DataFrame:
    """Exécuter une requête et rendre ses lignes dans un tableau pandas.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (lecture seule suffit).
    sql : str
        Requête à paramètres nommés.
    params : Mapping[str, Any]
        Valeurs des paramètres.

    Returns
    -------
    pd.DataFrame
        Une colonne par champ du résultat.
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, dict(params))
        columns = [column[0] for column in cursor.description]
        return pd.DataFrame(list(cursor.fetchall()), columns=columns)


def split(frame: pd.DataFrame, columns: Sequence[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sépare le mois de référence et le mois comparé, en gardant les colonnes utiles.

    Les valeurs numériques lues en base (`Decimal`) deviennent des nombres.

    Parameters
    ----------
    frame : pd.DataFrame
        Les lignes lues, avec la colonne `fenetre`.
    columns : Sequence[str]
        Les colonnes à comparer.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (référence, mois comparé).
    """

    if frame.empty:
        empty = pd.DataFrame(columns=list(columns))
        return empty, empty.copy()
    parts = []
    for window in ("reference", "courant"):
        part = frame.loc[frame["fenetre"] == window, list(columns)].reset_index(drop=True)
        parts.append(part)
    return parts[0], parts[1]


def to_numbers(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Convertit les colonnes numériques (`Decimal`, `None`) en nombres pandas.

    Parameters
    ----------
    frame : pd.DataFrame
        Le tableau à convertir.
    columns : Sequence[str]
        Les colonnes numériques.

    Returns
    -------
    pd.DataFrame
        Une copie, colonnes numériques converties.
    """

    converted = frame.copy()
    for column in columns:
        if column in converted:
            converted[column] = pd.to_numeric(converted[column], errors="coerce").astype(float)
    return converted


# ---------------------------------------------------------------------------
# Rapport Evidently, ou page « pas assez de données »
# ---------------------------------------------------------------------------

def enough(reference: pd.DataFrame, current: pd.DataFrame, minimum: int) -> bool:
    """Vrai si les deux mois ont assez de lignes pour être comparés.

    Parameters
    ----------
    reference : pd.DataFrame
        Le mois de référence.
    current : pd.DataFrame
        Le mois comparé.
    minimum : int
        Lignes nécessaires dans chacun des deux mois.

    Returns
    -------
    bool
        Vrai si les deux mois atteignent `minimum`.
    """

    return len(reference) >= minimum and len(current) >= minimum


def evidently_drift(reference: pd.DataFrame, current: pd.DataFrame, categorical: Sequence[str],
                    numerical: Sequence[str], html_path: Path) -> dict[str, Any]:
    """Rapport de dérive Evidently (`DataDriftPreset`), enregistré en HTML.

    Evidently est importé ici seulement : le reste du script (et ses tests)
    fonctionne sans lui.

    Parameters
    ----------
    reference : pd.DataFrame
        Le mois de référence.
    current : pd.DataFrame
        Le mois comparé.
    categorical : Sequence[str]
        Colonnes catégorielles.
    numerical : Sequence[str]
        Colonnes numériques.
    html_path : Path
        Où écrire le rapport HTML.

    Returns
    -------
    dict[str, Any]
        `colonnes_en_derive`, `colonnes` et `part_en_derive` d'après Evidently
        (None si son résultat ne se lit pas), plus le détail brut (`evidently`).
    """

    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    definition = DataDefinition(categorical_columns=list(categorical), numerical_columns=list(numerical))
    snapshot = Report([DataDriftPreset()]).run(
        current_data=Dataset.from_pandas(current, data_definition=definition),
        reference_data=Dataset.from_pandas(reference, data_definition=definition))
    snapshot.save_html(str(html_path))
    details = snapshot.dict()
    return {**drifted_columns(details), "evidently": details}


def drifted_columns(details: Mapping[str, Any]) -> dict[str, Any]:
    """Lit dans le résultat d'Evidently le nombre de colonnes en dérive.

    `DataDriftPreset` place en tête une métrique `DriftedColumnsCount`, dont la
    valeur est `{"count": …, "share": …}`.

    Parameters
    ----------
    details : Mapping[str, Any]
        `Snapshot.dict()` d'Evidently.

    Returns
    -------
    dict[str, Any]
        `colonnes_en_derive` et `part_en_derive` (None si introuvables).
    """

    for metric in details.get("metrics") or []:
        name = str(metric.get("metric_name") or metric.get("metric_id") or "")
        value = metric.get("value")
        if name.startswith("DriftedColumnsCount") and isinstance(value, Mapping):
            count, share = value.get("count"), value.get("share")
            return {"colonnes_en_derive": None if count is None else int(count),
                    "part_en_derive": None if share is None else round(float(share), 3)}
    return {"colonnes_en_derive": None, "part_en_derive": None}


def insufficient_page(title: str, windows: Windows, reference: int, current: int, minimum: int,
                      unit: str) -> str:
    """Page HTML qui dit pourquoi le jeu n'a pas été comparé.

    Parameters
    ----------
    title : str
        Le jeu (« Questions », « Collecte »).
    windows : Windows
        Les deux mois.
    reference : int
        Lignes du mois de référence.
    current : int
        Lignes du mois comparé.
    minimum : int
        Lignes nécessaires dans chaque mois.
    unit : str
        L'unité des lignes (« questions », « nuits »).

    Returns
    -------
    str
        La page HTML.
    """

    return (f"<!doctype html><html lang=\"fr\"><meta charset=\"utf-8\"><title>SortirIA · {html.escape(title)}"
            f"</title><body style=\"font-family:system-ui,sans-serif;max-width:640px;margin:40px auto\">"
            f"<h1>{html.escape(title)} : pas assez de données</h1>"
            f"<p>{html.escape(windows.label)} : {reference} {unit} en référence, {current} {unit} ce mois-ci. "
            f"Il en faut au moins {minimum} dans chacun des deux mois pour qu'une dérive veuille dire quelque "
            f"chose ; le jeu n'a pas été comparé.</p>"
            f"<p>Le suivi de chaque nuit (PSI, <code>agent/drift.py</code>) applique le même garde-fou.</p>"
            f"</body></html>")


def compare(name: str, title: str, reference: pd.DataFrame, current: pd.DataFrame, categorical: Sequence[str],
            numerical: Sequence[str], minimum: int, unit: str, windows: Windows, output: Path) -> dict[str, Any]:
    """Compare un jeu : rapport Evidently, ou page « pas assez de données ».

    Parameters
    ----------
    name : str
        Nom du fichier (« questions », « collecte »).
    title : str
        Titre lisible.
    reference : pd.DataFrame
        Le mois de référence.
    current : pd.DataFrame
        Le mois comparé.
    categorical : Sequence[str]
        Colonnes catégorielles.
    numerical : Sequence[str]
        Colonnes numériques.
    minimum : int
        Lignes nécessaires dans chaque mois.
    unit : str
        L'unité des lignes.
    windows : Windows
        Les deux mois.
    output : Path
        Dossier de sortie.

    Returns
    -------
    dict[str, Any]
        Le résumé du jeu : statut (`compare` ou `donnees_insuffisantes`),
        tailles, colonnes en dérive, fichier HTML.
    """

    html_path = output / f"{name}.html"
    summary: dict[str, Any] = {"jeu": name, "reference": len(reference), "courant": len(current),
                               "minimum": minimum, "fichier": html_path.name}
    if not enough(reference, current, minimum):
        html_path.write_text(insufficient_page(title, windows, len(reference), len(current), minimum, unit),
                             encoding="utf-8")
        return {**summary, "statut": "donnees_insuffisantes"}
    result = evidently_drift(to_numbers(reference, numerical), to_numbers(current, numerical),
                             categorical, numerical, html_path)
    return {**summary, "statut": "compare", "colonnes": len(categorical) + len(numerical),
            "colonnes_en_derive": result["colonnes_en_derive"], "part_en_derive": result["part_en_derive"]}


def markdown_summary(windows: Windows, results: Sequence[Mapping[str, Any]]) -> str:
    """Résumé lisible, pour le résumé du workflow GitHub (`$GITHUB_STEP_SUMMARY`).

    Parameters
    ----------
    windows : Windows
        Les deux mois.
    results : Sequence[Mapping[str, Any]]
        Le résumé de chaque jeu (`compare`).

    Returns
    -------
    str
        Le résumé en Markdown.
    """

    lines = [f"## Rapport Evidently mensuel : {windows.label}", "",
             "| Jeu | Référence | Mois comparé | Résultat |", "| --- | --- | --- | --- |"]
    for item in results:
        if item["statut"] == "donnees_insuffisantes":
            verdict = f"pas assez de données (minimum {item['minimum']} par mois)"
        elif item.get("colonnes_en_derive") is None:
            verdict = "comparé : voir le rapport HTML"
        else:
            verdict = f"{item['colonnes_en_derive']} colonne(s) en dérive sur {item['colonnes']}"
        lines.append(f"| {item['jeu']} | {item['reference']} | {item['courant']} | {verdict} |")
    lines += ["", "Rapports HTML : artefact « rapport-evidently » de cette exécution."]
    return "\n".join(lines) + "\n"


def build_reports(connection: Any, windows: Windows, output: Path) -> dict[str, Any]:
    """Lit les deux mois, compare les deux jeux, écrit les rapports et le résumé.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (lecture seule suffit).
    windows : Windows
        Les deux mois.
    output : Path
        Dossier de sortie (créé au besoin).

    Returns
    -------
    dict[str, Any]
        Le résumé complet (aussi écrit dans `resume.json` et `resume.md`).
    """

    output.mkdir(parents=True, exist_ok=True)
    params = query_params(windows)
    questions = fetch_frame(connection, QUESTIONS_SQL, params)
    collect = fetch_frame(connection, COLLECT_SQL, params)
    results = [
        compare("questions", "Questions des visiteurs", *split(questions, QUESTION_CATEGORIES + QUESTION_NUMBERS),
                QUESTION_CATEGORIES, QUESTION_NUMBERS, MIN_QUESTIONS, "questions", windows, output),
        compare("collecte", "Collecte de la nuit", *split(collect, COLLECT_NUMBERS),
                [], COLLECT_NUMBERS, MIN_NIGHTS, "nuits", windows, output),
    ]
    summary = {"mois": f"{windows.current_start:%Y-%m}", "reference": f"{windows.reference_start:%Y-%m}",
               "jeux": results}
    (output / "resume.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "resume.md").write_text(markdown_summary(windows, results), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit `DATABASE_URL`, écrit les rapports, affiche le résumé.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments de la ligne de commande ; par défaut ceux du processus.

    Returns
    -------
    int
        0 (un mois sans assez de données n'est pas une erreur).
    """

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mois", help="mois comparé, AAAA-MM (par défaut : le mois écoulé)")
    parser.add_argument("--sortie", type=Path, default=DEFAULT_OUTPUT, help="dossier des rapports")
    args = parser.parse_args(argv)

    import psycopg2

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:          # dans le workflow, DATABASE_URL vient des secrets
        pass
    windows = month_windows(args.mois)
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        connection.set_session(readonly=True)
        build_reports(connection, windows, args.sortie)
    finally:
        connection.close()
    print((args.sortie / "resume.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
