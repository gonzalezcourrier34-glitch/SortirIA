"""Moteur commun d'orchestration des extractions RSS et Atom.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/rss/rss_engine.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from config.constants import (
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
    RSS_ACCESS_DENIED_STATUS_CODES,
    RSS_ERROR_ACCESS_DENIED,
    RSS_ERROR_EXTRACTION_STOPPED,
    RSS_ERROR_INVALID_CONTENT_TYPE,
    RSS_ERROR_INVALID_FEED,
    RSS_ERROR_MAINTENANCE,
    RSS_ERROR_NETWORK,
    RSS_ERROR_PARSING,
    RSS_ERROR_RATE_LIMITED,
    RSS_ERROR_SERVER,
    RSS_ERROR_TIMEOUT,
    RSS_MAINTENANCE_STATUS_CODES,
    RSS_RATE_LIMIT_STATUS_CODES,
    RSS_SERVER_ERROR_STATUS_CODES,
    SOURCE_TYPE_RSS,
)
from config.paths import SOURCES_FILE
from config.source_config import SourceRegistry, load_source_registry
from src.article.processing.article_deduplicator import is_duplicate_article, register_article
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.core.extractor_service import execute_configured_sources_extractor
from src.extractors.rss.rss_adapter import (
    RssAdapter,
    RssItem,
    RssSource,
    validate_rss_article,
    validate_rss_item,
)
from src.logger import get_logger
from src.utils.extractor_utils import log_extraction_summary
from src.utils.filter_utils import get_filter_configuration
from src.utils.parsing_utils import parse_boolean, parse_non_negative_integer
from src.utils.url_utils import sanitize_url_for_logging
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)
RssExtractionResult = list[dict[str, Any]] | ExtractorResult


# Exceptions RSS

class RssExtractionStoppedError(RuntimeError):
    """Demande l'arrêt immédiat de l'extraction RSS courante.

    Les sous-classes ne changent que `error_code`, le motif machine reporté
    dans le bilan de la source.
    """

    error_code = RSS_ERROR_EXTRACTION_STOPPED

    def __init__(
        self,
        source_name: str,
        reason: str,
        status_code: int | None = None
    ) -> None:
        """Enregistre la source, le motif et le code HTTP de l'arrêt.

        Parameters
        ----------
        source_name : str
            Nom de la source ; vide, il devient « source RSS ».
        reason : str
            Motif lisible de l'arrêt ; vide, un motif générique est utilisé.
        status_code : int | None
            Code HTTP à l'origine de l'arrêt, s'il y en a un. Par défaut None.
        """
        self.source_name = normalize_value(source_name) or "source RSS"
        self.reason = normalize_value(reason) or "Erreur RSS non communiquée"
        self.status_code = status_code
        super().__init__(self.reason)


class RssAccessDeniedError(RssExtractionStoppedError):
    """Signale un accès refusé au flux RSS."""

    error_code = RSS_ERROR_ACCESS_DENIED


class RssRateLimitError(RssExtractionStoppedError):
    """Signale une limitation temporaire du serveur."""

    error_code = RSS_ERROR_RATE_LIMITED


class RssInvalidFeedError(RssExtractionStoppedError):
    """Signale un flux RSS ou Atom invalide."""

    error_code = RSS_ERROR_INVALID_FEED


class RssInvalidContentTypeError(RssExtractionStoppedError):
    """Signale un contenu incompatible avec RSS ou Atom."""

    error_code = RSS_ERROR_INVALID_CONTENT_TYPE


class RssMaintenanceError(RssExtractionStoppedError):
    """Signale une indisponibilité temporaire du serveur."""

    error_code = RSS_ERROR_MAINTENANCE


class RssTimeoutError(RssExtractionStoppedError):
    """Signale un dépassement du délai HTTP."""

    error_code = RSS_ERROR_TIMEOUT


class RssNetworkError(RssExtractionStoppedError):
    """Signale une erreur réseau."""

    error_code = RSS_ERROR_NETWORK


class RssParsingError(RssExtractionStoppedError):
    """Signale un contenu RSS impossible à analyser."""

    error_code = RSS_ERROR_PARSING


class RssServerError(RssExtractionStoppedError):
    """Signale une erreur interne du serveur RSS."""

    error_code = RSS_ERROR_SERVER
    
class RssRequestError(RuntimeError):
    """Signale l'échec non fatal d'une requête RSS isolée."""

    def __init__(
        self,
        source_name: str,
        reason: str,
        status_code: int | None = None
    ) -> None:
        """Enregistre la source, le motif et le code HTTP de l'échec.

        Parameters
        ----------
        source_name : str
            Nom de la source ; vide, il devient « source RSS ».
        reason : str
            Motif lisible de l'échec ; vide, un motif générique est utilisé.
        status_code : int | None
            Code HTTP de la réponse, s'il y en a un. Par défaut None.
        """
        self.source_name = normalize_value(source_name) or "source RSS"
        self.reason = normalize_value(reason) or "Erreur RSS non communiquée"
        self.status_code = status_code
        super().__init__(self.reason)


# État isolé d'une exécution RSS

_RSS_REQUEST_COUNT: ContextVar[int] = ContextVar("rss_request_count", default=0)
_RSS_REQUEST_ERRORS: ContextVar[tuple[str, ...]] = ContextVar(
    "rss_request_errors",
    default=()
)


def reset_rss_request_count() -> object:
    """Réinitialise le compteur HTTP RSS courant.

    Returns
    -------
    object
        Jeton de la variable de contexte, pour restaurer l'ancienne valeur.
    """

    return _RSS_REQUEST_COUNT.set(0)


def increment_rss_request_count() -> None:
    """Incrémente le compteur HTTP RSS courant."""

    _RSS_REQUEST_COUNT.set(_RSS_REQUEST_COUNT.get() + 1)


def get_rss_request_count() -> int:
    """Retourne le nombre de requêtes RSS effectuées.

    Returns
    -------
    int
        Nombre de requêtes comptées depuis la dernière réinitialisation.
    """

    return _RSS_REQUEST_COUNT.get()


def reset_rss_request_errors() -> object:
    """Réinitialise les erreurs RSS courantes.

    Returns
    -------
    object
        Jeton de la variable de contexte, pour restaurer l'ancienne valeur.
    """

    return _RSS_REQUEST_ERRORS.set(())


def get_rss_request_errors() -> list[str]:
    """Retourne les erreurs RSS enregistrées.

    Returns
    -------
    list[str]
        Messages d'erreur enregistrés depuis la dernière réinitialisation.
    """

    return list(_RSS_REQUEST_ERRORS.get())


def get_http_error_message(error: httpx.HTTPStatusError) -> str:
    """Extrait un message HTTP exploitable sans exposer l'URL complète.

    Parameters
    ----------
    error : httpx.HTTPStatusError
        Erreur HTTP reçue.

    Returns
    -------
    str
        Début du corps de la réponse (1000 caractères), sinon la raison HTTP,
        sinon « Erreur HTTP <code> », adresses masquées.
    """

    response_text = normalize_value(error.response.text[:1000])
    reason_phrase = normalize_value(error.response.reason_phrase)
    return sanitize_url_for_logging(
        response_text or reason_phrase or f"Erreur HTTP {error.response.status_code}"
    )


def get_rss_error_message(error: Any) -> str:
    """Retourne le message normalisé et sûr pour les logs d'une erreur RSS.

    Parameters
    ----------
    error : Any
        Erreur RSS, erreur httpx ou toute autre valeur.

    Returns
    -------
    str
        Motif de l'erreur, adresses masquées, ou un message par défaut.
    """

    if isinstance(error, (RssExtractionStoppedError, RssRequestError)):
        message = error.reason
    elif isinstance(error, httpx.HTTPStatusError):
        message = get_http_error_message(error)
    elif isinstance(error, httpx.HTTPError):
        message = type(error).__name__
    else:
        message = normalize_value(error)

    return sanitize_url_for_logging(message or "Erreur RSS non communiquée")


def record_rss_request_error(error: Any) -> str:
    """Enregistre une erreur RSS normalisée et sûre pour les logs.

    Parameters
    ----------
    error : Any
        Erreur RSS, erreur httpx ou toute autre valeur.

    Returns
    -------
    str
        Message enregistré dans la liste des erreurs courantes.
    """

    message = get_rss_error_message(error)
    _RSS_REQUEST_ERRORS.set((*_RSS_REQUEST_ERRORS.get(), message))
    return message


def raise_if_fatal_rss_error(
    error: Any,
    source_name: str,
    status_code: int | None = None
) -> str:
    """Qualifie une erreur RSS et lève une exception spécialisée si nécessaire.

    Parameters
    ----------
    error : Any
        Erreur rencontrée ; pour une `httpx.HTTPStatusError`, le code HTTP est
        lu dans la réponse.
    source_name : str
        Nom de la source.
    status_code : int | None
        Code HTTP connu par ailleurs. Par défaut None.

    Returns
    -------
    str
        Le motif de l'erreur, si elle n'est pas fatale.

    Raises
    ------
    RssAccessDeniedError
        Si le code HTTP signale un accès refusé.
    RssRateLimitError
        Si le code HTTP signale une limitation de débit.
    RssMaintenanceError
        Si le code HTTP signale une maintenance.
    RssServerError
        Si le code HTTP signale une erreur interne du serveur.
    """

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        reason = get_http_error_message(error)
    else:
        reason = sanitize_url_for_logging(normalize_value(error)) or (
            f"Erreur HTTP {status_code}"
            if status_code is not None
            else "Erreur RSS non communiquée"
        )

    if status_code in RSS_ACCESS_DENIED_STATUS_CODES:
        raise RssAccessDeniedError(source_name, reason, status_code) from None
    if status_code in RSS_RATE_LIMIT_STATUS_CODES:
        raise RssRateLimitError(source_name, reason, status_code) from None
    if status_code in RSS_MAINTENANCE_STATUS_CODES:
        raise RssMaintenanceError(source_name, reason, status_code) from None
    if status_code in RSS_SERVER_ERROR_STATUS_CODES:
        raise RssServerError(source_name, reason, status_code) from None
    return reason


def get_rss_failure_reason(error: RssExtractionStoppedError) -> str:
    """Retourne le motif machine d'un arrêt RSS spécialisé.

    Parameters
    ----------
    error : RssExtractionStoppedError
        Arrêt RSS.

    Returns
    -------
    str
        Son `error_code`, ou le motif générique d'arrêt.
    """

    return normalize_value(getattr(error, "error_code", "")) or RSS_ERROR_EXTRACTION_STOPPED


# Contextes d'exécution

@dataclass(slots=True)
class RssExtractionContext:
    """Porte l'état d'orchestration d'une source RSS.

    Attributes
    ----------
    source : dict[str, Any]
        Configuration de la source.
    filters : dict[str, Any]
        Filtres appliqués aux articles de la source.
    maximum_articles : int
        Nombre d'articles au-delà duquel la lecture s'arrête.
    remove_duplicates : bool
        Vrai pour écarter les doublons dans la source.
    articles : list[dict[str, Any]]
        Articles retenus.
    seen_keys : set[str]
        Clés de doublon déjà vues.
    rejection_stats : Counter[str]
        Nombre de rejets par motif.
    processed_count : int
        Nombre d'entrées examinées.
    converted_count : int
        Nombre d'entrées transformées en article (avant validation).
    """

    source: dict[str, Any]
    filters: dict[str, Any]
    maximum_articles: int
    remove_duplicates: bool
    articles: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    processed_count: int = 0
    converted_count: int = 0

    @property
    def source_name(self) -> str:
        """Nom lisible de la source.

        Returns
        -------
        str
            Le champ `name`, ou « source RSS » s'il est vide.
        """
        return normalize_value(self.source.get("name")) or "source RSS"

    @property
    def source_id(self) -> str:
        """Identifiant stable de la source.

        Returns
        -------
        str
            Le champ `source_id`, ou à défaut le nom de la source.
        """
        return normalize_value(self.source.get("source_id")) or self.source_name

    @property
    def completed(self) -> bool:
        """Vrai si le nombre maximal d'articles est atteint.

        Returns
        -------
        bool
            Vrai quand il n'y a plus d'article à lire pour cette source.
        """
        return len(self.articles) >= self.maximum_articles

    def reject(self, reason: Any) -> None:
        """Compte un rejet sous son motif.

        Parameters
        ----------
        reason : Any
            Motif du rejet ; vide, il est compté comme « article_invalide ».
        """
        self.rejection_stats[normalize_value(reason) or "article_invalide"] += 1

    def add_article(self, article: dict[str, Any]) -> None:
        """Retient un article et, si demandé, enregistre ses clés de doublon.

        Parameters
        ----------
        article : dict[str, Any]
            Article validé.
        """
        if self.remove_duplicates:
            register_article(article, self.seen_keys)
        self.articles.append(article)


@dataclass(slots=True)
class RssBatchContext:
    """Porte les résultats agrégés de plusieurs sources RSS.

    Attributes
    ----------
    articles : list[dict[str, Any]]
        Articles retenus sur le lot, sans doublon entre sources.
    errors : list[str]
        Messages d'erreur des sources.
    seen_keys : set[str]
        Clés de doublon déjà vues sur le lot.
    rejection_stats : Counter[str]
        Nombre de rejets par motif, toutes sources confondues.
    successful_sources : int
        Nombre de sources réussies.
    partial_sources : int
        Nombre de sources partiellement réussies.
    failed_sources : int
        Nombre de sources en échec.
    empty_sources : int
        Nombre de sources sans article.
    duplicate_count : int
        Nombre d'articles écartés comme doublons entre sources.
    analyzed_count : int
        Nombre total d'entrées examinées.
    rejected_count : int
        Nombre total d'entrées rejetées.
    requests_count : int
        Nombre total de requêtes HTTP.
    """

    articles: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    successful_sources: int = 0
    partial_sources: int = 0
    failed_sources: int = 0
    empty_sources: int = 0
    duplicate_count: int = 0
    analyzed_count: int = 0
    rejected_count: int = 0
    requests_count: int = 0

    def add_result(self, result: ExtractorResult) -> None:
        """Ajoute le résultat d'une source au lot.

        Additionne les compteurs, compte la source sous son statut et écarte
        les articles déjà vus dans une autre source.

        Parameters
        ----------
        result : ExtractorResult
            Résultat d'une source.
        """
        # Additionner les compteurs et les erreurs
        self.analyzed_count += result.analyzed_count
        self.rejected_count += result.rejected_count
        self.requests_count += result.requests_count
        self.errors.extend(result.errors)
        self.rejection_stats.update(result.rejection_reasons)

        # Compter la source sous son statut
        if result.status == EXTRACTOR_STATUS_SUCCESS:
            self.successful_sources += 1
        elif result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS:
            self.partial_sources += 1
        elif result.status == EXTRACTOR_STATUS_EMPTY:
            self.empty_sources += 1
        else:
            self.failed_sources += 1

        # Retenir les articles, sauf les doublons d'une autre source
        for article in result.articles:
            if is_duplicate_article(article, self.seen_keys):
                self.duplicate_count += 1
                self.rejection_stats["doublon_lot"] += 1
                self.rejected_count += 1
                continue
            register_article(article, self.seen_keys)
            self.articles.append(article)


# Façade du moteur

@dataclass(slots=True)
class RssExtractor:
    """Charge les sources et délègue leur traitement à un adaptateur RSS.

    Attributes
    ----------
    adapter : RssAdapter
        Adaptateur qui sait lire les flux concernés.
    sources_file : Path
        Fichier de configuration des sources. Par défaut `SOURCES_FILE`.
    _registry : SourceRegistry | None
        Registre des sources, chargé à la première demande.
    """

    adapter: RssAdapter
    sources_file: Path = SOURCES_FILE
    _registry: SourceRegistry | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Vérifie les types de l'adaptateur et du fichier de sources.

        Raises
        ------
        TypeError
            Si `adapter` n'est pas un `RssAdapter` ou `sources_file` pas un `Path`.
        """
        if not isinstance(self.adapter, RssAdapter):
            raise TypeError("adapter doit être une instance de RssAdapter.")
        if not isinstance(self.sources_file, Path):
            raise TypeError("sources_file doit être une instance de Path.")

    def reload_sources(self) -> SourceRegistry:
        """Recharge le registre depuis le fichier de configuration.

        Returns
        -------
        SourceRegistry
            Registre rechargé (sans exiger les secrets).
        """
        self._registry = load_source_registry(self.sources_file, require_secrets=False)
        return self._registry

    def get_registry(self) -> SourceRegistry:
        """Renvoie le registre, chargé au premier appel.

        Returns
        -------
        SourceRegistry
            Registre des sources.
        """
        return self._registry or self.reload_sources()

    def get_sources(self) -> list[dict[str, Any]]:
        """Renvoie les sources RSS configurées.

        Returns
        -------
        list[dict[str, Any]]
            Configuration de chaque source de type RSS.
        """
        return [source.to_dict() for source in self.get_registry().by_type(SOURCE_TYPE_RSS)]

    def extract_source_result(self, source: RssSource) -> ExtractorResult:
        """Extrait une source et renvoie son résultat détaillé.

        Parameters
        ----------
        source : RssSource
            Configuration de la source.

        Returns
        -------
        ExtractorResult
            Résultat qualifié de la source.
        """
        return extract_rss_source(source, self.adapter)

    def extract_source(self, source: RssSource) -> list[dict[str, Any]]:
        """Extrait une source et ne renvoie que ses articles.

        Parameters
        ----------
        source : RssSource
            Configuration de la source.

        Returns
        -------
        list[dict[str, Any]]
            Articles retenus.
        """
        return self.extract_source_result(source).articles

    def extract_sources_result(self, sources: Sequence[RssSource]) -> ExtractorResult:
        """Extrait plusieurs sources et renvoie le résultat agrégé.

        Parameters
        ----------
        sources : Sequence[RssSource]
            Configurations des sources.

        Returns
        -------
        ExtractorResult
            Résultat agrégé du lot.
        """
        return extract_rss_sources(sources, self.adapter)

    def extract_sources(self, sources: Sequence[RssSource]) -> list[dict[str, Any]]:
        """Extrait plusieurs sources et ne renvoie que leurs articles.

        Parameters
        ----------
        sources : Sequence[RssSource]
            Configurations des sources.

        Returns
        -------
        list[dict[str, Any]]
            Articles retenus, sans doublon entre sources.
        """
        return self.extract_sources_result(sources).articles

    def run(self) -> ExtractorResult:
        """Lance toutes les sources RSS configurées, par le service d'extraction commun.

        Returns
        -------
        ExtractorResult
            Résultat agrégé de toutes les sources RSS.
        """
        return execute_configured_sources_extractor(
            extractor_name=self.adapter.default_name,
            source_type=SOURCE_TYPE_RSS,
            sources_loader=self.get_sources,
            extraction_function=self.extract_sources_result
        )


