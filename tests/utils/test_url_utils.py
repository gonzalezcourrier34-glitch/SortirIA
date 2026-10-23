"""Tests des fonctions utilitaires liées aux URL.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_url_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.utils.url_utils import (
    REDACTED_URL_VALUE,
    build_normalized_netloc,
    canonicalize_url,
    get_url_domain,
    is_sensitive_query_parameter,
    is_tracking_query_parameter,
    is_valid_http_url,
    normalize_hostname,
    normalize_url,
    parse_http_url,
    sanitize_url_for_logging,
)

# Normalisation des noms d'hôte

@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("example.com", "example.com"),
        (" EXAMPLE.COM ", "example.com"),
        ("example.com.", "example.com"),
        ("sub.domain.example.com", "sub.domain.example.com"),
        ("127.0.0.1", "127.0.0.1"),
        ("2001:0db8::1", "2001:db8::1"),
        ("école.fr", "xn--cole-9oa.fr"),
        ("", ""),
        ("   ", ""),
        (".", ""),
        ("example..com", ""),
        ("-example.com", ""),
        ("example-.com", ""),
        ("exa_mple.com", "")
    ]
)
def test_normalize_hostname(
    hostname: str,
    expected: str
) -> None:
    """Le nom d'hôte doit être normalisé ou rejeté."""

    assert normalize_hostname(hostname) == expected


def test_normalize_hostname_rejects_too_long_hostname() -> None:
    """Un nom d'hôte dépassant la limite autorisée doit être rejeté."""

    hostname = ".".join(["a" * 63] * 5)

    assert normalize_hostname(hostname) == ""


def test_normalize_hostname_rejects_too_long_label() -> None:
    """Une étiquette de domaine trop longue doit être rejetée."""

    hostname = f"{'a' * 64}.example.com"

    assert normalize_hostname(hostname) == ""


# Analyse et validation des URL

@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://example.com",
        "https://example.com/article",
        "https://example.com:8443/article",
        "https://127.0.0.1/test",
        "https://[2001:db8::1]/test",
        "HTTPS://EXAMPLE.COM/article"
    ]
)
def test_parse_http_url_accepts_valid_urls(url: str) -> None:
    """Une URL HTTP ou HTTPS absolue valide doit être analysée."""

    parsed_url = parse_http_url(url)

    assert parsed_url is not None
    assert parsed_url.hostname is not None


@pytest.mark.parametrize(
    "url",
    [
        None,
        42,
        "",
        "   ",
        "example.com",
        "/article",
        "ftp://example.com",
        "file:///tmp/file.txt",
        "mailto:test@example.com",
        "http://",
        "https:///article",
        "https://user@example.com",
        "https://user:password@example.com",
        "https://example.com\\article",
        "https://example.com/article test",
        "https://example.com/\narticle",
        "https://exa_mple.com",
        "https://example.com:0",
        "https://example.com:65536",
        "https://example.com:invalid"
    ]
)
def test_parse_http_url_rejects_invalid_urls(url: Any) -> None:
    """Une URL invalide doit être rejetée."""

    assert parse_http_url(url) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com", True),
        ("http://example.com/article", True),
        ("ftp://example.com", False),
        ("example.com", False),
        ("", False),
        (None, False),
        (42, False)
    ]
)
def test_is_valid_http_url(
    url: Any,
    expected: bool
) -> None:
    """La validité d'une URL HTTP doit être correctement indiquée."""

    assert is_valid_http_url(url) is expected


# Construction de l'autorité réseau

@pytest.mark.parametrize(
    ("hostname", "scheme", "port", "expected"),
    [
        ("example.com", "http", None, "example.com"),
        ("example.com", "https", None, "example.com"),
        ("example.com", "http", 80, "example.com"),
        ("example.com", "https", 443, "example.com"),
        ("example.com", "http", 8080, "example.com:8080"),
        ("example.com", "https", 8443, "example.com:8443"),
        ("EXAMPLE.COM", "https", None, "example.com"),
        ("2001:db8::1", "https", None, "[2001:db8::1]"),
        ("2001:db8::1", "https", 8443, "[2001:db8::1]:8443"),
        ("", "https", None, ""),
        ("exa_mple.com", "https", None, "")
    ]
)
def test_build_normalized_netloc(
    hostname: str,
    scheme: str,
    port: int | None,
    expected: str
) -> None:
    """L'autorité réseau doit être construite et normalisée."""

    assert build_normalized_netloc(
        hostname=hostname,
        scheme=scheme,
        port=port
    ) == expected


