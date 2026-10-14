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
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH,
        "allowed_labels": []
    }


def test_boolean_validation_policy_fields_has_expected_values() -> None:
    assert module.BOOLEAN_VALIDATION_POLICY_FIELDS == (
        "require_title",
        "require_text",
        "require_image",
        "require_label",
        "remove_deleted_content",
        "validate_urls"
    )


def test_boolean_policy_fields_exist_in_default_policy() -> None:
    assert set(module.BOOLEAN_VALIDATION_POLICY_FIELDS).issubset(
        module.DEFAULT_VALIDATION_POLICY
    )


# Labels autorisés

@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        (),
        set()
    ]
)
def test_normalize_allowed_labels_returns_empty_set_for_empty_value(
    value: Any
) -> None:
    assert module.normalize_allowed_labels(value) == set()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fake", {"fake"}),
        ("real", {"real"}),
        ("false", {"false"}),
        ("true", {"true"}),
        (0, {"real"}),
        (1, {"fake"}),
        (0.0, {"0.0"}),
        (1.0, {"1.0"})
    ]
)
def test_normalize_allowed_labels_accepts_scalar_values(
    value: Any,
    expected: set[str]
) -> None:
    assert module.normalize_allowed_labels(value) == expected
    

def test_normalize_allowed_labels_normalizes_iterable_values() -> None:
    result = module.normalize_allowed_labels(
        [
            "fake",
            "REAL",
            "false",
            "true",
            "",
            None,
            "unknown"
        ]
    )

    assert result == {
        "fake",
        "real",
        "false",
        "true",
        "unknown"
    }
    

def test_normalize_allowed_labels_removes_duplicates() -> None:
    result = module.normalize_allowed_labels(
        [
            "fake",
            "FAKE",
            "false",
            1
        ]
    )

    assert result == {
        "fake",
        "false"
    }

def test_normalize_allowed_labels_accepts_generator() -> None:
    values = (
        value
        for value in [
            "fake",
            "real"
        ]
    )

    result = module.normalize_allowed_labels(values)

    assert result == {
        "fake",
        "real"
    }


def test_normalize_allowed_labels_accepts_tuple() -> None:
    result = module.normalize_allowed_labels(
        (
            "fake",
            "real"
        )
    )

    assert result == {
        "fake",
        "real"
    }


def test_normalize_allowed_labels_accepts_set() -> None:
    result = module.normalize_allowed_labels(
        {
            "fake",
            "real"
        }
    )

    assert result == {
        "fake",
        "real"
    }


@pytest.mark.parametrize(
    "error_type",
    [
        TypeError,
        ValueError
    ]
)
def test_normalize_allowed_labels_returns_empty_for_non_iterable_value(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception]
) -> None:
    class InvalidLabels:
        def __iter__(self):
            raise error_type("erreur simulée")

    assert module.normalize_allowed_labels(InvalidLabels()) == set()


def test_normalize_allowed_labels_calls_normalize_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_normalize_label(value: Any) -> str:
        calls.append(value)
        return f"normalized-{value}" if value is not None else ""

    monkeypatch.setattr(
        module,
        "normalize_label",
        fake_normalize_label
    )

    result = module.normalize_allowed_labels(
        [
            "fake",
            "real",
            None
        ]
    )

    assert calls == [
        "fake",
        "real",
        None
    ]
    assert result == {
        "normalized-fake",
        "normalized-real"
    }


def test_normalize_allowed_labels_ignores_empty_normalized_labels(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: "" if value == "invalid" else str(value)
    )

    result = module.normalize_allowed_labels(
        [
            "fake",
            "invalid"
        ]
    )

    assert result == {
        "fake"
    }


# Construction de la politique

def test_build_validation_policy_returns_complete_default_policy() -> None:
    result = module.build_validation_policy()

    assert result == {
        "require_title": True,
        "require_text": False,
        "require_image": True,
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH,
        "allowed_labels": set()
    }


def test_build_validation_policy_returns_new_dictionary() -> None:
    result = module.build_validation_policy()

    assert result is not module.DEFAULT_VALIDATION_POLICY


def test_build_validation_policy_does_not_modify_default_policy() -> None:
    result = module.build_validation_policy(
        {
            "require_title": False,
            "min_title_length": 99,
            "allowed_labels": ["fake"]
        }
    )

    result["require_text"] = True

    assert module.DEFAULT_VALIDATION_POLICY == {
        "require_title": True,
        "require_text": False,
        "require_image": True,
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": module.MIN_TITLE_LENGTH,
        "min_text_length": module.MIN_TEXT_LENGTH,
        "min_total_text_length": module.MIN_TOTAL_TEXT_LENGTH,
        "allowed_labels": []
    }


def test_build_validation_policy_applies_overrides() -> None:
    result = module.build_validation_policy(
        {
            "require_title": False,
            "require_text": True,
            "require_image": False,
            "require_label": True,
            "remove_deleted_content": False,
            "validate_urls": False,
            "min_title_length": 15,
            "min_text_length": 25,
            "min_total_text_length": 50,
            "allowed_labels": [
                "fake",
                "real"
            ]
        }
    )

    assert result == {
        "require_title": False,
        "require_text": True,
        "require_image": False,
        "require_label": True,
        "remove_deleted_content": False,
        "validate_urls": False,
        "min_title_length": 15,
        "min_text_length": 25,
        "min_total_text_length": 50,
        "allowed_labels": {
            "fake",
            "real"
        }
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
        "allowed_labels": [
            "fake",
            "real"
        ]
    }
    expected = {
        "require_title": False,
        "min_title_length": 20,
        "allowed_labels": [
            "fake",
            "real"
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
            "require_label": 1,
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
            1,
            False
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
    assert result["require_label"] is True
    assert result["remove_deleted_content"] is False
    assert result["validate_urls"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("require_title", "false"),
        ("require_text", "true"),
        ("require_image", 0),
        ("require_label", 1),
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


# Normalisation des labels dans la politique

def test_build_validation_policy_normalizes_allowed_labels(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    def fake_normalize_allowed_labels(value: Any) -> set[str]:
        captured.append(value)
        return {
            "normalized"
        }

    monkeypatch.setattr(
        module,
        "normalize_allowed_labels",
        fake_normalize_allowed_labels
    )

    result = module.build_validation_policy(
        {
            "allowed_labels": [
                "fake",
                "real"
            ]
        }
    )

    assert captured == [
        [
            "fake",
            "real"
        ]
    ]
    assert result["allowed_labels"] == {
        "normalized"
    }


def test_build_validation_policy_converts_default_allowed_labels_to_set() -> None:
    result = module.build_validation_policy()

    assert result["allowed_labels"] == set()
    assert isinstance(result["allowed_labels"], set)