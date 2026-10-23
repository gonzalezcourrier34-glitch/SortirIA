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
    ROBOTS_REASON_UNAVAILABLE,
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
    """Politique robots.txt mise en cache pour une origine.

    Attributes
    ----------
    parser : RobotFileParser
        Parseur des règles (réel, ou synthétique « tout permis » / « tout
        interdit » quand le fichier manque ou est illisible).
    robots_url : str
        URL du robots.txt lu (finale, après redirection).
    available : bool
        Vrai si un robots.txt a réellement été lu, ou si son accès est refusé.
    allow_without_file : bool
        Vrai quand le site ne publie pas de robots.txt (404 ou 410).
    crawl_delay : float
        Délai demandé entre deux requêtes, en secondes (0 si aucun).
    expires_at : float
        Instant d'expiration, sur l'horloge `monotonic()`.
    reason : str
        Motif de la politique (`ROBOTS_REASON_*`). Par défaut « autorisé ».
    error : str
        Message d'erreur éventuel. Vide par défaut.
    """

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
    """Décision détaillée concernant l'accès à une URL.

    Attributes
    ----------
    allowed : bool
        Vrai si l'URL peut être téléchargée.
    reason : str
        Motif de la décision (`ROBOTS_REASON_*`).
    url : str
        URL examinée.
    origin : str
        Origine de l'URL (schéma et hôte), vide si l'URL est invalide.
    robots_url : str
        URL du robots.txt consulté.
    crawl_delay : float
        Délai demandé entre deux requêtes, en secondes. Par défaut 0.
    error : str
        Message d'erreur éventuel. Vide par défaut.
    """

    allowed: bool
    reason: str
    url: str
    origin: str
    robots_url: str
    crawl_delay: float = 0.0
    error: str = ""

    @property
    def denied(self) -> bool:
        """Indique si l'accès est refusé.

        Returns
        -------
        bool
            L'inverse de `allowed`.
        """

        return not self.allowed


# État partagé

_POLICY_CACHE: dict[str, RobotsPolicy] = {}
_LAST_REQUEST_AT: dict[str, float] = {}
_POLICY_LOCKS: dict[str, Lock] = {}
_REQUEST_LOCKS: dict[str, Lock] = {}
_CACHE_LOCK = Lock()


# URL

def get_origin(url: str) -> str:
    """Extrait l'origine normalisée d'une URL HTTP.

    Parameters
    ----------
    url : str
        URL complète.

    Returns
    -------
    str
        Schéma et hôte en minuscules (`https://exemple.fr`), ou une chaîne
        vide si l'URL n'est pas une URL HTTP valide.
    """

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
    """Construit l'URL robots.txt d'une origine.

    Parameters
    ----------
    origin : str
        Origine produite par `get_origin`.

    Returns
    -------
    str
        L'URL `<origine>/robots.txt`, ou une chaîne vide.
    """

    if not origin:
        return ""

    return f"{origin.rstrip('/')}/robots.txt"


def get_effective_port(url: str) -> int | None:
    """Retourne le port explicite ou le port par défaut d'une URL.

    Parameters
    ----------
    url : str
        URL à analyser.

    Returns
    -------
    int | None
        Le port indiqué, sinon 80 pour HTTP et 443 pour HTTPS ; `None` pour
        un autre schéma ou une URL illisible.
    """

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
    """Autorise uniquement une redirection sûre vers le même hôte.

    Sont acceptés : même hôte, même schéma et même port, ou le passage de
    HTTP (port 80) à HTTPS (port 443) sur le même hôte.

    Parameters
    ----------
    original_url : str
        URL du robots.txt demandée.
    final_url : str
        URL atteinte après redirections.

    Returns
    -------
    bool
        Vrai si la redirection reste sur le même site.
    """

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
    """Crée un parseur synthétique autorisant ou refusant tout accès.

    Parameters
    ----------
    robots_url : str
        URL du robots.txt représenté.
    allow : bool
        Vrai pour « Allow: / », faux pour « Disallow: / ».

    Returns
    -------
    RobotFileParser
        Le parseur prêt à l'emploi.
    """

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
    """Construit une politique synthétique mise en cache.

    Sert quand aucun robots.txt exploitable n'a été lu (absent, refusé,
    illisible, URL invalide).

    Parameters
    ----------
    robots_url : str
        URL du robots.txt concerné.
    allow : bool
        Autorise (vrai) ou refuse (faux) tout accès.
    available : bool
        Indique si un robots.txt a été trouvé.
    allow_without_file : bool
        Vrai quand le site ne publie pas de robots.txt. Par défaut False.
    crawl_delay : float
        Délai entre requêtes, ramené à 0 s'il est négatif. Par défaut 0.
    ttl_seconds : int
        Durée de vie en cache. Par défaut `ROBOTS_CACHE_TTL_SECONDS` (1 h).
    reason : str
        Motif de la politique. Par défaut `ROBOTS_REASON_ALLOWED`.
    error : str
        Message d'erreur éventuel. Vide par défaut.

    Returns
    -------
    RobotsPolicy
        La politique construite.
    """

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
    """Retourne ou crée le verrou associé à une origine.

    Parameters
    ----------
    locks : dict[str, Lock]
        Dictionnaire de verrous (chargement ou requêtes).
    origin : str
        Origine concernée.

    Returns
    -------
    Lock
        Le verrou de cette origine, créé au premier appel.
    """

    with _CACHE_LOCK:
        return locks.setdefault(origin, Lock())


