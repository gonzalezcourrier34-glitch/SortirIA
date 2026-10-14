"""Tests du moteur commun d'exécution des extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/core/test_extractor_executor.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import src.extractors.core.extractor_executor as module

# Objets de test

class FakeResult:
    """Résultat minimal permettant de tester le moteur."""

    def __init__(
        self,
        *,
        analyzed_count: int = 0,
        extracted_count: int = 0,
        rejected_count: int = 0
    ) -> None:
        self.duration_seconds = 0.0
        self.analyzed_count = analyzed_count
        self.extracted_count = extracted_count
        self.rejected_count = rejected_count
        self.metadata: dict[str, Any] = {}
        self.rejection_reasons: dict[str, int] = {}

    def merge_metadata(
        self,
        metadata: Mapping[str, Any]
    ) -> None:
        """Fusionne des métadonnées."""

        self.metadata.update(metadata)

    def merge_rejection_reasons(
        self,
        reasons: Mapping[str, int]
    ) -> None:
        """Fusionne les motifs de rejet et actualise les compteurs."""

        for reason, count in reasons.items():
            self.rejection_reasons[reason] = (
                self.rejection_reasons.get(reason, 0) + count
            )

        self.rejected_count = max(
            self.rejected_count,
            sum(self.rejection_reasons.values())
        )
        self.analyzed_count = max(
            self.analyzed_count,
            self.extracted_count + self.rejected_count
        )

class FakeApiError(Exception):
    """Erreur API minimale."""

    def __init__(
        self,
        source_name: str = "API test",
        reason: str = "erreur",
        status_code: int | None = None
    ) -> None:
        super().__init__(reason)
        self.source_name = source_name
        self.reason = reason
        self.status_code = status_code


class FakeApiAuthenticationError(FakeApiError):
    """Erreur d'authentification."""


class FakeApiQuotaExceededError(FakeApiError):
    """Erreur de quota."""


class FakeApiRateLimitError(FakeApiError):
    """Erreur de limitation."""


class FakeApiInvalidParametersError(FakeApiError):
    """Erreur de paramètres."""


class FakeApiMaintenanceError(FakeApiError):
    """Erreur de maintenance."""


class FakeApiExtractionStoppedError(FakeApiError):
    """Erreur d'arrêt contrôlé."""


class FakeSourceRegistry:
    """Registre minimal pour les tests."""

    def __init__(
        self,
        sources_by_type: Mapping[str, list[Any]]
    ) -> None:
        self.sources_by_type = dict(sources_by_type)

    def by_type(
        self,
        source_type: str
    ) -> list[Any]:
        """Retourne les sources du type demandé."""

        return self.sources_by_type.get(source_type, [])


# Helpers

def patch_api_error_types(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remplace les erreurs API par des classes locales."""

    monkeypatch.setattr(
        module,
        "get_api_error_types",
        lambda: (
            FakeApiAuthenticationError,
            FakeApiQuotaExceededError,
            FakeApiRateLimitError,
            FakeApiInvalidParametersError,
            FakeApiMaintenanceError,
            FakeApiExtractionStoppedError
        )
    )


def patch_failed_result(
    monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Capture les arguments transmis à build_failed_result."""

    captured: dict[str, Any] = {}

    def fake_build_failed_result(
        **kwargs: Any
    ) -> FakeResult:
        captured.update(kwargs)
        return FakeResult(
            analyzed_count=kwargs.get("analyzed_count", 0),
            rejected_count=kwargs.get("rejected_count", 0)
        )

    monkeypatch.setattr(
        module,
        "build_failed_result",
        fake_build_failed_result
    )
    return captured


# Métadonnées d'échec

@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("Quota_Exceeded", "quota_exceeded"),
        ("  RATE_LIMITED  ", "rate_limited"),
        ("", "unknown"),
        ("   ", "unknown"),
        (None, "unknown")
    ]
)
def test_build_failure_metadata_normalizes_reason(
    reason: Any,
    expected: str
) -> None:
    assert module.build_failure_metadata(reason) == {
        "failure_reason": expected
    }


def test_build_failure_metadata_adds_error_type() -> None:
    error = RuntimeError("boom")

    assert module.build_failure_metadata(
        "unexpected_error",
        error
    ) == {
        "failure_reason": "unexpected_error",
        "error_type": "RuntimeError"
    }


