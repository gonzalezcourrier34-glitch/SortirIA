"""Décisions de routage de l'agent de collecte.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `routing.py` de Chess Agent (projet 13) : le routage est séparé des nœuds
pour être lisible et testé à part.
"""

from __future__ import annotations

from agent.state import CollectionState

DEFAULT_MAX_ATTEMPTS = 2

ROUTE_RETRY = "retry"
ROUTE_PREPARE = "prepare"
ROUTE_REPORT = "report"


def route_after_extraction(state: CollectionState) -> str:
    """Après l'extraction : continuer, réessayer, ou aller directement au bilan.

    - des éléments ont été extraits : on prépare ;
    - rien n'a été extrait et il reste un essai : on réessaie ;
    - rien après le dernier essai : on écrit un bilan d'échec (l'agent ne
      plante jamais sans laisser de trace).
    """

    if state.get("articles"):
        return ROUTE_PREPARE
    if state.get("attempts", 0) < state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        return ROUTE_RETRY
    return ROUTE_REPORT
