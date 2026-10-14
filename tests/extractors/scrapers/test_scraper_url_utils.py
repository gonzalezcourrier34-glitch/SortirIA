"""Tests des outils URL communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_url_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from bs4 import BeautifulSoup

from src.extractors.scrapers import scraper_url_utils as module


# Fabriques

def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une source HTML minimale."""

    source = {
        "base_url": "https://example.com",
        "same_origin_only": True,
        "respect_robots_txt": True,
        "selectors": {
            "article": "article",
            "link": "a[href]"
        }
    }
    source.update(overrides)
    return source


def build_client() -> httpx.Client:
    """Construit un client HTTPX minimal."""

    return httpx.Client()


# URL absolues

@pytest.mark.parametrize(
    ("value", "page_url", "expected"),
    [
        (
            "/article",
            "https://example.com/news",
            "https://example.com/article"
        ),
        (
            "article",
            "https://example.com/news/",
            "https://example.com/news/article"
        ),
        (
            "https://other.example/article",
            "https://example.com",
            "https://other.example/article"
        ),
        (
            "/article?a=1&amp;b=2",
            "https://example.com",
            "https://example.com/article?a=1&b=2"
        )
    ]
)
def test_make_absolute_url_returns_expected_url(
    value: Any,
    page_url: Any,
    expected: str
) -> None:
    assert module.make_absolute_url(value, page_url) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   "
    ]
)
def test_make_absolute_url_returns_empty_for_missing_value(
    value: Any
) -> None:
    assert (
        module.make_absolute_url(
            value,
            "https://example.com"
        )
        == ""
    )


def test_make_absolute_url_returns_empty_for_invalid_http_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: False
    )

    assert (
        module.make_absolute_url(
            "/article",
            "https://example.com"
        )
        == ""
    )


def test_make_absolute_url_can_canonicalize(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda value: "https://example.com/article"
    )

    result = module.make_absolute_url(
        "/article?utm_source=test",
        "https://example.com",
        canonicalize=True
    )

    assert result == "https://example.com/article"


def test_make_absolute_url_returns_empty_when_urljoin_fails(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "urljoin",
        lambda *args: (_ for _ in ()).throw(
            ValueError("URL invalide")
        )
    )

    assert (
        module.make_absolute_url(
            "/article",
            "https://example.com"
        )
        == ""
    )


# Origine

@pytest.mark.parametrize(
    ("url_a", "url_b", "expected"),
    [
        (
            "https://example.com/a",
            "https://example.com/b",
            True
        ),
        (
            "HTTPS://EXAMPLE.COM/a",
            "https://example.com/b",
            True
        ),
        (
            "http://example.com/a",
            "https://example.com/b",
            False
        ),
        (
            "https://example.com/a",
            "https://other.example/b",
            False
        ),
        (
            "https://example.com:443/a",
            "https://example.com/b",
            False
        )
    ]
)
def test_is_same_origin(
    url_a: str,
    url_b: str,
    expected: bool
) -> None:
    assert module.is_same_origin(url_a, url_b) is expected


# Motifs

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "News",
            [
                "news"
            ]
        ),
        (
            [
                "News",
                " Politics ",
                ""
            ],
            [
                "news",
                "politics"
            ]
        ),
        (
            (
                "Article",
                "Fact-Check"
            ),
            [
                "article",
                "fact-check"
            ]
        )
    ]
)
def test_normalize_pattern_list(
    value: Any,
    expected: list[str]
) -> None:
    assert module.normalize_pattern_list(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        42,
        {},
        object()
    ]
)
def test_normalize_pattern_list_returns_empty_for_invalid_value(
    value: Any
) -> None:
    assert module.normalize_pattern_list(value) == []


# URL probable d'article

def test_is_probable_article_url_rejects_invalid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: False
    )

    result = module.is_probable_article_url(
        "invalid",
        "https://example.com",
        build_source()
    )

    assert result is False


def test_is_probable_article_url_rejects_other_origin(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    result = module.is_probable_article_url(
        "https://other.example/article",
        "https://example.com",
        build_source()
    )

    assert result is False


def test_is_probable_article_url_accepts_other_origin_when_allowed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )

    result = module.is_probable_article_url(
        "https://other.example/article",
        "https://example.com",
        build_source(same_origin_only=False)
    )

    assert result is True


@pytest.mark.parametrize(
    "fragment",
    module.IGNORED_URL_FRAGMENTS
)
def test_is_probable_article_url_rejects_ignored_fragments(
    fragment: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    result = module.is_probable_article_url(
        f"https://example.com{fragment}test",
        "https://example.com",
        build_source()
    )

    assert result is False


def test_is_probable_article_url_applies_include_patterns(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )

    source = build_source(
        include_url_patterns=[
            "/news/"
        ]
    )

    assert (
        module.is_probable_article_url(
            "https://example.com/news/article",
            "https://example.com",
            source
        )
        is True
    )
    assert (
        module.is_probable_article_url(
            "https://example.com/sport/article",
            "https://example.com",
            source
        )
        is False
    )


def test_is_probable_article_url_applies_exclude_patterns(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )

    source = build_source(
        exclude_url_patterns=[
            "/live/"
        ]
    )

    assert (
        module.is_probable_article_url(
            "https://example.com/live/article",
            "https://example.com",
            source
        )
        is False
    )


def test_is_probable_article_url_checks_robots(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            True,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: False
    )

    result = module.is_probable_article_url(
        "https://example.com/article",
        "https://example.com",
        build_source()
    )

    assert result is False


def test_is_probable_article_url_skips_robots_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: pytest.fail(
            "robots.txt ne doit pas être consulté."
        )
    )

    result = module.is_probable_article_url(
        "https://example.com/article",
        "https://example.com",
        build_source()
    )

    assert result is True