# Normalisation des URL

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            " HTTPS://EXAMPLE.COM/article ",
            "https://example.com/article"
        ),
        (
            "http://example.com:80/article",
            "http://example.com/article"
        ),
        (
            "https://example.com:443/article",
            "https://example.com/article"
        ),
        (
            "https://example.com:8443/article",
            "https://example.com:8443/article"
        ),
        (
            "https://école.fr/article",
            "https://xn--cole-9oa.fr/article"
        ),
        (
            "https://example.com/article?a=1#section",
            "https://example.com/article?a=1#section"
        ),
        (
            "https://[2001:db8::1]:8443/article",
            "https://[2001:db8::1]:8443/article"
        ),
        ("ftp://example.com", ""),
        ("example.com", ""),
        ("", ""),
        (None, "")
    ]
)
def test_normalize_url(
    url: Any,
    expected: str
) -> None:
    """Une URL valide doit être retournée sous une forme normalisée."""

    assert normalize_url(url) == expected


# Extraction du domaine

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/article", "example.com"),
        ("HTTPS://EXAMPLE.COM/article", "example.com"),
        ("https://sub.example.com/article", "sub.example.com"),
        ("https://école.fr/article", "xn--cole-9oa.fr"),
        ("https://127.0.0.1/article", "127.0.0.1"),
        ("https://[2001:db8::1]/article", "2001:db8::1"),
        ("ftp://example.com", ""),
        ("example.com", ""),
        ("", ""),
        (None, "")
    ]
)
def test_get_url_domain(
    url: Any,
    expected: str
) -> None:
    """Le domaine normalisé doit être extrait d'une URL valide."""

    assert get_url_domain(url) == expected


# Paramètres de suivi

@pytest.mark.parametrize(
    "parameter",
    [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "UTM_CONTENT",
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "si",
        "wbraid",
        "gbraid",
        " FBCLID "
    ]
)
def test_is_tracking_query_parameter_returns_true(
    parameter: str
) -> None:
    """Les paramètres de suivi doivent être détectés."""

    assert is_tracking_query_parameter(parameter) is True


@pytest.mark.parametrize(
    "parameter",
    [
        "page",
        "query",
        "category",
        "source",
        "user_utm_source",
        "utm",
        ""
    ]
)
def test_is_tracking_query_parameter_returns_false(
    parameter: str
) -> None:
    """Les paramètres métier ne doivent pas être considérés comme traceurs."""

    assert is_tracking_query_parameter(parameter) is False


# Paramètres sensibles

@pytest.mark.parametrize(
    "parameter",
    [
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "api-key",
        "password",
        "secret",
        "client_secret",
        "authorization",
        "bearer",
        "jwt",
        "session",
        "session_id",
        "signature",
        " TOKEN "
    ]
)
def test_is_sensitive_query_parameter_returns_true(
    parameter: str
) -> None:
    """Les paramètres contenant des secrets doivent être détectés."""

    assert is_sensitive_query_parameter(parameter) is True


@pytest.mark.parametrize(
    "parameter",
    [
        "page",
        "query",
        "category",
        "utm_source",
        "token_type",
        "username",
        ""
    ]
)
def test_is_sensitive_query_parameter_returns_false(
    parameter: str
) -> None:
    """Les paramètres non sensibles ne doivent pas être masqués."""

    assert is_sensitive_query_parameter(parameter) is False


# Sécurisation des URL pour les journaux

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        (
            "https://example.com/article",
            "https://example.com/article"
        ),
        (
            "https://example.com/article?token=secret",
            f"https://example.com/article?token={REDACTED_URL_VALUE}"
        ),
        (
            "https://example.com/article?api_key=abc&page=2",
            (
                "https://example.com/article"
                f"?api_key={REDACTED_URL_VALUE}&page=2"
            )
        ),
        (
            "https://example.com/article?page=2&password=abc",
            (
                "https://example.com/article"
                f"?page=2&password={REDACTED_URL_VALUE}"
            )
        ),
        (
            "https://user:password@example.com/article",
            f"https://{REDACTED_URL_VALUE}@example.com/article"
        ),
        (
            "https://user@example.com/article?token=abc",
            (
                f"https://{REDACTED_URL_VALUE}@example.com"
                f"/article?token={REDACTED_URL_VALUE}"
            )
        ),
        (
            "https://[2001:db8::1]:8443/article?secret=abc",
            (
                "https://[2001:db8::1]:8443/article"
                f"?secret={REDACTED_URL_VALUE}"
            )
        ),
        (
            "https://example.com/article?query=test#section",
            "https://example.com/article?query=test#section"
        )
    ]
)
def test_sanitize_url_for_logging(
    url: Any,
    expected: str
) -> None:
    """Les informations sensibles doivent être masquées."""

    assert sanitize_url_for_logging(url) == expected


