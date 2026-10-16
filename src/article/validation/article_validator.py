"""Validation métier centralisée des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/validation/article_validator.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from src.article.schema.article_schema import STANDARD_ARTICLE_FIELDS
from src.article.article_utils import (
    get_article_identifier,
    get_article_text,
    is_deleted_value,
    is_empty_value
)
from src.article.validation.article_validation_policy import build_validation_policy
from src.logger import get_logger
from src.utils.url_utils import is_valid_http_url
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)


# Validation des images

def has_validated_local_image(article: Mapping[str, Any]) -> bool:
    """Vérifie qu'une image locale a été validée par image_service."""

    if not isinstance(article, Mapping):
        return False

    image_path = article.get("image_path")

    return (
        isinstance(image_path, str)
        and bool(image_path.strip())
        and article.get("image_is_valid") is True
    )


# Validation d'un article

def validate_article_with_reason(
    article: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
    *,
    policy_is_built: bool = False
) -> tuple[bool, str]:
    """Valide un article et retourne un motif de rejet stable."""

    if not isinstance(article, Mapping):
        return False, "format_article_invalide"

    validation_policy = (
        dict(policy)
        if policy_is_built and isinstance(policy, Mapping)
        else build_validation_policy(policy)
    )

    if any(field not in article for field in STANDARD_ARTICLE_FIELDS):
        return False, "schema_incomplet"

    if is_empty_value(article.get("id")):
        return False, "identifiant_absent"

    if is_empty_value(article.get("source")):
        return False, "source_absente"

    title = normalize_value(article.get("title"))
    article_text = normalize_value(get_article_text(article))
    author = normalize_value(article.get("author"))

    if validation_policy["remove_deleted_content"]:
        deleted_fields = (
            ("titre_supprime", title),
            ("texte_supprime", article_text),
            ("auteur_supprime", author)
        )

        for reason, value in deleted_fields:
            if value and is_deleted_value(value):
                return False, reason

    if validation_policy["require_title"] and not title:
        return False, "titre_absent"

    if title and len(title) < validation_policy["min_title_length"]:
        return False, "titre_trop_court"

    if validation_policy["require_text"]:
        if not article_text:
            return False, "texte_absent"

        if len(article_text) < validation_policy["min_text_length"]:
            return False, "texte_trop_court"

        total_text_length = len(title) + len(article_text)

        if total_text_length < validation_policy["min_total_text_length"]:
            return False, "contenu_textuel_trop_court"

    if validation_policy["validate_urls"]:
        article_url = article.get("url")
        image_url = article.get("image_url")

        if (
            not is_empty_value(article_url)
            and not is_valid_http_url(article_url)
        ):
            return False, "url_invalide"

        if (
            not is_empty_value(image_url)
            and not is_valid_http_url(image_url)
        ):
            return False, "url_image_invalide"

    if (
        validation_policy["require_image"]
        and not has_validated_local_image(article)
    ):
        return False, "image_absente_ou_invalide"

    return True, ""


# Validation d'une collection

def validate_articles(
    articles: list[Any],
    policy: Mapping[str, Any] | None = None,
    *,
    require_image: bool | None = None
) -> list[dict[str, Any]]:
    """Conserve uniquement les articles valides d'une collection."""

    if not isinstance(articles, list):
        raise TypeError("articles doit être une liste.")

    if not articles:
        logger.info("Aucun article à valider.")
        return []

    validation_policy = build_validation_policy(policy)

    if require_image is not None:
        validation_policy["require_image"] = bool(require_image)

    valid_articles: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()

    for article in articles:
        is_valid, reason = validate_article_with_reason(
            article,
            validation_policy,
            policy_is_built=True
        )

        if is_valid:
            valid_articles.append(dict(article))
            continue

        rejection_reason = reason or "raison_inconnue"
        rejection_counts[rejection_reason] += 1

        logger.warning(
            "Article ignoré [%s] : %s.",
            get_article_identifier(article),
            rejection_reason
        )

    logger.info(
        "%s article(s) valide(s) sur %s.",
        len(valid_articles),
        len(articles)
    )

    if rejection_counts:
        logger.info("Motifs de rejet : %s.", dict(rejection_counts))

    return valid_articles