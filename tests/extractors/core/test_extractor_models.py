"""Tests des modèles communs utilisés par les extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/core/test_extractor_models.py` (commit 93632c5).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

import src.extractors.core.extractor_models as module
from config.constants import (
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS
)


# ExtractorConfiguration

def test_extractor_configuration_normalizes_fields() -> None:
    configuration = module.ExtractorConfiguration(
        name="  NewsAPI  ",
        source_id="  newsapi  ",
        source_type="  API  ",
        language="  FR  ",
        country="  fr  ",
        category="  technology  ",
        role="  acquisition  "
    )

    assert configuration.name == "NewsAPI"
    assert configuration.source_id == "newsapi"
    assert configuration.source_type == "api"
    assert configuration.language == "FR"
    assert configuration.country == "fr"
    assert configuration.category == "technology"
    assert configuration.role == "acquisition"


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        (
            "name",
            "",
            "Le nom de la source ne peut pas être vide"
        ),
        (
            "name",
            "   ",
            "Le nom de la source ne peut pas être vide"
        ),
        (
            "source_id",
            "",
            "L'identifiant de la source ne peut pas être vide"
        ),
        (
            "source_id",
            "   ",
            "L'identifiant de la source ne peut pas être vide"
        )
    ]
)
def test_extractor_configuration_rejects_empty_required_text(
    field_name: str,
    value: str,
    message: str
) -> None:
    arguments = {
        "name": "NewsAPI",
        "source_id": "newsapi",
        "source_type": "api"
    }
    arguments[field_name] = value

    with pytest.raises(
        ValueError,
        match=message
    ):
        module.ExtractorConfiguration(**arguments)


@pytest.mark.parametrize(
    "source_type",
    [
        "",
        "unknown",
        " API_UNKNOWN "
    ]
)
def test_extractor_configuration_rejects_invalid_source_type(
    source_type: str
) -> None:
    with pytest.raises(
        ValueError,
        match="Type de source invalide"
    ):
        module.ExtractorConfiguration(
            name="Source",
            source_id="source",
            source_type=source_type
        )


@pytest.mark.parametrize(
    "enabled",
    [
        1,
        0,
        "true",
        None
    ]
)
def test_extractor_configuration_rejects_non_boolean_enabled(
    enabled: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="enabled doit être un booléen"
    ):
        module.ExtractorConfiguration(
            name="Source",
            source_id="source",
            source_type="api",
            enabled=enabled
        )


@pytest.mark.parametrize(
    "max_articles",
    [
        True,
        False,
        1.5,
        "10"
    ]
)
def test_extractor_configuration_rejects_invalid_max_articles_type(
    max_articles: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="max_articles doit être un entier ou None"
    ):
        module.ExtractorConfiguration(
            name="Source",
            source_id="source",
            source_type="api",
            max_articles=max_articles
        )


@pytest.mark.parametrize(
    "max_articles",
    [
        0,
        -1,
        -100
    ]
)
def test_extractor_configuration_rejects_non_positive_max_articles(
    max_articles: int
) -> None:
    with pytest.raises(
        ValueError,
        match="max_articles doit être strictement positif"
    ):
        module.ExtractorConfiguration(
            name="Source",
            source_id="source",
            source_type="api",
            max_articles=max_articles
        )


def test_extractor_configuration_accepts_positive_max_articles() -> None:
    configuration = module.ExtractorConfiguration(
        name="Source",
        source_id="source",
        source_type="api",
        max_articles=100
    )

    assert configuration.max_articles == 100


def test_extractor_configuration_accepts_none_max_articles() -> None:
    configuration = module.ExtractorConfiguration(
        name="Source",
        source_id="source",
        source_type="api",
        max_articles=None
    )

    assert configuration.max_articles is None


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        (
            "filters",
            [],
            "filters doit être un dictionnaire"
        ),
        (
            "filters",
            None,
            "filters doit être un dictionnaire"
        ),
        (
            "configuration",
            [],
            "configuration doit être un dictionnaire"
        ),
        (
            "configuration",
            None,
            "configuration doit être un dictionnaire"
        )
    ]
)
def test_extractor_configuration_rejects_invalid_dictionaries(
    field_name: str,
    value: Any,
    message: str
) -> None:
    arguments = {
        "name": "Source",
        "source_id": "source",
        "source_type": "api"
    }
    arguments[field_name] = value

    with pytest.raises(
        TypeError,
        match=message
    ):
        module.ExtractorConfiguration(**arguments)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  fr  ", "fr"),
        ("", None),
        ("   ", None),
        (None, None),
        (42, "42"),
        (False, None)
    ]
)
def test_normalize_optional_text(
    value: Any,
    expected: str | None
) -> None:
    assert (
        module.ExtractorConfiguration._normalize_optional_text(value)
        == expected
    )


def test_extractor_configuration_copies_dictionaries() -> None:
    filters = {
        "require_title": True
    }
    specific = {
        "endpoint": "https://example.com/api"
    }

    configuration = module.ExtractorConfiguration(
        name="Source",
        source_id="source",
        source_type="api",
        filters=filters,
        configuration=specific
    )

    assert configuration.filters == filters
    assert configuration.configuration == specific
    assert configuration.filters is not filters
    assert configuration.configuration is not specific

    filters["require_title"] = False
    specific["endpoint"] = "changed"

    assert configuration.filters == {
        "require_title": True
    }
    assert configuration.configuration == {
        "endpoint": "https://example.com/api"
    }


def test_extractor_configuration_is_enabled() -> None:
    enabled = module.ExtractorConfiguration(
        name="Enabled",
        source_id="enabled",
        source_type="api",
        enabled=True
    )
    disabled = module.ExtractorConfiguration(
        name="Disabled",
        source_id="disabled",
        source_type="api",
        enabled=False
    )

    assert enabled.is_enabled() is True
    assert disabled.is_enabled() is False


def test_extractor_configuration_to_dict_merges_specific_configuration() -> None:
    configuration = module.ExtractorConfiguration(
        name="NewsAPI",
        source_id="newsapi",
        source_type="api",
        language="fr",
        configuration={
            "endpoint": "https://example.com/api",
            "page_size": 100
        }
    )

    assert configuration.to_dict() == {
        "endpoint": "https://example.com/api",
        "page_size": 100,
        "name": "NewsAPI",
        "source_id": "newsapi",
        "source_type": "api",
        "enabled": True,
        "language": "fr",
        "country": None,
        "category": None,
        "role": None,
        "max_articles": None,
        "filters": {}
    }


def test_extractor_configuration_fields_override_specific_values() -> None:
    configuration = module.ExtractorConfiguration(
        name="NewsAPI",
        source_id="newsapi",
        source_type="api",
        configuration={
            "name": "Ancien nom",
            "source_id": "ancien_id",
            "enabled": False
        }
    )

    result = configuration.to_dict()

    assert result["name"] == "NewsAPI"
    assert result["source_id"] == "newsapi"
    assert result["enabled"] is True


def test_extractor_configuration_to_dict_is_independent() -> None:
    configuration = module.ExtractorConfiguration(
        name="NewsAPI",
        source_id="newsapi",
        source_type="api",
        filters={
            "require_title": True
        },
        configuration={
            "endpoint": "https://example.com/api"
        }
    )

    first = configuration.to_dict()
    second = configuration.to_dict()

    assert first == second
    assert first is not second
    assert first["filters"] is not configuration.filters

    first["filters"]["require_title"] = False

    assert configuration.filters == {
        "require_title": True
    }


def test_extractor_configuration_is_frozen() -> None:
    configuration = module.ExtractorConfiguration(
        name="NewsAPI",
        source_id="newsapi",
        source_type="api"
    )

    with pytest.raises(AttributeError):
        configuration.name = "Modified"


# ExtractorExecution

def build_execution(**overrides: Any) -> module.ExtractorExecution:
    """Construit un rapport d'exécution valide."""

    values = {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "status": EXTRACTOR_STATUS_SUCCESS,
        "article_count": 8,
        "duration_seconds": 1.25,
        "analyzed_count": 10,
        "rejected_count": 2,
        "requests_count": 3
    }
    values.update(overrides)
    return module.ExtractorExecution(**values)


