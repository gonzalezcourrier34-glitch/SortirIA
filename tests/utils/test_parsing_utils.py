"""Tests des fonctions communes de conversion des valeurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_parsing_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.utils.parsing_utils import (
    normalize_optional_text,
    parse_boolean,
    parse_non_negative_float,
    parse_non_negative_integer,
    parse_optional_float,
    parse_optional_integer,
    parse_positive_float,
    parse_positive_integer,
)

# Conversion en booléen

@pytest.mark.parametrize(
    "value",
    [
        True,
        "true",
        "TRUE",
        " True ",
        "1",
        "yes",
        "YES",
        "oui",
        "OUI",
        "on",
        "ON",
        1,
        1.0
    ]
)
def test_parse_boolean_returns_true(value: Any) -> None:
    """Les représentations positives doivent retourner True."""

    assert parse_boolean(value) is True


@pytest.mark.parametrize(
    "value",
    [
        False,
        "false",
        "FALSE",
        " False ",
        "0",
        "no",
        "NO",
        "non",
        "NON",
        "off",
        "OFF",
        0,
        0.0
    ]
)
def test_parse_boolean_returns_false(value: Any) -> None:
    """Les représentations négatives doivent retourner False."""

    assert parse_boolean(value) is False


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "vrai",
        "faux",
        "2",
        2,
        -1,
        0.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        [],
        {},
        object()
    ]
)
def test_parse_boolean_returns_false_default_for_invalid_values(
    value: Any
) -> None:
    """Une valeur invalide doit utiliser la valeur par défaut."""

    assert parse_boolean(value) is False


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "inconnu",
        2,
        -1,
        0.5,
        float("nan"),
        [],
        {}
    ]
)
def test_parse_boolean_returns_true_custom_default(
    value: Any
) -> None:
    """Une valeur invalide doit respecter une valeur par défaut positive."""

    assert parse_boolean(value, default=True) is True


def test_parse_boolean_preserves_boolean_value() -> None:
    """Une valeur booléenne doit être retournée sans conversion."""

    assert parse_boolean(True, default=False) is True
    assert parse_boolean(False, default=True) is False


# Conversion facultative en entier

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (1, 1),
        (42, 42),
        (0.0, 0),
        (1.0, 1),
        (42.0, 42),
        ("0", 0),
        ("1", 1),
        ("42", 42),
        (" 42 ", 42),
        ("42.0", 42),
        ("1e2", 100)
    ]
)
def test_parse_optional_integer_returns_integer(
    value: Any,
    expected: int
) -> None:
    """Une valeur entière positive ou nulle doit être convertie."""

    assert parse_optional_integer(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "   ",
        "texte",
        "1.5",
        1.5,
        -1,
        -1.0,
        "-1",
        float("nan"),
        float("inf"),
        float("-inf"),
        [],
        {},
        object()
    ]
)
def test_parse_optional_integer_returns_none_for_invalid_values(
    value: Any
) -> None:
    """Une valeur invalide doit retourner None."""

    assert parse_optional_integer(value) is None


# Conversion en entier positif ou nul

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (1, 1),
        (42, 42),
        ("0", 0),
        ("42", 42),
        ("42.0", 42)
    ]
)
def test_parse_non_negative_integer_returns_parsed_value(
    value: Any,
    expected: int
) -> None:
    """Une valeur entière positive ou nulle doit être conservée."""

    assert parse_non_negative_integer(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "texte",
        -1,
        1.5,
        float("nan")
    ]
)
def test_parse_non_negative_integer_uses_default(
    value: Any
) -> None:
    """Une valeur invalide doit utiliser la valeur par défaut."""

    assert parse_non_negative_integer(value, default=8) == 8


@pytest.mark.parametrize(
    "default",
    [
        None,
        True,
        False,
        "",
        "texte",
        -1,
        1.5,
        float("nan")
    ]
)
def test_parse_non_negative_integer_uses_zero_for_invalid_default(
    default: Any
) -> None:
    """Une valeur et un défaut invalides doivent produire zéro."""

    assert parse_non_negative_integer("invalide", default=default) == 0


def test_parse_non_negative_integer_accepts_zero_default() -> None:
    """Zéro doit être accepté comme valeur par défaut."""

    assert parse_non_negative_integer(None, default=0) == 0


# Conversion en entier strictement positif

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (42, 42),
        (1.0, 1),
        ("1", 1),
        ("42", 42),
        ("42.0", 42)
    ]
)
def test_parse_positive_integer_returns_parsed_value(
    value: Any,
    expected: int
) -> None:
    """Une valeur entière strictement positive doit être conservée."""

    assert parse_positive_integer(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "texte",
        0,
        -1,
        1.5,
        float("nan")
    ]
)
def test_parse_positive_integer_uses_default(
    value: Any
) -> None:
    """Une valeur non positive ou invalide doit utiliser le défaut."""

    assert parse_positive_integer(value, default=8) == 8


@pytest.mark.parametrize(
    "default",
    [
        None,
        True,
        False,
        "",
        "texte",
        0,
        -1,
        1.5,
        float("nan")
    ]
)
def test_parse_positive_integer_uses_one_for_invalid_default(
    default: Any
) -> None:
    """Une valeur et un défaut invalides doivent produire un."""

    assert parse_positive_integer("invalide", default=default) == 1


# Conversion facultative en nombre flottant

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (-1, -1.0),
        (1.5, 1.5),
        ("0", 0.0),
        ("1", 1.0),
        ("-1", -1.0),
        ("1.5", 1.5),
        (" 1.5 ", 1.5),
        ("1e2", 100.0)
    ]
)
def test_parse_optional_float_returns_float(
    value: Any,
    expected: float
) -> None:
    """Une valeur numérique finie doit être convertie en flottant."""

    assert parse_optional_float(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "   ",
        "texte",
        float("nan"),
        float("inf"),
        float("-inf"),
        [],
        {},
        object()
    ]
)
def test_parse_optional_float_returns_none_for_invalid_values(
    value: Any
) -> None:
    """Une valeur invalide ou non finie doit retourner None."""

    assert parse_optional_float(value) is None


# Conversion en flottant positif ou nul

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (1.5, 1.5),
        ("0", 0.0),
        ("1.5", 1.5)
    ]
)
def test_parse_non_negative_float_returns_parsed_value(
    value: Any,
    expected: float
) -> None:
    """Une valeur flottante positive ou nulle doit être conservée."""

    assert parse_non_negative_float(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "texte",
        -1,
        -0.5,
        float("nan"),
        float("inf")
    ]
)
def test_parse_non_negative_float_uses_default(
    value: Any
) -> None:
    """Une valeur invalide ou négative doit utiliser le défaut."""

    assert parse_non_negative_float(value, default=2.5) == 2.5


@pytest.mark.parametrize(
    "default",
    [
        None,
        True,
        False,
        "",
        "texte",
        -1,
        float("nan"),
        float("inf")
    ]
)
def test_parse_non_negative_float_uses_zero_for_invalid_default(
    default: Any
) -> None:
    """Une valeur et un défaut invalides doivent produire zéro."""

    assert parse_non_negative_float("invalide", default=default) == 0.0


# Conversion en flottant strictement positif

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (1.5, 1.5),
        ("1", 1.0),
        ("1.5", 1.5),
        ("1e2", 100.0)
    ]
)
def test_parse_positive_float_returns_parsed_value(
    value: Any,
    expected: float
) -> None:
    """Une valeur flottante strictement positive doit être conservée."""

    assert parse_positive_float(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "",
        "texte",
        0,
        -1,
        -0.5,
        float("nan"),
        float("inf")
    ]
)
def test_parse_positive_float_uses_default(
    value: Any
) -> None:
    """Une valeur invalide ou non positive doit utiliser le défaut."""

    assert parse_positive_float(value, default=2.5) == 2.5


@pytest.mark.parametrize(
    "default",
    [
        None,
        True,
        False,
        "",
        "texte",
        0,
        -1,
        float("nan"),
        float("inf")
    ]
)
def test_parse_positive_float_uses_one_for_invalid_default(
    default: Any
) -> None:
    """Une valeur et un défaut invalides doivent produire un."""

    assert parse_positive_float("invalide", default=default) == 1.0


# Normalisation des textes facultatifs

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("texte", "texte"),
        (" texte ", "texte"),
        (42, "42"),
        (0, "0"),
        (True, "True"),
        (False, "False"),
        (1.5, "1.5")
    ]
)
def test_normalize_optional_text(
    value: Any,
    expected: str | None
) -> None:
    """Une valeur doit devenir un texte nettoyé ou None."""

    assert normalize_optional_text(value) == expected