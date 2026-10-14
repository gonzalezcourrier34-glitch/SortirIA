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
    """Retourne le nombre maximal d'articles demandé."""

    configured = parse_non_negative_integer(
        source.get("max_articles", DEFAULT_MAX_HTML_ARTICLES),
        DEFAULT_MAX_HTML_ARTICLES
    )
    return min(configured, MAX_ARTICLES_PER_SOURCE)


def get_max_article_age_days(source: Mapping[str, Any]) -> int:
    """Retourne l'âge maximal autorisé pour un article."""

    configured = parse_non_negative_integer(
        source.get("max_article_age_days", MAX_ARTICLE_AGE_DAYS),
        MAX_ARTICLE_AGE_DAYS
    )

    if MAX_ARTICLE_AGE_DAYS <= 0:
        return configured

    return min(configured, MAX_ARTICLE_AGE_DAYS)


# Dates

def parse_iso_datetime(value: Any) -> datetime | None:
    """Convertit une date ISO en date UTC."""

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
    """Vérifie que la date respecte la période autorisée."""

    published_at = normalize_value(article.get("published_at"))

    if not published_at:
        return True, ""

    published_date = parse_iso_datetime(published_at)

    if published_date is None:
        return False, "date_publication_invalide"

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
    """Découvre les URL d'articles avec une session HTTP réutilisable."""

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
    """Télécharge et construit un article depuis une URL HTML."""

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
    """Vérifie la structure et l'URL d'un élément HTML standard."""

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
    """Vérifie l'âge maximal d'un article HTML standard."""

    return validate_article_age(
        article,
        get_max_article_age_days(source)
    )


# Fabrique

def create_standard_scraper_adapter(
    source_id: str,
    default_name: str
) -> ScraperAdapter:
    """Crée un adaptateur piloté par la configuration YAML."""

    return ScraperAdapter(
        source_id=source_id,
        default_name=default_name,
        iter_items=iter_standard_scraper_items,
        build_article=build_standard_scraper_article,
        validate_item=validate_standard_scraper_item,
        validate_article=validate_standard_scraper_article
    )