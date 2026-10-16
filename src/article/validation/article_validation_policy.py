"""Construction des politiques de validation des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/validation/article_validation_policy.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config.settings import (
    MIN_TEXT_LENGTH,
    MIN_TITLE_LENGTH,
    MIN_TOTAL_TEXT_LENGTH
)
from src.utils.parsing_utils import parse_boolean, parse_non_negative_integer

# Politique par défaut

DEFAULT_VALIDATION_POLICY: dict[str, Any] = {
    "require_title": True,
    "require_text": False,
    "require_image": True,
    "remove_deleted_content": True,
    "validate_urls": True,
    "min_title_length": MIN_TITLE_LENGTH,
    "min_text_length": MIN_TEXT_LENGTH,
    "min_total_text_length": MIN_TOTAL_TEXT_LENGTH
}

BOOLEAN_VALIDATION_POLICY_FIELDS = (
    "require_title",
    "require_text",
    "require_image",
    "remove_deleted_content",
    "validate_urls"
)


# Construction

def build_validation_policy(
    policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Construit une politique de validation complète."""

    validation_policy = DEFAULT_VALIDATION_POLICY.copy()

    if isinstance(policy, Mapping):
        validation_policy.update(policy)

    integer_fields = {
        "min_title_length": MIN_TITLE_LENGTH,
        "min_text_length": MIN_TEXT_LENGTH,
        "min_total_text_length": MIN_TOTAL_TEXT_LENGTH
    }

    for field, default in integer_fields.items():
        validation_policy[field] = parse_non_negative_integer(
            validation_policy.get(field),
            default
        )

    for field in BOOLEAN_VALIDATION_POLICY_FIELDS:
        validation_policy[field] = parse_boolean(
            validation_policy.get(field),
            DEFAULT_VALIDATION_POLICY[field]
        )

    return validation_policy