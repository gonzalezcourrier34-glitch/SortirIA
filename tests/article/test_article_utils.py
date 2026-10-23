"""Tests unitaires des fonctions utilitaires communes aux articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/test_article_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import UserDict
from collections.abc import Mapping
from typing import Any

import pytest

from src.article import article_utils as module

# Première valeur disponible

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
def test_get_first_article_value_returns_empty_for_non_mapping(
    article: Any
) -> None:
    assert module.get_first_article_value(
        article,
        ("id", "url")
    ) == ""


def test_get_first_article_value_returns_first_non_empty_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    article = {
        "id": "   ",
        "url": " https://example.com/article ",
        "title": "Titre"
    }

    result = module.get_first_article_value(
        article,
        (
            "id",
            "url",
            "title"
        )
    )

    assert result == "https://example.com/article"


def test_get_first_article_value_respects_field_order(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    article = {
        "id": "article-001",
        "url": "https://example.com/article"
    }

    result = module.get_first_article_value(
        article,
        (
            "url",
            "id"
        )
    )

    assert result == "https://example.com/article"


def test_get_first_article_value_returns_empty_when_no_value_exists(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: ""
    )

    assert module.get_first_article_value(
        {
            "id": None,
            "url": ""
        },
        (
            "id",
            "url"
        )
    ) == ""


def test_get_first_article_value_calls_normalize_whitespace(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_normalize_whitespace(value: Any) -> str:
        calls.append(value)
        return "normalized" if value == "usable" else ""

    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        fake_normalize_whitespace
    )

    result = module.get_first_article_value(
        {
            "id": None,
            "url": "usable",
            "title": "ignored"
        },
        (
            "id",
            "url",
            "title"
        )
    )

    assert result == "normalized"
    assert calls == [
        None,
        "usable"
    ]


def test_get_first_article_value_accepts_custom_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    article: Mapping[str, Any] = UserDict(
        {
            "id": "article-001"
        }
    )

    assert module.get_first_article_value(
        article,
        ("id",)
    ) == "article-001"


# Identifiant

def test_get_article_identifier_returns_first_identifier(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_IDENTIFIER_FIELDS",
        (
            "id",
            "url",
            "title"
        )
    )
    monkeypatch.setattr(
        module,
        "get_first_article_value",
        lambda article, fields: "article-001"
    )

    assert module.get_article_identifier({}) == "article-001"


def test_get_article_identifier_limits_identifier_to_80_characters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = "x" * 100

    monkeypatch.setattr(
        module,
        "get_first_article_value",
        lambda article, fields: identifier
    )

    result = module.get_article_identifier({})

    assert result == "x" * 80
    assert len(result) == 80


def test_get_article_identifier_accepts_identifier_at_limit(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = "x" * 80

    monkeypatch.setattr(
        module,
        "get_first_article_value",
        lambda article, fields: identifier
    )

    assert module.get_article_identifier({}) == identifier


def test_get_article_identifier_returns_default_when_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_first_article_value",
        lambda article, fields: ""
    )
    monkeypatch.setattr(
        module,
        "DEFAULT_ARTICLE_IDENTIFIER",
        "article-inconnu"
    )

    assert module.get_article_identifier({}) == "article-inconnu"


def test_get_article_identifier_uses_identifier_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []
    article = {
        "id": "article-001"
    }

    monkeypatch.setattr(
        module,
        "ARTICLE_IDENTIFIER_FIELDS",
        (
            "id",
            "url"
        )
    )

    def fake_get_first_article_value(
        supplied_article: Mapping[str, Any],
        fields: tuple[str, ...]
    ) -> str:
        captured.append(
            (
                supplied_article,
                fields
            )
        )
        return "article-001"

    monkeypatch.setattr(
        module,
        "get_first_article_value",
        fake_get_first_article_value
    )

    result = module.get_article_identifier(article)

    assert result == "article-001"
    assert captured == [
        (
            article,
            (
                "id",
                "url"
            )
        )
    ]


# Texte

def test_get_article_text_uses_text_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "text": "Contenu"
    }
    captured: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []

    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "summary"
        )
    )

    def fake_get_first_article_value(
        supplied_article: Mapping[str, Any],
        fields: tuple[str, ...]
    ) -> str:
        captured.append(
            (
                supplied_article,
                fields
            )
        )
        return "Contenu"

    monkeypatch.setattr(
        module,
        "get_first_article_value",
        fake_get_first_article_value
    )

    result = module.get_article_text(article)

    assert result == "Contenu"
    assert captured == [
        (
            article,
            (
                "text",
                "summary"
            )
        )
    ]


def test_get_article_text_returns_first_available_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_TEXT_FIELDS",
        (
            "text",
            "summary",
            "description"
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    result = module.get_article_text(
        {
            "text": "",
            "summary": "Résumé",
            "description": "Description"
        }
    )

    assert result == "Résumé"


# Source

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
def test_get_article_source_returns_empty_for_non_mapping(
    article: Any
) -> None:
    assert module.get_article_source(article) == ""


def test_get_article_source_normalizes_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    def fake_normalize_whitespace(value: Any) -> str:
        captured.append(value)
        return "Source normalisée"

    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        fake_normalize_whitespace
    )

    result = module.get_article_source(
        {
            "source": "  Source brute  "
        }
    )

    assert result == "Source normalisée"
    assert captured == [
        "  Source brute  "
    ]


def test_get_article_source_returns_empty_when_source_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: ""
    )

    assert module.get_article_source({}) == ""


def test_get_article_source_accepts_custom_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    article: Mapping[str, Any] = UserDict(
        {
            "source": "Source"
        }
    )

    assert module.get_article_source(article) == "Source"


# URL

def test_get_article_url_uses_url_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "url": "https://example.com/article"
    }
    captured: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []

    monkeypatch.setattr(
        module,
        "ARTICLE_URL_FIELDS",
        (
            "url",
            "link"
        )
    )

    def fake_get_first_article_value(
        supplied_article: Mapping[str, Any],
        fields: tuple[str, ...]
    ) -> str:
        captured.append(
            (
                supplied_article,
                fields
            )
        )
        return "https://example.com/article"

    monkeypatch.setattr(
        module,
        "get_first_article_value",
        fake_get_first_article_value
    )

    result = module.get_article_url(article)

    assert result == "https://example.com/article"
    assert captured == [
        (
            article,
            (
                "url",
                "link"
            )
        )
    ]


def test_get_article_url_returns_link_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ARTICLE_URL_FIELDS",
        (
            "url",
            "link"
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    result = module.get_article_url(
        {
            "url": "",
            "link": "https://example.com/fallback"
        }
    )

    assert result == "https://example.com/fallback"


# Rôle

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
def test_get_article_role_returns_empty_for_non_mapping(
    article: Any
) -> None:
    assert module.get_article_role(article) == ""


def test_get_article_role_returns_standard_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    result = module.get_article_role(
        {
            "role": "  Partenaire "
        }
    )

    assert result == "partenaire"


def test_get_article_role_uses_dataset_role_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    result = module.get_article_role(
        {
            "role": "",
            "dataset_role": " Agenda_Local "
        }
    )

    assert result == "agenda_local"


def test_get_article_role_prioritizes_standard_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    result = module.get_article_role(
        {
            "role": "Acquisition",
            "dataset_role": "Partenaire"
        }
    )

    assert result == "acquisition"


def test_get_article_role_returns_empty_when_no_role_exists(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: ""
    )

    assert module.get_article_role({}) == ""


def test_get_article_role_calls_casefold(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: "Straße"
    )

    assert module.get_article_role(
        {
            "role": "ignored"
        }
    ) == "strasse"


# Référence d'image

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
def test_has_image_reference_returns_false_for_non_mapping(
    article: Any
) -> None:
    assert module.has_image_reference(article) is False


@pytest.mark.parametrize(
    ("image_url", "image_path", "expected"),
    [
        ("https://example.com/image.jpg", "", True),
        ("", "images/article.jpg", True),
        ("https://example.com/image.jpg", "images/article.jpg", True),
        ("", "", False),
        ("   ", "   ", False),
        (None, None, False)
    ]
)
def test_has_image_reference_returns_expected_value(
    monkeypatch: pytest.MonkeyPatch,
    image_url: Any,
    image_path: Any,
    expected: bool
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: str(value).strip() if value is not None else ""
    )

    article = {
        "image_url": image_url,
        "image_path": image_path
    }

    assert module.has_image_reference(article) is expected


def test_has_image_reference_stops_after_valid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_normalize_whitespace(value: Any) -> str:
        calls.append(value)
        return "image" if value == "url" else ""

    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        fake_normalize_whitespace
    )

    result = module.has_image_reference(
        {
            "image_url": "url",
            "image_path": "path"
        }
    )

    assert result is True
    assert calls == [
        "url"
    ]


# Valeurs vides

def test_is_empty_value_returns_true_when_value_is_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_is_missing_value(value: Any) -> bool:
        calls.append(value)
        return True

    monkeypatch.setattr(
        module,
        "is_missing_value",
        fake_is_missing_value
    )

    assert module.is_empty_value("value") is True
    assert calls == [
        "value"
    ]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "\n\t"
    ]
)
def test_is_empty_value_returns_true_for_blank_string(
    monkeypatch: pytest.MonkeyPatch,
    value: str
) -> None:
    monkeypatch.setattr(
        module,
        "is_missing_value",
        lambda supplied_value: False
    )

    assert module.is_empty_value(value) is True


def test_is_empty_value_returns_false_for_non_empty_string(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_missing_value",
        lambda supplied_value: False
    )

    assert module.is_empty_value(" value ") is False


@pytest.mark.parametrize(
    "value",
    [
        [],
        (),
        set(),
        frozenset(),
        {}
    ]
)
def test_is_empty_value_returns_true_for_empty_collection(
    monkeypatch: pytest.MonkeyPatch,
    value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "is_missing_value",
        lambda supplied_value: False
    )

    assert module.is_empty_value(value) is True


@pytest.mark.parametrize(
    "value",
    [
        [1],
        (1,),
        {1},
        frozenset({1}),
        {
            "key": "value"
        }
    ]
)
def test_is_empty_value_returns_false_for_non_empty_collection(
    monkeypatch: pytest.MonkeyPatch,
    value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "is_missing_value",
        lambda supplied_value: False
    )

    assert module.is_empty_value(value) is False


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        False,
        True,
        object()
    ]
)
def test_is_empty_value_returns_false_for_other_values(
    monkeypatch: pytest.MonkeyPatch,
    value: Any
) -> None:
    monkeypatch.setattr(
        module,
        "is_missing_value",
        lambda supplied_value: False
    )

    assert module.is_empty_value(value) is False


def test_is_empty_value_stops_when_missing_value_detected(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class InvalidString:
        def strip(self) -> str:
            pytest.fail("strip ne doit pas être appelée.")

    monkeypatch.setattr(
        module,
        "is_missing_value",
        lambda value: True
    )

    assert module.is_empty_value(InvalidString()) is True


# Valeurs supprimées

@pytest.mark.parametrize(
    ("normalized_value", "expected"),
    [
        ("[deleted]", True),
        ("[removed]", True),
        ("deleted", False),
        ("", False)
    ]
)
def test_is_deleted_value_returns_expected_value(
    monkeypatch: pytest.MonkeyPatch,
    normalized_value: str,
    expected: bool
) -> None:
    monkeypatch.setattr(
        module,
        "DELETED_ARTICLE_VALUES",
        {
            "[deleted]",
            "[removed]"
        }
    )
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: normalized_value
    )

    assert module.is_deleted_value("raw value") is expected


def test_is_deleted_value_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "DELETED_ARTICLE_VALUES",
        {
            "[deleted]"
        }
    )
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: "[DELETED]"
    )

    assert module.is_deleted_value("raw value") is True


def test_is_deleted_value_uses_casefold(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "DELETED_ARTICLE_VALUES",
        {
            "strasse"
        }
    )
    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        lambda value: "Straße"
    )

    assert module.is_deleted_value("raw value") is True


def test_is_deleted_value_passes_value_to_normalizer(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    def fake_normalize_whitespace(value: Any) -> str:
        captured.append(value)
        return "[deleted]"

    monkeypatch.setattr(
        module,
        "normalize_whitespace",
        fake_normalize_whitespace
    )
    monkeypatch.setattr(
        module,
        "DELETED_ARTICLE_VALUES",
        {
            "[deleted]"
        }
    )

    result = module.is_deleted_value(
        {
            "unexpected": "value"
        }
    )

    assert result is True
    assert captured == [
        {
            "unexpected": "value"
        }
    ]