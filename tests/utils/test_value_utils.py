"""Tests des utilitaires de normalisation des valeurs textuelles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_value_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from src.utils.value_utils import (
    collapse_whitespace,
    is_missing_value,
    is_unsupported_text_collection,
    normalize_casefold,
    normalize_value,
    normalize_whitespace,
)

# Objets de test

class StringErrorValue:
    """Objet dont la conversion en chaîne échoue."""

    def __str__(self) -> str:
        raise ValueError("Conversion impossible")


class ComparisonErrorValue:
    """Objet dont la comparaison avec lui-même échoue."""

    def __ne__(self, other: object) -> bool:
        raise RuntimeError("Comparaison impossible")

    def __str__(self) -> str:
        return "valeur"


class NonBooleanComparison:
    """Objet représentant un résultat de comparaison non booléen."""

    def __bool__(self) -> bool:
        return True


class NonBooleanComparisonValue:
    """Objet retournant un résultat non booléen lors de la comparaison."""

    def __ne__(self, other: object) -> NonBooleanComparison:
        return NonBooleanComparison()

    def __str__(self) -> str:
        return "valeur"


class BoolConversionError:
    """Objet imitant un booléen externe dont la conversion échoue."""

    def __bool__(self) -> bool:
        raise ValueError("Conversion booléenne impossible")


BoolExternalError = type(
    "bool_",
    (),
    {
        "__bool__": lambda self: (_ for _ in ()).throw(
            ValueError("Conversion booléenne impossible")
        )
    }
)


class ExternalBooleanValue:
    """Objet dont la comparaison retourne un booléen externe."""

    def __ne__(self, other: object) -> Any:
        return BoolExternalError()

    def __str__(self) -> str:
        return "valeur"


NaTType = type("NaTType", (), {})
NAType = type("NAType", (), {})


# Collections complexes

@pytest.mark.parametrize(
    "value",
    [
        {},
        {"name": "CheckIt.AI"},
        [],
        ["article"],
        (),
        ("article",),
        set(),
        {"article"},
        frozenset({"article"})
    ]
)
def test_is_unsupported_text_collection_returns_true(
    value: Any
) -> None:
    """Les collections complexes ne doivent pas devenir du texte."""

    assert is_unsupported_text_collection(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "texte",
        b"texte",
        bytearray(b"texte"),
        memoryview(b"texte"),
        42,
        3.14,
        None
    ]
)
def test_is_unsupported_text_collection_returns_false(
    value: Any
) -> None:
    """Les valeurs simples et binaires restent convertibles."""

    assert is_unsupported_text_collection(value) is False


# Valeurs absentes

@pytest.mark.parametrize(
    "value",
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        math.nan,
        "none",
        " NONE ",
        "null",
        "NULL",
        "nan",
        "NaN",
        ""
    ]
)
def test_is_missing_value_detects_missing_values(
    value: Any
) -> None:
    """Les principales représentations d'une absence sont détectées."""

    assert is_missing_value(value) is True


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        False,
        True,
        "texte",
        "0",
        [],
        {},
        set()
    ]
)
def test_is_missing_value_rejects_present_values(
    value: Any
) -> None:
    """Les valeurs présentes ne doivent pas être considérées comme absentes."""

    assert is_missing_value(value) is False


def test_is_missing_value_detects_external_nat_type() -> None:
    """Un type externe NaT est considéré comme absent."""

    assert is_missing_value(NaTType()) is True


def test_is_missing_value_detects_external_na_type() -> None:
    """Un type externe NA est considéré comme absent."""

    assert is_missing_value(NAType()) is True


def test_is_missing_value_handles_comparison_error() -> None:
    """Une erreur de comparaison ne doit pas interrompre la détection."""

    assert is_missing_value(ComparisonErrorValue()) is False


def test_is_missing_value_ignores_non_boolean_comparison() -> None:
    """Un résultat de comparaison non booléen est ignoré."""

    assert is_missing_value(NonBooleanComparisonValue()) is False


def test_is_missing_value_handles_external_boolean_error() -> None:
    """Une erreur de conversion d'un booléen externe est ignorée."""

    assert is_missing_value(ExternalBooleanValue()) is False


def test_is_missing_value_handles_string_conversion_error() -> None:
    """Une erreur de conversion en chaîne retourne False."""

    assert is_missing_value(StringErrorValue()) is False


# Normalisation des valeurs

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" CheckIt.AI ", "CheckIt.AI"),
        (42, "42"),
        (3.14, "3.14"),
        (True, "True"),
        (False, "False"),
        (None, ""),
        (float("nan"), ""),
        (float("inf"), ""),
        (["article"], ""),
        ({"title": "Article"}, ""),
        ({"article"}, "")
    ]
)
def test_normalize_value_returns_expected_text(
    value: Any,
    expected: str
) -> None:
    """Une valeur simple est convertie en texte propre."""

    assert normalize_value(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (b" article ", "article"),
        (bytearray(b" article "), "article"),
        (memoryview(b" article "), "article"),
        (b"\xfftexte", "\ufffdtexte")
    ]
)
def test_normalize_value_decodes_binary_values(
    value: bytes | bytearray | memoryview,
    expected: str
) -> None:
    """Les valeurs binaires sont décodées en UTF-8."""

    assert normalize_value(value) == expected


def test_normalize_value_handles_string_conversion_error() -> None:
    """Une conversion en chaîne impossible retourne une chaîne vide."""

    assert normalize_value(StringErrorValue()) == ""


# Normalisation des espaces

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("texte", "texte"),
        ("  texte  ", "texte"),
        ("texte   avec   espaces", "texte avec espaces"),
        ("texte\navec\tplusieurs\r\nespaces", "texte avec plusieurs espaces"),
        ("   ", "")
    ]
)
def test_collapse_whitespace_returns_expected_text(
    text: str,
    expected: str
) -> None:
    """Les suites d'espaces sont remplacées par un espace simple."""

    assert collapse_whitespace(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Article   de presse  ", "Article de presse"),
        (42, "42"),
        (None, ""),
        (["article"], ""),
        (b"  article   teste  ", "article teste")
    ]
)
def test_normalize_whitespace_returns_expected_text(
    value: Any,
    expected: str
) -> None:
    """La valeur est convertie puis ses espaces sont normalisés."""

    assert normalize_whitespace(value) == expected


# Normalisation pour comparaison

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" CheckIt.AI ", "checkit.ai"),
        ("ARTICLE   DE PRESSE", "article de presse"),
        ("ÉTÉ", "été"),
        ("Straße", "strasse"),
        (None, ""),
        (42, "42")
    ]
)
def test_normalize_casefold_returns_comparable_text(
    value: Any,
    expected: str
) -> None:
    """La casse et les espaces sont normalisés pour les comparaisons."""

    assert normalize_casefold(value) == expected