def test_extractor_execution_normalizes_fields() -> None:
    execution = build_execution(
        extractor_name="  NewsAPI  ",
        source_type="  API  ",
        status=f"  {EXTRACTOR_STATUS_SUCCESS.upper()}  ",
        message="  Extraction terminée  ",
        main_reason="  RATE_LIMITED  ",
        errors=(
            "  erreur 1  ",
            "",
            "   ",
            None,
            "erreur 2"
        )
    )

    assert execution.extractor_name == "NewsAPI"
    assert execution.source_type == "api"
    assert execution.status == EXTRACTOR_STATUS_SUCCESS
    assert execution.message == "Extraction terminée"
    assert execution.main_reason == "rate_limited"
    assert execution.errors == (
        "erreur 1",
        "erreur 2"
    )


@pytest.mark.parametrize(
    "extractor_name",
    [
        "",
        "   ",
        None
    ]
)
def test_extractor_execution_rejects_empty_name(
    extractor_name: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="Le nom de l'extracteur ne peut pas être vide"
    ):
        build_execution(
            extractor_name=extractor_name
        )


@pytest.mark.parametrize(
    "source_type",
    [
        "",
        "unknown",
        None
    ]
)
def test_extractor_execution_rejects_invalid_source_type(
    source_type: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="Type de source invalide"
    ):
        build_execution(
            source_type=source_type
        )


