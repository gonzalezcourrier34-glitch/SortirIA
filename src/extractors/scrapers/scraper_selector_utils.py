"""Outils BeautifulSoup communs aux scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_selector_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bs4 import BeautifulSoup, Tag
from soupsieve import SelectorSyntaxError

from src.article.processing.article_cleaner import clean_text
from src.logger import get_logger
from src.utils.extractor_utils import convert_date_to_iso, normalize_value

logger = get_logger(__name__)

def parse_date(value: Any) -> str:
    """Convertit une date au format ISO 8601.

    Parameters
    ----------
    value : Any
        Date brute lue dans la page.

    Returns
    -------
    str
        Le résultat de `convert_date_to_iso`.
    """

    return convert_date_to_iso(value)


def select_element(soup: BeautifulSoup, selector: Any) -> Tag | None:
    """Retourne le premier élément correspondant.

    Un sélecteur CSS invalide est journalisé en avertissement et traité comme
    « rien trouvé ».

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    selector : Any
        Sélecteur CSS ; vide, il ne trouve rien.

    Returns
    -------
    Tag | None
        Le premier élément trouvé, ou `None`.
    """

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
    """Retourne le texte du premier élément.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    selector : Any
        Sélecteur CSS.

    Returns
    -------
    str
        Le texte nettoyé de l'élément, ou une chaîne vide.
    """

    element = select_element(soup, selector)
    if element is None:
        return ""

    return clean_text(element.get_text(separator=" ", strip=True))


def select_attribute(
    soup: BeautifulSoup,
    selector: Any,
    attributes: Sequence[str]
) -> str:
    """Retourne le premier attribut non vide.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    selector : Any
        Sélecteur CSS de l'élément.
    attributes : Sequence[str]
        Noms d'attributs à essayer, dans l'ordre.

    Returns
    -------
    str
        La première valeur non vide trouvée, ou une chaîne vide.
    """

    element = select_element(soup, selector)
    if element is None:
        return ""

    for attribute in attributes:
        value = normalize_value(element.get(attribute))
        if value:
            return value

    return ""


def get_source_selector(source: Mapping[str, Any], selector_name: str) -> str:
    """Retourne un sélecteur CSS depuis la configuration.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (bloc `selectors`).
    selector_name : str
        Nom du sélecteur voulu (`title`, `content`, `image`...).

    Returns
    -------
    str
        Le sélecteur configuré, ou une chaîne vide.
    """

    selectors = source.get("selectors") or {}

    if not isinstance(selectors, Mapping):
        return ""

    return normalize_value(selectors.get(selector_name))