"""Normalisation des articles vers le schéma métier CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/schema/article_schema_normalizer.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.article.schema.article_schema import (
    ARTICLE_FIELD_ALIASES,
    ARTICLE_TEXT_FIELDS,
    STANDARD_ARTICLE_FIELDS
)
from src.logger import get_logger
from src.utils.path_utils import normalize_source_name

logger = get_logger(__name__)


# Normalisation des champs

def normalize_standard_value(field: str, value: Any) -> Any:
    """Normalise une valeur absente selon le type du champ.

    Parameters
    ----------
    field : str
        Nom du champ standard.
    value : Any
        Sa valeur brute.

    Returns
    -------
    Any
        Pour `metadata`, une copie du dictionnaire (ou {}) ; pour les autres
        champs, "" à la place de None, sinon la valeur telle quelle.
    """

    if field == "metadata":
        return dict(value) if isinstance(value, Mapping) else {}

    return "" if value is None else value


def apply_field_aliases(article: Mapping[str, Any]) -> dict[str, Any]:
    """Copie les alias vers leur champ standard sans écraser une valeur connue.

    Exemple : `link` remplit `url` si `url` est absent, None ou vide.

    Parameters
    ----------
    article : Mapping[str, Any]
        L'article brut.

    Returns
    -------
    dict[str, Any]
        Une copie de l'article, champs standards complétés (les alias restent).
    """

    normalized_input = dict(article)

    for alias, standard_field in ARTICLE_FIELD_ALIASES.items():
        alias_value = normalized_input.get(alias)
        standard_value = normalized_input.get(standard_field)

        if (
            standard_field not in normalized_input
            or standard_value is None
            or standard_value == ""
        ):
            normalized_input[standard_field] = alias_value

    return normalized_input


def get_raw_article_text(article: Mapping[str, Any]) -> Any:
    """Retourne le premier contenu textuel brut renseigné.

    Parameters
    ----------
    article : Mapping[str, Any]
        L'article à lire (`text`, puis `summary`, puis `description`).

    Returns
    -------
    Any
        La première valeur non nulle et non blanche (pas forcément une chaîne), ou "".
    """

    for field in ARTICLE_TEXT_FIELDS:
        value = article.get(field)

        if value is None:
            continue

        if isinstance(value, str):
            if value.strip():
                return value

            continue

        return value

    return ""


def normalize_source(value: Any) -> str:
    """Normalise le nom de source sans masquer une valeur absente.

    Parameters
    ----------
    value : Any
        Le nom de source brut.

    Returns
    -------
    str
        Le nom normalisé par `normalize_source_name`, ou "" si la valeur est
        absente ou impossible à convertir (avec un avertissement au journal).
    """

    if value is None:
        return ""

    try:
        source_value = str(value).strip()
    except (TypeError, ValueError, AttributeError) as error:
        logger.warning(
            "Nom de source impossible à convertir : %s",
            error
        )
        return ""

    if not source_value:
        return ""

    try:
        normalized_source = normalize_source_name(source_value)
    except (TypeError, ValueError, AttributeError) as error:
        logger.warning(
            "Nom de source impossible à normaliser : %s",
            error
        )
        return ""

    return normalized_source or ""


# Normalisation d'un article

def normalize_article_schema(article: Any) -> dict[str, Any]:
    """Retourne un article conforme au schéma et à son ordre de champs.

    Les champs hors schéma sont rangés dans `metadata`, sans écraser une
    métadonnée déjà présente.

    Parameters
    ----------
    article : Any
        L'article brut.

    Returns
    -------
    dict[str, Any]
        L'article avec exactement les champs de `STANDARD_ARTICLE_FIELDS`, ou {}
        si l'entrée n'est pas un Mapping.
    """

    if not isinstance(article, Mapping):
        return {}

    # Résoudre les alias et choisir le texte
    source_article = apply_field_aliases(article)
    source_article["text"] = get_raw_article_text(source_article)

    # Construire l'article dans l'ordre des champs standards
    normalized_article = {
        field: normalize_standard_value(
            field,
            source_article.get(field)
        )
        for field in STANDARD_ARTICLE_FIELDS
    }

    normalized_article["source"] = normalize_source(
        normalized_article["source"]
    )

    # Ranger les champs hors schéma dans les métadonnées
    metadata = dict(normalized_article["metadata"])

    for key, value in source_article.items():
        if (
            isinstance(key, str)
            and key not in STANDARD_ARTICLE_FIELDS
            and key not in ARTICLE_FIELD_ALIASES
        ):
            metadata.setdefault(key, value)

    normalized_article["metadata"] = metadata
    return normalized_article


# Normalisation d'une collection

def normalize_articles_schema(
    articles: list[Any]
) -> tuple[list[dict[str, Any]], int]:
    """Normalise une collection et retourne le nombre d'éléments ignorés.

    Un élément qui n'est pas un Mapping, ou dont la normalisation échoue, est
    ignoré et compté.

    Parameters
    ----------
    articles : list[Any]
        Les articles bruts.

    Returns
    -------
    tuple[list[dict[str, Any]], int]
        Les articles normalisés, et le nombre d'éléments ignorés ; `([], 0)` si
        l'entrée n'est pas une liste.
    """

    # Entrée invalide ou vide : rien à normaliser
    if not isinstance(articles, list):
        logger.warning(
            "Collection d'articles invalide : %s.",
            type(articles).__name__
        )
        return [], 0

    if not articles:
        return [], 0

    # Normaliser chaque article, en comptant ceux qu'on ignore
    normalized_articles: list[dict[str, Any]] = []
    ignored_count = 0

    for index, article in enumerate(articles):
        try:
            normalized_article = normalize_article_schema(article)
        except (
            TypeError,
            ValueError,
            AttributeError,
            RuntimeError
        ) as error:
            logger.warning(
                "Article %s impossible à normaliser : %s",
                index,
                error
            )
            ignored_count += 1
            continue

        if not normalized_article:
            ignored_count += 1
            continue

        normalized_articles.append(normalized_article)

    return normalized_articles, ignored_count