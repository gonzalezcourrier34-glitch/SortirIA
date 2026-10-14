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
    """Retourne une datetime UTC."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


# Parsing

def parse_year(value: str) -> datetime | None:
    """Convertit une année seule."""

    if not YEAR_PATTERN.fullmatch(value):
        return None

    year = int(value)

    if not 1900 <= year <= 2100:
        return None

    return datetime(year, 1, 1, tzinfo=UTC)


def parse_timestamp(value: str) -> datetime | None:
    """Convertit un timestamp Unix."""

    if not UNIX_TIMESTAMP_PATTERN.fullmatch(value):
        return None

    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(timestamp):
        return None

    if abs(timestamp) > 100_000_000_000:
        timestamp /= 1000

    if not MIN_TIMESTAMP <= timestamp <= MAX_TIMESTAMP:
        return None

    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    """Convertit une valeur en datetime UTC."""

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

    if (parsed := parse_timestamp(value)) is not None:
        return parsed

    try:
        return normalize_datetime(date_parser.parse(value))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# Conversion

def convert_date_to_iso(value: Any) -> str:
    """Retourne une date ISO 8601 UTC ou une chaîne vide."""

    return (
        parsed.isoformat()
        if (parsed := parse_datetime(value))
        else ""
    )


def convert_optional_date_to_iso(value: Any) -> str | None:
    """Retourne une date ISO 8601 UTC ou None."""

    return (
        parsed.isoformat()
        if (parsed := parse_datetime(value))
        else None
    )


# Dates courantes

def get_current_datetime() -> datetime:
    """Retourne la date actuelle en UTC."""

    return datetime.now(UTC)


def get_extraction_date() -> str:
    """Retourne la date actuelle au format ISO 8601 UTC."""

    return get_current_datetime().isoformat()