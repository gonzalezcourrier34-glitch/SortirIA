"""Mesure de la pertinence des propositions (16/10/2026).

**Provenance** : nouveau script écrit pour SortirIA.

`scripts/eval_recherche.py` vérifie que chaque proposition **respecte les
contraintes** (date, lieu, public, gratuit, type) : c'est automatique. Il ne
dit pas si une proposition est **pertinente** (« est-ce une bonne idée pour
cette demande ? »). Ce jugement-là est humain ; ce script l'organise et le
rend rejouable :

1. **Préparer** la grille : les 50 questions du jeu d'évaluation passent par
   la vraie recherche (sans être tracées dans `rag_requests`), et les 3
   premières cartes de chaque réponse sont écrites dans un fichier à ouvrir
   dans Excel (`;`, UTF-8) :

       uv run python -m scripts.eval_pertinence --preparer

2. **Annoter** : dans la colonne `pertinent`, `o` (oui) ou `n` (non) pour
   chaque carte, en se demandant « est-ce que je proposerais cette sortie à
   quelqu'un qui a posé cette question ? ». Compter environ 1 h.

3. **Calculer** le score (objectif : 70 %) :

       uv run python -m scripts.eval_pertinence --calculer benchmarks/pertinence/grille_….csv

   - pertinence@3 : part des cartes jugées pertinentes ;
   - questions satisfaites : part des questions où au moins une des 3 cartes
     est pertinente ;
   - par famille de question (type, quand, où, pour qui…), pour voir où ça pèche.

4. **Juge Mistral, calibré sur l'humain** (facultatif) : Mistral juge les mêmes
   cartes ; l'accord avec l'annotation humaine (part d'accord et kappa de
   Cohen) dit si le juge est assez fiable pour remplacer l'humain lors des
   prochains rejeux (en CI, par exemple) :

       uv run python -m scripts.eval_pertinence --calculer grille.csv --juge

5. **Seconde grille** (16/10/2026), pour vérifier le juge sur des cartes qu'il
   n'a jamais vues, et une correction sans tout réannoter :

       # 50 recherches courtes tirées au hasard (graine fixe) dans eval_mots.yaml
       uv run python -m scripts.eval_pertinence --preparer --questions config/eval_mots.yaml --echantillon 50
       # les 50 questions de départ, notes reprises pour les cartes déjà annotées
       uv run python -m scripts.eval_pertinence --preparer --reprendre benchmarks/pertinence/grille_….csv

Les résultats sont gardés dans `benchmarks/pertinence/` (grilles annotées et
`historique.csv`) et, depuis le 16/10/2026, dans MLflow (expérience
`sortiria-evaluation-pertinence` : modèle du juge, version de sa consigne,
accord, kappa, AC1, grilles en pièces jointes ; `scripts/suivi_mlflow.py`).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.search_service import (
    PARIS,
    PROMPT_VERSION,
    SearchFilters,
    SearchServices,
    embed_question,
    run_search,
    write_with_mistral,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_FILE = ROOT / "config" / "eval_recherche.yaml"
BENCH_DIR = ROOT / "benchmarks" / "pertinence"
TOP = 3
DEFAULT_JUDGE_MODEL = "mistral-large-latest"
DEFAULT_CALL_PAUSE_S = 3.0
JUDGE_PROMPT_VERSION = "v7"   # historique des versions juste en dessous
MLFLOW_EXPERIMENT = "sortiria-evaluation-pertinence"
TARGET = 0.70
COLUMNS = ("num", "famille", "question", "filtres", "rang", "titre", "date", "lieu", "categorie", "prix", "public",
           "resume", "pertinent", "commentaire")
YES = {"o", "oui", "y", "yes", "1", "x", "vrai", "true"}
NO = {"n", "non", "no", "0", "faux", "false"}

# Consigne du juge (16/10/2026, version 7, figée). Historique des accords avec
# l'humain sur la grille du 16/10 (benchmarks/pertinence/historique.csv) :
# - v1 68 % : le juge ignorait la date du jour, le prix et le public ;
# - v2 55 % : lots de 15 cartes mêlant 5 questions, sans justification ;
# - v3 67 % : une question par appel, une raison par verdict ; les raisons
#   montrent des calculs de date faux et un lieu « Montpellier » exigé partout ;
# - v4 78 % : ce que la demande ne précise pas n'est plus une contrainte ;
# - v5 90 % : dates converties par le code, règle « public ». Mais les 14
#   désaccords restants violaient encore la consigne : délai « implicite »
#   inventé, « en cours » refusé pour demain, prix absent lu comme payant,
#   « sujet nature non demandé » pour « une sortie demain ».
# v6 sépare les rôles : le moment et le budget sont déjà vérifiés par du code
# (filtres SQL, scripts/eval_recherche.py : 98,7 %) ; le juge ne voit plus ni
# la date ni le prix et ne juge que le sujet, le public et le lieu. Modèle :
# mistral-large (PERTINENCE_JUDGE_MODEL), car medium lisait mal les résumés
# (« marionnettes avec lecture de contes » rejeté pour « une lecture de contes »).
# v6 88 % : 7 désaccords sur 17 étaient « lieu hors Montpellier (non précisé
# dans la demande) ». v7 retire aussi le lieu, vérifié par le code (famille
# « Où » d'eval_recherche : 100 %). Le juge garde ce qu'un modèle de langage
# apporte vraiment : le sujet et le public. Chaque version a retiré un critère
# vérifié ailleurs ; aucune n'a été réglée carte par carte. Version figée.
JUDGE_PROMPT = """Tu évalues un assistant de sorties (Montpellier, bassin de Thau).
On te donne UNE demande et les propositions affichées pour elle. Pour chacune,
dis si elle est PERTINENTE : une personne qui a écrit cette demande serait-elle
contente qu'on lui propose cette sortie ?
Le MOMENT (date, heure, « demain », « ce week-end »), le LIEU (ville, zone) et
le BUDGET (gratuit, prix maximum) ont déjà été vérifiés par un contrôle
automatique : on ne te donne ni la date, ni la commune, ni le prix ; ne les
juge pas et n'en tiens aucun compte, même si la demande en parle.
Tu juges seulement deux choses :
1. le SUJET : la proposition est-elle le genre de sortie demandé ? Lis le titre
   ET le résumé : un spectacle « avec lecture de contes » répond à « une lecture
   de contes ». Si la demande ne précise pas de sujet (« une sortie demain »,
   « sortir à Frontignan »), tout sujet convient.