def test_build_failure_metadata_merges_extra_metadata() -> None:
    error = RuntimeError("boom")

    assert module.build_failure_metadata(
        "unexpected_error",
        error,
        source_name="NewsAPI",
        status_code=500
    ) == {
        "failure_reason": "unexpected_error",
        "error_type": "RuntimeError",
        "source_name": "NewsAPI",
        "status_code": 500
    }


def test_build_failure_metadata_extra_values_can_override_defaults() -> None:
    assert module.build_failure_metadata(
        "quota_exceeded",
        failure_reason="custom"
    ) == {
        "failure_reason": "custom"
    }


# Compteurs d'échec

@pytest.mark.parametrize(
    ("reason", "count", "expected"),
    [
        ("Quota_Exceeded", 2, {"quota_exceeded": 2}),
        ("  RATE_LIMITED ", 1, {"rate_limited": 1}),
        ("", 1, {"unknown": 1}),
        (None, 1, {"unknown": 1}),
        ("error", -5, {"error": 0}),
        ("error", 0, {})
    ]
)
def test_build_failure_reasons(
    reason: Any,
    count: int,
    expected: dict[str, int]
) -> None:
    assert module.build_failure_reasons(
        reason,
        count
    ) == expected


# Conversion des erreurs d'extraction

@pytest.mark.parametrize(
    (
        "error_class",
        "reason_key",
        "message_prefix"
    ),
    [
        (
            FakeApiQuotaExceededError,
            "quota_exceeded",
            "Quota dépassé"
        ),
        (
            FakeApiAuthenticationError,
            "authentication_failed",
            "Authentification refusée"
        ),
        (
            FakeApiRateLimitError,
            "rate_limited",
            "Limitation de fréquence"
        ),
        (
            FakeApiInvalidParametersError,
            "invalid_parameters",
            "Paramètres API invalides"
        ),
        (
            FakeApiMaintenanceError,
            "maintenance",
            "API temporairement indisponible"
        ),
        (
            FakeApiExtractionStoppedError,
            "api_extraction_stopped",
            "Extraction API arrêtée"
        )
    ]
)
def test_build_extraction_error_result_handles_api_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_class: type[FakeApiError],
    reason_key: str,
    message_prefix: str
) -> None:
    patch_api_error_types(monkeypatch)
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: 10.5
    )

    error = error_class(
        source_name="NewsAPI",
        reason="erreur métier",
        status_code=429
    )

    result = module.build_extraction_error_result(
        extractor_name="NewsAPI",
        source_type="api",
        error=error,
        started_at=10.0
    )

    assert isinstance(result, FakeResult)
    assert captured["name"] == "NewsAPI"
    assert captured["source_type"] == "api"
    assert captured["message"] == (
        f"{message_prefix} : erreur métier"
    )
    assert captured["duration_seconds"] == 0.5
    assert captured["analyzed_count"] == 1
    assert captured["rejected_count"] == 1
    assert captured["rejection_reasons"] == {
        reason_key: 1
    }
    assert captured["metadata"] == {
        "failure_reason": reason_key,
        "error_type": error_class.__name__,
        "source_name": "NewsAPI",
        "status_code": 429
    }


def test_build_extraction_error_result_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_api_error_types(monkeypatch)
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: 5.25
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args, **kwargs: None
    )

    error = RuntimeError("erreur inattendue")

    result = module.build_extraction_error_result(
        extractor_name="RSS",
        source_type="rss",
        error=error,
        started_at=5.0
    )

    assert isinstance(result, FakeResult)
    assert captured == {
        "name": "RSS",
        "source_type": "rss",
        "message": "erreur inattendue",
        "duration_seconds": 0.25,
        "analyzed_count": 1,
        "rejected_count": 1,
        "rejection_reasons": {
            "unexpected_error": 1
        },
        "metadata": {
            "failure_reason": "unexpected_error",
            "error_type": "RuntimeError"
        }
    }


def test_build_extraction_error_result_uses_error_type_when_message_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_api_error_types(monkeypatch)
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: 1.0
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args, **kwargs: None
    )

    error = RuntimeError()

    module.build_extraction_error_result(
        extractor_name="RSS",
        source_type="rss",
        error=error,
        started_at=1.0
    )

    assert captured["message"] == "RuntimeError"


# Exécution simple

