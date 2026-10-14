"""
Utilitaires génériques de normalisation des valeurs textuelles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/value_utils.py` (commit 93632c5).

Ce module centralise :

- la détection des valeurs absentes ;
- la conversion sûre des valeurs simples en texte ;
- la normalisation des espaces ;
- la normalisation des textes destinés aux comparaisons.

Il ne réalise aucune conversion vers des types numériques ou booléens,
aucune validation métier et aucun nettoyage linguistique.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence, Set
from typing import Any

from config.constants import MISSING_TEXT_VALUES


# Types non convertibles directement en texte

UNSUPPORTED_TEXT_COLLECTIONS = (
    Mapping,
    Sequence,
    Set
)

BYTE_TYPES = (
    bytes,
    bytearray,
    memoryview
)


# Types externes représentant une valeur absente

MISSING_TYPE_NAMES: frozenset[str] = frozenset({
    "nattype",
    "natype"
})

BOOLEAN_TYPE_NAMES: frozenset[str] = frozenset({
    "bool_",
    "bool8"
})


# Expressions régulières

MULTIPLE_WHITESPACE_PATTERN = re.compile(r"\s+")


# Valeurs textuelles absentes normalisées

NORMALIZED_MISSING_TEXT_VALUES: frozenset[str] = frozenset(
    str(value).strip().casefold()
    for value in MISSING_TEXT_VALUES
)


# Détection des valeurs complexes

def is_unsupported_text_collection(value: Any) -> bool:
    """Indique si une collection complexe ne doit pas devenir du texte."""

    return (
        isinstance(value, UNSUPPORTED_TEXT_COLLECTIONS)
        and not isinstance(value, (str, *BYTE_TYPES))
    )


# Détection des valeurs absentes

def _is_non_finite_number(value: Any) -> bool:
    """Détecte les nombres flottants non finis."""

    if not isinstance(value, float):
        return False

    return not math.isfinite(value)


def _is_self_unequal(value: Any) -> bool:
    """Détecte les valeurs de type NaN qui ne sont pas égales à elles-mêmes."""

    try:
        comparison_result = value != value
    except (TypeError, ValueError, RuntimeError):
        return False

    if isinstance(comparison_result, bool):
        return comparison_result

    if type(comparison_result).__name__.casefold() not in BOOLEAN_TYPE_NAMES:
        return False

    try:
        return bool(comparison_result)
    except (TypeError, ValueError, RuntimeError):
        return False


def _has_missing_type(value: Any) -> bool:
    """Détecte certains types externes représentant une donnée absente."""

    return type(value).__name__.casefold() in MISSING_TYPE_NAMES


def is_missing_value(value: Any) -> bool:
    """Détecte les principales représentations d'une donnée absente."""

    if value is None:
        return True

    if is_unsupported_text_collection(value):
        return False

    if _is_non_finite_number(value):
        return True

    if _is_self_unequal(value):
        return True

    if _has_missing_type(value):
        return True

    try:
        normalized_value = str(value).strip().casefold()
    except (TypeError, ValueError, RuntimeError):
        return False

    return normalized_value in NORMALIZED_MISSING_TEXT_VALUES


# Conversion des valeurs simples

def _decode_bytes(value: bytes | bytearray | memoryview) -> str:
    """Décode une valeur binaire en UTF-8 sans lever d'exception."""

    try:
        return bytes(value).decode("utf-8", errors="replace").strip()
    except (TypeError, ValueError, UnicodeError):
        return ""


def normalize_value(value: Any) -> str:
    """Convertit une valeur simple en chaîne propre."""

    if is_missing_value(value):
        return ""

    if is_unsupported_text_collection(value):
        return ""

    if isinstance(value, BYTE_TYPES):
        return _decode_bytes(value)

    try:
        return str(value).strip()
    except (TypeError, ValueError, RuntimeError):
        return ""


# Normalisation des espaces

def collapse_whitespace(text: str) -> str:
    """Réduit toutes les suites d'espaces à un espace simple."""

    if not text:
        return ""

    return MULTIPLE_WHITESPACE_PATTERN.sub(" ", text).strip()


def normalize_whitespace(value: Any) -> str:
    """Convertit une valeur en texte puis normalise ses espaces."""

    return collapse_whitespace(normalize_value(value))


# Normalisation pour les comparaisons

def normalize_casefold(value: Any) -> str:
    """Normalise une valeur textuelle pour une comparaison insensible à la casse."""

    return normalize_whitespace(value).casefold()