"""Moteur commun des « sources-fichiers » : un document téléchargé par source.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), calqué sur
`src/extractors/rss/rss_engine.py` de CheckIt.AI. Écrit d'abord pour les
calendriers iCal, puis rendu commun : le même moteur sert aux calendriers
(`ical_sources`) et aux programmes PDF lus par Mistral (`document_sources`),
seul le lecteur change.

Comme le moteur RSS : chargement des sources d'une section de
`config/sources.yaml`, une source en échec n'arrête pas les autres, statuts
communs (succès, partiel, vide, échec), motifs de rejet comptés,
dédoublonnage par source puis sur le lot.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from config.constants import (
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
)
from config.paths import SOURCES_FILE
from config.source_config import SourceRegistry, load_source_registry
from src.article.processing.article_deduplicator import get_article_duplicate_keys
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.core.extractor_service import execute_configured_sources_extractor
from src.extractors.files.file_adapter import (
    FileSource,
    FileSourceAdapter,
    validate_file_article,
    validate_file_item,
)
from src.logger import get_logger
from src.utils.extractor_utils import log_extraction_summary
from src.utils.filter_utils import get_filter_configuration
from src.utils.parsing_utils import parse_boolean, parse_non_negative_integer
from src.utils.url_utils import sanitize_url_for_logging
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# État d'une source et d'un lot
# ---------------------------------------------------------------------------

def identity_keys(article: Mapping[str, Any]) -> set[str]:
    """Clés d'identité d'un article : identifiant et adresse, pas le titre.

    La clé « même titre, même source, même jour » de CheckIt.AI (articles de
    presse) fusionnait deux événements distincts : « Marché » à Mèze et à
    Marseillan le même samedi, « Visite guidée » dans deux musées (15/10/2026).
    Le dédoublonnage par titre + jour + LIEU est fait ensuite par la chaîne
    d'événements (event_pipeline).

    Parameters
    ----------
    article : Mapping[str, Any]
        Article standard du moteur.

    Returns
    -------
    set[str]
        Clés de doublon de l'article, sans celles fondées sur le titre.
    """

    return {key for key in get_article_duplicate_keys(article) if ":title:" not in key and not key.startswith("title:")}


def is_same_event(article: Mapping[str, Any], seen_keys: set[str]) -> bool:
    """Vrai si l'article a déjà été vu (même identifiant ou même adresse).

    Parameters
    ----------
    article : Mapping[str, Any]
        Article à tester.
    seen_keys : set[str]
        Clés d'identité des articles déjà retenus.

    Returns
    -------
    bool
        Vrai si au moins une clé d'identité est déjà connue.
    """

    return bool(identity_keys(article) & seen_keys)


@dataclass(slots=True)
class FileExtractionContext:
    """État de l'extraction d'une source.

    Attributes
    ----------
    source : dict[str, Any]
        Configuration de la source.
    filters : dict[str, Any]
        Filtres appliqués aux articles de cette source.
    maximum_articles : int
        Nombre d'articles au-delà duquel la lecture s'arrête.
    articles : list[dict[str, Any]]
        Articles retenus.
    seen_keys : set[str]
        Clés d'identité déjà vues, pour le dédoublonnage dans la source.
    rejection_stats : Counter[str]
        Nombre de rejets par motif.
    processed_count : int
        Nombre d'éléments examinés.
    """

    source: dict[str, Any]
    filters: dict[str, Any]
    maximum_articles: int
    articles: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    processed_count: int = 0

    @property
    def source_name(self) -> str:
        """Nom lisible de la source.

        Returns
        -------
        str
            Le champ `name`, ou « source » s'il est vide.
        """
        return normalize_value(self.source.get("name")) or "source"

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


@dataclass(slots=True)
class FileBatchContext:
    """Résultats agrégés de plusieurs sources.

    Attributes
    ----------
    articles : list[dict[str, Any]]
        Articles retenus sur l'ensemble du lot, sans doublon entre sources.
    errors : list[str]
        Messages d'erreur des sources.
    seen_keys : set[str]
        Clés d'identité déjà vues sur le lot.
    rejection_stats : Counter[str]
        Nombre de rejets par motif, toutes sources confondues.
    statuses : Counter[str]
        Nombre de sources par statut (succès, vide, échec...).
    analyzed_count : int
        Nombre total d'éléments examinés.
    rejected_count : int
        Nombre total d'éléments rejetés.
    requests_count : int
        Nombre total de requêtes (téléchargements).
    """

    articles: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    analyzed_count: int = 0
    rejected_count: int = 0
    requests_count: int = 0

    def add_result(self, result: ExtractorResult) -> None:
        """Ajoute le résultat d'une source au lot, en écartant les doublons entre sources.

        Parameters
        ----------
        result : ExtractorResult
            Résultat d'une source (compteurs, erreurs, articles).
        """
        self.analyzed_count += result.analyzed_count
        self.rejected_count += result.rejected_count
        self.requests_count += result.requests_count
        self.errors.extend(result.errors)
        self.rejection_stats.update(result.rejection_reasons)
        self.statuses[result.status] += 1
        for article in result.articles:
            # Deux sources peuvent annoncer le même événement.
            if is_same_event(article, self.seen_keys):
                self.rejection_stats["doublon_lot"] += 1
                self.rejected_count += 1
                continue
            self.seen_keys |= identity_keys(article)
            self.articles.append(article)


# ---------------------------------------------------------------------------
# Façade
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FileSourceExtractor:
    """Charge les sources d'un type (« ical », « document ») et les fait lire par l'adaptateur.

    Attributes
    ----------
    adapter : FileSourceAdapter
        Lecteur du format.
    source_type : str
        Type de source dans `config/sources.yaml` (« ical », « document »).
    sources_file : Path
        Fichier de configuration des sources. Par défaut `SOURCES_FILE`.
    _registry : SourceRegistry | None
        Registre des sources, chargé à la première demande.
    """

    adapter: FileSourceAdapter
    source_type: str
    sources_file: Path = SOURCES_FILE
    _registry: SourceRegistry | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Vérifie le type de l'adaptateur.

        Raises
        ------
        TypeError
            Si `adapter` n'est pas un `FileSourceAdapter`.
        """
        if not isinstance(self.adapter, FileSourceAdapter):
            raise TypeError("adapter doit être une instance de FileSourceAdapter.")

    def get_sources(self) -> list[dict[str, Any]]:
        """Renvoie les sources configurées pour ce type (registre chargé une seule fois).

        Returns
        -------
        list[dict[str, Any]]
            Configuration de chaque source du type, sans exiger les secrets.
        """
        if self._registry is None:
            self._registry = load_source_registry(self.sources_file, require_secrets=False)
        return [source.to_dict() for source in self._registry.by_type(self.source_type)]

    def extract_sources_result(self, sources: Sequence[FileSource]) -> ExtractorResult:
        """Lit une liste de sources avec l'adaptateur de ce lecteur.

        Parameters
        ----------
        sources : Sequence[FileSource]
            Configurations des sources à lire.

        Returns
        -------
        ExtractorResult
            Résultat agrégé du lot.
        """
        return extract_file_sources(sources, self.adapter, self.source_type)

    def run(self) -> ExtractorResult:
        """Lit toutes les sources configurées, par le service d'extraction commun.

        Returns
        -------
        ExtractorResult
            Résultat agrégé de toutes les sources du type.
        """
        return execute_configured_sources_extractor(
            extractor_name=self.adapter.default_name,
            source_type=self.source_type,
            sources_loader=self.get_sources,
            extraction_function=self.extract_sources_result,
        )


