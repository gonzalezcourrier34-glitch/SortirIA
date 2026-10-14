"""Moteur d'exécution commun des extracteurs CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/core/extractor_executor.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any

from config.source_config import SourceConfig, SourceRegistry
from src.extractors.core.extractor_configuration import (
    build_source_metadata,
    load_extractor_configuration,
    normalize_source_configuration,
)
from src.extractors.core.extractor_models import (
    ConfigurationType,
    SourceCollectionLoader,
    SourceLoader,
)
from src.extractors.core.extractor_results import (
    ConfiguredExtractor,
    ConfiguredSourcesExtractor,
    ExtractorResult,
    RawExtractorResult,
    build_disabled_result,
    build_empty_result,
    build_failed_result,
    normalize_extractor_result,
)
from src.logger import get_logger

logger = get_logger(__name__)

LoadedConfiguration = ConfigurationType | SourceConfig
LoadedSourceCollection = Sequence[LoadedConfiguration] | SourceRegistry


# Erreurs

def get_api_error_types() -> tuple[type[Exception], ...] :
    """Charge les exceptions API sans créer d'import circulaire."""

    from src.extractors.apis.api_extractor import (
        ApiAuthenticationError,
        ApiQuotaExceededError,
        ApiRateLimitError,
        ApiInvalidParametersError,
        ApiMaintenanceError,
        ApiExtractionStoppedError
    )

    return (
        ApiAuthenticationError,
        ApiQuotaExceededError,
        ApiRateLimitError,
        ApiInvalidParametersError,
        ApiMaintenanceError,
        ApiExtractionStoppedError
    )

def build_failure_metadata(
    reason: str,
    error: Exception | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    """Construit les métadonnées communes d'un échec."""

    failure_metadata = {
        "failure_reason": str(reason or "").strip().casefold() or "unknown",
    }

    if error is not None:
        failure_metadata["error_type"] = type(error).__name__

    return {**failure_metadata, **metadata}


def build_failure_reasons(
    reason: str,
    count: int = 1,
) -> dict[str, int]:
    """Construit un compteur homogène pour une cause d'échec."""

    normalized_reason = str(reason or "").strip().casefold() or "unknown"
    return {normalized_reason: max(0, int(count))} if count else {}

def build_extraction_error_result(
    extractor_name: str,
    source_type: str,
    error: Exception,
    started_at: float,
) -> ExtractorResult:
    """Transforme une exception isolée en résultat d'extraction."""

    duration_seconds = round(perf_counter() - started_at, 3)
    (
        ApiAuthenticationError,
        ApiQuotaExceededError,
        ApiRateLimitError,
        ApiInvalidParametersError,
        ApiMaintenanceError,
        ApiExtractionStoppedError
    ) = get_api_error_types()

    if isinstance(error, ApiQuotaExceededError):
        logger.warning(
            "Extraction %s arrêtée : quota dépassé pour %s.",
            extractor_name,
            error.source_name,
        )
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message=f"Quota dépassé : {error.reason}",
            duration_seconds=duration_seconds,
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons("quota_exceeded"),
            metadata=build_failure_metadata(
                "quota_exceeded",
                error,
                source_name=error.source_name,
                status_code=error.status_code,
            ),
        )

    if isinstance(error, ApiAuthenticationError):
        logger.warning(
            "Extraction %s arrêtée : authentification refusée pour %s.",
            extractor_name,
            error.source_name,
        )
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message=f"Authentification refusée : {error.reason}",
            duration_seconds=duration_seconds,
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons("authentication_failed"),
            metadata=build_failure_metadata(
                "authentication_failed",
                error,
                source_name=error.source_name,
                status_code=error.status_code,
            ),
        )
        
    if isinstance(error, ApiRateLimitError):
        logger.warning(
            "Extraction %s arrêtée : limitation de fréquence pour %s.",
            extractor_name,
            error.source_name,
        )
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message=f"Limitation de fréquence : {error.reason}",
            duration_seconds=duration_seconds,
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons("rate_limited"),
            metadata=build_failure_metadata(
                "rate_limited",
                error,
                source_name=error.source_name,
                status_code=error.status_code,
            ),
        )

    if isinstance(error, ApiInvalidParametersError):
        logger.warning(
            "Extraction %s arrêtée : paramètres invalides pour %s.",
            extractor_name,
            error.source_name,
        )
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message=f"Paramètres API invalides : {error.reason}",
            duration_seconds=duration_seconds,
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons("invalid_parameters"),
            metadata=build_failure_metadata(
                "invalid_parameters",
                error,
                source_name=error.source_name,
                status_code=error.status_code,
            ),
        )
        
    if isinstance(error, ApiMaintenanceError):
        logger.warning(
            "Extraction %s arrêtée : maintenance de %s.",
            extractor_name,
            error.source_name,
        )
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message=f"API temporairement indisponible : {error.reason}",
            duration_seconds=duration_seconds,
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons("maintenance"),
            metadata=build_failure_metadata(
                "maintenance",
                error,
                source_name=error.source_name,
                status_code=error.status_code,
            ),
        )
                            
    if isinstance(error, ApiExtractionStoppedError):
        logger.warning(
            "Extraction %s arrêtée pour %s : %s.",
            extractor_name,
            error.source_name,
            error.reason,
        )
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message=f"Extraction API arrêtée : {error.reason}",
            duration_seconds=duration_seconds,
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons("api_extraction_stopped"),
            metadata=build_failure_metadata(
                "api_extraction_stopped",
                error,
                source_name=error.source_name,
                status_code=error.status_code,
            ),
        )

    logger.exception(
        "Échec inattendu de l'extraction %s.",
        extractor_name,
        exc_info=error,
    )
    return build_failed_result(
        name=extractor_name,
        source_type=source_type,
        message=str(error) or type(error).__name__,
        duration_seconds=duration_seconds,
        analyzed_count=1,
        rejected_count=1,
        rejection_reasons=build_failure_reasons("unexpected_error"),
        metadata=build_failure_metadata("unexpected_error", error),
    )


