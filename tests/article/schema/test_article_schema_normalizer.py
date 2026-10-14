"""Tests unitaires du normaliseur de schéma des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/schema/test_article_schema_normalizer.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import UserDict
from collections.abc import Mapping
from typing import Any

import pytest

from src.article.schema import article_schema_normalizer as module


# Normalisation des valeurs

@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("title", None, ""),
        ("text", None, ""),
        ("source", None, ""),
        ("url", None, ""),
        ("language", None, ""),
        ("title", "", ""),
        ("title", "Titre", "Titre"),
        ("published_at", 123, 123),
        ("label", False, False)
    ]
)
def test_normalize_standard_value_returns_expected_value(
    field: str,
    value: Any,
    expected: Any
) -> None:
    assert module.normalize_standard_value(field, value) == expected


def test_normalize_standard_value_returns_empty_metadata_for_none() -> None:
    assert module.normalize_standard_value("metadata", None) == {}


def test_normalize_standard_value_returns_empty_metadata_for_non_mapping() -> None:
    assert module.normalize_standard_value(
        "metadata",
        ["invalid"]
    ) == {}


def test_normalize_standard_value_copies_metadata_mapping() -> None:
    metadata = {
        "provider": "example",
        "score": 0.9
    }

    result = module.normalize_standard_value(
        "metadata",
        metadata
    )

    assert result == metadata
    assert result is not metadata


def test_normalize_standard_value_accepts_custom_metadata_mapping() -> None:
    metadata = UserDict(
        {
            "provider": "example"
        }
    )

    result = module.normalize_standard_value(
        "metadata",
        metadata
    )

    assert result == {
        "provider": "example"
    }
    assert isinstance(result, dict)


# Alias des champs

def test_apply_field_aliases_returns_dictionary_copy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )

    article = {
        "title": "Titre"
    }

    result = module.apply_field_aliases(article)

    assert result == article
    assert result is not article


def test_apply_field_aliases_copies_alias_to_missing_standard_field(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title"
        }
    )

    result = module.apply_field_aliases(
        {
            "headline": "Titre alias"
        }
    )

    assert result == {
        "headline": "Titre alias",
        "title": "Titre alias"
    }


@pytest.mark.parametrize(
    "standard_value",
    [
        None,
        ""
    ]
)
def test_apply_field_aliases_replaces_empty_standard_value(
    monkeypatch: pytest.MonkeyPatch,
    standard_value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title"
        }
    )

    result = module.apply_field_aliases(
        {
            "headline": "Titre alias",
            "title": standard_value
        }
    )

    assert result["title"] == "Titre alias"


@pytest.mark.parametrize(
    "standard_value",
    [
        "Titre standard",
        "   ",
        0,
        False,
        [],
        {}
    ]
)
def test_apply_field_aliases_does_not_replace_known_standard_value(
    monkeypatch: pytest.MonkeyPatch,
    standard_value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title"
        }
    )

    result = module.apply_field_aliases(
        {
            "headline": "Titre alias",
            "title": standard_value
        }
    )

    assert result["title"] == standard_value


def test_apply_field_aliases_sets_none_when_alias_is_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title"
        }
    )

    result = module.apply_field_aliases({})

    assert result == {
        "title": None
    }


def test_apply_field_aliases_applies_multiple_aliases(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title",
            "body": "text",
            "publisher": "source"
        }
    )

    result = module.apply_field_aliases(
        {
            "headline": "Titre",
            "body": "Contenu",
            "publisher": "Source"
        }
    )

    assert result == {
        "headline": "Titre",
        "body": "Contenu",
        "publisher": "Source",
        "title": "Titre",
        "text": "Contenu",
        "source": "Source"
    }


def test_apply_field_aliases_accepts_custom_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title"
        }
    )

    article: Mapping[str, Any] = UserDict(
        {
            "headline": "Titre"
        }
    )

    result = module.apply_field_aliases(article)

    assert result["title"] == "Titre"
    assert isinstance(result, dict)


# Extraction du texte brut

def test_get_raw_article_text_returns_first_non_empty_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content",
            "description"
        )
    )

    article = {
        "text": "",
        "content": "Contenu principal",
        "description": "Description"
    }

    assert module.get_raw_article_text(article) == "Contenu principal"


def test_get_raw_article_text_ignores_none_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content"
        )
    )

    article = {
        "text": None,
        "content": "Contenu"
    }

    assert module.get_raw_article_text(article) == "Contenu"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "\n\t"
    ]
)
def test_get_raw_article_text_ignores_blank_strings(
    monkeypatch: pytest.MonkeyPatch,
    value: str
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content"
        )
    )

    article = {
        "text": value,
        "content": "Contenu"
    }

    assert module.get_raw_article_text(article) == "Contenu"


@pytest.mark.parametrize(
    "value",
    [
        0,
        False,
        [],
        {},
        123
    ]
)
def test_get_raw_article_text_returns_first_non_string_value(
    monkeypatch: pytest.MonkeyPatch,
    value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content"
        )
    )

    article = {
        "text": value,
        "content": "Contenu"
    }

    assert module.get_raw_article_text(article) == value


def test_get_raw_article_text_returns_empty_when_no_value_exists(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content"
        )
    )

    assert module.get_raw_article_text({}) == ""


# Normalisation de la source

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", "")
    ]
)
def test_normalize_source_returns_empty_for_missing_value(
    value: Any,
    expected: str
) -> None:
    assert module.normalize_source(value) == expected


def test_normalize_source_converts_value_to_string(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_normalize_source_name(value: str) -> str:
        captured["value"] = value
        return "source-123"

    monkeypatch.setattr(
        module,
        "normalize_source_name",
        fake_normalize_source_name
    )

    result = module.normalize_source(123)

    assert result == "source-123"
    assert captured["value"] == "123"


def test_normalize_source_strips_value_before_normalization(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_normalize_source_name(value: str) -> str:
        captured["value"] = value
        return "source-name"

    monkeypatch.setattr(
        module,
        "normalize_source_name",
        fake_normalize_source_name
    )

    result = module.normalize_source("  Source Name  ")

    assert result == "source-name"
    assert captured["value"] == "Source Name"


def test_normalize_source_returns_normalized_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "bbc-news"
    )

    assert module.normalize_source("BBC News") == "bbc-news"


@pytest.mark.parametrize(
    "normalized_value",
    [
        None,
        ""
    ]
)
def test_normalize_source_returns_empty_when_normalizer_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    normalized_value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: normalized_value
    )

    assert module.normalize_source("Source") == ""


@pytest.mark.parametrize(
    "error",
    [
        TypeError("erreur simulée"),
        ValueError("erreur simulée"),
        AttributeError("erreur simulée")
    ]
)
def test_normalize_source_handles_string_conversion_error(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception
) -> None:
    class InvalidSource:
        def __str__(self) -> str:
            raise error

    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: logged_messages.append(args)
    )

    result = module.normalize_source(InvalidSource())

    assert result == ""
    assert logged_messages == [
        (
            "Nom de source impossible à convertir : %s",
            error
        )
    ]


@pytest.mark.parametrize(
    "error",
    [
        TypeError("erreur simulée"),
        ValueError("erreur simulée"),
        AttributeError("erreur simulée")
    ]
)
def test_normalize_source_handles_normalization_error(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception
) -> None:
    logged_messages: list[tuple[Any, ...]] = []

    def raise_error(value: str) -> str:
        raise error

    monkeypatch.setattr(
        module,
        "normalize_source_name",
        raise_error
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: logged_messages.append(args)
    )

    result = module.normalize_source("Source")

    assert result == ""
    assert logged_messages == [
        (
            "Nom de source impossible à normaliser : %s",
            error
        )
    ]


# Normalisation d'un article

@pytest.mark.parametrize(
    "article",
    [
        None,
        "article",
        123,
        [],
        ()
    ]
)
def test_normalize_article_schema_returns_empty_for_non_mapping(
    article: Any
) -> None:
    assert module.normalize_article_schema(article) == {}


def test_normalize_article_schema_builds_expected_standard_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "id",
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title",
            "content": "text"
        }
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content"
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: value.lower().replace(" ", "_")
    )

    result = module.normalize_article_schema(
        {
            "id": "article-001",
            "source": "BBC News",
            "headline": "Titre",
            "content": "Contenu",
            "metadata": {
                "provider": "rss"
            }
        }
    )

    assert result == {
        "id": "article-001",
        "source": "bbc_news",
        "title": "Titre",
        "text": "Contenu",
        "metadata": {
            "provider": "rss"
        }
    }


def test_normalize_article_schema_preserves_standard_field_order(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: value
    )

    result = module.normalize_article_schema(
        {
            "title": "Titre",
            "source": "Source",
            "text": "Contenu"
        }
    )

    assert list(result) == [
        "source",
        "title",
        "text",
        "metadata"
    ]


def test_normalize_article_schema_fills_missing_standard_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )

    result = module.normalize_article_schema({})

    assert result == {
        "source": "",
        "title": "",
        "text": "",
        "metadata": {}
    }


def test_normalize_article_schema_uses_first_available_text_field(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "content",
            "description"
        )
    )

    result = module.normalize_article_schema(
        {
            "text": " ",
            "content": "Contenu principal",
            "description": "Description"
        }
    )

    assert result["text"] == "Contenu principal"


def test_normalize_article_schema_moves_unknown_fields_to_metadata(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )

    result = module.normalize_article_schema(
        {
            "source": "",
            "title": "Titre",
            "text": "Contenu",
            "custom_field": "custom value",
            "score": 0.9
        }
    )

    assert result["metadata"] == {
        "custom_field": "custom value",
        "score": 0.9
    }


def test_normalize_article_schema_preserves_existing_metadata_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )

    result = module.normalize_article_schema(
        {
            "source": "",
            "text": "Contenu",
            "metadata": {
                "custom_field": "metadata value"
            },
            "custom_field": "article value"
        }
    )

    assert result["metadata"] == {
        "custom_field": "metadata value"
    }


def test_normalize_article_schema_does_not_store_aliases_in_metadata(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title",
            "body": "text"
        }
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "body"
        )
    )

    result = module.normalize_article_schema(
        {
            "headline": "Titre",
            "body": "Contenu"
        }
    )

    assert result["title"] == "Titre"
    assert result["text"] == "Contenu"
    assert "headline" not in result["metadata"]
    assert "body" not in result["metadata"]


def test_normalize_article_schema_ignores_non_string_metadata_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )

    result = module.normalize_article_schema(
        {
            "text": "Contenu",
            123: "Valeur ignorée"
        }
    )

    assert result["metadata"] == {}


def test_normalize_article_schema_does_not_modify_input(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {
            "headline": "title"
        }
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )

    article = {
        "source": "",
        "headline": "Titre",
        "text": "Contenu",
        "metadata": {
            "provider": "rss"
        }
    }
    original_article = {
        "source": "",
        "headline": "Titre",
        "text": "Contenu",
        "metadata": {
            "provider": "rss"
        }
    }

    module.normalize_article_schema(article)

    assert article == original_article


def test_normalize_article_schema_accepts_custom_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "source",
            "title",
            "text",
            "metadata"
        )
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_FIELD_ALIASES",
        {}
    )
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        ("text",)
    )

    article: Mapping[str, Any] = UserDict(
        {
            "title": "Titre",
            "text": "Contenu"
        }
    )

    result = module.normalize_article_schema(article)

    assert result["title"] == "Titre"
    assert result["text"] == "Contenu"
    assert isinstance(result, dict)


# Normalisation d'une collection

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
def test_normalize_articles_schema_returns_empty_for_non_list(
    articles: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: logged_messages.append(args)
    )

    result = module.normalize_articles_schema(articles)

    assert result == ([], 0)
    assert logged_messages == [
        (
            "Collection d'articles invalide : %s.",
            type(articles).__name__
        )
    ]


def test_normalize_articles_schema_returns_empty_for_empty_list() -> None:
    assert module.normalize_articles_schema([]) == ([], 0)


def test_normalize_articles_schema_normalizes_all_articles(
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

    monkeypatch.setattr(
        module,
        "normalize_article_schema",
        lambda article: {
            **article,
            "normalized": True
        }
    )

    normalized_articles, ignored_count = (
        module.normalize_articles_schema(articles)
    )

    assert normalized_articles == [
        {
            "id": "article-001",
            "normalized": True
        },
        {
            "id": "article-002",
            "normalized": True
        }
    ]
    assert ignored_count == 0


def test_normalize_articles_schema_ignores_empty_results(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "article-001"
        },
        None,
        {
            "id": "article-002"
        }
    ]

    def fake_normalize(article: Any) -> dict[str, Any]:
        if article is None:
            return {}

        return dict(article)

    monkeypatch.setattr(
        module,
        "normalize_article_schema",
        fake_normalize
    )

    normalized_articles, ignored_count = (
        module.normalize_articles_schema(articles)
    )

    assert normalized_articles == [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]
    assert ignored_count == 1


@pytest.mark.parametrize(
    "error_type",
    [
        TypeError,
        ValueError,
        AttributeError,
        RuntimeError
    ]
)
def test_normalize_articles_schema_handles_supported_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception]
) -> None:
    error = error_type("erreur simulée")
    logged_messages: list[tuple[Any, ...]] = []

    def fake_normalize(article: Any) -> dict[str, Any]:
        if article == "invalid":
            raise error

        return {
            "id": article
        }

    monkeypatch.setattr(
        module,
        "normalize_article_schema",
        fake_normalize
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: logged_messages.append(args)
    )

    normalized_articles, ignored_count = (
        module.normalize_articles_schema(
            [
                "article-001",
                "invalid",
                "article-002"
            ]
        )
    )

    assert normalized_articles == [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]
    assert ignored_count == 1
    assert logged_messages == [
        (
            "Article %s impossible à normaliser : %s",
            1,
            error
        )
    ]


def test_normalize_articles_schema_preserves_article_order(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_article_schema",
        lambda article: {
            "id": article["id"]
        }
    )

    normalized_articles, ignored_count = (
        module.normalize_articles_schema(
            [
                {
                    "id": "article-003"
                },
                {
                    "id": "article-001"
                },
                {
                    "id": "article-002"
                }
            ]
        )
    )

    assert normalized_articles == [
        {
            "id": "article-003"
        },
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]
    assert ignored_count == 0


def test_normalize_articles_schema_counts_multiple_ignored_items(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_normalize(article: Any) -> dict[str, Any]:
        if article == "error":
            raise ValueError("erreur simulée")

        if article == "empty":
            return {}

        return {
            "id": article
        }

    monkeypatch.setattr(
        module,
        "normalize_article_schema",
        fake_normalize
    )

    normalized_articles, ignored_count = (
        module.normalize_articles_schema(
            [
                "article-001",
                "error",
                "empty",
                "article-002"
            ]
        )
    )

    assert normalized_articles == [
        {
            "id": "article-001"
        },
        {
            "id": "article-002"
        }
    ]
    assert ignored_count == 2


def test_normalize_articles_schema_does_not_catch_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(article: Any) -> dict[str, Any]:
        raise KeyError("erreur inattendue")

    monkeypatch.setattr(
        module,
        "normalize_article_schema",
        raise_error
    )

    with pytest.raises(
        KeyError,
        match="erreur inattendue"
    ):
        module.normalize_articles_schema(
            [
                {
                    "id": "article-001"
                }
            ]
        )