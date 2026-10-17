"""Tests unitaires de `src/event/event_validator.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.event.event_validator import (
    REASON_CANCELLED,
    REASON_END_BEFORE_START,
    REASON_INVALID_URL,
    REASON_MISSING_PLACE,
    REASON_MISSING_START,
    REASON_MISSING_TITLE,
    REASON_NOT_AN_OUTING,
    REASON_PAST,
    is_past,
    validate_event,
)
from src.event.openagenda_mapper import map_openagenda_event


@pytest.fixture
def event(openagenda_item):
    return map_openagenda_event(openagenda_item)


def test_valid_event(event, now) -> None:
    assert validate_event(event, now) == (True, "")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"title": " "}, REASON_MISSING_TITLE),
        ({"start_at": ""}, REASON_MISSING_START),
        ({"end_at": "2026-10-23T10:00:00+02:00"}, REASON_END_BEFORE_START),
        ({"city": "", "venue_name": "", "address": ""}, REASON_MISSING_PLACE),
        ({"url": "pas-une-url"}, REASON_INVALID_URL),
        # Annulation écrite dans le titre, statut resté « programmé » (15/10/2026).
        ({"title": "ANNULÉ - MONTE-CRISTO, LE SPECTACLE MUSICAL"}, REASON_CANCELLED),
        ({"title": "Annulation : concert de jazz"}, REASON_CANCELLED),
        ({"title": "AIDE AUX DEVOIRS"}, REASON_NOT_AN_OUTING),
    ],
)
def test_invalid_events(event, now, changes, reason) -> None:
    assert validate_event(replace(event, **changes), now) == (False, reason)


def test_finished_event_is_past(event, now) -> None:
    old = replace(event, start_at="2026-10-01T20:00:00+02:00", end_at="2026-10-01T22:00:00+02:00")
    assert is_past(old, now)
    assert validate_event(old, now) == (False, REASON_PAST)


def test_day_only_event_stays_visible_all_day(event, now) -> None:
    today = replace(event, start_at="2026-10-20T00:00:00+02:00", end_at="", time_known=False)
    assert not is_past(today, now)  # « maintenant » = 20/10 à midi


# --- Mise en conformité avec les contraintes de la base (15/10/2026) --------
def test_conform_to_schema_fixes_what_would_break_the_night(event) -> None:
    from src.event.event_validator import conform_to_schema

    broken = replace(event, age_min=13, age_max=12, audiences=["famille", "enfants", "soiree"],
                     status="peut-etre", attendance="?")
    fixed = conform_to_schema(broken)
    assert (fixed.age_min, fixed.age_max) == (None, None)        # âges incohérents oubliés, événement gardé
    assert fixed.audiences == ["famille", "soiree"]               # public inconnu retiré
    assert (fixed.status, fixed.attendance) == ("programme", "sur_place")
    kept = conform_to_schema(replace(event, age_min=6, age_max=150))
    assert (kept.age_min, kept.age_max) == (6, None)              # 150 ans : oublié, 6 ans gardé
    # 17/10/2026 : « de 32 à 29 € » (DATAtourisme) avait arrêté la nuit.
    prices = conform_to_schema(replace(event, price_min=32.0, price_max=29.0))
    assert (prices.price_min, prices.price_max) == (29.0, 32.0)
    ends = conform_to_schema(replace(event, start_at="2026-10-24T20:00:00+02:00", end_at="2026-10-24T18:00:00+02:00"))
    assert ends.end_at == "" and ends.start_at == "2026-10-24T20:00:00+02:00"


def test_pipeline_conforms_events_before_validating(openagenda_item, now) -> None:
    from src.event.event_pipeline import prepare_events
    from src.event.openagenda_mapper import map_openagenda_event

    event = replace(map_openagenda_event(openagenda_item), age_min=13, age_max=12)
    events, report = prepare_events([{"metadata": {"event": event.to_dict()}}], now)
    assert len(events) == 1 and report.events_rejected == 0
    assert (events[0].age_min, events[0].age_max) == (None, None)


def test_cancelled_word_inside_a_title_is_not_a_cancellation(event, now) -> None:
    assert validate_event(replace(event, title="Le bal des annulés : théâtre"), now) == (True, "")