# Préparation et traitement des items

def create_context(source: Mapping[str, Any]) -> RssExtractionContext:
    """Construit l'état d'orchestration depuis la configuration.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (`max_articles`, filtres...).

    Returns
    -------
    RssExtractionContext
        État initial ; le dédoublonnage est actif par défaut.

    Raises
    ------
    ValueError
        Si la configuration des filtres n'est pas un dictionnaire.
    """

    normalized_source = dict(source)
    filters = get_filter_configuration(normalized_source)

    if not isinstance(filters, Mapping):
        raise ValueError(  # noqa: TRY004  ValueError attendu par les appelants
            "Configuration des filtres RSS invalide."
        )

    return RssExtractionContext(
        source=normalized_source,
        filters=dict(filters),
        maximum_articles=parse_non_negative_integer(
            normalized_source.get("max_articles"),
            default=0
        ),
        remove_duplicates=parse_boolean(
            filters.get("remove_duplicates", True),
            default=True
        )
    )


def process_rss_item(
    item: RssItem,
    context: RssExtractionContext,
    adapter: RssAdapter
) -> None:
    """Valide, transforme puis déduplique une entrée RSS.

    L'article retenu est ajouté au contexte ; un rejet est compté sous son
    motif. Les erreurs de construction deviennent des rejets.

    Parameters
    ----------
    item : RssItem
        Entrée brute du flux.
    context : RssExtractionContext
        État de la source, mis à jour sur place.
    adapter : RssAdapter
        Adaptateur du flux.
    """

    # Valider l'entrée selon les règles de l'adaptateur
    context.processed_count += 1

    valid, reason = validate_rss_item(item, context, adapter)

    if not valid:
        context.reject(reason or "entree_invalide")
        return

    # Construction de l'article
    try:
        article = adapter.build_article(item, context.source)

    except (OSError, ValueError, TypeError, UnicodeDecodeError) as error:
        logger.warning(
            "Construction impossible pour une entrée de %s : %s",
            context.source_name,
            sanitize_url_for_logging(
                normalize_value(error) or type(error).__name__
            )
        )
        context.reject("construction_invalide")
        return

    except Exception as error:
        logger.exception(
            "Erreur inattendue pendant la construction d'une entrée de %s : %s",
            context.source_name,
            sanitize_url_for_logging(
                normalize_value(error) or type(error).__name__  # noqa: TRY401  adresses masquées
            )
        )
        context.reject("construction_invalide")
        return

    if not isinstance(article, dict) or not article:
        context.reject("article_vide")
        return

    # Certains adaptateurs utilisent "source" pour stocker le nom
    # du média (BBC, Le Monde, Reuters...). Le moteur RSS garantit
    # ensuite l'identifiant technique commun utilisé dans tout le pipeline.
    publisher = normalize_value(
        article.get("publisher") or article.get("source")
    )

    if (
        publisher
        and context.source_id
        and publisher.casefold() != context.source_id.casefold()
    ):
        article["publisher"] = publisher

    article["source"] = context.source_id
    article["source_type"] = SOURCE_TYPE_RSS
    article.setdefault("source_name", context.source_name)

    context.converted_count += 1

    # Valider l'article (règles propres puis communes), puis écarter les doublons
    valid, reason = validate_rss_article(
        article,
        context,
        adapter
    )

    if not valid:
        context.reject(reason or "article_invalide")
        return

    if (
        context.remove_duplicates
        and is_duplicate_article(article, context.seen_keys)
    ):
        context.reject("doublon")
        return

    context.add_article(article)

