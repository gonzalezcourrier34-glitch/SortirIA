"""Tests de l'implémentation technique RSS et Atom CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/rss copy/test_rss_extractor.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx
import pytest

from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.rss import rss_extractor as module
from src.extractors.rss.rss_engine import (
    RssInvalidContentTypeError,
    RssInvalidFeedError,
    RssNetworkError,
    RssParsingError,
    RssRequestError,
    RssTimeoutError
)


# Fabriques

def build_downloaded_feed(
    content: bytes = b"<?xml version='1.0'?><rss></rss>",
    content_type: str = "application/rss+xml",
    encoding: str = "utf-8",
    final_url: str = "https://example.com/feed.xml"
) -> module.DownloadedFeed:
    """Construit un flux téléchargé configurable."""

    return module.DownloadedFeed(
        content=content,
        content_type=content_type,
        encoding=encoding,
        final_url=final_url
    )


def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une source RSS minimale."""

    source = {
        "name": "Flux RSS de test",
        "url": "https://example.com/feed.xml",
        "language": "fr",
        "category": "actualité",
        "role": "acquisition",
        "max_article_age_days": 30
    }
    source.update(overrides)
    return source


def build_entry(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une entrée RSS minimale."""

    entry = {
        "id": "article-1",
        "title": "Titre de l'article",
        "summary": "<p>Contenu de l'article</p>",
        "link": "https://example.com/article-1",
        "published": "2026-07-20T10:00:00+00:00",
        "author": "Jean Dupont"
    }
    entry.update(overrides)
    return entry


class FakeFeed(dict[str, Any]):
    """Simule le résultat retourné par Feedparser."""

    def __init__(
        self,
        entries: list[Any] | None = None,
        bozo: bool = False,
        bozo_exception: Any = None
    ) -> None:
        super().__init__()
        self.entries = entries or []
        self.bozo = bozo
        self.bozo_exception = bozo_exception


# Nettoyage et URL

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("<p>Texte</p>", "Texte"),
        (" Texte ", "Texte"),
        ("", ""),
        (None, "")
    ]
)
def test_clean_html_returns_clean_text(
    value: Any,
    expected: str
) -> None:
    assert module.clean_html(value) == expected


def test_resolve_http_url_returns_absolute_url() -> None:
    result = module.resolve_http_url(
        "/images/article.jpg",
        "https://example.com/news/article"
    )

    assert result == "https://example.com/images/article.jpg"


def test_resolve_http_url_decodes_html_entities() -> None:
    result = module.resolve_http_url(
        "https://example.com/image?a=1&amp;b=2"
    )

    assert result == "https://example.com/image?a=1&b=2"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "ftp://example.com/file.xml",
        "javascript:alert(1)",
        "mailto:test@example.com"
    ]
)
def test_resolve_http_url_rejects_invalid_url(
    value: Any
) -> None:
    assert module.resolve_http_url(value) == ""


def test_resolve_http_url_can_canonicalize(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: f"canonical:{url}"
    )

    result = module.resolve_http_url(
        "https://example.com/article",
        canonicalize=True
    )

    assert result == "canonical:https://example.com/article"


# Lecture des URL

def test_get_entry_url_prefers_direct_link(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "resolve_http_url",
        lambda value, base_url="", canonicalize=False: (
            f"resolved:{value}" if value else ""
        )
    )

    entry = {
        "link": "https://example.com/direct",
        "links": [
            {
                "rel": "alternate",
                "href": "https://example.com/alternate"
            }
        ]
    }

    assert (
        module.get_entry_url(entry)
        == "resolved:https://example.com/direct"
    )


def test_get_entry_url_uses_alternate_link() -> None:
    entry = {
        "links": [
            {
                "rel": "alternate",
                "href": "https://example.com/alternate"
            }
        ]
    }

    assert (
        module.get_entry_url(entry)
        == "https://example.com/alternate"
    )


def test_get_entry_url_uses_first_valid_fallback_link() -> None:
    entry = {
        "links": [
            {
                "rel": "related",
                "href": "ftp://example.com/invalid"
            },
            {
                "rel": "related",
                "href": "https://example.com/fallback"
            }
        ]
    }

    assert (
        module.get_entry_url(entry)
        == "https://example.com/fallback"
    )


@pytest.mark.parametrize(
    "links",
    [
        None,
        {},
        "invalid"
    ]
)
def test_get_entry_url_rejects_invalid_links_collection(
    links: Any
) -> None:
    assert module.get_entry_url({"links": links}) == ""


def test_get_entry_identifier_prefers_id() -> None:
    entry = {
        "id": "article-id",
        "guid": "article-guid",
        "link": "https://example.com/article"
    }

    assert module.get_entry_identifier(entry) == "article-id"


def test_get_entry_identifier_uses_guid() -> None:
    entry = {
        "guid": "article-guid",
        "link": "https://example.com/article"
    }

    assert module.get_entry_identifier(entry) == "article-guid"


def test_get_entry_identifier_uses_url() -> None:
    entry = {
        "link": "https://example.com/article"
    }

    assert (
        module.get_entry_identifier(entry)
        == "https://example.com/article"
    )


# Lecture du texte

def test_get_entry_text_prefers_content() -> None:
    entry = {
        "content": [
            {
                "value": "<p>Contenu complet</p>"
            }
        ],
        "summary": "Résumé"
    }

    assert module.get_entry_text(entry) == "Contenu complet"


def test_get_entry_text_ignores_invalid_content_items() -> None:
    entry = {
        "content": [
            None,
            "invalid",
            {
                "value": "<p>Contenu valide</p>"
            }
        ]
    }

    assert module.get_entry_text(entry) == "Contenu valide"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (
            "summary",
            "<p>Résumé de l'article</p>",
            "Résumé de l'article"
        ),
        (
            "description",
            "<div>Description complète</div>",
            "Description complète"
        )
    ]
)
def test_get_entry_text_uses_html_text_fields(
    field: str,
    value: str,
    expected: str
) -> None:
    assert module.get_entry_text({field: value}) == expected


def test_get_entry_text_returns_empty_string() -> None:
    assert module.get_entry_text({}) == ""


# Images HTML

def test_extract_image_from_html_uses_src() -> None:
    result = module.extract_image_from_html(
        '<img src="/images/photo.jpg">',
        "https://example.com/article"
    )

    assert result == "https://example.com/images/photo.jpg"


@pytest.mark.parametrize(
    "attribute",
    [
        "data-src",
        "data-original"
    ]
)
def test_extract_image_from_html_uses_lazy_attributes(
    attribute: str
) -> None:
    result = module.extract_image_from_html(
        f'<img {attribute}="https://example.com/image.jpg">'
    )

    assert result == "https://example.com/image.jpg"


def test_extract_image_from_html_uses_first_srcset_candidate() -> None:
    result = module.extract_image_from_html(
        (
            '<img srcset="'
            'https://example.com/small.jpg 400w, '
            'https://example.com/large.jpg 1200w">'
        )
    )

    assert result == "https://example.com/small.jpg"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "<p>Aucune image</p>",
        '<img src="ftp://example.com/image.jpg">'
    ]
)
def test_extract_image_from_html_returns_empty_string(
    value: Any
) -> None:
    assert module.extract_image_from_html(value) == ""


# Médias RSS

def test_get_media_url_returns_first_valid_url() -> None:
    items = [
        None,
        {
            "url": "ftp://example.com/invalid.jpg"
        },
        {
            "href": "https://example.com/image.jpg"
        }
    ]

    assert (
        module.get_media_url(items)
        == "https://example.com/image.jpg"
    )


@pytest.mark.parametrize(
    "items",
    [
        None,
        {},
        "invalid"
    ]
)
def test_get_media_url_rejects_invalid_collection(
    items: Any
) -> None:
    assert module.get_media_url(items) == ""


def test_get_enclosure_image_url_uses_link_enclosure() -> None:
    entry = {
        "links": [
            {
                "rel": "enclosure",
                "type": "image/jpeg",
                "href": "/images/photo.jpg"
            }
        ]
    }

    result = module.get_enclosure_image_url(
        entry,
        "https://example.com/article"
    )

    assert result == "https://example.com/images/photo.jpg"


def test_get_enclosure_image_url_ignores_non_image_link() -> None:
    entry = {
        "links": [
            {
                "rel": "enclosure",
                "type": "audio/mpeg",
                "href": "https://example.com/audio.mp3"
            }
        ]
    }

    assert (
        module.get_enclosure_image_url(
            entry,
            "https://example.com/article"
        )
        == ""
    )


def test_get_enclosure_image_url_uses_enclosures_field() -> None:
    entry = {
        "enclosures": [
            {
                "type": "image/png",
                "url": "https://example.com/image.png"
            }
        ]
    }

    assert (
        module.get_enclosure_image_url(
            entry,
            "https://example.com/article"
        )
        == "https://example.com/image.png"
    )


def test_get_entry_image_url_prefers_media_content() -> None:
    entry = {
        "link": "https://example.com/article",
        "media_content": [
            {
                "url": "https://example.com/media.jpg"
            }
        ],
        "summary": (
            '<img src="https://example.com/summary.jpg">'
        )
    }

    assert (
        module.get_entry_image_url(entry)
        == "https://example.com/media.jpg"
    )


def test_get_entry_image_url_uses_media_thumbnail() -> None:
    entry = {
        "media_thumbnail": [
            {
                "url": "https://example.com/thumb.jpg"
            }
        ]
    }

    assert (
        module.get_entry_image_url(entry)
        == "https://example.com/thumb.jpg"
    )


def test_get_entry_image_url_uses_content_html() -> None:
    entry = {
        "link": "https://example.com/article",
        "content": [
            {
                "value": '<img src="/image.jpg">'
            }
        ]
    }

    assert (
        module.get_entry_image_url(entry)
        == "https://example.com/image.jpg"
    )


def test_get_entry_image_url_uses_summary_html() -> None:
    entry = {
        "link": "https://example.com/article",
        "summary": '<img src="/summary.jpg">'
    }

    assert (
        module.get_entry_image_url(entry)
        == "https://example.com/summary.jpg"
    )


def test_get_entry_image_url_returns_empty_string() -> None:
    assert module.get_entry_image_url({}) == ""


# Auteur

def test_get_entry_author_prefers_author() -> None:
    entry = {
        "author": "Jean Dupont",
        "dc_creator": "Marie Martin"
    }

    assert module.get_entry_author(entry) == "Jean Dupont"


def test_get_entry_author_uses_author_detail() -> None:
    entry = {
        "author_detail": {
            "name": "Marie Martin"
        }
    }

    assert module.get_entry_author(entry) == "Marie Martin"


def test_get_entry_author_uses_dc_creator() -> None:
    entry = {
        "dc_creator": "Auteur RSS"
    }

    assert module.get_entry_author(entry) == "Auteur RSS"


def test_get_entry_author_joins_unique_authors() -> None:
    entry = {
        "authors": [
            {
                "name": "Jean Dupont"
            },
            {
                "name": "Marie Martin"
            },
            {
                "name": "Jean Dupont"
            }
        ]
    }

    assert (
        module.get_entry_author(entry)
        == "Jean Dupont, Marie Martin"
    )


@pytest.mark.parametrize(
    "authors",
    [
        None,
        {},
        "invalid"
    ]
)
def test_get_entry_author_rejects_invalid_authors(
    authors: Any
) -> None:
    assert module.get_entry_author({"authors": authors}) == ""


# Catégorie

def test_get_entry_category_prefers_configured_category() -> None:
    entry = {
        "tags": [
            {
                "term": "International"
            }
        ]
    }

    result = module.get_entry_category(
        entry,
        "Politique"
    )

    assert result == "Politique"


def test_get_entry_category_joins_unique_tags() -> None:
    entry = {
        "tags": [
            {
                "term": "International"
            },
            {
                "label": "Technologie"
            },
            {
                "term": "International"
            }
        ]
    }

    result = module.get_entry_category(entry, "")

    assert result == "International, Technologie"


@pytest.mark.parametrize(
    "tags",
    [
        None,
        {},
        "invalid"
    ]
)
def test_get_entry_category_rejects_invalid_tags(
    tags: Any
) -> None:
    assert module.get_entry_category({"tags": tags}, "") == ""


# Date

def test_get_entry_date_uses_first_valid_date(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[Any] = []

    def fake_convert_date_to_iso(value: Any) -> str:
        received.append(value)
        return "2026-07-20T10:00:00+00:00" if value == "valid" else ""

    monkeypatch.setattr(
        module,
        "convert_date_to_iso",
        fake_convert_date_to_iso
    )

    entry = {
        "published": "invalid",
        "updated": "valid",
        "created": "unused"
    }

    result = module.get_entry_date(entry)

    assert result == "2026-07-20T10:00:00+00:00"
    assert received == ["invalid", "valid"]


def test_get_entry_date_returns_empty_string(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "convert_date_to_iso",
        lambda value: ""
    )

    assert module.get_entry_date({}) == ""


# Ancienneté des articles

def test_get_max_article_age_days_returns_configured_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        90
    )

    result = module.get_max_article_age_days(
        {
            "max_article_age_days": 30
        }
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
        {
            "max_article_age_days": 90
        }
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


def test_validate_article_age_accepts_missing_date() -> None:
    assert module.validate_article_age({}, 30) == (True, "")


def test_validate_article_age_rejects_invalid_date(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "parse_datetime",
        lambda value: None
    )

    result = module.validate_article_age(
        {
            "published_at": "invalid"
        },
        30
    )

    assert result == (False, "date_publication_invalide")


def test_validate_article_age_rejects_future_date(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=timezone.utc
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )
    monkeypatch.setattr(
        module,
        "parse_datetime",
        lambda value: now + timedelta(minutes=6)
    )

    result = module.validate_article_age(
        {
            "published_at": "future"
        },
        30
    )

    assert result == (False, "date_publication_future")


def test_validate_article_age_accepts_small_clock_difference(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=timezone.utc
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )
    monkeypatch.setattr(
        module,
        "parse_datetime",
        lambda value: now + timedelta(minutes=5)
    )

    result = module.validate_article_age(
        {
            "published_at": "future"
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
        tzinfo=timezone.utc
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )
    monkeypatch.setattr(
        module,
        "parse_datetime",
        lambda value: now - timedelta(days=31)
    )

    result = module.validate_article_age(
        {
            "published_at": "old"
        },
        30
    )

    assert result == (False, "article_trop_ancien")


def test_validate_article_age_accepts_old_article_when_limit_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=timezone.utc
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: now
    )
    monkeypatch.setattr(
        module,
        "parse_datetime",
        lambda value: now - timedelta(days=3650)
    )

    result = module.validate_article_age(
        {
            "published_at": "old"
        },
        0
    )

    assert result == (True, "")


# Reprises HTTP

@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("Timeout"),
        httpx.ConnectError("Network")
    ]
)
def test_is_retryable_http_error_accepts_temporary_errors(
    error: BaseException
) -> None:
    assert module.is_retryable_http_error(error) is True


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (429, True),
        (500, True),
        (503, True)
    ]
)
def test_is_retryable_http_error_qualifies_http_status(
    status_code: int,
    expected: bool
) -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/feed.xml"
    )
    response = httpx.Response(
        status_code,
        request=request
    )
    error = httpx.HTTPStatusError(
        "Erreur HTTP",
        request=request,
        response=response
    )

    assert module.is_retryable_http_error(error) is expected


def test_is_retryable_http_error_rejects_business_error() -> None:
    assert (
        module.is_retryable_http_error(ValueError("Erreur"))
        is False
    )


def test_download_feed_request_builds_expected_request(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}
    expected_response = httpx.Response(
        200,
        request=httpx.Request(
            "GET",
            "https://example.com/feed.xml"
        ),
        content=b"<rss></rss>"
    )

    class FakeClient:
        """Simule un client HTTPX."""

        def __init__(self, **kwargs: Any) -> None:
            received["client_kwargs"] = kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(
            self,
            exception_type: Any,
            exception: Any,
            traceback: Any
        ) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            received["url"] = url
            return expected_response

    request_count = 0

    def fake_increment_request_count() -> None:
        nonlocal request_count
        request_count += 1

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )
    monkeypatch.setattr(
        module,
        "increment_rss_request_count",
        fake_increment_request_count
    )

    result = module._download_feed_request.retry_with(
        stop=module.stop_after_attempt(1)
    )("https://example.com/feed.xml")

    assert result is expected_response
    assert received["url"] == "https://example.com/feed.xml"
    assert received["client_kwargs"]["follow_redirects"] is True
    assert request_count == 1


# Téléchargement

def test_download_feed_returns_downloaded_feed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = httpx.Response(
        200,
        request=httpx.Request(
            "GET",
            "https://example.com/final.xml"
        ),
        headers={
            "content-type": "Application/RSS+XML; charset=UTF-8"
        },
        content=b"<rss></rss>"
    )
    response.encoding = "utf-8"

    monkeypatch.setattr(
        module,
        "_download_feed_request",
        lambda url: response
    )

    result = module.download_feed(
        "https://example.com/feed.xml",
        "Flux RSS"
    )

    assert result == module.DownloadedFeed(
        content=b"<rss></rss>",
        content_type="application/rss+xml",
        encoding="utf-8",
        final_url="https://example.com/final.xml"
    )


def test_download_feed_converts_non_fatal_http_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/feed.xml"
    )
    response = httpx.Response(
        400,
        request=request,
        text="Requête invalide"
    )
    error = httpx.HTTPStatusError(
        "Erreur HTTP",
        request=request,
        response=response
    )

    def failing_request(url: str) -> httpx.Response:
        raise error

    monkeypatch.setattr(
        module,
        "_download_feed_request",
        failing_request
    )
    monkeypatch.setattr(
        module,
        "raise_if_fatal_rss_error",
        lambda error, source_name: "Requête invalide"
    )

    with pytest.raises(RssRequestError) as caught:
        module.download_feed(
            "https://example.com/feed.xml",
            "Flux RSS"
        )

    assert caught.value.reason == "Requête invalide"
    assert caught.value.status_code == 400


def test_download_feed_propagates_fatal_http_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/feed.xml"
    )
    response = httpx.Response(
        403,
        request=request
    )
    error = httpx.HTTPStatusError(
        "Erreur HTTP",
        request=request,
        response=response
    )

    def failing_request(url: str) -> httpx.Response:
        raise error

    def failing_qualification(
        error: Any,
        source_name: str
    ) -> str:
        raise RssInvalidFeedError(
            source_name,
            "Flux refusé",
            403
        )

    monkeypatch.setattr(
        module,
        "_download_feed_request",
        failing_request
    )
    monkeypatch.setattr(
        module,
        "raise_if_fatal_rss_error",
        failing_qualification
    )

    with pytest.raises(RssInvalidFeedError):
        module.download_feed(
            "https://example.com/feed.xml",
            "Flux RSS"
        )


def test_download_feed_converts_timeout(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_request(url: str) -> httpx.Response:
        raise httpx.ReadTimeout("Timeout")

    monkeypatch.setattr(
        module,
        "_download_feed_request",
        failing_request
    )

    with pytest.raises(RssTimeoutError):
        module.download_feed(
            "https://example.com/feed.xml",
            "Flux RSS"
        )


def test_download_feed_converts_network_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_request(url: str) -> httpx.Response:
        raise httpx.ConnectError("Connexion impossible")

    monkeypatch.setattr(
        module,
        "_download_feed_request",
        failing_request
    )

    with pytest.raises(RssNetworkError):
        module.download_feed(
            "https://example.com/feed.xml",
            "Flux RSS"
        )


# Détection de contenu

@pytest.mark.parametrize(
    "content",
    [
        b"<!DOCTYPE html><html></html>",
        b"<html><body>Erreur</body></html>",
        b"\xef\xbb\xbf   <HTML></HTML>"
    ]
)
def test_looks_like_html_detects_html(
    content: bytes
) -> None:
    assert module.looks_like_html(content) is True


@pytest.mark.parametrize(
    "content",
    [
        b"<?xml version='1.0'?><rss></rss>",
        b"<rss></rss>",
        b"<feed></feed>",
        b"<rdf:RDF></rdf:RDF>",
        b"\xef\xbb\xbf  <rss></rss>"
    ]
)
def test_looks_like_xml_detects_xml(
    content: bytes
) -> None:
    assert module.looks_like_xml(content) is True


def test_looks_like_xml_rejects_plain_text() -> None:
    assert module.looks_like_xml(b"Erreur serveur") is False


def test_validate_feed_content_type_accepts_xml() -> None:
    module.validate_feed_content_type(
        build_downloaded_feed(),
        "Flux RSS"
    )


def test_validate_feed_content_type_rejects_html_content() -> None:
    downloaded_feed = build_downloaded_feed(
        content=b"<html><body>Erreur</body></html>",
        content_type="application/rss+xml"
    )

    with pytest.raises(RssInvalidContentTypeError):
        module.validate_feed_content_type(
            downloaded_feed,
            "Flux RSS"
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "text/html",
        "application/xhtml+xml"
    ]
)
def test_validate_feed_content_type_rejects_html_mime_type(
    content_type: str
) -> None:
    downloaded_feed = build_downloaded_feed(
        content=b"contenu quelconque",
        content_type=content_type
    )

    with pytest.raises(RssInvalidContentTypeError):
        module.validate_feed_content_type(
            downloaded_feed,
            "Flux RSS"
        )


def test_validate_feed_content_type_rejects_incompatible_type() -> None:
    downloaded_feed = build_downloaded_feed(
        content=b"contenu non XML",
        content_type="application/json"
    )

    with pytest.raises(RssInvalidContentTypeError):
        module.validate_feed_content_type(
            downloaded_feed,
            "Flux RSS"
        )


def test_validate_feed_content_type_accepts_bad_mime_with_xml() -> None:
    downloaded_feed = build_downloaded_feed(
        content=b"<?xml version='1.0'?><rss></rss>",
        content_type="text/plain"
    )

    module.validate_feed_content_type(
        downloaded_feed,
        "Flux RSS"
    )


# Nettoyage XML

def test_clean_xml_prefix_removes_utf8_bom() -> None:
    content, changed = module.clean_xml_prefix(
        module.UTF8_BOM + b"<?xml version='1.0'?><rss></rss>"
    )

    assert content.startswith(b"<?xml")
    assert changed is True


def test_clean_xml_prefix_removes_spaces_before_declaration() -> None:
    content, changed = module.clean_xml_prefix(
        b" \n\t<?xml version='1.0'?><rss></rss>"
    )

    assert content.startswith(b"<?xml")
    assert changed is True


def test_clean_xml_prefix_preserves_unchanged_content() -> None:
    original = b"<rss></rss>"

    content, changed = module.clean_xml_prefix(original)

    assert content == original
    assert changed is False


def test_validate_xml_normally_accepts_valid_xml() -> None:
    valid, error = module.validate_xml_normally(
        b"<rss><channel /></rss>"
    )

    assert valid is True
    assert error is None


def test_validate_xml_normally_returns_parsing_error() -> None:
    valid, error = module.validate_xml_normally(
        b"<rss>"
    )

    assert valid is False
    assert isinstance(error, Exception)


# Feedparser

def test_parse_with_feedparser_returns_feed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = FakeFeed(entries=[{"id": "1"}])

    monkeypatch.setattr(
        module.feedparser,
        "parse",
        lambda content: feed
    )

    result = module.parse_with_feedparser(
        b"<rss></rss>",
        "Flux RSS",
        tolerant_mode=False
    )

    assert result is feed


def test_parse_with_feedparser_converts_parser_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_parse(content: bytes) -> Any:
        raise ValueError("Erreur de parsing")

    monkeypatch.setattr(
        module.feedparser,
        "parse",
        failing_parse
    )

    with pytest.raises(RssParsingError) as caught:
        module.parse_with_feedparser(
            b"<rss></rss>",
            "Flux RSS",
            tolerant_mode=False
        )

    assert "Erreur de parsing" in caught.value.reason


def test_parse_with_feedparser_rejects_unexpected_type(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.feedparser,
        "parse",
        lambda content: ["invalid"]
    )

    with pytest.raises(RssParsingError) as caught:
        module.parse_with_feedparser(
            b"<rss></rss>",
            "Flux RSS",
            tolerant_mode=False
        )

    assert "list" in caught.value.reason


def test_parse_with_feedparser_accepts_bozo_with_entries(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = FakeFeed(
        entries=[{"id": "1"}],
        bozo=True,
        bozo_exception=ValueError("XML imparfait")
    )

    monkeypatch.setattr(
        module.feedparser,
        "parse",
        lambda content: feed
    )

    result = module.parse_with_feedparser(
        b"<rss>",
        "Flux RSS",
        tolerant_mode=True
    )

    assert result is feed


def test_parse_with_feedparser_rejects_bozo_without_entries(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = FakeFeed(
        entries=[],
        bozo=True,
        bozo_exception=ValueError("XML invalide")
    )

    monkeypatch.setattr(
        module.feedparser,
        "parse",
        lambda content: feed
    )

    with pytest.raises(RssInvalidFeedError) as caught:
        module.parse_with_feedparser(
            b"<rss>",
            "Flux RSS",
            tolerant_mode=True
        )

    assert "XML invalide" in caught.value.reason


# Parsing complet

def test_parse_feed_content_rejects_empty_content() -> None:
    downloaded_feed = build_downloaded_feed(
        content=b""
    )

    with pytest.raises(RssInvalidFeedError):
        module.parse_feed_content(
            downloaded_feed,
            "Flux RSS"
        )


def test_parse_feed_content_uses_strict_parsing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded_feed = build_downloaded_feed()
    expected_feed = FakeFeed()

    monkeypatch.setattr(
        module,
        "validate_feed_content_type",
        lambda downloaded_feed, source_name: None
    )
    monkeypatch.setattr(
        module,
        "validate_xml_normally",
        lambda content: (True, None)
    )
    monkeypatch.setattr(
        module,
        "parse_with_feedparser",
        lambda content, source_name, tolerant_mode: expected_feed
    )

    result = module.parse_feed_content(
        downloaded_feed,
        "Flux RSS"
    )

    assert result is expected_feed


def test_parse_feed_content_retries_after_prefix_cleaning(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded_feed = build_downloaded_feed(
        content=(
            module.UTF8_BOM
            + b" \n<?xml version='1.0'?><rss></rss>"
        )
    )
    expected_feed = FakeFeed()
    validation_results = iter([
        (False, ValueError("BOM")),
        (True, None)
    ])
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "validate_feed_content_type",
        lambda downloaded_feed, source_name: None
    )
    monkeypatch.setattr(
        module,
        "validate_xml_normally",
        lambda content: next(validation_results)
    )

    def fake_parse(
        content: bytes,
        source_name: str,
        tolerant_mode: bool
    ) -> Mapping[str, Any]:
        received["content"] = content
        received["tolerant_mode"] = tolerant_mode
        return expected_feed

    monkeypatch.setattr(
        module,
        "parse_with_feedparser",
        fake_parse
    )

    result = module.parse_feed_content(
        downloaded_feed,
        "Flux RSS"
    )

    assert result is expected_feed
    assert received["content"].startswith(b"<?xml")
    assert received["tolerant_mode"] is False


def test_parse_feed_content_uses_tolerant_parser(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded_feed = build_downloaded_feed(
        content=b"<rss>"
    )
    expected_feed = FakeFeed()
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "validate_feed_content_type",
        lambda downloaded_feed, source_name: None
    )
    monkeypatch.setattr(
        module,
        "validate_xml_normally",
        lambda content: (
            False,
            ValueError("XML invalide")
        )
    )
    monkeypatch.setattr(
        module,
        "clean_xml_prefix",
        lambda content: (content, False)
    )
    monkeypatch.setattr(
        module,
        "looks_like_html",
        lambda content: False
    )

    def fake_parse(
        content: bytes,
        source_name: str,
        tolerant_mode: bool
    ) -> Mapping[str, Any]:
        received["tolerant_mode"] = tolerant_mode
        return expected_feed

    monkeypatch.setattr(
        module,
        "parse_with_feedparser",
        fake_parse
    )

    result = module.parse_feed_content(
        downloaded_feed,
        "Flux RSS"
    )

    assert result is expected_feed
    assert received["tolerant_mode"] is True


def test_parse_feed_content_rejects_html_after_xml_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded_feed = build_downloaded_feed(
        content=b"<html><body>Erreur</body></html>"
    )

    monkeypatch.setattr(
        module,
        "validate_feed_content_type",
        lambda downloaded_feed, source_name: None
    )
    monkeypatch.setattr(
        module,
        "validate_xml_normally",
        lambda content: (
            False,
            ValueError("XML invalide")
        )
    )
    monkeypatch.setattr(
        module,
        "clean_xml_prefix",
        lambda content: (content, False)
    )

    with pytest.raises(RssInvalidContentTypeError):
        module.parse_feed_content(
            downloaded_feed,
            "Flux RSS"
        )


def test_parse_feed_downloads_then_parses(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    downloaded_feed = build_downloaded_feed()
    expected_feed = FakeFeed()
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "download_feed",
        lambda source_url, source_name: downloaded_feed
    )

    def fake_parse_feed_content(
        feed: module.DownloadedFeed,
        source_name: str
    ) -> Mapping[str, Any]:
        received["feed"] = feed
        received["source_name"] = source_name
        return expected_feed

    monkeypatch.setattr(
        module,
        "parse_feed_content",
        fake_parse_feed_content
    )

    result = module.parse_feed(
        "https://example.com/feed.xml",
        "Flux RSS"
    )

    assert result is expected_feed
    assert received == {
        "feed": downloaded_feed,
        "source_name": "Flux RSS"
    }


# Construction des articles

def test_build_rss_article_rejects_non_mapping() -> None:
    assert module.build_rss_article(
        entry="invalid",  # type: ignore[arg-type]
        source=build_source()
    ) == {}


def test_build_rss_article_rejects_missing_identifier() -> None:
    entry = {
        "title": "Article sans identifiant"
    }

    assert module.build_rss_article(
        entry,
        build_source()
    ) == {}


def test_build_rss_article_builds_expected_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "build_standard_article",
        lambda **kwargs: kwargs
    )

    entry = build_entry(
        media_content=[
            {
                "url": "https://example.com/image.jpg"
            }
        ],
        tags=[
            {
                "term": "International"
            }
        ]
    )
    source = build_source(category="")

    result = module.build_rss_article(
        entry,
        source
    )

    assert result == {
        "identifier": "article-1",
        "source": "Flux RSS de test",
        "title": "Titre de l'article",
        "text": "Contenu de l'article",
        "image_url": "https://example.com/image.jpg",
        "image_path": "",
        "published_at": "2026-07-20T10:00:00+00:00",
        "url": "https://example.com/article-1",
        "author": "Jean Dupont",
        "language": "fr",
        "category": "International",
        "label": "",
        "dataset_role": "acquisition"
    }


def test_build_rss_article_uses_source_defaults(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "build_standard_article",
        lambda **kwargs: kwargs
    )

    entry = {
        "guid": "article-guid",
        "title": "Titre"
    }

    result = module.build_rss_article(
        entry,
        {}
    )

    assert result["source"] == "RSS"
    assert result["language"] == ""
    assert result["category"] == ""
    assert result["dataset_role"] == "acquisition"


# Itération des entrées

def test_iter_rss_items_rejects_invalid_source_url() -> None:
    with pytest.raises(
        ValueError,
        match="URL absente ou invalide"
    ):
        module.iter_rss_items(
            build_source(url="")
        )


def test_iter_rss_items_returns_empty_when_feed_is_none(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "parse_feed",
        lambda source_url, source_name: None
    )

    assert module.iter_rss_items(build_source()) == []


def test_iter_rss_items_rejects_invalid_entries_collection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = FakeFeed()
    feed.entries = {"id": "article-1"}

    monkeypatch.setattr(
        module,
        "parse_feed",
        lambda source_url, source_name: feed
    )

    with pytest.raises(
        TypeError,
        match="ne contient pas une liste d'entrées"
    ):
        module.iter_rss_items(build_source())


def test_iter_rss_items_keeps_only_mapping_entries(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_entry = {
        "id": "article-1"
    }
    feed = FakeFeed(
        entries=[
            valid_entry,
            None,
            "invalid",
            42
        ]
    )

    monkeypatch.setattr(
        module,
        "parse_feed",
        lambda source_url, source_name: feed
    )

    result = module.iter_rss_items(build_source())

    assert result == [valid_entry]


def test_iter_rss_items_returns_empty_list_without_entries(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = FakeFeed(entries=[])

    monkeypatch.setattr(
        module,
        "parse_feed",
        lambda source_url, source_name: feed
    )

    assert module.iter_rss_items(build_source()) == []


# Validation des entrées

@pytest.mark.parametrize(
    "entry",
    [
        None,
        [],
        "invalid",
        42
    ]
)
def test_validate_rss_entry_rejects_non_mapping(
    entry: Any
) -> None:
    result = module.validate_rss_entry(
        entry,
        build_source()
    )

    assert result == (False, "entree_invalide")


def test_validate_rss_entry_rejects_missing_identifier() -> None:
    result = module.validate_rss_entry(
        {
            "title": "Article"
        },
        build_source()
    )

    assert result == (False, "identifiant_absent")


def test_validate_rss_entry_accepts_identifier() -> None:
    result = module.validate_rss_entry(
        {
            "guid": "article-guid"
        },
        build_source()
    )

    assert result == (True, "")


def test_validate_rss_article_age_delegates_to_common_rule(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "get_max_article_age_days",
        lambda source: 15
    )

    def fake_validate_article_age(
        article: Mapping[str, Any],
        max_article_age_days: int
    ) -> tuple[bool, str]:
        received["article"] = article
        received["max_article_age_days"] = max_article_age_days
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article_age",
        fake_validate_article_age
    )

    article = {
        "published_at": "2026-07-20T10:00:00+00:00"
    }

    result = module.validate_rss_article_age(
        article,
        build_source()
    )

    assert result == (True, "")
    assert received == {
        "article": article,
        "max_article_age_days": 15
    }


# Assemblage

def test_rss_adapter_uses_expected_callbacks() -> None:
    assert module.RSS_ADAPTER.iter_items is module.iter_rss_items
    assert module.RSS_ADAPTER.build_article is module.build_rss_article
    assert module.RSS_ADAPTER.validate_item is module.validate_rss_entry
    assert (
        module.RSS_ADAPTER.validate_article
        is module.validate_rss_article_age
    )
    assert module.RSS_ADAPTER.default_name == "RSS"


def test_rss_extractor_uses_expected_adapter() -> None:
    assert module.RSS_EXTRACTOR.adapter is module.RSS_ADAPTER
    assert module.RSS_EXTRACTOR.sources_file == module.SOURCES_FILE


# API publique

# API publique

def test_extract_articles_from_source_delegates_to_extractor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_articles = [{"title": "Article RSS"}]
    source = {"name": "Flux RSS"}
    extractor = module.RSS_EXTRACTOR

    def fake_extract_source(
        self: Any,
        current_source: Any
    ) -> list[dict[str, Any]]:
        assert current_source is source
        return expected_articles

    monkeypatch.setattr(
        type(extractor),
        "extract_source",
        fake_extract_source
    )

    result = module.extract_articles_from_source(source)

    assert result == expected_articles


def test_extract_articles_from_sources_delegates_to_extractor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_articles = [{"title": "Article RSS"}]
    sources = [{"name": "Flux RSS"}]
    extractor = module.RSS_EXTRACTOR

    def fake_extract_sources(
        self: Any,
        current_sources: Any
    ) -> list[dict[str, Any]]:
        assert current_sources is sources
        return expected_articles

    monkeypatch.setattr(
        type(extractor),
        "extract_sources",
        fake_extract_sources
    )

    result = module.extract_articles_from_sources(sources)

    assert result == expected_articles


def test_load_rss_sources_reloads_sources(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    expected_sources = [{"name": "Flux RSS"}]
    extractor = module.RSS_EXTRACTOR

    def fake_reload_sources(
        self: Any
    ) -> Any:
        calls.append("reload")
        return object()

    def fake_get_sources(
        self: Any
    ) -> list[dict[str, Any]]:
        return expected_sources

    monkeypatch.setattr(
        type(extractor),
        "reload_sources",
        fake_reload_sources
    )
    monkeypatch.setattr(
        type(extractor),
        "get_sources",
        fake_get_sources
    )

    result = module.load_rss_sources()

    assert result == expected_sources
    assert calls == ["reload"]


def test_extract_all_articles_runs_extractor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = ExtractorResult(
        name="RSS",
        source_type="rss",
        status="success",
        articles=[
            {
                "title": "Article RSS"
            }
        ]
    )
    extractor = module.RSS_EXTRACTOR

    def fake_run(
        self: Any
    ) -> ExtractorResult:
        return expected_result

    monkeypatch.setattr(
        type(extractor),
        "run",
        fake_run
    )

    result = module.extract_all_articles()

    assert result is expected_result