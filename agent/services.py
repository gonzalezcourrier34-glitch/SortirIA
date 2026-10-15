"""Services utilisés par l'agent : extraction, base de données, embeddings.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Comme dans
Chess Agent (projet 13), les services sont regroupés dans un objet injecté au
graphe : les nœuds restent testables avec de faux services, sans réseau ni base.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent.diagnosis import probe_pdf
from agent.report import send_report_by_email
from agent.tracking import NullTracker, build_tracker
from src.event.event_normalizer import PARIS

# Modèle d'embeddings Mistral (même valeur par défaut que app/config.py).
DEFAULT_EMBEDDING_MODEL = "mistral-embed"


@dataclass(frozen=True, slots=True)
class ExtractionOutput:
    """Ce que renvoie une extraction, quel que soit le moteur utilisé."""

    articles: list[dict[str, Any]]
    failed_sources: list[str] = field(default_factory=list)
    # Tokens Mistral consommés par les extracteurs ({modèle: {"input", "output"}}).
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    # Bilan de chaque extracteur (nom, statut, durée, éléments lus et gardés,
    # motifs de rejet) : suivi de la qualité, source par source.
    extractor_stats: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CollectionServices:
    """Regroupe les dépendances de l'agent."""

    # Lance les extracteurs demandés.
    extract: Callable[[Sequence[str]], ExtractionOutput]
    # Ouvre une connexion PostgreSQL (gestionnaire de contexte : commit / fermeture).
    connect: Callable[[], AbstractContextManager[Any]]
    # Calcule les embeddings d'une liste de textes.
    embed: Callable[[list[str]], list[list[float]]]
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # Sources lancées chaque nuit.
    default_sources: tuple[str, ...] = (
        "openagenda", "datatourisme", "rss", "ical", "document", "partenaire", "scraper",
    )
    # Horloge (remplaçable dans les tests).
    now: Callable[[], datetime] = lambda: datetime.now(PARIS)
    # Dit si une source a au moins une configuration active (par défaut : oui).
    is_configured: Callable[[str], bool] = lambda name: True
    # Envoie le bilan de la nuit (par défaut : rien ; en production : un mail).
    notify: Callable[[Mapping[str, Any]], Any] = lambda report: None
    # Ce que l'agent surveille après le chargement (programmes PDF et leur fin,
    # fichiers des partenaires et leur date) ; par défaut : rien.
    watch_inputs: Callable[[], Mapping[str, Any]] = dict
    # Enrichissement d'un lot d'événements par Mistral : renvoie
    # ({event_id: Enrichment}, consommation) ; None : pas d'enrichissement.
    enrich: Callable[[list[dict[str, Any]]], tuple[dict[str, Any], dict[str, dict[str, int]]]] | None = None
    # Vérifie qu'une adresse répond avec un PDF (diagnostic d'un document
    # déplacé) ; None : pas de recherche de nouvelle adresse.
    probe: Callable[[str], bool] | None = None
    # Découverte hebdomadaire des agendas OpenAgenda absents de `agenda_uids`
    # ({uid: événements dans la zone}) ; None = jamais (tests, essais).
    discover_agendas: Callable[[], Mapping[str, int]] | None = None
    # Notes des clients : bilan nocturne (désactivé dans les tests de l'agent)
    # et modération des commentaires restés en attente.
    ratings_enabled: bool = False
    moderate: Callable[[str], tuple[str, dict[str, Any]]] | None = None
    # Juge de qualité d'un échantillon : renvoie ({event_id: Verdict}, consommation) ;
    # None : pas de contrôle.
    judge: Callable[[list[dict[str, Any]]], tuple[dict[str, Any], dict[str, dict[str, int]]]] | None = None
    # Suivi des exécutions (MLflow en production ; par défaut : rien).
    tracker: Any = field(default_factory=lambda: NullTracker())


# ---------------------------------------------------------------------------
# Implémentations réelles (utilisées en production)
# ---------------------------------------------------------------------------