def test_sanitize_url_for_logging_preserves_blank_values() -> None:
    """Les paramètres vides doivent être conservés."""

    url = "https://example.com/article?query=&token="

    result = sanitize_url_for_logging(url)

    assert result == (
        "https://example.com/article"
        f"?query=&token={REDACTED_URL_VALUE}"
    )


def test_sanitize_url_for_logging_preserves_duplicate_parameters() -> None:
    """Les paramètres répétés doivent être conservés."""

    url = "https://example.com/article?tag=a&tag=b&token=secret"

    result = sanitize_url_for_logging(url)

    assert result == (
        "https://example.com/article"
        f"?tag=a&tag=b&token={REDACTED_URL_VALUE}"
    )


def test_sanitize_url_for_logging_handles_relative_value() -> None:
    """Une valeur non absolue doit être sécurisée par expressions régulières."""

    url = "/article?token=secret&page=2"

    result = sanitize_url_for_logging(url)

    assert result == (
        f"/article?token={REDACTED_URL_VALUE}&page=2"
    )


def test_sanitize_url_for_logging_handles_malformed_port() -> None:
    """Une URL avec un port invalide doit être sécurisée sans exception."""

    url = "https://example.com:invalid/article?token=secret"

    result = sanitize_url_for_logging(url)

    assert f"token={REDACTED_URL_VALUE}" in result
    assert "secret" not in result


def test_sanitize_url_for_logging_handles_non_string_value() -> None:
    """Une valeur simple doit être convertie sans erreur."""

    assert sanitize_url_for_logging(42) == "42"


# Canonicalisation

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://example.com",
            "https://example.com/"
        ),
        (
            "https://example.com/",
            "https://example.com/"
        ),
        (
            "HTTPS://EXAMPLE.COM/article/",
            "https://example.com/article"
        ),
        (
            "http://example.com:80/article",
            "http://example.com/article"
        ),
        (
            "https://example.com:443/article",
            "https://example.com/article"
        ),
        (
            "https://example.com:8443/article",
            "https://example.com:8443/article"
        ),
        (
            "https://example.com/article#section",
            "https://example.com/article"
        ),
        (
            "https://example.com/article;param?b=2",
            "https://example.com/article?b=2"
        ),
        (
            "https://école.fr/article/",
            "https://xn--cole-9oa.fr/article"
        ),
        (
            "https://[2001:db8::1]/article/",
            "https://[2001:db8::1]/article"
        ),
        ("ftp://example.com", ""),
        ("example.com", ""),
        ("", ""),
        (None, "")
    ]
)
def test_canonicalize_url(
    url: Any,
    expected: str
) -> None:
    """Une URL doit être canonicalisée pour la déduplication."""

    assert canonicalize_url(url) == expected


def test_canonicalize_url_removes_tracking_parameters() -> None:
    """Les paramètres de suivi doivent être supprimés."""

    url = (
        "https://example.com/article"
        "?utm_source=newsletter"
        "&fbclid=abc"
        "&page=2"
    )

    result = canonicalize_url(url)

    assert result == "https://example.com/article?page=2"


def test_canonicalize_url_removes_tracking_parameters_case_insensitively() -> None:
    """La détection des paramètres de suivi doit ignorer la casse."""

    url = (
        "https://example.com/article"
        "?UTM_SOURCE=newsletter"
        "&GCLID=abc"
        "&page=2"
    )

    result = canonicalize_url(url)

    assert result == "https://example.com/article?page=2"


def test_canonicalize_url_sorts_query_parameters() -> None:
    """Les paramètres doivent être triés pour stabiliser l'URL."""

    url = "https://example.com/article?z=3&a=2&a=1&b=4"

    result = canonicalize_url(url)

    assert result == (
        "https://example.com/article"
        "?a=1&a=2&b=4&z=3"
    )


def test_canonicalize_url_preserves_duplicate_parameters() -> None:
    """Les paramètres identiques ne doivent pas être supprimés."""

    url = "https://example.com/article?tag=b&tag=a&tag=a"

    result = canonicalize_url(url)

    assert result == (
        "https://example.com/article"
        "?tag=a&tag=a&tag=b"
    )


def test_canonicalize_url_preserves_blank_parameters() -> None:
    """Les paramètres sans valeur doivent être conservés."""

    url = "https://example.com/article?query=&page=2"

    result = canonicalize_url(url)

    assert result == (
        "https://example.com/article"
        "?page=2&query="
    )


def test_canonicalize_url_preserves_sensitive_parameters() -> None:
    """La canonicalisation ne doit pas supprimer les paramètres sensibles."""

    url = "https://example.com/article?token=abc&page=2"

    result = canonicalize_url(url)

    assert result == (
        "https://example.com/article"
        "?page=2&token=abc"
    )