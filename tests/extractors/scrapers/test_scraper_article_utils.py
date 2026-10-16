"""Tests de l'extraction et de la construction des articles HTML.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_article_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from bs4 import BeautifulSoup

from src.extractors.scrapers import scraper_article_utils as module


# Fabriques

def build_soup(html: str = "<html></html>") -> BeautifulSoup:
    """Construit un document HTML de test."""

    return BeautifulSoup(html, "html.parser")


def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une source scraper minimale."""

    source = {
        "name": "Source HTML",
        "source_id": "html_test",
        "type": "scraper",
        "language": "fr",
        "category": "actualité",
        "country": "FR",
        "role": "partenaire",
        "selectors": {}
    }
    source.update(overrides)
    return source


# Valeurs structurées

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Jean Dupont  ", "Jean Dupont"),
        (
            {
                "name": "Marie Martin"
            },
            "Marie Martin"
        ),
        (
            {
                "legalName": "Société Exemple"
            },
            "Société Exemple"
        ),
        (
            {
                "alternateName": "Auteur alternatif"
            },
            "Auteur alternatif"
        ),
        (
            [
                {
                    "name": "Jean Dupont"
                },
                {
                    "name": "Marie Martin"
                },
                {
                    "name": "Jean Dupont"
                }
            ],
            "Jean Dupont, Marie Martin"
        ),
        (None, ""),
        (42, "")
    ]
)
def test_extract_person_name(
    value: Any,
    expected: str
) -> None:
    assert module.extract_person_name(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://example.com/image.jpg",
            "https://example.com/image.jpg"
        ),
        (
            {
                "url": "https://example.com/image.jpg"
            },
            "https://example.com/image.jpg"
        ),
        (
            {
                "contentUrl": "https://example.com/content.jpg"
            },
            "https://example.com/content.jpg"
        ),
        (
            {
                "@id": "https://example.com/id.jpg"
            },
            "https://example.com/id.jpg"
        ),
        (
            [
                {},
                {
                    "url": "https://example.com/list.jpg"
                }
            ],
            "https://example.com/list.jpg"
        ),
        (None, "")
    ]
)
def test_extract_json_ld_image(
    value: Any,
    expected: str
) -> None:
    assert module.extract_json_ld_image(value) == expected


def test_get_image_url_from_tag_returns_empty_without_element() -> None:
    assert (
        module.get_image_url_from_tag(
            None,
            "https://example.com/article"
        )
        == ""
    )


# URL canonique

def test_extract_canonical_url_prefers_html_link() -> None:
    soup = build_soup(
        '<link rel="canonical" href="/canonical">'
    )

    result = module.extract_canonical_url(
        soup,
        {
            "url": "https://example.com/json-ld"
        },
        {
            "url": "https://example.com/open-graph"
        },
        "https://example.com/page"
    )

    assert result == "https://example.com/canonical"


def test_extract_canonical_url_uses_open_graph() -> None:
    result = module.extract_canonical_url(
        build_soup(),
        {},
        {
            "url": "/open-graph"
        },
        "https://example.com/page"
    )

    assert result == "https://example.com/open-graph"


def test_extract_canonical_url_uses_json_ld_main_entity() -> None:
    result = module.extract_canonical_url(
        build_soup(),
        {
            "mainEntityOfPage": {
                "@id": "/json-ld"
            }
        },
        {},
        "https://example.com/page"
    )

    assert result == "https://example.com/json-ld"


def test_extract_canonical_url_uses_page_url_as_fallback() -> None:
    result = module.extract_canonical_url(
        build_soup(),
        {},
        {},
        "https://example.com/page"
    )

    assert result == "https://example.com/page"


# Titre et texte

def test_extract_title_prefers_configured_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_source_selector",
        lambda source, key: ".configured-title" if key == "title" else ""
    )

    soup = build_soup(
        """
        <html>
            <head>
                <title>Titre HTML</title>
            </head>
            <body>
                <h1>Titre H1</h1>
                <div class="configured-title">Titre configuré</div>
            </body>
        </html>
        """
    )

    result = module.extract_title(
        soup,
        build_source(),
        {
            "headline": "Titre JSON-LD"
        },
        {
            "title": "Titre Open Graph"
        }
    )

    assert result == "Titre configuré"


