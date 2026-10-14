"""Moteur commun d'orchestration des calendriers iCal.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), calqué sur
`src/extractors/rss/rss_engine.py` de CheckIt.AI : chargement des sources de
la section `ical_sources`, une source en échec n'arrête pas les autres,
statuts communs (succès, partiel, vide, échec), motifs de rejet comptés,
dédoublonnage par source puis sur le lot. La version RSS gère en plus des
codes d'erreur propres aux flux de presse ; on garde ici l'essentiel.
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
    SOURCE_TYPE_ICAL,
)
from config.paths import SOURCES_FILE
from config.source_config import SourceRegistry, load_source_registry
from src.article.processing.article_deduplicator import is_duplicate_article, register_article
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.core.extractor_service import execute_configured_sources_extractor
from src.extractors.ical.ical_adapter import (
    IcalAdapter,
    IcalSource,
    validate_ical_article,
    validate_ical_item,
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

@dataclass(slots=True)
class IcalExtractionContext:
    """État de l'extraction d'un calendrier."""

    source: dict[str, Any]
    filters: dict[str, Any]
    maximum_articles: int
    articles: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    processed_count: int = 0

    @property
    def source_name(self) -> str:
        return normalize_value(self.source.get("name")) or "calendrier iCal"

    @property
    def source_id(self) -> str:
        return normalize_value(self.source.get("source_id")) or self.source_name

    @property
    def completed(self) -> bool:
        return len(self.articles) >= self.maximum_articles

    def reject(self, reason: Any) -> None:
        self.rejection_stats[normalize_value(reason) or "article_invalide"] += 1


@dataclass(slots=True)
class IcalBatchContext:
    """Résultats agrégés de plusieurs calendriers."""

    articles: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    analyzed_count: int = 0
    rejected_count: int = 0
    requests_count: int = 0

    def add_result(self, result: ExtractorResult) -> None:
        self.analyzed_count += result.analyzed_count
        self.rejected_count += result.rejected_count
        self.requests_count += result.requests_count
        self.errors.extend(result.errors)
        self.rejection_stats.update(result.rejection_reasons)
        self.statuses[result.status] += 1
        for article in result.articles:
            # Deux lieux peuvent annoncer le même événement.
            if is_duplicate_article(article, self.seen_keys):
                self.rejection_stats["doublon_lot"] += 1
                self.rejected_count += 1
                continue
            register_article(article, self.seen_keys)
            self.articles.append(article)


# ---------------------------------------------------------------------------
# Façade
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class IcalExtractor:
    """Charge les sources `ical_sources` et délègue leur lecture à un adaptateur."""

    adapter: IcalAdapter
    sources_file: Path = SOURCES_FILE
    _registry: SourceRegistry | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, IcalAdapter):
            raise TypeError("adapter doit être une instance de IcalAdapter.")

    def get_sources(self) -> list[dict[str, Any]]:
        if self._registry is None:
            self._registry = load_source_registry(self.sources_file, require_secrets=False)
        return [source.to_dict() for source in self._registry.by_type(SOURCE_TYPE_ICAL)]

    def extract_sources_result(self, sources: Sequence[IcalSource]) -> ExtractorResult:
        return extract_ical_sources(sources, self.adapter)

    def run(self) -> ExtractorResult:
        return execute_configured_sources_extractor(
            extractor_name=self.adapter.default_name,
            source_type=SOURCE_TYPE_ICAL,
            sources_loader=self.get_sources,
            extraction_function=self.extract_sources_result,
        )


# ---------------------------------------------------------------------------
# Une source
# ---------------------------------------------------------------------------

def process_ical_item(item: Any, context: IcalExtractionContext, adapter: IcalAdapter) -> None:
    """Valide, transforme puis déduplique un élément du calendrier."""

    context.processed_count += 1
    valid, reason = validate_ical_item(item, context.source, adapter)
    if not valid:
        context.reject(reason)
        return
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
    article["source_type"] = SOURCE_TYPE_ICAL
    article.setdefault("source_name", context.source_name)

    valid, reason = validate_ical_article(article, context.filters)
    if not valid:
        context.reject(reason)
        return
    if is_duplicate_article(article, context.seen_keys):
        context.reject("doublon")
        return
    register_article(article, context.seen_keys)
    context.articles.append(article)


def extract_ical_source(source: Mapping[str, Any], adapter: IcalAdapter) -> ExtractorResult:
    """Lit un calendrier et renvoie un résultat qualifié."""

    filters = get_filter_configuration(dict(source))
    context = IcalExtractionContext(
        source=dict(source),
        filters=dict(filters) if isinstance(filters, Mapping) else {},
        maximum_articles=parse_non_negative_integer(source.get("max_articles"), default=0),
    )
    metadata = {"source_id": context.source_id, "maximum_articles": context.maximum_articles}

    if not parse_boolean(context.source.get("enabled"), default=True):
        return ExtractorResult(name=context.source_name, source_type=SOURCE_TYPE_ICAL,
                               status=EXTRACTOR_STATUS_EMPTY, message="Calendrier désactivé.", metadata=metadata)
    if context.maximum_articles <= 0:
        return ExtractorResult(name=context.source_name, source_type=SOURCE_TYPE_ICAL,
                               status=EXTRACTOR_STATUS_EMPTY, message="Aucun article demandé.", metadata=metadata)

    logger.info("Début de la lecture du calendrier : %s.", context.source_name)
    for item in adapter.iter_items(context.source):
        if context.completed:
            break
        process_ical_item(item, context, adapter)

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
        source_type=SOURCE_TYPE_ICAL,
        status=status,
        message="" if context.articles else "Aucun événement exploitable dans le calendrier.",
        articles=context.articles,
        analyzed_count=max(context.processed_count, len(context.articles) + rejected),
        rejected_count=rejected,
        requests_count=1,
        rejection_reasons=dict(context.rejection_stats),
        metadata={**metadata, "processed_count": context.processed_count},
    )


def build_failed_source_result(source_name: str, error: BaseException, source_id: str = "") -> ExtractorResult:
    """Résultat d'échec homogène pour un calendrier illisible ou injoignable."""

    if isinstance(error, httpx.HTTPStatusError):
        message = f"Calendrier inaccessible (HTTP {error.response.status_code})."
    elif isinstance(error, httpx.TimeoutException):
        message = "Délai dépassé pendant le téléchargement du calendrier."
    elif isinstance(error, httpx.HTTPError):
        message = f"Erreur réseau : {type(error).__name__}."
    else:
        message = sanitize_url_for_logging(normalize_value(error)) or type(error).__name__
    return ExtractorResult(
        name=source_name,
        source_type=SOURCE_TYPE_ICAL,
        status=EXTRACTOR_STATUS_FAILED,
        message=message,
        errors=[f"{source_name} : {message}"],
        requests_count=1,
        metadata={"source_id": source_id, "error_type": type(error).__name__},
    )


