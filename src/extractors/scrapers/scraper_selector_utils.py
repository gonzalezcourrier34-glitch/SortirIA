"""Outils BeautifulSoup communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_selector_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bs4 import BeautifulSoup, Tag
from soupsieve import SelectorSyntaxError

from src.logger import get_logger
from src.article.processing.article_cleaner import clean_text 

from src.utils.extractor_utils import convert_date_to_iso, normalize_value

logger = get_logger(__name__)

def parse_date(value: Any) -> str:
    """Convertit une date au format ISO 8601."""

    return convert_date_to_iso(value)


def select_element(soup: BeautifulSoup, selector: Any) -> Tag | None:
    """Retourne le premier élément correspondant."""

    selector = normalize_value(selector)
    if not selector:
        return None

    try:
        element = soup.select_one(selector)
    except SelectorSyntaxError as error:
        logger.warning("Sélecteur CSS invalide '%s' : %s", selector, error)
        return None

    return element if isinstance(element, Tag) else None


def select_text(soup: BeautifulSoup, selector: Any) -> str:
    """Retourne le texte du premier élément."""

    element = select_element(soup, selector)
    if element is None:
        return ""

    return clean_text(element.get_text(separator=" ", strip=True))


def select_attribute(
    soup: BeautifulSoup,
    selector: Any,
    attributes: Sequence[str]
) -> str:
    """Retourne le premier attribut non vide."""

    element = select_element(soup, selector)
    if element is None:
        return ""

    for attribute in attributes:
        value = normalize_value(element.get(attribute))
        if value:
            return value

    return ""


def get_source_selector(source: Mapping[str, Any], selector_name: str) -> str:
    """Retourne un sélecteur CSS depuis la configuration."""

    selectors = source.get("selectors") or {}

    if not isinstance(selectors, Mapping):
        return ""

    return normalize_value(selectors.get(selector_name))