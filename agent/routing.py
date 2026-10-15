"""Décisions de routage de l'agent de collecte.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `routing.py` de Chess Agent (projet 13) : le routage est séparé des nœuds
pour être lisible et testé à part.
"""

from __future__ import annotations

from agent.state import CollectionState
from src.event.event_normalizer import normalize_for_comparison

DEFAULT_MAX_ATTEMPTS = 2

ROUTE_RETRY = "retry"
ROUTE_PREPARE = "prepare"
ROUTE_REPORT = "report"


STATUS_OK = "ok"
STATUS_PARTIAL = "partiel"
STATUS_FAILED = "echec"


def source_statuses(state: CollectionState) -> dict[str, str]:
    """Statut de chaque source prévue : « ok », « partiel » ou « echec ».

    D'abord d'après le bilan des extracteurs (`extractor_stats`, qui porte
    `source_id` et `status` depuis le 15/10/2026) ; à défaut, d'après
    `failed_sources`, qui peut contenir l'identifiant (« openagenda ») ou le
    nom complet de l'extracteur (« OpenAgenda - Montpellier et bassin de Thau »).
    « partiel » : l'extracteur a rendu des événements, mais une partie de sa
    lecture a échoué (un PDF sur deux en 404, un agenda OpenAgenda en 429...).
    """

    by_source: dict[str, str] = {}
    for stat in state.get("extractor_stats", []):
        source_id = stat.get("source_id")
        if source_id:
            by_source[str(source_id)] = str(stat.get("status", ""))
    failed_names = [normalize_for_comparison(name) for name in state.get("failed_sources", [])]
    statuses = {}
    for source_id in state.get("sources", []):
        status = by_source.get(source_id)
        if status is None:
            matched = any(normalize_for_comparison(source_id) == name or normalize_for_comparison(source_id) in name.split()
                          for name in failed_names)
            statuses[source_id] = STATUS_FAILED if matched else STATUS_OK
        elif status == "failed":
            statuses[source_id] = STATUS_FAILED
        elif status == "partial_success":
            statuses[source_id] = STATUS_PARTIAL
        else:
            statuses[source_id] = STATUS_OK
    return statuses


def failed_source_ids(state: CollectionState) -> list[str]:
    """Identifiants des sources prévues qui sont en échec (à relancer)."""

    return [source_id for source_id, status in source_statuses(state).items() if status == STATUS_FAILED]


def route_after_extraction(state: CollectionState) -> str:
    """Après l'extraction : continuer, réessayer, ou aller directement au bilan.

    - une ou plusieurs sources en échec et il reste un essai : on réessaie
      **seulement celles-là** (modifié le 14/10/2026 : avant, l'agent ne
      réessayait que si rien du tout n'était extrait) ;
    - rien n'a été extrait et il reste un essai : on réessaie tout ;
    - des éléments ont été extraits : on prépare ;
    - rien après le dernier essai : on écrit un bilan d'échec (l'agent ne
      plante jamais sans laisser de trace).
    """

    has_attempt_left = state.get("attempts", 0) < state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    if has_attempt_left and (failed_source_ids(state) or not state.get("articles")):
        return ROUTE_RETRY
    if state.get("articles"):
        return ROUTE_PREPARE
    return ROUTE_REPORT