2. le PUBLIC : un public demandé (« pour seniors », « pour ados », « avec les
   enfants ») veut dire « qui convient à ce public », pas « réservé à ce
   public ». Une sortie ouverte à tous qui peut lui plaire est pertinente ;
   rejette seulement si elle l'exclut ou vise clairement un autre public (une
   soirée en boîte pour des enfants, un spectacle pour tout-petits pour des ados).
Une information absente de la fiche ne fait jamais rejeter. Le nom d'un
événement suffit souvent : « Les Pépites de Noël » en décembre est un marché de
Noël si le résumé parle de stands ou d'artisans.
Donne pour chaque proposition une raison en quelques mots.
Réponds uniquement avec un objet JSON :
{"verdicts": [{"id": "...", "raison": "...", "pertinent": true}]}"""


# ---------------------------------------------------------------------------
# Préparer la grille
# ---------------------------------------------------------------------------

def load_questions(path: Path = QUESTIONS_FILE) -> list[dict[str, Any]]:
    """Questions du jeu d'évaluation, avec leur famille (titre de section du YAML)."""

    family = ""
    families: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# ---"):
            family = stripped.strip("# -").strip()
        elif stripped.startswith("- question:"):
            families.append(family)
    items = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    return [{"question": str(item["question"]), "filtres": item.get("filtres") or {},
             "famille": families[index] if index < len(families) else ""}
            for index, item in enumerate(items)]