@pytest.mark.parametrize(
    "status",
    [
        "",
        "unknown",
        None
    ]
)
def test_extractor_execution_rejects_invalid_status(
    status: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="Statut d'extracteur invalide"
    ):
        build_execution(
            status=status
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "article_count",
        "analyzed_count",
        "rejected_count",
        "requests_count"
    ]
)
@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        1.5,
        "1",
        None
    ]
)
def test_extractor_execution_rejects_invalid_counter_type(
    field_name: str,
    value: Any
) -> None:
    with pytest.raises(
        TypeError,
        match=f"{field_name} doit être un entier"
    ):
        build_execution(
            **{
                field_name: value
            }
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "article_count",
        "analyzed_count",
        "rejected_count",
        "requests_count"
    ]
)
def test_extractor_execution_rejects_negative_counter(
    field_name: str
) -> None:
    with pytest.raises(
        ValueError,
        match=f"{field_name} ne peut pas être négatif"
    ):
        build_execution(
            **{
                field_name: -1
            }
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1.2",
        None
    ]
)
def test_extractor_execution_rejects_invalid_duration_type(
    value: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="duration_seconds doit être un nombre"
    ):
        build_execution(
            duration_seconds=value
        )


@pytest.mark.parametrize(
    "value",
    [
        -0.1,
        math.inf,
        -math.inf,
        math.nan
    ]
)
def test_extractor_execution_rejects_invalid_duration(
    value: float
) -> None:
    with pytest.raises(
        ValueError,
        match="duration_seconds doit être positive, finie ou nulle"
    ):
        build_execution(
            duration_seconds=value
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (1.25, 1.25)
    ]
)
def test_extractor_execution_normalizes_duration(
    value: int | float,
    expected: float
) -> None:
    execution = build_execution(
        duration_seconds=value
    )

    assert execution.duration_seconds == expected
    assert isinstance(execution.duration_seconds, float)


