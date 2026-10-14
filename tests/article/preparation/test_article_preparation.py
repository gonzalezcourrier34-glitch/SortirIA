"""Tests unitaires du service de préparation métier des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/preparation/test_article_preparation.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import pytest

import src.article.preparation.article_preparation as module


# Regroupement par source

def test_group_articles_by_source_groups_articles() -> None:
    articles = [
        {
            "id": "article-001",
            "source": "Source A"
        },
        {
            "id": "article-002",
            "source": "Source B"
        },
        {
            "id": "article-003",
            "source": "Source A"
        }
    ]

    result = module.group_articles_by_source(articles)

    assert result == {
        "Source A": [
            {
                "id": "article-001",
                "source": "Source A"
            },
            {
                "id": "article-003",
                "source": "Source A"
            }
        ],
        "Source B": [
            {
                "id": "article-002",
                "source": "Source B"
            }
        ]
    }


def test_group_articles_by_source_returns_copies() -> None:
    article = {
        "id": "article-001",
        "source": "Source A"
    }

    result = module.group_articles_by_source([article])

    assert result["Source A"][0] == article
    assert result["Source A"][0] is not article


@pytest.mark.parametrize(
    "articles",
    [
        None,
        "articles",
        {},
        (),
        123
    ]
)
def test_group_articles_by_source_returns_empty_for_non_list(
    articles: Any,
    caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        result = module.group_articles_by_source(articles)

    assert result == {}
    assert "Collection d'articles invalide" in caplog.text


def test_group_articles_by_source_ignores_invalid_items(
    caplog: pytest.LogCaptureFixture
) -> None:
    articles = [
        {
            "id": "article-001",
            "source": "Source A"
        },
        None,
        "article invalide",
        123
    ]

    with caplog.at_level("WARNING"):
        result = module.group_articles_by_source(articles)

    assert result == {
        "Source A": [
            {
                "id": "article-001",
                "source": "Source A"
            }
        ]
    }
    assert "3 élément(s) ignoré(s)" in caplog.text


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        "   ",
        123,
        [],
        {}
    ]
)
def test_group_articles_by_source_ignores_invalid_source(
    source: Any
) -> None:
    result = module.group_articles_by_source(
        [
            {
                "id": "article-001",
                "source": source
            }
        ]
    )

    assert result == {}


def test_group_articles_by_source_preserves_source_value() -> None:
    result = module.group_articles_by_source(
        [
            {
                "id": "article-001",
                "source": " Source A "
            }
        ]
    )

    assert result == {
        " Source A ": [
            {
                "id": "article-001",
                "source": " Source A "
            }
        ]
    }


def test_group_articles_by_source_accepts_custom_mapping() -> None:
    class CustomMapping(dict[str, Any]):
        """Mapping personnalisé utilisé pour le test."""

    article: Mapping[str, Any] = CustomMapping(
        {
            "id": "article-001",
            "source": "Source A"
        }
    )

    result = module.group_articles_by_source([article])

    assert result["Source A"][0] == dict(article)
    assert isinstance(result["Source A"][0], dict)


def test_group_articles_by_source_logs_debug_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda *args: logged_messages.append(args)
    )

    articles = [
        {
            "id": "article-001",
            "source": "Source A"
        },
        {
            "id": "article-002",
            "source": "Source B"
        }
    ]

    result = module.group_articles_by_source(articles)

    assert len(result) == 2
    assert logged_messages == [
        (
            "%s source(s) détectée(s) pour %s article(s).",
            2,
            2
        )
    ]
    
# Validation

def test_validate_prepared_articles_keeps_valid_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]
    validation_policy = {
        "require_title": True
    }

    def fake_validate(
        article: dict[str, Any],
        policy: Mapping[str, Any]
    ) -> tuple[bool, str | None]:
        assert policy is validation_policy
        return True, None

    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        fake_validate
    )

    valid_articles, rejection_reasons = (
        module.validate_prepared_articles(
            articles,
            validation_policy
        )
    )

    assert valid_articles == articles
    assert rejection_reasons == Counter()


def test_validate_prepared_articles_aggregates_rejection_reasons(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        },
        {
            "id": "article-003"
        },
        {
            "id": "article-004"
        }
    ]

    responses = iter(
        [
            (True, None),
            (False, "missing_title"),
            (False, "missing_title"),
            (False, "invalid_url")
        ]
    )

    def fake_validate(
        article: dict[str, Any],
        policy: Mapping[str, Any]
    ) -> tuple[bool, str | None]:
        return next(responses)

    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        fake_validate
    )

    valid_articles, rejection_reasons = (
        module.validate_prepared_articles(
            articles,
            {}
        )
    )

    assert valid_articles == [
        {
            "id": "article-001"
        }
    ]
    assert rejection_reasons == Counter(
        {
            "missing_title": 2,
            "invalid_url": 1
        }
    )


def test_validate_prepared_articles_uses_unknown_reason_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy: (False, None)
    )

    valid_articles, rejection_reasons = (
        module.validate_prepared_articles(
            [
                {
                    "id": "article-001"
                }
            ],
            {}
        )
    )

    assert valid_articles == []
    assert rejection_reasons == Counter(
        {
            "raison_inconnue": 1
        }
    )


def test_validate_prepared_articles_preserves_article_identity(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "article-001"
    }

    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy: (True, None)
    )

    valid_articles, _ = module.validate_prepared_articles(
        [article],
        {}
    )

    assert valid_articles[0] is article


def test_validate_prepared_articles_returns_empty_for_empty_list() -> None:
    valid_articles, rejection_reasons = (
        module.validate_prepared_articles([], {})
    )

    assert valid_articles == []
    assert rejection_reasons == Counter()


# Préparation générale

def test_prepare_articles_returns_empty_result_for_non_list(
    caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        result = module.prepare_articles(None)  # type: ignore[arg-type]

    assert result.articles == []
    assert result.report.received == 0
    assert result.report.kept == 0
    assert "Collection d'articles invalide" in caplog.text


def test_prepare_articles_returns_empty_result_for_empty_list(
    caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        result = module.prepare_articles([])

    assert result.articles == []
    assert result.report.received == 0
    assert result.report.normalized == 0
    assert result.report.cleaned == 0
    assert result.report.kept == 0
    assert "Aucun article fourni au service métier." in caplog.text


def test_prepare_articles_returns_empty_when_normalization_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: ([], 2)
    )

    with caplog.at_level("WARNING"):
        result = module.prepare_articles(
            [
                None,
                "article invalide"
            ]
        )

    assert result.articles == []
    assert result.report.received == 2
    assert result.report.normalized == 0
    assert result.report.normalization_ignored == 2
    assert result.report.cleaned == 0
    assert "Aucun article exploitable après normalisation." in caplog.text


def test_prepare_articles_returns_empty_when_cleaning_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture
) -> None:
    normalized_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (normalized_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: []
    )

    with caplog.at_level("WARNING"):
        result = module.prepare_articles(
            [
                {
                    "raw": "article-001"
                },
                {
                    "raw": "article-002"
                }
            ]
        )

    assert result.articles == []
    assert result.report.received == 2
    assert result.report.normalized == 2
    assert result.report.cleaned == 0
    assert result.report.cleaning_ignored == 2
    assert "Aucun article exploitable après nettoyage." in caplog.text


def test_prepare_articles_runs_complete_pipeline(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_articles = [
        {
            "raw_id": "001"
        },
        {
            "raw_id": "002"
        },
        {
            "raw_id": "003"
        }
    ]
    normalized_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        },
        {
            "id": "article-002"
        }
    ]
    cleaned_articles = [
        {
            "id": "article-001",
            "title": "Premier article"
        },
        {
            "id": "article-002",
            "title": "Deuxième article"
        },
        {
            "id": "article-002",
            "title": "Deuxième article"
        }
    ]
    deduplicated_articles = [
        cleaned_articles[0],
        cleaned_articles[1]
    ]
    valid_articles = [
        cleaned_articles[0]
    ]
    validation_policy = {
        "require_title": True
    }
    rejection_reasons = Counter(
        {
            "invalid_url": 1
        }
    )

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: validation_policy
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (normalized_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: cleaned_articles
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: deduplicated_articles
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (
            valid_articles,
            rejection_reasons
        )
    )

    result = module.prepare_articles(raw_articles)

    assert result.articles == valid_articles
    assert result.report.received == 3
    assert result.report.normalized == 3
    assert result.report.normalization_ignored == 0
    assert result.report.cleaned == 3
    assert result.report.cleaning_ignored == 0
    assert result.report.duplicates == 1
    assert result.report.invalid == 1
    assert result.report.kept == 1
    assert result.report.rejection_reasons == rejection_reasons


def test_prepare_articles_disables_deduplication(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaned_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-001"
        }
    ]

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (cleaned_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: cleaned_articles
    )

    def fail_if_called(articles: list[dict[str, Any]]) -> None:
        pytest.fail("deduplicate_articles ne doit pas être appelée.")

    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        fail_if_called
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (articles, Counter())
    )

    result = module.prepare_articles(
        cleaned_articles,
        remove_duplicates=False
    )

    assert result.articles == cleaned_articles
    assert result.report.duplicates == 0
    assert result.report.kept == 2


def test_prepare_articles_passes_policy_name_and_overrides(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    overrides = {
        "require_text": True
    }

    def fake_get_validation_policy(
        policy_name: module.PreparationPolicyName,
        validation_overrides: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        captured["policy_name"] = policy_name
        captured["overrides"] = validation_overrides
        return {
            "require_text": True
        }

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        fake_get_validation_policy
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (
            [
                {
                    "id": "article-001"
                }
            ],
            0
        )
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (articles, Counter())
    )

    module.prepare_articles(
        [
            {
                "id": "article-001"
            }
        ],
        policy_name="labeled_reference",
        validation_overrides=overrides
    )

    assert captured == {
        "policy_name": "labeled_reference",
        "overrides": overrides
    }


def test_prepare_articles_calculates_cleaning_ignored(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    normalized_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        },
        {
            "id": "article-003"
        }
    ]
    cleaned_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (normalized_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: cleaned_articles
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (articles, Counter())
    )

    result = module.prepare_articles(normalized_articles)

    assert result.report.normalized == 3
    assert result.report.cleaned == 2
    assert result.report.cleaning_ignored == 1


def test_prepare_articles_never_sets_negative_cleaning_ignored(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    normalized_articles = [
        {
            "id": "article-001"
        }
    ]
    cleaned_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (normalized_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: cleaned_articles
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (articles, Counter())
    )

    result = module.prepare_articles(normalized_articles)

    assert result.report.cleaning_ignored == 0


def test_prepare_articles_logs_summary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture
) -> None:
    articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (articles, 0)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (
            [articles[0]],
            Counter({"invalid_url": 1})
        )
    )

    with caplog.at_level("INFO"):
        result = module.prepare_articles(articles)

    assert result.report.received == 2
    assert result.report.invalid == 1
    assert result.report.kept == 1
    assert (
        "Préparation terminée : reçus=2, normalisés=2, "
        "nettoyés=2, doublons=0, invalides=1, conservés=1."
        in caplog.text
    )
    assert "Motifs de rejet des articles" in caplog.text


def test_prepare_articles_logs_ignored_counts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture
) -> None:
    normalized_articles = [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]
    cleaned_articles = [
        normalized_articles[0]
    ]

    monkeypatch.setattr(
        module,
        "get_validation_policy",
        lambda policy_name, overrides: {}
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (normalized_articles, 1)
    )
    monkeypatch.setattr(
        module,
        "clean_articles",
        lambda articles: cleaned_articles
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "validate_prepared_articles",
        lambda articles, policy: (articles, Counter())
    )

    with caplog.at_level("WARNING"):
        result = module.prepare_articles(
            [
                {
                    "id": "article-001"
                },
                {
                    "id": "article-002"
                },
                None
            ]
        )

    assert result.report.normalization_ignored == 1
    assert result.report.cleaning_ignored == 1
    assert "1 élément(s) ignoré(s) pendant la normalisation." in caplog.text
    assert "1 article(s) ignoré(s) pendant le nettoyage." in caplog.text