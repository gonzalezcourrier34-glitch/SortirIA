"""Surveillance de la dérive : ce que les visiteurs demandent, ce que SortirIA
répond et ce que la collecte apporte changent-ils ?

**Provenance** : nouveau module écrit pour SortirIA (19/10/2026). Le projet 5
(scoring crédit) suivait la dérive avec Evidently
(`reference/p5_monitoring/app/services/analysis_services/evidently_service.py`).
SortirIA n'entraîne pas de modèle : il appelle Mistral. Ce qui peut dériver,
ce sont les **entrées** (les questions), les **sorties** (le type de réponse)
et les **données** (ce que les sources apportent chaque nuit). On compare donc
ces distributions, sans ajouter Evidently à l'image de production : le PSI
(Population Stability Index) est l'indicateur qu'Evidently applique par
défaut aux variables catégorielles, et il tient en quelques lignes.

Deux fenêtres glissantes :

- **référence** : les 28 jours qui précèdent la semaine en cours ;
- **courante** : les 7 derniers jours.

Ce qui est comparé :

| Famille | Indicateur | Type de dérive |
| --- | --- | --- |
| questions | zone, public, catégorie, moment, gratuit | dérive des données d'entrée (*data drift*) |
| réponses | type de réponse (exacte, élargie, idées voisines, introuvable, erreur) | dérive des sorties (*prediction drift*) |
| réponses | latence P95, part sans Mistral, part des pouces haut | dérive de la performance |
| collecte | part de chaque source, motifs de rejet | dérive des données collectées |
| collecte | taux de rejet, score du juge de qualité | dérive de la qualité (*concept drift* de l'enrichissement) |

Lecture du PSI (seuils usuels, ceux d'Evidently) : moins de 0,10 stable ;
de 0,10 à 0,25 dérive modérée ; au-delà, dérive forte.

Le calcul tourne chaque nuit (résultat dans `collect_runs.drift`, tableau
Grafana « Modèles et coûts », métriques MLflow `derive/...`) ; le lundi, il
part dans le mail de bilan et une dérive forte devient une alerte. À la
demande : `uv run python -m agent.drift`.

Faible trafic : sous `DERIVE_MIN_QUESTIONS` questions dans la semaine (30 par
défaut), un indicateur n'est pas jugé (« données insuffisantes ») plutôt que
de déclencher une fausse alerte.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

REFERENCE_DAYS = 28
CURRENT_DAYS = 7

PSI_MODERATE = 0.10
PSI_STRONG = 0.25
# Proportion plancher : une catégorie absente d'une fenêtre ne rend pas le PSI infini.
PSI_EPSILON = 1e-4

STABLE, MODERATE, STRONG, INSUFFICIENT = "stable", "moderee", "forte", "donnees_insuffisantes"
STATUS_STABLE, STATUS_WATCH, STATUS_DRIFT = "stable", "a_surveiller", "derive"

# Taille minimale des échantillons (questions, avis, nuits) pour juger.
DEFAULT_MIN_QUESTIONS = 30
MIN_REFERENCE_FACTOR = 2      # la référence doit être au moins deux fois plus fournie
MIN_FEEDBACK = 20
MIN_CURRENT_NIGHTS = 3
MIN_REFERENCE_NIGHTS = 7

LABELS = {
    ("questions", "zone"): "zone demandée",
    ("questions", "public"): "public demandé",
    ("questions", "categorie"): "type de sortie demandé",
    ("questions", "moment"): "moment de la journée",
    ("questions", "gratuit"): "sorties gratuites demandées",
    ("reponses", "type_de_reponse"): "type de réponse",
    ("reponses", "latence_p95_ms"): "latence P95 (ms, hors cache)",
    ("reponses", "part_sans_mistral"): "réponses sans Mistral",
    ("reponses", "part_pouces_haut"): "pouces haut",
    ("collecte", "source"): "part de chaque source",
    ("collecte", "motif_rejet"): "motifs de rejet",
    ("collecte", "taux_rejet"): "taux de rejet de la collecte",
    ("collecte", "score_juge"): "score du juge de qualité",
}

# Mesures (une valeur par fenêtre) : sens de la dégradation et seuils.
# « ratio » : courant / référence ; « hausse » / « baisse » : écart absolu.
MEASURE_RULES: dict[tuple[str, str], dict[str, Any]] = {
    ("reponses", "latence_p95_ms"): {"mode": "ratio", "moderee": 1.25, "forte": 1.5},
    ("reponses", "part_sans_mistral"): {"mode": "hausse", "moderee": 0.05, "forte": 0.10},
    ("reponses", "part_pouces_haut"): {"mode": "baisse", "moderee": 0.08, "forte": 0.15},
    ("collecte", "taux_rejet"): {"mode": "hausse", "moderee": 0.05, "forte": 0.10},
    ("collecte", "score_juge"): {"mode": "baisse", "moderee": 0.05, "forte": 0.10},
}


def min_questions() -> int:
    """Questions de la semaine en dessous desquelles on ne juge pas (`DERIVE_MIN_QUESTIONS`).

    Returns
    -------
    int
        La valeur de `DERIVE_MIN_QUESTIONS` (au moins 1), ou `DEFAULT_MIN_QUESTIONS`
        (30) si elle est absente ou illisible.
    """

    try:
        return max(1, int(os.getenv("DERIVE_MIN_QUESTIONS", DEFAULT_MIN_QUESTIONS)))
    except ValueError:
        return DEFAULT_MIN_QUESTIONS


# ---------------------------------------------------------------------------
# Calcul (fonctions pures, testées sans base)
# ---------------------------------------------------------------------------

def proportions(counts: Mapping[str, float], categories: Iterable[str]) -> dict[str, float]:
    """Part de chaque catégorie, avec un plancher (PSI_EPSILON) pour les absentes.

    Les comptes négatifs valent zéro ; les parts ne sont pas renormalisées
    après l'application du plancher.

    Parameters
    ----------
    counts : Mapping[str, float]
        Compte de chaque catégorie dans la fenêtre.
    categories : Iterable[str]
        Toutes les catégories à couvrir (union des deux fenêtres).

    Returns
    -------
    dict[str, float]
        Part de chaque catégorie, au moins `PSI_EPSILON`.
    """

    total = float(sum(max(0.0, float(value)) for value in counts.values()))
    shares = {category: (max(0.0, float(counts.get(category, 0))) / total if total else 0.0)
              for category in categories}
    return {category: max(share, PSI_EPSILON) for category, share in shares.items()}


def psi(reference: Mapping[str, float], current: Mapping[str, float]) -> float:
    """Population Stability Index entre deux distributions de comptes.

    PSI = Σ (courant − référence) × ln(courant / référence), sur les parts.
    0 si les distributions sont identiques ; symétrique ; toujours ≥ 0.

    Parameters
    ----------
    reference : Mapping[str, float]
        Comptes par catégorie dans la fenêtre de référence.
    current : Mapping[str, float]
        Comptes par catégorie dans la fenêtre courante.

    Returns
    -------
    float
        Le PSI (0 si aucune catégorie).
    """

    categories = sorted(set(reference) | set(current))
    if not categories:
        return 0.0
    ref, cur = proportions(reference, categories), proportions(current, categories)
    return float(sum((cur[c] - ref[c]) * math.log(cur[c] / ref[c]) for c in categories))


def psi_level(value: float) -> str:
    """stable, modérée ou forte (seuils usuels du PSI).

    Parameters
    ----------
    value : float
        Le PSI.

    Returns
    -------
    str
        `STRONG` à partir de `PSI_STRONG` (0,25), `MODERATE` à partir de
        `PSI_MODERATE` (0,10), sinon `STABLE`.
    """

    if value >= PSI_STRONG:
        return STRONG
    if value >= PSI_MODERATE:
        return MODERATE
    return STABLE


def largest_changes(reference: Mapping[str, float], current: Mapping[str, float],
                    limit: int = 3) -> list[dict[str, Any]]:
    """Les catégories dont la part a le plus bougé (pour expliquer un PSI).

    Parameters
    ----------
    reference : Mapping[str, float]
        Comptes par catégorie dans la fenêtre de référence.
    current : Mapping[str, float]
        Comptes par catégorie dans la fenêtre courante.
    limit : int
        Nombre de catégories gardées. Par défaut 3.

    Returns
    -------
    list[dict[str, Any]]
        Pour chaque catégorie : `valeur`, part de `reference` et part `courant`,
        du plus grand écart au plus petit.
    """

    ref_total = float(sum(reference.values())) or 1.0
    cur_total = float(sum(current.values())) or 1.0
    changes = [
        {"valeur": category,
         "reference": round(float(reference.get(category, 0)) / ref_total, 4),
         "courant": round(float(current.get(category, 0)) / cur_total, 4)}
        for category in set(reference) | set(current)
    ]
    changes.sort(key=lambda item: (-abs(item["courant"] - item["reference"]), item["valeur"]))
    return changes[:limit]


def compare_distribution(family: str, name: str, reference: Mapping[str, float], current: Mapping[str, float],
                         n_reference: int, n_current: int, min_current: int, min_reference: int) -> dict[str, Any]:
    """Un indicateur catégoriel : PSI, niveau et plus grands écarts.

    Parameters
    ----------
    family : str
        Famille de l'indicateur (`questions`, `reponses`, `collecte`).
    name : str
        Nom de l'indicateur (`zone`, `source`...).
    reference : Mapping[str, float]
        Comptes par catégorie dans la fenêtre de référence.
    current : Mapping[str, float]
        Comptes par catégorie dans la fenêtre courante.
    n_reference : int
        Taille de l'échantillon de référence (questions ou nuits).
    n_current : int
        Taille de l'échantillon courant.
    min_current : int
        Taille courante minimale pour juger.
    min_reference : int
        Taille de référence minimale pour juger.

    Returns
    -------
    dict[str, Any]
        L'indicateur : tailles, `psi`, `niveau` et `ecarts` ; niveau
        `INSUFFICIENT` et `psi` à `None` si un échantillon est trop petit.
    """

    item: dict[str, Any] = {"famille": family, "nom": name, "type": "distribution",
                            "n_reference": int(n_reference), "n_courant": int(n_current)}
    if n_current < min_current or n_reference < min_reference:
        return {**item, "psi": None, "niveau": INSUFFICIENT}
    value = psi(reference, current)
    return {**item, "psi": round(value, 4), "niveau": psi_level(value),
            "ecarts": largest_changes(reference, current)}


def compare_measure(family: str, name: str, reference: float | None, current: float | None,
                    enough: bool) -> dict[str, Any]:
    """Une mesure (latence, taux) : valeur de chaque fenêtre et niveau selon MEASURE_RULES.

    Parameters
    ----------
    family : str
        Famille de l'indicateur (`reponses`, `collecte`).
    name : str
        Nom de la mesure ; `(family, name)` doit figurer dans `MEASURE_RULES`.
    reference : float | None
        Valeur sur la fenêtre de référence.
    current : float | None
        Valeur sur la fenêtre courante.
    enough : bool
        Vrai si les échantillons sont assez fournis pour juger.

    Returns
    -------
    dict[str, Any]
        La mesure : valeurs arrondies, `ecart` et `niveau` (`INSUFFICIENT` si
        on ne peut pas juger).
    """

    item: dict[str, Any] = {"famille": family, "nom": name, "type": "mesure",
                            "reference": None if reference is None else round(float(reference), 4),
                            "courant": None if current is None else round(float(current), 4)}
    if not enough or reference is None or current is None:
        return {**item, "niveau": INSUFFICIENT}
    # Écart dans le sens de la dégradation, puis niveau selon les seuils de la règle
    rule = MEASURE_RULES[(family, name)]
    reference, current = float(reference), float(current)
    if rule["mode"] == "ratio":
        change = current / reference if reference > 0 else 1.0
    elif rule["mode"] == "hausse":
        change = current - reference
    else:  # baisse
        change = reference - current
    level = STRONG if change >= rule["forte"] else MODERATE if change >= rule["moderee"] else STABLE
    return {**item, "ecart": round(change, 4), "niveau": level}


def overall_status(indicators: Sequence[Mapping[str, Any]]) -> str:
    """derive (au moins une forte), a_surveiller (une modérée), stable, ou données insuffisantes.

    Parameters
    ----------
    indicators : Sequence[Mapping[str, Any]]
        Les indicateurs du rapport (clé `niveau`).

    Returns
    -------
    str
        `STATUS_DRIFT`, `STATUS_WATCH`, `STATUS_STABLE` ou `INSUFFICIENT`.
    """

    levels = {item.get("niveau") for item in indicators}
    if STRONG in levels:
        return STATUS_DRIFT
    if MODERATE in levels:
        return STATUS_WATCH
    if STABLE in levels:
        return STATUS_STABLE
    return INSUFFICIENT


def split_windows(rows: Iterable[Sequence[Any]]) -> dict[str, dict[str, dict[str, float]]]:
    """(indicateur, fenêtre, valeur, n) → {indicateur: {fenêtre: {valeur: n}}}.

    Parameters
    ----------
    rows : Iterable[Sequence[Any]]
        Lignes lues en base : (indicateur, fenêtre, valeur, n).

    Returns
    -------
    dict[str, dict[str, dict[str, float]]]
        Comptes regroupés ; chaque indicateur a au moins les fenêtres
        `reference` et `courant`.
    """

    grouped: dict[str, dict[str, dict[str, float]]] = {}
    for name, window, value, count in rows:
        bucket = grouped.setdefault(str(name), {"reference": {}, "courant": {}})
        bucket.setdefault(str(window), {})
        bucket[str(window)][str(value)] = bucket[str(window)].get(str(value), 0.0) + float(count or 0)
    return grouped


def build_report(*, question_rows: Iterable[Sequence[Any]], question_counts: Mapping[str, int],
                 question_measures: Mapping[str, Mapping[str, Any]], feedback: Mapping[str, Mapping[str, Any]],
                 collect_rows: Iterable[Sequence[Any]], collect_measures: Mapping[str, Mapping[str, Any]],
                 minimum: int | None = None, reference_days: int = REFERENCE_DAYS,
                 current_days: int = CURRENT_DAYS) -> dict[str, Any]:
    """Assemble le rapport de dérive à partir des comptes lus en base.

    - `question_rows` / `collect_rows` : (indicateur, fenêtre, valeur, n) ;
    - `question_counts` : questions par fenêtre ;
    - `question_measures` : {fenêtre: {latence_p95_ms, part_sans_mistral}} ;
    - `feedback` : {fenêtre: {avis, part_pouces_haut}} ;
    - `collect_measures` : {fenêtre: {nuits, taux_rejet, score_juge}}.

    Parameters
    ----------
    question_rows : Iterable[Sequence[Any]]
        Distributions des questions et des types de réponse.
    question_counts : Mapping[str, int]
        Nombre de questions par fenêtre.
    question_measures : Mapping[str, Mapping[str, Any]]
        Latence P95 et part sans Mistral, par fenêtre.
    feedback : Mapping[str, Mapping[str, Any]]
        Nombre d'avis et part des pouces haut, par fenêtre.
    collect_rows : Iterable[Sequence[Any]]
        Distributions de la collecte (part des sources, motifs de rejet).
    collect_measures : Mapping[str, Mapping[str, Any]]
        Nombre de nuits, taux de rejet et score du juge, par fenêtre.
    minimum : int | None
        Questions de la semaine nécessaires pour juger ; `None` par défaut
        pour `min_questions()`.
    reference_days : int
        Longueur de la fenêtre de référence. Par défaut 28 jours.
    current_days : int
        Longueur de la fenêtre courante. Par défaut 7 jours.

    Returns
    -------
    dict[str, Any]
        Le rapport : `fenetres`, `questions`, `nuits`, `statut` et `indicateurs`.
    """

    # Seuils et tailles des échantillons de questions
    minimum = minimum or min_questions()
    min_reference = minimum * MIN_REFERENCE_FACTOR
    n_ref = int(question_counts.get("reference", 0))
    n_cur = int(question_counts.get("courant", 0))
    indicators: list[dict[str, Any]] = []

    # Questions (dérive des entrées) et type de réponse (dérive des sorties)
    questions = split_windows(question_rows)
    for name in ("zone", "public", "categorie", "moment", "gratuit"):
        windows = questions.get(name, {"reference": {}, "courant": {}})
        indicators.append(compare_distribution("questions", name, windows["reference"], windows["courant"],
                                               n_ref, n_cur, minimum, min_reference))
    windows = questions.get("type_de_reponse", {"reference": {}, "courant": {}})
    indicators.append(compare_distribution("reponses", "type_de_reponse", windows["reference"],
                                           windows["courant"], n_ref, n_cur, minimum, min_reference))

    # Performance des réponses : latence, part sans Mistral, pouces haut
    enough_questions = n_cur >= minimum and n_ref >= min_reference
    ref_measures, cur_measures = question_measures.get("reference", {}), question_measures.get("courant", {})
    for name in ("latence_p95_ms", "part_sans_mistral"):
        indicators.append(compare_measure("reponses", name, ref_measures.get(name), cur_measures.get(name),
                                          enough_questions))
    ref_feedback, cur_feedback = feedback.get("reference", {}), feedback.get("courant", {})
    enough_feedback = (int(cur_feedback.get("avis", 0)) >= MIN_FEEDBACK
                       and int(ref_feedback.get("avis", 0)) >= MIN_FEEDBACK)
    indicators.append(compare_measure("reponses", "part_pouces_haut", ref_feedback.get("part_pouces_haut"),
                                      cur_feedback.get("part_pouces_haut"), enough_feedback))

    # Collecte : distributions (sources, rejets) puis qualité, jugées sur le nombre de nuits
    ref_nights = int(collect_measures.get("reference", {}).get("nuits", 0))
    cur_nights = int(collect_measures.get("courant", {}).get("nuits", 0))
    collect = split_windows(collect_rows)
    for name in ("source", "motif_rejet"):
        windows = collect.get(name, {"reference": {}, "courant": {}})
        indicators.append(compare_distribution("collecte", name, windows["reference"], windows["courant"],
                                               ref_nights, cur_nights, MIN_CURRENT_NIGHTS, MIN_REFERENCE_NIGHTS))
    enough_nights = cur_nights >= MIN_CURRENT_NIGHTS and ref_nights >= MIN_REFERENCE_NIGHTS
    for name in ("taux_rejet", "score_juge"):
        indicators.append(compare_measure("collecte", name, collect_measures.get("reference", {}).get(name),
                                          collect_measures.get("courant", {}).get(name), enough_nights))

    # Assembler le rapport et son statut global
    return {
        "fenetres": {"reference_jours": int(reference_days), "courant_jours": int(current_days)},
        "questions": {"reference": n_ref, "courant": n_cur, "minimum": int(minimum)},
        "nuits": {"reference": ref_nights, "courant": cur_nights},
        "statut": overall_status(indicators),
        "indicateurs": indicators,
    }


# ---------------------------------------------------------------------------
# Restitution : alertes, mail, MLflow
# ---------------------------------------------------------------------------

def label(item: Mapping[str, Any]) -> str:
    """Libellé lisible d'un indicateur (d'après `LABELS`).

    Parameters
    ----------
    item : Mapping[str, Any]
        L'indicateur (`famille`, `nom`).

    Returns
    -------
    str
        Le libellé, ou le nom brut s'il n'est pas dans `LABELS`.
    """
    return LABELS.get((str(item.get("famille")), str(item.get("nom"))), str(item.get("nom")))


def percent(value: float) -> str:
    """0.2 → « 20 % » (écriture française).

    Parameters
    ----------
    value : float
        Une part entre 0 et 1.

    Returns
    -------
    str
        Le pourcentage arrondi à l'unité.
    """

    return f"{float(value) * 100:.0f} %"


def describe(item: Mapping[str, Any]) -> str:
    """Un indicateur en une ligne lisible : « zone demandée : PSI 0,31 (bassin_de_thau 12 % → 25 %) ».

    Parameters
    ----------
    item : Mapping[str, Any]
        Un indicateur jugé (distribution avec `psi`, ou mesure avec ses deux valeurs).

    Returns
    -------
    str
        La ligne : PSI et deux plus grands écarts pour une distribution,
        valeur de référence puis courante pour une mesure.
    """

    if item.get("type") == "distribution":
        changes = ", ".join(f"{change['valeur']} {percent(change['reference'])} → {percent(change['courant'])}"
                            for change in item.get("ecarts", [])[:2])
        text = f"{label(item)} : PSI {float(item['psi']):.2f}".replace(".", ",")
        return f"{text} ({changes})" if changes else text
    reference, current = item.get("reference"), item.get("courant")
    if item.get("nom") == "latence_p95_ms":
        return f"{label(item)} : {float(reference):.0f} → {float(current):.0f} ms"
    return f"{label(item)} : {percent(reference)} → {percent(current)}"


FAMILY_LABELS = {"questions": "questions", "reponses": "réponses", "collecte": "collecte"}


def drift_alerts(report: Mapping[str, Any]) -> list[str]:
    """Une alerte par dérive forte (envoyées le lundi avec le bilan).

    Parameters
    ----------
    report : Mapping[str, Any]
        Le rapport de dérive.

    Returns
    -------
    list[str]
        Une phrase par indicateur de niveau `STRONG`.
    """

    return [f"Dérive forte ({FAMILY_LABELS.get(str(item['famille']), item['famille'])}) : {describe(item)}."
            for item in report.get("indicateurs") or [] if item.get("niveau") == STRONG]


STATUS_LABELS = {
    STATUS_STABLE: "stable",
    STATUS_WATCH: "à surveiller",
    STATUS_DRIFT: "DÉRIVE",
    INSUFFICIENT: "données insuffisantes",
}


def report_lines(report: Mapping[str, Any]) -> list[str]:
    """Section « Dérive » du mail de bilan.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le rapport de dérive (vide les nuits où il n'est pas envoyé).

    Returns
    -------
    list[str]
        Les lignes de la section (commençant par une ligne vide), ou une liste vide.
    """

    if not report or not report.get("indicateurs"):
        return []
    # En-tête : statut global, fenêtres comparées, tailles des échantillons
    windows = report.get("fenetres") or {}
    questions = report.get("questions") or {}
    nights = report.get("nuits") or {}
    status = STATUS_LABELS.get(str(report.get("statut")), str(report.get("statut")))
    heading = (f"Dérive : {status} ({windows.get('courant_jours', CURRENT_DAYS)} derniers jours comparés aux "
               f"{windows.get('reference_jours', REFERENCE_DAYS)} précédents)")
    sizes = (f"- questions : {questions.get('courant', 0)} contre {questions.get('reference', 0)} "
             f"(jugées à partir de {questions.get('minimum', DEFAULT_MIN_QUESTIONS)} dans la semaine) ; "
             f"nuits de collecte : {nights.get('courant', 0)} contre {nights.get('reference', 0)}")
    lines = ["", heading, sizes]
    # Indicateurs jugés : dérives fortes, puis modérées, puis la liste des stables
    judged = [item for item in report["indicateurs"] if item.get("niveau") in (STABLE, MODERATE, STRONG)]
    for level, heading in ((STRONG, "forte"), (MODERATE, "modérée")):
        for item in judged:
            if item.get("niveau") == level:
                lines.append(f"- {heading} : {describe(item)}")
    stable = [label(item) for item in judged if item.get("niveau") == STABLE]
    if stable:
        lines.append(f"- stables : {', '.join(stable)}")
    # Indicateurs non jugés, et renvoi vers la procédure en cas de dérive
    skipped = [label(item) for item in report["indicateurs"] if item.get("niveau") == INSUFFICIENT]
    if skipped:
        lines.append(f"- pas assez de données : {', '.join(skipped)}")
    if any(item.get("niveau") in (MODERATE, STRONG) for item in judged):
        lines.append("  Que faire : voir monitoring/README.md, section « Dérive ».")
    return lines


def mlflow_metrics(report: Mapping[str, Any]) -> dict[str, float]:
    """Métriques `derive/...` pour l'exécution MLflow de la nuit.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le rapport de dérive.

    Returns
    -------
    dict[str, float]
        `derive/statut` (-1 données insuffisantes, 0 stable, 1 à surveiller,
        2 dérive), puis le PSI et la valeur courante de chaque indicateur.
    """

    metrics: dict[str, float] = {}
    codes = {INSUFFICIENT: -1.0, STATUS_STABLE: 0.0, STATUS_WATCH: 1.0, STATUS_DRIFT: 2.0}
    if report.get("statut") in codes:
        metrics["derive/statut"] = codes[str(report["statut"])]
    for item in report.get("indicateurs") or []:
        key = f"derive/{item['famille']}/{item['nom']}"
        if item.get("psi") is not None:
            metrics[f"{key}/psi"] = float(item["psi"])
        if item.get("type") == "mesure" and item.get("courant") is not None:
            metrics[f"{key}/courant"] = float(item["courant"])
    return metrics


# ---------------------------------------------------------------------------
# Lecture en base
# ---------------------------------------------------------------------------

QUESTIONS_CTE = """
WITH q AS (
    SELECT CASE WHEN asked_at >= now() - make_interval(days => %(current_days)s)
                THEN 'courant' ELSE 'reference' END AS fenetre,
           status, latency_ms, from_cache, degraded,
           constraints AS c,
           COALESCE(constraints -> 'compris', '{}'::jsonb) AS p,
           COALESCE(constraints -> 'recherche', '{}'::jsonb) AS r
    FROM sortiria.rag_requests
    WHERE asked_at >= now() - make_interval(days => %(current_days)s + %(reference_days)s)
      AND status <> 'refuse'
)
"""

# Une ligne par (indicateur, fenêtre, valeur). Type de réponse : mêmes règles
# que le tableau Grafana « SortirIA - Recherche ».
QUESTION_DISTRIBUTIONS_SQL = QUESTIONS_CTE + """
SELECT 'zone', fenetre, COALESCE(NULLIF(p ->> 'zone', ''), NULLIF(c ->> 'zone', ''), 'non_precisee'), count(*)
FROM q GROUP BY 1, 2, 3
UNION ALL
SELECT 'public', fenetre, COALESCE(NULLIF(p ->> 'public', ''), NULLIF(c ->> 'public', ''), 'non_precise'), count(*)
FROM q GROUP BY 1, 2, 3
UNION ALL
SELECT 'categorie', fenetre,
       CASE WHEN jsonb_typeof(p -> 'categories') = 'array'
            THEN COALESCE(NULLIF(p -> 'categories' ->> 0, ''), 'aucune') ELSE 'aucune' END, count(*)
FROM q GROUP BY 1, 2, 3
UNION ALL
SELECT 'moment', fenetre,
       COALESCE(NULLIF(p ->> 'moment', ''), CASE WHEN p ->> 'soir' = 'true' THEN 'soir' END, 'non_precise'), count(*)
FROM q GROUP BY 1, 2, 3
UNION ALL
SELECT 'gratuit', fenetre,
       CASE WHEN COALESCE(p ->> 'gratuit', c ->> 'free_only', 'false') = 'true' THEN 'oui' ELSE 'non' END, count(*)
FROM q GROUP BY 1, 2, 3
UNION ALL
SELECT 'type_de_reponse', fenetre,
       CASE WHEN status = 'erreur' THEN 'erreur'
            WHEN status = 'aucun_resultat' OR r ->> 'sujet_introuvable' = 'true' THEN 'introuvable'
            WHEN jsonb_typeof(r -> 'elargi') = 'array' AND jsonb_array_length(r -> 'elargi') > 0 THEN 'elargie'
            WHEN r ->> 'exact' = 'true' THEN 'exacte'
            ELSE 'idees_voisines' END, count(*)
FROM q GROUP BY 1, 2, 3
"""

QUESTION_MEASURES_SQL = QUESTIONS_CTE + """
SELECT fenetre, count(*) AS questions,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
           FILTER (WHERE NOT from_cache AND latency_ms IS NOT NULL) AS latence_p95_ms,
       avg(degraded::int) AS part_sans_mistral
FROM q GROUP BY fenetre
"""

FEEDBACK_SQL = """
SELECT CASE WHEN created_at >= now() - make_interval(days => %(current_days)s)
            THEN 'courant' ELSE 'reference' END AS fenetre,
       count(*) AS avis, avg((rating = 1)::int) AS part_pouces_haut
FROM sortiria.feedback
WHERE event_id = '' AND created_at >= now() - make_interval(days => %(current_days)s + %(reference_days)s)
GROUP BY 1
"""

RUNS_CTE = """
WITH runs AS (
    SELECT run_id, rejection_reasons, events_extracted, events_rejected, quality_score,
           CASE WHEN started_at >= now() - make_interval(days => %(current_days)s)
                THEN 'courant' ELSE 'reference' END AS fenetre
    FROM sortiria.collect_runs
    WHERE started_at >= now() - make_interval(days => %(current_days)s + %(reference_days)s)
      AND status <> 'running'
)
"""

COLLECT_DISTRIBUTIONS_SQL = RUNS_CTE + """
SELECT 'source', runs.fenetre, s.source_id, sum(s.events_kept)
FROM runs JOIN sortiria.collect_run_sources AS s USING (run_id)
GROUP BY 1, 2, 3
UNION ALL
SELECT 'motif_rejet', runs.fenetre, m.key, sum((m.value #>> '{}')::numeric)
FROM runs, jsonb_each(runs.rejection_reasons) AS m
WHERE jsonb_typeof(m.value) = 'number'
GROUP BY 1, 2, 3
"""

COLLECT_MEASURES_SQL = RUNS_CTE + """
SELECT fenetre, count(*) AS nuits,
       sum(events_rejected)::numeric / NULLIF(sum(events_extracted), 0) AS taux_rejet,
       avg(quality_score) AS score_juge
FROM runs GROUP BY fenetre
"""


def _rows(connection: Any, sql: str, params: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """Exécuter une requête et renvoyer toutes ses lignes.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    sql : str
        Requête à paramètres nommés.
    params : Mapping[str, Any]
        Valeurs des paramètres (`reference_days`, `current_days`).

    Returns
    -------
    list[tuple[Any, ...]]
        Les lignes du résultat.
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, dict(params))
        return list(cursor.fetchall())


def _float(value: Any) -> float | None:
    """Convertir en `float` une valeur lue en base (`Decimal` par exemple), `None` restant `None`.

    Parameters
    ----------
    value : Any
        Valeur lue en base.

    Returns
    -------
    float | None
        La valeur convertie, ou `None`.
    """
    return None if value is None else float(value)


def fetch_drift(connection: Any, reference_days: int = REFERENCE_DAYS, current_days: int = CURRENT_DAYS,
                minimum: int | None = None) -> dict[str, Any]:
    """Lit les comptes des deux fenêtres et renvoie le rapport de dérive.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    reference_days : int
        Longueur de la fenêtre de référence. Par défaut 28 jours.
    current_days : int
        Longueur de la fenêtre courante. Par défaut 7 jours.
    minimum : int | None
        Questions de la semaine nécessaires pour juger ; `None` par défaut
        pour `min_questions()`.

    Returns
    -------
    dict[str, Any]
        Le rapport de dérive (voir `build_report`).
    """

    # Questions : distributions, nombre et mesures par fenêtre
    params = {"reference_days": int(reference_days), "current_days": int(current_days)}
    question_rows = _rows(connection, QUESTION_DISTRIBUTIONS_SQL, params)
    question_counts: dict[str, int] = {}
    question_measures: dict[str, dict[str, Any]] = {}
    for window, questions, p95, degraded in _rows(connection, QUESTION_MEASURES_SQL, params):
        question_counts[window] = int(questions)
        question_measures[window] = {"latence_p95_ms": _float(p95), "part_sans_mistral": _float(degraded)}
    # Avis des visiteurs, puis collecte : distributions et mesures par fenêtre
    feedback = {window: {"avis": int(count), "part_pouces_haut": _float(share)}
                for window, count, share in _rows(connection, FEEDBACK_SQL, params)}
    collect_rows = _rows(connection, COLLECT_DISTRIBUTIONS_SQL, params)
    collect_measures = {window: {"nuits": int(nights), "taux_rejet": _float(rate), "score_juge": _float(score)}
                        for window, nights, rate, score in _rows(connection, COLLECT_MEASURES_SQL, params)}
    return build_report(question_rows=question_rows, question_counts=question_counts,
                        question_measures=question_measures, feedback=feedback, collect_rows=collect_rows,
                        collect_measures=collect_measures, minimum=minimum, reference_days=reference_days,
                        current_days=current_days)


# ---------------------------------------------------------------------------
# À la demande : uv run python -m agent.drift
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> None:
    """Calculer le rapport de dérive à la demande et l'afficher (texte ou JSON).

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments de la ligne de commande ; `None` par défaut pour `sys.argv`.
    """
    # Lire les options
    parser = argparse.ArgumentParser(description="Rapport de dérive SortirIA (questions, réponses, collecte).")
    parser.add_argument("--jours-courant", type=int, default=CURRENT_DAYS)
    parser.add_argument("--jours-reference", type=int, default=REFERENCE_DAYS)
    parser.add_argument("--minimum", type=int, default=None,
                        help="Questions de la semaine nécessaires pour juger (DERIVE_MIN_QUESTIONS).")
    parser.add_argument("--json", action="store_true", help="Affiche le rapport complet en JSON.")
    args = parser.parse_args(argv)

    from agent.services import connect_postgres
    from config.environment import load_environment

    # Lire les comptes en base, puis afficher le rapport et les alertes
    load_environment()
    with connect_postgres() as connection:
        report = fetch_drift(connection, args.jours_reference, args.jours_courant, args.minimum)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("\n".join(report_lines(report)).strip())
    for alert in drift_alerts(report):
        print(f"ALERTE : {alert}")


if __name__ == "__main__":
    main()
