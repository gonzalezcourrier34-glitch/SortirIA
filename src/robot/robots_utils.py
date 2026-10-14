"""Gestion centralisée du protocole robots.txt pour CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/robot/robots_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic, sleep
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from config.constants import (
    ROBOTS_REASON_ALLOWED,
    ROBOTS_REASON_CHECK_ERROR,
    ROBOTS_REASON_DENIED,
    ROBOTS_REASON_INVALID_URL,
    ROBOTS_REASON_UNAVAILABLE
)
from config.settings import HTTP_HEADERS, REQUEST_TIMEOUT, USER_AGENT
from src.logger import get_logger
from src.utils.url_utils import is_valid_http_url

logger = get_logger(__name__)


# Configuration

ROBOTS_CACHE_TTL_SECONDS = 3600
ROBOTS_FAILURE_TTL_SECONDS = 60
ROBOTS_MAX_SIZE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class RobotsPolicy:
    """Politique robots.txt mise en cache pour une origine."""

    parser: RobotFileParser
    robots_url: str
    available: bool
    allow_without_file: bool
    crawl_delay: float
    expires_at: float
    reason: str = ROBOTS_REASON_ALLOWED
    error: str = ""


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """Décision détaillée concernant l'accès à une URL."""

    allowed: bool
    reason: str
    url: str
    origin: str
    robots_url: str
    crawl_delay: float = 0.0
    error: str = ""

    @property
    def denied(self) -> bool:
        """Indique si l'accès est refusé."""

        return not self.allowed


# État partagé

_POLICY_CACHE: dict[str, RobotsPolicy] = {}
_LAST_REQUEST_AT: dict[str, float] = {}
_POLICY_LOCKS: dict[str, Lock] = {}
_REQUEST_LOCKS: dict[str, Lock] = {}
_CACHE_LOCK = Lock()


# URL

def get_origin(url: str) -> str:
    """Extrait l'origine normalisée d'une URL HTTP."""

    if not is_valid_http_url(url):
        return ""

    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return ""

    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        "",
        "",
        ""
    ))


def get_robots_url(origin: str) -> str:
    """Construit l'URL robots.txt d'une origine."""

    if not origin:
        return ""

    return f"{origin.rstrip('/')}/robots.txt"


def get_effective_port(url: str) -> int | None:
    """Retourne le port explicite ou le port par défaut d'une URL."""

    try:
        parsed = urlsplit(url)

        if parsed.port is not None:
            return parsed.port

        if parsed.scheme.lower() == "http":
            return 80

        if parsed.scheme.lower() == "https":
            return 443

    except (TypeError, ValueError):
        return None

    return None


def is_allowed_robots_redirect(
    original_url: str,
    final_url: str
) -> bool:
    """Autorise uniquement une redirection sûre vers le même hôte."""

    try:
        original = urlsplit(original_url)
        final = urlsplit(final_url)
    except (TypeError, ValueError):
        return False

    original_scheme = original.scheme.lower()
    final_scheme = final.scheme.lower()
    original_host = (original.hostname or "").lower()
    final_host = (final.hostname or "").lower()

    if not original_host or original_host != final_host:
        return False

    if original_scheme == final_scheme:
        return get_effective_port(original_url) == get_effective_port(final_url)

    return (
        original_scheme == "http"
        and final_scheme == "https"
        and get_effective_port(original_url) == 80
        and get_effective_port(final_url) == 443
    )


# Politiques

def build_default_parser(
    robots_url: str,
    allow: bool
) -> RobotFileParser:
    """Crée un parseur synthétique autorisant ou refusant tout accès."""

    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse([
        "User-agent: *",
        "Allow: /" if allow else "Disallow: /"
    ])

    return parser


