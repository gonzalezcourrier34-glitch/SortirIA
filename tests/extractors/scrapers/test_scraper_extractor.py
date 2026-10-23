"""Tests de l'implémentation HTML standard des scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_extractor.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from bs4 import BeautifulSoup

from src.extractors.scrapers import scraper_extractor as module
from src.extractors.scrapers.scraper_adapter import ScraperAdapter

# Fabriques

def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une source HTML minimale."""

    source = {
        "name": "Source HTML",
        "source_id": "html_test",
        "max_articles": 10,
        "max_article_age_days": 30
    }
    source.update(overrides)
    return source


def build_client() -> httpx.Client:
    """Construit un client HTTPX minimal."""

    return httpx.Client()


# Limites

def test_get_max_articles_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLES_PER_SOURCE",
        100
    )

    result = module.get_max_articles(
        build_source(max_articles=25)
    )

    assert result == 25


def test_get_max_articles_limits_global_maximum(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLES_PER_SOURCE",
        20
    )

    result = module.get_max_articles(
        build_source(max_articles=50)
    )

    assert result == 20


def test_get_max_articles_uses_default_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLES_PER_SOURCE",
        100
    )
    monkeypatch.setattr(
        module,
        "DEFAULT_MAX_HTML_ARTICLES",
        1
    )

    result = module.get_max_articles({})

    assert result == 1


def test_get_max_articles_normalizes_invalid_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLES_PER_SOURCE",
        100
    )

    result = module.get_max_articles(
        build_source(max_articles="invalid")
    )

    assert result == module.DEFAULT_MAX_HTML_ARTICLES


def test_get_max_article_age_days_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        90
    )

    result = module.get_max_article_age_days(
        build_source(max_article_age_days=30)
    )

    assert result == 30


def test_get_max_article_age_days_limits_global_maximum(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        30
    )

    result = module.get_max_article_age_days(
        build_source(max_article_age_days=90)
    )

    assert result == 30


def test_get_max_article_age_days_uses_global_default(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        30
    )

    result = module.get_max_article_age_days({})

    assert result == 30


def test_get_max_article_age_days_returns_configured_when_global_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        0
    )

    result = module.get_max_article_age_days(
        build_source(max_article_age_days=365)
    )

    assert result == 365


# Dates

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2026-07-28T10:00:00+00:00",
            datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
        ),
        (
            "2026-07-28T10:00:00Z",
            datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
        ),
        (
            "2026-07-28T12:00:00+02:00",
            datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
        ),
        (
            "2026-07-28T10:00:00",
            datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
        )
    ]
)
def test_parse_iso_datetime_returns_utc_datetime(
    value: str,
    expected: datetime
) -> None:
    assert module.parse_iso_datetime(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "invalid",
        {}
    ]
)
def test_parse_iso_datetime_returns_none_for_invalid_value(
    value: Any
) -> None:
    assert module.parse_iso_datetime(value) is None


def test_validate_article_age_accepts_missing_date() -> None:
    assert (
        module.validate_article_age(
            {},
            30
        )
        == (True, "")
    )


def test_validate_article_age_rejects_invalid_date() -> None:
    result = module.validate_article_age(
        {
            "published_at": "invalid"
        },
        30
    )

    assert result == (
        False,
        "date_publication_invalide"
    )


def test_validate_article_age_rejects_future_date(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=UTC
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )

    result = module.validate_article_age(
        {
            "published_at": (
                now + timedelta(minutes=6)
            ).isoformat()
        },
        30
    )

    assert result == (
        False,
        "date_publication_future"
    )


def test_validate_article_age_accepts_small_clock_difference(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=UTC
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )

    result = module.validate_article_age(
        {
            "published_at": (
                now + timedelta(minutes=5)
            ).isoformat()
        },
        30
    )

    assert result == (True, "")


def test_validate_article_age_rejects_old_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=UTC
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )

    result = module.validate_article_age(
        {
            "published_at": (
                now - timedelta(days=31)
            ).isoformat()
        },
        30
    )

    assert result == (
        False,
        "article_trop_ancien"
    )


