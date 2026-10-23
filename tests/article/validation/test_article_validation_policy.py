"""Tests unitaires du constructeur des politiques de validation.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/validation/test_article_validation_policy.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import UserDict
from typing import Any

import pytest

from src.article.validation import article_validation_policy as module

# Politique par défaut

def test_default_validation_policy_has_expected_values() -> None:
    assert module.DEFAULT_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": False,
        "require_image": True,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH
    }


def test_boolean_validation_policy_fields_has_expected_values() -> None:
    assert module.BOOLEAN_VALIDATION_POLICY_FIELDS == (
        "require_title",
        "require_text",
        "require_image",
        "remove_deleted_content",
        "validate_urls"
    )


def test_boolean_policy_fields_exist_in_default_policy() -> None:
    assert set(module.BOOLEAN_VALIDATION_POLICY_FIELDS).issubset(
        module.DEFAULT_VALIDATION_POLICY
    )


# Construction de la politique

def test_build_validation_policy_returns_complete_default_policy() -> None:
    result = module.build_validation_policy()

    assert result == {
        "require_title": True,
        "require_text": False,
        "require_image": True,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH
    }


def test_build_validation_policy_returns_new_dictionary() -> None:
    result = module.build_validation_policy()

    assert result is not module.DEFAULT_VALIDATION_POLICY


def test_build_validation_policy_does_not_modify_default_policy() -> None:
    result = module.build_validation_policy(
        {
            "require_title": False,
            "min_title_length": 99
        }
    )

    result["require_text"] = True

    assert module.DEFAULT_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": False,
        "require_image": True,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH
    }


def test_build_validation_policy_applies_overrides() -> None:
    result = module.build_validation_policy(
        {
            "require_title": False,
            "require_text": True,
            "require_image": False,
            "remove_deleted_content": False,
            "validate_urls": False,
            "min_title_length": 15,
            "min_text_length": 25,
            "min_total_text_length": 50
        }
    )

    assert result == {
        "require_title": False,
        "require_text": True,
        "require_image": False,
        "remove_deleted_content": False,
        "validate_urls": False,
        "min_title_length": 15,
        "min_text_length": 25,
        "min_total_text_length": 50
    }


def test_build_validation_policy_preserves_unknown_fields() -> None:
    result = module.build_validation_policy(
        {
            "custom_option": "value"
        }
    )

    assert result["custom_option"] == "value"


def test_build_validation_policy_accepts_custom_mapping() -> None:
    policy = UserDict(
        {
            "require_text": True,
            "min_text_length": 42
        }
    )

    result = module.build_validation_policy(policy)

    assert result["require_text"] is True
    assert result["min_text_length"] == 42


@pytest.mark.parametrize(
    "policy",
    [
        None,
        "policy",
        123,
        [],
        (),
        True
    ]
)
def test_build_validation_policy_ignores_non_mapping_policy(
    policy: Any
) -> None:
    result = module.build_validation_policy(policy)

    assert result == module.build_validation_policy()


def test_build_validation_policy_does_not_modify_input_policy() -> None:
    policy = {
        "require_title": False,
        "min_title_length": 20,
        "custom_values": [
            "premier",
            "second"
        ]
    }
    expected = {
        "require_title": False,
        "min_title_length": 20,
        "custom_values": [
            "premier",
            "second"
        ]
    }

    module.build_validation_policy(policy)

    assert policy == expected


# Champs numériques

def test_build_validation_policy_normalizes_integer_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, int]] = []

    def fake_parse_non_negative_integer(
        value: Any,
        default: int
    ) -> int:
        calls.append((value, default))
        return 123

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        fake_parse_non_negative_integer
    )

    result = module.build_validation_policy(
        {
            "min_title_length": "10",
            "min_text_length": None,
            "min_total_text_length": -1
        }
    )

    assert calls == [
        (
            "10",
            module.MIN_TITLE_LENGTH
        ),
        (
            None,
            module.MIN_TEXT_LENGTH
        ),
        (
            -1,
            module.MIN_TOTAL_TEXT_LENGTH
        )
    ]
    assert result["min_title_length"] == 123
    assert result["min_text_length"] == 123
    assert result["min_total_text_length"] == 123


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_title_length", "12"),
        ("min_text_length", 25.0),
        ("min_total_text_length", True),
        ("min_title_length", -5),
        ("min_text_length", None),
        ("min_total_text_length", "invalid")
    ]
)
def test_build_validation_policy_uses_integer_parser(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any
) -> None:
    captured: list[tuple[Any, int]] = []

    def fake_parse_non_negative_integer(
        raw_value: Any,
        default: int
    ) -> int:
        captured.append((raw_value, default))
        return 77

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        fake_parse_non_negative_integer
    )

    result = module.build_validation_policy(
        {
            field: value
        }
    )

    expected_defaults = {
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH
    }

    assert (
        value,
        expected_defaults[field]
    ) in captured
    assert result[field] == 77


# Champs booléens

def test_build_validation_policy_normalizes_boolean_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, bool]] = []

    def fake_parse_boolean(
        value: Any,
        default: bool
    ) -> bool:
        calls.append((value, default))
        return not default

    monkeypatch.setattr(
        module,
        "parse_boolean",
        fake_parse_boolean
    )

    result = module.build_validation_policy(
        {
            "require_title": "false",
            "require_text": "true",
            "require_image": 0,
            "remove_deleted_content": None,
            "validate_urls": "invalid"
        }
    )

    assert calls == [
        (
            "false",
            True
        ),
        (
            "true",
            False
        ),
        (
            0,
            True
        ),
        (
            None,
            True
        ),
        (
            "invalid",
            True
        )
    ]

    assert result["require_title"] is False
    assert result["require_text"] is True
    assert result["require_image"] is False
    assert result["remove_deleted_content"] is False
    assert result["validate_urls"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("require_title", "false"),
        ("require_text", "true"),
        ("require_image", 0),
        ("remove_deleted_content", None),
        ("validate_urls", "yes")
    ]
)
def test_build_validation_policy_uses_boolean_parser(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any
) -> None:
    captured: list[tuple[Any, bool]] = []

    def fake_parse_boolean(
        raw_value: Any,
        default: bool
    ) -> bool:
        captured.append((raw_value, default))
        return False

    monkeypatch.setattr(
        module,
        "parse_boolean",
        fake_parse_boolean
    )

    result = module.build_validation_policy(
        {
            field: value
        }
    )

    assert (
        value,
        module.DEFAULT_VALIDATION_POLICY[field]
    ) in captured
    assert result[field] is False