# Extraction d'une source

def extract_rss_source(
    source: Mapping[str, Any],
    adapter: RssAdapter
) -> ExtractorResult:
    """Extrait une source RSS avec la même qualification que le moteur API.

    Un arrêt fatal (accès refusé, quota, délai...) après quelques articles
    donne un succès partiel ; sans aucun article, l'arrêt remonte à l'appelant.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source.
    adapter : RssAdapter
        Adaptateur du flux.

    Returns
    -------
    ExtractorResult
        Résultat qualifié (succès, partiel, vide ou échec), articles, erreurs
        de requêtes et compteurs.

    Raises
    ------
    TypeError
        Si `adapter` n'est pas un `RssAdapter` ou `source` pas un dictionnaire.
    RssExtractionStoppedError
        Si l'extraction est arrêtée avant d'avoir retenu un article.
    RuntimeError
        Pour une autre erreur HTTP ou une erreur de lecture du flux (message
        sans adresse complète).
    """

    if not isinstance(adapter, RssAdapter):
        raise TypeError("adapter doit être une instance de RssAdapter.")
    if not isinstance(source, Mapping):
        raise TypeError(f"Configuration RSS invalide : {type(source).__name__}.")

    # Préparer l'état ; source désactivée ou sans quota : résultat vide
    context = create_context(source)
    base_metadata = {
        "source_id": context.source_id,
        "maximum_articles": context.maximum_articles
    }

    if not parse_boolean(context.source.get("enabled"), default=True):
        return ExtractorResult(
            name=context.source_name,
            source_type=SOURCE_TYPE_RSS,
            status=EXTRACTOR_STATUS_EMPTY,
            message="Source RSS désactivée.",
            metadata=base_metadata
        )

    if context.maximum_articles <= 0:
        return ExtractorResult(
            name=context.source_name,
            source_type=SOURCE_TYPE_RSS,
            status=EXTRACTOR_STATUS_EMPTY,
            message="Aucun article demandé.",
            metadata=base_metadata
        )

    # Lire les entrées avec des compteurs de requêtes propres à cette source ;
    # les erreurs HTTP fatales sont gardées pour qualifier le résultat
    logger.info("Début de l'extraction RSS : %s.", context.source_name)
    request_count_token = reset_rss_request_count()
    request_errors_token = reset_rss_request_errors()
    fatal_error: RssExtractionStoppedError | None = None

    try:
        try:
            items = adapter.iter_items(context.source)

            if items is None:
                raise ValueError(
                    f"Aucune séquence retournée par l'adaptateur RSS de {context.source_name}."
                )
            if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
                raise TypeError(
                    f"L'adaptateur RSS de {context.source_name} doit retourner une séquence."
                )

            for item in items:
                if context.completed:
                    break
                process_rss_item(item, context, adapter)

        except RssExtractionStoppedError as error:
            fatal_error = error

        except httpx.HTTPStatusError as error:
            try:
                reason = raise_if_fatal_rss_error(error, context.source_name)
            except RssExtractionStoppedError as stopped_error:
                fatal_error = stopped_error
            else:
                fatal_error = RssExtractionStoppedError(
                    context.source_name,
                    reason,
                    error.response.status_code
                )

        except httpx.TimeoutException:
            fatal_error = RssTimeoutError(
                context.source_name,
                "Délai dépassé pendant le téléchargement du flux RSS."
            )

        except httpx.NetworkError as error:
            fatal_error = RssNetworkError(
                context.source_name,
                normalize_value(error) or type(error).__name__
            )

        except httpx.HTTPError as error:
            safe_url = (
                sanitize_url_for_logging(str(error.request.url))
                if getattr(error, "request", None) is not None
                else ""
            )
            raise RuntimeError(
                f"Erreur HTTP pendant l'extraction de {context.source_name}"
                f"{f', URL={safe_url}' if safe_url else ''} : {type(error).__name__}."
            ) from None

        except (OSError, ValueError, TypeError, UnicodeDecodeError) as error:
            safe_message = sanitize_url_for_logging(normalize_value(error))
            raise RuntimeError(
                f"Erreur pendant l'extraction de {context.source_name} : "
                f"{safe_message or type(error).__name__}."
            ) from None

        request_errors = get_rss_request_errors()
        requests_count = get_rss_request_count()

    finally:
        _RSS_REQUEST_COUNT.reset(request_count_token)
        _RSS_REQUEST_ERRORS.reset(request_errors_token)

    # Arrêt sans aucun article : la source est en échec
    if fatal_error is not None and not context.articles:
        raise fatal_error

    # Assembler les compteurs et les métadonnées
    fatal_message = ""
    fatal_reason = ""

    if fatal_error is not None:
        fatal_message = sanitize_url_for_logging(fatal_error.reason)
        fatal_reason = get_rss_failure_reason(fatal_error)
        request_errors.append(fatal_message)

    rejected_count = sum(context.rejection_stats.values())
    analyzed_count = max(
        context.processed_count,
        len(context.articles) + rejected_count
    )
    metadata = {
        **base_metadata,
        "converted_count": context.converted_count,
        "processed_count": context.processed_count,
        "request_errors_count": len(request_errors)
    }

    if fatal_error is not None:
        metadata.update({
            "partial_failure_reason": fatal_reason,
            "partial_error_type": type(fatal_error).__name__,
            "partial_error_status_code": fatal_error.status_code
        })

    log_extraction_summary(
        source_name=context.source_name,
        extracted_count=len(context.articles),
        processed_count=context.processed_count,
        rejection_stats=context.rejection_stats
    )

    # Qualifier la source : arrêt, requêtes en erreur, ou lecture complète
    if fatal_error is not None:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS
        message = (
            f"Extraction interrompue après {len(context.articles)} article(s) "
            f"conservé(s) : {fatal_message}"
        )
    elif request_errors:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS if context.articles else EXTRACTOR_STATUS_FAILED
        message = (
            f"{len(request_errors)} requête(s) RSS en erreur, "
            f"{len(context.articles)} article(s) conservé(s)."
            if context.articles
            else f"{len(request_errors)} requête(s) RSS en erreur."
        )
    else:
        status = EXTRACTOR_STATUS_SUCCESS if context.articles else EXTRACTOR_STATUS_EMPTY
        message = "" if context.articles else "Aucun article exploitable retourné par le flux RSS."

    return ExtractorResult(
        name=context.source_name,
        source_type=SOURCE_TYPE_RSS,
        status=status,
        message=message,
        articles=context.articles,
        errors=request_errors,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=dict(context.rejection_stats),
        metadata=metadata
    )


