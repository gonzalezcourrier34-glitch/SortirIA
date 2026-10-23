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
from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse, urlunparse

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
    """Normalise et valide un nom d'hôte ou une adresse IP.

    Parameters
    ----------
    hostname : str
        Le nom d'hôte ou l'adresse IP à contrôler.

    Returns
    -------
    str
        L'adresse IP compressée, ou le domaine en minuscules et en IDNA ;
        vide si le nom est invalide (étiquette mal formée, plus de 253 caractères).
    """

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

    # Vérifie la longueur totale puis chaque étiquette du domaine.
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
    """Analyse une URL HTTP ou HTTPS absolue valide.

    Sont refusés : les caractères de contrôle, espaces et barres obliques
    inverses, les schémas autres que http(s), les identifiants intégrés, les
    noms d'hôte invalides et les ports hors de 1 à 65535.

    Parameters
    ----------
    url : Any
        La valeur à analyser ; tout ce qui n'est pas une chaîne est refusé.

    Returns
    -------
    ParseResult | None
        L'URL découpée par `urlparse`, ou None si elle n'est pas valide.
    """

    # Écarter les valeurs vides et les caractères interdits
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

    # Découper l'URL et exiger un schéma http(s) et un hôte
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

    # Contrôler le nom d'hôte et le port
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
    """Indique si une valeur est une URL HTTP ou HTTPS absolue valide.

    Parameters
    ----------
    url : Any
        La valeur à contrôler.

    Returns
    -------
    bool
        Vrai si `parse_http_url` l'accepte.
    """

    return parse_http_url(url) is not None


# Construction des URL

def build_normalized_netloc(
    hostname: str,
    scheme: str,
    port: int | None
) -> str:
    """Construit une autorité réseau normalisée.

    Parameters
    ----------
    hostname : str
        Le nom d'hôte ou l'adresse IP.
    scheme : str
        Le schéma en minuscules, pour reconnaître le port standard.
    port : int | None
        Le port explicite, ou None.

    Returns
    -------
    str
        « hôte » ou « hôte:port » (IPv6 entre crochets, ports 80 et 443
        standards retirés) ; vide si l'hôte est invalide.
    """

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
    """Retourne une URL valide dont le schéma et le domaine sont normalisés.

    Le chemin, les paramètres et le fragment sont gardés tels quels.

    Parameters
    ----------
    url : Any
        L'URL à normaliser.

    Returns
    -------
    str
        L'URL normalisée, ou une chaîne vide si elle est invalide.
    """

    # Valider l'URL et normaliser schéma, hôte et port
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

    # Réassembler l'URL sans toucher au reste
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
    """Retourne le domaine normalisé d'une URL valide.

    Parameters
    ----------
    url : Any
        L'URL à lire.

    Returns
    -------
    str
        Le domaine (ou l'adresse IP) normalisé, ou une chaîne vide si l'URL
        est invalide.
    """

    parsed_url = parse_http_url(url)

    if parsed_url is None or parsed_url.hostname is None:
        return ""

    return normalize_hostname(parsed_url.hostname)


# Détection des paramètres

def is_tracking_query_parameter(parameter: str) -> bool:
    """Indique si un paramètre appartient au suivi publicitaire.

    Parameters
    ----------
    parameter : str
        Le nom du paramètre de requête.

    Returns
    -------
    bool
        Vrai s'il est dans `TRACKING_QUERY_PARAMETERS` ou commence par « utm_ ».
    """

    normalized_parameter = parameter.strip().casefold()

    return (
        normalized_parameter in TRACKING_QUERY_PARAMETERS
        or normalized_parameter.startswith(TRACKING_QUERY_PREFIXES)
    )


def is_sensitive_query_parameter(parameter: str) -> bool:
    """Indique si un paramètre peut contenir une information sensible.

    Parameters
    ----------
    parameter : str
        Le nom du paramètre de requête.

    Returns
    -------
    bool
        Vrai s'il est dans `SENSITIVE_QUERY_PARAMETERS` (clé, jeton, mot de passe...).
    """

    return parameter.strip().casefold() in SENSITIVE_QUERY_PARAMETERS


# Sécurisation des journaux

def _sanitize_url_with_patterns(value: str) -> str:
    """Masque les secrets dans une URL difficile à analyser.

    Repli par expressions régulières : identifiants avant « @ » et valeurs
    des paramètres sensibles sont remplacés par « *** ».

    Parameters
    ----------
    value : str
        L'URL brute.

    Returns
    -------
    str
        L'URL avec les secrets masqués.
    """

    sanitized_value = URL_CREDENTIALS_PATTERN.sub(
        rf"\1{REDACTED_URL_VALUE}@",
        value
    )

    return SENSITIVE_URL_PARAMETER_PATTERN.sub(
        rf"\1{REDACTED_URL_VALUE}",
        sanitized_value
    )


def sanitize_url_for_logging(url: Any) -> str:
    """Masque les informations sensibles avant journalisation.

    Identifiants intégrés et valeurs des paramètres sensibles sont remplacés
    par « *** ». Une URL qui ne se découpe pas proprement passe par le repli
    à expressions régulières.

    Parameters
    ----------
    url : Any
        L'URL à journaliser.

    Returns
    -------
    str
        L'URL sans secret, ou une chaîne vide si elle est absente.
    """

    # Découper l'URL ; en cas d'échec, masquer par expressions régulières
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

    # Reconstruire l'autorité réseau sans les identifiants
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

    # Masquer les valeurs des paramètres sensibles, puis réassembler
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
    """Retourne une représentation stable pour la déduplication.

    Deux URL qui ne diffèrent que par la casse du domaine, le port standard,
    les traceurs publicitaires, l'ordre des paramètres, la barre oblique
    finale ou le fragment donnent le même résultat.

    Parameters
    ----------
    url : Any
        L'URL à canonicaliser.

    Returns
    -------
    str
        L'URL canonique, ou une chaîne vide si elle est invalide.
    """

    # Valider l'URL et normaliser schéma, hôte et port
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

    # Retirer la barre oblique finale et le fragment, puis réassembler
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