"""Nettoyage des champs textuels des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/processing/article_cleaner.py` (commit 93632c5).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from html import unescape
from typing import Any

from src.article.schema.article_schema import TEXT_FIELDS_TO_CLEAN
from src.cleaner.html_cleaner import remove_html
from src.logger import get_logger
from src.utils.value_utils import (
    collapse_whitespace,
    normalize_value
)

logger = get_logger(__name__)

# Configuration

CLEAN_TEXT_CACHE_SIZE = 2_000
MAX_CACHED_TEXT_LENGTH = 10_000

# Expressions régulières

INVISIBLE_CHARACTERS_PATTERN = re.compile(
    r"[\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e"
    r"\u2060-\u2069\ufeff]"
)
CONTROL_CHARACTERS_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)
MULTIPLE_WHITESPACE_PATTERN = re.compile(r"\s+")


# Nettoyage élémentaire

def normalize_unicode(text: str) -> str:
    """Uniformise les caractères Unicode équivalents."""

    if not text:
        return ""

    try:
        return unicodedata.normalize("NFKC", text)
    except (TypeError, ValueError):
        return text


def remove_invisible_characters(text: str) -> str:
    """Supprime les caractères invisibles et de contrôle."""

    if not text:
        return ""

    text = INVISIBLE_CHARACTERS_PATTERN.sub("", text)
    return CONTROL_CHARACTERS_PATTERN.sub(" ", text)


# Nettoyage textuel

def _clean_text_content(text: str) -> str:
    """Nettoie une chaîne déjà normalisée."""

    try:
        cleaned_text = unescape(text)
        cleaned_text = remove_html(cleaned_text)
        cleaned_text = unescape(cleaned_text)
        cleaned_text = normalize_unicode(cleaned_text)
        cleaned_text = remove_invisible_characters(cleaned_text)
        return collapse_whitespace(cleaned_text)

    except (TypeError, ValueError, UnicodeError, RuntimeError) as error:
        logger.debug("Texte impossible à nettoyer : %s", error)
        return collapse_whitespace(text)


@lru_cache(maxsize=CLEAN_TEXT_CACHE_SIZE)
def _clean_cached_text(text: str) -> str:
    """Nettoie et mémorise les textes de taille raisonnable."""

    return _clean_text_content(text)


def clean_text(value: Any) -> str:
    """Nettoie une valeur sans sérialiser les objets complexes."""

    text = normalize_value(value)

    if not text:
        return ""

    if len(text) <= MAX_CACHED_TEXT_LENGTH:
        return _clean_cached_text(text)

    return _clean_text_content(text)


# Nettoyage des articles

def clean_article(article: Mapping[str, Any]) -> dict[str, Any]:
    """Nettoie les champs textuels sans modifier l'article original."""

    if not isinstance(article, Mapping):
        raise TypeError("article doit être une structure de type Mapping.")

    cleaned_article = dict(article)

    for field in TEXT_FIELDS_TO_CLEAN:
        if field in cleaned_article:
            cleaned_article[field] = clean_text(cleaned_article[field])

    return cleaned_article


def clean_articles(articles: list[Any]) -> list[dict[str, Any]]:
    """Nettoie une collection d'articles normalisés."""

    if not isinstance(articles, list):
        raise TypeError("articles doit être une liste.")

    if not articles:
        logger.info("Aucun article à nettoyer.")
        return []

    invalid_indexes = [
        index
        for index, article in enumerate(articles)
        if not isinstance(article, Mapping)
    ]

    if invalid_indexes:
        raise TypeError(
            "La collection contient des articles non normalisés aux index : "
            f"{invalid_indexes[:10]}."
        )

    results = [clean_article(article) for article in articles]

    logger.info(
        "%s article(s) nettoyé(s) sur %s élément(s).",
        len(results),
        len(articles)
    )

    return results