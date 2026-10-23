"""Moteur d'exécution commun des extracteurs CheckIt.AI.

Charge la ou les configurations de source, lance la fonction d'extraction et
transforme toute erreur isolée en `ExtractorResult` en échec, pour qu'une source
défaillante n'arrête jamais toute la collecte.

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
    """Charge les exceptions API sans créer d'import circulaire.

    L'import est fait à l'appel, car `api_extractor` importe lui-même ce module.

    Returns
    -------
    tuple[type[Exception], ...]
        Les classes d'erreur API, dans l'ordre : authentification, quota,
        limitation de fréquence, paramètres invalides, maintenance, arrêt.
    """

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
    """Construit les métadonnées communes d'un échec.

    Parameters
    ----------
    reason : str
        Cause de l'échec ; normalisée en minuscules, « unknown » si vide.
    error : Exception | None
        Exception d'origine, dont le nom de classe est ajouté en `error_type`.
        Par défaut aucune.
    **metadata
        Métadonnées supplémentaires, qui priment sur les clés communes.

    Returns
    -------
    dict[str, Any]
        Les métadonnées `failure_reason`, éventuellement `error_type`, puis les extras.
    """

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
    """Construit un compteur homogène pour une cause d'échec.

    Parameters
    ----------
    reason : str
        Cause de l'échec ; normalisée en minuscules, « unknown » si vide.
    count : int
        Nombre d'occurrences, ramené à 0 au minimum. Par défaut 1.

    Returns
    -------
    dict[str, int]
        `{cause: nombre}`, ou un dictionnaire vide si `count` vaut 0.
    """

    normalized_reason = str(reason or "").strip().casefold() or "unknown"
    return {normalized_reason: max(0, int(count))} if count else {}

def build_extraction_error_result(
    extractor_name: str,
    source_type: str,
    error: Exception,
    started_at: float,
) -> ExtractorResult:
    """Transforme une exception isolée en résultat d'extraction.

    Les erreurs API connues (quota, authentification, fréquence, paramètres,
    maintenance, arrêt) donnent un échec qualifié par sa cause et un simple
    avertissement ; toute autre erreur est journalisée avec sa trace complète.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, repris dans le résultat et les journaux.
    source_type : str
        Type de source (api, dataset, scraping...).
    error : Exception
        L'exception levée par la fonction d'extraction.
    started_at : float
        Instant de départ (`perf_counter()`), pour calculer la durée.

    Returns
    -------
    ExtractorResult
        Un résultat en échec, avec cause de rejet et métadonnées d'erreur.
    """

    # Mesurer la durée et récupérer les classes d'erreurs API (import différé)
    duration_seconds = round(perf_counter() - started_at, 3)
    (
        ApiAuthenticationError,
        ApiQuotaExceededError,
        ApiRateLimitError,
        ApiInvalidParametersError,
        ApiMaintenanceError,
        ApiExtractionStoppedError
    ) = get_api_error_types()

    # Erreurs API connues : un échec qualifié par sa cause, sans trace complète
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

    # Toute autre erreur : journaliser la trace et classer en « unexpected_error »
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
    """Exécute un extracteur sans propager une erreur isolée.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, repris dans le résultat.
    source_type : str
        Type de source (api, dataset, scraping...).
    extraction_function : Callable[[], RawExtractorResult]
        Fonction sans argument qui réalise l'extraction.

    Returns
    -------
    ExtractorResult
        Le résultat normalisé avec sa durée, ou un échec si la fonction n'est pas
        appelable ou a levé une exception.
    """

    # Refuser d'emblée une fonction d'extraction non appelable
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

    # Lancer l'extraction ; une exception devient un résultat en échec
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

    # Noter la durée réelle de l'extraction réussie
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
    """Exécute un extracteur avec une configuration déjà chargée.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, repris dans le résultat.
    source_type : str
        Type de source (api, dataset, scraping...).
    configuration : ConfigurationType
        Configuration normalisée (une source ou une liste de sources).
    extraction_function : Callable[[ConfigurationType], RawExtractorResult]
        Fonction d'extraction qui reçoit la configuration.

    Returns
    -------
    ExtractorResult
        Le résultat normalisé, ou un échec si la fonction n'est pas appelable.
    """

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
    """Charge une source puis lance son extracteur.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, repris dans le résultat.
    source_type : str
        Type de source (api, dataset, scraping...).
    source_loader : SourceLoader
        Fonction qui charge la configuration de la source.
    extraction_function : ConfiguredExtractor
        Fonction d'extraction qui reçoit la configuration normalisée.

    Returns
    -------
    ExtractorResult
        Le résultat enrichi des métadonnées de la source ; un échec si la
        configuration manque ou est invalide, un résultat « désactivé » si la
        source est coupée.
    """

    # Charger la configuration ; une erreur de chargement est renvoyée telle quelle
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

    # Normaliser la configuration et refuser une configuration vide ou invalide
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

    # Ne pas lancer une source désactivée
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

    # Extraire puis rattacher les métadonnées de la source au résultat
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
    """Uniformise un registre ou une séquence de configurations.

    Parameters
    ----------
    sources : LoadedSourceCollection
        Un `SourceRegistry` ou une séquence de configurations.
    source_type : str
        Type de source à retenir quand `sources` est un registre.

    Returns
    -------
    list[LoadedConfiguration] | None
        La liste des configurations, ou None si `sources` n'est ni un registre ni
        une séquence (une chaîne n'est pas acceptée).
    """

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
    """Normalise les sources et compte celles qui sont ignorées.

    Parameters
    ----------
    source_collection : Sequence[LoadedConfiguration]
        Les configurations brutes.
    extractor_name : str
        Nom de l'extracteur, pour les journaux de normalisation.

    Returns
    -------
    tuple[list[ConfigurationType], int]
        Les sources valides normalisées, et le nombre de sources écartées.
    """

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
    """Construit les métadonnées d'une collection de sources.

    Parameters
    ----------
    source_collection : Sequence[LoadedConfiguration]
        Les configurations brutes, avant normalisation.
    normalized_sources : Sequence[ConfigurationType]
        Les configurations valides normalisées.
    ignored_count : int
        Nombre de configurations écartées comme invalides.

    Returns
    -------
    dict[str, Any]
        Les compteurs (configurées, valides, ignorées, actives) et les
        identifiants des sources actives.
    """

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
    """Charge plusieurs sources puis lance leur extracteur.

    Seules les sources valides et actives sont passées à l'extracteur ; les
    configurations invalides sont comptées comme rejets dans le résultat.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, repris dans le résultat.
    source_type : str
        Type de source (api, dataset, scraping...).
    sources_loader : SourceCollectionLoader
        Fonction qui charge le registre ou la liste des configurations.
    extraction_function : ConfiguredSourcesExtractor
        Fonction d'extraction qui reçoit la liste des sources actives.

    Returns
    -------
    ExtractorResult
        Le résultat enrichi des métadonnées de collection ; un échec, un résultat
        vide ou « désactivé » quand rien ne peut être lancé.
    """

    # Charger les configurations ; une erreur de chargement est renvoyée telle quelle
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

    # Ramener le registre ou la séquence à une liste de configurations
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

    # Normaliser chaque source et préparer les métadonnées de la collection
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

    # Ne garder que les sources actives
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

    # Extraire, puis ajouter les configurations ignorées aux rejets
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