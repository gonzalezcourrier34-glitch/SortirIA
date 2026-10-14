"""Nœuds de l'agent de collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Un nœud =
une étape, comme les fichiers `nodes/A_*.py` à `H_*.py` de Chess Agent
(projet 13). Chaque nœud reçoit l'état et la configuration LangGraph, et
renvoie seulement les champs qu'il modifie.

Les services (extraction, base, embeddings) arrivent par
`config["configurable"]["services"]` : voir `agent/services.py`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from agent.services import CollectionServices
from agent.state import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    CollectionState,
)
from src.event import event_repository as repository
from src.event.event_embedding import build_embedding_text
from src.event.event_normalizer import normalize_for_comparison
from src.event.event_pipeline import prepare_events
from src.event.event_schema import Event
from src.logger import get_logger

logger = get_logger(__name__)

# Au-delà de cette part d'événements rejetés, on lève une alerte.
REJECTION_ALERT_RATIO = 0.20
# Nombre maximal d'embeddings calculés par nuit (garde-fou sur le coût Mistral).
MAX_EMBEDDINGS_PER_RUN = 3000
EMBEDDING_BATCH_SIZE = 64


def get_services(config: RunnableConfig) -> CollectionServices:
    """Récupère les services injectés dans la configuration du graphe."""

    return config["configurable"]["services"]


# ---------------------------------------------------------------------------
# A. Planification
# ---------------------------------------------------------------------------

def plan_sources(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Choisit les sources de la nuit (celles demandées, sinon celles par défaut)."""

    services = get_services(config)
    if state.get("sources"):
        sources = list(state["sources"])  # demandées explicitement (--source)
    else:
        # Décision de l'agent : on ne lance pas une source sans configuration
        # active (ex. aucun calendrier de partenaire déclaré pour l'instant),
        # sinon elle remonterait comme une panne chaque nuit.
        sources = [name for name in services.default_sources if services.is_configured(name)]
        skipped = [name for name in services.default_sources if name not in sources]
        if skipped:
            logger.info("Sources sans configuration active, non lancées : %s.", skipped)
    logger.info("Collecte nocturne : sources prévues %s.", sources)
    return {
        "sources": sources,
        "started_at": services.now().isoformat(),
        "attempts": 0,
        "errors": [],
        "alerts": [],
    }


# ---------------------------------------------------------------------------
# B. Extraction
# ---------------------------------------------------------------------------

