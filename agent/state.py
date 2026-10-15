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
    discover_agendas: bool            # True : découverte OpenAgenda forcée (sinon le jour prévu)
    max_attempts: int                 # nombre maximal d'essais d'extraction

    # Planification
    sources: list[str]                # extracteurs à lancer (ex. ["openagenda"])
    started_at: str                   # début de la collecte (ISO 8601)
    skipped_sources: list[str]        # sources sans configuration active, non lancées

    # Extraction
    attempts: int
    articles: list[dict[str, Any]]    # sortie brute des extracteurs
    failed_sources: list[str]
    source_attempts: dict[str, int]   # essais par source (réessai ciblé de celles en échec)
    extractor_stats: list[dict[str, Any]]  # bilan de chaque extracteur (suivi de la qualité)
    coverage_db: dict[str, dict[str, int]]  # couverture de la semaine lue en base après chargement
    llm_usage: dict[str, dict[str, int]]  # tokens Mistral : {modèle: {"input", "output"}}

    # Préparation
    events: list[dict[str, Any]]      # événements validés et dédoublonnés
    preparation: dict[str, Any]       # bilan de event_pipeline.prepare_events

    # Chargement
    events_upserted: int
    events_expired: int
    events_removed: int               # événements disparus de leur source
    source_drops: dict[str, tuple[int, int]]  # source -> (avant, maintenant) en forte baisse
    purged: dict[str, int]            # lignes supprimées par la purge RGPD
    events_embedded: int
    events_enriched: int                  # événements enrichis par Mistral cette nuit
    events_capped: int                    # retirés par le plafond (les plus lointains)
    events_total: int                     # taille de la base après la collecte
    ratings: dict[str, Any]               # notes des clients : nouvelles, moyenne, partenaires mal notés
    enrichment: dict[str, Any]            # bilan de l'enrichissement (refus, « pas une sortie »...)

    # Contrôle de qualité (juge Mistral sur un échantillon)
    quality: dict[str, Any]               # score, taux par question, exemples de problèmes

    # Surveillance (après le chargement)
    coverage: dict[str, dict[str, int]]   # sorties de la semaine : {zone: {public: n}}
    watch_alerts: list[str]               # programmes, partenaires, couverture, coût
    cost_usd: float                       # coût Mistral estimé de la nuit
    proposals: list[dict[str, Any]]       # réparations proposées (à valider par un humain)

    # Suivi (MLflow, collect_runs)
    node_durations: dict[str, float]      # secondes passées dans chaque nœud

    # Bilan
    status: str
    alerts: list[str]
    run_id: int | None
    errors: list[str]
