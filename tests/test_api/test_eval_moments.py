"""Le moment de la journée (config/eval_moments.yaml) est compris (18/10/2026).

Sans base : compréhension et contrôle de l'heure. La pertinence se mesure sur
la vraie base : uv run python -m scripts.eval_recherche --fichier config/eval_moments.yaml
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.search_filters import PARIS, SearchFilters
from app.search_service import plan_search
from scripts.eval_recherche import card_errors, understanding_errors

MOMENTS_FILE = Path(__file__).resolve().parents[2] / "config" / "eval_moments.yaml"
QUESTIONS = yaml.safe_load(MOMENTS_FILE.read_text(encoding="utf-8"))["questions"]
NOW = datetime(2026, 10, 15, 18, tzinfo=PARIS)


@pytest.mark.parametrize("item", QUESTIONS, ids=[item["question"] for item in QUESTIONS])
def test_each_moment_search_is_understood(item: dict) -> None:
    plan, _ = plan_search(item["question"], SearchFilters(), NOW)
    assert understanding_errors(plan, item["compris"]) == []


def test_the_start_hour_is_checked_when_shown() -> None:
    plan, _ = plan_search("un concert le matin", SearchFilters(), NOW)
    card = {"title": "Concert", "category": "concert", "summary": "", "place": "", "start_at": NOW.isoformat(),
            "end_at": None, "audiences": [], "price": "10 €", "date": "Sam. 17 oct. · 20:30"}
    assert card_errors(card, plan, {"moment": "matin"}, []) == ["heure"]
    assert card_errors({**card, "date": "Sam. 17 oct. · 10:00"}, plan, {"moment": "matin"}, []) == []
    assert card_errors({**card, "date": "Sam. 17 oct."}, plan, {"moment": "matin"}, []) == []   # heure inconnue
    assert card_errors(card, plan, {"soir": True}, []) == []
