"""Nœuds de l'agent de collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Un nœud =
une étape, comme les fichiers `nodes/A_*.py` à `H_*.py` de Chess Agent
(projet 13). Chaque nœud reçoit l'état et la configuration LangGraph, et
renvoie seulement les champs qu'il modifie.

Les services (extraction, base, embeddings) arrivent par
`config["configurable"]["services"]` : voir `agent/services.py`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from langchain_core.runnables import RunnableConfig

from agent import watch
from agent.routing import STATUS_OK, failed_source_ids, source_statuses
from agent.services import CollectionServices
from agent.state import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    CollectionState,
)
from src.event import event_repository as repository
from src.event.document_mapper import PROMPT_VERSION
from src.event.event_embedding import build_embedding_text
from src.event.event_pipeline import prepare_events
from src.event.event_schema import Event
from src.logger import get_logger

logger = get_logger(__name__)

# Au-delà de cette part d'événements rejetés, on lève une alerte.
REJECTION_ALERT_RATIO = 0.20
# Nombre maximal d'embeddings calculés par nuit (garde-fou sur le coût Mistral).
MAX_EMBEDDINGS_PER_RUN = 10_000   # le plafond de la base : tout peut être indexé en une nuit
EMBEDDING_BATCH_SIZE = 64
# Enrichissement Mistral : 3 000 événements par nuit au plus (fenêtre de
# 3 mois : la base complète est enrichie en deux ou trois nuits, ensuite
# seuls les nouveaux passent), par lots de 15 (une réponse JSON lisible).
MAX_ENRICHMENTS_PER_RUN = 3000
ENRICHMENT_BATCH_SIZE = 15
MAX_EMBEDDING_FAILURES = 3      # lots d'embeddings en échec avant d'abandonner la nuit


def get_services(config: RunnableConfig) -> CollectionServices:
    """Récupère les services injectés dans la configuration du graphe."""

    return config["configurable"]["services"]


# ---------------------------------------------------------------------------
# A. Planification
# ---------------------------------------------------------------------------

def disabled_sources() -> list[str]:
    """Sources coupées sur ce serveur : variable `SOURCES_DESACTIVEES` (« document,rss »).

    Ajout du 15/10/2026, pour Azure : le site de Sète agglopôle renvoie une page
    web au lieu du PDF aux adresses des centres de données, alors qu'il sert
    le PDF depuis une box. La source « document » est alors coupée sur Azure
    (sinon elle remonterait comme une panne chaque nuit) et lue depuis le PC
    (`python -m agent --source document`). Ses événements ne sont pas balayés :
    seule une source lancée et lue sans échec peut l'être.
    """

    return [name.strip() for name in os.getenv("SOURCES_DESACTIVEES", "").split(",") if name.strip()]


def plan_sources(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Choisit les sources de la nuit (celles demandées, sinon celles par défaut)."""

    services = get_services(config)
    skipped: list[str] = []
    disabled: list[str] = []
    if state.get("sources"):
        sources = list(state["sources"])  # demandées explicitement (--source)
    else:
        # Décision de l'agent : on ne lance pas une source sans configuration
        # active (ex. aucun calendrier de partenaire déclaré pour l'instant),
        # sinon elle remonterait comme une panne chaque nuit.
        try:
            sources = [name for name in services.default_sources if services.is_configured(name)]
        except Exception as error:  # sources.yaml invalide : on va au bilan, pas au plantage
            logger.exception("Configuration des sources illisible : aucune source lancée.")
            return {"sources": [], "skipped_sources": [], "started_at": services.now().isoformat(),
                    "attempts": 0, "errors": [f"configuration des sources : {error}"], "alerts": []}
        skipped = [name for name in services.default_sources if name not in sources]
        if skipped:
            logger.info("Sources sans configuration active, non lancées : %s.", skipped)
        disabled = [name for name in sources if name in disabled_sources()]
        if disabled:
            sources = [name for name in sources if name not in disabled]
            logger.info("Sources coupées sur ce serveur (SOURCES_DESACTIVEES) : %s.", disabled)
    logger.info("Collecte nocturne : sources prévues %s.", sources)
    return {
        "sources": sources,
        "skipped_sources": skipped,
        "disabled_sources": disabled,
        "started_at": services.now().isoformat(),
        "attempts": 0,
        "errors": [],
        "alerts": [],
    }


