"""Tests unitaires de `src/event/event_validator.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.event.event_validator import (
    REASON_END_BEFORE_START,
    REASON_INVALID_URL,
    REASON_MISSING_PLACE,
    REASON_MISSING_START,
    REASON_MISSING_TITLE,
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
