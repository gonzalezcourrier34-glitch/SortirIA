"""Tests de la façade d'exécution des extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/core/test_extractor_runner.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest

import src.extractors.core.extractor_runner as module
from config.constants import (
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
)
from src.extractors.core.extractor_models import ExtractorExecution
from src.extractors.core.extractor_results import ExtractorResult

# Helpers

def build_result(**overrides: Any) -> ExtractorResult:
    """Construit un résultat d'extraction valide."""

    values = {
        "name": "NewsAPI",
        "source_type": "api",
        "status": EXTRACTOR_STATUS_SUCCESS,
        "message": "Extraction terminée.",
        "articles": [
            {
                "id": "article-1",
                "title": "Premier article"
            }
        ],
        "errors": [],
        "duration_seconds": 1.25,
        "analyzed_count": 2,
        "rejected_count": 1,
        "requests_count": 3,
        "rejection_reasons": {
            "invalid_article": 1
        },
        "metadata": {
            "source_id": "newsapi"
        }
    }
    values.update(overrides)
    return ExtractorResult(**values)


# Conversion

def test_build_execution_report_converts_all_fields() -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        errors=[
            "Une page a échoué"
        ]
    )

    execution = module.build_execution_report(result)

    assert isinstance(execution, ExtractorExecution)
    assert execution.extractor_name == "NewsAPI"
    assert execution.source_type == "api"
    assert execution.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert execution.article_count == 1
    assert execution.duration_seconds == 1.25
    assert execution.message == "Extraction terminée."
    assert execution.errors == (
        "Une page a échoué",
    )
    assert execution.analyzed_count == 2
    assert execution.rejected_count == 1
    assert execution.requests_count == 3
    assert execution.main_reason == "invalid_article"
    assert execution.rejection_reasons == {
        "invalid_article": 1
    }
    assert execution.metadata == {
        "source_id": "newsapi"
    }


def test_build_execution_report_copies_mutable_values() -> None:
    result = build_result()

    execution = module.build_execution_report(result)

    assert execution.rejection_reasons is not result.rejection_reasons
    assert execution.metadata is not result.metadata

    result.rejection_reasons["invalid_article"] = 99
    result.metadata["source_id"] = "changed"

    assert execution.rejection_reasons == {
        "invalid_article": 1
    }
    assert execution.metadata == {
        "source_id": "newsapi"
    }


def test_build_execution_report_uses_extracted_count() -> None:
    result = build_result(
        articles=[
            {
                "id": "article-1"
            },
            {
                "id": "article-2"
            }
        ],
        analyzed_count=2,
        rejected_count=0,
        rejection_reasons={}
    )

    execution = module.build_execution_report(result)

    assert execution.article_count == 2


def test_build_execution_report_without_errors() -> None:
    result = build_result(
        errors=[]
    )

    execution = module.build_execution_report(result)

    assert execution.errors == ()
    assert execution.has_errors is False


# Copie des articles

def test_copy_articles_returns_independent_list() -> None:
    articles = [
        {
            "id": "article-1"
        },
        {
            "id": "article-2"
        }
    ]

    copied = module.copy_articles(articles)

    assert copied == articles
    assert copied is not articles


def test_copy_articles_copies_each_article() -> None:
    article = {
        "id": "article-1",
        "title": "Article"
    }

    copied = module.copy_articles([
        article
    ])

    assert copied[0] == article
    assert copied[0] is not article

    article["title"] = "Modifié"

    assert copied[0]["title"] == "Article"


def test_copy_articles_is_shallow() -> None:
    metadata = {
        "publisher": "Reuters"
    }
    article = {
        "id": "article-1",
        "metadata": metadata
    }

    copied = module.copy_articles([
        article
    ])

    assert copied[0] is not article
    assert copied[0]["metadata"] is metadata


def test_copy_articles_returns_empty_list() -> None:
    assert module.copy_articles([]) == []


# Exécution