# ---------------------------------------------------------------------------
# B. Extraction
# ---------------------------------------------------------------------------

def extract_sources(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Lance les extracteurs. Une erreur ne casse pas la nuit : elle est notée.

    Au premier essai, toutes les sources prévues. Aux essais suivants,
    **seulement les sources en échec** : leurs événements s'ajoutent à ceux
    des sources qui ont réussi (décision ajoutée le 14/10/2026). Si rien
    n'avait été extrait du tout, tout est relancé.
    """

    services = get_services(config)
    attempt = state.get("attempts", 0) + 1
    previous_articles = list(state.get("articles", []))
    retry = failed_source_ids(state) if attempt > 1 else []
    to_run = retry or list(state["sources"])
    if attempt > 1 and retry:
        logger.info("Nouvel essai (essai %s) pour les sources en échec : %s.", attempt, retry)
    elif attempt > 1:
        previous_articles = []   # rien n'avait été extrait : tout est relancé
    source_attempts = dict(state.get("source_attempts", {}))
    for name in to_run:
        source_attempts[name] = source_attempts.get(name, 0) + 1
    try:
        output = services.extract(to_run)
    except Exception as error:  # l'agent doit toujours arriver au bilan
        logger.exception("Extraction en échec (essai %s).", attempt)
        return {
            "attempts": attempt,
            "articles": previous_articles,
            "failed_sources": list(to_run),
            "source_attempts": source_attempts,
            "errors": [*state.get("errors", []), f"extraction (essai {attempt}) : {error}"],
        }
    logger.info("Extraction (essai %s) : %s élément(s).", attempt, len(output.articles))
    return {
        "attempts": attempt,
        "articles": previous_articles + list(output.articles),
        "failed_sources": output.failed_sources,
        "source_attempts": source_attempts,
        "llm_usage": watch.merge_usage(state.get("llm_usage", {}), output.usage),
        # Un extracteur relancé remplace son bilan du premier essai.
        "extractor_stats": [
            *[stat for stat in state.get("extractor_stats", [])
              if stat.get("name") not in {new.get("name") for new in output.extractor_stats}],
            *output.extractor_stats,
        ],
    }


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
    """Enregistre les événements, fait le ménage, calcule les embeddings, purge.

    Tout se passe dans une seule transaction : si une étape échoue, la base
    reste dans l'état de la veille. 17/10/2026 : l'erreur ne remonte plus hors
    du graphe (une contrainte violée par une fiche DATAtourisme avait arrêté la
    nuit sans bilan ni mail) ; la nuit continue jusqu'au bilan, en échec.
    """

    try:
        return write_events(state, config)
    except Exception as error:  # la nuit doit toujours finir par un bilan
        logger.exception("Écriture en base impossible : la base reste celle de la veille.")
        return {"events_upserted": 0, "events_expired": 0, "events_removed": 0, "events_embedded": 0,
                "events_enriched": 0, "source_drops": {}, "purged": {},
                "load_error": f"{type(error).__name__} : {str(error).splitlines()[0][:300]}"}


def write_events(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Le travail de `load_events`, dans une transaction (lève en cas d'erreur)."""

    if state.get("dry_run"):
        logger.info("Mode essai : aucune écriture en base.")
        return {"events_upserted": 0, "events_expired": 0, "events_removed": 0,
                "events_embedded": 0, "events_enriched": 0, "source_drops": {}, "purged": {}}

    services = get_services(config)
    events = [Event.from_dict(data) for data in state.get("events", [])]
    with services.connect() as connection:
        previous = repository.fetch_previous_source_counts(connection)
        drops = detect_source_drops(state, previous)
        # Une source en forte baisse garde ses événements de la veille.
        sweepable = [source for source in sources_safe_to_sweep(state) if source not in drops]
        # Fichiers des partenaires : partenaire créé ou mis à jour, partner_id
        # attribué, bilan du fichier enregistré (avant l'écriture des événements).
        repository.attach_partners(connection, events)
        upserted = repository.upsert_events(connection, events)
        removed = repository.delete_unseen_events(connection, sweepable)
        expired = repository.delete_past_events(connection, services.now())
        # Plafond de sécurité : au-delà, les événements les plus lointains partent
        # (statistiques gardées). Avant l'enrichissement : on ne paie pas pour eux.
        capped = repository.enforce_event_cap(connection, watch.max_events())
        total = repository.count_events(connection)
        # Enrichissement avant les embeddings : ils incluent ainsi la catégorie
        # unifiée et le résumé.
        enrichment = enrich_missing(connection, services)
        embedded, embedding_tokens = embed_missing(connection, services)
        # Notes des clients : commentaires en attente modérés, bilan des notes.
        ratings = review_ratings(connection, services) if services.ratings_enabled else {}
        purged = repository.purge_old_data(connection)
        coverage_db = repository.fetch_weekly_coverage(connection, watch.COVERAGE_DAYS)
    vocabulary_size, search_gaps = refresh_search_vocabulary(services)
    drift = measure_drift(services)
    logger.info(
        "Base : %s enregistré(s), %s disparu(s) de leur source, %s expiré(s), %s enrichi(s) "
        "(%s réponse(s) refusée(s), %s lot(s) en échec), %s embedding(s), %s retiré(s) par le plafond, "
        "%s en base. Purge : %s.",
        upserted, removed, expired, enrichment["enriched"], enrichment["invalid"], enrichment["failed_batches"],
        embedded, capped, total, purged,
    )
    return {
        "events_upserted": upserted,
        "events_expired": expired,
        "events_removed": removed,
        "events_embedded": embedded,
        "source_drops": drops,
        "purged": purged,
        "events_enriched": enrichment["enriched"],
        "enrichment": enrichment,
        "events_capped": capped,
        "events_total": total,
        "coverage_db": coverage_db,
        "vocabulary_size": vocabulary_size,
        "search_gaps": search_gaps,
        "drift": drift,
        "ratings": ratings,
        "llm_usage": watch.merge_usage(
            state.get("llm_usage", {}), enrichment["usage"],
            {services.embedding_model: {"input": embedding_tokens, "output": 0}},
        ),
    }


def review_ratings(connection: Any, services: CollectionServices) -> dict[str, Any]:
    """Modère les commentaires restés en attente, puis fait le bilan des notes clients."""

    moderated = 0
    if services.moderate is not None:
        for row in repository.fetch_pending_comments(connection):
            status, details = services.moderate(row["comment"])
            if status != "en_attente":
                repository.update_comment_status(connection, row["rating_id"], status, details)
                moderated += 1
    return {**repository.ratings_summary(connection), "moderated": moderated}


def enrich_missing(connection: Any, services: CollectionServices) -> dict[str, Any]:
    """Enrichit par Mistral les événements nouveaux ou modifiés (catégorie, publics, résumé).

    Un lot en échec (réponse illisible, quota) est ignoré : ses événements
    seront repris la nuit suivante. Renvoie le bilan : enrichis, refusés par
    la validation, signalés « pas une sortie », lots en échec, consommation.
    """

    from src.event.event_enrichment import ENRICHMENT_VERSION, price_band

    summary: dict[str, Any] = {"enriched": 0, "invalid": 0, "not_outing": 0, "failed_batches": 0, "usage": {}}
    if services.enrich is None:
        return summary
    rows = repository.fetch_events_to_enrich(connection, ENRICHMENT_VERSION, limit=MAX_ENRICHMENTS_PER_RUN)
    for start in range(0, len(rows), ENRICHMENT_BATCH_SIZE):
        batch = rows[start:start + ENRICHMENT_BATCH_SIZE]
        try:
            results, usage = services.enrich(batch)
        except Exception as error:  # noqa: BLE001 (un lot en panne ne bloque ni la nuit ni les autres lots)
            logger.warning("Enrichissement : lot %s en échec (%s).", start // ENRICHMENT_BATCH_SIZE + 1, error)
            summary["failed_batches"] += 1
            continue
        summary["usage"] = watch.merge_usage(summary["usage"], usage)
        updates = []
        for row in batch:
            result = results.get(str(row["event_id"]))
            if result is None:
                summary["invalid"] += 1
                continue
            summary["not_outing"] += 0 if result.est_une_sortie else 1
            updates.append({
                "event_id": row["event_id"],
                "category_norm": result.categorie,
                "summary": result.resume,
                "price_band": price_band(row.get("is_free"), row.get("price_min"), row.get("price_max")),
                "audiences_llm": result.publics,
                "is_outing": result.est_une_sortie,
            })
        summary["enriched"] += repository.update_enrichments(connection, updates, ENRICHMENT_VERSION)
    return summary


# Seuils de l'alerte « baisse anormale » : une source qui apportait au moins
# 20 événements et qui en apporte moins de la moitié.
DROP_MIN_PREVIOUS = 20
DROP_RATIO = 0.5


def detect_source_drops(state: CollectionState, previous: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Sources dont le nombre d'événements s'effondre par rapport à la dernière fois.

    Une décision de l'agent : une API qui répond mais renvoie soudain beaucoup
    moins (filtre cassé, agenda vidé, pagination perdue comme le 14/10/2026)
    ne doit ni vider la base ni passer inaperçue.
    """

    current: dict[str, int] = {}
    for event in state.get("events", []):
        current[event.get("source", "")] = current.get(event.get("source", ""), 0) + 1
    drops = {}
    for source in state.get("sources", []):
        before, now = previous.get(source, 0), current.get(source, 0)
        if before >= DROP_MIN_PREVIOUS and now < before * DROP_RATIO:
            drops[source] = (before, now)
    return drops


def sources_safe_to_sweep(state: CollectionState) -> list[str]:
    """Sources dont on peut retirer les événements qui n'ont pas été revus.

    Une décision de l'agent : on ne fait ce ménage que pour une source lue
    sans échec ni échec partiel, ET qui a fourni au moins un événement. Si une source tombe en
    panne ou renvoie une liste vide (API en maintenance), on garde ses
    événements de la veille plutôt que de vider la base.
    """

    # « partiel » compte comme non sûr : si un des deux PDF est en 404, ses
    # événements ne sont pas relus, ils ne doivent pas être pris pour disparus.
    healthy = {source for source, status in source_statuses(state).items() if status == STATUS_OK}
    counts: dict[str, int] = {}
    for event in state.get("events", []):
        counts[event.get("source", "")] = counts.get(event.get("source", ""), 0) + 1
    return [source for source in state.get("sources", []) if source in healthy and counts.get(source)]


def embed_missing(connection: Any, services: CollectionServices) -> tuple[int, int]:
    """Calcule par lots les embeddings des événements qui n'en ont pas encore.

    Renvoie (embeddings calculés, tokens estimés) : le nombre de tokens sert
    au coût de la nuit (l'API ne le renvoie pas, voir `watch.estimate_tokens`).

    Un lot en échec (clé absente, 429, 503) est journalisé et passé : avant le
    15/10/2026, l'exception remontait, annulait toute la transaction de la nuit
    (événements, enrichissements payés) et l'agent s'arrêtait sans bilan.
    """

    rows = repository.fetch_events_missing_embedding(connection, limit=MAX_EMBEDDINGS_PER_RUN)
    total, tokens, failed = 0, 0, 0
    for start in range(0, len(rows), EMBEDDING_BATCH_SIZE):
        batch = rows[start:start + EMBEDDING_BATCH_SIZE]
        texts = [build_embedding_text(row) for row in batch]
        try:
            vectors = services.embed(texts)
        except Exception as error:  # noqa: BLE001 (Mistral absent : les embeddings attendront la nuit suivante)
            failed += 1
            if failed == 1:
                logger.warning("Embeddings : lot %s en échec (%s).", start // EMBEDDING_BATCH_SIZE + 1, error)
            if failed >= MAX_EMBEDDING_FAILURES:
                logger.error("Embeddings : %s lots en échec de suite, on arrête pour cette nuit.", failed)
                break
            continue
        tokens += watch.estimate_tokens(texts)
        total += repository.update_embeddings(
            connection,
            zip((row["event_id"] for row in batch), vectors, strict=True),
            services.embedding_model,
        )
    return total, tokens


# ---------------------------------------------------------------------------
# D bis. Contrôle de qualité par Mistral (ajouté le 14/10/2026)
# ---------------------------------------------------------------------------

def judge_quality(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Fait juger un échantillon d'événements par Mistral (src/event/quality_judge.py).

    Échantillon tiré dans la base (événements proposables, enrichis) ; en
    mode essai, parmi les événements de la nuit. Une panne du juge ne fait
    jamais échouer la collecte : le score est simplement absent.
    """

    from src.event.quality_judge import SAMPLE_SIZE, summarize

    services = get_services(config)
    if services.judge is None:
        return {"quality": {}}
    try:
        if state.get("dry_run"):
            import random

            rows = random.sample(state.get("events", []), min(SAMPLE_SIZE, len(state.get("events", []))))
        else:
            with services.connect() as connection:
                rows = repository.fetch_quality_sample(connection, SAMPLE_SIZE)
        if not rows:
            return {"quality": {}}
        verdicts, usage = services.judge(rows)
    except Exception as error:  # noqa: BLE001 (le juge est un contrôle, pas une étape indispensable)
        logger.warning("Juge de qualité indisponible : %s", error)
        return {"quality": {"error": str(error)}}
    quality = summarize(verdicts, rows)
    logger.info("Qualité : %s.", quality)
    return {"quality": quality, "llm_usage": watch.merge_usage(state.get("llm_usage", {}), usage)}


# ---------------------------------------------------------------------------
# E. Surveillance (ajoutée le 14/10/2026, voir agent/watch.py)
# ---------------------------------------------------------------------------

def measure_drift(services: CollectionServices) -> dict[str, Any]:
    """Dérive des questions, des réponses et de la collecte (agent/drift.py, 19/10/2026).

    Calculée chaque nuit (fenêtre glissante, gardée dans `collect_runs.drift`) ;
    envoyée et transformée en alertes le lundi seulement (`watch_collection`).
    Transaction à part, jamais bloquante : une erreur ici ne coûte que le
    rapport de dérive de la nuit.
    """

    from agent.drift import fetch_drift

    try:
        with services.connect() as connection:
            report = fetch_drift(connection)
    except Exception:
        logger.warning("Rapport de dérive indisponible cette nuit.", exc_info=True)
        return {}
    logger.info("Dérive : %s (%s question(s) cette semaine, %s nuit(s)).", report.get("statut"),
                (report.get("questions") or {}).get("courant", 0), (report.get("nuits") or {}).get("courant", 0))
    return report


def refresh_search_vocabulary(services: CollectionServices) -> tuple[int, dict[str, Any]]:
    """Après l'écriture : vocabulaire des titres recalculé (sujets reconnus par
    la recherche) et recherches de la semaine restées sans réponse exacte.

    Transaction à part : une base dont le schéma n'a pas encore la table
    `title_vocabulary` ne fait pas échouer la collecte (la recherche lit
    alors les titres directement).
    """

    try:
        with services.connect() as connection:
            size = repository.refresh_title_vocabulary(connection)
            gaps = repository.fetch_search_gaps(connection, watch.SEARCH_REPORT_DAYS)
    except Exception:
        logger.warning("Vocabulaire des titres ou bilan de la recherche indisponible "
                       "(lancer scripts/init_db.py ?).", exc_info=True)
        return 0, {}
    # Pouces bas de la semaine (16/10/2026) : transaction à part, une erreur ici
    # ne fait perdre ni le vocabulaire ni le bilan de la recherche.
    try:
        with services.connect() as connection:
            gaps["thumbs_down"] = repository.fetch_thumbs_down(connection, watch.SEARCH_REPORT_DAYS)
    except Exception:
        logger.warning("Pouces bas de la semaine illisibles.", exc_info=True)
    logger.info("Vocabulaire des titres : %s racine(s) ; recherche (7 j) : %s question(s), %s sans réponse exacte.",
                size, gaps.get("questions", 0), gaps.get("not_exact", 0))
    return size, gaps


def watch_collection(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Vérifie ce qui demande une intervention : programmes, partenaires, couverture, coût.

    Fonctionne aussi en mode essai : tout est calculé à partir de l'état et de
    la configuration (la couverture lue en base par `load_events` est utilisée
    quand elle existe).
    """

    services = get_services(config)
    today = services.now().date()
    try:
        inputs = services.watch_inputs() or {}
    except Exception:
        logger.exception("Lecture de la configuration à surveiller impossible.")
        inputs = {}
    events = state.get("events", [])
    # Couverture : d'après la base quand elle a été lue (toutes les sources,
    # publics de Mistral compris) ; sinon d'après les événements de la nuit
    # (mode essai). Une source en panne ne fait plus croire à un trou d'offre.
    counts = state.get("coverage_db") or watch.coverage(events, today)
    cost = watch.nightly_cost(state.get("llm_usage", {}))
    # Les partenaires ne sont contrôlés que si leurs fichiers ont bien été lus
    # cette nuit : sinon chacun recevrait « plus aucune séance » à tort.
    partner_files = inputs.get("partner_files", []) if source_statuses(state).get("partenaire") == STATUS_OK else []
    alerts = [
        *watch.check_documents(inputs.get("documents", []), today),
        *watch.check_partners(partner_files, events, today),
        *watch.check_coverage(counts),
        *watch.check_cost(cost),
        *watch.check_quality(state.get("quality") or {}),
        *watch.check_capacity(state.get("events_total"), state.get("events_capped", 0)),
        *watch.check_ratings(state.get("ratings") or {}),
    ]
    # Diagnostic des pannes de la nuit, avec une réparation proposée quand
    # l'agent sait la trouver (agent/diagnosis.py) : jamais appliquée seule.
    from agent.diagnosis import diagnose

    diagnoses = diagnose(state.get("extractor_stats", []), inputs.get("documents", []), services.probe, today)
    alerts += [diagnosis.as_alert() for diagnosis in diagnoses]
    proposals = [
        {"source_id": d.proposal.get("source_id", ""), "kind": d.kind, "diagnosis": d.as_alert(), "action": d.proposal}
        for d in diagnoses if d.proposal
    ]
    agendas, discovery_alerts = discover_new_agendas(state, services, inputs, today)
    alerts += discovery_alerts
    proposals += agenda_proposals(agendas, today)
    # Documents des partenaires (PDF, Word) lus par Mistral : importés seulement
    # après validation humaine (16/10/2026).
    proposals += partner_document_proposals(inputs.get("partner_documents", []), today)
    logger.info("Surveillance : couverture %s, coût estimé %.4f $, %s alerte(s).", counts, cost, len(alerts))
    # Le lundi : ce que les utilisateurs ont cherché sans trouver (bilan par
    # mail), et ce que l'agent en tire : synonymes, agendas à lire, pistes.
    search_report = (state.get("search_gaps") or {}) if watch.search_report_due(today) else {}
    learning_notes: list[str] = []
    feedback_notes: list[str] = []
    # Le lundi aussi : commentaires refusés par la modération, contestables.
    refused_comments = (state.get("ratings") or {}).get("refused_comments", []) if search_report else []
    # Le lundi encore : la dérive de la semaine (agent/drift.py) ; une dérive
    # forte devient une alerte, sans changer le statut de la nuit.
    drift_report = (state.get("drift") or {}) if watch.search_report_due(today) else {}
    if drift_report:
        from agent.drift import drift_alerts

        alerts += drift_alerts(drift_report)
    usage = state.get("llm_usage", {})
    if search_report and services.learn_searches is not None and not state.get("dry_run"):
        try:
            learning = services.learn_searches(search_report, today)
        except Exception as error:  # l'apprentissage ne doit jamais faire échouer la nuit
            logger.exception("Apprentissage de la recherche impossible.")
            learning_notes = [f"Apprentissage de la recherche impossible : {error}"]
        else:
            proposals += learning.proposals
            learning_notes = learning.notes
            feedback_notes = list(getattr(learning, "feedback_notes", []) or [])
            usage = watch.merge_usage(usage, learning.usage)
            cost = watch.nightly_cost(usage)
            logger.info("Apprentissage de la recherche : %s proposition(s), %s piste(s).",
                        len(learning.proposals), len(learning.notes))
    return {"coverage": counts, "cost_usd": cost, "watch_alerts": alerts, "proposals": proposals,
            "search_report": search_report, "search_learning": learning_notes, "llm_usage": usage,
            "feedback_learning": feedback_notes, "refused_comments": refused_comments,
            "drift_report": drift_report}


def discover_new_agendas(state: CollectionState, services: CollectionServices, inputs: Mapping[str, Any],
                         today: date) -> tuple[dict[str, int], list[str]]:
    """Découverte hebdomadaire des agendas OpenAgenda (`discovery_weekday`,
    dimanche par défaut), ou forcée (`uv run python -m agent --decouverte`).

    La nuit ne lit que les agendas de `agenda_uids` (rapide) ; une fois par
    semaine, l'agent refait la recherche complète par termes pour repérer un
    agenda apparu dans la zone. Il ne touche pas à la configuration : chaque
    agenda trouvé devient une proposition, à valider par un humain.
    Renvoie ({uid: événements dans la zone}, alertes).
    """

    weekday = inputs.get("discovery_weekday")
    forced = bool(state.get("discover_agendas"))
    if services.discover_agendas is None or (not forced and (weekday is None or today.weekday() != weekday)):
        return {}, []
    try:
        found = dict(services.discover_agendas())
    except Exception as error:  # la découverte ne doit jamais faire échouer la nuit
        logger.exception("Découverte des agendas OpenAgenda impossible.")
        return {}, [f"Découverte des agendas OpenAgenda impossible : {error}"]
    logger.info("Découverte OpenAgenda : %s nouvel(s) agenda(s) utile(s) : %s.", len(found), found)
    return found, []


def agenda_proposals(agendas: Mapping[str, int], today: date) -> list[dict[str, Any]]:
    """Une proposition « ajouter_agenda » par agenda trouvé (les plus riches d'abord)."""

    return [
        {"source_id": "openagenda", "kind": "nouvel_agenda",
         "diagnosis": (f"Agenda OpenAgenda {uid} : {count} événement(s) dans la zone, "
                       "absent de agenda_uids (config/sources.yaml)."),
         "action": {"type": "ajouter_agenda", "new": uid, "events": count,
                    "found_on": today.isoformat(), "url": f"https://openagenda.com/agendas/{uid}"}}
        for uid, count in sorted(agendas.items(), key=lambda item: -item[1])
    ]


def partner_document_proposals(documents: Sequence[Mapping[str, Any]], today: date) -> list[dict[str, Any]]:
    """Une proposition « valider_document_partenaire » par document lu et pas encore validé.

    Rien n'est publié avant l'accord : la proposition donne le nombre de
    séances lues et un aperçu, pour juger sans ouvrir le fichier. Un document
    modifié (autre empreinte) est une nouvelle proposition.
    """

    return [
        {"source_id": "partenaire", "kind": "document_partenaire",
         "diagnosis": (f"Document de {doc['name']} ({doc['file_name']}) lu par Mistral : {doc['events']} séance(s), "
                       f"{doc.get('rejected', 0)} ligne(s) écartée(s). À valider avant publication."),
         "action": {"type": "valider_document_partenaire", "new": f"{doc['file_name']}@{str(doc['sha256'])[:12]}",
                    "file": doc["file_name"], "sha256": doc["sha256"], "partner": doc["name"],
                    "events": doc["events"], "preview": list(doc.get("preview") or [])[:5],
                    "found_on": today.isoformat()}}
        for doc in documents if doc.get("events")
    ]


# ---------------------------------------------------------------------------
# F. Bilan
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
    for source, (before, now) in (state.get("source_drops") or {}).items():
        alerts.append(
            f"Baisse anormale pour {source} : {now} événement(s) contre {before} la dernière fois "
            "(ses événements de la veille sont conservés)."
        )
    if extracted and rejected / extracted > REJECTION_ALERT_RATIO:
        alerts.append(f"{rejected} événement(s) rejeté(s) sur {extracted} (plus de {REJECTION_ALERT_RATIO:.0%}).")
    # Rappels de la surveillance (programme à renouveler, partenaire à
    # relancer, couverture, coût) : signalés, sans changer le statut.
    alerts.extend(state.get("watch_alerts", []))

    if state.get("load_error"):
        alerts.append(f"Écriture en base impossible, la base reste celle de la veille : {state['load_error']}")
        return STATUS_FAILED, alerts
    if kept == 0:
        return STATUS_FAILED, alerts
    if state.get("failed_sources") or state.get("errors") or state.get("source_drops"):
        return STATUS_PARTIAL, alerts
    return STATUS_SUCCESS, alerts


def build_source_rows(state: CollectionState) -> list[dict[str, Any]]:
    """Détail par source pour `sortiria.collect_run_sources`.

    Le statut vient de `routing.source_statuses` (bilan des extracteurs).
    """

    statuses = source_statuses(state)
    events = state.get("events", [])
    rows = []
    for source_id in state.get("sources", []):
        rows.append({
            "source_id": source_id,
            "status": statuses[source_id],     # ok, partiel ou echec
            "attempts": state.get("source_attempts", {}).get(source_id, state.get("attempts", 1)),
            # Dans l'état du graphe, les événements sont des dictionnaires
            # (Event.to_dict), pas des objets Event.
            "events_kept": sum(1 for event in events if event.get("source") == source_id),
        })
    return rows


def write_report(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
    """Écrit le bilan dans `sortiria.collect_runs`, journalise les alertes et l'envoie par mail."""

    status, alerts = decide_status(state)
    for alert in alerts:
        logger.warning("ALERTE collecte : %s", alert)

    services = get_services(config)
    source_rows = build_source_rows(state)
    report = {
        **(state.get("preparation") or {}),
        "status": status,
        "events_upserted": state.get("events_upserted", 0),
        "events_expired": state.get("events_expired", 0),
        "events_removed": state.get("events_removed", 0),
        "started_at": state.get("started_at"),
        "failed_sources": state.get("failed_sources", []),
        "message": " | ".join([*alerts, *state.get("errors", [])]),
        # Suivi de l'agent (14/10/2026)
        "events_embedded": state.get("events_embedded", 0),
        "cost_usd": state.get("cost_usd", 0.0),
        "llm_usage": state.get("llm_usage", {}),
        "coverage": state.get("coverage", {}),
        "node_durations": state.get("node_durations", {}),
        "alerts": alerts,
        "prompt_version": PROMPT_VERSION if "document" in state.get("sources", []) else "",
        "mlflow_run_id": getattr(services.tracker, "run_id", ""),
        "quality": state.get("quality", {}),
        "events_capped": state.get("events_capped", 0),
        "events_total": state.get("events_total"),
        "drift": state.get("drift") or {},
    }
    run_id = None
    pull_requests: dict[str, Any] = {}
    if not state.get("dry_run"):
        try:
            with services.connect() as connection:
                run_id = repository.record_collect_run(connection, report)
                repository.record_run_sources(connection, run_id, source_rows)
                # Réparations proposées : en attente de validation humaine.
                repository.record_proposals(connection, run_id, state.get("proposals", []))
        except Exception:
            logger.exception("Impossible d'enregistrer le bilan de la collecte.")
        # Propositions en pull request (16/10/2026) : dans une transaction à
        # part, après le bilan ; GitHub injoignable ne coûte que les PR de la nuit.
        if services.pull_requests is not None:
            try:
                with services.connect() as connection:
                    pull_requests = dict(services.pull_requests(connection))
            except Exception:
                logger.exception("Pull requests de l'agent impossibles cette nuit.")
                pull_requests = {"erreurs": ["GitHub ou la base injoignable : PR reportées à la nuit suivante"]}
        # Le bilan par mail (REPORT_EMAIL_TO dans .env). Un mail qui ne part
        # pas ne doit jamais faire échouer la collecte.
        try:
            services.notify({
                **report,
                "preparation": state.get("preparation") or {},
                "events_embedded": state.get("events_embedded", 0),
                "events_enriched": state.get("events_enriched", 0),
                "events_capped": state.get("events_capped", 0),
                "events_total": state.get("events_total"),
                "ratings": state.get("ratings", {}),
                "quality": state.get("quality", {}),
                "proposals": state.get("proposals", []),
                "pull_requests": pull_requests,
                "enrichment": state.get("enrichment", {}),
                "source_rows": source_rows,
                "skipped_sources": state.get("skipped_sources", []),
                "disabled_sources": state.get("disabled_sources", []),
                "purged": state.get("purged", {}),
                "alerts": alerts,
                "run_id": run_id,
                "coverage": state.get("coverage", {}),
                "cost_usd": state.get("cost_usd", 0.0),
                "llm_usage": state.get("llm_usage", {}),
                "vocabulary_size": state.get("vocabulary_size"),
                "search_report": state.get("search_report") or {},
                "search_learning": state.get("search_learning") or [],
                "feedback_learning": state.get("feedback_learning") or [],
                "refused_comments": state.get("refused_comments") or [],
                "drift_report": state.get("drift_report") or {},
            })
        except Exception:
            logger.exception("Impossible d'envoyer le bilan.")
    logger.info("Collecte terminée : statut %s.", status)
    return {"status": status, "alerts": alerts, "run_id": run_id}
