"""Tests de sécurité du téléchargement des images CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_downloader_security.py` (commit 93632c5).
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from requests.adapters import HTTPAdapter

import src.images.image_downloader as module
from src.images.image_downloader import (
    ALLOWED_REMOTE_PORTS,
    BLOCKED_HOSTNAMES,
    IMAGE_STATUS_BLOCKED_REDIRECT,
    MAX_IMAGE_REDIRECTS,
    UnsafeImageUrlError,
    create_http_session,
    get_http_session,
    get_redirect_url,
    is_forbidden_ip_address,
    request_image_response,
    resolve_hostname_addresses,
    validate_remote_image_url
)


# Faux objets HTTP

class FakeResponse:
    """Simule une réponse HTTP utilisée pendant les redirections."""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self) -> None:
        """Marque la réponse comme fermée."""

        self.closed = True


class FakeSession:
    """Simule une session requests avec une suite de réponses."""

    def __init__(
        self,
        responses: list[FakeResponse]
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: tuple[int, int],
        stream: bool,
        allow_redirects: bool
    ) -> FakeResponse:
        """Retourne la prochaine réponse configurée."""

        self.calls.append({
            "url": url,
            "headers": headers,
            "timeout": timeout,
            "stream": stream,
            "allow_redirects": allow_redirects
        })

        if not self.responses:
            raise AssertionError(
                "Aucune réponse HTTP simulée n'est disponible."
            )

        return self.responses.pop(0)


# Session HTTP

def test_create_http_session_returns_configured_session(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "MAX_RETRIES", 2)
    monkeypatch.setattr(module, "RETRY_DELAY_SECONDS", 0.5)
    monkeypatch.setattr(module, "MAX_RETRY_DELAY_SECONDS", 4.0)
    monkeypatch.setattr(module, "HTTP_POOL_CONNECTIONS", 3)
    monkeypatch.setattr(module, "HTTP_POOL_MAXSIZE", 7)
    monkeypatch.setattr(module, "USER_AGENT", "CheckItAI-Test/1.0")
    monkeypatch.setattr(
        module,
        "RETRYABLE_HTTP_METHODS",
        {"GET", "HEAD"}
    )
    monkeypatch.setattr(
        module,
        "RETRYABLE_HTTP_STATUS_CODES",
        {429, 500, 502, 503}
    )

    session = create_http_session()

    assert isinstance(session, requests.Session)
    assert session.headers["User-Agent"] == "CheckItAI-Test/1.0"
    assert "image/avif" in session.headers["Accept"]

    http_adapter = session.adapters["http://"]
    https_adapter = session.adapters["https://"]

    assert isinstance(http_adapter, HTTPAdapter)
    assert isinstance(https_adapter, HTTPAdapter)
    assert http_adapter is https_adapter
    assert http_adapter._pool_connections == 3
    assert http_adapter._pool_maxsize == 7

    retry = http_adapter.max_retries

    assert retry.total == 2
    assert retry.connect == 2
    assert retry.read == 2
    assert retry.status == 2
    assert retry.other == 0
    assert retry.allowed_methods == frozenset({
        "GET",
        "HEAD"
    })
    assert set(retry.status_forcelist) == {
        500,
        502,
        503
    }
    assert retry.backoff_factor == 0.5
    assert retry.backoff_max == 4.0
    assert retry.respect_retry_after_header is True
    assert retry.raise_on_status is False


def test_create_http_session_normalizes_invalid_settings(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "MAX_RETRIES", -5)
    monkeypatch.setattr(module, "RETRY_DELAY_SECONDS", -1)
    monkeypatch.setattr(module, "MAX_RETRY_DELAY_SECONDS", -2)
    monkeypatch.setattr(module, "HTTP_POOL_CONNECTIONS", 0)
    monkeypatch.setattr(module, "HTTP_POOL_MAXSIZE", -4)

    session = create_http_session()
    adapter = session.adapters["https://"]
    retry = adapter.max_retries

    assert retry.total == 0
    assert retry.connect == 0
    assert retry.read == 0
    assert retry.status == 0
    assert retry.backoff_factor == 0.0
    assert retry.backoff_max == 0.0
    assert adapter._pool_connections == 1
    assert adapter._pool_maxsize == 1


def test_get_http_session_creates_and_reuses_session(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session = requests.Session()
    creation_count = 0

    if hasattr(module._HTTP_LOCAL, "session"):
        delattr(module._HTTP_LOCAL, "session")

    def fake_create_session() -> requests.Session:
        nonlocal creation_count
        creation_count += 1
        return fake_session

    monkeypatch.setattr(
        module,
        "create_http_session",
        fake_create_session
    )

    first_session = get_http_session()
    second_session = get_http_session()

    assert first_session is fake_session
    assert second_session is fake_session
    assert creation_count == 1


def test_get_http_session_returns_existing_session(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_session = requests.Session()
    module._HTTP_LOCAL.session = existing_session

    monkeypatch.setattr(
        module,
        "create_http_session",
        lambda: pytest.fail(
            "La session ne devait pas être recréée."
        )
    )

    result = get_http_session()

    assert result is existing_session


# Validation des adresses IP

@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.0.0.2",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::"
    ]
)
def test_is_forbidden_ip_address_blocks_internal_addresses(
    address: str
) -> None:
    assert is_forbidden_ip_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
        "2606:4700:4700::1111"
    ]
)
def test_is_forbidden_ip_address_allows_public_addresses(
    address: str
) -> None:
    assert is_forbidden_ip_address(address) is False


@pytest.mark.parametrize(
    "address",
    [
        "",
        "invalid",
        "999.999.999.999",
        "example.com",
        None
    ]
)
def test_is_forbidden_ip_address_blocks_invalid_values(
    address: Any
) -> None:
    assert is_forbidden_ip_address(address) is True


# Résolution DNS

def test_resolve_hostname_addresses(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    address_information = [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            ("93.184.216.34", 0)
        ),
        (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            6,
            "",
            ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0)
        ),
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            ("93.184.216.34", 0)
        )
    ]

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *args, **kwargs: address_information
    )

    result = resolve_hostname_addresses("example.com")

    assert result == {
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946"
    }


def test_resolve_hostname_addresses_uses_expected_options(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_getaddrinfo(
        hostname: str,
        port: Any,
        *,
        family: int,
        type: int
    ) -> list[Any]:
        calls.append({
            "hostname": hostname,
            "port": port,
            "family": family,
            "type": type
        })
        return []

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        fake_getaddrinfo
    )

    result = resolve_hostname_addresses("example.com")

    assert result == set()
    assert calls == [
        {
            "hostname": "example.com",
            "port": None,
            "family": socket.AF_UNSPEC,
            "type": socket.SOCK_STREAM
        }
    ]


@pytest.mark.parametrize(
    "exception",
    [
        socket.gaierror("DNS impossible"),
        OSError("résolution impossible")
    ]
)
def test_resolve_hostname_addresses_handles_resolution_error(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception
) -> None:
    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        raise exception

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        fake_getaddrinfo
    )

    assert resolve_hostname_addresses("example.com") == set()


def test_resolve_hostname_addresses_rejects_empty_hostname(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail(
            "La résolution DNS ne devait pas être appelée."
        )
    )

    assert resolve_hostname_addresses("") == set()


# Validation des URL distantes

@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "example.com/image.jpg",
        "ftp://example.com/image.jpg",
        "file:///tmp/image.jpg",
        "javascript:alert(1)"
    ]
)
def test_validate_remote_image_url_rejects_invalid_protocol(
    monkeypatch: pytest.MonkeyPatch,
    url: Any
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: False
    )

    is_valid, reason = validate_remote_image_url(url)

    assert is_valid is False
    assert reason == "URL HTTP ou HTTPS invalide."


def test_validate_remote_image_url_accepts_public_hostname(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "resolve_hostname_addresses",
        lambda hostname: {
            "93.184.216.34",
            "8.8.8.8"
        }
    )

    is_valid, reason = validate_remote_image_url(
        "https://example.com/image.jpg"
    )

    assert is_valid is True
    assert reason == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/image.jpg",
        "https://localhost.localdomain/image.jpg",
        "https://metadata.google.internal/image.jpg",
        "https://test.localhost/image.jpg"
    ]
)
def test_validate_remote_image_url_blocks_local_hostnames(
    monkeypatch: pytest.MonkeyPatch,
    url: str
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(url)

    assert is_valid is False
    assert "Hôte local interdit" in reason


@pytest.mark.parametrize(
    "url",
    [
        "https://user@example.com/image.jpg",
        "https://user:password@example.com/image.jpg",
        "https://:password@example.com/image.jpg"
    ]
)
def test_validate_remote_image_url_blocks_embedded_credentials(
    monkeypatch: pytest.MonkeyPatch,
    url: str
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(url)

    assert is_valid is False
    assert reason == "Identifiants intégrés à l'URL interdits."


@pytest.mark.parametrize(
    "port",
    [
        21,
        22,
        25,
        3000,
        8000,
        8080,
        8443
    ]
)
def test_validate_remote_image_url_blocks_forbidden_ports(
    monkeypatch: pytest.MonkeyPatch,
    port: int
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(
        f"https://example.com:{port}/image.jpg"
    )

    assert is_valid is False
    assert reason == f"Port distant interdit : {port}"


@pytest.mark.parametrize(
    "port",
    sorted(ALLOWED_REMOTE_PORTS)
)
def test_validate_remote_image_url_allows_remote_ports(
    monkeypatch: pytest.MonkeyPatch,
    port: int
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "resolve_hostname_addresses",
        lambda hostname: {"93.184.216.34"}
    )

    is_valid, reason = validate_remote_image_url(
        f"https://example.com:{port}/image.jpg"
    )

    assert is_valid is True
    assert reason == ""


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/image.jpg",
        "http://10.0.0.1/image.jpg",
        "http://192.168.1.10/image.jpg",
        "http://[::1]/image.jpg",
        "http://[fc00::1]/image.jpg"
    ]
)
def test_validate_remote_image_url_blocks_direct_private_ip(
    monkeypatch: pytest.MonkeyPatch,
    url: str
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(url)

    assert is_valid is False
    assert "Adresse IP interdite" in reason


@pytest.mark.parametrize(
    "url",
    [
        "https://8.8.8.8/image.jpg",
        "https://1.1.1.1/image.jpg",
        "https://[2606:4700:4700::1111]/image.jpg"
    ]
)
def test_validate_remote_image_url_accepts_direct_public_ip(
    monkeypatch: pytest.MonkeyPatch,
    url: str
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(url)

    assert is_valid is True
    assert reason == ""


def test_validate_remote_image_url_blocks_unresolved_hostname(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "resolve_hostname_addresses",
        lambda hostname: set()
    )

    is_valid, reason = validate_remote_image_url(
        "https://unknown.example/image.jpg"
    )

    assert is_valid is False
    assert reason == (
        "Hôte impossible à résoudre : unknown.example"
    )


def test_validate_remote_image_url_blocks_hostname_with_private_ip(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )
    monkeypatch.setattr(
        module,
        "resolve_hostname_addresses",
        lambda hostname: {
            "93.184.216.34",
            "192.168.1.5",
            "127.0.0.1"
        }
    )

    is_valid, reason = validate_remote_image_url(
        "https://example.com/image.jpg"
    )

    assert is_valid is False
    assert reason == (
        "Destination interne interdite : "
        "127.0.0.1, 192.168.1.5"
    )


def test_validate_remote_image_url_rejects_missing_hostname(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(
        "https:///image.jpg"
    )

    assert is_valid is False
    assert reason == "Nom d'hôte absent."


def test_validate_remote_image_url_handles_invalid_port(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: True
    )

    is_valid, reason = validate_remote_image_url(
        "https://example.com:invalid/image.jpg"
    )

    assert is_valid is False
    assert reason == "URL impossible à analyser."


def test_blocked_hostnames_configuration() -> None:
    assert "localhost" in BLOCKED_HOSTNAMES
    assert "localhost.localdomain" in BLOCKED_HOSTNAMES
    assert "metadata.google.internal" in BLOCKED_HOSTNAMES


# Construction des URL de redirection

def test_get_redirect_url_with_absolute_location() -> None:
    response = FakeResponse(
        status_code=302,
        headers={
            "Location": "https://cdn.example.com/image.jpg"
        }
    )

    result = get_redirect_url(
        response,  # type: ignore[arg-type]
        "https://example.com/article"
    )

    assert result == "https://cdn.example.com/image.jpg"


def test_get_redirect_url_with_relative_location() -> None:
    response = FakeResponse(
        status_code=302,
        headers={
            "Location": "/media/image.jpg"
        }
    )

    result = get_redirect_url(
        response,  # type: ignore[arg-type]
        "https://example.com/articles/news"
    )

    assert result == "https://example.com/media/image.jpg"


@pytest.mark.parametrize(
    "location",
    [
        "",
        " ",
        None
    ]
)
def test_get_redirect_url_without_location(
    location: Any
) -> None:
    response = FakeResponse(
        status_code=302,
        headers={
            "Location": location
        }
    )

    result = get_redirect_url(
        response,  # type: ignore[arg-type]
        "https://example.com/image.jpg"
    )

    assert result == ""


# Requête HTTP et redirections

def test_request_image_response_returns_direct_response(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = FakeResponse(status_code=200)
    session = FakeSession([response])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )
    monkeypatch.setattr(
        module,
        "IMAGE_DOWNLOAD_CONNECT_TIMEOUT",
        3
    )
    monkeypatch.setattr(
        module,
        "IMAGE_DOWNLOAD_READ_TIMEOUT",
        8
    )

    result = request_image_response(
        session,  # type: ignore[arg-type]
        "https://example.com/image.jpg"
    )

    assert result is response
    assert session.calls == [
        {
            "url": "https://example.com/image.jpg",
            "headers": None,
            "timeout": (3, 8),
            "stream": True,
            "allow_redirects": False
        }
    ]


def test_request_image_response_adds_valid_referer(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = FakeResponse(status_code=200)
    session = FakeSession([response])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda url: url.startswith("https://")
    )

    result = request_image_response(
        session,  # type: ignore[arg-type]
        "https://cdn.example.com/image.jpg",
        referer="https://example.com/article"
    )

    assert result is response
    assert session.calls[0]["headers"] == {
        "Referer": "https://example.com/article"
    }


def test_request_image_response_ignores_invalid_referer(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = FakeResponse(status_code=200)
    session = FakeSession([response])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda url: False
    )

    request_image_response(
        session,  # type: ignore[arg-type]
        "https://example.com/image.jpg",
        referer="invalid"
    )

    assert session.calls[0]["headers"] is None


def test_request_image_response_normalizes_timeouts(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    response = FakeResponse(status_code=200)
    session = FakeSession([response])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )
    monkeypatch.setattr(
        module,
        "IMAGE_DOWNLOAD_CONNECT_TIMEOUT",
        0
    )
    monkeypatch.setattr(
        module,
        "IMAGE_DOWNLOAD_READ_TIMEOUT",
        -10
    )

    request_image_response(
        session,  # type: ignore[arg-type]
        "https://example.com/image.jpg"
    )

    assert session.calls[0]["timeout"] == (1, 1)


def test_request_image_response_follows_safe_redirect(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_response = FakeResponse(
        status_code=302,
        headers={
            "Location": "https://cdn.example.com/image.jpg"
        }
    )
    final_response = FakeResponse(status_code=200)
    session = FakeSession([
        redirect_response,
        final_response
    ])

    checked_urls: list[str] = []

    def fake_validate(url: str) -> tuple[bool, str]:
        checked_urls.append(url)
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        fake_validate
    )

    result = request_image_response(
        session,  # type: ignore[arg-type]
        "https://example.com/image.jpg"
    )

    assert result is final_response
    assert redirect_response.closed is True
    assert checked_urls == [
        "https://example.com/image.jpg",
        "https://cdn.example.com/image.jpg",
        "https://cdn.example.com/image.jpg"
    ]
    assert [
        call["url"]
        for call in session.calls
    ] == [
        "https://example.com/image.jpg",
        "https://cdn.example.com/image.jpg"
    ]


def test_request_image_response_follows_relative_redirect(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_response = FakeResponse(
        status_code=301,
        headers={
            "Location": "/images/final.jpg"
        }
    )
    final_response = FakeResponse(status_code=200)
    session = FakeSession([
        redirect_response,
        final_response
    ])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )

    result = request_image_response(
        session,  # type: ignore[arg-type]
        "https://example.com/old/image.jpg"
    )

    assert result is final_response
    assert session.calls[1]["url"] == (
        "https://example.com/images/final.jpg"
    )


@pytest.mark.parametrize(
    "redirect_status",
    [
        301,
        302,
        303,
        307,
        308
    ]
)
def test_request_image_response_handles_redirect_statuses(
    monkeypatch: pytest.MonkeyPatch,
    redirect_status: int
) -> None:
    redirect_response = FakeResponse(
        status_code=redirect_status,
        headers={
            "Location": "https://cdn.example.com/image.jpg"
        }
    )
    final_response = FakeResponse(status_code=200)
    session = FakeSession([
        redirect_response,
        final_response
    ])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )

    result = request_image_response(
        session,  # type: ignore[arg-type]
        "https://example.com/image.jpg"
    )

    assert result is final_response
    assert redirect_response.closed is True


def test_request_image_response_blocks_initial_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    session = FakeSession([])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (
            False,
            "Destination interne interdite"
        )
    )

    with pytest.raises(
        UnsafeImageUrlError,
        match="URL distante bloquée"
    ) as error_info:
        request_image_response(
            session,  # type: ignore[arg-type]
            "http://127.0.0.1/image.jpg"
        )

    assert error_info.value.status == module.IMAGE_STATUS_BLOCKED_URL
    assert session.calls == []


def test_request_image_response_blocks_redirect(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_response = FakeResponse(
        status_code=302,
        headers={
            "Location": "http://127.0.0.1/image.jpg"
        }
    )
    session = FakeSession([redirect_response])

    def fake_validate(url: str) -> tuple[bool, str]:
        if url.startswith("http://127."):
            return False, "Adresse IP interdite"

        return True, ""

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        fake_validate
    )

    with pytest.raises(
        UnsafeImageUrlError,
        match="Redirection bloquée"
    ) as error_info:
        request_image_response(
            session,  # type: ignore[arg-type]
            "https://example.com/image.jpg"
        )

    assert error_info.value.status == IMAGE_STATUS_BLOCKED_REDIRECT
    assert redirect_response.closed is True


def test_request_image_response_rejects_redirect_without_location(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_response = FakeResponse(
        status_code=302,
        headers={}
    )
    session = FakeSession([redirect_response])

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )

    with pytest.raises(
        requests.TooManyRedirects,
        match="Redirection sans en-tête Location"
    ):
        request_image_response(
            session,  # type: ignore[arg-type]
            "https://example.com/image.jpg"
        )

    assert redirect_response.closed is True


def test_request_image_response_rejects_too_many_redirects(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [
        FakeResponse(
            status_code=302,
            headers={
                "Location": f"https://example.com/image-{index}.jpg"
            }
        )
        for index in range(MAX_IMAGE_REDIRECTS + 1)
    ]
    session = FakeSession(responses)

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )

    with pytest.raises(
        requests.TooManyRedirects,
        match=f"Plus de {MAX_IMAGE_REDIRECTS} redirections"
    ):
        request_image_response(
            session,  # type: ignore[arg-type]
            "https://example.com/image.jpg"
        )

    assert len(session.calls) == MAX_IMAGE_REDIRECTS + 1
    assert all(response.closed for response in responses)


def test_request_image_response_preserves_http_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingSession:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise requests.Timeout("délai dépassé")

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )

    with pytest.raises(
        requests.Timeout,
        match="délai dépassé"
    ):
        request_image_response(
            FailingSession(),  # type: ignore[arg-type]
            "https://example.com/image.jpg"
        )


# Exceptions personnalisées

def test_unsafe_image_url_error_default_status() -> None:
    error = UnsafeImageUrlError("URL bloquée")

    assert str(error) == "URL bloquée"
    assert error.status == module.IMAGE_STATUS_BLOCKED_URL


def test_unsafe_image_url_error_custom_status() -> None:
    error = UnsafeImageUrlError(
        "Redirection bloquée",
        status=IMAGE_STATUS_BLOCKED_REDIRECT
    )

    assert str(error) == "Redirection bloquée"
    assert error.status == IMAGE_STATUS_BLOCKED_REDIRECT