def test_extract_title_uses_fallbacks() -> None:
    soup = build_soup("<h1>Titre H1</h1>")

    assert (
        module.extract_title(
            soup,
            build_source(),
            {
                "headline": "Titre JSON-LD"
            },
            {}
        )
        == "Titre JSON-LD"
    )

    assert (
        module.extract_title(
            soup,
            build_source(),
            {},
            {
                "title": "Titre Open Graph"
            }
        )
        == "Titre Open Graph"
    )

    assert (
        module.extract_title(
            soup,
            build_source(),
            {},
            {}
        )
        == "Titre H1"
    )


def test_extract_text_uses_configured_content(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_source_selector",
        lambda source, key: ".content" if key == "content" else ""
    )

    soup = build_soup(
        """
        <div class="content">
            <p>Premier paragraphe</p>
            <script>Contenu indésirable</script>
            <p>Deuxième paragraphe</p>
        </div>
        """
    )

    result = module.extract_text(
        soup,
        build_source(),
        {}
    )

    assert result == "Premier paragraphe\n\nDeuxième paragraphe"


def test_extract_text_uses_json_ld_article_body() -> None:
    result = module.extract_text(
        build_soup(),
        build_source(),
        {
            "articleBody": "Contenu JSON-LD"
        }
    )

    assert result == "Contenu JSON-LD"


def test_extract_text_uses_article_fallback() -> None:
    soup = build_soup(
        """
        <article>
            <p>Premier paragraphe</p>
            <p>Deuxième paragraphe</p>
        </article>
        """
    )

    result = module.extract_text(
        soup,
        build_source(),
        {}
    )

    assert result == "Premier paragraphe\n\nDeuxième paragraphe"


def test_extract_text_returns_empty_string() -> None:
    assert (
        module.extract_text(
            build_soup(),
            build_source(),
            {}
        )
        == ""
    )


# Étiquette (catégorie)

def test_extract_image_url_uses_open_graph() -> None:
    result = module.extract_image_url(
        build_soup(),
        build_source(),
        {},
        {
            "image": "/open-graph.jpg"
        },
        "https://example.com/article"
    )

    assert result == "https://example.com/open-graph.jpg"


def test_extract_author_uses_json_ld() -> None:
    result = module.extract_author(
        build_soup(),
        build_source(),
        {
            "author": {
                "name": "Jean Dupont"
            }
        },
        {}
    )

    assert result == "Jean Dupont"


def test_extract_author_uses_meta_fallback() -> None:
    soup = build_soup(
        '<meta name="author" content="Marie Martin">'
    )

    result = module.extract_author(
        soup,
        build_source(),
        {},
        {}
    )

    assert result == "Marie Martin"


def test_extract_published_at_uses_json_ld(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "convert_date_to_iso",
        lambda value: f"iso:{value}"
    )

    result = module.extract_published_at(
        build_soup(),
        build_source(),
        {
            "datePublished": "2026-07-28"
        },
        {}
    )

    assert result == "iso:2026-07-28"


@pytest.mark.parametrize(
    ("html", "article_json_ld", "open_graph", "source", "expected"),
    [
        (
            '<html lang="fr-FR"></html>',
            {},
            {},
            build_source(language="en"),
            "fr"
        ),
        (
            "<html></html>",
            {
                "inLanguage": "EN_us"
            },
            {},
            build_source(language="fr"),
            "en"
        ),
        (
            "<html></html>",
            {},
            {
                "locale": "es_ES"
            },
            build_source(language="fr"),
            "es"
        ),
        (
            "<html></html>",
            {},
            {},
            build_source(language="DE-de"),
            "de"
        )
    ]
)
def test_extract_language(
    html: str,
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any],
    source: Mapping[str, Any],
    expected: str
) -> None:
    result = module.extract_language(
        build_soup(html),
        source,
        article_json_ld,
        open_graph
    )

    assert result == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Politique ", "Politique"),
        (
            {
                "name": "International"
            },
            "International"
        ),
        (
            [
                "Politique",
                {
                    "name": "International"
                },
                "Politique"
            ],
            "Politique, International"
        ),
        (None, "")
    ]
)
def test_normalize_category_value(
    value: Any,
    expected: str
) -> None:
    assert module.normalize_category_value(value) == expected