def test_execute_extractor_call_rejects_non_callable(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = patch_failed_result(monkeypatch)

    result = module.execute_extractor_call(
        extractor_name="NewsAPI",
        source_type="api",
        extraction_function=None
    )

    assert isinstance(result, FakeResult)
    assert captured == {
        "name": "NewsAPI",
        "source_type": "api",
        "message": "La fonction d'extraction doit être appelable.",
        "analyzed_count": 1,
        "rejected_count": 1,
        "rejection_reasons": {
            "invalid_extraction_function": 1
        },
        "metadata": {
            "failure_reason": "invalid_extraction_function"
        }
    }


def test_execute_extractor_call_normalizes_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FakeResult()
    captured: dict[str, Any] = {}
    times = iter([10.0, 10.456])

    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: next(times)
    )

    def fake_normalize(
        raw_result: Any,
        extractor_name: str,
        source_type: str
    ) -> FakeResult:
        captured.update({
            "raw_result": raw_result,
            "extractor_name": extractor_name,
            "source_type": source_type
        })
        return expected

    monkeypatch.setattr(
        module,
        "normalize_extractor_result",
        fake_normalize
    )

    result = module.execute_extractor_call(
        extractor_name="NewsAPI",
        source_type="api",
        extraction_function=lambda: [
            {
                "title": "Article"
            }
        ]
    )

    assert result is expected
    assert result.duration_seconds == 0.456
    assert captured == {
        "raw_result": [
            {
                "title": "Article"
            }
        ],
        "extractor_name": "NewsAPI",
        "source_type": "api"
    }


def test_execute_extractor_call_converts_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FakeResult()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: 10.0
    )

    def fake_error_result(
        extractor_name: str,
        source_type: str,
        error: Exception,
        started_at: float
    ) -> FakeResult:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "error": error,
            "started_at": started_at
        })
        return expected

    monkeypatch.setattr(
        module,
        "build_extraction_error_result",
        fake_error_result
    )

    error = RuntimeError("boom")

    def extraction_function() -> Any:
        raise error

    result = module.execute_extractor_call(
        extractor_name="NewsAPI",
        source_type="api",
        extraction_function=extraction_function
    )

    assert result is expected
    assert captured == {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "error": error,
        "started_at": 10.0
    }


def test_execute_loaded_extractor_rejects_non_callable(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = patch_failed_result(monkeypatch)

    result = module.execute_loaded_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        configuration={
            "source_id": "newsapi"
        },
        extraction_function=None
    )

    assert isinstance(result, FakeResult)
    assert captured["rejection_reasons"] == {
        "invalid_extraction_function": 1
    }


def test_execute_loaded_extractor_passes_configuration(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FakeResult()
    captured: dict[str, Any] = {}
    configuration = {
        "source_id": "newsapi"
    }

    def fake_execute(
        extractor_name: str,
        source_type: str,
        extraction_function: Any
    ) -> FakeResult:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "value": extraction_function()
        })
        return expected

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        fake_execute
    )

    result = module.execute_loaded_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        configuration=configuration,
        extraction_function=lambda source: {
            "received": source
        }
    )

    assert result is expected
    assert captured == {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "value": {
            "received": configuration
        }
    }


# Source unique

def test_execute_configured_extractor_returns_loading_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    loading_error = FakeResult()

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            None,
            loading_error
        )
    )

    result = module.execute_configured_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        source_loader=lambda: {},
        extraction_function=lambda source: []
    )

    assert result is loading_error


def test_execute_configured_extractor_handles_missing_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            None,
            None
        )
    )

    module.execute_configured_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        source_loader=lambda: {},
        extraction_function=lambda source: []
    )

    assert captured["message"] == "Configuration indisponible."
    assert captured["rejection_reasons"] == {
        "configuration_unavailable": 1
    }


def test_execute_configured_extractor_handles_invalid_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            object(),
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_configuration",
        lambda source, extractor_name: None
    )

    module.execute_configured_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        source_loader=lambda: {},
        extraction_function=lambda source: []
    )

    assert captured["message"] == "Configuration invalide ou vide."
    assert captured["rejection_reasons"] == {
        "invalid_configuration": 1
    }


