"""Tests des résultats métier communs des extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/core/test_extractor_results.py` (commit 93632c5).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pytest

import src.extractors.core.extractor_results as module
from config.constants import (
    EXTRACTOR_STATUS_DISABLED,
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_ROBOTS_DENIED,
    EXTRACTOR_STATUS_SUCCESS,
)

# Helpers

def build_result(**overrides: Any) -> module.ExtractorResult:
    """Construit un résultat métier valide."""

    values = {
        "name": "NewsAPI",
        "source_type": "api",
        "status": EXTRACTOR_STATUS_SUCCESS,
        "articles": [
            {
                "id": "article-1",
                "title": "Premier article"
            }
        ],
        "duration_seconds": 1.25,
        "analyzed_count": 1,
        "rejected_count": 0,
        "requests_count": 1
    }
    values.update(overrides)
    return module.ExtractorResult(**values)


# Normalisation générale

def test_extractor_result_normalizes_text_fields() -> None:
    result = build_result(
        name="  NewsAPI  ",
        source_type="  API  ",
        status=f"  {EXTRACTOR_STATUS_SUCCESS.upper()}  ",
        message="  Extraction terminée  "
    )

    assert result.name == "NewsAPI"
    assert result.source_type == "api"
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    assert result.message == "Extraction terminée"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        None
    ]
)
def test_extractor_result_rejects_empty_name(
    name: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="Le nom de l'extracteur ne peut pas être vide"
    ):
        build_result(name=name)


@pytest.mark.parametrize(
    "source_type",
    [
        "",
        "unknown",
        None
    ]
)
def test_extractor_result_rejects_invalid_source_type(
    source_type: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="Type de source invalide"
    ):
        build_result(source_type=source_type)


@pytest.mark.parametrize(
    "status",
    [
        "",
        "unknown",
        None
    ]
)
def test_extractor_result_rejects_invalid_status(
    status: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="Statut d'extracteur invalide"
    ):
        build_result(status=status)


# Validation des collections

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "invalid"
    ]
)
def test_extractor_result_requires_articles_list(
    articles: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="articles doit être une liste"
    ):
        build_result(articles=articles)


@pytest.mark.parametrize(
    "errors",
    [
        None,
        {},
        (),
        "invalid"
    ]
)
def test_extractor_result_requires_errors_list(
    errors: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="errors doit être une liste"
    ):
        build_result(errors=errors)


@pytest.mark.parametrize(
    "rejection_reasons",
    [
        None,
        [],
        "invalid"
    ]
)
def test_extractor_result_requires_mapping_rejection_reasons(
    rejection_reasons: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="rejection_reasons doit être un dictionnaire"
    ):
        build_result(
            rejection_reasons=rejection_reasons
        )


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "invalid"
    ]
)
def test_extractor_result_requires_mapping_metadata(
    metadata: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="metadata doit être un dictionnaire"
    ):
        build_result(metadata=metadata)


# Articles

def test_extractor_result_copies_articles() -> None:
    article = {
        "id": "article-1",
        "title": "Article"
    }
    articles = [
        article
    ]

    result = build_result(
        articles=articles
    )

    assert result.articles == articles
    assert result.articles is not articles
    assert result.articles[0] is not article

    article["title"] = "Modifié"
    articles.append({
        "id": "article-2"
    })

    assert result.articles == [
        {
            "id": "article-1",
            "title": "Article"
        }
    ]


def test_extractor_result_accepts_mapping_articles() -> None:
    class ArticleMapping(Mapping[str, Any]):
        def __init__(self) -> None:
            self.data = {
                "id": "article-1",
                "title": "Article"
            }

        def __getitem__(self, key: str) -> Any:
            return self.data[key]

        def __iter__(self):
            return iter(self.data)

        def __len__(self) -> int:
            return len(self.data)

    result = build_result(
        articles=[
            ArticleMapping()
        ]
    )

    assert result.articles == [
        {
            "id": "article-1",
            "title": "Article"
        }
    ]


def test_extractor_result_rejects_non_mapping_articles() -> None:
    result = build_result(
        articles=[
            {
                "id": "article-1"
            },
            "invalid",
            None,
            42
        ],
        analyzed_count=0,
        rejected_count=0
    )

    assert result.articles == [
        {
            "id": "article-1"
        }
    ]
    assert result.extracted_count == 1
    assert result.rejected_count == 3
    assert result.rejection_reasons == {
        module.REJECTION_REASON_INVALID_ARTICLE: 3
    }
    assert result.analyzed_count == 4


def test_extractor_result_ignores_all_invalid_articles() -> None:
    result = build_result(
        articles=[
            None,
            "invalid"
        ],
        analyzed_count=0,
        rejected_count=0
    )

    assert result.articles == []
    assert result.extracted_count == 0
    assert result.rejected_count == 2
    assert result.status == EXTRACTOR_STATUS_EMPTY


def test_extracted_count_is_always_derived_from_articles() -> None:
    result = module.ExtractorResult(
        name="NewsAPI",
        source_type="api",
        status=EXTRACTOR_STATUS_SUCCESS,
        articles=[
            {
                "id": "article-1"
            },
            {
                "id": "article-2"
            }
        ],
        extracted_count=999
    )

    assert result.extracted_count == 2


# Erreurs

def test_extractor_result_normalizes_errors() -> None:
    result = build_result(
        errors=[
            "  Erreur réseau  ",
            "",
            "   ",
            None,
            42
        ]
    )

    assert result.errors == [
        "Erreur réseau",
        "42"
    ]


def test_extractor_result_copies_errors() -> None:
    errors = [
        "Erreur réseau"
    ]

    result = build_result(
        errors=errors
    )

    assert result.errors == errors
    assert result.errors is not errors

    errors.append("Nouvelle erreur")

    assert result.errors == [
        "Erreur réseau"
    ]


# Compteurs

@pytest.mark.parametrize(
    "field_name",
    [
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
def test_extractor_result_rejects_invalid_counter_type(
    field_name: str,
    value: Any
) -> None:
    with pytest.raises(
        TypeError,
        match=f"{field_name} doit être un entier"
    ):
        build_result(
            **{
                field_name: value
            }
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "analyzed_count",
        "rejected_count",
        "requests_count"
    ]
)
def test_extractor_result_rejects_negative_counter(
    field_name: str
) -> None:
    with pytest.raises(
        ValueError,
        match=f"{field_name} ne peut pas être négatif"
    ):
        build_result(
            **{
                field_name: -1
            }
        )


def test_extractor_result_adjusts_analyzed_count() -> None:
    result = build_result(
        articles=[
            {
                "id": "article-1"
            },
            {
                "id": "article-2"
            }
        ],
        analyzed_count=1,
        rejected_count=3
    )

    assert result.extracted_count == 2
    assert result.rejected_count == 3
    assert result.analyzed_count == 5


def test_extractor_result_preserves_higher_analyzed_count() -> None:
    result = build_result(
        analyzed_count=20,
        rejected_count=2
    )

    assert result.analyzed_count == 20


# Durée

@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1.25",
        None
    ]
)
def test_extractor_result_rejects_invalid_duration_type(
    value: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="duration_seconds doit être un nombre"
    ):
        build_result(
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
def test_extractor_result_rejects_invalid_duration(
    value: float
) -> None:
    with pytest.raises(
        ValueError,
        match="duration_seconds doit être positive, finie ou nulle"
    ):
        build_result(
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
def test_extractor_result_normalizes_duration(
    value: float,
    expected: float
) -> None:
    result = build_result(
        duration_seconds=value
    )

    assert result.duration_seconds == expected
    assert isinstance(result.duration_seconds, float)


# Motifs de rejet

def test_extractor_result_normalizes_rejection_reasons() -> None:
    result = build_result(
        analyzed_count=10,
        rejected_count=7,
        rejection_reasons={
            "  INVALID_URL  ": 2,
            "invalid_url": 1,
            "missing_title": 3,
            "": 10,
            None: 4,
            "zero": 0
        }
    )

    assert result.rejection_reasons == {
        "invalid_url": 3,
        "missing_title": 3,
        module.REJECTION_REASON_UNKNOWN: 1
    }
    assert result.rejected_count == 7


def test_extractor_result_increases_rejected_count_from_reasons() -> None:
    result = build_result(
        analyzed_count=0,
        rejected_count=1,
        rejection_reasons={
            "invalid_url": 3,
            "missing_title": 2
        }
    )

    assert result.rejected_count == 5
    assert result.analyzed_count == 6


def test_extractor_result_adds_unknown_reason() -> None:
    result = build_result(
        rejected_count=5,
        rejection_reasons={
            "invalid_url": 2
        }
    )

    assert result.rejection_reasons == {
        "invalid_url": 2,
        module.REJECTION_REASON_UNKNOWN: 3
    }


def test_extractor_result_rejects_invalid_reason_counter() -> None:
    with pytest.raises(
        TypeError,
        match=r"rejection_reasons\[invalid_url\] doit être un entier"
    ):
        build_result(
            rejection_reasons={
                "invalid_url": 1.5
            }
        )


def test_extractor_result_rejects_negative_reason_counter() -> None:
    with pytest.raises(
        ValueError,
        match=r"rejection_reasons\[invalid_url\] ne peut pas être négatif"
    ):
        build_result(
            rejection_reasons={
                "invalid_url": -1
            }
        )


def test_extractor_result_copies_rejection_reasons() -> None:
    reasons = {
        "invalid_url": 2
    }

    result = build_result(
        rejected_count=2,
        rejection_reasons=reasons
    )

    assert result.rejection_reasons == reasons
    assert result.rejection_reasons is not reasons

    reasons["invalid_url"] = 10

    assert result.rejection_reasons == {
        "invalid_url": 2
    }


# Ajout de motifs

def test_add_rejection_reason() -> None:
    result = build_result(
        rejected_count=0,
        rejection_reasons={}
    )

    result.add_rejection_reason(
        "  INVALID_URL  ",
        2
    )
    result.add_rejection_reason(
        "invalid_url",
        1
    )

    assert result.rejection_reasons == {
        "invalid_url": 3
    }


@pytest.mark.parametrize(
    ("reason", "count"),
    [
        ("", 1),
        ("   ", 1),
        (None, 1),
        ("invalid_url", 0)
    ]
)
def test_add_rejection_reason_ignores_empty_values(
    reason: Any,
    count: int
) -> None:
    result = build_result(
        rejection_reasons={}
    )

    result.add_rejection_reason(
        reason,
        count
    )

    assert result.rejection_reasons == {}


@pytest.mark.parametrize(
    "count",
    [
        True,
        1.5,
        "1",
        None
    ]
)
def test_add_rejection_reason_rejects_invalid_count(
    count: Any
) -> None:
    result = build_result()

    with pytest.raises(
        TypeError,
        match="count doit être un entier"
    ):
        result.add_rejection_reason(
            "invalid_url",
            count
        )


def test_add_rejection_reason_rejects_negative_count() -> None:
    result = build_result()

    with pytest.raises(
        ValueError,
        match="count ne peut pas être négatif"
    ):
        result.add_rejection_reason(
            "invalid_url",
            -1
        )


# Fusion des motifs

def test_merge_rejection_reasons() -> None:
    result = build_result(
        articles=[
            {
                "id": "article-1"
            }
        ],
        analyzed_count=1,
        rejected_count=0,
        rejection_reasons={}
    )

    result.merge_rejection_reasons({
        "invalid_url": 2,
        "  MISSING_TITLE  ": 1
    })

    assert result.rejection_reasons == {
        "invalid_url": 2,
        "missing_title": 1
    }
    assert result.rejected_count == 3
    assert result.analyzed_count == 4


def test_merge_rejection_reasons_adds_existing_counts() -> None:
    result = build_result(
        rejected_count=2,
        rejection_reasons={
            "invalid_url": 2
        }
    )

    result.merge_rejection_reasons({
        "invalid_url": 3
    })

    assert result.rejection_reasons == {
        "invalid_url": 5
    }
    assert result.rejected_count == 5


def test_merge_rejection_reasons_preserves_higher_rejected_count() -> None:
    result = build_result(
        analyzed_count=11,
        rejected_count=10,
        rejection_reasons={
            "invalid_url": 10
        }
    )

    result.merge_rejection_reasons({
        "missing_title": 1
    })

    assert result.rejected_count == 11
    assert result.analyzed_count == 12


def test_merge_rejection_reasons_rejects_non_mapping() -> None:
    result = build_result()

    with pytest.raises(
        TypeError,
        match="reasons doit être un dictionnaire"
    ):
        result.merge_rejection_reasons([
            "invalid"
        ])


# Métadonnées

def test_extractor_result_copies_metadata() -> None:
    metadata = {
        "source_id": "newsapi"
    }

    result = build_result(
        metadata=metadata
    )

    assert result.metadata == metadata
    assert result.metadata is not metadata

    metadata["source_id"] = "changed"

    assert result.metadata == {
        "source_id": "newsapi"
    }


def test_merge_metadata_adds_missing_values_only() -> None:
    result = build_result(
        metadata={
            "source_id": "newsapi",
            "language": "fr"
        }
    )

    result.merge_metadata({
        "source_id": "changed",
        "country": "fr"
    })

    assert result.metadata == {
        "source_id": "newsapi",
        "language": "fr",
        "country": "fr"
    }


def test_merge_metadata_rejects_non_mapping() -> None:
    result = build_result()

    with pytest.raises(
        TypeError,
        match="metadata doit être un dictionnaire"
    ):
        result.merge_metadata([
            "invalid"
        ])


# Ajustement des statuts

def test_success_without_articles_becomes_empty() -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_SUCCESS,
        articles=[]
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY


def test_partial_without_errors_becomes_success() -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        articles=[
            {
                "id": "article-1"
            }
        ],
        errors=[]
    )

    assert result.status == EXTRACTOR_STATUS_SUCCESS


def test_partial_without_errors_and_articles_becomes_empty() -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        articles=[],
        errors=[]
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY


def test_partial_with_errors_is_preserved() -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        errors=[
            "Erreur réseau"
        ]
    )

    assert result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS


# Propriétés

def test_main_reason_returns_dominant_reason() -> None:
    result = build_result(
        rejected_count=5,
        rejection_reasons={
            "missing_title": 2,
            "invalid_url": 3
        }
    )

    assert result.main_reason == "invalid_url"


def test_main_reason_resolves_tie_alphabetically() -> None:
    result = build_result(
        rejected_count=4,
        rejection_reasons={
            "missing_title": 2,
            "invalid_url": 2
        }
    )

    assert result.main_reason == "invalid_url"


def test_main_reason_returns_empty_without_rejections() -> None:
    result = build_result(
        rejected_count=0,
        rejection_reasons={}
    )

    assert result.main_reason == ""


@pytest.mark.parametrize(
    (
        "articles",
        "analyzed_count",
        "rejected_count",
        "expected_success",
        "expected_rejection"
    ),
    [
        (
            [
                {
                    "id": "1"
                },
                {
                    "id": "2"
                }
            ],
            4,
            2,
            50.0,
            50.0
        ),
        (
            [
                {
                    "id": "1"
                }
            ],
            3,
            2,
            33.33,
            66.67
        ),
        (
            [],
            0,
            0,
            0.0,
            0.0
        )
    ]
)
def test_result_rates(
    articles: list[dict[str, Any]],
    analyzed_count: int,
    rejected_count: int,
    expected_success: float,
    expected_rejection: float
) -> None:
    result = build_result(
        articles=articles,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count
    )

    assert result.success_rate == expected_success
    assert result.rejection_rate == expected_rejection


def test_operation_aliases() -> None:
    result = build_result(
        articles=[
            {
                "id": "1"
            },
            {
                "id": "2"
            }
        ],
        rejected_count=3
    )

    assert result.successful_operations == 2
    assert result.failed_operations == 3


def test_has_articles() -> None:
    assert build_result().has_articles is True
    assert build_result(
        articles=[]
    ).has_articles is False


def test_has_errors() -> None:
    assert build_result(
        errors=[
            "Erreur"
        ]
    ).has_errors is True
    assert build_result(
        errors=[]
    ).has_errors is False


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
        ),
        (
            EXTRACTOR_STATUS_EMPTY,
            False
        ),
        (
            EXTRACTOR_STATUS_DISABLED,
            False
        ),
        (
            EXTRACTOR_STATUS_ROBOTS_DENIED,
            False
        )
    ]
)
def test_is_successful(
    status: str,
    expected: bool
) -> None:
    result = build_result(
        status=status,
        errors=(
            [
                "Erreur"
            ]
            if status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
            else []
        )
    )

    assert result.is_successful is expected


# Sérialisation

def test_to_dict() -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        articles=[
            {
                "id": "article-1"
            }
        ],
        errors=[
            "Erreur réseau"
        ],
        analyzed_count=3,
        rejected_count=2,
        rejection_reasons={
            "invalid_url": 2
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    assert result.to_dict() == {
        "name": "NewsAPI",
        "source_type": "api",
        "status": EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        "message": "",
        "articles": [
            {
                "id": "article-1"
            }
        ],
        "errors": [
            "Erreur réseau"
        ],
        "duration_seconds": 1.25,
        "analyzed_count": 3,
        "extracted_count": 1,
        "rejected_count": 2,
        "requests_count": 1,
        "rejection_reasons": {
            "invalid_url": 2
        },
        "metadata": {
            "source_id": "newsapi"
        },
        "main_reason": "invalid_url",
        "success_rate": 33.33,
        "rejection_rate": 66.67
    }


def test_to_dict_returns_independent_data() -> None:
    result = build_result(
        rejection_reasons={
            "invalid_url": 1
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    serialized = result.to_dict()

    serialized["articles"][0]["id"] = "changed"
    serialized["rejection_reasons"]["invalid_url"] = 100
    serialized["metadata"]["source_id"] = "changed"

    assert result.articles == [
        {
            "id": "article-1",
            "title": "Premier article"
        }
    ]
    assert result.rejection_reasons == {
        "invalid_url": 1
    }
    assert result.metadata == {
        "source_id": "newsapi"
    }


# Constructeur d'échec

def test_build_failed_result() -> None:
    result = module.build_failed_result(
        name="NewsAPI",
        source_type="api",
        message="  Clé API invalide  ",
        duration_seconds=1.2,
        analyzed_count=1,
        rejected_count=1,
        requests_count=2,
        rejection_reasons={
            "authentication_failed": 1
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    assert result.status == EXTRACTOR_STATUS_FAILED
    assert result.message == "Clé API invalide"
    assert result.errors == [
        "Clé API invalide"
    ]
    assert result.duration_seconds == 1.2
    assert result.analyzed_count == 1
    assert result.rejected_count == 1
    assert result.requests_count == 2
    assert result.rejection_reasons == {
        "authentication_failed": 1
    }
    assert result.metadata == {
        "source_id": "newsapi"
    }


def test_build_failed_result_without_message() -> None:
    result = module.build_failed_result(
        name="NewsAPI",
        source_type="api",
        message=""
    )

    assert result.message == ""
    assert result.errors == []


# Constructeur désactivé

def test_build_disabled_result() -> None:
    result = module.build_disabled_result(
        name="NewsAPI",
        source_type="api",
        metadata={
            "source_id": "newsapi"
        }
    )

    assert result.status == EXTRACTOR_STATUS_DISABLED
    assert result.message == "Source désactivée."
    assert result.articles == []
    assert result.metadata == {
        "source_id": "newsapi"
    }


def test_build_disabled_result_custom_message() -> None:
    result = module.build_disabled_result(
        name="NewsAPI",
        source_type="api",
        message="API désactivée par configuration."
    )

    assert result.message == "API désactivée par configuration."


# Constructeur vide

def test_build_empty_result() -> None:
    result = module.build_empty_result(
        name="NewsAPI",
        source_type="api",
        message="Aucun résultat.",
        duration_seconds=0.5,
        analyzed_count=2,
        rejected_count=2,
        requests_count=1,
        rejection_reasons={
            "invalid_article": 2
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert result.message == "Aucun résultat."
    assert result.articles == []
    assert result.rejected_count == 2
    assert result.rejection_reasons == {
        "invalid_article": 2
    }


# Constructeur robots.txt

def test_build_robots_denied_result_adds_default_reason() -> None:
    result = module.build_robots_denied_result(
        name="Reuters",
        source_type="scraper",
        rejected_count=3
    )

    assert result.status == EXTRACTOR_STATUS_ROBOTS_DENIED
    assert result.message == "Accès refusé par robots.txt."
    assert result.rejected_count == 3
    assert result.rejection_reasons == {
        module.REJECTION_REASON_ROBOTS_DENIED: 3
    }
    assert result.analyzed_count == 3


def test_build_robots_denied_result_preserves_custom_reasons() -> None:
    result = module.build_robots_denied_result(
        name="Reuters",
        source_type="scraper",
        rejected_count=2,
        rejection_reasons={
            "custom_reason": 2
        }
    )

    assert result.rejection_reasons == {
        "custom_reason": 2
    }


def test_build_robots_denied_result_without_rejection() -> None:
    result = module.build_robots_denied_result(
        name="Reuters",
        source_type="scraper",
        rejected_count=0
    )

    assert result.rejection_reasons == {}
    assert result.rejected_count == 0


# Constructeur réussi

def test_build_success_result() -> None:
    articles = [
        {
            "id": "article-1"
        }
    ]

    result = module.build_success_result(
        name="NewsAPI",
        source_type="api",
        articles=articles,
        duration_seconds=0.75,
        analyzed_count=2,
        rejected_count=1,
        requests_count=1,
        rejection_reasons={
            "invalid_article": 1
        },
        metadata={
            "source_id": "newsapi"
        },
        message="Extraction terminée."
    )

    assert result.status == EXTRACTOR_STATUS_SUCCESS
    assert result.message == "Extraction terminée."
    assert result.articles == articles
    assert result.extracted_count == 1
    assert result.rejected_count == 1


def test_build_success_result_without_articles_becomes_empty() -> None:
    result = module.build_success_result(
        name="NewsAPI",
        source_type="api",
        articles=[]
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY


# Constructeur partiel

def test_build_partial_result() -> None:
    result = module.build_partial_result(
        name="NewsAPI",
        source_type="api",
        articles=[
            {
                "id": "article-1"
            }
        ],
        errors=[
            "Une page a échoué"
        ],
        analyzed_count=2,
        rejected_count=1,
        rejection_reasons={
            "request_failed": 1
        }
    )

    assert result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert result.articles == [
        {
            "id": "article-1"
        }
    ]
    assert result.errors == [
        "Une page a échoué"
    ]


def test_build_partial_result_without_errors_becomes_success() -> None:
    result = module.build_partial_result(
        name="NewsAPI",
        source_type="api",
        articles=[
            {
                "id": "article-1"
            }
        ],
        errors=[]
    )

    assert result.status == EXTRACTOR_STATUS_SUCCESS


def test_build_partial_result_without_articles_or_errors_becomes_empty() -> None:
    result = module.build_partial_result(
        name="NewsAPI",
        source_type="api",
        articles=[],
        errors=[]
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY


# Normalisation des anciens articles

def test_normalize_extracted_articles_returns_empty_for_none() -> None:
    assert module.normalize_extracted_articles(
        None,
        "NewsAPI"
    ) == (
        [],
        0
    )


def test_normalize_extracted_articles_copies_valid_items() -> None:
    article = {
        "id": "article-1"
    }

    articles, rejected_count = module.normalize_extracted_articles(
        [
            article,
            {
                "id": "article-2"
            }
        ],
        "NewsAPI"
    )

    assert articles == [
        {
            "id": "article-1"
        },
        {
            "id": "article-2"
        }
    ]
    assert articles[0] is not article
    assert rejected_count == 0


def test_normalize_extracted_articles_ignores_invalid_items() -> None:
    articles, rejected_count = module.normalize_extracted_articles(
        [
            {
                "id": "article-1"
            },
            None,
            "invalid",
            42
        ],
        "NewsAPI"
    )

    assert articles == [
        {
            "id": "article-1"
        }
    ]
    assert rejected_count == 3


@pytest.mark.parametrize(
    "raw_result",
    [
        {},
        (),
        "invalid",
        42
    ]
)
def test_normalize_extracted_articles_rejects_invalid_result_type(
    raw_result: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="doit retourner une liste ou un ExtractorResult"
    ):
        module.normalize_extracted_articles(
            raw_result,
            "NewsAPI"
        )


# Normalisation d'un résultat existant

def test_normalize_extractor_result_copies_existing_result() -> None:
    original = module.ExtractorResult(
        name="NewsAPI",
        source_type="api",
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        message="Extraction partielle",
        articles=[
            {
                "id": "article-1"
            }
        ],
        errors=[
            "Erreur réseau"
        ],
        duration_seconds=1.5,
        analyzed_count=2,
        rejected_count=1,
        requests_count=3,
        rejection_reasons={
            "request_failed": 1
        },
        metadata={
            "source_id": "newsapi"
        }
    )

    result = module.normalize_extractor_result(
        original,
        extractor_name="Ignored",
        source_type="rss"
    )

    assert result is not original
    assert result.name == "NewsAPI"
    assert result.source_type == "api"
    assert result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert result.articles == original.articles
    assert result.articles is not original.articles
    assert result.errors == original.errors
    assert result.metadata == original.metadata
    assert result.metadata is not original.metadata


def test_normalize_extractor_result_preserves_existing_counters() -> None:
    original = module.ExtractorResult(
        name="NewsAPI",
        source_type="api",
        status=EXTRACTOR_STATUS_SUCCESS,
        articles=[
            {
                "id": "article-1"
            }
        ],
        analyzed_count=5,
        rejected_count=4,
        rejection_reasons={
            "invalid_url": 4
        }
    )

    result = module.normalize_extractor_result(
        original,
        "NewsAPI",
        "api"
    )

    assert result.analyzed_count == 5
    assert result.extracted_count == 1
    assert result.rejected_count == 4
    assert result.rejection_reasons == {
        "invalid_url": 4
    }


# Normalisation d'une liste historique

def test_normalize_extractor_result_from_article_list() -> None:
    raw_result = [
        {
            "id": "article-1"
        },
        {
            "id": "article-2"
        }
    ]

    result = module.normalize_extractor_result(
        raw_result,
        extractor_name="NewsAPI",
        source_type="api"
    )

    assert result.name == "NewsAPI"
    assert result.source_type == "api"
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    assert result.articles == raw_result
    assert result.extracted_count == 2
    assert result.analyzed_count == 2
    assert result.rejected_count == 0
    assert result.rejection_reasons == {}


def test_normalize_extractor_result_from_mixed_list() -> None:
    raw_result = [
        {
            "id": "article-1"
        },
        None,
        "invalid"
    ]

    result = module.normalize_extractor_result(
        raw_result,
        extractor_name="NewsAPI",
        source_type="api"
    )

    assert result.status == EXTRACTOR_STATUS_SUCCESS
    assert result.articles == [
        {
            "id": "article-1"
        }
    ]
    assert result.extracted_count == 1
    assert result.analyzed_count == 3
    assert result.rejected_count == 2
    assert result.rejection_reasons == {
        module.REJECTION_REASON_INVALID_ARTICLE: 2
    }


def test_normalize_extractor_result_from_empty_list() -> None:
    result = module.normalize_extractor_result(
        [],
        extractor_name="NewsAPI",
        source_type="api"
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert result.articles == []
    assert result.analyzed_count == 0
    assert result.rejected_count == 0


def test_normalize_extractor_result_from_none() -> None:
    result = module.normalize_extractor_result(
        None,
        extractor_name="NewsAPI",
        source_type="api"
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert result.articles == []
    assert result.analyzed_count == 0
    assert result.rejected_count == 0


@pytest.mark.parametrize(
    "raw_result",
    [
        {},
        (),
        "invalid",
        42
    ]
)
def test_normalize_extractor_result_rejects_invalid_type(
    raw_result: Any
) -> None:
    with pytest.raises(TypeError):
        module.normalize_extractor_result(
            raw_result,
            extractor_name="NewsAPI",
            source_type="api"
        )