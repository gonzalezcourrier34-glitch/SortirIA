"""Tests des utilitaires textuels pour les datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/datasets/test_dataset_text_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest

import src.extractors.datasets.dataset_text_utils as module


# get_dataset_title

def test_get_dataset_title_returns_cleaned_first_available_title(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {
        "headline": "",
        "title": "  Un titre exploitable  "
    }

    monkeypatch.setattr(
        module,
        "get_value",
        lambda value, fields: value["title"]
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: value.strip()
    )

    result = module.get_dataset_title(
        item,
        ("headline", "title")
    )

    assert result == "Un titre exploitable"


def test_get_dataset_title_passes_item_and_fields_to_get_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {
        "title": "Article"
    }
    title_fields = (
        "headline",
        "title"
    )
    received: dict[str, Any] = {}

    def fake_get_value(
        received_item: Any,
        received_fields: Any
    ) -> str:
        received["item"] = received_item
        received["fields"] = received_fields
        return "Article"

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: value
    )

    result = module.get_dataset_title(
        item,
        title_fields
    )

    assert result == "Article"
    assert received == {
        "item": item,
        "fields": title_fields
    }


def test_get_dataset_title_passes_value_to_clean_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: "  Article brut  "
    )

    def fake_clean_text(value: Any) -> str:
        received["value"] = value
        return "Article nettoyé"

    monkeypatch.setattr(
        module,
        "clean_text",
        fake_clean_text
    )

    result = module.get_dataset_title(
        {
            "title": "  Article brut  "
        },
        ("title",)
    )

    assert result == "Article nettoyé"
    assert received["value"] == "  Article brut  "


def test_get_dataset_title_returns_empty_string_when_value_is_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: None
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: ""
    )

    result = module.get_dataset_title(
        {},
        ("title",)
    )

    assert result == ""


@pytest.mark.parametrize(
    "invalid_item",
    [
        None,
        [],
        (),
        "article",
        42,
        3.14,
        True
    ]
)
def test_get_dataset_title_rejects_non_mapping_items(
    invalid_item: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="item doit être une structure de données associative"
    ):
        module.get_dataset_title(
            invalid_item,
            ("title",)
        )


def test_get_dataset_title_accepts_empty_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: ""
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: ""
    )

    result = module.get_dataset_title(
        {},
        ("title",)
    )

    assert result == ""


def test_get_dataset_title_accepts_empty_field_sequence(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def fake_get_value(
        item: Any,
        fields: Any
    ) -> str:
        received["fields"] = fields
        return ""

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: ""
    )

    result = module.get_dataset_title(
        {},
        ()
    )

    assert result == ""
    assert received["fields"] == ()


# get_dataset_text

def test_get_dataset_text_returns_primary_text_when_available(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {
        "text": "Texte principal",
        "description": "Texte de secours"
    }

    def fake_get_value(
        value: Any,
        fields: Any
    ) -> str:
        if fields == ("text",):
            return value["text"]
        return value["description"]

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: value.strip()
    )

    result = module.get_dataset_text(
        item,
        ("text",),
        ("description",)
    )

    assert result == "Texte principal"


def test_get_dataset_text_uses_fallback_when_primary_text_is_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {
        "text": "",
        "description": "Texte de secours"
    }

    def fake_get_value(
        value: Any,
        fields: Any
    ) -> str:
        if fields == ("text",):
            return value["text"]
        return value["description"]

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: value.strip()
    )

    result = module.get_dataset_text(
        item,
        ("text",),
        ("description",)
    )

    assert result == "Texte de secours"


def test_get_dataset_text_uses_fallback_when_primary_cleaning_returns_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    values = iter(
        [
            "contenu supprimé",
            "description exploitable"
        ]
    )
    cleaned_values = iter(
        [
            "",
            "Description exploitable"
        ]
    )

    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: next(values)
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: next(cleaned_values)
    )

    result = module.get_dataset_text(
        {
            "text": "contenu supprimé",
            "description": "description exploitable"
        },
        ("text",),
        ("description",)
    )

    assert result == "Description exploitable"


def test_get_dataset_text_does_not_read_fallback_when_primary_is_valid(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_get_value(
        item: Any,
        fields: Any
    ) -> str:
        calls.append(fields)

        if fields == ("text",):
            return "Texte principal"

        raise AssertionError(
            "Les champs de repli ne doivent pas être consultés."
        )

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: value
    )

    result = module.get_dataset_text(
        {
            "text": "Texte principal"
        },
        ("text",),
        ("description",)
    )

    assert result == "Texte principal"
    assert calls == [
        ("text",)
    ]


def test_get_dataset_text_returns_empty_string_when_no_text_is_available(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: None
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: ""
    )

    result = module.get_dataset_text(
        {},
        ("text",),
        ("description",)
    )

    assert result == ""


def test_get_dataset_text_uses_empty_fallback_fields_by_default(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received_fields: list[Any] = []

    def fake_get_value(
        item: Any,
        fields: Any
    ) -> str:
        received_fields.append(fields)
        return ""

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        lambda value: ""
    )

    result = module.get_dataset_text(
        {},
        ("text",)
    )

    assert result == ""
    assert received_fields == [
        ("text",),
        ()
    ]


def test_get_dataset_text_cleans_primary_and_fallback_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    cleaned_values: list[Any] = []

    def fake_get_value(
        item: Any,
        fields: Any
    ) -> Any:
        if fields == ("text",):
            return None
        return "  Texte de secours  "

    def fake_clean_text(value: Any) -> str:
        cleaned_values.append(value)

        if value is None:
            return ""

        return value.strip()

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "clean_text",
        fake_clean_text
    )

    result = module.get_dataset_text(
        {},
        ("text",),
        ("description",)
    )

    assert result == "Texte de secours"
    assert cleaned_values == [
        None,
        "  Texte de secours  "
    ]


@pytest.mark.parametrize(
    "invalid_item",
    [
        None,
        [],
        (),
        "article",
        42,
        3.14,
        False
    ]
)
def test_get_dataset_text_rejects_non_mapping_items(
    invalid_item: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="item doit être une structure de données associative"
    ):
        module.get_dataset_text(
            invalid_item,
            ("text",),
            ("description",)
        )


# get_dataset_url

def test_get_dataset_url_returns_valid_http_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: "http://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    result = module.get_dataset_url(
        {
            "url": "http://example.com/article"
        },
        ("url",)
    )

    assert result == "http://example.com/article"


def test_get_dataset_url_returns_valid_https_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    result = module.get_dataset_url(
        {
            "url": "https://example.com/article"
        },
        ("url",)
    )

    assert result == "https://example.com/article"


def test_get_dataset_url_adds_https_to_domain_without_scheme(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    validated_urls: list[str] = []

    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: "example.com/article"
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value.strip()
    )

    def fake_is_valid_http_url(value: str) -> bool:
        validated_urls.append(value)
        return True

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fake_is_valid_http_url
    )

    result = module.get_dataset_url(
        {
            "url": "example.com/article"
        },
        ("url",)
    )

    assert result == "https://example.com/article"
    assert validated_urls == [
        "https://example.com/article"
    ]


def test_get_dataset_url_converts_protocol_relative_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: "//example.com/article"
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: value == "https://example.com/article"
    )

    result = module.get_dataset_url(
        {
            "url": "//example.com/article"
        },
        ("url",)
    )

    assert result == "https://example.com/article"


@pytest.mark.parametrize(
    ("raw_url", "expected_url"),
    [
        (
            "HTTP://example.com/article",
            "HTTP://example.com/article"
        ),
        (
            "HTTPS://example.com/article",
            "HTTPS://example.com/article"
        ),
        (
            "Http://example.com/article",
            "Http://example.com/article"
        ),
        (
            "Https://example.com/article",
            "Https://example.com/article"
        )
    ]
)
def test_get_dataset_url_preserves_existing_scheme_case(
    raw_url: str,
    expected_url: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: raw_url
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    result = module.get_dataset_url(
        {
            "url": raw_url
        },
        ("url",)
    )

    assert result == expected_url


@pytest.mark.parametrize(
    "normalized_url",
    [
        "",
        None
    ]
)
def test_get_dataset_url_returns_empty_string_when_url_is_missing(
    normalized_url: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    validation_called = False

    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: None
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: normalized_url
    )

    def fake_is_valid_http_url(value: str) -> bool:
        nonlocal validation_called
        validation_called = True
        return True

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fake_is_valid_http_url
    )

    result = module.get_dataset_url(
        {},
        ("url",)
    )

    assert result == ""
    assert validation_called is False


def test_get_dataset_url_returns_empty_string_when_url_is_invalid(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: "invalid-url"
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: False
    )

    result = module.get_dataset_url(
        {
            "url": "invalid-url"
        },
        ("url",)
    )

    assert result == ""


def test_get_dataset_url_validates_normalized_and_completed_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: " example.com/article "
    )

    def fake_normalize_value(value: Any) -> str:
        received["raw_value"] = value
        return value.strip()

    def fake_is_valid_http_url(value: str) -> bool:
        received["validated_url"] = value
        return True

    monkeypatch.setattr(
        module,
        "normalize_value",
        fake_normalize_value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fake_is_valid_http_url
    )

    result = module.get_dataset_url(
        {
            "url": " example.com/article "
        },
        ("url",)
    )

    assert result == "https://example.com/article"
    assert received == {
        "raw_value": " example.com/article ",
        "validated_url": "https://example.com/article"
    }


def test_get_dataset_url_passes_item_and_fields_to_get_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    item = {
        "article_url": "example.com"
    }
    url_fields = (
        "url",
        "article_url"
    )
    received: dict[str, Any] = {}

    def fake_get_value(
        received_item: Any,
        received_fields: Any
    ) -> str:
        received["item"] = received_item
        received["fields"] = received_fields
        return "example.com"

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    result = module.get_dataset_url(
        item,
        url_fields
    )

    assert result == "https://example.com"
    assert received == {
        "item": item,
        "fields": url_fields
    }


@pytest.mark.parametrize(
    "invalid_item",
    [
        None,
        [],
        (),
        "article",
        42,
        3.14,
        True
    ]
)
def test_get_dataset_url_rejects_non_mapping_items(
    invalid_item: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="item doit être une structure de données associative"
    ):
        module.get_dataset_url(
            invalid_item,
            ("url",)
        )


def test_get_dataset_url_accepts_empty_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_value",
        lambda item, fields: None
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
    )

    result = module.get_dataset_url(
        {},
        ("url",)
    )

    assert result == ""


def test_get_dataset_url_accepts_empty_field_sequence(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def fake_get_value(
        item: Any,
        fields: Any
    ) -> None:
        received["fields"] = fields
        return None

    monkeypatch.setattr(
        module,
        "get_value",
        fake_get_value
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
    )

    result = module.get_dataset_url(
        {},
        ()
    )

    assert result == ""
    assert received["fields"] == ()