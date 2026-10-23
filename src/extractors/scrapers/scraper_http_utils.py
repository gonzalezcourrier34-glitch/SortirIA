"""Outils HTTP communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_http_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import HTTP_HEADERS, MAX_RETRIES, REQUEST_TIMEOUT, RETRY_DELAY_SECONDS
from src.article.processing.article_cleaner import clean_text
from src.logger import get_logger
from src.robot.robots_utils import is_url_allowed_by_robots, register_request, wait_for_crawl_delay
from src.utils.parsing_utils import parse_boolean
from src.utils.url_utils import canonicalize_url
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)

HTML_ACCEPT_HEADER = (
    "text/html,application/xhtml+xml,"
    "application/xml;q=0.9,*/*;q=0.8"
)
HTML_ACCEPT_LANGUAGE_HEADER = "fr,en;q=0.8"

ANTI_BOT_MARKERS: tuple[str, ...] = (
    "access denied",
    "checking your browser",
    "enable javascript and cookies",
    "just a moment",
    "please verify you are a human",
    "security check required",
    "unusual traffic",
    "verify you are human"
)

_SCRAPER_REQUESTS_COUNT: ContextVar[int] = ContextVar(
    "scraper_requests_count",
    default=0
)


class ScraperPageError(Exception):
    """Erreur contrôlée associée au traitement d'une page HTML.

    Elle porte un motif court (`reason`) que le moteur reprend tel quel comme
    motif de rejet dans ses statistiques.
    """

    def __init__(self, reason: str, message: str):
        """Crée l'erreur avec son motif technique et son message lisible.

        Parameters
        ----------
        reason : str
            Motif court (`robots_interdit`, `page_vide`...) ; à défaut
            « page_inexploitable ».
        message : str
            Message détaillé pour les journaux.
        """
        super().__init__(message)
        self.reason = normalize_value(reason) or "page_inexploitable"


# Métriques
def reset_scraper_requests_count() -> None:
    """Réinitialise le compteur HTTP de l'extraction courante.

    Le compteur est une `ContextVar`, donc propre au contexte d'exécution.
    """

    _SCRAPER_REQUESTS_COUNT.set(0)


def increment_scraper_requests_count() -> None:
    """Compte une tentative HTTP réelle."""

    _SCRAPER_REQUESTS_COUNT.set(_SCRAPER_REQUESTS_COUNT.get() + 1)


def get_scraper_requests_count() -> int:
    """Retourne le nombre de tentatives HTTP de l'extraction courante.

    Returns
    -------
    int
        Nombre de requêtes réellement envoyées, réessais compris.
    """

    return _SCRAPER_REQUESTS_COUNT.get()


# Configuration
def get_scraper_http_options(
    source: Mapping[str, Any]
) -> tuple[bool, float]:
    """Retourne les options HTTP configurées.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (`respect_robots_txt`, par défaut vrai ;
        `request_delay_seconds`, par défaut 1 seconde).

    Returns
    -------
    tuple[bool, float]
        Respect de robots.txt, et délai minimal entre requêtes (jamais
        négatif ; 1.0 si la valeur est illisible).
    """

    respect_robots = parse_boolean(
        source.get("respect_robots_txt"),
        default=True
    )

    try:
        request_delay = max(
            float(source.get("request_delay_seconds", 1.0)),
            0.0
        )
    except (TypeError, ValueError):
        request_delay = 1.0

    return respect_robots, request_delay


def create_http_client() -> httpx.Client:
    """Construit une session HTTP réutilisable.

    Returns
    -------
    httpx.Client
        Client avec les en-têtes du projet, des en-têtes `Accept` orientés
        HTML, le délai `REQUEST_TIMEOUT` et le suivi des redirections.
    """

    return httpx.Client(
        headers={
            **HTTP_HEADERS,
            "Accept": HTML_ACCEPT_HEADER,
            "Accept-Language": HTML_ACCEPT_LANGUAGE_HEADER
        },
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True
    )


# Erreurs
def is_retryable_http_error(error: BaseException) -> bool:
    """Indique si une erreur HTTP peut être retentée.

    Parameters
    ----------
    error : BaseException
        Erreur levée pendant la requête.

    Returns
    -------
    bool
        Vrai pour un dépassement de délai, une erreur réseau, un statut 429 ou
        un statut 5xx.
    """

    if isinstance(
        error,
        (httpx.TimeoutException, httpx.NetworkError)
    ):
        return True

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code == 429 or 500 <= status_code < 600

    return False


def is_antibot_page(raw_html: str) -> bool:
    """Détecte les principales pages anti-bot.

    Cherche des phrases typiques (« just a moment », « verify you are
    human »...) dans le titre et les 20 000 premiers caractères du texte, puis
    des éléments de captcha ou de « challenge ».

    Parameters
    ----------
    raw_html : str
        HTML brut de la page reçue.

    Returns
    -------
    bool
        Vrai si la page ressemble à une barrière anti-robot.
    """

    if not raw_html:
        return False

    # Chercher les phrases typiques dans le titre et le début du texte
    soup = BeautifulSoup(raw_html, "html.parser")

    title = clean_text(
        soup.title.get_text(separator=" ", strip=True)
        if soup.title
        else ""
    ).lower()

    text = clean_text(
        soup.get_text(separator=" ", strip=True)
    )[:20_000].lower()

    searchable = f"{title} {text}"

    if any(marker in searchable for marker in ANTI_BOT_MARKERS):
        return True

    # Sinon, repérer un captcha ou un script de challenge dans la structure
    return bool(
        soup.select_one(
            "[id*='captcha'],[class*='captcha'],"
            "[id*='challenge'],[class*='challenge'],"
            "iframe[src*='captcha'],script[src*='challenge']"
        )
    )


# Téléchargement
@retry(
    retry=retry_if_exception(is_retryable_http_error),
    stop=stop_after_attempt(MAX_RETRIES + 1),
    wait=wait_exponential(
        multiplier=RETRY_DELAY_SECONDS,
        min=RETRY_DELAY_SECONDS,
        max=max(
            RETRY_DELAY_SECONDS * 4,
            RETRY_DELAY_SECONDS
        )
    ),
    reraise=True
)
def fetch_html(
    client: httpx.Client,
    url: str,
    respect_robots: bool = True,
    request_delay_seconds: float = 1.0
) -> tuple[str, str]:
    """Télécharge une page HTML.

    Les erreurs réseau, les délais dépassés et les statuts 429 ou 5xx sont
    retentés jusqu'à `MAX_RETRIES` fois avec une attente exponentielle
    (décorateur `retry`) ; chaque tentative est comptée.

    Parameters
    ----------
    client : httpx.Client
        Session HTTP à utiliser.
    url : str
        URL déjà canonisée de la page.
    respect_robots : bool
        Vérifie robots.txt (accès et crawl-delay) avant la requête. Si faux,
        robots.txt n'est pas téléchargé. Par défaut True.
    request_delay_seconds : float
        Délai minimal entre deux requêtes vers le même site, appliqué même
        si robots.txt n'est pas respecté. Par défaut 1 s.

    Returns
    -------
    tuple[str, str]
        Le HTML brut et l'URL finale après redirections.

    Raises
    ------
    ScraperPageError
        Si robots.txt interdit l'accès, si le contenu n'est pas du HTML, si la
        page est vide ou si c'est une page anti-bot.
    httpx.HTTPError
        Erreurs HTTP et réseau, laissées remonter pour `get_html`.
    """

    # Respecter robots.txt et le délai de politesse
    if respect_robots and not is_url_allowed_by_robots(url):
        raise ScraperPageError(
            "robots_interdit",
            f"Accès interdit par robots.txt : {url}"
        )

    wait_for_crawl_delay(
        url,
        minimum_delay=request_delay_seconds,
        use_robots=respect_robots
    )

    # Envoyer la requête, en la comptant et en la notant pour le crawl-delay
    increment_scraper_requests_count()

    try:
        response = client.get(url)
    finally:
        register_request(url)

    response.raise_for_status()

    # Refuser ce qui n'est pas une vraie page HTML exploitable
    content_type = (
        response.headers
        .get("Content-Type", "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )

    if (
        content_type
        and content_type not in {
            "text/html",
            "application/xhtml+xml"
        }
    ):
        raise ScraperPageError(
            "type_contenu_invalide",
            f"Contenu non HTML reçu : {content_type}"
        )

    raw_html = response.text

    if not raw_html.strip():
        raise ScraperPageError(
            "page_vide",
            f"Page HTML vide reçue pour {response.url}"
        )

    if is_antibot_page(raw_html):
        raise ScraperPageError(
            "page_antibot",
            f"Page anti-bot détectée pour {response.url}"
        )

    return raw_html, str(response.url)


def get_html(
    client: httpx.Client,
    url: Any,
    respect_robots: bool = True,
    request_delay_seconds: float = 1.0
) -> tuple[str, str]:
    """Télécharge une page en normalisant les erreurs.

    Toute erreur HTTP, réseau ou de décodage est convertie en
    `ScraperPageError` avec un motif court ; un statut 403 ou 429 est classé
    « page_antibot ».

    Parameters
    ----------
    client : httpx.Client
        Session HTTP à utiliser.
    url : Any
        URL de la page, canonisée avant la requête.
    respect_robots : bool
        Vérifie robots.txt avant la requête. Par défaut True.
    request_delay_seconds : float
        Délai minimal entre deux requêtes vers le même site. Par défaut 1 s.

    Returns
    -------
    tuple[str, str]
        Le HTML brut et l'URL finale après redirections.

    Raises
    ------
    ScraperPageError
        Si l'URL est invalide ou si le téléchargement échoue, quelle qu'en
        soit la cause.
    """

    # Valider l'URL avant toute requête
    normalized_url = canonicalize_url(url)

    if not normalized_url:
        raise ScraperPageError(
            "url_invalide",
            (
                "URL HTML invalide : "
                f"{normalize_value(url) or 'valeur absente'}"
            )
        )

    # Télécharger, puis traduire chaque famille d'erreur en motif de rejet
    try:
        return fetch_html(
            client,
            normalized_url,
            respect_robots,
            request_delay_seconds
        )

    except ScraperPageError:
        raise

    except httpx.TimeoutException as error:
        raise ScraperPageError(
            "delai_depasse",
            f"Délai dépassé pour {normalized_url}"
        ) from error

    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        reason = (
            "page_antibot"
            if status in {403, 429}
            else "statut_http_invalide"
        )

        raise ScraperPageError(
            reason,
            f"Erreur HTTP {status} pour {normalized_url}"
        ) from error

    except httpx.NetworkError as error:
        raise ScraperPageError(
            "erreur_reseau",
            (
                f"Erreur réseau pour {normalized_url} : "
                f"{error}"
            )
        ) from error

    except httpx.HTTPError as error:
        raise ScraperPageError(
            "erreur_http",
            (
                f"Erreur HTTP pour {normalized_url} : "
                f"{error}"
            )
        ) from error

    except (TypeError, ValueError, UnicodeError) as error:
        raise ScraperPageError(
            "page_inexploitable",
            (
                f"Page inexploitable pour {normalized_url} : "
                f"{error}"
            )
        ) from error