def get_policy_lock(origin: str) -> Lock:
    """Retourne le verrou de chargement d'une politique.

    Parameters
    ----------
    origin : str
        Origine concernée.

    Returns
    -------
    Lock
        Le verrou qui évite de télécharger deux fois le même robots.txt.
    """

    return get_origin_lock(_POLICY_LOCKS, origin)


def get_request_lock(origin: str) -> Lock:
    """Retourne le verrou d'application du crawl-delay.

    Parameters
    ----------
    origin : str
        Origine concernée.

    Returns
    -------
    Lock
        Le verrou qui sérialise les attentes vers cette origine.
    """

    return get_origin_lock(_REQUEST_LOCKS, origin)


def get_cached_policy(origin: str) -> RobotsPolicy | None:
    """Retourne une politique non expirée.

    Une politique expirée est retirée du cache au passage.

    Parameters
    ----------
    origin : str
        Origine concernée.

    Returns
    -------
    RobotsPolicy | None
        La politique en cache, ou `None` si absente ou expirée.
    """

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
    """Enregistre une politique dans le cache.

    Parameters
    ----------
    origin : str
        Origine concernée.
    policy : RobotsPolicy
        Politique à conserver.

    Returns
    -------
    RobotsPolicy
        La même politique, pour chaîner l'appel.
    """

    with _CACHE_LOCK:
        _POLICY_CACHE[origin] = policy

    return policy


# Téléchargement

def get_robots_headers() -> dict[str, str]:
    """Retourne les en-têtes utilisés pour robots.txt.

    Returns
    -------
    dict[str, str]
        Les en-têtes du projet, avec le User-Agent et un `Accept` texte.
    """

    return {
        **HTTP_HEADERS,
        "User-Agent": USER_AGENT,
        "Accept": "text/plain,*/*;q=0.1"
    }


def parse_content_length(response: httpx.Response) -> int | None:
    """Lit Content-Length lorsqu'il est exploitable.

    Parameters
    ----------
    response : httpx.Response
        Réponse HTTP reçue.

    Returns
    -------
    int | None
        La taille annoncée (jamais négative), ou `None` si absente ou
        illisible.
    """

    value = response.headers.get("Content-Length")

    if not value:
        return None

    try:
        size = int(value)
    except (TypeError, ValueError, OverflowError):
        return None

    return max(size, 0)


