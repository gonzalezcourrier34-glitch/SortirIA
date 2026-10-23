"""Utilitaires de normalisation des dates CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/date_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import UTC, datetime
from time import struct_time
from typing import Any

from dateutil import parser as date_parser

from src.article.processing.article_cleaner import normalize_value
from src.utils.value_utils import is_missing_value

YEAR_PATTERN = re.compile(r"^\d{4}$")
UNIX_TIMESTAMP_PATTERN = re.compile(r"^[+-]?\d{9,13}(?:\.\d+)?$")

MIN_DATETIME = datetime(1900, 1, 1, tzinfo=UTC)
MAX_DATETIME = datetime(2100, 12, 31, 23, 59, 59, tzinfo=UTC)

MIN_TIMESTAMP = MIN_DATETIME.timestamp()
MAX_TIMESTAMP = MAX_DATETIME.timestamp()


# Normalisation

def normalize_datetime(value: datetime) -> datetime:
    """Retourne une datetime UTC.

    Une datetime sans fuseau est considérée comme déjà exprimée en UTC.

    Parameters
    ----------
    value : datetime
        La date à normaliser, avec ou sans fuseau horaire.

    Returns
    -------
    datetime
        Le même instant, rattaché au fuseau UTC.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


# Parsing

def parse_year(value: str) -> datetime | None:
    """Convertit une année seule.

    Seules les années sur quatre chiffres entre 1900 et 2100 sont acceptées.

    Parameters
    ----------
    value : str
        La chaîne à lire, par exemple « 2024 ».

    Returns
    -------
    datetime | None
        Le 1er janvier de cette année à minuit UTC, ou None si la chaîne
        n'est pas une année valide.
    """

    if not YEAR_PATTERN.fullmatch(value):
        return None

    year = int(value)

    if not 1900 <= year <= 2100:
        return None

    return datetime(year, 1, 1, tzinfo=UTC)


def parse_timestamp(value: str) -> datetime | None:
    """Convertit un timestamp Unix.

    Les timestamps en millisecondes (valeur absolue au-delà de 10^11) sont
    ramenés en secondes ; seules les dates entre 1900 et 2100 sont gardées.

    Parameters
    ----------
    value : str
        La chaîne à lire, de 9 à 13 chiffres avec signe et décimales facultatifs.

    Returns
    -------
    datetime | None
        La date UTC correspondante, ou None si la valeur n'est pas un timestamp
        exploitable.
    """

    # Vérifier la forme, puis convertir en nombre fini

    if not UNIX_TIMESTAMP_PATTERN.fullmatch(value):
        return None

    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(timestamp):
        return None

    # Ramener les millisecondes en secondes et borner la plage admise
    if abs(timestamp) > 100_000_000_000:
        timestamp /= 1000

    if not MIN_TIMESTAMP <= timestamp <= MAX_TIMESTAMP:
        return None

    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    """Convertit une valeur en datetime UTC.

    Les formats sont essayés dans cet ordre : datetime, `struct_time`, année
    seule, ISO 8601, timestamp Unix, puis lecture libre par `dateutil`.

    Parameters
    ----------
    value : Any
        La valeur à convertir (datetime, `struct_time`, chaîne ou autre).

    Returns
    -------
    datetime | None
        La date en UTC, ou None si la valeur est absente ou illisible.
    """

    # Écarter les valeurs absentes et traiter les objets date déjà typés
    if is_missing_value(value):
        return None

    if isinstance(value, datetime):
        return normalize_datetime(value)

    if isinstance(value, struct_time):
        try:
            return datetime.fromtimestamp(
                calendar.timegm(value),
                tz=UTC,
            )
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    # Nettoyer la chaîne, puis essayer les formats stricts (année, ISO 8601)
    value = normalize_value(value)

    if not value:
        return None

    if (parsed := parse_year(value)) is not None:
        return parsed

    try:
        return normalize_datetime(
            datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        )
    except ValueError:
        pass

    # En dernier recours : timestamp Unix, puis lecture libre par dateutil
    if (parsed := parse_timestamp(value)) is not None:
        return parsed

    try:
        return normalize_datetime(date_parser.parse(value))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# Conversion

def convert_date_to_iso(value: Any) -> str:
    """Retourne une date ISO 8601 UTC ou une chaîne vide.

    Parameters
    ----------
    value : Any
        La valeur à convertir, lue par `parse_datetime`.

    Returns
    -------
    str
        La date au format ISO 8601 UTC, ou "" si elle est illisible.
    """

    return (
        parsed.isoformat()
        if (parsed := parse_datetime(value))
        else ""
    )


def convert_optional_date_to_iso(value: Any) -> str | None:
    """Retourne une date ISO 8601 UTC ou None.

    Parameters
    ----------
    value : Any
        La valeur à convertir, lue par `parse_datetime`.

    Returns
    -------
    str | None
        La date au format ISO 8601 UTC, ou None si elle est illisible.
    """

    return (
        parsed.isoformat()
        if (parsed := parse_datetime(value))
        else None
    )


# Dates courantes

def get_current_datetime() -> datetime:
    """Retourne la date actuelle en UTC.

    Returns
    -------
    datetime
        L'instant présent, avec le fuseau UTC.
    """

    return datetime.now(UTC)


def get_extraction_date() -> str:
    """Retourne la date actuelle au format ISO 8601 UTC.

    Sert d'horodatage d'extraction pour les données collectées.

    Returns
    -------
    str
        L'instant présent au format ISO 8601 UTC.
    """

    return get_current_datetime().isoformat()