def build_policy(
    robots_url: str,
    allow: bool,
    available: bool,
    allow_without_file: bool = False,
    crawl_delay: float = 0.0,
    ttl_seconds: int = ROBOTS_CACHE_TTL_SECONDS,
    reason: str = ROBOTS_REASON_ALLOWED,
    error: str = ""
) -> RobotsPolicy:
    """Construit une politique synthétique mise en cache."""

    return RobotsPolicy(
        parser=build_default_parser(robots_url, allow),
        robots_url=robots_url,
        available=available,
        allow_without_file=allow_without_file,
        crawl_delay=max(float(crawl_delay), 0.0),
        expires_at=monotonic() + max(int(ttl_seconds), 0),
        reason=reason,
        error=str(error or "").strip()
    )


# Verrous et cache

def get_origin_lock(
    locks: dict[str, Lock],
    origin: str
) -> Lock:
    """Retourne ou crée le verrou associé à une origine."""

    with _CACHE_LOCK:
        return locks.setdefault(origin, Lock())


def get_policy_lock(origin: str) -> Lock:
    """Retourne le verrou de chargement d'une politique."""

    return get_origin_lock(_POLICY_LOCKS, origin)


def get_request_lock(origin: str) -> Lock:
    """Retourne le verrou d'application du crawl-delay."""

    return get_origin_lock(_REQUEST_LOCKS, origin)


def get_cached_policy(origin: str) -> RobotsPolicy | None:
    """Retourne une politique non expirée."""

    with _CACHE_LOCK:
        policy = _POLICY_CACHE.get(origin)

        if policy is None:
            return None

        if policy.expires_at <= monotonic():
            _POLICY_CACHE.pop(origin, None)
            return None

        return policy


def cache_policy(
    origin: str,
    policy: RobotsPolicy
) -> RobotsPolicy:
    """Enregistre une politique dans le cache."""

    with _CACHE_LOCK:
        _POLICY_CACHE[origin] = policy

    return policy


# Téléchargement

def get_robots_headers() -> dict[str, str]:
    """Retourne les en-têtes utilisés pour robots.txt."""

    return {
        **HTTP_HEADERS,
        "User-Agent": USER_AGENT,
        "Accept": "text/plain,*/*;q=0.1"
    }


def parse_content_length(response: httpx.Response) -> int | None:
    """Lit Content-Length lorsqu'il est exploitable."""

    value = response.headers.get("Content-Length")

    if not value:
        return None

    try:
        size = int(value)
    except (TypeError, ValueError, OverflowError):
        return None

    return max(size, 0)


def read_limited_content(response: httpx.Response) -> bytes:
    """Lit une réponse en streaming sans dépasser la taille maximale."""

    announced_size = parse_content_length(response)

    if (
        announced_size is not None
        and announced_size > ROBOTS_MAX_SIZE_BYTES
    ):
        raise ValueError(
            "Fichier robots.txt annoncé comme trop volumineux."
        )

    content = bytearray()

    for chunk in response.iter_bytes():
        if not chunk:
            continue

        remaining_size = ROBOTS_MAX_SIZE_BYTES - len(content)

        if len(chunk) > remaining_size:
            raise ValueError("Fichier robots.txt trop volumineux.")

        content.extend(chunk)

    return bytes(content)


def decode_robots_content(
    response: httpx.Response,
    content: bytes
) -> str:
    """Décode le contenu de robots.txt avec un encodage sûr."""

    encoding = response.encoding or "utf-8"

    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def looks_like_html(content: str) -> bool:
    """Détecte une page HTML renvoyée à la place de robots.txt."""

    normalized = content.lstrip().lower()

    return normalized.startswith((
        "<!doctype html",
        "<html",
        "<head",
        "<body"
    ))


def build_loaded_policy(
    response: httpx.Response,
    robots_url: str,
    content: bytes
) -> RobotsPolicy:
    """Construit une politique depuis une réponse robots.txt valide."""

    decoded_content = decode_robots_content(response, content)

    if looks_like_html(decoded_content):
        raise ValueError(
            "Une page HTML a été reçue à la place de robots.txt."
        )

    parser = RobotFileParser()
    parser.set_url(str(response.url))
    parser.parse(decoded_content.splitlines())

    delay = parser.crawl_delay(USER_AGENT)

    if delay is None:
        delay = parser.crawl_delay("*")

    try:
        crawl_delay = max(float(delay or 0.0), 0.0)
    except (TypeError, ValueError, OverflowError):
        crawl_delay = 0.0

    return RobotsPolicy(
        parser=parser,
        robots_url=str(response.url) or robots_url,
        available=True,
        allow_without_file=False,
        crawl_delay=crawl_delay,
        expires_at=monotonic() + ROBOTS_CACHE_TTL_SECONDS,
        reason=ROBOTS_REASON_ALLOWED
    )


