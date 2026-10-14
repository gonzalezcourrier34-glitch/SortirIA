"""Tests du statut, de l'âge, de l'accessibilité et des publics OpenAgenda."""

from __future__ import annotations

import pytest

from src.event.event_validator import (
    REASON_CANCELLED,
    REASON_NOT_AN_OUTING,
    REASON_ONLINE_ONLY,
    validate_event,
)
from src.event.openagenda_mapper import (
    get_accessibility,
    get_age_range,
    get_attendance,
    get_status,
    infer_audiences,
    map_openagenda_event,
)

from .conftest import make_openagenda_item

FUTURE_EVENING = "2030-10-24T18:30:00.000Z"   # 20 h 30, heure de Paris
FUTURE_NIGHT = "2030-10-24T21:30:00.000Z"     # 23 h 30, heure de Paris


@pytest.mark.parametrize(
    ("code", "expected"),
    [(1, "programme"), (4, "reporte"), (5, "complet"), (6, "annule"), (None, "programme"), ("x", "programme")],
)
def test_status_codes(code, expected) -> None:
    assert get_status({"status": code}) == expected


def test_attendance_codes() -> None:
    assert get_attendance({"attendanceMode": 2}) == "en_ligne"
    assert get_attendance({"attendanceMode": 3}) == "mixte"
    assert get_attendance({}) == "sur_place"


def test_age_range() -> None:
    assert get_age_range({"age": {"min": 3, "max": 10}}) == (3, 10)
    assert get_age_range({"age": None}) == (None, None)
    assert get_age_range({"age": {"min": 12, "max": 3}}) == (None, None)  # incohérent


def test_accessibility_list_or_dict() -> None:
    assert get_accessibility({"accessibility": ["mi", "vi", "xx"]}) == ["moteur", "visuel"]
    assert get_accessibility({"accessibility": {"hi": True, "mi": False}}) == ["auditif"]
    assert get_accessibility({}) == []


@pytest.mark.parametrize(
    ("texts", "start", "age_min", "age_max", "expected"),
    [
        (["Spectacle jeune public"], FUTURE_EVENING, None, None, ["famille"]),
        (["Atelier peinture"], FUTURE_EVENING, 4, 10, ["famille"]),
        (["Soirée DJ électro"], FUTURE_EVENING, None, None, ["soiree"]),
        (["Concert"], FUTURE_NIGHT, None, None, ["soiree"]),
        (["Balade contée en famille"], FUTURE_EVENING, None, None, ["famille"]),  # « balade » n'est pas « bal »
        (["Soirée enfants interdite"], FUTURE_EVENING, 18, None, ["soiree"]),     # 18 ans et plus : jamais famille
        (["Exposition"], FUTURE_EVENING, None, None, []),
    ],
)
def test_infer_audiences(texts, start, age_min, age_max, expected) -> None:
    assert infer_audiences(texts, start, True, age_min, age_max) == expected


def test_day_only_event_is_not_a_night_out() -> None:
    assert infer_audiences(["Marché"], "2030-10-24T00:00:00+02:00", False, None, None) == []


def test_mapper_fills_new_fields() -> None:
    item = make_openagenda_item(
        title={"fr": "Spectacle de marionnettes"},
        timings=[{"start": FUTURE_EVENING, "end": "2030-10-24T19:30:00.000Z"}],
        status=5, attendanceMode=1, age={"min": 3, "max": 8}, accessibility=["mi"],
    )
    event = map_openagenda_event(item)
    assert event is not None
    assert (event.status, event.attendance, event.age_min, event.age_max) == ("complet", "sur_place", 3, 8)
    assert event.audiences == ["famille"]
    assert event.accessibility == ["moteur"]
    assert event.is_sponsored is False and event.partner_id is None


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"status": 6}, REASON_CANCELLED),
    ({"status": 4}, REASON_CANCELLED),
    ({"status": 3}, REASON_ONLINE_ONLY),
    ({"attendanceMode": 2}, REASON_ONLINE_ONLY),
])
def test_cancelled_or_online_events_are_rejected(overrides, reason) -> None:
    item = make_openagenda_item(timings=[{"start": FUTURE_EVENING, "end": "2030-10-24T20:00:00.000Z"}], **overrides)
    event = map_openagenda_event(item)
    assert validate_event(event) == (False, reason)


def test_full_or_mixed_events_stay_valid() -> None:
    item = make_openagenda_item(timings=[{"start": FUTURE_EVENING, "end": "2030-10-24T20:00:00.000Z"}],
                                status=5, attendanceMode=3)
    assert validate_event(map_openagenda_event(item)) == (True, "")


def test_job_offers_are_not_outings() -> None:
    item = make_openagenda_item(
        timings=[{"start": FUTURE_EVENING, "end": "2030-10-24T20:00:00.000Z"}],
        tags=[{"label": "Opportunité d'emploi"}],
    )
    assert validate_event(map_openagenda_event(item)) == (False, REASON_NOT_AN_OUTING)


def test_word_inside_another_word_is_not_matched() -> None:
    # « emploi » ne doit pas être reconnu dans « emploiement » ou un autre mot.
    item = make_openagenda_item(
        timings=[{"start": FUTURE_EVENING, "end": "2030-10-24T20:00:00.000Z"}],
        tags=[{"label": "Exposition"}],
    )
    assert validate_event(map_openagenda_event(item)) == (True, "")
