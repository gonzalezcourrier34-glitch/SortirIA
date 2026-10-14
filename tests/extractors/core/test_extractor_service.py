"""Tests de la façade publique du moteur d'extraction.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/core/test_extractor_service.py` (commit 93632c5).
"""

from __future__ import annotations

import pytest

import src.extractors.core.extractor_service as module
from src.extractors.core import extractor_configuration
from src.extractors.core import extractor_executor
from src.extractors.core import extractor_models
from src.extractors.core import extractor_results
from src.extractors.core import extractor_runner


# Exports publics

EXPECTED_EXPORTS = [
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


def test_all_contains_expected_exports() -> None:
    assert module.__all__ == EXPECTED_EXPORTS


def test_all_contains_no_duplicates() -> None:
    assert len(module.__all__) == len(set(module.__all__))


@pytest.mark.parametrize(
    "export_name",
    EXPECTED_EXPORTS
)
def test_all_exports_are_available(
    export_name: str
) -> None:
    assert hasattr(module, export_name)


@pytest.mark.parametrize(
    "export_name",
    EXPECTED_EXPORTS
)
def test_all_exports_are_not_none(
    export_name: str
) -> None:
    assert getattr(module, export_name) is not None


# Modèles

@pytest.mark.parametrize(
    ("export_name", "expected"),
    [
        (
            "ConfigurationType",
            extractor_models.ConfigurationType
        ),
        (
            "ExtractorConfiguration",
            extractor_models.ExtractorConfiguration
        ),
        (
            "ExtractorExecution",
            extractor_models.ExtractorExecution
        ),
        (
            "SourceCollection",
            extractor_models.SourceCollection
        ),
        (
            "SourceCollectionLoader",
            extractor_models.SourceCollectionLoader
        ),
        (
            "SourceConfiguration",
            extractor_models.SourceConfiguration
        ),
        (
            "SourceLoader",
            extractor_models.SourceLoader
        )
    ]
)
def test_model_exports_reference_original_objects(
    export_name: str,
    expected: object
) -> None:
    assert getattr(module, export_name) is expected


# Types et résultats

@pytest.mark.parametrize(
    ("export_name", "expected"),
    [
        (
            "ArticleExtractor",
            extractor_results.ArticleExtractor
        ),
        (
            "ConfiguredExtractor",
            extractor_results.ConfiguredExtractor
        ),
        (
            "ConfiguredSourcesExtractor",
            extractor_results.ConfiguredSourcesExtractor
        ),
        (
            "ExtractorResult",
            extractor_results.ExtractorResult
        ),
        (
            "RawExtractorResult",
            extractor_results.RawExtractorResult
        )
    ]
)
def test_result_type_exports_reference_original_objects(
    export_name: str,
    expected: object
) -> None:
    assert getattr(module, export_name) is expected


@pytest.mark.parametrize(
    ("export_name", "expected"),
    [
        (
            "build_disabled_result",
            extractor_results.build_disabled_result
        ),
        (
            "build_empty_result",
            extractor_results.build_empty_result
        ),
        (
            "build_failed_result",
            extractor_results.build_failed_result
        ),
        (
            "build_partial_result",
            extractor_results.build_partial_result
        ),
        (
            "build_robots_denied_result",
            extractor_results.build_robots_denied_result
        ),
        (
            "build_success_result",
            extractor_results.build_success_result
        ),
        (
            "normalize_extracted_articles",
            extractor_results.normalize_extracted_articles
        ),
        (
            "normalize_extractor_result",
            extractor_results.normalize_extractor_result
        )
    ]
)
def test_result_function_exports_reference_original_functions(
    export_name: str,
    expected: object
) -> None:
    assert getattr(module, export_name) is expected


# Configuration

@pytest.mark.parametrize(
    ("export_name", "expected"),
    [
        (
            "build_source_metadata",
            extractor_configuration.build_source_metadata
        ),
        (
            "load_extractor_configuration",
            extractor_configuration.load_extractor_configuration
        ),
        (
            "normalize_source_configuration",
            extractor_configuration.normalize_source_configuration
        )
    ]
)
def test_configuration_exports_reference_original_functions(
    export_name: str,
    expected: object
) -> None:
    assert getattr(module, export_name) is expected


# Exécution

@pytest.mark.parametrize(
    ("export_name", "expected"),
    [
        (
            "execute_configured_extractor",
            extractor_executor.execute_configured_extractor
        ),
        (
            "execute_configured_sources_extractor",
            extractor_executor.execute_configured_sources_extractor
        ),
        (
            "execute_extractor_call",
            extractor_executor.execute_extractor_call
        ),
        (
            "execute_loaded_extractor",
            extractor_executor.execute_loaded_extractor
        )
    ]
)
def test_executor_exports_reference_original_functions(
    export_name: str,
    expected: object
) -> None:
    assert getattr(module, export_name) is expected


def test_run_extractor_references_runner_function() -> None:
    assert module.run_extractor is extractor_runner.run_extractor


# Imports historiques

def test_historical_imports_are_supported() -> None:
    from src.extractors.core.extractor_service import (
        ArticleExtractor,
        ConfigurationType,
        ConfiguredExtractor,
        ConfiguredSourcesExtractor,
        ExtractorConfiguration,
        ExtractorExecution,
        ExtractorResult,
        RawExtractorResult,
        SourceCollection,
        SourceCollectionLoader,
        SourceConfiguration,
        SourceLoader,
        build_disabled_result,
        build_empty_result,
        build_failed_result,
        build_partial_result,
        build_robots_denied_result,
        build_source_metadata,
        build_success_result,
        execute_configured_extractor,
        execute_configured_sources_extractor,
        execute_extractor_call,
        execute_loaded_extractor,
        load_extractor_configuration,
        normalize_extracted_articles,
        normalize_extractor_result,
        normalize_source_configuration,
        run_extractor
    )

    assert ArticleExtractor is extractor_results.ArticleExtractor
    assert ConfigurationType is extractor_models.ConfigurationType
    assert ConfiguredExtractor is extractor_results.ConfiguredExtractor
    assert (
        ConfiguredSourcesExtractor
        is extractor_results.ConfiguredSourcesExtractor
    )
    assert ExtractorConfiguration is extractor_models.ExtractorConfiguration
    assert ExtractorExecution is extractor_models.ExtractorExecution
    assert ExtractorResult is extractor_results.ExtractorResult
    assert RawExtractorResult is extractor_results.RawExtractorResult
    assert SourceCollection is extractor_models.SourceCollection
    assert SourceCollectionLoader is extractor_models.SourceCollectionLoader
    assert SourceConfiguration is extractor_models.SourceConfiguration
    assert SourceLoader is extractor_models.SourceLoader
    assert (
        build_disabled_result
        is extractor_results.build_disabled_result
    )
    assert build_empty_result is extractor_results.build_empty_result
    assert build_failed_result is extractor_results.build_failed_result
    assert build_partial_result is extractor_results.build_partial_result
    assert (
        build_robots_denied_result
        is extractor_results.build_robots_denied_result
    )
    assert build_source_metadata is extractor_configuration.build_source_metadata
    assert build_success_result is extractor_results.build_success_result
    assert (
        execute_configured_extractor
        is extractor_executor.execute_configured_extractor
    )
    assert (
        execute_configured_sources_extractor
        is extractor_executor.execute_configured_sources_extractor
    )
    assert execute_extractor_call is extractor_executor.execute_extractor_call
    assert execute_loaded_extractor is extractor_executor.execute_loaded_extractor
    assert (
        load_extractor_configuration
        is extractor_configuration.load_extractor_configuration
    )
    assert (
        normalize_extracted_articles
        is extractor_results.normalize_extracted_articles
    )
    assert (
        normalize_extractor_result
        is extractor_results.normalize_extractor_result
    )
    assert (
        normalize_source_configuration
        is extractor_configuration.normalize_source_configuration
    )
    assert run_extractor is extractor_runner.run_extractor


def test_star_import_exposes_only_public_api() -> None:
    namespace: dict[str, object] = {}

    exec(
        "from src.extractors.core.extractor_service import *",
        {},
        namespace
    )

    assert set(namespace) == set(EXPECTED_EXPORTS)


def test_star_import_values_match_module_exports() -> None:
    namespace: dict[str, object] = {}

    exec(
        "from src.extractors.core.extractor_service import *",
        {},
        namespace
    )

    for export_name in EXPECTED_EXPORTS:
        assert namespace[export_name] is getattr(module, export_name)


# Utilisation depuis la façade

def test_can_build_success_result_from_public_facade() -> None:
    result = module.build_success_result(
        name="NewsAPI",
        source_type="api",
        articles=[
            {
                "id": "article-1",
                "title": "Article"
            }
        ]
    )

    assert isinstance(result, module.ExtractorResult)
    assert result.name == "NewsAPI"
    assert result.source_type == "api"
    assert result.status == "success"
    assert result.extracted_count == 1


def test_can_build_failed_result_from_public_facade() -> None:
    result = module.build_failed_result(
        name="NewsAPI",
        source_type="api",
        message="Erreur réseau"
    )

    assert isinstance(result, module.ExtractorResult)
    assert result.status == "failed"
    assert result.message == "Erreur réseau"
    assert result.errors == [
        "Erreur réseau"
    ]


def test_can_build_disabled_result_from_public_facade() -> None:
    result = module.build_disabled_result(
        name="NewsAPI",
        source_type="api"
    )

    assert isinstance(result, module.ExtractorResult)
    assert result.status == "disabled"


def test_can_normalize_articles_from_public_facade() -> None:
    articles, rejected_count = module.normalize_extracted_articles(
        [
            {
                "id": "article-1"
            },
            None
        ],
        extractor_name="NewsAPI"
    )

    assert articles == [
        {
            "id": "article-1"
        }
    ]
    assert rejected_count == 1