"""Tests des utilitaires de normalisation des dates.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_date_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta, timezone
from time import struct_time
from typing import Any

import pytest

from src.utils.date_utils import (
    MAX_DATETIME,
    MAX_TIMESTAMP,
    MIN_DATETIME,
    MIN_TIMESTAMP,
    convert_date_to_iso,
    convert_optional_date_to_iso,
    get_current_datetime,
    get_extraction_date,
    normalize_datetime,
    parse_datetime,
    parse_timestamp,
    parse_year,
)

# Normalisation

def test_normalize_datetime_adds_utc_to_naive_datetime() -> None:
    """Une datetime sans fuseau doit être considérée comme UTC."""

    value = datetime(2026, 7, 27, 12, 30, 45)

    result = normalize_datetime(value)

    assert result == datetime(2026, 7, 27, 12, 30, 45, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_normalize_datetime_preserves_utc_datetime() -> None:
    """Une datetime déjà en UTC doit rester inchangée."""

    value = datetime(2026, 7, 27, 12, 30, 45, tzinfo=UTC)

    result = normalize_datetime(value)

    assert result == value
    assert result.tzinfo is UTC


def test_normalize_datetime_converts_timezone_to_utc() -> None:
    """Une datetime avec fuseau doit être convertie en UTC."""

    paris_timezone = timezone(timedelta(hours=2))
    value = datetime(
        2026,
        7,
        27,
        14,
        30,
        45,
        tzinfo=paris_timezone
    )

    result = normalize_datetime(value)

    assert result == datetime(
        2026,
        7,
        27,
        12,
        30,
        45,
        tzinfo=UTC
    )


# Années seules

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "1900",
            datetime(1900, 1, 1, tzinfo=UTC)
        ),
        (
            "2026",
            datetime(2026, 1, 1, tzinfo=UTC)
        ),
        (
            "2100",
            datetime(2100, 1, 1, tzinfo=UTC)
        )
    ]
)
def test_parse_year_returns_datetime(
    value: str,
    expected: datetime
) -> None:
    """Une année valide doit devenir le premier janvier UTC."""

    assert parse_year(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "1899",
        "2101",
        "999",
        "02026",
        "2026-01",
        "abcd",
        "+2026",
        "-2026"
    ]
)
def test_parse_year_returns_none_for_invalid_value(
    value: str
) -> None:
    """Une année invalide doit retourner None."""

    assert parse_year(value) is None


# Timestamps Unix

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "946684800",
            datetime(2000, 1, 1, tzinfo=UTC)
        ),
        (
            "946684800.5",
            datetime(2000, 1, 1, 0, 0, 0, 500000, tzinfo=UTC)
        ),
        (
            "1767225600",
            datetime(2026, 1, 1, tzinfo=UTC)
        )
    ]
)
def test_parse_timestamp_returns_datetime(
    value: str,
    expected: datetime
) -> None:
    """Un timestamp Unix valide doit être converti en UTC."""

    assert parse_timestamp(value) == expected


def test_parse_timestamp_accepts_milliseconds() -> None:
    """Un timestamp exprimé en millisecondes doit être converti."""

    result = parse_timestamp("1767225600000")

    assert result == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "12345678",
        "12345678901234",
        "timestamp",
        "nan",
        "inf",
        "+inf",
        "-inf",
        "1e9",
        "123.45"
    ]
)
def test_parse_timestamp_returns_none_for_invalid_format(
    value: str
) -> None:
    """Un format de timestamp invalide doit retourner None."""

    assert parse_timestamp(value) is None


def test_parse_timestamp_accepts_minimum_boundary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La borne minimale doit être transmise au convertisseur."""

    class FakeDatetime(datetime):
        @classmethod
        def fromtimestamp(
            cls,
            timestamp: float,
            tz: timezone | None = None
        ) -> datetime:
            assert timestamp == MIN_TIMESTAMP
            assert tz is UTC
            return MIN_DATETIME

    monkeypatch.setattr(
        "src.utils.date_utils.datetime",
        FakeDatetime
    )

    result = parse_timestamp(str(MIN_TIMESTAMP))

    assert result == MIN_DATETIME

