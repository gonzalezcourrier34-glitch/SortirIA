"""Tests unitaires des politiques de préparation des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/preparation/test_article_preparation_profiles.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import UserDict
from typing import Any

import pytest

import src.article.preparation.article_preparation_profiles as module


# Politiques déclarées

def test_acquisition_validation_policy_has_expected_values() -> None:
    assert module.ACQUISITION_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_url": False,
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True
    }


def test_labeled_reference_validation_policy_has_expected_values() -> None:
    assert module.LABELED_REFERENCE_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": True,
        "require_image": False,
        "require_url": False,
        "require_label": True,
        "remove_deleted_content": True,
        "validate_urls": False
    }


def test_multimodal_reference_validation_policy_has_expected_values() -> None:
    assert module.MULTIMODAL_REFERENCE_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_url": False,
        "require_label": True,
        "remove_deleted_content": True,
        "validate_urls": False
    }


def test_validation_policies_references_expected_policies() -> None:
    assert module.VALIDATION_POLICIES == {
        "acquisition": module.ACQUISITION_VALIDATION_POLICY,
        "labeled_reference": module.LABELED_REFERENCE_VALIDATION_POLICY,
        "multimodal_reference": (
            module.MULTIMODAL_REFERENCE_VALIDATION_POLICY
        )
    }


def test_validation_policies_uses_original_policy_objects() -> None:
    assert (
        module.VALIDATION_POLICIES["acquisition"]
        is module.ACQUISITION_VALIDATION_POLICY
    )
    assert (
        module.VALIDATION_POLICIES["labeled_reference"]
        is module.LABELED_REFERENCE_VALIDATION_POLICY
    )
    assert (
        module.VALIDATION_POLICIES["multimodal_reference"]
        is module.MULTIMODAL_REFERENCE_VALIDATION_POLICY
    )


# Récupération des politiques

@pytest.mark.parametrize(
    ("policy_name", "expected"),
    [
        ("acquisition", module.ACQUISITION_VALIDATION_POLICY),
        (
            "labeled_reference",
            module.LABELED_REFERENCE_VALIDATION_POLICY
        ),
        (
            "multimodal_reference",
            module.MULTIMODAL_REFERENCE_VALIDATION_POLICY
        )
    ]
)
def test_get_validation_policy_returns_expected_policy(
    policy_name: module.PreparationPolicyName,
    expected: dict[str, Any]
) -> None:
    result = module.get_validation_policy(policy_name)

    assert result == expected


def test_get_validation_policy_uses_acquisition_by_default() -> None:
    result = module.get_validation_policy()

    assert result == module.ACQUISITION_VALIDATION_POLICY


def test_get_validation_policy_returns_copy() -> None:
    result = module.get_validation_policy("acquisition")

    assert result == module.ACQUISITION_VALIDATION_POLICY
    assert result is not module.ACQUISITION_VALIDATION_POLICY


def test_get_validation_policy_returns_new_copy_on_each_call() -> None:
    first_result = module.get_validation_policy("acquisition")
    second_result = module.get_validation_policy("acquisition")

    assert first_result == second_result
    assert first_result is not second_result


def test_modifying_returned_policy_does_not_modify_constant() -> None:
    result = module.get_validation_policy("acquisition")

    result["require_title"] = False
    result["custom_option"] = True

    assert module.ACQUISITION_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_url": False,
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True
    }
    assert "custom_option" not in module.ACQUISITION_VALIDATION_POLICY


# Surcharges

def test_get_validation_policy_applies_overrides() -> None:
    result = module.get_validation_policy(
        "acquisition",
        {
            "require_text": True,
            "require_image": True
        }
    )

    assert result == {
        "require_title": True,
        "require_text": True,
        "require_image": True,
        "require_url": False,
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True
    }


def test_get_validation_policy_accepts_new_override_keys() -> None:
    result = module.get_validation_policy(
        "acquisition",
        {
            "minimum_title_length": 10
        }
    )

    assert result["minimum_title_length"] == 10


def test_get_validation_policy_does_not_modify_overrides() -> None:
    overrides = {
        "require_text": True,
        "custom_option": "value"
    }
    original_overrides = dict(overrides)

    module.get_validation_policy(
        "acquisition",
        overrides
    )

    assert overrides == original_overrides


def test_get_validation_policy_accepts_custom_mapping() -> None:
    overrides = UserDict(
        {
            "require_url": True,
            "validate_urls": False
        }
    )

    result = module.get_validation_policy(
        "acquisition",
        overrides
    )

    assert result["require_url"] is True
    assert result["validate_urls"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        None,
        "overrides",
        123,
        [],
        (),
        True
    ]
)
def test_get_validation_policy_ignores_non_mapping_overrides(
    overrides: Any
) -> None:
    result = module.get_validation_policy(
        "acquisition",
        overrides
    )

    assert result == module.ACQUISITION_VALIDATION_POLICY


def test_get_validation_policy_applies_empty_mapping() -> None:
    result = module.get_validation_policy(
        "labeled_reference",
        {}
    )

    assert result == module.LABELED_REFERENCE_VALIDATION_POLICY
    assert result is not module.LABELED_REFERENCE_VALIDATION_POLICY


# Politique inconnue

def test_get_validation_policy_falls_back_to_acquisition_for_unknown_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    result = module.get_validation_policy(
        "unknown_policy"  # type: ignore[arg-type]
    )

    assert result == module.ACQUISITION_VALIDATION_POLICY
    assert result is not module.ACQUISITION_VALIDATION_POLICY


def test_unknown_policy_can_receive_overrides() -> None:
    result = module.get_validation_policy(
        "unknown_policy",  # type: ignore[arg-type]
        {
            "require_label": True
        }
    )

    assert result == {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_url": False,
        "require_label": True,
        "remove_deleted_content": True,
        "validate_urls": True
    }


# Isolation des politiques

@pytest.mark.parametrize(
    "policy_name",
    [
        "acquisition",
        "labeled_reference",
        "multimodal_reference"
    ]
)
def test_returned_policy_is_isolated_from_registered_policy(
    policy_name: module.PreparationPolicyName
) -> None:
    result = module.get_validation_policy(policy_name)

    result["require_title"] = not result["require_title"]

    assert (
        result["require_title"]
        != module.VALIDATION_POLICIES[policy_name]["require_title"]
    )