# ---------------------------------------------------------------------------
# Une source
# ---------------------------------------------------------------------------

def process_file_item(item: Any, context: FileExtractionContext, adapter: FileSourceAdapter, source_type: str) -> None:
    """Valide, transforme puis déduplique un élément de la source.

    L'article retenu est ajouté à `context.articles` ; un rejet est compté
    sous son motif dans `context.rejection_stats`.

    Parameters
    ----------
    item : Any
        Élément brut renvoyé par l'adaptateur.
    context : FileExtractionContext
        État de l'extraction de la source, mis à jour sur place.
    adapter : FileSourceAdapter
        Lecteur du format.
    source_type : str
        Type de source, recopié dans l'article.
    """

    # Valider l'élément selon les règles du format
    context.processed_count += 1
    valid, reason = validate_file_item(item, context.source, adapter)
    if not valid:
        context.reject(reason)
        return
    # Construire l'article et y inscrire sa source
    try:
        article = adapter.build_article(item, context.source)
    except (ValueError, TypeError, KeyError) as error:
        logger.warning("Construction impossible pour %s : %s", context.source_name, error)
        context.reject("construction_invalide")
        return
    if not isinstance(article, dict) or not article:
        context.reject("article_vide")
        return
    article["source"] = context.source_id
    article["source_type"] = source_type
    article.setdefault("source_name", context.source_name)

    # Appliquer la validation commune, puis écarter les doublons de la source
    valid, reason = validate_file_article(article, context.filters)
    if not valid:
        context.reject(reason)
        return
    if is_same_event(article, context.seen_keys):
        context.reject("doublon")
        return
    context.seen_keys |= identity_keys(article)
    context.articles.append(article)