def fetch_robots_policy(url: str) -> RobotsPolicy:
    """Télécharge robots.txt et retourne une politique exploitable."""

    origin = get_origin(url)
    robots_url = get_robots_url(origin)

    if not origin:
        return build_policy(
            robots_url="",
            allow=False,
            available=False,
            ttl_seconds=ROBOTS_FAILURE_TTL_SECONDS,
            reason=ROBOTS_REASON_INVALID_URL,
            error="URL invalide."
        )

    try:
        with httpx.Client(
            headers=get_robots_headers(),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True
        ) as client:
            with client.stream("GET", robots_url) as response:
                final_url = str(response.url)

                if not is_allowed_robots_redirect(
                    robots_url,
                    final_url
                ):
                    return build_policy(
                        robots_url=robots_url,
                        allow=False,
                        available=False,
                        ttl_seconds=ROBOTS_FAILURE_TTL_SECONDS,
                        reason=ROBOTS_REASON_UNAVAILABLE,
                        error=(
                            "Redirection robots.txt vers une autre origine : "
                            f"{final_url}"
                        )
                    )

                if response.status_code in {404, 410}:
                    logger.debug(
                        "Aucun robots.txt publié pour %s.",
                        origin
                    )
                    return build_policy(
                        robots_url=robots_url,
                        allow=True,
                        available=False,
                        allow_without_file=True
                    )

                if response.status_code in {401, 403}:
                    logger.debug(
                        "Accès au robots.txt refusé pour %s (HTTP %s).",
                        origin,
                        response.status_code
                    )
                    return build_policy(
                        robots_url=robots_url,
                        allow=False,
                        available=True,
                        reason=ROBOTS_REASON_DENIED,
                        error=f"HTTP {response.status_code}"
                    )

                if response.status_code >= 400:
                    error_message = (
                        f"Erreur HTTP {response.status_code}."
                    )
                else:
                    try:
                        content = read_limited_content(response)

                        return build_loaded_policy(
                            response,
                            robots_url,
                            content
                        )
                    except (
                        TypeError,
                        ValueError,
                        UnicodeError,
                        OverflowError
                    ) as error:
                        error_message = (
                            f"robots.txt invalide : {error}"
                        )

    except httpx.TimeoutException:
        error_message = "Délai de connexion dépassé."

    except httpx.NetworkError as error:
        error_message = f"Erreur réseau : {error}"

    except httpx.HTTPError as error:
        error_message = f"Erreur HTTP : {error}"

    except Exception as error:
        logger.debug(
            "Erreur inattendue pendant la lecture de %s : %s",
            robots_url,
            error,
            exc_info=True
        )
        error_message = f"{type(error).__name__}: {error}"

    logger.debug(
        "Politique robots.txt indisponible pour %s : %s",
        origin,
        error_message
    )

    return build_policy(
        robots_url=robots_url,
        allow=False,
        available=False,
        ttl_seconds=ROBOTS_FAILURE_TTL_SECONDS,
        reason=ROBOTS_REASON_UNAVAILABLE,
        error=error_message
    )


def get_robots_policy(url: str) -> RobotsPolicy:
    """Retourne la politique en cache ou la charge une seule fois."""

    origin = get_origin(url)

    if not origin:
        return build_policy(
            robots_url="",
            allow=False,
            available=False,
            ttl_seconds=ROBOTS_FAILURE_TTL_SECONDS,
            reason=ROBOTS_REASON_INVALID_URL,
            error="URL invalide."
        )

    cached_policy = get_cached_policy(origin)

    if cached_policy is not None:
        return cached_policy

    with get_policy_lock(origin):
        cached_policy = get_cached_policy(origin)

        if cached_policy is not None:
            return cached_policy

        policy = fetch_robots_policy(url)

        return cache_policy(origin, policy)