def grid_rows(num: int, item: Mapping[str, Any], cards: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Lignes de la grille pour une question : ses 3 premières cartes (ou une ligne vide)."""

    base = {"num": num, "famille": item["famille"], "question": item["question"],
            "filtres": json.dumps(item["filtres"], ensure_ascii=False) if item["filtres"] else ""}
    if not cards:
        return [{**base, "rang": 0, "titre": "(aucune proposition)", "date": "", "lieu": "", "categorie": "",
                 "prix": "", "public": "", "resume": "", "pertinent": "", "commentaire": ""}]
    return [{**base, "rang": rank, "titre": card.get("title", ""), "date": card.get("date", ""),
             "lieu": card.get("place", ""), "categorie": card.get("category_label") or card.get("category", ""),
             "prix": card.get("price", ""), "public": ", ".join(card.get("audiences") or []),
             "resume": card.get("summary", ""), "pertinent": "", "commentaire": ""}
            for rank, card in enumerate(cards[:TOP], start=1)]


def prepare(connection: Any, questions: Sequence[Mapping[str, Any]], services: SearchServices) -> list[dict[str, Any]]:
    rows = []
    for num, item in enumerate(questions, start=1):
        result: dict[str, Any] = {}
        for event in run_search(connection, item["question"], SearchFilters(**item["filtres"]), services):
            if event.get("event") == "completed":
                result = event["result"]
        rows += grid_rows(num, item, result.get("events") or [])
    return rows


SAMPLE_SEED = 2026


def sample_questions(questions: Sequence[Mapping[str, Any]], count: int | None,
                     seed: int = SAMPLE_SEED) -> list[Mapping[str, Any]]:
    """Échantillon tiré au hasard, toujours le même (graine fixe), dans l'ordre du fichier."""

    if not count or count >= len(questions):
        return list(questions)
    import random

    chosen = set(random.Random(seed).sample(range(len(questions)), count))
    return [item for index, item in enumerate(questions) if index in chosen]


def carry_over(rows: Sequence[dict[str, Any]], previous: Sequence[Mapping[str, str]]) -> int:
    """Reprend la note et le commentaire des cartes déjà annotées (même question,
    même titre) ; les cartes nouvelles restent à annoter. Renvoie le nombre de
    notes reprises."""

    known = {(str(row.get("question")), str(row.get("titre"))): row for row in previous
             if verdict(str(row.get("pertinent", ""))) is not None}
    carried = 0
    for row in rows:
        old = known.get((str(row.get("question")), str(row.get("titre"))))
        if old and str(row.get("rang")) != "0":
            row["pertinent"], row["commentaire"] = old.get("pertinent", ""), old.get("commentaire", "")
            carried += 1
    return carried


def write_grid(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    """Grille lisible par Excel en français : `;` et UTF-8 avec BOM."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Calculer le score
# ---------------------------------------------------------------------------

def read_grid(path: Path) -> list[dict[str, str]]:
    """Grille annotée, enregistrée par Excel, OpenOffice ou LibreOffice : UTF-8
    (avec ou sans BOM), sinon Windows-1252 ; séparateur `;` ou `,`."""

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    sample = text[:2048]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter))


def verdict(value: str) -> bool | None:
    """`o` / `n` (et variantes) ; None si la case est vide ou illisible."""

    text = (value or "").strip().lower()
    return True if text in YES else False if text in NO else None


def score(rows: Sequence[Mapping[str, str]], column: str = "pertinent") -> dict[str, Any]:
    """pertinence@3, questions satisfaites, détail par famille. Une question sans
    proposition compte comme non satisfaite (0 carte pertinente sur 3)."""

    judged = [row for row in rows if str(row.get("rang")) != "0" and verdict(row.get(column, "")) is not None]
    missing = [row for row in rows if str(row.get("rang")) != "0" and verdict(row.get(column, "")) is None]
    by_question: dict[str, list[bool]] = {}
    families: dict[str, str] = {}
    for row in rows:
        num = str(row["num"])
        families[num] = row.get("famille", "")
        by_question.setdefault(num, [])
        if str(row.get("rang")) == "0":
            by_question[num] += [False] * TOP
        elif (value := verdict(row.get(column, ""))) is not None:
            by_question[num].append(value)
    cards = [value for values in by_question.values() for value in values]
    satisfied = [any(values) for values in by_question.values() if values]
    detail: dict[str, dict[str, Any]] = {}
    for num, values in by_question.items():
        family = detail.setdefault(families[num] or "autre", {"questions": 0, "pertinentes": 0, "cartes": 0})
        family["questions"] += 1
        family["pertinentes"] += sum(values)
        family["cartes"] += len(values)
    for family in detail.values():
        family["pertinence"] = round(family["pertinentes"] / family["cartes"], 3) if family["cartes"] else None
    return {
        "questions": len(by_question),
        "cartes_jugees": len(judged),
        "cartes_non_annotees": len(missing),
        "pertinence_at_3": round(sum(cards) / len(cards), 3) if cards else None,
        "questions_satisfaites": round(sum(satisfied) / len(satisfied), 3) if satisfied else None,
        "objectif": TARGET,
        "par_famille": detail,
    }


def agreement(rows: Sequence[Mapping[str, str]], human: str = "pertinent", judge: str = "juge") -> dict[str, Any]:
    """Accord entre l'humain et le juge : part d'accord, kappa de Cohen et AC1 de Gwet.

    Quand presque toutes les cartes sont pertinentes (137 sur 139 le
    16/10/2026), le kappa reste proche de 0 même avec 95 % d'accord : l'accord
    « attendu par hasard » est déjà très haut. L'AC1 de Gwet ne souffre pas de
    ce biais ; on donne aussi combien des rejets humains le juge retrouve.
    """

    pairs = [(verdict(row.get(human, "")), verdict(row.get(judge, ""))) for row in rows]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return {"cartes": 0, "accord": None, "kappa": None}
    observed = sum(a == b for a, b in pairs) / len(pairs)
    p_human = sum(a for a, _ in pairs) / len(pairs)
    p_judge = sum(b for _, b in pairs) / len(pairs)
    expected = p_human * p_judge + (1 - p_human) * (1 - p_judge)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    mean = (p_human + p_judge) / 2
    chance = 2 * mean * (1 - mean)
    ac1 = (observed - chance) / (1 - chance) if chance < 1 else 1.0
    human_no = [b for a, b in pairs if not a]
    return {"cartes": len(pairs), "accord": round(observed, 3), "kappa": round(kappa, 3), "ac1": round(ac1, 3),
            "rejets_humains": len(human_no), "rejets_retrouves": sum(not b for b in human_no)}


# Seules les cases que le juge doit prendre en compte (v7) : le moment, le lieu
# et le budget sont vérifiés par le code.
FILTER_LABELS = {"public": "public", "outdoor": "en extérieur", "indoor": "en intérieur"}


def describe_filters(raw: str) -> str:
    """Cases cochées utiles au juge (zone, public…), en français plutôt qu'en JSON."""

    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(values, dict):
        return raw
    return ", ".join(f"{FILTER_LABELS.get(key, key)} : {'oui' if value is True else value}"
                     for key, value in values.items() if key in FILTER_LABELS and value not in (None, False, ""))


def judge_rows(rows: list[dict[str, str]], call: Any = None) -> list[dict[str, str]]:
    """Ajoute les colonnes `juge` (o / n) et `raison_juge` : un appel Mistral par question.

    Le juge voit le titre, la catégorie, le public et le résumé ; ni la date, ni
    la commune, ni le prix, vérifiés par le code (v7).
    """

    prompt = JUDGE_PROMPT

    if call is None:
        import time

        from src.event.event_enrichment import call_mistral_json
        from src.extractors.documents.document_extractor import call_with_rate_limit

        model = os.getenv("PERTINENCE_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)
        pause = float(os.getenv("JUDGE_CALL_PAUSE_S", DEFAULT_CALL_PAUSE_S))

        def call(prompt: str, text: str) -> str:
            # 16/10/2026 : 429 dès les premiers appels avec mistral-large (offre
            # gratuite). Pause entre deux questions, puis 10 s, 20 s, 40 s… si
            # Mistral refuse encore (même mécanisme que la lecture des PDF).
            time.sleep(pause)
            return call_with_rate_limit(lambda p, t: call_mistral_json(p, t, model), prompt, text)[0]

    cards = [(index, row) for index, row in enumerate(rows) if str(row.get("rang")) != "0"]
    questions: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for index, row in cards:
        questions.setdefault(str(row.get("num") or row.get("question")), []).append((index, row))
    for chunk in questions.values():
        first = chunk[0][1]
        filters = describe_filters(first.get("filtres", ""))
        text = (f"Demande : {first['question']}"
                + (f" (cases cochées : {filters})" if filters else "")
                + "\n\n" + "\n\n".join(
                    f"id: c{index}\nProposition : {row['titre']} - {row['categorie']}"
                    + (f" - public : {row['public']}" if row.get("public") else "")
                    + f"\nRésumé : {row.get('resume') or '-'}"
                    for index, row in chunk))
        try:
            answer = json.loads(call(prompt, text))
            verdicts = {str(item.get("id")): item for item in answer.get("verdicts", [])}
        except (ValueError, AttributeError, TypeError):
            verdicts = {}
        for index, row in chunk:
            item = verdicts.get(f"c{index}")
            if isinstance(item, dict) and isinstance(item.get("pertinent"), bool):
                row["juge"] = "o" if item["pertinent"] else "n"
                row["raison_juge"] = str(item.get("raison") or "")[:200]
    return rows


def save_history(report: Mapping[str, Any], grid: Path, note: str = "") -> Path:
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    history = BENCH_DIR / "historique.csv"
    new = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["date", "grille", "questions", "cartes_jugees", "pertinence_at_3",
                             "questions_satisfaites", "juge_pertinence_at_3", "accord_juge", "kappa_juge",
                             "prompt_version", "note"])
        judge = report.get("juge") or {}
        writer.writerow([datetime.now(PARIS).isoformat(timespec="seconds"), grid.name, report["questions"],
                         report["cartes_jugees"], report["pertinence_at_3"], report["questions_satisfaites"],
                         (judge.get("score") or {}).get("pertinence_at_3", ""),
                         (judge.get("accord") or {}).get("accord", ""), (judge.get("accord") or {}).get("kappa", ""),
                         PROMPT_VERSION, note])
    return history


def mlflow_record(report: Mapping[str, Any], grid: Path, judged: Path | None,
                  note: str = "") -> tuple[dict[str, Any], dict[str, Any], list[Path]]:
    """Paramètres, métriques et fichiers d'un calcul, pour MLflow."""

    judge = report.get("juge") or {}
    accord = judge.get("accord") or {}
    params: dict[str, Any] = {"grille": grid.name, "questions": report["questions"],
                              "cartes_jugees": report["cartes_jugees"], "prompt_version": PROMPT_VERSION,
                              "note": note}
    metrics: dict[str, Any] = {"pertinence_at_3": report["pertinence_at_3"],
                               "questions_satisfaites": report["questions_satisfaites"]}
    if judge:
        params |= {"juge_modele": os.getenv("PERTINENCE_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
                   "juge_consigne": JUDGE_PROMPT_VERSION}
        metrics |= {"juge_pertinence_at_3": (judge.get("score") or {}).get("pertinence_at_3"),
                    "accord_juge": accord.get("accord"), "kappa_juge": accord.get("kappa"),
                    "ac1_juge": accord.get("ac1"), "rejets_humains": accord.get("rejets_humains"),
                    "rejets_retrouves": accord.get("rejets_retrouves")}
    return params, metrics, [grid, *([judged] if judged else [])]


def print_score(report: Mapping[str, Any]) -> None:
    def pct(value: Any) -> str:
        return "-" if value is None else f"{value:.0%}"

    print(f"{report['questions']} questions, {report['cartes_jugees']} cartes jugées"
          + (f" ({report['cartes_non_annotees']} non annotées)" if report["cartes_non_annotees"] else ""))
    print(f"  pertinence@3 : {pct(report['pertinence_at_3'])} (objectif {report['objectif']:.0%})")
    print(f"  questions avec au moins une carte pertinente : {pct(report['questions_satisfaites'])}")
    for family, values in report["par_famille"].items():
        print(f"  - {family} : {pct(values['pertinence'])} ({values['questions']} questions)")
    judge = report.get("juge")
    if judge:
        accord = judge["accord"]
        print(f"Juge Mistral : pertinence@3 {pct(judge['score']['pertinence_at_3'])} ; accord avec l'humain "
              f"{pct(accord['accord'])}, AC1 {accord.get('ac1')}, kappa {accord['kappa']} ; "
              f"rejets humains retrouvés : {accord.get('rejets_retrouves', 0)}/{accord.get('rejets_humains', 0)}")
        print("  (accord ≥ 85 % et AC1 ≥ 0,6 : le juge peut servir aux rejeux. Le kappa reste bas quand "
              "presque toutes les cartes sont pertinentes.)")


def main() -> None:
    from config.environment import load_environment

    parser = argparse.ArgumentParser(description="Pertinence des propositions : grille humaine et juge calibré.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preparer", action="store_true", help="Crée la grille à annoter (3 cartes par question).")
    action.add_argument("--calculer", type=Path, help="Calcule le score d'une grille annotée.")
    parser.add_argument("--juge", action="store_true", help="Avec --calculer : fait aussi juger par Mistral.")
    parser.add_argument("--note", default="", help="Note gardée dans l'historique.")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_FILE,
                        help="Avec --preparer : jeu de questions (YAML), par défaut config/eval_recherche.yaml.")
    parser.add_argument("--echantillon", type=int, default=None,
                        help="Avec --preparer : nombre de questions tirées au hasard (graine fixe).")
    parser.add_argument("--reprendre", type=Path, default=None,
                        help="Avec --preparer : grille déjà annotée dont on reprend les notes (même question et titre).")
    args = parser.parse_args()

    load_environment()
    if args.preparer:
        from agent.services import connect_postgres

        services = SearchServices(embed=embed_question, write=write_with_mistral, log=False, cache=False)
        questions = sample_questions(load_questions(args.questions), args.echantillon)
        with connect_postgres() as connection:
            rows = prepare(connection, questions, services)
        carried = carry_over(rows, read_grid(args.reprendre)) if args.reprendre else 0
        suffix = "" if args.questions == QUESTIONS_FILE else f"_{args.questions.stem.removeprefix('eval_')}"
        path = BENCH_DIR / f"grille_{datetime.now(PARIS).strftime('%Y-%m-%d_%Hh%M')}{suffix}.csv"
        write_grid(rows, path)
        todo = sum(1 for row in rows if str(row.get("rang")) != "0" and not row.get("pertinent"))
        print(f"Grille à annoter : {path.relative_to(ROOT)} ({len(questions)} questions, {len(rows)} lignes"
              + (f", {carried} notes reprises" if args.reprendre else "") + f", {todo} cartes à annoter). "
              "Colonne « pertinent » : o ou n. Puis --calculer avec ce fichier.")
        return

    rows = read_grid(args.calculer)
    report = score(rows)
    judged = None
    if args.juge:
        rows = judge_rows(rows)
        report["juge"] = {"score": score(rows, "juge"), "accord": agreement(rows)}
        judged = args.calculer.with_name(args.calculer.stem + "_juge.csv")
        with judged.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=[*COLUMNS, "juge", "raison_juge"], delimiter=";", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"Verdicts du juge : {judged}")
    print_score(report)
    print(f"Historique : {save_history(report, args.calculer, args.note).relative_to(ROOT)}")
    from scripts.suivi_mlflow import log_run

    params, metrics, files = mlflow_record(report, args.calculer, judged, args.note)
    print(log_run(MLFLOW_EXPERIMENT, f"pertinence {args.calculer.stem.removeprefix('grille_')}",
                  params, metrics, files))


if __name__ == "__main__":
    main()