def extract_file_source(source: Mapping[str, Any], adapter: FileSourceAdapter, source_type: str) -> ExtractorResult:
    """Lit une source et renvoie un résultat qualifié.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (nom, url, `max_articles`, `enabled`, filtres...).
    adapter : FileSourceAdapter
        Lecteur du format.
    source_type : str
        Type de source (« ical », « document »).

    Returns
    -------
    ExtractorResult
        Statut succès ou vide, articles retenus et motifs de rejet. Les erreurs
        de lecture remontent à l'appelant (`extract_file_sources`).
    """

    # Préparer l'état de la source : filtres et nombre maximal d'articles
    filters = get_filter_configuration(dict(source))
    context = FileExtractionContext(
        source=dict(source),
        filters=dict(filters) if isinstance(filters, Mapping) else {},
        maximum_articles=parse_non_negative_integer(source.get("max_articles"), default=0),
    )
    metadata = {"source_id": context.source_id, "maximum_articles": context.maximum_articles}

    # Source désactivée ou sans quota : résultat vide sans téléchargement
    if not parse_boolean(context.source.get("enabled"), default=True):
        return ExtractorResult(name=context.source_name, source_type=source_type,
                               status=EXTRACTOR_STATUS_EMPTY, message="Source désactivée.", metadata=metadata)
    if context.maximum_articles <= 0:
        return ExtractorResult(name=context.source_name, source_type=source_type,
                               status=EXTRACTOR_STATUS_EMPTY, message="Aucun article demandé.", metadata=metadata)

    # Lire les éléments jusqu'au nombre maximal d'articles
    logger.info("Début de la lecture : %s.", context.source_name)
    for item in adapter.iter_items(context.source):
        if context.completed:
            break
        process_file_item(item, context, adapter, source_type)

    # Journaliser le bilan et qualifier le résultat
    rejected = sum(context.rejection_stats.values())
    log_extraction_summary(
        source_name=context.source_name,
        extracted_count=len(context.articles),
        processed_count=context.processed_count,
        rejection_stats=context.rejection_stats,
    )
    status = EXTRACTOR_STATUS_SUCCESS if context.articles else EXTRACTOR_STATUS_EMPTY
    return ExtractorResult(
        name=context.source_name,
        source_type=source_type,
        status=status,
        message="" if context.articles else "Aucun événement exploitable dans la source.",
        articles=context.articles,
        analyzed_count=max(context.processed_count, len(context.articles) + rejected),
        rejected_count=rejected,
        requests_count=1,
        rejection_reasons=dict(context.rejection_stats),
        metadata={**metadata, "processed_count": context.processed_count},
    )