def read_limited_content(response: httpx.Response) -> bytes:
    """Lit une réponse en streaming sans dépasser la taille maximale.

    La limite est `ROBOTS_MAX_SIZE_BYTES` (1 Mo), vérifiée sur la taille
    annoncée puis pendant la lecture.

    Parameters
    ----------
    response : httpx.Response
        Réponse ouverte en streaming.

    Returns
    -------
    bytes
        Le contenu brut du fichier.

    Raises
    ------
    ValueError
        Si le fichier annoncé ou lu dépasse la taille maximale.
    """

    # Refuser d'emblée un fichier annoncé trop gros
    announced_size = parse_content_length(response)

    if (
        announced_size is not None
        and announced_size > ROBOTS_MAX_SIZE_BYTES
    ):
        raise ValueError(
            "Fichier robots.txt annoncé comme trop volumineux."
        )

    # Lire morceau par morceau en surveillant la taille cumulée
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
    """Décode le contenu de robots.txt avec un encodage sûr.

    Parameters
    ----------
    response : httpx.Response
        Réponse HTTP, dont l'encodage annoncé est essayé d'abord.
    content : bytes
        Contenu brut lu.

    Returns
    -------
    str
        Le texte décodé ; en UTF-8 avec remplacement si l'encodage annoncé
        échoue.
    """

    encoding = response.encoding or "utf-8"

    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def looks_like_html(content: str) -> bool:
    """Détecte une page HTML renvoyée à la place de robots.txt.

    Parameters
    ----------
    content : str
        Texte décodé.

    Returns
    -------
    bool
        Vrai si le texte commence par une balise HTML usuelle.
    """

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
    """Construit une politique depuis une réponse robots.txt valide.

    Le crawl-delay propre à notre User-Agent prime sur celui de `*`.

    Parameters
    ----------
    response : httpx.Response
        Réponse HTTP du robots.txt.
    robots_url : str
        URL demandée, utilisée si l'URL finale est vide.
    content : bytes
        Contenu brut lu.

    Returns
    -------
    RobotsPolicy
        La politique lue, valable `ROBOTS_CACHE_TTL_SECONDS`.

    Raises
    ------
    ValueError
        Si une page HTML a été reçue à la place du robots.txt.
    """

    # Décoder et écarter une page HTML servie à la place du fichier
    decoded_content = decode_robots_content(response, content)

    if looks_like_html(decoded_content):
        raise ValueError(
            "Une page HTML a été reçue à la place de robots.txt."
        )

    # Analyser les règles
    parser = RobotFileParser()
    parser.set_url(str(response.url))
    parser.parse(decoded_content.splitlines())

    # Lire le crawl-delay : notre User-Agent d'abord, puis `*`
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
    """Télécharge robots.txt et retourne une politique exploitable.

    Ne lève jamais d'exception : 404 ou 410 donnent « tout permis », 401 ou
    403 « tout interdit », et toute autre erreur (redirection vers un autre
    site, statut d'erreur, fichier trop gros ou HTML, panne réseau) une
    politique « indisponible » qui refuse l'accès, gardée 60 secondes
    seulement.

    Parameters
    ----------
    url : str
        URL quelconque du site concerné.

    Returns
    -------
    RobotsPolicy
        La politique à appliquer pour l'origine de l'URL.
    """

    # Écarter une URL invalide
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

    # Télécharger le fichier en streaming
    try:
        with httpx.Client(
            headers=get_robots_headers(),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True
        ) as client, client.stream("GET", robots_url) as response:
            final_url = str(response.url)

            # Refuser une redirection vers un autre site
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

            # Interpréter le statut : absent, refusé, en erreur ou lisible
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

    # Échec : politique « indisponible » qui refuse, gardée peu de temps
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
    """Retourne la politique en cache ou la charge une seule fois.

    Le cache est revérifié sous le verrou de l'origine, pour qu'un seul
    thread télécharge le robots.txt.

    Parameters
    ----------
    url : str
        URL quelconque du site concerné.

    Returns
    -------
    RobotsPolicy
        La politique de l'origine (refus pour une URL invalide).
    """

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
    """Retourne une décision détaillée sans produire d'avertissement.

    Une URL invalide, un robots.txt refusé ou indisponible, ou une erreur de
    vérification donnent un refus avec son motif.

    Parameters
    ----------
    url : str
        URL à télécharger.

    Returns
    -------
    RobotsDecision
        La décision, son motif et le crawl-delay applicable.
    """

    # Écarter une URL invalide
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

    # Refuser si la politique elle-même est un refus ou une indisponibilité
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

    # Appliquer les règles du fichier à notre User-Agent
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

    Parameters
    ----------
    url : str
        URL à télécharger.

    Returns
    -------
    bool
        Vrai si l'accès est autorisé.
    """

    return get_robots_decision(url).allowed


# Crawl-delay

def normalize_delay(value: float) -> float:
    """Normalise un délai positif et fini.

    Parameters
    ----------
    value : float
        Délai en secondes.

    Returns
    -------
    float
        Le délai s'il est strictement positif et fini, sinon 0.
    """

    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0

    if 0.0 < delay < float("inf"):
        return delay

    return 0.0


def wait_for_crawl_delay(
    url: str,
    minimum_delay: float = 0.0,
    use_robots: bool = True
) -> None:
    """Attend avant la prochaine requête vers la même origine.

    Le délai retenu est le plus grand entre le crawl-delay du robots.txt et
    `minimum_delay` ; l'attente est mesurée depuis la dernière requête vers
    cette origine. Si `use_robots` est faux, robots.txt n'est pas lu et seul
    `minimum_delay` compte.

    Parameters
    ----------
    url : str
        URL qui va être téléchargée.
    minimum_delay : float
        Délai minimal voulu par la source, en secondes. Par défaut 0.
    use_robots : bool
        Tient compte du crawl-delay de robots.txt (et donc télécharge ce
        fichier si besoin). Par défaut True.
    """

    origin = get_origin(url)

    if not origin:
        return

    # Calculer le délai à respecter (robots.txt lu seulement s'il est respecté)
    robots_delay = (
        normalize_delay(get_robots_policy(url).crawl_delay)
        if use_robots
        else 0.0
    )
    delay = max(
        robots_delay,
        normalize_delay(minimum_delay)
    )

    if delay <= 0:
        return

    # Attendre le reste du délai, puis noter l'heure de cette requête
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
    """Enregistre la fin d'une requête pour son origine.

    Parameters
    ----------
    url : str
        URL qui vient d'être téléchargée.
    """

    origin = get_origin(url)

    if not origin:
        return

    with _CACHE_LOCK:
        _LAST_REQUEST_AT[origin] = monotonic()


# Réinitialisation

def clear_robots_cache(origin: str | None = None) -> None:
    """Vide tous les états robots.txt ou ceux d'une origine.

    Les verrous ne sont pas retirés. Utile surtout pour les tests.

    Parameters
    ----------
    origin : str | None
        Origine ou URL à oublier. Par défaut `None`, qui vide tout.
    """

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