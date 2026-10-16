"""Tests unitaires des transformations métier des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/processing/test_article_transformer.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.article.processing import article_transformer as module


# Données de test

@pytest.fixture
def complete_article() -> dict[str, Any]:
    """Retourne un article complet contenant des valeurs à normaliser."""

    return {
        "id": "article-001",
        "source": "Source test",
        "title": "Titre de test",
        "text": "Contenu principal",
        "language": " French ",
        "category": " Politique, Économie, politique ",
        "dataset_role": " test_role ",
        "url": "https://example.com/article"
    }


# Normalisation des langues

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("fr", "fr"),
        ("FR", "fr"),
        (" French ", "fr"),
        ("fr-FR", "fr"),
        ("fr-fr", "fr"),
        ("en", "en"),
        ("EN", "en"),
        ("English", "en"),
        ("en-US", "en"),
        ("de-DE", ""),
        ("unknown-language", "")
    ]
)
def test_normalize_language_returns_expected_value(
    value: Any,
    expected: str
) -> None:
    assert module.normalize_language(value) == expected


def test_normalize_language_uses_language_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "LANGUAGE_MAPPING",
        {"français": "fr"}
    )
    monkeypatch.setattr(
        module,
        "SUPPORTED_LANGUAGES",
        {"fr"}
    )

    assert module.normalize_language(" Français ") == "fr"


def test_normalize_language_returns_empty_for_unsupported_language(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "LANGUAGE_MAPPING", {})
    monkeypatch.setattr(module, "SUPPORTED_LANGUAGES", {"fr", "en"})

    assert module.normalize_language("de-DE") == ""


# Normalisation des catégories

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Politique", "politique"),
        (" Politique ", "politique"),
        ("Politique, Économie", "politique, économie"),
        (
            "Politique, Économie, politique",
            "politique, économie"
        ),
        ("politique,,économie", "politique, économie")
    ]
)
def test_normalize_category_returns_expected_value(
    value: Any,
    expected: str
) -> None:
    assert module.normalize_category(value) == expected


def test_normalize_category_removes_empty_category_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "EMPTY_CATEGORY_VALUES",
        {"", "none", "unknown"}
    )

    result = module.normalize_category(
        "Politique, unknown, none, Économie"
    )

    assert result == "politique, économie"


def test_normalize_category_preserves_first_occurrence_order() -> None:
    result = module.normalize_category(
        "science, politique, science, économie, politique"
    )

    assert result == "science, politique, économie"


def test_normalize_category_accepts_non_string_value() -> None:
    assert module.normalize_category(123) == "123"


# Normalisation des rôles

@pytest.mark.parametrize(
    ("value", "supported_roles", "default_role", "expected"),
    [
        (
            "test_role",
            {"test_role"},
            "acquisition",
            "test_role"
        ),
        (
            " TEST_ROLE ",
            {"test_role"},
            "acquisition",
            "test_role"
        ),
        (
            "unknown",
            {"test_role"},
            "acquisition",
            "acquisition"
        ),
        (
            None,
            {"test_role"},
            "acquisition",
            "acquisition"
        )
    ]
)
def test_normalize_dataset_role_returns_expected_value(
    monkeypatch: pytest.MonkeyPatch,
    value: Any,
    supported_roles: set[str],
    default_role: str,
    expected: str
) -> None:
    monkeypatch.setattr(module, "DATASET_ROLES", supported_roles)
    monkeypatch.setattr(module, "DEFAULT_DATASET_ROLE", default_role)

    assert module.normalize_dataset_role(value) == expected


# Transformation d'un article

def test_transform_article_returns_new_dictionary(
    monkeypatch: pytest.MonkeyPatch,
    complete_article: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        module,
        "LANGUAGE_MAPPING",
        {"french": "fr"}
    )
    monkeypatch.setattr(
        module,
        "SUPPORTED_LANGUAGES",
        {"fr"}
    )
    monkeypatch.setattr(
        module,
        "DATASET_ROLES",
        {"test_role"}
    )

    result = module.transform_article(complete_article)

    assert result is not complete_article
    assert result == {
        "id": "article-001",
        "source": "Source test",
        "title": "Titre de test",
        "text": "Contenu principal",
        "language": "fr",
        "category": "politique, économie",
        "role": "test_role",
        "url": "https://example.com/article"
    }


def test_transform_article_does_not_modify_original_article(
    complete_article: dict[str, Any]
) -> None:
    original_article = dict(complete_article)

    module.transform_article(complete_article)

    assert complete_article == original_article


def test_transform_article_uses_role_before_dataset_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "DATASET_ROLES",
        {"acquisition", "test_role"}
    )

    article = {
        "role": "acquisition",
        "dataset_role": "test_role"
    }

    result = module.transform_article(article)

    assert result["role"] == "acquisition"
    assert "dataset_role" not in result


def test_transform_article_uses_dataset_role_when_role_is_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "DATASET_ROLES",
        {"test_role"}
    )

    article = {
        "dataset_role": "test_role"
    }

    result = module.transform_article(article)

    assert result["role"] == "test_role"
    assert "dataset_role" not in result


def test_transform_article_uses_dataset_role_when_role_is_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "DATASET_ROLES",
        {"test_role"}
    )

    article = {
        "role": "",
        "dataset_role": "test_role"
    }

    result = module.transform_article(article)

    assert result["role"] == "test_role"
    assert "dataset_role" not in result


def test_transform_article_adds_normalized_fields_when_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "DEFAULT_DATASET_ROLE", "acquisition")
    monkeypatch.setattr(module, "DATASET_ROLES", {"acquisition"})

    result = module.transform_article(
        {
            "id": "article-001",
            "title": "Titre"
        }
    )

    assert result == {
        "id": "article-001",
        "title": "Titre",
        "language": "",
        "category": "",
        "role": "acquisition"
    }


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
def test_transform_article_rejects_non_mapping(article: Any) -> None:
    with pytest.raises(
        TypeError,
        match="article doit être une structure de type Mapping"
    ):
        module.transform_article(article)


def test_transform_article_accepts_custom_mapping() -> None:
    class CustomMapping(dict[str, Any]):
        """Mapping personnalisé utilisé pour vérifier le contrat."""

    article: Mapping[str, Any] = CustomMapping(
        {
            "language": "",
            "category": ""
        }
    )

    result = module.transform_article(article)

    assert isinstance(result, dict)


# Transformation d'une collection

def test_transform_articles_returns_transformed_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "article-001",
            "language": "French",
            "category": "Politique",
            "role": "acquisition"
        },
        {
            "id": "article-002",
            "language": "English",
            "category": "Science",
            "role": "acquisition"
        }
    ]

    monkeypatch.setattr(
        module,
        "LANGUAGE_MAPPING",
        {
            "french": "fr",
            "english": "en"
        }
    )
    monkeypatch.setattr(
        module,
        "SUPPORTED_LANGUAGES",
        {"fr", "en"}
    )
    monkeypatch.setattr(
        module,
        "DATASET_ROLES",
        {"acquisition"}
    )

    result = module.transform_articles(articles)

    assert result == [
        {
            "id": "article-001",
            "language": "fr",
            "category": "politique",
            "role": "acquisition"
        },
        {
            "id": "article-002",
            "language": "en",
            "category": "science",
            "role": "acquisition"
        }
    ]


def test_transform_articles_returns_new_dictionaries() -> None:
    articles = [
        {
            "id": "article-001",
            "role": "acquisition"
        }
    ]

    result = module.transform_articles(articles)

    assert result is not articles
    assert result[0] is not articles[0]


def test_transform_articles_returns_empty_list_for_empty_collection(
    caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO"):
        result = module.transform_articles([])

    assert result == []
    assert "Aucun article à transformer." in caplog.text


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
def test_transform_articles_rejects_non_list(articles: Any) -> None:
    with pytest.raises(
        TypeError,
        match="articles doit être une liste"
    ):
        module.transform_articles(articles)


def test_transform_articles_rejects_invalid_items() -> None:
    articles = [
        {"id": "article-001"},
        None,
        {"id": "article-002"},
        "article invalide",
        123
    ]

    with pytest.raises(
        TypeError,
        match=(
            "La collection contient des articles non normalisés "
            r"aux index : \[1, 3, 4\]"
        )
    ):
        module.transform_articles(articles)


def test_transform_articles_limits_invalid_indexes_in_error_message() -> None:
    articles: list[Any] = [None] * 15

    with pytest.raises(TypeError) as error:
        module.transform_articles(articles)

    assert "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]" in str(error.value)
    assert "10" not in str(error.value)


def test_transform_articles_logs_transformation_summary(
    caplog: pytest.LogCaptureFixture
) -> None:
    articles = [
        {"id": "article-001"},
        {"id": "article-002"}
    ]

    with caplog.at_level("INFO"):
        result = module.transform_articles(articles)

    assert len(result) == 2
    assert "2 article(s) transformé(s) sur 2 élément(s)." in caplog.text