def test_parse_timestamp_accepts_maximum_boundary() -> None:
    """La borne maximale autorisée doit être acceptée."""

    result = parse_timestamp(str(MAX_TIMESTAMP))

    assert result == MAX_DATETIME


def test_parse_timestamp_rejects_value_before_minimum() -> None:
    """Un timestamp antérieur à la borne minimale doit être refusé."""

    result = parse_timestamp(str(MIN_TIMESTAMP - 1))

    assert result is None


def test_parse_timestamp_rejects_value_after_maximum() -> None:
    """Un timestamp postérieur à la borne maximale doit être refusé."""

    result = parse_timestamp(str(MAX_TIMESTAMP + 1))

    assert result is None


def test_parse_timestamp_handles_fromtimestamp_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une erreur de conversion système doit retourner None."""

    class FakeDatetime(datetime):
        @classmethod
        def fromtimestamp(
            cls,
            timestamp: float,
            tz: timezone | None = None
        ) -> datetime:
            raise OSError("Erreur simulée")

    monkeypatch.setattr(
        "src.utils.date_utils.datetime",
        FakeDatetime
    )

    assert parse_timestamp("1767225600") is None


# Parsing général

@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        float("nan")
    ]
)
def test_parse_datetime_returns_none_for_missing_value(
    value: Any
) -> None:
    """Une valeur absente doit retourner None."""

    assert parse_datetime(value) is None


def test_parse_datetime_accepts_naive_datetime() -> None:
    """Une datetime naïve doit être normalisée en UTC."""

    value = datetime(2026, 7, 27, 12, 30)

    assert parse_datetime(value) == datetime(
        2026,
        7,
        27,
        12,
        30,
        tzinfo=UTC
    )


def test_parse_datetime_accepts_aware_datetime() -> None:
    """Une datetime avec fuseau doit être convertie en UTC."""

    value = datetime(
        2026,
        7,
        27,
        14,
        30,
        tzinfo=timezone(timedelta(hours=2))
    )

    assert parse_datetime(value) == datetime(
        2026,
        7,
        27,
        12,
        30,
        tzinfo=UTC
    )


def test_parse_datetime_accepts_struct_time() -> None:
    """Une structure time doit être interprétée en UTC."""

    value = struct_time((
        2026,
        7,
        27,
        12,
        30,
        45,
        0,
        208,
        0
    ))

    expected = datetime.fromtimestamp(
        calendar.timegm(value),
        tz=UTC
    )

    assert parse_datetime(value) == expected


def test_parse_datetime_handles_struct_time_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une structure time invalide doit retourner None."""

    monkeypatch.setattr(
        "src.utils.date_utils.calendar.timegm",
        lambda value: (_ for _ in ()).throw(ValueError("Erreur simulée"))
    )

    value = struct_time((
        2026,
        7,
        27,
        12,
        30,
        45,
        0,
        208,
        0
    ))

    assert parse_datetime(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2026",
            datetime(2026, 1, 1, tzinfo=UTC)
        ),
        (
            "2026-07-27",
            datetime(2026, 7, 27, tzinfo=UTC)
        ),
        (
            "2026-07-27T12:30:45",
            datetime(2026, 7, 27, 12, 30, 45, tzinfo=UTC)
        ),
        (
            "2026-07-27T12:30:45Z",
            datetime(2026, 7, 27, 12, 30, 45, tzinfo=UTC)
        ),
        (
            "2026-07-27Z",
            datetime(2026, 7, 27, tzinfo=UTC)
        ),
        (
            "2026-07-27T14:30:45+02:00",
            datetime(2026, 7, 27, 12, 30, 45, tzinfo=UTC)
        ),
        (
            "1767225600",
            datetime(2026, 1, 1, tzinfo=UTC)
        ),
        (
            "July 27, 2026 12:30",
            datetime(2026, 7, 27, 12, 30, tzinfo=UTC)
        )
    ]
)
def test_parse_datetime_accepts_supported_formats(
    value: str,
    expected: datetime
) -> None:
    """Les formats de dates courants doivent être pris en charge."""

    assert parse_datetime(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "date inconnue",
        "32/13/2026",
        "2026-99-99",
        "99999999999999",
        [],
        {},
        object()
    ]
)
def test_parse_datetime_returns_none_for_invalid_values(
    value: Any
) -> None:
    """Une valeur non convertible doit retourner None."""

    assert parse_datetime(value) is None


