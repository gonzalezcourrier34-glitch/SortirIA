"""Tests des contrats et de l'adaptateur communs aux extracteurs HTML.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from src.extractors.scrapers import scraper_adapter as module

# Fabriques

def iter_items(
    source: Mapping[str, Any]
) -> Iterable[dict[str, Any]]:
    """Retourne une entrée HTML minimale."""

    return [
        {
            "title": "Article HTML"
        }
    ]


def build_article(
    item: Any,
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Construit un article HTML minimal."""

    return {
        "title": item.get("title", ""),
        "source": source.get("name", "")
    }


def validate_item(
    item: Any,
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Valide une entrée HTML."""

    return True, ""


def validate_article(
    article: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Valide un article HTML."""

    return True, ""


def build_adapter(
    **overrides: Any
) -> module.ScraperAdapter:
    """Construit un adaptateur HTML configurable."""

    values = {
        "source_id": "html_test",
        "default_name": "HTML Test",
        "iter_items": iter_items,
        "build_article": build_article,
        "validate_item": validate_item,
        "validate_article": validate_article
    }
    values.update(overrides)

    return module.ScraperAdapter(**values)


# Construction

def test_scraper_adapter_builds_expected_adapter() -> None:
    adapter = build_adapter()

    assert adapter.source_id == "html_test"
    assert adapter.default_name == "HTML Test"
    assert adapter.iter_items is iter_items
    assert adapter.build_article is build_article
    assert adapter.validate_item is validate_item
    assert adapter.validate_article is validate_article


def test_scraper_adapter_accepts_optional_validators() -> None:
    adapter = build_adapter(
        validate_item=None,
        validate_article=None
    )

    assert adapter.validate_item is None
    assert adapter.validate_article is None


# Normalisation

def test_scraper_adapter_normalizes_source_id() -> None:
    adapter = build_adapter(
        source_id="  html_test  "
    )

    assert adapter.source_id == "html_test"


def test_scraper_adapter_normalizes_default_name() -> None:
    adapter = build_adapter(
        default_name="  HTML Test  "
    )

    assert adapter.default_name == "HTML Test"


@pytest.mark.parametrize(
    "default_name",
    [
        "",
        "   ",
        None
    ]
)
def test_scraper_adapter_uses_source_id_as_default_name(
    default_name: Any
) -> None:
    adapter = build_adapter(
        source_id="html_test",
        default_name=default_name
    )

    assert adapter.default_name == "html_test"


# Validation de l'identifiant

@pytest.mark.parametrize(
    "source_id",
    [
        "",
        "   ",
        None
    ]
)
def test_scraper_adapter_rejects_empty_source_id(
    source_id: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="L'identifiant d'un ScraperAdapter ne peut pas être vide."
    ):
        build_adapter(source_id=source_id)


# Validation des fonctions obligatoires

@pytest.mark.parametrize(
    "invalid_iterator",
    [
        None,
        "iter_items",
        42,
        {},
        []
    ]
)
def test_scraper_adapter_rejects_invalid_iter_items(
    invalid_iterator: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="iter_items doit être une fonction appelable."
    ):
        build_adapter(iter_items=invalid_iterator)


@pytest.mark.parametrize(
    "invalid_builder",
    [
        None,
        "build_article",
        42,
        {},
        []
    ]
)
def test_scraper_adapter_rejects_invalid_build_article(
    invalid_builder: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="build_article doit être une fonction appelable."
    ):
        build_adapter(build_article=invalid_builder)


# Validation des fonctions optionnelles

@pytest.mark.parametrize(
    "invalid_validator",
    [
        "validate_item",
        42,
        {},
        []
    ]
)
def test_scraper_adapter_rejects_invalid_item_validator(
    invalid_validator: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="validate_item doit être une fonction appelable ou None."
    ):
        build_adapter(validate_item=invalid_validator)


@pytest.mark.parametrize(
    "invalid_validator",
    [
        "validate_article",
        42,
        {},
        []
    ]
)
def test_scraper_adapter_rejects_invalid_article_validator(
    invalid_validator: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="validate_article doit être une fonction appelable ou None."
    ):
        build_adapter(validate_article=invalid_validator)


# Exécution des fonctions

def test_scraper_adapter_iter_items_can_be_called() -> None:
    adapter = build_adapter()
    source = {
        "name": "Source HTML"
    }

    result = list(adapter.iter_items(source))

    assert result == [
        {
            "title": "Article HTML"
        }
    ]


def test_scraper_adapter_build_article_can_be_called() -> None:
    adapter = build_adapter()
    item = {
        "title": "Article HTML"
    }
    source = {
        "name": "Source HTML"
    }

    result = adapter.build_article(
        item,
        source
    )

    assert result == {
        "title": "Article HTML",
        "source": "Source HTML"
    }


def test_scraper_adapter_validate_item_can_be_called() -> None:
    adapter = build_adapter()

    assert adapter.validate_item is not None

    result = adapter.validate_item(
        {
            "title": "Article HTML"
        },
        {
            "name": "Source HTML"
        }
    )

    assert result == (True, "")


def test_scraper_adapter_validate_article_can_be_called() -> None:
    adapter = build_adapter()

    assert adapter.validate_article is not None

    result = adapter.validate_article(
        {
            "title": "Article HTML"
        },
        {
            "name": "Source HTML"
        }
    )

    assert result == (True, "")


# Immutabilité

@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        (
            "source_id",
            "autre_source"
        ),
        (
            "default_name",
            "Autre nom"
        ),
        (
            "iter_items",
            lambda source: []
        ),
        (
            "build_article",
            lambda item, source: {}
        )
    ]
)
def test_scraper_adapter_is_immutable(
    attribute: str,
    value: Any
) -> None:
    adapter = build_adapter()

    with pytest.raises(FrozenInstanceError):
        setattr(
            adapter,
            attribute,
            value
        )


def test_scraper_adapter_rejects_dynamic_attribute() -> None:
    adapter = build_adapter()

    with pytest.raises(
        (
            AttributeError,
            TypeError
        )
    ):
        adapter.unexpected_attribute = "value"