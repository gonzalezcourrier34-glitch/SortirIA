"""Politiques de préparation métier des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/preparation/article_preparation_profiles.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

PreparationPolicyName = Literal[
    "acquisition",
    "labeled_reference",
    "multimodal_reference"
]

ACQUISITION_VALIDATION_POLICY: dict[str, Any] = {
    "require_title": True,
    "require_text": False,
    "require_image": False,
    "require_url": False,
    "require_label": False,
    "remove_deleted_content": True,
    "validate_urls": True
}

LABELED_REFERENCE_VALIDATION_POLICY: dict[str, Any] = {
    "require_title": True,
    "require_text": True,
    "require_image": False,
    "require_url": False,
    "require_label": True,
    "remove_deleted_content": True,
    "validate_urls": False
}

MULTIMODAL_REFERENCE_VALIDATION_POLICY: dict[str, Any] = {
    "require_title": True,
    "require_text": False,
    "require_image": False,
    "require_url": False,
    "require_label": True,
    "remove_deleted_content": True,
    "validate_urls": False
}

VALIDATION_POLICIES: dict[PreparationPolicyName, dict[str, Any]] = {
    "acquisition": ACQUISITION_VALIDATION_POLICY,
    "labeled_reference": LABELED_REFERENCE_VALIDATION_POLICY,
    "multimodal_reference": MULTIMODAL_REFERENCE_VALIDATION_POLICY
}


def get_validation_policy(
    policy_name: PreparationPolicyName = "acquisition",
    overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Construit la politique de validation utilisée par le service."""

    validation_policy = VALIDATION_POLICIES.get(
        policy_name,
        ACQUISITION_VALIDATION_POLICY
    ).copy()

    if isinstance(overrides, Mapping):
        validation_policy.update(overrides)

    return validation_policy
