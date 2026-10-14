"""Tests unitaires des modèles de préparation des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/preparation/test_article_preparation_models.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import FrozenInstanceError, fields, is_dataclass
from typing import Any

import pytest

import src.article.preparation.article_preparation_models as module

# ArticlePreparationReport

def test_article_preparation_report_is_dataclass() -> None:
    assert is_dataclass(module.ArticlePreparationReport)


def test_article_preparation_report_uses_slots() -> None:
    report = module.ArticlePreparationReport()

    with pytest.raises(AttributeError):
        report.unexpected_attribute = "value"


def test_article_preparation_report_has_expected_default_values() -> None:
    report = module.ArticlePreparationReport()

    assert report.received == 0
    assert report.normalized == 0
    assert report.normalization_ignored == 0
    assert report.cleaned == 0
    assert report.cleaning_ignored == 0
    assert report.duplicates == 0
    assert report.invalid == 0
    assert report.kept == 0
    assert report.rejection_reasons == Counter()


def test_article_preparation_report_accepts_custom_values() -> None:
    rejection_reasons = Counter(
        {
            "missing_title": 3,
            "invalid_url": 2
        }
    )

    report = module.ArticlePreparationReport(
        received=20,
        normalized=18,
        normalization_ignored=2,
        cleaned=17,
        cleaning_ignored=1,
        duplicates=3,
        invalid=4,
        kept=10,
        rejection_reasons=rejection_reasons
    )

    assert report.received == 20
    assert report.normalized == 18
    assert report.normalization_ignored == 2
    assert report.cleaned == 17
    assert report.cleaning_ignored == 1
    assert report.duplicates == 3
    assert report.invalid == 4
    assert report.kept == 10
    assert report.rejection_reasons == rejection_reasons


def test_article_preparation_report_rejection_reasons_uses_independent_counter() -> None:
    first_report = module.ArticlePreparationReport()
    second_report = module.ArticlePreparationReport()

    first_report.rejection_reasons["missing_title"] += 1

    assert first_report.rejection_reasons == Counter(
        {
            "missing_title": 1
        }
    )
    assert second_report.rejection_reasons == Counter()
    assert (
        first_report.rejection_reasons
        is not second_report.rejection_reasons
    )


def test_article_preparation_report_is_mutable() -> None:
    report = module.ArticlePreparationReport()

    report.received = 5
    report.kept = 3
    report.rejection_reasons["duplicate"] = 2

    assert report.received == 5
    assert report.kept == 3
    assert report.rejection_reasons["duplicate"] == 2


def test_article_preparation_report_fields_have_expected_order() -> None:
    field_names = [
        dataclass_field.name
        for dataclass_field in fields(module.ArticlePreparationReport)
    ]

    assert field_names == [
        "received",
        "normalized",
        "normalization_ignored",
        "cleaned",
        "cleaning_ignored",
        "duplicates",
        "invalid",
        "kept",
        "rejection_reasons"
    ]


def test_article_preparation_report_supports_equality() -> None:
    first_report = module.ArticlePreparationReport(
        received=10,
        kept=8,
        rejection_reasons=Counter({"duplicate": 2})
    )
    second_report = module.ArticlePreparationReport(
        received=10,
        kept=8,
        rejection_reasons=Counter({"duplicate": 2})
    )

    assert first_report == second_report


def test_article_preparation_report_detects_different_values() -> None:
    first_report = module.ArticlePreparationReport(received=10)
    second_report = module.ArticlePreparationReport(received=11)

    assert first_report != second_report


def test_article_preparation_report_repr_contains_class_name_and_values() -> None:
    report = module.ArticlePreparationReport(
        received=10,
        kept=8
    )

    representation = repr(report)

    assert "ArticlePreparationReport" in representation
    assert "received=10" in representation
    assert "kept=8" in representation


# ArticlePreparationResult

def test_article_preparation_result_is_dataclass() -> None:
    assert is_dataclass(module.ArticlePreparationResult)


def test_article_preparation_result_uses_slots() -> None:
    result = module.ArticlePreparationResult(
        articles=[],
        report=module.ArticlePreparationReport()
    )

    with pytest.raises(AttributeError):
        result.unexpected_attribute = "value"


def test_article_preparation_result_stores_articles_and_report() -> None:
    articles: list[dict[str, Any]] = [
        {
            "id": "article-001",
            "title": "Premier article"
        },
        {
            "id": "article-002",
            "title": "Deuxième article"
        }
    ]
    report = module.ArticlePreparationReport(
        received=2,
        kept=2
    )

    result = module.ArticlePreparationResult(
        articles=articles,
        report=report
    )

    assert result.articles == articles
    assert result.report == report
    assert result.articles is articles
    assert result.report is report


def test_article_preparation_result_accepts_empty_articles() -> None:
    report = module.ArticlePreparationReport()

    result = module.ArticlePreparationResult(
        articles=[],
        report=report
    )

    assert result.articles == []
    assert result.report is report


def test_article_preparation_result_is_mutable() -> None:
    result = module.ArticlePreparationResult(
        articles=[],
        report=module.ArticlePreparationReport()
    )

    result.articles.append(
        {
            "id": "article-001"
        }
    )
    result.report.kept = 1

    assert result.articles == [
        {
            "id": "article-001"
        }
    ]
    assert result.report.kept == 1


def test_article_preparation_result_requires_articles() -> None:
    with pytest.raises(TypeError):
        module.ArticlePreparationResult(
            report=module.ArticlePreparationReport()
        )


def test_article_preparation_result_requires_report() -> None:
    with pytest.raises(TypeError):
        module.ArticlePreparationResult(
            articles=[]
        )


def test_article_preparation_result_fields_have_expected_order() -> None:
    field_names = [
        dataclass_field.name
        for dataclass_field in fields(module.ArticlePreparationResult)
    ]

    assert field_names == [
        "articles",
        "report"
    ]


def test_article_preparation_result_supports_equality() -> None:
    first_result = module.ArticlePreparationResult(
        articles=[
            {
                "id": "article-001"
            }
        ],
        report=module.ArticlePreparationReport(
            received=1,
            kept=1
        )
    )
    second_result = module.ArticlePreparationResult(
        articles=[
            {
                "id": "article-001"
            }
        ],
        report=module.ArticlePreparationReport(
            received=1,
            kept=1
        )
    )

    assert first_result == second_result


def test_article_preparation_result_detects_different_articles() -> None:
    first_result = module.ArticlePreparationResult(
        articles=[
            {
                "id": "article-001"
            }
        ],
        report=module.ArticlePreparationReport()
    )
    second_result = module.ArticlePreparationResult(
        articles=[
            {
                "id": "article-002"
            }
        ],
        report=module.ArticlePreparationReport()
    )

    assert first_result != second_result


def test_article_preparation_result_repr_contains_expected_values() -> None:
    result = module.ArticlePreparationResult(
        articles=[
            {
                "id": "article-001"
            }
        ],
        report=module.ArticlePreparationReport(
            received=1,
            kept=1
        )
    )

    representation = repr(result)

    assert "ArticlePreparationResult" in representation
    assert "article-001" in representation
    assert "received=1" in representation
    assert "kept=1" in representation