def test_parse_datetime_normalizes_numeric_value() -> None:
    """Une valeur numérique doit pouvoir être interprétée comme timestamp."""

    assert parse_datetime(1767225600) == datetime(
        2026,
        1,
        1,
        tzinfo=UTC
    )


def test_parse_datetime_prefers_year_before_timestamp() -> None:
    """Une chaîne de quatre chiffres doit être interprétée comme année."""

    assert parse_datetime("2026") == datetime(
        2026,
        1,
        1,
        tzinfo=UTC
    )


def test_parse_datetime_uses_dateutil_as_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le parseur dateutil doit être utilisé en dernier recours."""

    expected = datetime(2026, 7, 27, 12, 30, tzinfo=UTC)

    monkeypatch.setattr(
        "src.utils.date_utils.date_parser.parse",
        lambda value: expected
    )

    result = parse_datetime("date personnalisée")

    assert result == expected


def test_parse_datetime_handles_dateutil_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une erreur du parseur dateutil doit retourner None."""

    monkeypatch.setattr(
        "src.utils.date_utils.date_parser.parse",
        lambda value: (_ for _ in ()).throw(
            OverflowError("Erreur simulée")
        )
    )

    assert parse_datetime("date personnalisée") is None


# Conversion ISO

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 27, 12, 30, tzinfo=UTC),
            "2026-07-27T12:30:00+00:00"
        ),
        (
            "2026-07-27T12:30:00Z",
            "2026-07-27T12:30:00+00:00"
        ),
        (
            "2026",
            "2026-01-01T00:00:00+00:00"
        ),
        (
            1767225600,
            "2026-01-01T00:00:00+00:00"
        )
    ]
)
def test_convert_date_to_iso_returns_iso_string(
    value: Any,
    expected: str
) -> None:
    """Une date valide doit être convertie en chaîne ISO UTC."""

    assert convert_date_to_iso(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "date inconnue",
        [],
        {}
    ]
)
def test_convert_date_to_iso_returns_empty_string(
    value: Any
) -> None:
    """Une date invalide doit produire une chaîne vide."""

    assert convert_date_to_iso(value) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 27, 12, 30, tzinfo=UTC),
            "2026-07-27T12:30:00+00:00"
        ),
        (
            "2026-07-27",
            "2026-07-27T00:00:00+00:00"
        ),
        (
            None,
            None
        ),
        (
            "date invalide",
            None
        )
    ]
)
def test_convert_optional_date_to_iso(
    value: Any,
    expected: str | None
) -> None:
    """Une date facultative doit produire une chaîne ISO ou None."""

    assert convert_optional_date_to_iso(value) == expected


# Dates courantes

def test_get_current_datetime_returns_utc_datetime() -> None:
    """La date courante doit être associée au fuseau UTC."""

    result = get_current_datetime()

    assert isinstance(result, datetime)
    assert result.tzinfo is UTC


def test_get_current_datetime_returns_recent_value() -> None:
    """La date retournée doit correspondre à l'instant courant."""

    before = datetime.now(UTC)
    result = get_current_datetime()
    after = datetime.now(UTC)

    assert before <= result <= after


def test_get_extraction_date_uses_current_datetime(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La date d'extraction doit provenir de get_current_datetime."""

    current_datetime = datetime(
        2026,
        7,
        27,
        12,
        30,
        45,
        123456,
        tzinfo=UTC
    )

    monkeypatch.setattr(
        "src.utils.date_utils.get_current_datetime",
        lambda: current_datetime
    )

    assert (
        get_extraction_date()
        == "2026-07-27T12:30:45.123456+00:00"
    )