def extract_sources(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Lance les extracteurs. Une erreur ne casse pas la nuit : elle est notée."""

    services = get_services(config)
    attempt = state.get("attempts", 0) + 1
    try:
        output = services.extract(state["sources"])
    except Exception as error:  # l'agent doit toujours arriver au bilan
        logger.exception("Extraction en échec (essai %s).", attempt)
        return {
            "attempts": attempt,
            "articles": [],
            "failed_sources": list(state["sources"]),
            "errors": [*state.get("errors", []), f"extraction (essai {attempt}) : {error}"],
        }
    logger.info("Extraction (essai %s) : %s élément(s).", attempt, len(output.articles))
    return {"attempts": attempt, "articles": output.articles, "failed_sources": output.failed_sources}


# ---------------------------------------------------------------------------
# C. Préparation
# ---------------------------------------------------------------------------

def prepare(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Convertit, valide et dédoublonne les événements."""

    services = get_services(config)
    events, report = prepare_events(state.get("articles", []), now=services.now())
    logger.info("Préparation : %s événement(s) retenu(s), bilan %s.", len(events), report.to_dict())
    return {"events": [event.to_dict() for event in events], "preparation": report.to_dict()}


# ---------------------------------------------------------------------------
# D. Chargement, expiration, embeddings
# ---------------------------------------------------------------------------

def load_events(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Enregistre les événements, retire les passés et calcule les embeddings manquants."""

    if state.get("dry_run"):
        logger.info("Mode essai : aucune écriture en base.")
        return {"events_upserted": 0, "events_expired": 0, "events_removed": 0, "events_embedded": 0}

    services = get_services(config)
    events = [Event.from_dict(data) for data in state.get("events", [])]
    complete_sources = sources_safe_to_sweep(state)
    with services.connect() as connection:
        upserted = repository.upsert_events(connection, events)
        removed = repository.delete_unseen_events(connection, complete_sources)
        expired = repository.delete_past_events(connection, services.now())
        embedded = embed_missing(connection, services)
    logger.info(
        "Base : %s enregistré(s), %s disparu(s) de leur source, %s expiré(s), %s embedding(s).",
        upserted, removed, expired, embedded,
    )
    return {
        "events_upserted": upserted,
        "events_expired": expired,
        "events_removed": removed,
        "events_embedded": embedded,
    }


def sources_safe_to_sweep(state: CollectionState) -> list[str]:
    """Sources dont on peut retirer les événements qui n'ont pas été revus.

    Une décision de l'agent : on ne fait ce ménage que pour une source lue
    sans échec ET qui a fourni au moins un événement. Si une source tombe en
    panne ou renvoie une liste vide (API en maintenance), on garde ses
    événements de la veille plutôt que de vider la base.
    """

    failed = {source for source, status in source_statuses(state).items() if status == "echec"}
    counts: dict[str, int] = {}
    for event in state.get("events", []):
        counts[event.get("source", "")] = counts.get(event.get("source", ""), 0) + 1
    return [source for source in state.get("sources", []) if source not in failed and counts.get(source)]


def embed_missing(connection: Any, services: CollectionServices) -> int:
    """Calcule par lots les embeddings des événements qui n'en ont pas encore."""

    rows = repository.fetch_events_missing_embedding(connection, limit=MAX_EMBEDDINGS_PER_RUN)
    total = 0
    for start in range(0, len(rows), EMBEDDING_BATCH_SIZE):
        batch = rows[start:start + EMBEDDING_BATCH_SIZE]
        vectors = services.embed([build_embedding_text(row) for row in batch])
        total += repository.update_embeddings(
            connection,
            zip((row["event_id"] for row in batch), vectors, strict=True),
            services.embedding_model,
        )
    return total


# ---------------------------------------------------------------------------
# E. Bilan
# ---------------------------------------------------------------------------

def decide_status(state: CollectionState) -> tuple[str, list[str]]:
    """Statut de la nuit et alertes à remonter."""

    alerts: list[str] = []
    preparation = state.get("preparation") or {}
    extracted = preparation.get("events_extracted", 0)
    rejected = preparation.get("events_rejected", 0)
    kept = len(state.get("events", []))

    if extracted == 0:
        alerts.append("Aucun événement collecté cette nuit.")
    if state.get("failed_sources"):
        alerts.append(f"Sources en échec : {', '.join(state['failed_sources'])}.")
    if extracted and rejected / extracted > REJECTION_ALERT_RATIO:
        alerts.append(f"{rejected} événement(s) rejeté(s) sur {extracted} (plus de {REJECTION_ALERT_RATIO:.0%}).")

    if kept == 0:
        return STATUS_FAILED, alerts
    if state.get("failed_sources") or state.get("errors"):
        return STATUS_PARTIAL, alerts
    return STATUS_SUCCESS, alerts


def source_statuses(state: CollectionState) -> dict[str, str]:
    """« ok » ou « echec » pour chaque source prévue.

    `failed_sources` peut contenir l'identifiant (« openagenda ») ou le nom
    complet de l'extracteur (« OpenAgenda - Montpellier et bassin de Thau »).
    """

    failed = [normalize_for_comparison(name) for name in state.get("failed_sources", [])]
    statuses = {}
    for source_id in state.get("sources", []):
        key = normalize_for_comparison(source_id)
        is_failed = any(key == name or key in name.split() for name in failed)
        statuses[source_id] = "echec" if is_failed else "ok"
    return statuses


def build_source_rows(state: CollectionState) -> list[dict[str, Any]]:
    """Détail par source pour `sortiria.collect_run_sources`.

    Une source est en échec si son identifiant apparaît dans `failed_sources`
    (qui peut contenir l'identifiant ou le nom complet, ex. « OpenAgenda -
    Montpellier et bassin de Thau »).
    """

    statuses = source_statuses(state)
    events = state.get("events", [])
    rows = []
    for source_id in state.get("sources", []):
        rows.append({
            "source_id": source_id,
            "status": statuses[source_id],
            "attempts": state.get("attempts", 1),
            # Dans l'état du graphe, les événements sont des dictionnaires
            # (Event.to_dict), pas des objets Event.
            "events_kept": sum(1 for event in events if event.get("source") == source_id),
        })
    return rows


def write_report(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Écrit le bilan dans `sortiria.collect_runs` et journalise les alertes."""

    status, alerts = decide_status(state)
    for alert in alerts:
        logger.warning("ALERTE collecte : %s", alert)

    report = {
        **(state.get("preparation") or {}),
        "status": status,
        "events_upserted": state.get("events_upserted", 0),
        "events_expired": state.get("events_expired", 0),
        "events_removed": state.get("events_removed", 0),
        "started_at": state.get("started_at"),
        "failed_sources": state.get("failed_sources", []),
        "message": " | ".join([*alerts, *state.get("errors", [])]),
    }
    run_id = None
    if not state.get("dry_run"):
        try:
            with get_services(config).connect() as connection:
                run_id = repository.record_collect_run(connection, report)
                repository.record_run_sources(connection, run_id, build_source_rows(state))
        except Exception:
            logger.exception("Impossible d'enregistrer le bilan de la collecte.")
    logger.info("Collecte terminée : statut %s.", status)
    return {"status": status, "alerts": alerts, "run_id": run_id}