def test_execute_configured_extractor_returns_disabled_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FakeResult()
    captured: dict[str, Any] = {}
    source = {
        "source_id": "newsapi",
        "enabled": False
    }

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            source,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_configuration",
        lambda value, name: dict(value)
    )
    monkeypatch.setattr(
        module,
        "build_source_metadata",
        lambda value, source_type: {
            "source_id": "newsapi",
            "source_type": "api"
        }
    )

    def fake_disabled_result(
        **kwargs: Any
    ) -> FakeResult:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        module,
        "build_disabled_result",
        fake_disabled_result
    )

    result = module.execute_configured_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        source_loader=lambda: source,
        extraction_function=lambda current_source: []
    )

    assert result is expected
    assert captured == {
        "name": "NewsAPI",
        "source_type": "api",
        "metadata": {
            "source_id": "newsapi",
            "source_type": "api"
        }
    }


def test_execute_configured_extractor_executes_and_merges_metadata(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = FakeResult()
    source = {
        "source_id": "newsapi",
        "enabled": True
    }

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            source,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_configuration",
        lambda value, name: dict(value)
    )
    monkeypatch.setattr(
        module,
        "build_source_metadata",
        lambda value, source_type: {
            "source_id": "newsapi",
            "source_type": "api"
        }
    )
    monkeypatch.setattr(
        module,
        "execute_loaded_extractor",
        lambda *args: result
    )

    returned = module.execute_configured_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        source_loader=lambda: source,
        extraction_function=lambda current_source: []
    )

    assert returned is result
    assert result.metadata == {
        "source_id": "newsapi",
        "source_type": "api"
    }


# Normalisation des collections

def test_normalize_source_collection_reads_registry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "SourceRegistry",
        FakeSourceRegistry
    )

    registry = FakeSourceRegistry({
        "api": [
            {
                "source_id": "newsapi"
            },
            {
                "source_id": "gnews"
            }
        ]
    })

    assert module.normalize_source_collection(
        registry,
        "api"
    ) == [
        {
            "source_id": "newsapi"
        },
        {
            "source_id": "gnews"
        }
    ]


@pytest.mark.parametrize(
    "sources",
    [
        [
            {
                "source_id": "newsapi"
            }
        ],
        (
            {
                "source_id": "gnews"
            },
        )
    ]
)
def test_normalize_source_collection_accepts_sequence(
    sources: Any
) -> None:
    assert module.normalize_source_collection(
        sources,
        "api"
    ) == list(sources)


@pytest.mark.parametrize(
    "sources",
    [
        "newsapi",
        b"newsapi",
        bytearray(b"newsapi"),
        42,
        None
    ]
)
def test_normalize_source_collection_rejects_invalid_value(
    sources: Any
) -> None:
    assert module.normalize_source_collection(
        sources,
        "api"
    ) is None


def test_normalize_configured_sources_counts_ignored_items(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = [
        {
            "source_id": "newsapi"
        },
        None,
        {
            "source_id": "gnews"
        },
        "invalid"
    ]

    monkeypatch.setattr(
        module,
        "normalize_source_configuration",
        lambda source, extractor_name: (
            dict(source)
            if isinstance(source, Mapping)
            else None
        )
    )

    normalized, ignored = module.normalize_configured_sources(
        collection,
        "APIs"
    )

    assert normalized == [
        {
            "source_id": "newsapi"
        },
        {
            "source_id": "gnews"
        }
    ]
    assert ignored == 2


def test_normalize_configured_sources_returns_empty_list() -> None:
    normalized, ignored = module.normalize_configured_sources(
        [],
        "APIs"
    )

    assert normalized == []
    assert ignored == 0


# Métadonnées de collection

def test_build_collection_metadata() -> None:
    source_collection = [
        {
            "source_id": "newsapi"
        },
        {
            "source_id": "gnews"
        },
        {
            "source_id": "currents"
        },
        "invalid"
    ]
    normalized_sources = [
        {
            "source_id": "newsapi",
            "enabled": True
        },
        {
            "id": "gnews"
        },
        {
            "source_id": "currents",
            "enabled": False
        }
    ]

    assert module.build_collection_metadata(
        source_collection,
        normalized_sources,
        ignored_count=1
    ) == {
        "configured_sources_count": 4,
        "valid_sources_count": 3,
        "ignored_sources_count": 1,
        "enabled_sources_count": 2,
        "source_ids": [
            "newsapi",
            "gnews"
        ]
    }


def test_build_collection_metadata_ignores_missing_ids() -> None:
    assert module.build_collection_metadata(
        source_collection=[
            {}
        ],
        normalized_sources=[
            {
                "enabled": True
            }
        ],
        ignored_count=0
    ) == {
        "configured_sources_count": 1,
        "valid_sources_count": 1,
        "ignored_sources_count": 0,
        "enabled_sources_count": 1,
        "source_ids": []
    }


# Collection de sources

def test_execute_configured_sources_returns_loading_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    loading_error = FakeResult()

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            None,
            loading_error
        )
    )

    result = module.execute_configured_sources_extractor(
        extractor_name="RSS",
        source_type="rss",
        sources_loader=lambda: [],
        extraction_function=lambda sources: []
    )

    assert result is loading_error