# Extraction d'un lot

def build_failed_source_result(
    source_name: str,
    error: Any,
    source_id: str = ""
) -> ExtractorResult:
    """Construit un résultat d'échec homogène pour une source RSS.

    Le message n'est pas ajouté aux erreurs RSS courantes : celles-ci sont
    propres à l'extraction d'une source (`extract_rss_source`), et l'échec
    est déjà porté par le résultat retourné.

    Parameters
    ----------
    source_name : str
        Nom de la source.
    error : Any
        Erreur rencontrée ; pour un arrêt RSS, son motif et son code HTTP vont
        dans les métadonnées.
    source_id : str
        Identifiant de la source. Par défaut vide.

    Returns
    -------
    ExtractorResult
        Résultat au statut échec.
    """

    message = get_rss_error_message(error)
    metadata: dict[str, Any] = {
        "source_id": source_id,
        "failure_reason": RSS_ERROR_EXTRACTION_STOPPED,
        "error_type": type(error).__name__
    }

    # Un arrêt RSS précise son motif et son code HTTP
    if isinstance(error, RssExtractionStoppedError):
        metadata["failure_reason"] = get_rss_failure_reason(error)
        metadata["error_status_code"] = error.status_code

    return ExtractorResult(
        name=source_name,
        source_type=SOURCE_TYPE_RSS,
        status=EXTRACTOR_STATUS_FAILED,
        message=message,
        errors=[message],
        metadata=metadata
    )


