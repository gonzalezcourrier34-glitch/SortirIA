"""Tests unitaires de `src/event/event_normalizer.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

import pytest

from src.event.event_normalizer import (
    build_dedup_key,
    build_event_id,
    clean_text,
    has_time,
    normalize_city,
    normalize_for_comparison,
    parse_event_datetime,
    parse_price,
    pick_localized,
)


def test_clean_text_removes_html_and_entities() -> None:
    assert clean_text("<p>Rock &amp;  <b>roll</b></p>") == "Rock & roll"


def test_pick_localized_prefers_requested_language() -> None:
    assert pick_localized({"en": "Concert", "fr": "Concert FR"}) == "Concert FR"
    assert pick_localized({"en": "Only english"}) == "Only english"
    assert pick_localized("simple") == "simple"
    assert pick_localized({}) == ""


def test_normalize_for_comparison_ignores_accents_and_punctuation() -> None:
    assert normalize_for_comparison("Théâtre Jean-Vilar !") == "theatre jean vilar"


def test_normalize_city() -> None:
    assert normalize_city("MONTPELLIER") == "Montpellier"
    assert normalize_city("castelnau-le-lez") == "Castelnau-le-Lez"
    assert normalize_city("SAINT-JEAN-DE-VÉDAS") == "Saint-Jean-de-Védas"
    assert normalize_city("Palavas-les-Flots") == "Palavas-les-Flots"   # casse mixte gardée
    assert normalize_city("LA GRANDE-MOTTE") == "La Grande-Motte"
    assert normalize_city(None) == ""


def test_parse_event_datetime_converts_utc_to_paris() -> None:
    parsed = parse_event_datetime("2026-10-24T18:30:00.000Z")
    assert parsed is not None
    assert parsed.isoformat() == "2026-10-24T20:30:00+02:00"


def test_parse_event_datetime_handles_day_only_and_garbage() -> None:
    day = parse_event_datetime("2026-10-24")
    assert day is not None and day.hour == 0
    assert not has_time("2026-10-24")
    assert has_time("2026-10-24T20:00:00")
    assert parse_event_datetime("pas une date") is None
    assert parse_event_datetime("") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Gratuit", (0.0, 0.0, True)),
        ("Entrée libre", (0.0, 0.0, True)),
        ("12 €", (12.0, 12.0, False)),
        ("De 8 à 15 euros", (8.0, 15.0, False)),
        ("Tarif plein 12,50€ / réduit 8€", (8.0, 12.5, False)),
        ("€5", (5.0, 5.0, False)),
        ("Sur réservation", (None, None, None)),
        ("", (None, None, None)),
    ],
)
def test_parse_price(text: str, expected: tuple) -> None:
    assert parse_price(text) == expected


def test_dedup_key_is_identical_for_the_same_event_written_differently() -> None:
    first = build_dedup_key("Concert Jazz au Jam", "2026-10-24T20:30:00+02:00", "Le Jam")
    second = build_dedup_key("concert jazz au JAM !", "2026-10-24T20:30:00+02:00", "le jam")
    assert first == second
    assert build_event_id(first) == build_event_id(second)
    assert len(build_event_id(first)) == 16


def test_dedup_key_separates_two_sessions_the_same_day() -> None:
    """Deux séances d'un film à 14 h et 20 h 30 au même cinéma sont deux événements (15/10/2026)."""

    afternoon = build_dedup_key("Le Film", "2026-10-20T14:00:00+02:00", "Le Ciné")
    evening = build_dedup_key("Le Film", "2026-10-20T20:30:00+02:00", "Le Ciné")
    assert afternoon == "le film|2026-10-20T14:00|le cine" and evening.endswith("T20:30|le cine")
    # Sans heure connue (minuit), la clé reste celle du jour.
    assert build_dedup_key("Le Film", "2026-10-20", "Le Ciné") == "le film|2026-10-20|le cine"


def test_dedup_key_differs_on_another_day() -> None:
    assert build_dedup_key("Concert", "2026-10-24", "Jam") != build_dedup_key("Concert", "2026-10-25", "Jam")