def extract_with_checkit(source_names: Sequence[str]) -> ExtractionOutput:
    """Lance les extracteurs du moteur CheckIt.AI et renvoie leurs résultats."""

    from config.constants import EXTRACTOR_STATUS_FAILED
    from src.extractor_execution_service import extract_all_sources, normalize_extractor_names
    from src.extractors.documents.document_extractor import take_token_usage

    take_token_usage()   # relevé remis à zéro : seule cette extraction compte
    result = extract_all_sources(list(source_names), fail_if_empty=False, fail_on_extractor_error=False)
    # Les bilans reviennent dans l'ordre des extracteurs demandés : chacun
    # reçoit son identifiant de source (« document », « partenaire »...). Avant
    # le 15/10/2026, l'agent retrouvait la source par son nom affiché
    # (« Programmes PDF ») et ne reconnaissait pas l'échec de ces extracteurs.
    source_ids = normalize_extractor_names(list(source_names))
    executions = list(result.extractor_results)
    if len(source_ids) != len(executions):   # jamais vu ; on garde alors les noms affichés
        source_ids = [execution.extractor_name for execution in executions]
    return ExtractionOutput(
        articles=list(result.articles),
        failed_sources=[source_id for source_id, execution in zip(source_ids, executions, strict=True)
                        if execution.status == EXTRACTOR_STATUS_FAILED],
        usage=take_token_usage(),
        extractor_stats=[
            {
                "source_id": source_id,
                "name": execution.extractor_name,
                "type": execution.source_type,
                "status": execution.status,
                "analyzed": execution.analyzed_count,
                "kept": execution.article_count,
                "rejected": execution.rejected_count,
                "requests": execution.requests_count,
                "duration_s": round(execution.duration_seconds, 3),
                "rejection_reasons": dict(execution.rejection_reasons),
                # Messages d'erreur (« Nom de la source : PDF introuvable (404)... ») :
                # matière première du diagnostic (agent/diagnosis.py).
                "message": execution.message,
                "errors": list(execution.errors),
            }
            for source_id, execution in zip(source_ids, executions, strict=True)
        ],
    )


@contextmanager
def connect_postgres() -> Iterable[Any]:
    """Ouvre une connexion PostgreSQL (DATABASE_URL), validée si tout se passe bien."""

    import psycopg2

    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@functools.lru_cache(maxsize=4)
def embedding_client(model: str) -> Any:
    """Client d'embeddings, créé une seule fois par modèle : sa création
    télécharge le tokenizer depuis Hugging Face (une requête à chaque
    recherche de l'interface sinon)."""

    from langchain_mistralai import MistralAIEmbeddings

    return MistralAIEmbeddings(model=model)


def embed_with_mistral(texts: list[str]) -> list[list[float]]:
    """Calcule les embeddings avec Mistral (clé MISTRAL_API_KEY)."""

    model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    return embedding_client(model).embed_documents(texts)


def source_is_configured(name: str) -> bool:
    """Vrai si `config/sources.yaml` déclare au moins une source active pour cet extracteur.

    « rss » regroupe tous les flux d'agenda (`rss_sources`), « ical » tous les calendriers (section `ical_sources`), « document »
    tous les programmes PDF (`document_sources`) ; « partenaire » n'est lancé
    que si `data/partenaires/` contient au moins un fichier (sinon : dossier
    vide, fausse panne chaque nuit). Pour les API, on cherche une source active portant cet
    identifiant.
    """

    from config.paths import SOURCES_FILE
    from config.source_config import load_source_registry

    registry = load_source_registry(SOURCES_FILE, require_secrets=False)
    if name in ("rss", "ical", "document", "scraper"):
        return any(source.enabled for source in registry.by_type(name))
    if name == "partenaire":
        from src.extractors.datasets.source.partner_extractor import has_partner_files

        return any(
            source.enabled and source.source_id == name and has_partner_files(source.to_dict())
            for source in registry.sources
        )
    return any(source.enabled and source.source_id == name for source in registry.sources)