def test_validate_article_age_accepts_old_article_when_limit_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=UTC
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )

    result = module.validate_article_age(
        {
            "published_at": (
                now - timedelta(days=3650)
            ).isoformat()
        },
        0
    )

    assert result == (True, "")


# Itération standard

def test_iter_standard_scraper_items_reuses_http_client(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}
    fake_client = object()
    article_urls = [
        "https://example.com/article-1",
        "https://example.com/article-2"
    ]

    class FakeClientContext:
        """Simule le contexte d'un client HTTP."""

        def __enter__(self) -> Any:
            received["entered"] = True
            return fake_client

        def __exit__(
            self,
            exception_type: object,
            exception: object,
            traceback: object
        ) -> None:
            received["exited"] = True

    monkeypatch.setattr(
        module,
        "create_http_client",
        lambda: FakeClientContext()
    )
    monkeypatch.setattr(
        module,
        "get_max_articles",
        lambda source: 2
    )

    def fake_discover_article_links(
        *,
        client: Any,
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> list[str]:
        received["client"] = client
        received["source"] = source
        received["maximum_articles"] = maximum_articles
        return article_urls

    monkeypatch.setattr(
        module,
        "discover_article_links",
        fake_discover_article_links
    )

    source = build_source()
    result = list(
        module.iter_standard_scraper_items(source)
    )

    assert result == [
        (
            fake_client,
            "https://example.com/article-1"
        ),
        (
            fake_client,
            "https://example.com/article-2"
        )
    ]
    assert received == {
        "entered": True,
        "client": fake_client,
        "source": source,
        "maximum_articles": 2,
        "exited": True
    }


# Construction standard

def test_build_standard_scraper_article_rejects_invalid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda value: ""
    )
    client = build_client()

    try:
        result = module.build_standard_scraper_article(
            (
                client,
                "invalid"
            ),
            build_source()
        )
    finally:
        client.close()

    assert result == {}


def test_build_standard_scraper_article_builds_expected_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    client = build_client()
    source = {
        **build_source(),
        "respect_robots_txt": False,
        "request_delay_seconds": 4
    }
    received: dict[str, Any] = {}
    expected_article = {
        "title": "Article HTML"
    }

    def fake_canonicalize_url(value: Any) -> str:
        values = {
            "https://example.com/article": (
                "https://example.com/article"
            ),
            "https://example.com/final": (
                "https://example.com/final"
            )
        }
        return values.get(value, "")

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        fake_canonicalize_url
    )

    def fake_get_html(
        current_client: httpx.Client,
        article_url: str,
        respect_robots: bool,
        request_delay_seconds: float
    ) -> tuple[str, str]:
        received["client"] = current_client
        received["article_url"] = article_url
        received["respect_robots"] = respect_robots
        received["request_delay_seconds"] = request_delay_seconds
        return (
            "<html><h1>Article HTML</h1></html>",
            "https://example.com/final"
        )

    monkeypatch.setattr(
        module,
        "get_html",
        fake_get_html
    )
    monkeypatch.setattr(
        module,
        "extract_metadata",
        lambda raw_html, page_url: {
            "page_url": page_url
        }
    )

    def fake_build_scraped_article(
        *,
        soup: BeautifulSoup,
        source: Mapping[str, Any],
        metadata: Mapping[str, Any],
        page_url: str
    ) -> dict[str, Any]:
        received["soup"] = soup
        received["metadata"] = metadata
        received["page_url"] = page_url
        return expected_article

    monkeypatch.setattr(
        module,
        "build_scraped_article",
        fake_build_scraped_article
    )

    try:
        result = module.build_standard_scraper_article(
            (
                client,
                "https://example.com/article"
            ),
            source
        )
    finally:
        client.close()

    assert result is expected_article
    assert received["client"] is client
    assert received["article_url"] == "https://example.com/article"
    # Les options HTTP de la configuration sont transmises au téléchargement.
    assert received["respect_robots"] is False
    assert received["request_delay_seconds"] == 4.0
    assert received["metadata"] == {
        "page_url": "https://example.com/final"
    }
    assert received["page_url"] == "https://example.com/final"
    assert isinstance(received["soup"], BeautifulSoup)


