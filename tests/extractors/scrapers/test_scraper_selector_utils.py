"""Tests des outils BeautifulSoup communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_selector_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from bs4 import BeautifulSoup, Tag

from src.extractors.scrapers import scraper_selector_utils as module

# Fabriques

def build_soup(
    raw_html: str = (
        "<html>"
        "<head>"
        '<meta property="og:image" content="https://example.com/image.jpg">'
        "</head>"
        "<body>"
        '<article class="content">'
        "<h1>  Titre   principal  </h1>"
        '<a class="article-link" href="/article" data-url="https://example.com/article">'
        "Lire"
        "</a>"
        "</article>"
        "</body>"
        "</html>"
    )
) -> BeautifulSoup:
    """Construit une page BeautifulSoup minimale."""

    return BeautifulSoup(
        raw_html,
        "html.parser"
    )


# Dates

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2026-07-28",
            "2026-07-28T00:00:00+00:00"
        ),
        (
            "2026-07-28T10:30:00Z",
            "2026-07-28T10:30:00+00:00"
        ),
        (
            "",
            ""
        ),
        (
            None,
            ""
        )
    ]
)
def test_parse_date_delegates_to_converter(
    value: Any,
    expected: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[Any] = []

    def fake_convert_date_to_iso(
        current_value: Any
    ) -> str:
        received.append(current_value)
        return expected

    monkeypatch.setattr(
        module,
        "convert_date_to_iso",
        fake_convert_date_to_iso
    )

    result = module.parse_date(value)

    assert result == expected
    assert received == [value]


# Sélection d'éléments

def test_select_element_returns_matching_tag() -> None:
    soup = build_soup()

    result = module.select_element(
        soup,
        "article.content h1"
    )

    assert isinstance(result, Tag)
    assert result.name == "h1"
    assert result.get_text(strip=True) == "Titre   principal"


@pytest.mark.parametrize(
    "selector",
    [
        None,
        "",
        "   "
    ]
)
def test_select_element_returns_none_for_empty_selector(
    selector: Any
) -> None:
    soup = build_soup()

    assert module.select_element(soup, selector) is None


def test_select_element_returns_none_when_no_match() -> None:
    soup = build_soup()

    assert (
        module.select_element(
            soup,
            ".missing"
        )
        is None
    )


def test_select_element_returns_none_for_invalid_selector() -> None:
    soup = build_soup()

    result = module.select_element(
        soup,
        "article["
    )

    assert result is None


def test_select_element_normalizes_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    soup = build_soup()
    received: list[Any] = []

    def fake_normalize_value(
        value: Any
    ) -> str:
        received.append(value)
        return "article.content"

    monkeypatch.setattr(
        module,
        "normalize_value",
        fake_normalize_value
    )

    selector = object()

    result = module.select_element(
        soup,
        selector
    )

    assert isinstance(result, Tag)
    assert result.name == "article"
    assert received == [selector]


def test_select_element_rejects_non_tag_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    soup = build_soup()

    monkeypatch.setattr(
        soup,
        "select_one",
        lambda selector: "not-a-tag"
    )

    assert (
        module.select_element(
            soup,
            "article"
        )
        is None
    )


# Sélection de texte

def test_select_text_returns_cleaned_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    soup = build_soup()
    received: list[str] = []

    def fake_clean_text(
        value: str
    ) -> str:
        received.append(value)
        return "Titre principal"

    monkeypatch.setattr(
        module,
        "clean_text",
        fake_clean_text
    )

    result = module.select_text(
        soup,
        "article.content h1"
    )

    assert result == "Titre principal"
    assert received == [
        "Titre   principal"
    ]


def test_select_text_returns_empty_when_element_missing() -> None:
    soup = build_soup()

    assert (
        module.select_text(
            soup,
            ".missing"
        )
        == ""
    )


# Sélection d'attributs

def test_select_attribute_returns_first_non_empty_attribute() -> None:
    soup = build_soup()

    result = module.select_attribute(
        soup,
        "a.article-link",
        [
            "missing",
            "href",
            "data-url"
        ]
    )

    assert result == "/article"


def test_select_attribute_skips_empty_attributes() -> None:
    soup = build_soup(
        "<html><body>"
        '<a class="article-link" href="" data-url="https://example.com/article">'
        "Lire"
        "</a>"
        "</body></html>"
    )

    result = module.select_attribute(
        soup,
        "a.article-link",
        [
            "href",
            "data-url"
        ]
    )

    assert result == "https://example.com/article"


def test_select_attribute_returns_empty_when_element_missing() -> None:
    soup = build_soup()

    result = module.select_attribute(
        soup,
        ".missing",
        [
            "href",
            "src"
        ]
    )

    assert result == ""


def test_select_attribute_returns_empty_when_no_attribute_matches() -> None:
    soup = build_soup()

    result = module.select_attribute(
        soup,
        "h1",
        [
            "href",
            "src"
        ]
    )

    assert result == ""


def test_select_attribute_normalizes_attribute_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    soup = build_soup()
    received: list[Any] = []

    def fake_normalize_value(value: Any) -> str:
        received.append(value)

        if value == "a.article-link":
            return "a.article-link"

        if value == "/article":
            return "https://example.com/article"

        return ""

    monkeypatch.setattr(
        module,
        "normalize_value",
        fake_normalize_value
    )

    result = module.select_attribute(
        soup,
        "a.article-link",
        [
            "missing",
            "href"
        ]
    )

    assert result == "https://example.com/article"
    assert received == [
        "a.article-link",
        None,
        "/article"
    ]

def test_select_attribute_accepts_tuple_of_attributes() -> None:
    soup = build_soup()

    result = module.select_attribute(
        soup,
        "meta[property='og:image']",
        (
            "content",
            "href"
        )
    )

    assert result == "https://example.com/image.jpg"


# Sélecteurs de source

def test_get_source_selector_returns_configured_selector() -> None:
    source = {
        "selectors": {
            "title": "article h1"
        }
    }

    result = module.get_source_selector(
        source,
        "title"
    )

    assert result == "article h1"


def test_get_source_selector_returns_empty_when_selectors_missing() -> None:
    assert (
        module.get_source_selector(
            {},
            "title"
        )
        == ""
    )


@pytest.mark.parametrize(
    "selectors",
    [
        None,
        [],
        "invalid",
        42
    ]
)
def test_get_source_selector_returns_empty_for_invalid_selectors(
    selectors: Any
) -> None:
    source = {
        "selectors": selectors
    }

    assert (
        module.get_source_selector(
            source,
            "title"
        )
        == ""
    )


def test_get_source_selector_returns_empty_when_name_missing() -> None:
    source = {
        "selectors": {
            "title": "article h1"
        }
    }

    assert (
        module.get_source_selector(
            source,
            "text"
        )
        == ""
    )


def test_get_source_selector_normalizes_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "selectors": {
            "title": "  article h1  "
        }
    }
    received: list[Any] = []

    def fake_normalize_value(
        value: Any
    ) -> str:
        received.append(value)
        return "article h1"

    monkeypatch.setattr(
        module,
        "normalize_value",
        fake_normalize_value
    )

    result = module.get_source_selector(
        source,
        "title"
    )

    assert result == "article h1"
    assert received == [
        "  article h1  "
    ]


def test_get_source_selector_accepts_mapping_subclass() -> None:
    class SelectorMapping(dict[str, str]):
        """Mapping spécialisé utilisé pour le test."""

    source: Mapping[str, Any] = {
        "selectors": SelectorMapping(
            {
                "title": "article h1"
            }
        )
    }

    assert (
        module.get_source_selector(
            source,
            "title"
        )
        == "article h1"
    )