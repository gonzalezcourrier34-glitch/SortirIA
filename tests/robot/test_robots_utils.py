"""Tests de la gestion centralisée du protocole robots.txt.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/robot/test_robots_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from threading import Lock
from typing import Any
from urllib.robotparser import RobotFileParser

import httpx
import pytest

import src.robot.robots_utils as module
from config.constants import (
    ROBOTS_REASON_ALLOWED,
    ROBOTS_REASON_CHECK_ERROR,
    ROBOTS_REASON_DENIED,
    ROBOTS_REASON_INVALID_URL,
    ROBOTS_REASON_UNAVAILABLE
)
from src.robot.robots_utils import (
    ROBOTS_CACHE_TTL_SECONDS,
    ROBOTS_FAILURE_TTL_SECONDS,
    ROBOTS_MAX_SIZE_BYTES,
    RobotsDecision,
    RobotsPolicy,
    build_default_parser,
    build_loaded_policy,
    build_policy,
    cache_policy,
    clear_robots_cache,
    decode_robots_content,
    fetch_robots_policy,
    get_cached_policy,
    get_effective_port,
    get_origin,
    get_origin_lock,
    get_policy_lock,
    get_request_lock,
    get_robots_decision,
    get_robots_headers,
    get_robots_policy,
    get_robots_url,
    is_allowed_robots_redirect,
    is_url_allowed_by_robots,
    looks_like_html,
    normalize_delay,
    parse_content_length,
    read_limited_content,
    register_request,
    wait_for_crawl_delay
)


# Doubles HTTP

class FakeStreamResponse:
    """Réponse HTTP simulée compatible avec httpx.Client.stream()."""

    def __init__(
        self,
        *,
        url: str = "https://example.com/robots.txt",
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        encoding: str | None = "utf-8",
        chunks: list[bytes] | None = None
    ) -> None:
        self.url = httpx.URL(url)
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self.encoding = encoding
        self._content = content
        self._chunks = chunks

    def __enter__(self) -> FakeStreamResponse:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any
    ) -> None:
        return None

    def iter_bytes(self) -> Any:
        if self._chunks is not None:
            yield from self._chunks
            return

        if self._content:
            yield self._content


class FakeHttpClient:
    """Client HTTP simulé retournant une réponse configurée."""

    response: FakeStreamResponse | None = None
    raised_error: BaseException | None = None
    received_arguments: dict[str, Any] = {}
    requested_method: str | None = None
    requested_url: str | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).received_arguments = kwargs

    def __enter__(self) -> FakeHttpClient:
        if type(self).raised_error is not None:
            raise type(self).raised_error

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any
    ) -> None:
        return None

    def stream(
        self,
        method: str,
        url: str
    ) -> FakeStreamResponse:
        type(self).requested_method = method
        type(self).requested_url = url

        if type(self).raised_error is not None:
            raise type(self).raised_error

        assert type(self).response is not None
        return type(self).response


@pytest.fixture(autouse=True)
def reset_robots_state() -> None:
    """Réinitialise les caches et les doubles entre les tests."""

    clear_robots_cache()

    with module._CACHE_LOCK:
        module._POLICY_LOCKS.clear()
        module._REQUEST_LOCKS.clear()

    FakeHttpClient.response = None
    FakeHttpClient.raised_error = None
    FakeHttpClient.received_arguments = {}
    FakeHttpClient.requested_method = None
    FakeHttpClient.requested_url = None


def make_response(
    *,
    url: str = "https://example.com/robots.txt",
    status_code: int = 200,
    content: bytes = b"",
    headers: dict[str, str] | None = None,
    encoding: str | None = "utf-8"
) -> httpx.Response:
    """Construit une réponse httpx réelle pour les fonctions unitaires."""

    request = httpx.Request("GET", url)
    response = httpx.Response(
        status_code=status_code,
        headers=headers,
        content=content,
        request=request
    )

    if encoding is not None:
        response.encoding = encoding

    return response

def make_policy(
    *,
    allow: bool = True,
    robots_url: str = "https://example.com/robots.txt",
    available: bool = True,
    crawl_delay: float = 0.0,
    reason: str = ROBOTS_REASON_ALLOWED,
    error: str = ""
) -> RobotsPolicy:
    """Construit une politique simple pour les tests."""

    return build_policy(
        robots_url=robots_url,
        allow=allow,
        available=available,
        crawl_delay=crawl_delay,
        reason=reason,
        error=error
    )


# URL

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://Example.COM/article?id=1#section",
            "https://example.com"
        ),
        (
            "http://example.com:8080/path",
            "http://example.com:8080"
        ),
        (
            "https://example.com/",
            "https://example.com"
        ),
        ("", ""),
        ("not-an-url", ""),
        ("ftp://example.com/file", "")
    ]
)
def test_get_origin(
    url: str,
    expected: str
) -> None:
    assert get_origin(url) == expected


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (
            "https://example.com",
            "https://example.com/robots.txt"
        ),
        (
            "https://example.com/",
            "https://example.com/robots.txt"
        ),
        ("", "")
    ]
)
def test_get_robots_url(
    origin: str,
    expected: str
) -> None:
    assert get_robots_url(origin) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://example.com", 80),
        ("https://example.com", 443),
        ("https://example.com:8443", 8443),
        ("ftp://example.com", None),
        ("invalid", None)
    ]
)
def test_get_effective_port(
    url: str,
    expected: int | None
) -> None:
    assert get_effective_port(url) == expected


@pytest.mark.parametrize(
    ("original_url", "final_url", "expected"),
    [
        (
            "https://example.com/robots.txt",
            "https://example.com/robots.txt",
            True
        ),
        (
            "https://example.com/robots.txt",
            "https://example.com/security/robots.txt",
            True
        ),
        (
            "http://example.com/robots.txt",
            "https://example.com/robots.txt",
            True
        ),
        (
            "https://example.com/robots.txt",
            "http://example.com/robots.txt",
            False
        ),
        (
            "https://example.com/robots.txt",
            "https://other.example/robots.txt",
            False
        ),
        (
            "https://example.com:8443/robots.txt",
            "https://example.com/robots.txt",
            False
        ),
        (
            "http://example.com:8080/robots.txt",
            "https://example.com/robots.txt",
            False
        ),
        ("invalid", "https://example.com/robots.txt", False)
    ]
)
def test_is_allowed_robots_redirect(
    original_url: str,
    final_url: str,
    expected: bool
) -> None:
    assert (
        is_allowed_robots_redirect(original_url, final_url)
        is expected
    )


# Politiques

def test_build_default_parser_allows_everything() -> None:
    parser = build_default_parser(
        "https://example.com/robots.txt",
        allow=True
    )

    assert parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/private"
    )


def test_build_default_parser_denies_everything() -> None:
    parser = build_default_parser(
        "https://example.com/robots.txt",
        allow=False
    )

    assert not parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/public"
    )


def test_build_policy() -> None:
    before = module.monotonic()

    policy = build_policy(
        robots_url="https://example.com/robots.txt",
        allow=True,
        available=False,
        allow_without_file=True,
        crawl_delay=-5,
        ttl_seconds=60,
        reason=ROBOTS_REASON_ALLOWED,
        error="  erreur  "
    )

    after = module.monotonic()

    assert policy.robots_url == "https://example.com/robots.txt"
    assert policy.available is False
    assert policy.allow_without_file is True
    assert policy.crawl_delay == 0.0
    assert before + 60 <= policy.expires_at <= after + 60
    assert policy.reason == ROBOTS_REASON_ALLOWED
    assert policy.error == "erreur"
    assert policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/article"
    )


def test_robots_decision_denied_property() -> None:
    allowed = RobotsDecision(
        allowed=True,
        reason=ROBOTS_REASON_ALLOWED,
        url="https://example.com",
        origin="https://example.com",
        robots_url="https://example.com/robots.txt"
    )
    denied = RobotsDecision(
        allowed=False,
        reason=ROBOTS_REASON_DENIED,
        url="https://example.com/private",
        origin="https://example.com",
        robots_url="https://example.com/robots.txt"
    )

    assert allowed.denied is False
    assert denied.denied is True


# Verrous et cache

def test_get_origin_lock_reuses_same_lock() -> None:
    locks: dict[str, Lock] = {}

    first_lock = get_origin_lock(locks, "https://example.com")
    second_lock = get_origin_lock(locks, "https://example.com")

    assert first_lock is second_lock


def test_policy_and_request_locks_are_distinct() -> None:
    policy_lock = get_policy_lock("https://example.com")
    request_lock = get_request_lock("https://example.com")

    assert policy_lock is not request_lock


def test_cache_policy_and_get_cached_policy() -> None:
    policy = make_policy()

    returned_policy = cache_policy(
        "https://example.com",
        policy
    )

    assert returned_policy is policy
    assert get_cached_policy("https://example.com") is policy


def test_get_cached_policy_removes_expired_policy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = build_default_parser(
        "https://example.com/robots.txt",
        True
    )
    policy = RobotsPolicy(
        parser=parser,
        robots_url="https://example.com/robots.txt",
        available=True,
        allow_without_file=False,
        crawl_delay=0.0,
        expires_at=10.0
    )

    module._POLICY_CACHE["https://example.com"] = policy
    monkeypatch.setattr(module, "monotonic", lambda: 10.0)

    assert get_cached_policy("https://example.com") is None
    assert "https://example.com" not in module._POLICY_CACHE


def test_clear_robots_cache_clears_all_runtime_values() -> None:
    policy = make_policy()

    module._POLICY_CACHE["https://example.com"] = policy
    module._LAST_REQUEST_AT["https://example.com"] = 12.0
    policy_lock = get_policy_lock("https://example.com")
    request_lock = get_request_lock("https://example.com")

    clear_robots_cache()

    assert module._POLICY_CACHE == {}
    assert module._LAST_REQUEST_AT == {}

    assert (
        module._POLICY_LOCKS["https://example.com"]
        is policy_lock
    )
    assert (
        module._REQUEST_LOCKS["https://example.com"]
        is request_lock
    )


def test_clear_robots_cache_clears_one_origin() -> None:
    first_policy = make_policy()
    second_policy = build_policy(
        "https://other.example/robots.txt",
        True,
        True
    )

    module._POLICY_CACHE["https://example.com"] = first_policy
    module._POLICY_CACHE["https://other.example"] = second_policy
    module._LAST_REQUEST_AT["https://example.com"] = 10.0
    module._LAST_REQUEST_AT["https://other.example"] = 20.0

    clear_robots_cache("https://example.com/article")

    assert "https://example.com" not in module._POLICY_CACHE
    assert "https://example.com" not in module._LAST_REQUEST_AT
    assert "https://other.example" in module._POLICY_CACHE
    assert "https://other.example" in module._LAST_REQUEST_AT


# En-têtes et taille

def test_get_robots_headers() -> None:
    headers = get_robots_headers()

    assert headers["User-Agent"] == module.USER_AGENT
    assert headers["Accept"] == "text/plain,*/*;q=0.1"

    for name, value in module.HTTP_HEADERS.items():
        if name.lower() != "user-agent":
            assert headers[name] == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123", 123),
        ("0", 0),
        ("-5", 0),
        ("invalid", None),
        ("", None)
    ]
)
def test_parse_content_length(
    value: str,
    expected: int | None
) -> None:
    response = make_response(
        headers={"Content-Length": value}
    )

    assert parse_content_length(response) == expected


def test_parse_content_length_without_header() -> None:
    response = make_response()

    assert parse_content_length(response) is None


def test_read_limited_content() -> None:
    response = FakeStreamResponse(
        chunks=[b"User-agent: *\n", b"Disallow: /private\n"],
        headers={"Content-Length": "34"}
    )

    content = read_limited_content(response)

    assert content == b"User-agent: *\nDisallow: /private\n"


def test_read_limited_content_rejects_announced_oversize() -> None:
    response = FakeStreamResponse(
        headers={
            "Content-Length": str(ROBOTS_MAX_SIZE_BYTES + 1)
        }
    )

    with pytest.raises(
        ValueError,
        match="annoncé comme trop volumineux"
    ):
        read_limited_content(response)


def test_read_limited_content_rejects_stream_oversize() -> None:
    response = FakeStreamResponse(
        chunks=[
            b"a" * ROBOTS_MAX_SIZE_BYTES,
            b"b"
        ]
    )

    with pytest.raises(
        ValueError,
        match="Fichier robots.txt trop volumineux"
    ):
        read_limited_content(response)


def test_read_limited_content_accepts_exact_limit() -> None:
    response = FakeStreamResponse(
        chunks=[b"a" * ROBOTS_MAX_SIZE_BYTES]
    )

    content = read_limited_content(response)

    assert len(content) == ROBOTS_MAX_SIZE_BYTES


# Décodage et contenu

def test_decode_robots_content_uses_response_encoding() -> None:
    response = make_response()
    response.encoding = "latin-1"

    result = decode_robots_content(
        response,
        "Disallow: /café".encode("latin-1")
    )

    assert result == "Disallow: /café"


def test_decode_robots_content_falls_back_to_utf8() -> None:
    response = make_response()
    response.encoding = "unknown-encoding"

    result = decode_robots_content(
        response,
        "User-agent: *".encode()
    )

    assert result == "User-agent: *"


def test_decode_robots_content_replaces_invalid_bytes() -> None:
    response = make_response()
    response.encoding = "utf-8"

    result = decode_robots_content(
        response,
        b"User-agent: *\xff"
    )

    assert result == "User-agent: *�"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("<!DOCTYPE html><html></html>", True),
        ("   <html><body>Error</body></html>", True),
        ("\n<head><title>Error</title></head>", True),
        ("<body>Error</body>", True),
        ("User-agent: *\nDisallow: /private", False),
        ("# robots.txt\nUser-agent: *", False),
        ("", False)
    ]
)
def test_looks_like_html(
    content: str,
    expected: bool
) -> None:
    assert looks_like_html(content) is expected


# Construction d'une politique chargée

def test_build_loaded_policy() -> None:
    response = make_response(
        url="https://example.com/robots.txt"
    )
    content = (
        b"User-agent: *\n"
        b"Disallow: /private\n"
        b"Crawl-delay: 5\n"
    )

    policy = build_loaded_policy(
        response,
        "https://example.com/robots.txt",
        content
    )

    assert policy.available is True
    assert policy.allow_without_file is False
    assert policy.crawl_delay == 5.0
    assert policy.reason == ROBOTS_REASON_ALLOWED
    assert policy.robots_url == "https://example.com/robots.txt"

    assert policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/public"
    )
    assert not policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/private/page"
    )


def test_build_loaded_policy_prefers_specific_user_agent_delay(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeParser:
        def set_url(self, url: str) -> None:
            return None

        def parse(self, lines: list[str]) -> None:
            return None

        def crawl_delay(self, user_agent: str) -> float | None:
            if user_agent == module.USER_AGENT:
                return 7.0

            return 3.0

    monkeypatch.setattr(module, "RobotFileParser", FakeParser)

    response = make_response()
    policy = build_loaded_policy(
        response,
        "https://example.com/robots.txt",
        b"User-agent: *"
    )

    assert policy.crawl_delay == 7.0


def test_build_loaded_policy_uses_wildcard_delay(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeParser:
        def set_url(self, url: str) -> None:
            return None

        def parse(self, lines: list[str]) -> None:
            return None

        def crawl_delay(self, user_agent: str) -> float | None:
            if user_agent == module.USER_AGENT:
                return None

            return 4.0

    monkeypatch.setattr(module, "RobotFileParser", FakeParser)

    response = make_response()
    policy = build_loaded_policy(
        response,
        "https://example.com/robots.txt",
        b"User-agent: *"
    )

    assert policy.crawl_delay == 4.0


def test_build_loaded_policy_normalizes_invalid_delay(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeParser:
        def set_url(self, url: str) -> None:
            return None

        def parse(self, lines: list[str]) -> None:
            return None

        def crawl_delay(self, user_agent: str) -> str:
            return "invalid"

    monkeypatch.setattr(module, "RobotFileParser", FakeParser)

    response = make_response()
    policy = build_loaded_policy(
        response,
        "https://example.com/robots.txt",
        b"User-agent: *"
    )

    assert policy.crawl_delay == 0.0


def test_build_loaded_policy_rejects_html() -> None:
    response = make_response()

    with pytest.raises(
        ValueError,
        match="page HTML"
    ):
        build_loaded_policy(
            response,
            "https://example.com/robots.txt",
            b"<!DOCTYPE html><html></html>"
        )


# Téléchargement complet

def test_fetch_robots_policy_rejects_invalid_url() -> None:
    policy = fetch_robots_policy("invalid")

    assert policy.available is False
    assert policy.allow_without_file is False
    assert policy.reason == ROBOTS_REASON_INVALID_URL
    assert policy.error == "URL invalide."
    assert policy.robots_url == ""

    assert not policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/article"
    )


def test_fetch_robots_policy_loads_valid_file(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        url="https://example.com/robots.txt",
        status_code=200,
        content=(
            b"User-agent: *\n"
            b"Disallow: /private\n"
        )
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is True
    assert policy.reason == ROBOTS_REASON_ALLOWED
    assert FakeHttpClient.requested_method == "GET"
    assert (
        FakeHttpClient.requested_url
        == "https://example.com/robots.txt"
    )
    assert FakeHttpClient.received_arguments["follow_redirects"] is True
    assert (
        FakeHttpClient.received_arguments["timeout"]
        == module.REQUEST_TIMEOUT
    )
    assert not policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/private"
    )


@pytest.mark.parametrize("status_code", [404, 410])
def test_fetch_robots_policy_allows_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        status_code=status_code
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.allow_without_file is True
    assert policy.reason == ROBOTS_REASON_ALLOWED
    assert policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/private"
    )


@pytest.mark.parametrize("status_code", [401, 403])
def test_fetch_robots_policy_denies_protected_file(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        status_code=status_code
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is True
    assert policy.allow_without_file is False
    assert policy.reason == ROBOTS_REASON_DENIED
    assert policy.error == f"HTTP {status_code}"
    assert not policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/article"
    )


@pytest.mark.parametrize("status_code", [400, 429, 500, 503])
def test_fetch_robots_policy_denies_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        status_code=status_code
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert policy.error == f"Erreur HTTP {status_code}."
    assert not policy.parser.can_fetch(
        module.USER_AGENT,
        "https://example.com/article"
    )


def test_fetch_robots_policy_allows_http_to_https_redirect(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        url="https://example.com/robots.txt",
        content=b"User-agent: *\nAllow: /\n"
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "http://example.com/article"
    )

    assert policy.available is True
    assert policy.reason == ROBOTS_REASON_ALLOWED


def test_fetch_robots_policy_rejects_cross_origin_redirect(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        url="https://other.example/robots.txt",
        content=b"User-agent: *\nAllow: /\n"
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert "autre origine" in policy.error
    assert "https://other.example/robots.txt" in policy.error


def test_fetch_robots_policy_rejects_large_content(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        headers={
            "Content-Length": str(ROBOTS_MAX_SIZE_BYTES + 1)
        }
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert "trop volumineux" in policy.error


def test_fetch_robots_policy_rejects_html(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.response = FakeStreamResponse(
        content=b"<!DOCTYPE html><html><body>Error</body></html>"
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert "page HTML" in policy.error


def test_fetch_robots_policy_handles_timeout(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.raised_error = httpx.TimeoutException(
        "timeout"
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert policy.error == "Délai de connexion dépassé."


def test_fetch_robots_policy_handles_network_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/robots.txt"
    )
    FakeHttpClient.raised_error = httpx.ConnectError(
        "connexion impossible",
        request=request
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert "Erreur réseau" in policy.error
    assert "connexion impossible" in policy.error


def test_fetch_robots_policy_handles_http_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/robots.txt"
    )
    FakeHttpClient.raised_error = httpx.ProtocolError(
        "protocole invalide",
        request=request
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert "Erreur HTTP" in policy.error
    assert "protocole invalide" in policy.error


def test_fetch_robots_policy_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeHttpClient.raised_error = RuntimeError(
        "erreur inattendue"
    )
    monkeypatch.setattr(module.httpx, "Client", FakeHttpClient)

    policy = fetch_robots_policy(
        "https://example.com/article"
    )

    assert policy.available is False
    assert policy.reason == ROBOTS_REASON_UNAVAILABLE
    assert policy.error == "RuntimeError: erreur inattendue"


# Récupération d'une politique

def test_get_robots_policy_rejects_invalid_url() -> None:
    policy = get_robots_policy("invalid")

    assert policy.reason == ROBOTS_REASON_INVALID_URL
    assert policy.available is False


def test_get_robots_policy_returns_cached_policy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = make_policy()
    cache_policy("https://example.com", policy)

    def fail_fetch(url: str) -> RobotsPolicy:
        raise AssertionError("Le téléchargement ne doit pas être appelé.")

    monkeypatch.setattr(module, "fetch_robots_policy", fail_fetch)

    result = get_robots_policy(
        "https://example.com/article"
    )

    assert result is policy


def test_get_robots_policy_fetches_and_caches_policy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = make_policy()
    calls: list[str] = []

    def fake_fetch(url: str) -> RobotsPolicy:
        calls.append(url)
        return policy

    monkeypatch.setattr(module, "fetch_robots_policy", fake_fetch)

    first_result = get_robots_policy(
        "https://example.com/article"
    )
    second_result = get_robots_policy(
        "https://example.com/other"
    )

    assert first_result is policy
    assert second_result is policy
    assert calls == ["https://example.com/article"]
    assert get_cached_policy("https://example.com") is policy


# Décisions

def test_get_robots_decision_rejects_invalid_url() -> None:
    decision = get_robots_decision("invalid")

    assert decision.allowed is False
    assert decision.denied is True
    assert decision.reason == ROBOTS_REASON_INVALID_URL
    assert decision.url == "invalid"
    assert decision.origin == ""
    assert decision.robots_url == ""
    assert decision.error == "URL invalide."


@pytest.mark.parametrize(
    "reason",
    [
        ROBOTS_REASON_DENIED,
        ROBOTS_REASON_UNAVAILABLE,
        ROBOTS_REASON_INVALID_URL
    ]
)
def test_get_robots_decision_propagates_policy_failure(
    monkeypatch: pytest.MonkeyPatch,
    reason: str
) -> None:
    policy = make_policy(
        allow=False,
        available=False,
        crawl_delay=3.0,
        reason=reason,
        error="Erreur simulée"
    )
    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )

    decision = get_robots_decision(
        "https://example.com/article"
    )

    assert decision.allowed is False
    assert decision.reason == reason
    assert decision.crawl_delay == 3.0
    assert decision.error == "Erreur simulée"


def test_get_robots_decision_allows_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = RobotFileParser()
    parser.parse([
        "User-agent: *",
        "Allow: /"
    ])
    policy = RobotsPolicy(
        parser=parser,
        robots_url="https://example.com/robots.txt",
        available=True,
        allow_without_file=False,
        crawl_delay=2.0,
        expires_at=module.monotonic() + 60
    )

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )

    decision = get_robots_decision(
        "https://example.com/article"
    )

    assert decision.allowed is True
    assert decision.reason == ROBOTS_REASON_ALLOWED
    assert decision.crawl_delay == 2.0


def test_get_robots_decision_denies_disallowed_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = RobotFileParser()
    parser.parse([
        "User-agent: *",
        "Disallow: /private"
    ])
    policy = RobotsPolicy(
        parser=parser,
        robots_url="https://example.com/robots.txt",
        available=True,
        allow_without_file=False,
        crawl_delay=0.0,
        expires_at=module.monotonic() + 60
    )

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )

    decision = get_robots_decision(
        "https://example.com/private/article"
    )

    assert decision.allowed is False
    assert decision.reason == ROBOTS_REASON_DENIED


def test_get_robots_decision_handles_parser_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenParser:
        def can_fetch(
            self,
            user_agent: str,
            url: str
        ) -> bool:
            raise ValueError("Parseur indisponible")

    policy = RobotsPolicy(
        parser=BrokenParser(),
        robots_url="https://example.com/robots.txt",
        available=True,
        allow_without_file=False,
        crawl_delay=1.0,
        expires_at=module.monotonic() + 60
    )

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )

    decision = get_robots_decision(
        "https://example.com/article"
    )

    assert decision.allowed is False
    assert decision.reason == ROBOTS_REASON_CHECK_ERROR
    assert decision.crawl_delay == 1.0
    assert decision.error == "Parseur indisponible"


def test_is_url_allowed_by_robots(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = RobotsDecision(
        allowed=True,
        reason=ROBOTS_REASON_ALLOWED,
        url="https://example.com/article",
        origin="https://example.com",
        robots_url="https://example.com/robots.txt"
    )

    monkeypatch.setattr(
        module,
        "get_robots_decision",
        lambda url: decision
    )

    assert is_url_allowed_by_robots(
        "https://example.com/article"
    )


# Crawl-delay

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5.0),
        (2.5, 2.5),
        ("3.5", 3.5),
        (0, 0.0),
        (-1, 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        (float("nan"), 0.0),
        ("invalid", 0.0),
        (None, 0.0)
    ]
)
def test_normalize_delay(
    value: Any,
    expected: float
) -> None:
    assert normalize_delay(value) == expected


def test_wait_for_crawl_delay_ignores_invalid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: calls.append(url)
    )

    wait_for_crawl_delay("invalid", minimum_delay=5)

    assert calls == []


def test_wait_for_crawl_delay_ignores_zero_delay(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = make_policy(crawl_delay=0.0)
    sleeps: list[float] = []

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )
    monkeypatch.setattr(
        module,
        "sleep",
        lambda delay: sleeps.append(delay)
    )

    wait_for_crawl_delay(
        "https://example.com/article"
    )

    assert sleeps == []
    assert "https://example.com" not in module._LAST_REQUEST_AT


def test_wait_for_crawl_delay_uses_greatest_delay(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = make_policy(crawl_delay=2.0)
    times = iter([10.0, 12.0])
    sleeps: list[float] = []

    module._LAST_REQUEST_AT["https://example.com"] = 6.0

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )
    monkeypatch.setattr(
        module,
        "monotonic",
        lambda: next(times)
    )
    monkeypatch.setattr(
        module,
        "sleep",
        lambda delay: sleeps.append(delay)
    )

    wait_for_crawl_delay(
        "https://example.com/article",
        minimum_delay=5.0
    )

    assert sleeps == [1.0]
    assert module._LAST_REQUEST_AT["https://example.com"] == 12.0


def test_wait_for_crawl_delay_does_not_sleep_when_elapsed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = make_policy(crawl_delay=3.0)
    times = iter([20.0, 21.0])
    sleeps: list[float] = []

    module._LAST_REQUEST_AT["https://example.com"] = 10.0

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )
    monkeypatch.setattr(
        module,
        "monotonic",
        lambda: next(times)
    )
    monkeypatch.setattr(
        module,
        "sleep",
        lambda delay: sleeps.append(delay)
    )

    wait_for_crawl_delay(
        "https://example.com/article"
    )

    assert sleeps == []
    assert module._LAST_REQUEST_AT["https://example.com"] == 21.0


def test_wait_for_crawl_delay_registers_first_request(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = make_policy(crawl_delay=2.0)

    monkeypatch.setattr(
        module,
        "get_robots_policy",
        lambda url: policy
    )
    monkeypatch.setattr(
        module,
        "monotonic",
        lambda: 50.0
    )

    wait_for_crawl_delay(
        "https://example.com/article"
    )

    assert module._LAST_REQUEST_AT["https://example.com"] == 50.0


def test_register_request_ignores_invalid_url() -> None:
    register_request("invalid")

    assert module._LAST_REQUEST_AT == {}


def test_register_request_records_origin(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "monotonic",
        lambda: 75.0
    )

    register_request(
        "https://example.com/article?id=1"
    )

    assert module._LAST_REQUEST_AT == {
        "https://example.com": 75.0
    }


# Constantes

def test_cache_configuration() -> None:
    assert ROBOTS_CACHE_TTL_SECONDS == 3600
    assert ROBOTS_FAILURE_TTL_SECONDS == 60
    assert ROBOTS_MAX_SIZE_BYTES == 1_000_000