def test_extract_category_uses_json_ld() -> None:
    result = module.extract_category(
        build_soup(),
        build_source(category="actualité"),
        {
            "articleSection": [
                "Politique",
                "International"
            ]
        },
        {}
    )

    assert result == "Politique, International"


# Construction finale

def test_build_scraped_article_builds_expected_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_json_ld",
        lambda metadata: {
            "headline": "Titre JSON-LD"
        }
    )
    monkeypatch.setattr(
        module,
        "get_open_graph",
        lambda metadata: {}
    )
    monkeypatch.setattr(
        module,
        "extract_canonical_url",
        lambda soup, article_json_ld, open_graph, page_url: (
            "https://example.com/canonical"
        )
    )
    monkeypatch.setattr(
        module,
        "extract_title",
        lambda soup, source, article_json_ld, open_graph: "Titre"
    )
    monkeypatch.setattr(
        module,
        "extract_text",
        lambda soup, source, article_json_ld: "Contenu"
    )
    monkeypatch.setattr(
        module,
        "extract_label",
        lambda soup, source, article_json_ld: "Concert"
    )
    monkeypatch.setattr(
        module,
        "extract_image_url",
        lambda soup, source, article_json_ld, open_graph, page_url: (
            "https://example.com/image.jpg"
        )
    )
    monkeypatch.setattr(
        module,
        "extract_published_at",
        lambda soup, source, article_json_ld, open_graph: (
            "2026-07-28T10:00:00+00:00"
        )
    )
    monkeypatch.setattr(
        module,
        "extract_author",
        lambda soup, source, article_json_ld, open_graph: "Jean Dupont"
    )
    monkeypatch.setattr(
        module,
        "extract_language",
        lambda soup, source, article_json_ld, open_graph: "fr"
    )
    monkeypatch.setattr(
        module,
        "extract_category",
        lambda soup, source, article_json_ld, open_graph: "Politique"
    )
    monkeypatch.setattr(
        module,
        "build_standard_article",
        lambda **kwargs: kwargs
    )

    source = build_source()
    metadata = {
        "json_ld": [],
        "open_graph": {}
    }

    result = module.build_scraped_article(
        build_soup(),
        source,
        metadata,
        "https://example.com/page"
    )

    assert result == {
        "identifier": "https://example.com/canonical",
        "source": "Source HTML",
        "title": "Titre",
        "text": "Contenu",
        "label": "Concert",
        "image_url": "https://example.com/image.jpg",
        "published_at": "2026-07-28T10:00:00+00:00",
        "author": "Jean Dupont",
        "language": "fr",
        "url": "https://example.com/canonical",
        "category": "Politique",
        "role": "partenaire",
        "metadata": {
            "source_id": "html_test",
            "source_type": "scraper",
            "country": "FR"
        }
    }


def test_build_scraped_article_uses_page_url_as_identifier(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_json_ld",
        lambda metadata: {}
    )
    monkeypatch.setattr(
        module,
        "get_open_graph",
        lambda metadata: {}
    )
    monkeypatch.setattr(
        module,
        "extract_canonical_url",
        lambda soup, article_json_ld, open_graph, page_url: ""
    )
    monkeypatch.setattr(
        module,
        "extract_title",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_text",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_label",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_image_url",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_published_at",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_author",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_language",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "extract_category",
        lambda *args: ""
    )
    monkeypatch.setattr(
        module,
        "build_standard_article",
        lambda **kwargs: kwargs
    )

    result = module.build_scraped_article(
        build_soup(),
        {},
        {},
        "https://example.com/page"
    )

    assert result["identifier"] == "https://example.com/page"
    assert result["url"] == "https://example.com/page"
    assert result["metadata"] == {
        "source_id": "",
        "source_type": "scraper",
        "country": ""
    }


def test_extract_label_reads_configured_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_source_selector",
        lambda source, key: ".categorie" if key == "category" else ""
    )

    soup = build_soup('<span class="categorie">Concert</span>')

    assert module.extract_label(soup, build_source(), {}) == "Concert"


def test_extract_label_returns_empty_without_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_source_selector", lambda source, key: "")

    assert module.extract_label(build_soup(), build_source(), {}) == ""
