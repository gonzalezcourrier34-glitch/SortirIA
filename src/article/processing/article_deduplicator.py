"""Déduplication des articles CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/processing/article_deduplicator.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from src.article.article_utils import (
    get_article_role,
    get_article_source,
    get_article_url
)
from src.logger import get_logger
from src.utils.url_utils import canonicalize_url
from src.utils.value_utils import normalize_whitespace

logger = get_logger(__name__)


# Construction des clés

def get_article_duplicate_keys(article: Mapping[str, Any]) -> set[str]:
    """Construit les clés stables utilisées pour détecter les doublons."""

    if not isinstance(article, Mapping):
        return set()

    duplicate_keys: set[str] = set()
    role = get_article_role(article)
    role_prefix = f"{role}:" if role else ""

    article_id = normalize_whitespace(article.get("id"))
    article_url = canonicalize_url(get_article_url(article)).casefold()
    source = get_article_source(article).casefold()
    title = normalize_whitespace(article.get("title")).casefold()
    published_day = normalize_whitespace(article.get("published_at"))[:10]

    if article_id:
        duplicate_keys.add(f"{role_prefix}id:{article_id}")

    if article_url:
        duplicate_keys.add(f"{role_prefix}url:{article_url}")

    if title:
        title_identifier = f"{role}|{source}|{title}|{published_day}"
        title_hash = hashlib.md5(
            title_identifier.encode("utf-8"),
            usedforsecurity=False
        ).hexdigest()

        duplicate_keys.add(f"{role_prefix}title:{title_hash}")

    return duplicate_keys


# Détection et enregistrement

def is_duplicate_article(
    article: Mapping[str, Any],
    seen_keys: set[str]
) -> bool:
    """Vérifie si un article a déjà été rencontré."""

    if not isinstance(article, Mapping):
        raise TypeError("article doit être une structure de type Mapping.")
    if not isinstance(seen_keys, set):
        raise TypeError("seen_keys doit être un ensemble de chaînes.")

    return bool(get_article_duplicate_keys(article) & seen_keys)


def register_article(
    article: Mapping[str, Any],
    seen_keys: set[str]
) -> None:
    """Enregistre les clés d'un article déjà traité."""

    if not isinstance(article, Mapping):
        raise TypeError("article doit être une structure de type Mapping.")
    if not isinstance(seen_keys, set):
        raise TypeError("seen_keys doit être un ensemble de chaînes.")

    seen_keys.update(get_article_duplicate_keys(article))


# Déduplication d'une collection

def deduplicate_articles(articles: list[Any]) -> list[dict[str, Any]]:
    """Supprime les doublons en conservant leur dernière version."""

    if not isinstance(articles, list):
        raise TypeError("articles doit être une liste.")

    if not articles:
        logger.info("Aucun article à dédupliquer.")
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

    unique_articles: list[dict[str, Any] | None] = []
    keys_by_index: list[set[str]] = []
    index_by_key: dict[str, int] = {}

    for raw_article in articles:
        article = dict(raw_article)
        article_keys = get_article_duplicate_keys(article)

        if not article_keys:
            unique_articles.append(article)
            keys_by_index.append(set())
            continue

        duplicate_indexes = {
            index_by_key[key]
            for key in article_keys
            if key in index_by_key
        }

        if not duplicate_indexes:
            article_index = len(unique_articles)
            unique_articles.append(article)
            keys_by_index.append(set(article_keys))
        else:
            article_index = min(duplicate_indexes)
            merged_keys = set(article_keys)

            for duplicate_index in duplicate_indexes:
                merged_keys.update(keys_by_index[duplicate_index])

                if duplicate_index != article_index:
                    unique_articles[duplicate_index] = None
                    keys_by_index[duplicate_index] = set()

            unique_articles[article_index] = article
            keys_by_index[article_index] = merged_keys

        for key in keys_by_index[article_index]:
            index_by_key[key] = article_index

    deduplicated_articles = [
        article
        for article in unique_articles
        if article is not None
    ]

    logger.debug(
        "%s article(s) conservé(s) sur %s après déduplication.",
        len(deduplicated_articles),
        len(articles)
    )

    return deduplicated_articles