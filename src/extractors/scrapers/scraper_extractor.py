"""Implémentation HTML standard des scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_extractor.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup

from config.settings import (
    MAX_ARTICLE_AGE_DAYS,
    MAX_ARTICLES_PER_SOURCE
)
from src.extractors.scrapers.scraper_adapter import ScraperAdapter
from src.extractors.scrapers.scraper_article_utils import build_scraped_article
from src.extractors.scrapers.scraper_http_utils import (
    create_http_client,
    get_html
)
from src.extractors.scrapers.scraper_metadata_utils import extract_metadata
from src.extractors.scrapers.scraper_url_utils import discover_article_links
from src.utils.date_utils import get_current_datetime
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.url_utils import canonicalize_url
from src.utils.value_utils import normalize_value

DEFAULT_MAX_HTML_ARTICLES = 1


# Limites

def get_max_articles(source: Mapping[str, Any]) -> int:
    """Retourne le nombre maximal d'articles demandé.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (clé `max_articles`, par défaut
        `DEFAULT_MAX_HTML_ARTICLES`, soit 1).

    Returns
    -------
    int
        La valeur configurée, plafonnée à `MAX_ARTICLES_PER_SOURCE`.
    """

    configured = parse_non_negative_integer(
        source.get("max_articles", DEFAULT_MAX_HTML_ARTICLES),
        DEFAULT_MAX_HTML_ARTICLES
    )
    return min(configured, MAX_ARTICLES_PER_SOURCE)


def get_max_article_age_days(source: Mapping[str, Any]) -> int:
    """Retourne l'âge maximal autorisé pour un article.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (clé `max_article_age_days`, par défaut
        `MAX_ARTICLE_AGE_DAYS`).

    Returns
    -------
    int
        L'âge maximal en jours, plafonné au réglage global quand celui-ci est
        positif ; 0 signifie « pas de limite ».
    """

    configured = parse_non_negative_integer(
        source.get("max_article_age_days", MAX_ARTICLE_AGE_DAYS),
        MAX_ARTICLE_AGE_DAYS
    )

    if MAX_ARTICLE_AGE_DAYS <= 0:
        return configured

    return min(configured, MAX_ARTICLE_AGE_DAYS)


# Dates

def parse_iso_datetime(value: Any) -> datetime | None:
    """Convertit une date ISO en date UTC.

    Le suffixe `Z` est accepté ; une date sans fuseau est considérée en UTC.

    Parameters
    ----------
    value : Any
        Date au format ISO 8601.

    Returns
    -------
    datetime | None
        La date avec fuseau UTC, ou `None` si la valeur est vide ou illisible.
    """

    normalized = normalize_value(value)

    if not normalized:
        return None

    try:
        parsed = datetime.fromisoformat(
            normalized.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def validate_article_age(
    article: Mapping[str, Any],
    max_article_age_days: int
) -> tuple[bool, str]:
    """Vérifie que la date respecte la période autorisée.

    Un article sans date est accepté ; une date illisible, dans le futur (plus
    de 5 minutes d'avance) ou trop ancienne est refusée.

    Parameters
    ----------
    article : Mapping[str, Any]
        Article à contrôler (champ `published_at`).
    max_article_age_days : int
        Âge maximal en jours ; 0 ou moins désactive le contrôle d'ancienneté.

    Returns
    -------
    tuple[bool, str]
        Validité de l'article et motif du rejet le cas échéant.
    """

    # Lire la date de publication, absente ou illisible
    published_at = normalize_value(article.get("published_at"))

    if not published_at:
        return True, ""

    published_date = parse_iso_datetime(published_at)

    if published_date is None:
        return False, "date_publication_invalide"

    # Comparer à maintenant : ni future, ni trop ancienne
    now = get_current_datetime()

    if published_date > now + timedelta(minutes=5):
        return False, "date_publication_future"

    if (
        max_article_age_days > 0
        and published_date < now - timedelta(days=max_article_age_days)
    ):
        return False, "article_trop_ancien"

    return True, ""


# Extraction standard

def iter_standard_scraper_items(
    source: Mapping[str, Any]
) -> Iterable[tuple[httpx.Client, str]]:
    """Découvre les URL d'articles avec une session HTTP réutilisable.

    Le client HTTP reste ouvert tant que le générateur est parcouru, pour être
    réutilisé lors du téléchargement de chaque article.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (pages de liste, sélecteurs, limite).

    Yields
    ------
    tuple[httpx.Client, str]
        Le client HTTP partagé et l'URL d'un article découvert.
    """

    with create_http_client() as client:
        article_urls = discover_article_links(
            client=client,
            source=source,
            maximum_articles=get_max_articles(source)
        )

        for article_url in article_urls:
            yield client, article_url


def build_standard_scraper_article(
    item: tuple[httpx.Client, str],
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Télécharge et construit un article depuis une URL HTML.

    Les erreurs de téléchargement (`ScraperPageError` levée par `get_html`)
    remontent au moteur, qui les compte comme rejet.

    Parameters
    ----------
    item : tuple[httpx.Client, str]
        Client HTTP partagé et URL de l'article.
    source : Mapping[str, Any]
        Configuration de la source.

    Returns
    -------
    dict[str, Any]
        L'article standard, ou un dictionnaire vide si l'URL est invalide.
    """

    client, article_url = item
    article_url = canonicalize_url(article_url)

    if not article_url:
        return {}

    # Télécharge la page avec la session HTTP déjà ouverte.
    raw_html, final_url = get_html(
        client,
        article_url,
        source
    )

    page_url = canonicalize_url(final_url) or article_url

    # Prépare les éléments attendus par scraper_article_utils.
    soup = BeautifulSoup(raw_html, "html.parser")
    metadata = extract_metadata(raw_html, page_url)

    return build_scraped_article(
        soup=soup,
        source=source,
        metadata=metadata,
        page_url=page_url
    )


# Validation standard

def validate_standard_scraper_item(
    item: tuple[httpx.Client, str],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Vérifie la structure et l'URL d'un élément HTML standard.

    Parameters
    ----------
    item : tuple[httpx.Client, str]
        Élément attendu : un client HTTP et une URL.
    source : Mapping[str, Any]
        Configuration de la source ; non utilisée.

    Returns
    -------
    tuple[bool, str]
        Validité de l'élément et motif du rejet le cas échéant.
    """

    del source

    if not isinstance(item, tuple) or len(item) != 2:
        return False, "element_invalide"

    client, article_url = item

    if not isinstance(client, httpx.Client):
        return False, "client_http_invalide"

    if not canonicalize_url(article_url):
        return False, "url_invalide"

    return True, ""


def validate_standard_scraper_article(
    article: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Vérifie l'âge maximal d'un article HTML standard.

    Parameters
    ----------
    article : Mapping[str, Any]
        Article construit.
    source : Mapping[str, Any]
        Configuration de la source (âge maximal).

    Returns
    -------
    tuple[bool, str]
        Validité de l'article et motif du rejet le cas échéant.
    """

    return validate_article_age(
        article,
        get_max_article_age_days(source)
    )


# Fabrique

def create_standard_scraper_adapter(
    source_id: str,
    default_name: str
) -> ScraperAdapter:
    """Crée un adaptateur piloté par la configuration YAML.

    Parameters
    ----------
    source_id : str
        Identifiant technique de la source.
    default_name : str
        Nom affiché de l'extracteur.

    Returns
    -------
    ScraperAdapter
        Un adaptateur branché sur les fonctions standard de ce module.
    """

    return ScraperAdapter(
        source_id=source_id,
        default_name=default_name,
        iter_items=iter_standard_scraper_items,
        build_article=build_standard_scraper_article,
        validate_item=validate_standard_scraper_item,
        validate_article=validate_standard_scraper_article
    )