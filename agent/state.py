"""État partagé par les nœuds de l'agent de collecte.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `state.py` de l'agent de Chess Agent (projet 13).

Chaque nœud lit les champs dont il a besoin et renvoie un dictionnaire
partiel : LangGraph fusionne ces mises à jour dans l'état. Les services
(extraction, base, embeddings) ne sont jamais stockés dans l'état : ils sont
injectés par la configuration du graphe (voir `agent/services.py`).
"""

from __future__ import annotations

from typing import Any, TypedDict

# Statuts possibles d'une collecte.
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial_success"
STATUS_FAILED = "failed"


class CollectionState(TypedDict, total=False):
    """Ce que l'agent sait au fil de la collecte."""

    # Paramètres
    dry_run: bool                     # True : aucune écriture en base
    max_attempts: int                 # nombre maximal d'essais d'extraction

    # Planification
    sources: list[str]                # extracteurs à lancer (ex. ["openagenda"])
    started_at: str                   # début de la collecte (ISO 8601)

    # Extraction
    attempts: int
    articles: list[dict[str, Any]]    # sortie brute des extracteurs
    failed_sources: list[str]

    # Préparation
    events: list[dict[str, Any]]      # événements validés et dédoublonnés
    preparation: dict[str, Any]       # bilan de event_pipeline.prepare_events

    # Chargement
    events_upserted: int
    events_expired: int
    events_removed: int               # événements disparus de leur source
    events_embedded: int

    # Bilan
    status: str
    alerts: list[str]
    run_id: int | None
    errors: list[str]
