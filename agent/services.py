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
from pathlib import Path
from typing import Any

from agent.diagnosis import probe_pdf
from agent.report import send_report_by_email
from agent.tracking import NullTracker, build_tracker
from src.event.event_normalizer import PARIS
from src.logger import get_logger

logger = get_logger(__name__)

# Modèle d'embeddings Mistral (même valeur par défaut que app/config.py).
DEFAULT_EMBEDDING_MODEL = "mistral-embed"


@dataclass(frozen=True, slots=True)
class ExtractionOutput:
    """Ce que renvoie une extraction, quel que soit le moteur utilisé.

    Attributes
    ----------
    articles : list[dict[str, Any]]
        Les articles bruts produits par les extracteurs.
    failed_sources : list[str]
        Identifiants des sources dont l'extracteur a échoué.
    usage : dict[str, dict[str, int]]
        Tokens Mistral consommés, par modèle (`{"input": n, "output": n}`).
    extractor_stats : list[dict[str, Any]]
        Bilan de chaque extracteur (statut, durée, éléments lus et gardés, motifs
        de rejet, messages d'erreur).
    """

    articles: list[dict[str, Any]]
    failed_sources: list[str] = field(default_factory=list)
    # Tokens Mistral consommés par les extracteurs ({modèle: {"input", "output"}}).
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    # Bilan de chaque extracteur (nom, statut, durée, éléments lus et gardés,
    # motifs de rejet) : suivi de la qualité, source par source.
    extractor_stats: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CollectionServices:
    """Regroupe les dépendances de l'agent.

    Le graphe ne parle qu'à cet objet : les tests y mettent de faux services,
    la production les vrais (`build_default_services`).

    Attributes
    ----------
    extract : Callable[[Sequence[str]], ExtractionOutput]
        Lance les extracteurs demandés.
    connect : Callable[[], AbstractContextManager[Any]]
        Ouvre une connexion PostgreSQL (commit et fermeture en sortie).
    embed : Callable[[list[str]], list[list[float]]]
        Calcule les embeddings d'une liste de textes.
    embedding_model : str
        Nom du modèle d'embeddings. Par défaut `mistral-embed`.
    default_sources : tuple[str, ...]
        Sources lancées chaque nuit.
    now : Callable[[], datetime]
        Horloge (heure de Paris), remplaçable dans les tests.
    is_configured : Callable[[str], bool]
        Dit si une source a au moins une configuration active (par défaut : oui).
    notify : Callable[[Mapping[str, Any]], Any]
        Envoie le bilan de la nuit (par défaut : rien ; en production : un mail).
    watch_inputs : Callable[[], Mapping[str, Any]]
        Ce que l'agent surveille après le chargement (programmes PDF, fichiers
        des partenaires) ; par défaut : rien.
    enrich : Callable[[list[dict[str, Any]]], tuple[dict, dict]] | None
        Enrichissement d'un lot d'événements par Mistral : renvoie
        `({event_id: Enrichment}, consommation)` ; None : pas d'enrichissement.
    probe : Callable[[str], bool] | None
        Vérifie qu'une adresse répond avec un PDF ; None : pas de recherche de
        nouvelle adresse.
    discover_agendas : Callable[[], Mapping[str, int]] | None
        Découverte hebdomadaire des agendas OpenAgenda non configurés ; None : jamais.
    ratings_enabled : bool
        Active le bilan nocturne des notes des clients. Par défaut False.
    moderate : Callable[[str], tuple[str, dict[str, Any]]] | None
        Modération des commentaires restés en attente ; None : pas de modération.
    judge : Callable[[list[dict[str, Any]]], tuple[dict, dict]] | None
        Juge de qualité d'un échantillon : renvoie `({event_id: Verdict},
        consommation)` ; None : pas de contrôle.
    tracker : Any
        Suivi des exécutions (MLflow en production ; par défaut `NullTracker`).
    learn_searches : Callable[[Mapping[str, Any], Any], Any] | None
        Apprentissage du lundi sur les recherches ratées ; None : jamais.
    pull_requests : Callable[[Any], Mapping[str, Any]] | None
        Ouverture des pull requests de validation ; None : pas de jeton GitHub.
    """

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
    # Le lundi : apprend des recherches ratées de la semaine (agent/search_learning.py) ;
    # reçoit (bilan de la recherche, date) et renvoie un LearningResult. None : jamais.
    learn_searches: Callable[[Mapping[str, Any], Any], Any] | None = None
    # Validation par pull request (16/10/2026, agent/pull_requests.py) : reçoit
    # une connexion, relit les décisions et ouvre les PR manquantes ; renvoie le
    # bilan pour le mail. None : pas de jeton GitHub, on décide en ligne de commande.
    pull_requests: Callable[[Any], Mapping[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Implémentations réelles (utilisées en production)
# ---------------------------------------------------------------------------

def extract_with_checkit(source_names: Sequence[str]) -> ExtractionOutput:
    """Lance les extracteurs du moteur CheckIt.AI et renvoie leurs résultats.

    Le relevé des tokens Mistral est remis à zéro avant l'extraction, pour ne
    compter que cette exécution.

    Parameters
    ----------
    source_names : Sequence[str]
        Identifiants des sources à lancer (« openagenda », « document »...).

    Returns
    -------
    ExtractionOutput
        Articles, sources en échec, consommation Mistral et bilan par extracteur.
    """

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
    """Ouvre une connexion PostgreSQL (DATABASE_URL), validée si tout se passe bien.

    En cas d'exception, la transaction est annulée et l'exception remonte ; la
    connexion est fermée dans tous les cas.

    Yields
    ------
    Any
        La connexion psycopg2 ouverte.
    """

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
    recherche de l'interface sinon).

    Parameters
    ----------
    model : str
        Nom du modèle d'embeddings Mistral.

    Returns
    -------
    Any
        Un client `MistralAIEmbeddings`, mis en cache.
    """

    from langchain_mistralai import MistralAIEmbeddings

    return MistralAIEmbeddings(model=model)


def embed_with_mistral(texts: list[str]) -> list[list[float]]:
    """Calcule les embeddings avec Mistral (clé MISTRAL_API_KEY).

    Le modèle est lu dans `EMBEDDING_MODEL` (par défaut `mistral-embed`).

    Parameters
    ----------
    texts : list[str]
        Les textes à vectoriser.

    Returns
    -------
    list[list[float]]
        Un vecteur par texte, dans le même ordre.
    """

    model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    return embedding_client(model).embed_documents(texts)


def source_is_configured(name: str) -> bool:
    """Vrai si `config/sources.yaml` déclare au moins une source active pour cet extracteur.

    « rss » regroupe tous les flux d'agenda (`rss_sources`), « ical » tous les calendriers (section `ical_sources`), « document »
    tous les programmes PDF (`document_sources`) ; « partenaire » n'est lancé
    que si `data/partenaires/` contient au moins un fichier (sinon : dossier
    vide, fausse panne chaque nuit). Pour les API, on cherche une source active portant cet
    identifiant.

    Parameters
    ----------
    name : str
        Identifiant de l'extracteur (« rss », « openagenda », « partenaire »...).

    Returns
    -------
    bool
        True si au moins une source active correspond.
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


def pending_partner_document(path: Path, partner: Mapping[str, Any]) -> dict[str, Any] | None:
    """Document partenaire (PDF, Word) lu par Mistral cette nuit et pas encore validé :
    de quoi proposer son import (nombre de séances, aperçu). None si déjà validé ou
    pas encore lu (le cache évite tout nouvel appel à Mistral).

    Parameters
    ----------
    path : Path
        Chemin du document dans le dossier des partenaires.
    partner : Mapping[str, Any]
        Configuration du partenaire auquel appartient le fichier.

    Returns
    -------
    dict[str, Any] | None
        Nom du partenaire, nom et empreinte du fichier, nombre de séances et de
        rejets, aperçu des cinq premières séances ; None s'il n'y a rien à valider.
    """

    from src.extractors.datasets.source.partner_documents import (
        cached_rows,
        file_sha256,
        is_approved,
    )
    from src.extractors.datasets.source.partner_extractor import read_partner_file

    if is_approved(path) or cached_rows(path) is None:
        return None
    items = read_partner_file(path, partner, require_approval=False)
    events = [item["event"] for item in items if "event" in item]
    return {
        "name": partner["name"], "file_name": path.name, "sha256": file_sha256(path),
        "events": len(events), "rejected": sum(1 for item in items if "rejected" in item),
        "preview": [f"{event.start_at[:16].replace('T', ' ')} {event.title} ({event.venue_name}, {event.city})"
                    for event in events[:5]],
    }


def read_watch_inputs() -> dict[str, Any]:
    """Programmes PDF actifs (nom, fin de période), fichiers des partenaires (date de mise à jour)
    et documents des partenaires lus par Mistral en attente de validation.

    Renvoie aussi le jour de la découverte des agendas OpenAgenda, lu dans la
    configuration de cette source.

    Returns
    -------
    dict[str, Any]
        Clés `documents`, `partner_files`, `partner_documents` et
        `discovery_weekday` (None si OpenAgenda n'est pas actif).
    """

    from config.paths import SOURCES_FILE
    from config.source_config import load_source_registry
    from src.event.document_mapper import parse_period
    from src.extractors.datasets.dataset_file_utils import resolve_dataset_directory
    from src.extractors.datasets.source.partner_documents import is_document
    from src.extractors.datasets.source.partner_extractor import (
        find_partner_files,
        partner_for_file,
    )

    # Programmes PDF actifs et date de fin de leur période
    registry = load_source_registry(SOURCES_FILE, require_secrets=False)
    documents = []
    for source in registry.by_type("document"):
        if source.enabled:
            data = source.to_dict()
            period = parse_period(data)
            documents.append({"name": data.get("name"), "end": period[1] if period else None,
                              "source_id": data.get("source_id"), "url": data.get("url")})
    # Parcourir les sources : jour de découverte OpenAgenda, fichiers et
    # documents des partenaires (ceux en attente de validation à part)
    partner_files = []
    partner_documents = []
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
                if is_document(path):
                    try:
                        pending = pending_partner_document(path, partner_for_file(path, data))
                    except Exception:  # un document illisible ne prive pas la nuit de sa surveillance
                        logger.exception("Document partenaire %s illisible.", path.name)
                        continue
                    if pending is not None:
                        # Pas encore validé : ni publié, ni surveillé (« plus aucune séance »).
                        partner_documents.append(pending)
                        continue
                partner_files.append({
                    "name": partner_for_file(path, data)["name"],
                    "file_name": path.name,
                    "updated": datetime.fromtimestamp(path.stat().st_mtime, PARIS).date(),
                })
    return {"documents": documents, "partner_files": partner_files, "partner_documents": partner_documents,
            "discovery_weekday": discovery_weekday}


DEFAULT_DISCOVERY_WEEKDAY = 6   # dimanche


def discover_openagenda_agendas() -> dict[str, int]:
    """Mode découverte d'OpenAgenda : recherche des agendas par termes (lent,
    ~6 min), comme `scripts/audit_openagenda.py --decouverte`.

    Renvoie {uid: événements dans la zone} pour les agendas utiles absents de
    `agenda_uids` (les plus riches d'abord). La configuration n'est pas
    modifiée : l'agent en fait des propositions.

    Returns
    -------
    dict[str, int]
        `{uid: nombre d'événements dans la zone}`, du plus riche au moins riche.

    Raises
    ------
    RuntimeError
        Si la recherche des agendas a échoué.
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
    """Enrichissement réel : Mistral (`ENRICHMENT_MODEL`, mistral-small-latest par défaut).

    Parameters
    ----------
    rows : list[dict[str, Any]]
        Les événements à enrichir.

    Returns
    -------
    tuple[dict[str, Any], dict[str, dict[str, int]]]
        `{event_id: Enrichment}` et la consommation de tokens par modèle.
    """

    from src.event.event_enrichment import enrich_with_mistral

    return enrich_with_mistral(rows)


def moderate_comment(text: str) -> tuple[str, dict[str, Any]]:
    """Modération réelle d'un commentaire client (Mistral Moderation).

    Parameters
    ----------
    text : str
        Le commentaire à modérer.

    Returns
    -------
    tuple[str, dict[str, Any]]
        La décision de modération et le détail renvoyé par Mistral.
    """

    from src.event.event_ratings import moderate_with_mistral

    return moderate_with_mistral(text)


def judge_events(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    """Juge réel : Mistral (`JUDGE_MODEL`, mistral-medium-latest par défaut).

    Parameters
    ----------
    rows : list[dict[str, Any]]
        L'échantillon d'événements à juger.

    Returns
    -------
    tuple[dict[str, Any], dict[str, dict[str, int]]]
        `{event_id: Verdict}` et la consommation de tokens par modèle.
    """

    from src.event.quality_judge import judge_with_mistral

    return judge_with_mistral(rows)


MAX_AGENDAS_PER_WORD = 40     # agendas de la zone interrogés pour un mot (un appel chacun)


def find_agendas_for_words(words: Sequence[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Cherche des sujets (« zumba ») dans les agendas OpenAgenda de la zone
    que la collecte ne lit pas encore.

    Les agendas de la zone sont trouvés comme pour la découverte (recherche par
    termes : Montpellier, Sète, Thau…) ; pour chacun, une seule page filtrée
    par le mot (`oaq[what]`), puis le même filtre géographique que la
    collecte. Renvoie {mot: {agenda: {"events": n, "examples": [titres]}}}.

    Au plus `MAX_AGENDAS_PER_WORD` agendas sont interrogés par mot ; un agenda
    en erreur est ignoré.

    Parameters
    ----------
    words : Sequence[str]
        Les sujets à chercher.

    Returns
    -------
    dict[str, dict[str, dict[str, Any]]]
        Pour chaque mot trouvé, les agendas qui en parlent, avec le nombre
        d'événements et trois titres d'exemple.
    """

    from src.event.event_normalizer import pick_localized
    from src.event.openagenda_mapper import get_path
    from src.extractors.apis.source.openagenda_extractor import (
        OPENAGENDA_EXTRACTOR,
        fetch_agenda_page,
        get_api_key,
        is_in_area,
        search_agenda_uids,
    )

    source = dict(OPENAGENDA_EXTRACTOR.source)
    api_key = get_api_key()
    # Trouver les agendas de la zone que la collecte ne lit pas encore
    configured = {str(uid).strip() for uid in source.get("agenda_uids") or []}
    agendas = [uid for uid in search_agenda_uids({**source, "agenda_uids": []}, api_key) if uid not in configured]
    # Pour chaque mot, une page filtrée par agenda, puis le filtre géographique
    found: dict[str, dict[str, dict[str, Any]]] = {}
    for word in words:
        for uid in agendas[:MAX_AGENDAS_PER_WORD]:
            # Un agenda privé ou en erreur est journalisé et n'arrête pas la recherche
            try:
                events = fetch_agenda_page({**source, "search_text": word}, uid, api_key, 0, 50)
            except Exception as error:  # noqa: BLE001  résilience, erreur journalisée
                logger.warning("Agenda %s illisible pour « %s », passé (%s).", uid, word, error)
                continue
            titles = [pick_localized(get_path(item, ("title",)), "fr") for item in events if is_in_area(item, source)]
            titles = [str(title) for title in titles if title]
            if titles:
                found.setdefault(word, {})[uid] = {"events": len(titles), "examples": titles[:3]}
    return found


def known_title_words(words: Sequence[str]) -> set[str]:
    """Mots présents dans les titres de la base (vocabulaire des titres).

    Parameters
    ----------
    words : Sequence[str]
        Les mots à vérifier.

    Returns
    -------
    set[str]
        Ceux qui apparaissent dans au moins un titre.
    """

    from app.search_query import known_subjects

    with connect_postgres() as connection:
        return known_subjects(connection, list(words))


def learn_from_week(gaps: Mapping[str, Any], today: Any) -> Any:
    """Apprentissage réel : Mistral lit les pouces bas et les mots, la base
    vérifie, OpenAgenda cherche (16/10/2026 : les pouces bas en plus).

    Parameters
    ----------
    gaps : Mapping[str, Any]
        Bilan des recherches de la semaine (recherches ratées, pouces bas).
    today : Any
        Date du jour.

    Returns
    -------
    Any
        Le `LearningResult` de la semaine.
    """

    from agent.feedback_learning import learn_with_feedback, read_feedback_with_mistral
    from agent.search_learning import learn_from_searches, read_words_with_mistral

    def learn(gaps: Mapping[str, Any], today: Any) -> Any:
        """Apprend des recherches ratées avec les services réels (Mistral, base, OpenAgenda)."""
        return learn_from_searches(
            gaps, today, read_words=read_words_with_mistral, known_words=known_title_words,
            find_agendas=find_agendas_for_words if source_is_configured("openagenda") else None,
        )

    return learn_with_feedback(gaps, today, learn=learn, read_feedback=read_feedback_with_mistral)


def build_default_services() -> CollectionServices:
    """Services réels : moteur CheckIt.AI, PostgreSQL, Mistral.

    Le juge et l'enrichissement se coupent avec `JUGE_ACTIF` et
    `ENRICHISSEMENT_ACTIF` (« false », « 0 » ou « non ») ; la découverte des
    agendas n'est branchée que si OpenAgenda est configuré.

    Returns
    -------
    CollectionServices
        Les services de production.
    """

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
        learn_searches=learn_from_week,
        pull_requests=pull_requests_from_env(),
    )


def pull_requests_from_env() -> Callable[[Any], Mapping[str, Any]] | None:
    """Les PR de l'agent si `GITHUB_TOKEN_AGENT` est réglé (agent/pull_requests.py).

    Returns
    -------
    Callable[[Any], Mapping[str, Any]] | None
        Une fonction qui, à partir d'une connexion, ouvre les PR manquantes et
        renvoie leur bilan ; None sans jeton GitHub.
    """

    from agent.pull_requests import github_from_env, sync_pull_requests

    github = github_from_env()
    if github is None:
        return None
    return lambda connection: sync_pull_requests(connection, github).as_dict()
