"""Transformations métier appliquées aux articles CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/processing/article_transformer.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config.constants import (
    DEFAULT_DATASET_ROLE,
    EMPTY_CATEGORY_VALUES,
    LANGUAGE_MAPPING
)
from src.article.schema.article_schema import DATASET_ROLES, SUPPORTED_LANGUAGES
from src.logger import get_logger
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)


# Normalisation des champs métier

def normalize_language(value: Any) -> str:
    """Normalise une langue vers un code pris en charge.

    La table `LANGUAGE_MAPPING` est consultée d'abord ; sinon on garde la
    partie avant le tiret (« fr-FR » donne « fr »).

    Parameters
    ----------
    value : Any
        La langue brute.

    Returns
    -------
    str
        Le code de langue s'il fait partie de `SUPPORTED_LANGUAGES`, sinon "".
    """

    language = normalize_value(value).lower()

    if not language:
        return ""

    normalized_language = LANGUAGE_MAPPING.get(
        language,
        language.split("-", maxsplit=1)[0]
    )

    return (
        normalized_language
        if normalized_language in SUPPORTED_LANGUAGES
        else ""
    )


def normalize_category(value: Any) -> str:
    """Normalise et déduplique une ou plusieurs catégories.

    Parameters
    ----------
    value : Any
        Une catégorie ou plusieurs séparées par des virgules.

    Returns
    -------
    str
        Les catégories en minuscules, sans doublons ni valeurs vides
        (`EMPTY_CATEGORY_VALUES`), jointes par « , ».
    """

    raw_category = normalize_value(value)

    if not raw_category:
        return ""

    categories = (
        category
        for item in raw_category.split(",")
        if (
            category := item.strip().lower()
        ) and category not in EMPTY_CATEGORY_VALUES
    )

    return ", ".join(dict.fromkeys(categories))


def normalize_dataset_role(value: Any) -> str:
    """Normalise le rôle métier porté par l'article.

    Parameters
    ----------
    value : Any
        Le rôle brut.

    Returns
    -------
    str
        Le rôle s'il fait partie de `DATASET_ROLES`, sinon `DEFAULT_DATASET_ROLE`.
    """

    role = normalize_value(value).lower()
    return role if role in DATASET_ROLES else DEFAULT_DATASET_ROLE


# Transformation des articles

def transform_article(article: Mapping[str, Any]) -> dict[str, Any]:
    """Applique les transformations métier à une copie d'article.

    Langue, catégorie et rôle sont normalisés ; `dataset_role` est fondu dans
    `role` puis retiré.

    Parameters
    ----------
    article : Mapping[str, Any]
        L'article à transformer.

    Returns
    -------
    dict[str, Any]
        Une copie transformée de l'article.

    Raises
    ------
    TypeError
        Si `article` n'est pas un Mapping.
    """

    if not isinstance(article, Mapping):
        raise TypeError("article doit être une structure de type Mapping.")

    transformed_article = dict(article)

    transformed_article["language"] = normalize_language(
        transformed_article.get("language")
    )
    transformed_article["category"] = normalize_category(
        transformed_article.get("category")
    )

    role = (
        transformed_article.get("role")
        or transformed_article.get("dataset_role")
    )

    transformed_article["role"] = normalize_dataset_role(role)
    transformed_article.pop("dataset_role", None)

    return transformed_article


def transform_articles(articles: list[Any]) -> list[dict[str, Any]]:
    """Transforme une collection sans supprimer d'article normalisé.

    Parameters
    ----------
    articles : list[Any]
        Les articles, qui doivent tous être des Mapping.

    Returns
    -------
    list[dict[str, Any]]
        Les articles transformés, dans le même ordre.

    Raises
    ------
    TypeError
        Si `articles` n'est pas une liste ou contient un élément qui n'est pas
        un Mapping.
    """

    # Vérifier l'entrée : une liste d'articles tous normalisés
    if not isinstance(articles, list):
        raise TypeError("articles doit être une liste.")

    if not articles:
        logger.info("Aucun article à transformer.")
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

    # Transformer chaque article et journaliser le bilan
    transformed_articles = [
        transform_article(article)
        for article in articles
    ]

    logger.info(
        "%s article(s) transformé(s) sur %s élément(s).",
        len(transformed_articles),
        len(articles)
    )

    return transformed_articles