def build_failed_source_result(
    source_name: str, error: BaseException, source_type: str, source_id: str = ""
) -> ExtractorResult:
    """Résultat d'échec homogène pour une source illisible ou injoignable.

    Parameters
    ----------
    source_name : str
        Nom de la source.
    error : BaseException
        Erreur rencontrée ; les erreurs HTTP, de délai et de réseau ont un
        message dédié, les autres gardent leur message (adresses masquées).
    source_type : str
        Type de source.
    source_id : str
        Identifiant de la source. Par défaut vide.

    Returns
    -------
    ExtractorResult
        Résultat au statut échec, avec le message et le type d'erreur.
    """

    if isinstance(error, httpx.HTTPStatusError):
        message = f"Fichier inaccessible (HTTP {error.response.status_code})."
    elif isinstance(error, httpx.TimeoutException):
        message = "Délai dépassé pendant le téléchargement."
    elif isinstance(error, httpx.HTTPError):
        message = f"Erreur réseau : {type(error).__name__}."
    else:
        message = sanitize_url_for_logging(normalize_value(error)) or type(error).__name__
    return ExtractorResult(
        name=source_name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_FAILED,
        message=message,
        errors=[f"{source_name} : {message}"],
        requests_count=1,
        metadata={"source_id": source_id, "error_type": type(error).__name__},
    )


# ---------------------------------------------------------------------------
# Plusieurs sources
# ---------------------------------------------------------------------------

def extract_file_sources(sources: Sequence[FileSource], adapter: FileSourceAdapter, source_type: str) -> ExtractorResult:
    """Lit toutes les sources ; une source en échec n'arrête pas les autres.

    Parameters
    ----------
    sources : Sequence[FileSource]
        Configurations des sources à lire.
    adapter : FileSourceAdapter
        Lecteur du format.
    source_type : str
        Type de source (« ical », « document »).

    Returns
    -------
    ExtractorResult
        Résultat agrégé : articles sans doublon entre sources, statut du lot
        (succès, partiel, vide, échec) et compteurs.

    Raises
    ------
    TypeError
        Si `sources` n'est pas une séquence (ou est une chaîne).
    """

    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise TypeError("sources doit être une séquence de configurations.")

    # Lire chaque source ; une erreur devient un résultat d'échec
    batch = FileBatchContext()
    for source in sources:
        name = normalize_value(source.get("name")) if isinstance(source, Mapping) else ""
        source_id = normalize_value(source.get("source_id")) if isinstance(source, Mapping) else ""
        try:
            if not isinstance(source, Mapping):
                raise TypeError(f"Configuration invalide : {type(source).__name__}.")
            result = extract_file_source(source, adapter, source_type)
        except Exception as error:  # noqa: BLE001 (une source en panne ne doit pas arrêter les autres)
            result = build_failed_source_result(name or "source", error, source_type, source_id)
            logger.error("Échec de la source %s : %s", name or "?", result.message)
        batch.add_result(result)

    # Qualifier le lot selon les échecs et les articles obtenus
    failed = batch.statuses[EXTRACTOR_STATUS_FAILED]
    if failed:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS if batch.articles else EXTRACTOR_STATUS_FAILED
    else:
        status = EXTRACTOR_STATUS_SUCCESS if batch.articles else EXTRACTOR_STATUS_EMPTY
    message = {
        EXTRACTOR_STATUS_FAILED: f"Échec des {failed} source(s).",
        EXTRACTOR_STATUS_PARTIAL_SUCCESS: f"{len(batch.articles)} événement(s), {failed} source(s) en échec.",
        EXTRACTOR_STATUS_EMPTY: "Aucune source n'a produit d'événement exploitable.",
    }.get(status, "")
    # Journaliser et assembler le résultat du lot
    logger.info(
        "%s : %s événement(s) unique(s) depuis %s source(s), %s échec(s).",
        adapter.default_name, len(batch.articles), len(sources), failed,
    )
    return ExtractorResult(
        name=adapter.default_name,
        source_type=source_type,
        status=status,
        message=message,
        articles=batch.articles,
        errors=batch.errors,
        analyzed_count=batch.analyzed_count,
        rejected_count=batch.rejected_count,
        requests_count=batch.requests_count,
        rejection_reasons=dict(batch.rejection_stats),
        metadata={"configured_sources": len(sources), **{f"sources_{k}": v for k, v in batch.statuses.items()}},
    )