def test_run_extractor_calls_executor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result()
    captured: dict[str, Any] = {}

    def extractor() -> list[dict[str, Any]]:
        return [
            {
                "id": "article-1"
            }
        ]

    def fake_execute_extractor_call(
        *,
        extractor_name: str,
        source_type: str,
        extraction_function: Any
    ) -> ExtractorResult:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "extraction_function": extraction_function
        })
        return result

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        fake_execute_extractor_call
    )

    module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=extractor
    )

    assert captured == {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "extraction_function": extractor
    }


def test_run_extractor_builds_execution_report(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result()
    expected_execution = object()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )

    def fake_build_execution_report(
        current_result: ExtractorResult
    ) -> Any:
        captured["result"] = current_result
        return expected_execution

    monkeypatch.setattr(
        module,
        "build_execution_report",
        fake_build_execution_report
    )

    articles, execution = module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    assert execution is expected_execution
    assert captured["result"] is result
    assert articles == result.articles


def test_run_extractor_returns_copied_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "article-1",
        "title": "Article"
    }
    result = build_result(
        articles=[
            article
        ],
        analyzed_count=1,
        rejected_count=0,
        rejection_reasons={}
    )

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )

    articles, execution = module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    assert articles == result.articles
    assert articles is not result.articles
    assert articles[0] is not result.articles[0]
    assert isinstance(execution, ExtractorExecution)

    articles[0]["title"] = "Modifié"

    assert result.articles[0]["title"] == "Article"


def test_run_extractor_returns_empty_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result(
        articles=[],
        analyzed_count=0,
        rejected_count=0,
        rejection_reasons={},
        status=EXTRACTOR_STATUS_SUCCESS
    )

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )

    articles, execution = module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    assert articles == []
    assert execution.article_count == 0


def test_run_extractor_returns_failed_execution(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result(
        status=EXTRACTOR_STATUS_FAILED,
        articles=[],
        errors=[
            "Clé API invalide"
        ],
        message="Clé API invalide",
        analyzed_count=1,
        rejected_count=1,
        rejection_reasons={
            "authentication_failed": 1
        }
    )

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )

    articles, execution = module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    assert articles == []
    assert execution.status == EXTRACTOR_STATUS_FAILED
    assert execution.errors == (
        "Clé API invalide",
    )
    assert execution.main_reason == "authentication_failed"


# Logs

def test_run_extractor_logs_start(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result()
    messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: messages.append(args)
    )

    module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    assert messages[0] == (
        "Démarrage de l'extracteur %s.",
        "NewsAPI"
    )


def test_run_extractor_logs_execution_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result(
        message="Extraction terminée."
    )
    messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: messages.append(args)
    )

    module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    summary = messages[1]

    assert summary[1] == "NewsAPI"
    assert summary[2] == result.status
    assert summary[3] == result.analyzed_count
    assert summary[4] == result.extracted_count
    assert summary[5] == result.rejected_count
    assert summary[6] == result.requests_count
    assert summary[7] == result.duration_seconds
    assert summary[8] == result.success_rate
    assert summary[9] == result.main_reason
    assert summary[10] == " | message=Extraction terminée."


def test_run_extractor_logs_dash_without_main_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result(
        rejected_count=0,
        rejection_reasons={},
        message=""
    )
    messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: messages.append(args)
    )

    module.run_extractor(
        extractor_name="NewsAPI",
        source_type="api",
        extractor=list
    )

    summary = messages[1]

    assert summary[9] == "-"
    assert summary[10] == ""


def test_run_extractor_uses_result_name_in_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = build_result(
        name="Nom réel"
    )
    messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "execute_extractor_call",
        lambda **kwargs: result
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: messages.append(args)
    )

    module.run_extractor(
        extractor_name="Nom demandé",
        source_type="api",
        extractor=list
    )

    assert messages[0][1] == "Nom demandé"
    assert messages[1][1] == "Nom réel"