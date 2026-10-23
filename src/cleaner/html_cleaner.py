"""Nettoyage du contenu HTML.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/cleaner/html_cleaner.py` (commit 93632c5).

Ce module contient uniquement :

- la détection prudente du HTML ;
- la création du parseur BeautifulSoup ;
- la suppression des balises et éléments non textuels.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, FeatureNotFound
from bs4.builder import ParserRejectedMarkup

from src.logger import get_logger

logger = get_logger(__name__)

# Configuration

MAX_HTML_LENGTH = 1_000_000
PREFERRED_HTML_PARSER = "lxml"
FALLBACK_HTML_PARSER = "html.parser"

# Expressions régulières

HTML_TAG_PATTERN = re.compile(
    r"</?[a-zA-Z][^>]*>|<!--.*?-->|<!DOCTYPE[^>]*>",
    flags=re.DOTALL | re.IGNORECASE
)

NON_TEXTUAL_HTML_ELEMENTS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas"
)


# Détection

def contains_html(text: str) -> bool:
    """Détecte prudemment la présence probable de balises HTML.

    Parameters
    ----------
    text : str
        Le texte à examiner.

    Returns
    -------
    bool
        True si le texte contient une balise, un commentaire ou un DOCTYPE.
    """

    return bool(text and HTML_TAG_PATTERN.search(text))


# Parsing

def create_html_soup(text: str) -> BeautifulSoup:
    """Crée un parseur HTML avec repli sur le parseur natif.

    Parameters
    ----------
    text : str
        Le HTML à analyser.

    Returns
    -------
    BeautifulSoup
        Le document analysé par `lxml`, ou par `html.parser` si `lxml` manque.
    """

    try:
        return BeautifulSoup(text, PREFERRED_HTML_PARSER)
    except FeatureNotFound:
        return BeautifulSoup(text, FALLBACK_HTML_PARSER)


# Nettoyage

def remove_html(text: str) -> str:
    """Retire les balises et les éléments HTML non textuels.

    Un texte sans HTML est rendu tel quel. Au-delà de `MAX_HTML_LENGTH`
    caractères, le contenu est tronqué avant l'analyse.

    Parameters
    ----------
    text : str
        Le texte, éventuellement HTML.

    Returns
    -------
    str
        Le texte visible, ses morceaux séparés par des espaces ; en cas d'échec
        de l'analyse, le contenu (tronqué) non nettoyé.
    """

    if not text or not contains_html(text):
        return text

    # Tronquer les contenus démesurés
    html_content = text[:MAX_HTML_LENGTH]

    if len(text) > MAX_HTML_LENGTH:
        logger.warning(
            "Contenu HTML tronqué de %s à %s caractères avant nettoyage.",
            len(text),
            MAX_HTML_LENGTH
        )

    # Analyser, retirer scripts, styles et médias, puis garder le texte visible
    try:
        soup = create_html_soup(html_content)

        for element in soup(NON_TEXTUAL_HTML_ELEMENTS):
            element.decompose()

        return soup.get_text(separator=" ", strip=True)

    except (
        ParserRejectedMarkup,
        TypeError,
        ValueError,
        AttributeError,
        RecursionError
    ) as error:
        logger.debug(
            "Contenu HTML impossible à analyser : %s",
            error
        )
        return html_content