def test_execute_configured_sources_handles_missing_sources(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            None,
            None
        )
    )

    module.execute_configured_sources_extractor(
        extractor_name="RSS",
        source_type="rss",
        sources_loader=lambda: [],
        extraction_function=lambda sources: []
    )

    assert captured["message"] == "Configurations indisponibles."
    assert captured["rejection_reasons"] == {
        "configurations_unavailable": 1
    }


def test_execute_configured_sources_handles_invalid_collection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = patch_failed_result(monkeypatch)

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            object(),
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_collection",
        lambda sources, source_type: None
    )

    module.execute_configured_sources_extractor(
        extractor_name="RSS",
        source_type="rss",
        sources_loader=lambda: object(),
        extraction_function=lambda sources: []
    )

    assert captured["message"] == "Configurations invalides."
    assert captured["rejection_reasons"] == {
        "invalid_configurations": 1
    }


def test_execute_configured_sources_returns_empty_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FakeResult()
    captured: dict[str, Any] = {}
    collection = [
        None,
        "invalid"
    ]

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            collection,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_collection",
        lambda sources, source_type: collection
    )
    monkeypatch.setattr(
        module,
        "normalize_configured_sources",
        lambda sources, extractor_name: (
            [],
            2
        )
    )
    monkeypatch.setattr(
        module,
        "build_collection_metadata",
        lambda *args: {
            "configured_sources_count": 2,
            "valid_sources_count": 0,
            "ignored_sources_count": 2,
            "enabled_sources_count": 0,
            "source_ids": []
        }
    )

    def fake_empty_result(
        **kwargs: Any
    ) -> FakeResult:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        module,
        "build_empty_result",
        fake_empty_result
    )

    result = module.execute_configured_sources_extractor(
        extractor_name="RSS",
        source_type="rss",
        sources_loader=lambda: collection,
        extraction_function=lambda sources: []
    )

    assert result is expected
    assert captured == {
        "name": "RSS",
        "source_type": "rss",
        "message": "Aucune source disponible.",
        "analyzed_count": 2,
        "rejected_count": 2,
        "rejection_reasons": {
            "invalid_configuration": 2
        },
        "metadata": {
            "configured_sources_count": 2,
            "valid_sources_count": 0,
            "ignored_sources_count": 2,
            "enabled_sources_count": 0,
            "source_ids": []
        }
    }


def test_execute_configured_sources_returns_disabled_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = FakeResult()
    captured: dict[str, Any] = {}
    collection = [
        {
            "source_id": "newsapi",
            "enabled": False
        }
    ]

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            collection,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_collection",
        lambda sources, source_type: collection
    )
    monkeypatch.setattr(
        module,
        "normalize_configured_sources",
        lambda sources, extractor_name: (
            collection,
            0
        )
    )
    monkeypatch.setattr(
        module,
        "build_collection_metadata",
        lambda *args: {
            "configured_sources_count": 1,
            "valid_sources_count": 1,
            "ignored_sources_count": 0,
            "enabled_sources_count": 0,
            "source_ids": []
        }
    )

    def fake_disabled_result(
        **kwargs: Any
    ) -> FakeResult:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        module,
        "build_disabled_result",
        fake_disabled_result
    )

    result = module.execute_configured_sources_extractor(
        extractor_name="APIs",
        source_type="api",
        sources_loader=lambda: collection,
        extraction_function=lambda sources: []
    )

    assert result is expected
    assert captured["message"] == (
        "Toutes les sources sont désactivées."
    )
    assert captured["metadata"]["enabled_sources_count"] == 0