# Découverte

def test_discover_article_links_returns_empty_for_invalid_start_urls(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_source(
        start_urls=42
    )

    result = module.discover_article_links(
        build_client(),
        source,
        maximum_articles=10
    )

    assert result == []


def test_discover_article_links_returns_empty_for_invalid_selectors() -> None:
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            build_source(selectors="invalid"),
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == []


def test_discover_article_links_returns_listing_without_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda value: normalize_url(value)
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )

    source = build_source(
        start_urls=[
            "https://example.com/article",
            "https://example.com/article"
        ],
        selectors={}
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == [
        "https://example.com/article"
    ]


def normalize_url(value: Any) -> str:
    """Normalise une URL de test."""

    if isinstance(value, str) and value.startswith("http"):
        return value.strip()

    return ""


def test_discover_article_links_skips_invalid_and_forbidden_start_urls(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            True,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: not url.endswith("/forbidden")
    )

    source = build_source(
        start_urls=[
            None,
            "invalid",
            "https://example.com/forbidden",
            "https://example.com/allowed"
        ],
        selectors={}
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == [
        "https://example.com/allowed"
    ]


def test_discover_article_links_extracts_and_deduplicates_links(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_html = """
    <html>
      <body>
        <article><a href="/article-1">Article 1</a></article>
        <article><a href="/article-2">Article 2</a></article>
        <article><a href="/article-1">Doublon</a></article>
        <article><a href="/category/world">Catégorie</a></article>
      </body>
    </html>
    """

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.25
        )
    )
    monkeypatch.setattr(
        module,
        "get_html",
        lambda client, url, respect_robots, delay: (
            raw_html,
            "https://example.com/news"
        )
    )

    def fake_make_absolute_url(
        value: Any,
        page_url: Any,
        canonicalize: bool = False
    ) -> str:
        if not value:
            return ""

        return f"https://example.com{value}"

    monkeypatch.setattr(
        module,
        "make_absolute_url",
        fake_make_absolute_url
    )
    monkeypatch.setattr(
        module,
        "is_probable_article_url",
        lambda article_url, listing_url, source: (
            "/category/" not in article_url
        )
    )

    source = build_source(
        start_urls=[
            "https://example.com/news"
        ]
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == [
        "https://example.com/article-1",
        "https://example.com/article-2"
    ]


def test_discover_article_links_stops_at_maximum(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_html = """
    <html>
      <body>
        <article><a href="/article-1">Article 1</a></article>
        <article><a href="/article-2">Article 2</a></article>
        <article><a href="/article-3">Article 3</a></article>
      </body>
    </html>
    """

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "get_html",
        lambda *args: (
            raw_html,
            "https://example.com/news"
        )
    )
    monkeypatch.setattr(
        module,
        "make_absolute_url",
        lambda value, page_url, canonicalize=False: (
            f"https://example.com{value}"
        )
    )
    monkeypatch.setattr(
        module,
        "is_probable_article_url",
        lambda *args: True
    )

    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            build_source(
                start_urls="https://example.com/news"
            ),
            maximum_articles=2
        )
    finally:
        client.close()

    assert result == [
        "https://example.com/article-1",
        "https://example.com/article-2"
    ]


def test_discover_article_links_uses_container_anchor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_html = """
    <html>
      <body>
        <a class="article" href="/article">Article</a>
      </body>
    </html>
    """

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "get_html",
        lambda *args: (
            raw_html,
            "https://example.com"
        )
    )
    monkeypatch.setattr(
        module,
        "make_absolute_url",
        lambda value, page_url, canonicalize=False: (
            "https://example.com/article"
        )
    )
    monkeypatch.setattr(
        module,
        "is_probable_article_url",
        lambda *args: True
    )

    source = build_source(
        start_urls="https://example.com",
        selectors={
            "article": "a.article",
            "link": "a[href]"
        }
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == [
        "https://example.com/article"
    ]


def test_discover_article_links_continues_after_page_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )

    def fake_get_html(
        client: Any,
        url: str,
        respect_robots: bool,
        delay: float
    ) -> tuple[str, str]:
        if url.endswith("/first"):
            raise module.ScraperPageError(
                "page_indisponible",
                "Page indisponible."
            )

        return (
            '<article><a href="/article">Article</a></article>',
            url
        )

    monkeypatch.setattr(
        module,
        "get_html",
        fake_get_html
    )
    monkeypatch.setattr(
        module,
        "make_absolute_url",
        lambda value, page_url, canonicalize=False: (
            "https://example.com/article"
        )
    )
    monkeypatch.setattr(
        module,
        "is_probable_article_url",
        lambda *args: True
    )

    source = build_source(
        start_urls=[
            "https://example.com/first",
            "https://example.com/second"
        ]
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == [
        "https://example.com/article"
    ]


def test_discover_article_links_returns_current_links_for_invalid_list_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "get_html",
        lambda *args: (
            "<html></html>",
            "https://example.com"
        )
    )

    source = build_source(
        start_urls="https://example.com",
        selectors={
            "article": "article[",
            "link": "a[href]"
        }
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == []


def test_discover_article_links_returns_current_links_for_invalid_link_selector(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_html = "<article><a href='/article'>Article</a></article>"

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        normalize_url
    )
    monkeypatch.setattr(
        module,
        "get_scraper_http_options",
        lambda source: (
            False,
            0.0
        )
    )
    monkeypatch.setattr(
        module,
        "get_html",
        lambda *args: (
            raw_html,
            "https://example.com"
        )
    )

    source = build_source(
        start_urls="https://example.com",
        selectors={
            "article": "article",
            "link": "a["
        }
    )
    client = build_client()

    try:
        result = module.discover_article_links(
            client,
            source,
            maximum_articles=10
        )
    finally:
        client.close()

    assert result == []