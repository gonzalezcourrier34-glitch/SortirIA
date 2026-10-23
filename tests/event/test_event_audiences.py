"""Tests unitaires de `src/event/event_audiences.py` (publics déduits par règles)."""

from __future__ import annotations

import pytest

from src.event.event_audiences import infer_audiences

FUTURE_EVENING = "2030-10-24T18:30:00.000Z"   # 20 h 30, heure de Paris
FUTURE_NIGHT = "2030-10-24T21:30:00.000Z"     # 23 h 30, heure de Paris


@pytest.mark.parametrize(
    ("texts", "start", "age_min", "age_max", "expected"),
    [
        (["Spectacle jeune public"], FUTURE_EVENING, None, None, ["famille"]),
        (["Atelier peinture"], FUTURE_EVENING, 4, 10, ["famille"]),
        (["Soirée DJ électro"], FUTURE_EVENING, None, None, ["soiree"]),
        (["Concert"], FUTURE_NIGHT, None, None, ["soiree"]),
        (["Concert"], "2030-10-25T00:30:00.000Z", None, None, ["soiree"]),  # 2 h 30 à Paris
        (["Balade contée en famille"], FUTURE_EVENING, None, None, ["famille"]),  # « balade » n'est pas « bal »
        (["Soirée enfants interdite"], FUTURE_EVENING, 18, None, ["soiree"]),     # 18 ans et plus : jamais famille
        (["Exposition"], FUTURE_EVENING, None, None, []),
    ],
)
def test_infer_audiences(texts, start, age_min, age_max, expected) -> None:
    assert infer_audiences(texts, start, True, age_min, age_max) == expected


def test_day_only_event_is_not_a_night_out() -> None:
    assert infer_audiences(["Marché"], "2030-10-24T00:00:00+02:00", False, None, None) == []
