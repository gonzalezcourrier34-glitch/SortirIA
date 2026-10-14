"""
Fonctions utilitaires liées aux URL.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/url_utils.py` (commit 93632c5).

Ce module centralise la validation, la normalisation, la canonicalisation,
l'extraction des domaines et le masquage des informations sensibles.

Aucune requête HTTP n'est effectuée ici.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import (
    ParseResult,
    parse_qsl,
    urlencode,
    urlparse,
    urlunparse
)


# Schémas autorisés

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({
    "http",
    "https"
})


# Paramètres de suivi

TRACKING_QUERY_PARAMETERS: frozenset[str] = frozenset({
    "dclid",
    "fbclid",
    "gclid",
    "gbraid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "si",
    "wbraid"
})

TRACKING_QUERY_PREFIXES: tuple[str, ...] = (
    "utm_",
)


# Paramètres sensibles

SENSITIVE_QUERY_PARAMETERS: frozenset[str] = frozenset({
    "access_key",
    "access_token",
    "api-key",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "client_id",
    "client_secret",
    "id_token",
    "jwt",
    "key",
    "password",
    "refresh_token",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "signature",
    "token"
})

REDACTED_URL_VALUE = "***"


# Expressions régulières

INVALID_URL_CHARACTER_PATTERN = re.compile(r"[\x00-\x20\x7f]")

DOMAIN_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    flags=re.IGNORECASE
)

SENSITIVE_URL_PARAMETER_PATTERN = re.compile(
    r"(?i)([?&](?:"
    + "|".join(
        re.escape(parameter)
        for parameter in sorted(
            SENSITIVE_QUERY_PARAMETERS,
            key=len,
            reverse=True
        )
    )
    + r")=)[^&#\s]*"
)

URL_CREDENTIALS_PATTERN = re.compile(
    r"(?i)(https?://)([^/@\s]+)@"
)


# Validation des domaines

def normalize_hostname(hostname: str) -> str:
    """Normalise et valide un nom d'hôte ou une adresse IP."""

    normalized_hostname = hostname.strip().rstrip(".").casefold()

    if not normalized_hostname:
        return ""

    # Normalise les adresses IPv4 et IPv6.
    try:
        return ipaddress.ip_address(normalized_hostname).compressed.casefold()
    except ValueError:
        pass

    # Convertit les domaines internationaux vers leur représentation IDNA.
    try:
        ascii_hostname = normalized_hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return ""

    if len(ascii_hostname) > 253:
        return ""

    labels = ascii_hostname.split(".")

    if not labels or any(
        not label or not DOMAIN_LABEL_PATTERN.fullmatch(label)
        for label in labels
    ):
        return ""

    return ascii_hostname.casefold()


def parse_http_url(url: Any) -> ParseResult | None:
    """Analyse une URL HTTP ou HTTPS absolue valide."""

    if not isinstance(url, str):
        return None

    normalized_url = url.strip()

    if not normalized_url:
        return None

    if (
        INVALID_URL_CHARACTER_PATTERN.search(normalized_url)
        or "\\" in normalized_url
    ):
        return None

    try:
        parsed_url = urlparse(normalized_url)
    except (TypeError, ValueError):
        return None

    scheme = parsed_url.scheme.casefold()

    if scheme not in ALLOWED_URL_SCHEMES:
        return None

    if not parsed_url.netloc or not parsed_url.hostname:
        return None

    # Les identifiants intégrés dans une URL sont refusés.
    if parsed_url.username is not None or parsed_url.password is not None:
        return None

    if not normalize_hostname(parsed_url.hostname):
        return None

    try:
        port = parsed_url.port
    except ValueError:
        return None

    if port is not None and not 1 <= port <= 65535:
        return None

    return parsed_url


def is_valid_http_url(url: Any) -> bool:
    """Indique si une valeur est une URL HTTP ou HTTPS absolue valide."""

    return parse_http_url(url) is not None


# Construction des URL

def build_normalized_netloc(
    hostname: str,
    scheme: str,
    port: int | None
) -> str:
    """Construit une autorité réseau normalisée."""

    normalized_hostname = normalize_hostname(hostname)

    if not normalized_hostname:
        return ""

    # Les adresses IPv6 doivent être placées entre crochets dans une URL.
    try:
        parsed_ip = ipaddress.ip_address(normalized_hostname)

        if isinstance(parsed_ip, ipaddress.IPv6Address):
            normalized_hostname = f"[{normalized_hostname}]"
    except ValueError:
        pass

    # Supprime les ports standards qui n'apportent aucune information.
    if (
        port is None
        or scheme == "http" and port == 80
        or scheme == "https" and port == 443
    ):
        return normalized_hostname

    return f"{normalized_hostname}:{port}"


