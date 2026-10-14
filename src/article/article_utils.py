"""Fonctions utilitaires communes aux articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/article_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config.constants import (
    DEFAULT_ARTICLE_IDENTIFIER,
    DELETED_ARTICLE_VALUES
)
from src.article.schema.article_schema import (
    ARTICLE_IDENTIFIER_FIELDS,
    ARTICLE_TEXT_FIELDS,
    ARTICLE_URL_FIELDS
)
from src.utils.value_utils import (
    is_missing_value,
    normalize_whitespace
)


# Lecture des articles

def get_first_article_value(
    article: Mapping[str, Any],
    fields: tuple[str, ...]
) -> str:
    """Retourne la première valeur renseignée parmi plusieurs champs."""

    if not isinstance(article, Mapping):
        return ""

    for field in fields:
        if value := normalize_whitespace(article.get(field)):
            return value

    return ""


def get_article_identifier(article: Mapping[str, Any]) -> str:
    """Retourne un identifiant court utilisable dans les logs."""

    identifier = get_first_article_value(article, ARTICLE_IDENTIFIER_FIELDS)
    return identifier[:80] if identifier else DEFAULT_ARTICLE_IDENTIFIER


def get_article_text(article: Mapping[str, Any]) -> str:
    """Retourne le meilleur contenu textuel disponible."""

    return get_first_article_value(article, ARTICLE_TEXT_FIELDS)


def get_article_source(article: Mapping[str, Any]) -> str:
    """Retourne le nom normalisé de la source."""

    return (
        normalize_whitespace(article.get("source"))
        if isinstance(article, Mapping)
        else ""
    )


def get_article_url(article: Mapping[str, Any]) -> str:
    """Retourne l'URL principale de l'article."""

    return get_first_article_value(article, ARTICLE_URL_FIELDS)


def get_article_role(article: Mapping[str, Any]) -> str:
    """Retourne le rôle métier standard de l'article."""

    if not isinstance(article, Mapping):
        return ""

    role = article.get("role") or article.get("dataset_role")
    return normalize_whitespace(role).casefold()


# Vérification des valeurs

def has_image_reference(article: Mapping[str, Any]) -> bool:
    """Vérifie qu'une URL ou un chemin d'image est présent."""

    if not isinstance(article, Mapping):
        return False

    return bool(
        normalize_whitespace(article.get("image_url"))
        or normalize_whitespace(article.get("image_path"))
    )


def is_empty_value(value: Any) -> bool:
    """Vérifie si une valeur est absente ou vide."""

    if is_missing_value(value):
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return not value

    return False


def is_deleted_value(value: Any) -> bool:
    """Détecte les contenus explicitement marqués comme supprimés."""

    return normalize_whitespace(value).casefold() in DELETED_ARTICLE_VALUES