def test_extractor_execution_requires_mapping_rejection_reasons() -> None:
    with pytest.raises(
        TypeError,
        match="rejection_reasons doit être un dictionnaire"
    ):
        build_execution(
            rejection_reasons=[
                "invalid"
            ]
        )


def test_extractor_execution_normalizes_rejection_reasons() -> None:
    execution = build_execution(
        article_count=3,
        analyzed_count=10,
        rejected_count=7,
        rejection_reasons={
            "  INVALID_URL  ": 2,
            "invalid_url": 1,
            "missing_title": 3,
            "": 5,
            None: 4,
            "zero": 0
        }
    )

    assert execution.rejection_reasons == {
        "invalid_url": 3,
        "missing_title": 3,
        "unknown": 1
    }
    assert execution.rejected_count == 7


def test_extractor_execution_rejects_invalid_reason_counter() -> None:
    with pytest.raises(
        TypeError,
        match=r"rejection_reasons\[invalid_url\] doit être un entier"
    ):
        build_execution(
            rejection_reasons={
                "invalid_url": 1.5
            }
        )


def test_extractor_execution_rejects_negative_reason_counter() -> None:
    with pytest.raises(
        ValueError,
        match=r"rejection_reasons\[invalid_url\] ne peut pas être négatif"
    ):
        build_execution(
            rejection_reasons={
                "invalid_url": -1
            }
        )


def test_extractor_execution_increases_rejected_count_from_reasons() -> None:
    execution = build_execution(
        article_count=3,
        analyzed_count=5,
        rejected_count=1,
        rejection_reasons={
            "invalid_url": 2,
            "missing_title": 3
        }
    )

    assert execution.rejected_count == 5
    assert execution.rejection_reasons == {
        "invalid_url": 2,
        "missing_title": 3
    }
    assert execution.analyzed_count == 8


def test_extractor_execution_adds_unknown_reason_for_missing_count() -> None:
    execution = build_execution(
        rejected_count=5,
        rejection_reasons={
            "invalid_url": 2
        }
    )

    assert execution.rejection_reasons == {
        "invalid_url": 2,
        "unknown": 3
    }


def test_extractor_execution_adjusts_analyzed_count() -> None:
    execution = build_execution(
        article_count=8,
        analyzed_count=2,
        rejected_count=3
    )

    assert execution.analyzed_count == 11


def test_extractor_execution_preserves_higher_analyzed_count() -> None:
    execution = build_execution(
        article_count=3,
        analyzed_count=20,
        rejected_count=2
    )

    assert execution.analyzed_count == 20


def test_extractor_execution_resolves_main_reason_from_counts() -> None:
    execution = build_execution(
        rejected_count=5,
        rejection_reasons={
            "missing_title": 2,
            "invalid_url": 3
        },
        main_reason=""
    )

    assert execution.main_reason == "invalid_url"


def test_extractor_execution_resolves_tied_main_reason_alphabetically() -> None:
    execution = build_execution(
        rejected_count=4,
        rejection_reasons={
            "missing_title": 2,
            "invalid_url": 2
        },
        main_reason=""
    )

    assert execution.main_reason == "invalid_url"


def test_extractor_execution_prefers_explicit_main_reason() -> None:
    execution = build_execution(
        rejected_count=5,
        rejection_reasons={
            "invalid_url": 5
        },
        main_reason="manual_reason"
    )

    assert execution.main_reason == "manual_reason"


def test_extractor_execution_uses_metadata_failure_reason() -> None:
    execution = build_execution(
        rejected_count=0,
        rejection_reasons={},
        main_reason="",
        metadata={
            "failure_reason": "authentication_failed"
        }
    )

    assert execution.main_reason == "authentication_failed"


def test_extractor_execution_copies_metadata() -> None:
    metadata = {
        "source_id": "newsapi"
    }

    execution = build_execution(
        metadata=metadata
    )

    assert execution.metadata == metadata
    assert execution.metadata is not metadata

    metadata["source_id"] = "changed"

    assert execution.metadata == {
        "source_id": "newsapi"
    }