def test_execute_configured_sources_executes_enabled_sources_only(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = FakeResult()
    captured: dict[str, Any] = {}
    collection = [
        {
            "source_id": "newsapi",
            "enabled": True
        },
        {
            "source_id": "gnews",
            "enabled": False
        },
        {
            "source_id": "currents"
        }
    ]

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            collection,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_collection",
        lambda sources, source_type: collection
    )
    monkeypatch.setattr(
        module,
        "normalize_configured_sources",
        lambda sources, extractor_name: (
            collection,
            0
        )
    )
    monkeypatch.setattr(
        module,
        "build_collection_metadata",
        lambda *args: {
            "configured_sources_count": 3,
            "valid_sources_count": 3,
            "ignored_sources_count": 0,
            "enabled_sources_count": 2,
            "source_ids": [
                "newsapi",
                "currents"
            ]
        }
    )

    def fake_execute(
        extractor_name: str,
        source_type: str,
        configuration: Any,
        extraction_function: Any
    ) -> FakeResult:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "configuration": configuration,
            "extraction_function": extraction_function
        })
        return result

    monkeypatch.setattr(
        module,
        "execute_loaded_extractor",
        fake_execute
    )

    extraction_function = lambda sources: []

    returned = module.execute_configured_sources_extractor(
        extractor_name="APIs",
        source_type="api",
        sources_loader=lambda: collection,
        extraction_function=extraction_function
    )

    assert returned is result
    assert captured["configuration"] == [
        {
            "source_id": "newsapi",
            "enabled": True
        },
        {
            "source_id": "currents"
        }
    ]
    assert captured["extraction_function"] is extraction_function
    assert result.metadata == {
        "configured_sources_count": 3,
        "valid_sources_count": 3,
        "ignored_sources_count": 0,
        "enabled_sources_count": 2,
        "source_ids": [
            "newsapi",
            "currents"
        ]
    }


def test_execute_configured_sources_merges_ignored_sources(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = FakeResult(
        analyzed_count=1,
        extracted_count=1,
        rejected_count=0
    )
    collection = [
        {
            "source_id": "newsapi"
        },
        None,
        "invalid"
    ]
    normalized_sources = [
        {
            "source_id": "newsapi"
        }
    ]

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            collection,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_collection",
        lambda sources, source_type: collection
    )
    monkeypatch.setattr(
        module,
        "normalize_configured_sources",
        lambda sources, extractor_name: (
            normalized_sources,
            2
        )
    )
    monkeypatch.setattr(
        module,
        "build_collection_metadata",
        lambda *args: {
            "configured_sources_count": 3,
            "valid_sources_count": 1,
            "ignored_sources_count": 2,
            "enabled_sources_count": 1,
            "source_ids": [
                "newsapi"
            ]
        }
    )
    monkeypatch.setattr(
        module,
        "execute_loaded_extractor",
        lambda *args: result
    )

    returned = module.execute_configured_sources_extractor(
        extractor_name="APIs",
        source_type="api",
        sources_loader=lambda: collection,
        extraction_function=lambda sources: []
    )

    assert returned is result
    assert result.rejection_reasons == {
        "invalid_configuration": 2
    }
    assert result.analyzed_count == 3
    assert result.metadata == {
        "configured_sources_count": 3,
        "valid_sources_count": 1,
        "ignored_sources_count": 2,
        "enabled_sources_count": 1,
        "source_ids": [
            "newsapi"
        ]
    }


def test_execute_configured_sources_preserves_higher_analyzed_count(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = FakeResult(
        analyzed_count=10,
        extracted_count=1,
        rejected_count=0
    )
    collection = [
        {
            "source_id": "newsapi"
        },
        None
    ]

    monkeypatch.setattr(
        module,
        "load_extractor_configuration",
        lambda *args: (
            collection,
            None
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_collection",
        lambda sources, source_type: collection
    )
    monkeypatch.setattr(
        module,
        "normalize_configured_sources",
        lambda sources, extractor_name: (
            [
                {
                    "source_id": "newsapi"
                }
            ],
            1
        )
    )
    monkeypatch.setattr(
        module,
        "build_collection_metadata",
        lambda *args: {}
    )
    monkeypatch.setattr(
        module,
        "execute_loaded_extractor",
        lambda *args: result
    )

    module.execute_configured_sources_extractor(
        extractor_name="APIs",
        source_type="api",
        sources_loader=lambda: collection,
        extraction_function=lambda sources: []
    )

    assert result.analyzed_count == 10
    assert result.rejection_reasons == {
        "invalid_configuration": 1
    }