def normalize_url(url: Any) -> str:
    """Retourne une URL valide dont le schéma et le domaine sont normalisés."""

    parsed_url = parse_http_url(url)

    if parsed_url is None or parsed_url.hostname is None:
        return ""

    scheme = parsed_url.scheme.casefold()

    try:
        port = parsed_url.port
    except ValueError:
        return ""

    netloc = build_normalized_netloc(
        hostname=parsed_url.hostname,
        scheme=scheme,
        port=port
    )

    if not netloc:
        return ""

    return urlunparse((
        scheme,
        netloc,
        parsed_url.path,
        parsed_url.params,
        parsed_url.query,
        parsed_url.fragment
    ))


# Lecture des URL

def get_url_domain(url: Any) -> str:
    """Retourne le domaine normalisé d'une URL valide."""

    parsed_url = parse_http_url(url)

    if parsed_url is None or parsed_url.hostname is None:
        return ""

    return normalize_hostname(parsed_url.hostname)


# Détection des paramètres

def is_tracking_query_parameter(parameter: str) -> bool:
    """Indique si un paramètre appartient au suivi publicitaire."""

    normalized_parameter = parameter.strip().casefold()

    return (
        normalized_parameter in TRACKING_QUERY_PARAMETERS
        or normalized_parameter.startswith(TRACKING_QUERY_PREFIXES)
    )


def is_sensitive_query_parameter(parameter: str) -> bool:
    """Indique si un paramètre peut contenir une information sensible."""

    return parameter.strip().casefold() in SENSITIVE_QUERY_PARAMETERS


# Sécurisation des journaux

def _sanitize_url_with_patterns(value: str) -> str:
    """Masque les secrets dans une URL difficile à analyser."""

    sanitized_value = URL_CREDENTIALS_PATTERN.sub(
        rf"\1{REDACTED_URL_VALUE}@",
        value
    )

    return SENSITIVE_URL_PARAMETER_PATTERN.sub(
        rf"\1{REDACTED_URL_VALUE}",
        sanitized_value
    )


def sanitize_url_for_logging(url: Any) -> str:
    """Masque les informations sensibles avant journalisation."""

    if url is None:
        return ""

    raw_url = str(url).strip()

    if not raw_url:
        return ""

    try:
        parsed_url = urlparse(raw_url)
    except (TypeError, ValueError):
        return _sanitize_url_with_patterns(raw_url)

    if not parsed_url.scheme or not parsed_url.netloc:
        return _sanitize_url_with_patterns(raw_url)

    hostname = parsed_url.hostname or ""

    if not hostname:
        return _sanitize_url_with_patterns(raw_url)

    netloc = hostname

    # Encadre les adresses IPv6.
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"

    try:
        port = parsed_url.port
    except ValueError:
        return _sanitize_url_with_patterns(raw_url)

    if port is not None:
        netloc = f"{netloc}:{port}"

    # Les identifiants intégrés sont remplacés par une valeur neutre.
    if parsed_url.username is not None or parsed_url.password is not None:
        netloc = f"{REDACTED_URL_VALUE}@{netloc}"

    query_parameters = [
        (
            key,
            REDACTED_URL_VALUE
            if is_sensitive_query_parameter(key)
            else value
        )
        for key, value in parse_qsl(
            parsed_url.query,
            keep_blank_values=True
        )
    ]

    sanitized_url = urlunparse((
        parsed_url.scheme,
        netloc,
        parsed_url.path,
        parsed_url.params,
        urlencode(query_parameters, doseq=True),
        parsed_url.fragment
    ))

    return _sanitize_url_with_patterns(sanitized_url)


# Canonicalisation

def canonicalize_url(url: Any) -> str:
    """Retourne une représentation stable pour la déduplication."""

    parsed_url = parse_http_url(url)

    if parsed_url is None or parsed_url.hostname is None:
        return ""

    scheme = parsed_url.scheme.casefold()

    try:
        port = parsed_url.port
    except ValueError:
        return ""

    netloc = build_normalized_netloc(
        hostname=parsed_url.hostname,
        scheme=scheme,
        port=port
    )

    if not netloc:
        return ""

    # Supprime uniquement les paramètres identifiés comme traceurs.
    query_parameters = [
        (key, value)
        for key, value in parse_qsl(
            parsed_url.query,
            keep_blank_values=True
        )
        if not is_tracking_query_parameter(key)
    ]

    # Stabilise l'ordre des paramètres sans supprimer les doublons.
    query_parameters.sort(
        key=lambda item: (
            item[0].casefold(),
            item[0],
            item[1]
        )
    )

    canonical_query = urlencode(
        query_parameters,
        doseq=True
    )

    path = parsed_url.path or "/"

    if path != "/":
        path = path.rstrip("/") or "/"

    return urlunparse((
        scheme,
        netloc,
        path,
        "",
        canonical_query,
        ""
    ))