# Décision

def get_robots_decision(url: str) -> RobotsDecision:
    """Retourne une décision détaillée sans produire d'avertissement."""

    normalized_url = str(url or "").strip()
    origin = get_origin(normalized_url)

    if not origin:
        return RobotsDecision(
            allowed=False,
            reason=ROBOTS_REASON_INVALID_URL,
            url=normalized_url,
            origin="",
            robots_url="",
            error="URL invalide."
        )

    policy = get_robots_policy(normalized_url)

    if policy.reason in {
        ROBOTS_REASON_DENIED,
        ROBOTS_REASON_UNAVAILABLE,
        ROBOTS_REASON_INVALID_URL
    }:
        return RobotsDecision(
            allowed=False,
            reason=policy.reason,
            url=normalized_url,
            origin=origin,
            robots_url=policy.robots_url,
            crawl_delay=policy.crawl_delay,
            error=policy.error
        )

    try:
        allowed = policy.parser.can_fetch(
            USER_AGENT,
            normalized_url
        )
    except (TypeError, ValueError, AttributeError) as error:
        logger.debug(
            "Vérification robots.txt impossible pour %s : %s",
            normalized_url,
            error
        )
        return RobotsDecision(
            allowed=False,
            reason=ROBOTS_REASON_CHECK_ERROR,
            url=normalized_url,
            origin=origin,
            robots_url=policy.robots_url,
            crawl_delay=policy.crawl_delay,
            error=str(error)
        )

    return RobotsDecision(
        allowed=allowed,
        reason=(
            ROBOTS_REASON_ALLOWED
            if allowed
            else ROBOTS_REASON_DENIED
        ),
        url=normalized_url,
        origin=origin,
        robots_url=policy.robots_url,
        crawl_delay=policy.crawl_delay
    )


def is_url_allowed_by_robots(url: str) -> bool:
    """Retourne True si robots.txt autorise l'URL.

    Cette façade booléenne reste disponible pour compatibilité.
    Les nouveaux appels doivent privilégier get_robots_decision()
    afin de conserver le motif précis du refus.
    """

    return get_robots_decision(url).allowed


# Crawl-delay

def normalize_delay(value: float) -> float:
    """Normalise un délai positif et fini."""

    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0

    if 0.0 < delay < float("inf"):
        return delay

    return 0.0


def wait_for_crawl_delay(
    url: str,
    minimum_delay: float = 0.0
) -> None:
    """Attend avant la prochaine requête vers la même origine."""

    origin = get_origin(url)

    if not origin:
        return

    policy = get_robots_policy(url)
    delay = max(
        normalize_delay(policy.crawl_delay),
        normalize_delay(minimum_delay)
    )

    if delay <= 0:
        return

    with get_request_lock(origin):
        with _CACHE_LOCK:
            previous_request = _LAST_REQUEST_AT.get(origin)

        if previous_request is not None:
            elapsed = monotonic() - previous_request
            remaining = delay - elapsed

            if remaining > 0:
                sleep(remaining)

        with _CACHE_LOCK:
            _LAST_REQUEST_AT[origin] = monotonic()


def register_request(url: str) -> None:
    """Enregistre la fin d'une requête pour son origine."""

    origin = get_origin(url)

    if not origin:
        return

    with _CACHE_LOCK:
        _LAST_REQUEST_AT[origin] = monotonic()


# Réinitialisation

def clear_robots_cache(origin: str | None = None) -> None:
    """Vide tous les états robots.txt ou ceux d'une origine."""

    with _CACHE_LOCK:
        if origin is None:
            _POLICY_CACHE.clear()
            _LAST_REQUEST_AT.clear()
            return

        normalized_origin = (
            get_origin(origin)
            or str(origin or "").strip()
        )

        _POLICY_CACHE.pop(normalized_origin, None)
        _LAST_REQUEST_AT.pop(normalized_origin, None)