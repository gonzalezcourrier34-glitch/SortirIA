"""Façade publique du moteur d'extraction.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/core/extractor_service.py` (commit 93632c5).

Ce module conserve les imports historiques du projet tout en
répartissant les responsabilités dans plusieurs modules spécialisés.
"""

from __future__ import annotations

from src.extractors.core.extractor_configuration import (
    build_source_metadata,
    load_extractor_configuration,
    normalize_source_configuration
)
from src.extractors.core.extractor_executor import (
    execute_configured_extractor,
    execute_configured_sources_extractor,
    execute_extractor_call,
    execute_loaded_extractor
)
from src.extractors.core.extractor_models import (
    ConfigurationType,
    ExtractorConfiguration,
    ExtractorExecution,
    SourceCollection,
    SourceCollectionLoader,
    SourceConfiguration,
    SourceLoader
)
from src.extractors.core.extractor_results import (
    ArticleExtractor,
    ConfiguredExtractor,
    ConfiguredSourcesExtractor,
    ExtractorResult,
    RawExtractorResult,
    build_disabled_result,
    build_empty_result,
    build_failed_result,
    build_partial_result,
    build_robots_denied_result,
    build_success_result,
    normalize_extracted_articles,
    normalize_extractor_result
)
from src.extractors.core.extractor_runner import run_extractor

__all__ = [
    "ArticleExtractor",
    "ConfigurationType",
    "ConfiguredExtractor",
    "ConfiguredSourcesExtractor",
    "ExtractorConfiguration",
    "ExtractorExecution",
    "ExtractorResult",
    "RawExtractorResult",
    "SourceCollection",
    "SourceCollectionLoader",
    "SourceConfiguration",
    "SourceLoader",
    "build_disabled_result",
    "build_empty_result",
    "build_failed_result",
    "build_partial_result",
    "build_robots_denied_result",
    "build_source_metadata",
    "build_success_result",
    "execute_configured_extractor",
    "execute_configured_sources_extractor",
    "execute_extractor_call",
    "execute_loaded_extractor",
    "load_extractor_configuration",
    "normalize_extracted_articles",
    "normalize_extractor_result",
    "normalize_source_configuration",
    "run_extractor"
]