def test_build_standard_scraper_article_uses_original_url_when_final_invalid(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    client = build_client()
    received: dict[str, Any] = {}

    def fake_canonicalize_url(value: Any) -> str:
        if value == "https://example.com/article":
            return "https://example.com/article"
        return ""

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        fake_canonicalize_url
    )
    monkeypatch.setattr(
        module,
        "get_html",
        lambda client, article_url, respect_robots, request_delay_seconds: (
            "<html></html>",
            "invalid"
        )
    )
    monkeypatch.setattr(
        module,
        "extract_metadata",
        lambda raw_html, page_url: {}
    )

    def fake_build_scraped_article(
        *,
        soup: BeautifulSoup,
        source: Mapping[str, Any],
        metadata: Mapping[str, Any],
        page_url: str
    ) -> dict[str, Any]:
        received["page_url"] = page_url
        return {}

    monkeypatch.setattr(
        module,
        "build_scraped_article",
        fake_build_scraped_article
    )

    try:
        module.build_standard_scraper_article(
            (
                client,
                "https://example.com/article"
            ),
            build_source()
        )
    finally:
        client.close()

    assert received["page_url"] == "https://example.com/article"


# Validation standard des éléments

@pytest.mark.parametrize(
    "item",
    [
        None,
        [],
        {},
        "invalid",
        (),
        (
            "only-one",
        ),
        (
            "one",
            "two",
            "three"
        )
    ]
)
def test_validate_standard_scraper_item_rejects_invalid_structure(
    item: Any
) -> None:
    result = module.validate_standard_scraper_item(
        item,
        build_source()
    )

    assert result == (
        False,
        "element_invalide"
    )


def test_validate_standard_scraper_item_rejects_invalid_client() -> None:
    result = module.validate_standard_scraper_item(
        (
            object(),
            "https://example.com/article"
        ),
        build_source()
    )

    assert result == (
        False,
        "client_http_invalide"
    )


def test_validate_standard_scraper_item_rejects_invalid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda value: ""
    )
    client = build_client()

    try:
        result = module.validate_standard_scraper_item(
            (
                client,
                "invalid"
            ),
            build_source()
        )
    finally:
        client.close()

    assert result == (
        False,
        "url_invalide"
    )


def test_validate_standard_scraper_item_accepts_valid_item(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda value: "https://example.com/article"
    )
    client = build_client()

    try:
        result = module.validate_standard_scraper_item(
            (
                client,
                "https://example.com/article"
            ),
            build_source()
        )
    finally:
        client.close()

    assert result == (True, "")


# Validation standard des articles

def test_validate_standard_scraper_article_delegates_to_age_validation(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "published_at": "2026-07-28T10:00:00+00:00"
    }
    source = build_source()
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "get_max_article_age_days",
        lambda current_source: 15
    )

    def fake_validate_article_age(
        current_article: Mapping[str, Any],
        max_article_age_days: int
    ) -> tuple[bool, str]:
        received["article"] = current_article
        received["max_article_age_days"] = max_article_age_days
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article_age",
        fake_validate_article_age
    )

    result = module.validate_standard_scraper_article(
        article,
        source
    )

    assert result == (True, "")
    assert received == {
        "article": article,
        "max_article_age_days": 15
    }


# Fabrique

def test_create_standard_scraper_adapter_builds_expected_adapter() -> None:
    adapter = module.create_standard_scraper_adapter(
        source_id="html_test",
        default_name="HTML Test"
    )

    assert isinstance(adapter, ScraperAdapter)
    assert adapter.source_id == "html_test"
    assert adapter.default_name == "HTML Test"
    assert (
        adapter.iter_items
        is module.iter_standard_scraper_items
    )
    assert (
        adapter.build_article
        is module.build_standard_scraper_article
    )
    assert (
        adapter.validate_item
        is module.validate_standard_scraper_item
    )
    assert (
        adapter.validate_article
        is module.validate_standard_scraper_article
    )


def test_create_standard_scraper_adapter_normalizes_values() -> None:
    adapter = module.create_standard_scraper_adapter(
        source_id="  html_test  ",
        default_name="  HTML Test  "
    )

    assert adapter.source_id == "html_test"
    assert adapter.default_name == "HTML Test"