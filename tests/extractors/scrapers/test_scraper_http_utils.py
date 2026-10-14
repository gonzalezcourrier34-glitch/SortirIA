"""Tests des outils HTTP communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_http_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from src.extractors.scrapers import scraper_http_utils as module


# Fabriques

def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une configuration HTTP minimale."""

    source = {
        "respect_robots_txt": True,
        "request_delay_seconds": 1.0
    }
    source.update(overrides)
    return source


def build_request(
    url: str = "https://example.com/article"
) -> httpx.Request:
    """Construit une requête HTTPX minimale."""

    return httpx.Request(
        "GET",
        url
    )


def build_response(
    *,
    status_code: int = 200,
    url: str = "https://example.com/article",
    headers: Mapping[str, str] | None = None,
    text: str = "<html><body>Article</body></html>"
) -> httpx.Response:
    """Construit une réponse HTTPX minimale."""

    return httpx.Response(
        status_code=status_code,
        request=build_request(url),
        headers=dict(headers or {}),
        text=text
    )


class FakeClient:
    """Simule un client HTTPX retournant une réponse définie."""

    def __init__(
        self,
        response: httpx.Response | None = None,
        error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.urls: list[str] = []

    def get(
        self,
        url: str
    ) -> httpx.Response:
        """Retourne la réponse configurée ou lève l'erreur."""

        self.urls.append(url)

        if self.error is not None:
            raise self.error

        if self.response is None:
            raise RuntimeError("Aucune réponse configurée.")

        return self.response


# Erreur métier

@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("robots_interdit", "robots_interdit"),
        ("  page_vide  ", "page_vide"),
        ("", "page_inexploitable"),
        (None, "page_inexploitable")
    ]
)
def test_scraper_page_error_normalizes_reason(
    reason: Any,
    expected: str
) -> None:
    error = module.ScraperPageError(
        reason,
        "Erreur HTML"
    )

    assert str(error) == "Erreur HTML"
    assert error.reason == expected


# Métriques

def test_scraper_request_counter_can_be_reset_and_incremented() -> None:
    module.reset_scraper_requests_count()

    assert module.get_scraper_requests_count() == 0

    module.increment_scraper_requests_count()
    module.increment_scraper_requests_count()

    assert module.get_scraper_requests_count() == 2

    module.reset_scraper_requests_count()

    assert module.get_scraper_requests_count() == 0


# Configuration

@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            {},
            (
                True,
                1.0
            )
        ),
        (
            {
                "respect_robots_txt": False,
                "request_delay_seconds": 2.5
            },
            (
                False,
                2.5
            )
        ),
        (
            {
                "respect_robots_txt": "false",
                "request_delay_seconds": "0.25"
            },
            (
                False,
                0.25
            )
        ),
        (
            {
                "request_delay_seconds": -3
            },
            (
                True,
                0.0
            )
        ),
        (
            {
                "request_delay_seconds": "invalid"
            },
            (
                True,
                1.0
            )
        )
    ]
)
def test_get_scraper_http_options(
    source: Mapping[str, Any],
    expected: tuple[bool, float]
) -> None:
    assert module.get_scraper_http_options(source) == expected


def test_create_http_client_builds_expected_client(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}
    expected_client = object()

    def fake_http_client(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_client

    monkeypatch.setattr(
        module.httpx,
        "Client",
        fake_http_client
    )
    monkeypatch.setattr(
        module,
        "HTTP_HEADERS",
        {
            "User-Agent": "CheckItAI/1.0"
        }
    )
    monkeypatch.setattr(
        module,
        "REQUEST_TIMEOUT",
        12
    )

    result = module.create_http_client()

    assert result is expected_client
    assert received == {
        "headers": {
            "User-Agent": "CheckItAI/1.0",
            "Accept": module.HTML_ACCEPT_HEADER,
            "Accept-Language": module.HTML_ACCEPT_LANGUAGE_HEADER
        },
        "timeout": 12,
        "follow_redirects": True
    }


# Erreurs retentables

@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("Timeout"),
        httpx.ConnectError(
            "Connexion impossible",
            request=build_request()
        ),
        httpx.ReadError(
            "Lecture impossible",
            request=build_request()
        )
    ]
)
def test_is_retryable_http_error_accepts_network_errors(
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
        (503, True),
        (599, True),
        (600, False)
    ]
)
def test_is_retryable_http_error_maps_http_status(
    status_code: int,
    expected: bool
) -> None:
    response = build_response(
        status_code=status_code
    )
    error = httpx.HTTPStatusError(
        "Erreur HTTP",
        request=response.request,
        response=response
    )

    assert module.is_retryable_http_error(error) is expected