# Exécution

def execute_extractor_call(
    extractor_name: str,
    source_type: str,
    extraction_function: Callable[[], RawExtractorResult],
) -> ExtractorResult:
    """Exécute un extracteur sans propager une erreur isolée."""

    started_at = perf_counter()

    if not callable(extraction_function):
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message="La fonction d'extraction doit être appelable.",
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons(
                "invalid_extraction_function",
            ),
            metadata=build_failure_metadata(
                "invalid_extraction_function",
            ),
        )

    try:
        result = normalize_extractor_result(
            extraction_function(),
            extractor_name,
            source_type,
        )
    except Exception as error:
        return build_extraction_error_result(
            extractor_name,
            source_type,
            error,
            started_at,
        )

    result.duration_seconds = round(perf_counter() - started_at, 3)
    return result


def execute_loaded_extractor(
    extractor_name: str,
    source_type: str,
    configuration: ConfigurationType,
    extraction_function: Callable[
        [ConfigurationType],
        RawExtractorResult,
    ],
) -> ExtractorResult:
    """Exécute un extracteur avec une configuration déjà chargée."""

    if not callable(extraction_function):
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message="La fonction d'extraction doit être appelable.",
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons(
                "invalid_extraction_function",
            ),
            metadata=build_failure_metadata(
                "invalid_extraction_function",
            ),
        )

    return execute_extractor_call(
        extractor_name,
        source_type,
        lambda: extraction_function(configuration),
    )


# Source unique

def execute_configured_extractor(
    extractor_name: str,
    source_type: str,
    source_loader: SourceLoader,
    extraction_function: ConfiguredExtractor,
) -> ExtractorResult:
    """Charge une source puis lance son extracteur."""

    source, loading_error = load_extractor_configuration(
        extractor_name,
        source_type,
        source_loader,
    )

    if loading_error:
        return loading_error

    if source is None:
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message="Configuration indisponible.",
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons(
                "configuration_unavailable",
            ),
            metadata=build_failure_metadata(
                "configuration_unavailable",
            ),
        )

    normalized_source = normalize_source_configuration(
        source,
        extractor_name,
    )

    if normalized_source is None:
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message="Configuration invalide ou vide.",
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons(
                "invalid_configuration",
            ),
            metadata=build_failure_metadata(
                "invalid_configuration",
            ),
        )

    metadata = build_source_metadata(
        normalized_source,
        source_type,
    )

    if not normalized_source.get("enabled", True):
        return build_disabled_result(
            name=extractor_name,
            source_type=source_type,
            metadata=metadata,
        )

    result = execute_loaded_extractor(
        extractor_name,
        source_type,
        normalized_source,
        extraction_function,
    )
    result.merge_metadata(metadata)
    return result


