"""Outils URL communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_url_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from src.extractors.scrapers.scraper_http_utils import ScraperPageError, get_html, get_scraper_http_options
from src.logger import get_logger
from src.robot.robots_utils import is_url_allowed_by_robots
from src.utils.value_utils import normalize_value 
from src.utils.parsing_utils import parse_boolean
from src.utils.url_utils import canonicalize_url, is_valid_http_url

logger = get_logger(__name__)

IGNORED_URL_FRAGMENTS: tuple[str, ...] = (
    "/tag/", "/tags/", "/category/", "/categories/",
    "/author/", "/authors/", "/search", "/login",
    "/register", "/privacy", "/terms", "/contact",
    "/about", "/newsletter", "/video/", "/podcast/"
)


def make_absolute_url(value: Any, page_url: Any, canonicalize: bool = False) -> str:
    """Transforme une URL relative en URL HTTP valide."""

    raw_url = html.unescape(normalize_value(value))
    if not raw_url:
        return ""

    try:
        absolute_url = urljoin(normalize_value(page_url), raw_url)
    except (TypeError, ValueError):
        return ""

    if not is_valid_http_url(absolute_url):
        return ""

    return canonicalize_url(absolute_url) if canonicalize else absolute_url


def is_same_origin(url_a: str, url_b: str) -> bool:
    """Vérifie que deux URL appartiennent à la même origine."""

    first, second = urlsplit(url_a), urlsplit(url_b)
    return (first.scheme.lower(), first.netloc.lower()) == (second.scheme.lower(), second.netloc.lower())


def normalize_pattern_list(value: Any) -> list[str]:
    """Normalise une liste de motifs."""

    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        return []

    return [normalized.lower() for item in values if (normalized := normalize_value(item))]


def is_probable_article_url(article_url: str, listing_url: str, source: Mapping[str, Any]) -> bool:
    """Vérifie qu'une URL ressemble à un article."""

    if not is_valid_http_url(article_url):
        return False

    if parse_boolean(source.get("same_origin_only"), default=True) and not is_same_origin(article_url, listing_url):
        return False

    if any(fragment in urlsplit(article_url).path.lower() for fragment in IGNORED_URL_FRAGMENTS):
        return False

    include_patterns = normalize_pattern_list(source.get("include_url_patterns"))
    exclude_patterns = normalize_pattern_list(source.get("exclude_url_patterns"))

    if include_patterns and not any(pattern in article_url.lower() for pattern in include_patterns):
        return False

    if any(pattern in article_url.lower() for pattern in exclude_patterns):
        return False

    respect_robots, _ = get_scraper_http_options(source)
    return not respect_robots or is_url_allowed_by_robots(article_url)


def discover_article_links(client: httpx.Client, source: Mapping[str, Any], maximum_articles: int) -> list[str]:
    """Découvre les URL d'articles."""

    from bs4 import BeautifulSoup, Tag
    from soupsieve import SelectorSyntaxError

    raw_start_urls = source.get("start_urls") or source.get("base_url") or []
    start_urls = [raw_start_urls] if isinstance(raw_start_urls, str) else raw_start_urls

    if not isinstance(start_urls, Sequence):
        return []

    selectors = source.get("selectors") or {}
    if not isinstance(selectors, Mapping):
        return []

    list_selector = normalize_value(selectors.get("article"))
    link_selector = normalize_value(selectors.get("link")) or "a[href]"
    link_attribute = normalize_value(source.get("link_attribute")) or "href"

    respect_robots, delay = get_scraper_http_options(source)

    links: list[str] = []
    seen: set[str] = set()

    for start_url in start_urls:
        listing_url = canonicalize_url(start_url)
        if not listing_url:
            continue

        if respect_robots and not is_url_allowed_by_robots(listing_url):
            continue

        if not list_selector:
            if listing_url not in seen:
                seen.add(listing_url)
                links.append(listing_url)
            continue

        try:
            raw_html, final_url = get_html(client, listing_url, respect_robots, delay)
        except ScraperPageError:
            continue

        effective = canonicalize_url(final_url) or listing_url
        soup = BeautifulSoup(raw_html, "html.parser")

        try:
            containers = soup.select(list_selector)
        except SelectorSyntaxError:
            return links

        for container in containers:
            if not isinstance(container, Tag):
                continue

            try:
                elements = container.select(link_selector)
            except SelectorSyntaxError:
                return links

            if container.name == "a" and container.get(link_attribute):
                elements = [container, *elements]

            for element in elements:
                if not isinstance(element, Tag):
                    continue

                article_url = make_absolute_url(element.get(link_attribute), effective, canonicalize=True)

                if not article_url or article_url in seen or not is_probable_article_url(article_url, effective, source):
                    continue

                seen.add(article_url)
                links.append(article_url)

                if len(links) >= maximum_articles:
                    return links

    return links