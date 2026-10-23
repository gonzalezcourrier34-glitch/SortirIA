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
    """Convertit une valeur courante en booléen.

    Sont reconnus les booléens, les nombres 0 et 1, et les chaînes de
    `TRUE_VALUES` / `FALSE_VALUES` (« oui », « off »...), sans tenir compte de
    la casse ni des espaces autour.

    Parameters
    ----------
    value : Any
        La valeur à convertir.
    default : bool
        Valeur renvoyée si la valeur est absente ou non reconnue. Par défaut False.

    Returns
    -------
    bool
        Le booléen lu, ou `default`.
    """

    # Booléen déjà typé ou valeur absente
    if isinstance(value, bool):
        return value

    if value is None:
        return default

    # Nombre : seuls 0 et 1 sont acceptés
    if isinstance(value, (int, float)):
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return default

        if math.isfinite(numeric_value) and numeric_value in {0.0, 1.0}:
            return bool(numeric_value)

        return default

    # Chaîne : comparer aux listes de valeurs vraies et fausses
    if isinstance(value, str):
        normalized_value = value.strip().casefold()

        if normalized_value in TRUE_VALUES:
            return True

        if normalized_value in FALSE_VALUES:
            return False

    return default


# Conversion en entier

def parse_optional_integer(value: Any) -> int | None:
    """Convertit une valeur facultative en entier positif ou nul.

    Les booléens sont refusés ; « 3.0 » est accepté, « 3.5 » ne l'est pas.

    Parameters
    ----------
    value : Any
        La valeur à convertir (nombre ou chaîne).

    Returns
    -------
    int | None
        L'entier positif ou nul, ou None si la valeur est absente, non entière
        ou négative.
    """

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
    """Convertit une valeur en entier positif ou nul.

    Parameters
    ----------
    value : Any
        La valeur à convertir.
    default : int
        Valeur de repli si `value` est illisible ; elle-même invalide, c'est 0.
        Par défaut 0.

    Returns
    -------
    int
        L'entier lu, sinon `default`, sinon 0.
    """

    parsed_value = parse_optional_integer(value)

    if parsed_value is not None:
        return parsed_value

    parsed_default = parse_optional_integer(default)
    return parsed_default if parsed_default is not None else 0


def parse_positive_integer(value: Any, default: int = 1) -> int:
    """Convertit une valeur en entier strictement positif.

    Parameters
    ----------
    value : Any
        La valeur à convertir.
    default : int
        Valeur de repli si `value` est illisible ou nulle ; elle-même invalide,
        c'est 1. Par défaut 1.

    Returns
    -------
    int
        L'entier lu, sinon `default`, sinon 1.
    """

    parsed_value = parse_optional_integer(value)

    if parsed_value is not None and parsed_value > 0:
        return parsed_value

    parsed_default = parse_optional_integer(default)
    return parsed_default if parsed_default is not None and parsed_default > 0 else 1


# Conversion en nombre flottant

def parse_optional_float(value: Any) -> float | None:
    """Convertit une valeur facultative en nombre flottant fini.

    Parameters
    ----------
    value : Any
        La valeur à convertir ; les booléens sont refusés.

    Returns
    -------
    float | None
        Le nombre lu, ou None s'il est absent, illisible, infini ou NaN.
    """

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
    """Convertit une valeur en nombre flottant positif ou nul.

    Parameters
    ----------
    value : Any
        La valeur à convertir.
    default : float
        Valeur de repli si `value` est illisible ou négative ; elle-même
        invalide, c'est 0.0. Par défaut 0.0.

    Returns
    -------
    float
        Le nombre lu, sinon `default`, sinon 0.0.
    """

    parsed_value = parse_optional_float(value)

    if parsed_value is not None and parsed_value >= 0:
        return parsed_value

    parsed_default = parse_optional_float(default)
    return parsed_default if parsed_default is not None and parsed_default >= 0 else 0.0


def parse_positive_float(
    value: Any,
    default: float = 1.0
) -> float:
    """Convertit une valeur en nombre flottant strictement positif.

    Parameters
    ----------
    value : Any
        La valeur à convertir.
    default : float
        Valeur de repli si `value` est illisible, nulle ou négative ; elle-même
        invalide, c'est 1.0. Par défaut 1.0.

    Returns
    -------
    float
        Le nombre lu, sinon `default`, sinon 1.0.
    """

    parsed_value = parse_optional_float(value)

    if parsed_value is not None and parsed_value > 0:
        return parsed_value

    parsed_default = parse_optional_float(default)
    return parsed_default if parsed_default is not None and parsed_default > 0 else 1.0


# Normalisation des textes facultatifs

def normalize_optional_text(value: Any) -> str | None:
    """Retourne un texte nettoyé ou None.

    Parameters
    ----------
    value : Any
        La valeur à convertir en texte.

    Returns
    -------
    str | None
        Le texte sans espaces autour, ou None s'il est absent ou vide.
    """

    if value is None:
        return None

    normalized_value = str(value).strip()
    return normalized_value or None