# Collection de sources

def normalize_source_collection(
    sources: LoadedSourceCollection,
    source_type: str,
) -> list[LoadedConfiguration] | None:
    """Uniformise un registre ou une séquence de configurations."""

    if isinstance(sources, SourceRegistry):
        return list(sources.by_type(source_type))

    if isinstance(sources, Sequence) and not isinstance(
        sources,
        (str, bytes, bytearray),
    ):
        return list(sources)

    return None


def normalize_configured_sources(
    source_collection: Sequence[LoadedConfiguration],
    extractor_name: str,
) -> tuple[list[ConfigurationType], int]:
    """Normalise les sources et compte celles qui sont ignorées."""

    normalized_sources = [
        source
        for item in source_collection
        if (
            source := normalize_source_configuration(
                item,
                extractor_name,
            )
        )
        is not None
    ]
    return (
        normalized_sources,
        len(source_collection) - len(normalized_sources),
    )


def build_collection_metadata(
    source_collection: Sequence[LoadedConfiguration],
    normalized_sources: Sequence[ConfigurationType],
    ignored_count: int,
) -> dict[str, Any]:
    """Construit les métadonnées d'une collection de sources."""

    enabled_sources = [
        source
        for source in normalized_sources
        if source.get("enabled", True)
    ]
    source_ids = [
        source_id
        for source in enabled_sources
        if (source_id := source.get("source_id") or source.get("id"))
    ]

    return {
        "configured_sources_count": len(source_collection),
        "valid_sources_count": len(normalized_sources),
        "ignored_sources_count": ignored_count,
        "enabled_sources_count": len(enabled_sources),
        "source_ids": source_ids,
    }


def execute_configured_sources_extractor(
    extractor_name: str,
    source_type: str,
    sources_loader: SourceCollectionLoader,
    extraction_function: ConfiguredSourcesExtractor,
) -> ExtractorResult:
    """Charge plusieurs sources puis lance leur extracteur."""

    sources, loading_error = load_extractor_configuration(
        extractor_name,
        source_type,
        sources_loader,
    )

    if loading_error:
        return loading_error

    if sources is None:
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message="Configurations indisponibles.",
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons(
                "configurations_unavailable",
            ),
            metadata=build_failure_metadata(
                "configurations_unavailable",
            ),
        )

    source_collection = normalize_source_collection(
        sources,
        source_type,
    )

    if source_collection is None:
        return build_failed_result(
            name=extractor_name,
            source_type=source_type,
            message="Configurations invalides.",
            analyzed_count=1,
            rejected_count=1,
            rejection_reasons=build_failure_reasons(
                "invalid_configurations",
            ),
            metadata=build_failure_metadata(
                "invalid_configurations",
            ),
        )

    normalized_sources, ignored_count = normalize_configured_sources(
        source_collection,
        extractor_name,
    )
    metadata = build_collection_metadata(
        source_collection,
        normalized_sources,
        ignored_count,
    )

    if not normalized_sources:
        return build_empty_result(
            name=extractor_name,
            source_type=source_type,
            message="Aucune source disponible.",
            analyzed_count=ignored_count,
            rejected_count=ignored_count,
            rejection_reasons=build_failure_reasons(
                "invalid_configuration",
                ignored_count,
            ),
            metadata=metadata,
        )

    enabled_sources = [
        source
        for source in normalized_sources
        if source.get("enabled", True)
    ]

    if not enabled_sources:
        return build_disabled_result(
            name=extractor_name,
            source_type=source_type,
            message="Toutes les sources sont désactivées.",
            metadata=metadata,
        )

    result = execute_loaded_extractor(
        extractor_name,
        source_type,
        enabled_sources,
        extraction_function,
    )

    if ignored_count:
        result.merge_rejection_reasons(
            build_failure_reasons(
                "invalid_configuration",
                ignored_count,
            )
        )
        result.analyzed_count = max(
            result.analyzed_count,
            result.extracted_count + result.rejected_count,
        )

    result.merge_metadata(metadata)
    return result