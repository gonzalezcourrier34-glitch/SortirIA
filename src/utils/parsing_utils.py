"""Fonctions communes de conversion des valeurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/parsing_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import math
from typing import Any


# Valeurs booléennes reconnues

TRUE_VALUES: frozenset[str] = frozenset({
    "true",
    "1",
    "yes",
    "oui",
    "on"
})

FALSE_VALUES: frozenset[str] = frozenset({
    "false",
    "0",
    "no",
    "non",
    "off"
})


# Conversion en booléen

def parse_boolean(value: Any, default: bool = False) -> bool:
    """Convertit une valeur courante en booléen."""

    if isinstance(value, bool):
        return value

    if value is None:
        return default

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return default

        if math.isfinite(numeric_value) and numeric_value in {0.0, 1.0}:
            return bool(numeric_value)

        return default

    if isinstance(value, str):
        normalized_value = value.strip().casefold()

        if normalized_value in TRUE_VALUES:
            return True

        if normalized_value in FALSE_VALUES:
            return False

    return default


# Conversion en entier

def parse_optional_integer(value: Any) -> int | None:
    """Convertit une valeur facultative en entier positif ou nul."""

    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(numeric_value) or not numeric_value.is_integer():
        return None

    integer_value = int(numeric_value)
    return integer_value if integer_value >= 0 else None


def parse_non_negative_integer(value: Any, default: int = 0) -> int:
    """Convertit une valeur en entier positif ou nul."""

    parsed_value = parse_optional_integer(value)

    if parsed_value is not None:
        return parsed_value

    parsed_default = parse_optional_integer(default)
    return parsed_default if parsed_default is not None else 0


def parse_positive_integer(value: Any, default: int = 1) -> int:
    """Convertit une valeur en entier strictement positif."""

    parsed_value = parse_optional_integer(value)

    if parsed_value is not None and parsed_value > 0:
        return parsed_value

    parsed_default = parse_optional_integer(default)
    return parsed_default if parsed_default is not None and parsed_default > 0 else 1


# Conversion en nombre flottant

def parse_optional_float(value: Any) -> float | None:
    """Convertit une valeur facultative en nombre flottant fini."""

    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

    try:
        parsed_value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    return parsed_value if math.isfinite(parsed_value) else None


def parse_non_negative_float(
    value: Any,
    default: float = 0.0
) -> float:
    """Convertit une valeur en nombre flottant positif ou nul."""

    parsed_value = parse_optional_float(value)

    if parsed_value is not None and parsed_value >= 0:
        return parsed_value

    parsed_default = parse_optional_float(default)
    return parsed_default if parsed_default is not None and parsed_default >= 0 else 0.0


def parse_positive_float(
    value: Any,
    default: float = 1.0
) -> float:
    """Convertit une valeur en nombre flottant strictement positif."""

    parsed_value = parse_optional_float(value)

    if parsed_value is not None and parsed_value > 0:
        return parsed_value

    parsed_default = parse_optional_float(default)
    return parsed_default if parsed_default is not None and parsed_default > 0 else 1.0


# Normalisation des textes facultatifs

def normalize_optional_text(value: Any) -> str | None:
    """Retourne un texte nettoyé ou None."""

    if value is None:
        return None

    normalized_value = str(value).strip()
    return normalized_value or None