def extract_rss_sources(
    sources: Sequence[RssSource],
    adapter: RssAdapter
) -> ExtractorResult:
    """Extrait plusieurs sources et agrège leurs résultats détaillés.

    Une source en échec (configuration invalide, arrêt, erreur inattendue)
    n'arrête pas les autres.

    Parameters
    ----------
    sources : Sequence[RssSource]
        Configurations des sources.
    adapter : RssAdapter
        Adaptateur des flux.

    Returns
    -------
    ExtractorResult
        Résultat agrégé : articles sans doublon entre sources, statut du lot
        et compteurs par statut de source.

    Raises
    ------
    TypeError
        Si `adapter` n'est pas un `RssAdapter` ou si `sources` n'est pas une
        séquence (ou est une chaîne).
    """

    if not isinstance(adapter, RssAdapter):
        raise TypeError("adapter doit être une instance de RssAdapter.")
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise TypeError("sources doit être une séquence de configurations RSS.")

    # Extraire chaque source ; toute erreur devient un résultat d'échec
    context = RssBatchContext()

    for source_index, source in enumerate(sources, start=1):
        if not isinstance(source, Mapping):
            result = build_failed_source_result(
                source_name=f"source RSS {source_index}",
                error=TypeError(f"Configuration invalide : {type(source).__name__}.")
            )
            context.add_result(result)
            logger.error(
                "Source RSS %s/%s ignorée : configuration invalide (%s).",
                source_index,
                len(sources),
                type(source).__name__
            )
            continue

        source_name = normalize_value(source.get("name")) or "source RSS"
        source_id = normalize_value(source.get("source_id"))

        try:
            result = extract_rss_source(source, adapter)
        except RssExtractionStoppedError as error:
            result = build_failed_source_result(source_name, error, source_id)
            logger.error(
                "Échec de la source RSS %s%s : %s",
                source_name,
                f" (HTTP {error.status_code})" if error.status_code else "",
                result.message
            )
        except Exception as error:
            result = build_failed_source_result(source_name, error, source_id)
            logger.exception(
                "Échec inattendu de la source RSS %s : %s",
                source_name,
                result.message
            )

        context.add_result(result)

        if result.status == EXTRACTOR_STATUS_SUCCESS:
            logger.info("Source RSS réussie : %s, %s article(s).", source_name, len(result.articles))
        elif result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS:
            logger.warning("Source RSS partiellement réussie : %s, %s", source_name, result.message)
        elif result.status == EXTRACTOR_STATUS_EMPTY:
            logger.warning("Source RSS terminée sans article exploitable : %s.", source_name)

    # Qualifier le lot et rédiger son message
    total_sources = len(sources)
    metadata = {
        "configured_sources": total_sources,
        "successful_sources": context.successful_sources,
        "partial_sources": context.partial_sources,
        "empty_sources": context.empty_sources,
        "failed_sources": context.failed_sources,
        "duplicate_count": context.duplicate_count
    }

    if context.failed_sources or context.partial_sources:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS if context.articles else EXTRACTOR_STATUS_FAILED
    else:
        status = EXTRACTOR_STATUS_SUCCESS if context.articles else EXTRACTOR_STATUS_EMPTY

    if status == EXTRACTOR_STATUS_FAILED:
        message = f"Échec des {context.failed_sources} source(s) RSS exécutée(s)."
    elif status == EXTRACTOR_STATUS_PARTIAL_SUCCESS:
        message = (
            f"Extraction RSS partielle : {len(context.articles)} article(s), "
            f"{context.failed_sources} échec(s), {context.partial_sources} source(s) partielle(s)."
        )
    elif status == EXTRACTOR_STATUS_EMPTY:
        message = "Aucune source RSS n'a produit d'article exploitable."
    else:
        message = ""

    # Journaliser le bilan et assembler le résultat
    logger.info(
        "%s article(s) RSS unique(s), %s réussite(s), %s partielle(s), "
        "%s vide(s), %s échec(s), %s doublon(s).",
        len(context.articles),
        context.successful_sources,
        context.partial_sources,
        context.empty_sources,
        context.failed_sources,
        context.duplicate_count
    )

    return ExtractorResult(
        name=adapter.default_name,
        source_type=SOURCE_TYPE_RSS,
        status=status,
        message=message,
        articles=context.articles,
        errors=context.errors,
        analyzed_count=context.analyzed_count,
        rejected_count=context.rejected_count,
        requests_count=context.requests_count,
        rejection_reasons=dict(context.rejection_stats),
        metadata=metadata
    )