def read_watch_inputs() -> dict[str, Any]:
    """Programmes PDF actifs (nom, fin de période) et fichiers des partenaires (date de mise à jour)."""

    from config.paths import SOURCES_FILE
    from config.source_config import load_source_registry
    from src.event.document_mapper import parse_period
    from src.extractors.datasets.dataset_file_utils import resolve_dataset_directory
    from src.extractors.datasets.source.partner_extractor import (
        find_partner_files,
        partner_for_file,
    )

    registry = load_source_registry(SOURCES_FILE, require_secrets=False)
    documents = []
    for source in registry.by_type("document"):
        if source.enabled:
            data = source.to_dict()
            period = parse_period(data)
            documents.append({"name": data.get("name"), "end": period[1] if period else None,
                              "source_id": data.get("source_id"), "url": data.get("url")})
    partner_files = []
    discovery_weekday = None
    for source in registry.sources:
        data = source.to_dict()
        if source.enabled and source.source_id == "openagenda":
            # Jour de la découverte des agendas (0 = lundi … 6 = dimanche).
            discovery_weekday = int(data.get("discovery_weekday", DEFAULT_DISCOVERY_WEEKDAY))
        if source.enabled and source.source_id == "partenaire":
            try:
                directory = resolve_dataset_directory(data, str(data.get("name")))
            except (OSError, ValueError):
                continue
            for path in find_partner_files(directory, data):
                partner_files.append({
                    "name": partner_for_file(path, data)["name"],
                    "file_name": path.name,
                    "updated": datetime.fromtimestamp(path.stat().st_mtime, PARIS).date(),
                })
    return {"documents": documents, "partner_files": partner_files, "discovery_weekday": discovery_weekday}


DEFAULT_DISCOVERY_WEEKDAY = 6   # dimanche


def discover_openagenda_agendas() -> dict[str, int]:
    """Mode découverte d'OpenAgenda : recherche des agendas par termes (lent,
    ~6 min), comme `scripts/audit_openagenda.py --decouverte`.

    Renvoie {uid: événements dans la zone} pour les agendas utiles absents de
    `agenda_uids` (les plus riches d'abord). La configuration n'est pas
    modifiée : l'agent en fait des propositions.
    """

    from collections import Counter

    from src.extractors.apis.source.openagenda_extractor import (
        OPENAGENDA_EXTRACTOR,
        extract_articles_from_source,
    )

    source = dict(OPENAGENDA_EXTRACTOR.source)
    configured = {str(uid).strip() for uid in source.get("agenda_uids") or []}
    result = extract_articles_from_source({**source, "agenda_uids": []})
    if result.status == "failed":
        raise RuntimeError(f"recherche des agendas impossible : {result.message or result.status}")
    counts = Counter(str((article.get("metadata") or {}).get("agenda_uid") or "") for article in result.articles)
    return {uid: count for uid, count in counts.most_common() if uid and uid not in configured}


def enrich_events(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    """Enrichissement réel : Mistral (`ENRICHMENT_MODEL`, mistral-small-latest par défaut)."""

    from src.event.event_enrichment import enrich_with_mistral

    return enrich_with_mistral(rows)


def moderate_comment(text: str) -> tuple[str, dict[str, Any]]:
    """Modération réelle d'un commentaire client (Mistral Moderation)."""

    from src.event.event_ratings import moderate_with_mistral

    return moderate_with_mistral(text)


def judge_events(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    """Juge réel : Mistral (`JUDGE_MODEL`, mistral-medium-latest par défaut)."""

    from src.event.quality_judge import judge_with_mistral

    return judge_with_mistral(rows)


def build_default_services() -> CollectionServices:
    """Services réels : moteur CheckIt.AI, PostgreSQL, Mistral."""

    return CollectionServices(
        extract=extract_with_checkit,
        connect=connect_postgres,
        embed=embed_with_mistral,
        embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        is_configured=source_is_configured,
        notify=send_report_by_email,
        watch_inputs=read_watch_inputs,
        tracker=build_tracker(),
        probe=probe_pdf,
        discover_agendas=discover_openagenda_agendas if source_is_configured("openagenda") else None,
        ratings_enabled=True,
        moderate=moderate_comment,
        judge=judge_events if os.getenv("JUGE_ACTIF", "true").lower() not in ("false", "0", "non") else None,
        enrich=enrich_events if os.getenv("ENRICHISSEMENT_ACTIF", "true").lower() not in ("false", "0", "non") else None,
    )