def test_is_retryable_http_error_rejects_other_errors() -> None:
    assert (
        module.is_retryable_http_error(
            ValueError("Erreur")
        )
        is False
    )


# Détection anti-bot

@pytest.mark.parametrize(
    "raw_html",
    [
        "",
        "   ",
        "<html><body>Article normal</body></html>"
    ]
)
def test_is_antibot_page_returns_false_for_normal_page(
    raw_html: str
) -> None:
    assert module.is_antibot_page(raw_html) is False


@pytest.mark.parametrize(
    "marker",
    module.ANTI_BOT_MARKERS
)
def test_is_antibot_page_detects_text_markers(
    marker: str
) -> None:
    raw_html = (
        "<html>"
        f"<head><title>{marker}</title></head>"
        "<body>Contenu</body>"
        "</html>"
    )

    assert module.is_antibot_page(raw_html) is True


@pytest.mark.parametrize(
    "html",
    [
        '<div id="captcha-box"></div>',
        '<div class="captcha-container"></div>',
        '<div id="security-challenge"></div>',
        '<div class="browser-challenge"></div>',
        '<iframe src="/captcha/index.html"></iframe>',
        '<script src="/challenge.js"></script>'
    ]
)
def test_is_antibot_page_detects_html_markers(
    html: str
) -> None:
    assert module.is_antibot_page(html) is True


# Téléchargement brut

def test_fetch_html_rejects_robots_denial(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: False
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: pytest.fail(
            "Le délai ne doit pas être appliqué."
        )
    )

    with pytest.raises(
        module.ScraperPageError,
        match="Accès interdit par robots.txt"
    ) as captured:
        module.fetch_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "robots_interdit"


def test_fetch_html_downloads_valid_page(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    response = build_response(
        headers={
            "Content-Type": "text/html; charset=utf-8"
        },
        text="<html><body>Article HTML</body></html>",
        url="https://example.com/final"
    )
    client = FakeClient(response=response)

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: True
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda url, minimum_delay: events.append(
            (
                "wait",
                url,
                minimum_delay
            )
        )
    )
    monkeypatch.setattr(
        module,
        "register_request",
        lambda url: events.append(
            (
                "register",
                url
            )
        )
    )
    monkeypatch.setattr(
        module,
        "is_antibot_page",
        lambda raw_html: False
    )

    module.reset_scraper_requests_count()

    result = module.fetch_html(
        client,  # type: ignore[arg-type]
        "https://example.com/article",
        respect_robots=True,
        request_delay_seconds=0.5
    )

    assert result == (
        "<html><body>Article HTML</body></html>",
        "https://example.com/final"
    )
    assert client.urls == [
        "https://example.com/article"
    ]
    assert module.get_scraper_requests_count() == 1
    assert events == [
        (
            "wait",
            "https://example.com/article",
            0.5
        ),
        (
            "register",
            "https://example.com/article"
        )
    ]


def test_fetch_html_skips_robots_check_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_response(
        headers={
            "Content-Type": "text/html"
        }
    )

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: pytest.fail(
            "robots.txt ne doit pas être consulté."
        )
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "register_request",
        lambda url: None
    )
    monkeypatch.setattr(
        module,
        "is_antibot_page",
        lambda raw_html: False
    )

    result = module.fetch_html(
        FakeClient(response=response),  # type: ignore[arg-type]
        "https://example.com/article",
        respect_robots=False
    )

    assert result[0]