def test_extractor_execution_copies_rejection_reasons() -> None:
    reasons = {
        "invalid_url": 2
    }

    execution = build_execution(
        rejected_count=2,
        rejection_reasons=reasons
    )

    assert execution.rejection_reasons == reasons
    assert execution.rejection_reasons is not reasons

    reasons["invalid_url"] = 10

    assert execution.rejection_reasons == {
        "invalid_url": 2
    }


@pytest.mark.parametrize(
    (
        "article_count",
        "analyzed_count",
        "expected_success",
        "expected_rejection"
    ),
    [
        (8, 10, 80.0, 20.0),
        (1, 3, 33.33, 66.67),
        (0, 0, 0.0, 0.0)
    ]
)
def test_extractor_execution_rates(
    article_count: int,
    analyzed_count: int,
    expected_success: float,
    expected_rejection: float
) -> None:
    rejected_count = max(
        analyzed_count - article_count,
        0
    )

    execution = build_execution(
        article_count=article_count,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count
    )

    assert execution.success_rate == expected_success
    assert execution.rejection_rate == expected_rejection


def test_extractor_execution_operation_aliases() -> None:
    execution = build_execution(
        article_count=8,
        rejected_count=2
    )

    assert execution.successful_operations == 8
    assert execution.failed_operations == 2


def test_extractor_execution_has_errors() -> None:
    with_errors = build_execution(
        errors=(
            "Erreur réseau",
        )
    )
    without_errors = build_execution(
        errors=()
    )

    assert with_errors.has_errors is True
    assert without_errors.has_errors is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            EXTRACTOR_STATUS_SUCCESS,
            True
        ),
        (
            EXTRACTOR_STATUS_PARTIAL_SUCCESS,
            True
        ),
        (
            EXTRACTOR_STATUS_FAILED,
            False
        )
    ]
)
def test_extractor_execution_is_successful(
    status: str,
    expected: bool
) -> None:
    execution = build_execution(
        status=status
    )

    assert execution.is_successful is expected


def test_extractor_execution_to_dict() -> None:
    execution = build_execution(
        extractor_name="NewsAPI",
        source_type="api",
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        article_count=8,
        duration_seconds=1.25,
        message="Extraction partielle",
        errors=(
            "Erreur réseau",
        ),
        analyzed_count=10,
        rejected_count=2,
        requests_count=3,
        main_reason="invalid_url",
        rejection_reasons={
            "invalid_url": 2
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    assert execution.to_dict() == {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "status": EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        "article_count": 8,
        "duration_seconds": 1.25,
        "message": "Extraction partielle",
        "errors": (
            "Erreur réseau",
        ),
        "analyzed_count": 10,
        "rejected_count": 2,
        "requests_count": 3,
        "main_reason": "invalid_url",
        "rejection_reasons": {
            "invalid_url": 2
        },
        "metadata": {
            "source_id": "newsapi"
        },
        "success_rate": 80.0,
        "rejection_rate": 20.0
    }


def test_extractor_execution_to_dict_is_independent() -> None:
    execution = build_execution(
        rejection_reasons={
            "invalid_url": 2
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    first = execution.to_dict()
    second = execution.to_dict()

    assert first == second
    assert first is not second
    assert first["metadata"] is not execution.metadata
    assert first["rejection_reasons"] is not execution.rejection_reasons

    first["metadata"]["source_id"] = "changed"
    first["rejection_reasons"]["invalid_url"] = 100

    assert execution.metadata == {
        "source_id": "newsapi"
    }
    assert execution.rejection_reasons == {
        "invalid_url": 2
    }


def test_extractor_execution_is_frozen() -> None:
    execution = build_execution()

    with pytest.raises(AttributeError):
        execution.status = EXTRACTOR_STATUS_FAILED