# ---------------------------------------------------------------------------
# Plusieurs sources
# ---------------------------------------------------------------------------

def extract_ical_sources(sources: Sequence[IcalSource], adapter: IcalAdapter) -> ExtractorResult:
    """Lit tous les calendriers ; une source en échec n'arrête pas les autres."""

    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise TypeError("sources doit être une séquence de configurations iCal.")

    batch = IcalBatchContext()
    for source in sources:
        name = normalize_value(source.get("name")) if isinstance(source, Mapping) else ""
        source_id = normalize_value(source.get("source_id")) if isinstance(source, Mapping) else ""
        try:
            if not isinstance(source, Mapping):
                raise TypeError(f"Configuration iCal invalide : {type(source).__name__}.")
            result = extract_ical_source(source, adapter)
        except Exception as error:  # noqa: BLE001 (une source en panne ne doit pas arrêter les autres)
            result = build_failed_source_result(name or "calendrier iCal", error, source_id)
            logger.error("Échec du calendrier %s : %s", name or "?", result.message)
        batch.add_result(result)

    failed = batch.statuses[EXTRACTOR_STATUS_FAILED]
    if failed:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS if batch.articles else EXTRACTOR_STATUS_FAILED
    else:
        status = EXTRACTOR_STATUS_SUCCESS if batch.articles else EXTRACTOR_STATUS_EMPTY
    message = {
        EXTRACTOR_STATUS_FAILED: f"Échec des {failed} calendrier(s).",
        EXTRACTOR_STATUS_PARTIAL_SUCCESS: f"{len(batch.articles)} événement(s), {failed} calendrier(s) en échec.",
        EXTRACTOR_STATUS_EMPTY: "Aucun calendrier n'a produit d'événement exploitable.",
    }.get(status, "")
    logger.info(
        "iCal : %s événement(s) unique(s) depuis %s calendrier(s), %s échec(s).",
        len(batch.articles), len(sources), failed,
    )
    return ExtractorResult(
        name=adapter.default_name,
        source_type=SOURCE_TYPE_ICAL,
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