def test_fetch_html_registers_request_when_client_fails(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    registered: list[str] = []
    error = ValueError("Erreur client")

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: True
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "register_request",
        registered.append
    )

    module.reset_scraper_requests_count()

    with pytest.raises(
        ValueError,
        match="Erreur client"
    ):
        module.fetch_html(
            FakeClient(error=error),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert registered == [
        "https://example.com/article"
    ]
    assert module.get_scraper_requests_count() == 1


def test_fetch_html_rejects_http_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_response(
        status_code=404
    )

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: True
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "register_request",
        lambda url: None
    )

    with pytest.raises(httpx.HTTPStatusError):
        module.fetch_html(
            FakeClient(response=response),  # type: ignore[arg-type]
            "https://example.com/article"
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "image/jpeg",
        "application/pdf"
    ]
)
def test_fetch_html_rejects_invalid_content_type(
    content_type: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_response(
        headers={
            "Content-Type": content_type
        }
    )

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: True
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "register_request",
        lambda url: None
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.fetch_html(
            FakeClient(response=response),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "type_contenu_invalide"


@pytest.mark.parametrize(
    "raw_html",
    [
        "",
        " ",
        "\n\t"
    ]
)
def test_fetch_html_rejects_empty_page(
    raw_html: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_response(
        headers={
            "Content-Type": "text/html"
        },
        text=raw_html
    )

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: True
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "register_request",
        lambda url: None
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.fetch_html(
            FakeClient(response=response),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "page_vide"


def test_fetch_html_rejects_antibot_page(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_response(
        headers={
            "Content-Type": "text/html"
        },
        text="<html>Verify you are human</html>"
    )

    monkeypatch.setattr(
        module,
        "is_url_allowed_by_robots",
        lambda url: True
    )
    monkeypatch.setattr(
        module,
        "wait_for_crawl_delay",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "register_request",
        lambda url: None
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.fetch_html(
            FakeClient(response=response),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "page_antibot"


# Normalisation des erreurs

def test_get_html_rejects_invalid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: ""
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            None
        )

    assert captured.value.reason == "url_invalide"


def test_get_html_delegates_to_fetch_html(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = (
        "<html>Article</html>",
        "https://example.com/final"
    )
    received: dict[str, Any] = {}
    client = FakeClient()

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )

    def fake_fetch_html(
        current_client: Any,
        url: str,
        respect_robots: bool,
        request_delay_seconds: float
    ) -> tuple[str, str]:
        received["client"] = current_client
        received["url"] = url
        received["respect_robots"] = respect_robots
        received["request_delay_seconds"] = request_delay_seconds
        return expected

    monkeypatch.setattr(
        module,
        "fetch_html",
        fake_fetch_html
    )

    result = module.get_html(
        client,  # type: ignore[arg-type]
        " https://example.com/article ",
        respect_robots=False,
        request_delay_seconds=0.25
    )

    assert result == expected
    assert received == {
        "client": client,
        "url": "https://example.com/article",
        "respect_robots": False,
        "request_delay_seconds": 0.25
    }


def test_get_html_preserves_scraper_page_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_error = module.ScraperPageError(
        "page_vide",
        "Page vide"
    )

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "fetch_html",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            expected_error
        )
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value is expected_error


def test_get_html_maps_timeout_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    error = httpx.TimeoutException(
        "Timeout",
        request=build_request()
    )

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "fetch_html",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "delai_depasse"
    assert captured.value.__cause__ is error


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (400, "statut_http_invalide"),
        (403, "page_antibot"),
        (404, "statut_http_invalide"),
        (429, "page_antibot"),
        (500, "statut_http_invalide")
    ]
)
def test_get_html_maps_http_status_error(
    status_code: int,
    expected_reason: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_response(
        status_code=status_code
    )
    error = httpx.HTTPStatusError(
        "Erreur HTTP",
        request=response.request,
        response=response
    )

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "fetch_html",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == expected_reason
    assert captured.value.__cause__ is error


def test_get_html_maps_network_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    error = httpx.ConnectError(
        "Connexion impossible",
        request=build_request()
    )

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "fetch_html",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "erreur_reseau"
    assert captured.value.__cause__ is error


def test_get_html_maps_generic_http_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    error = httpx.ProtocolError(
        "Protocole invalide",
        request=build_request()
    )

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "fetch_html",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "erreur_http"
    assert captured.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [
        TypeError("Type invalide"),
        ValueError("Valeur invalide"),
        UnicodeError("Unicode invalide")
    ]
)
def test_get_html_maps_unusable_page_errors(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda url: "https://example.com/article"
    )
    monkeypatch.setattr(
        module,
        "fetch_html",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(
        module.ScraperPageError
    ) as captured:
        module.get_html(
            FakeClient(),  # type: ignore[arg-type]
            "https://example.com/article"
        )

    assert captured.value.reason == "page_inexploitable"
    assert captured.value.__cause__ is error