# API publique du moteur

def extract_articles_from_sources(
    sources: Sequence[RssSource],
    extractor: RssExtractor
) -> list[dict[str, Any]]:
    """Exécute un lot déjà chargé et retourne uniquement ses articles.

    Parameters
    ----------
    sources : Sequence[RssSource]
        Configurations des sources.
    extractor : RssExtractor
        Extracteur à utiliser.

    Returns
    -------
    list[dict[str, Any]]
        Articles retenus.

    Raises
    ------
    TypeError
        Si `extractor` n'est pas un `RssExtractor`.
    """

    if not isinstance(extractor, RssExtractor):
        raise TypeError("extractor doit être une instance de RssExtractor.")
    return extractor.extract_sources(sources)


def load_rss_sources(extractor: RssExtractor) -> list[dict[str, Any]]:
    """Recharge puis retourne les sources RSS.

    Parameters
    ----------
    extractor : RssExtractor
        Extracteur dont le registre est rechargé.

    Returns
    -------
    list[dict[str, Any]]
        Configuration de chaque source RSS.

    Raises
    ------
    TypeError
        Si `extractor` n'est pas un `RssExtractor`.
    """

    if not isinstance(extractor, RssExtractor):
        raise TypeError("extractor doit être une instance de RssExtractor.")
    extractor.reload_sources()
    return extractor.get_sources()


def extract_all_articles(extractor: RssExtractor) -> ExtractorResult:
    """Lance toutes les sources RSS actives.

    Parameters
    ----------
    extractor : RssExtractor
        Extracteur à exécuter.

    Returns
    -------
    ExtractorResult
        Résultat agrégé de toutes les sources RSS.

    Raises
    ------
    TypeError
        Si `extractor` n'est pas un `RssExtractor`.
    """

    if not isinstance(extractor, RssExtractor):
        raise TypeError("extractor doit être une instance de RssExtractor.")
    return extractor.run()