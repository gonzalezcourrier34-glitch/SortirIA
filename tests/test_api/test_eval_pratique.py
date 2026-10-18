"""Les recherches « faire soi-même » (config/eval_pratique.yaml) sont comprises (17/10/2026).

Sans base : compréhension de chaque recherche et règle « type exclu ». La
pertinence se mesure sur la vraie base :
    uv run python -m scripts.eval_recherche --fichier config/eval_pratique.yaml
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.search_service import PARIS, SearchFilters, plan_search
from scripts.eval_recherche import card_errors, understanding_errors

PRACTICE_FILE = Path(__file__).resolve().parents[2] / "config" / "eval_pratique.yaml"
QUESTIONS = yaml.safe_load(PRACTICE_FILE.read_text(encoding="utf-8"))["questions"]
NOW = datetime(2026, 10, 15, 18, tzinfo=PARIS)


def test_practice_set_has_questions_and_controls() -> None:
    questions = [item["question"].lower() for item in QUESTIONS]
    assert len(questions) == len(set(questions)) == 25
    assert sum(1 for item in QUESTIONS if item["compris"].get("pratique") is None) == 10     # témoins et « regarder »
    assert sum(1 for item in QUESTIONS if item["compris"].get("regarder")) == 5      # dont « une pièce de théâtre »


@pytest.mark.parametrize("item", QUESTIONS, ids=[item["question"] for item in QUESTIONS])
def test_each_practice_search_is_understood(item: dict) -> None:
    plan, _ = plan_search(item["question"], SearchFilters(), NOW)
    assert understanding_errors(plan, item["compris"]) == []


def test_a_concert_is_not_a_place_to_sing_unless_you_take_part() -> None:
    plan, _ = plan_search("je veux chanter", SearchFilters(), NOW)
    card = {"title": "Concert de chants corses", "category": "concert", "summary": "", "place": "",
            "start_at": NOW.isoformat(), "end_at": None, "audiences": [], "price": "12 €"}
    words, excluded = ["karaoke", "chant"], ["concert", "spectacle"]
    assert card_errors(card, plan, {}, words, excluded) == ["type exclu"]
    assert card_errors({**card, "title": "Stage de chant polyphonique"}, plan, {}, words, excluded) == []
    assert card_errors({**card, "title": "Karaoké géant", "category": "soiree"}, plan, {}, words, excluded) == []


def test_a_ball_is_not_a_dance_show() -> None:
    plan, _ = plan_search("un spectacle de danse", SearchFilters(), NOW)
    assert plan.watch and plan.categories == ("danse",) and "spectacle" not in plan.keywords
    card = {"title": "Bal folk", "category": "danse", "category_label": "Bal", "summary": "", "place": "",
            "start_at": NOW.isoformat(), "end_at": None, "audiences": [], "price": "5 €"}
    assert card_errors(card, plan, {"pas_pratique": True}, []) == ["pratique au lieu de spectacle"]
    assert card_errors({**card, "title": "Ballet de l'Opéra", "category_label": "Danse"}, plan, {"pas_pratique": True}, []) == []


def test_a_play_is_watched_and_acting_is_practised() -> None:
    watched, _ = plan_search("une pièce de théâtre", SearchFilters(), NOW)
    assert watched.watch and watched.practice is None and "piece" not in watched.keywords
    acting, _ = plan_search("jouer au théâtre", SearchFilters(), NOW)
    assert acting.practice == "theatre" and not acting.watch


def test_without_the_practice_rule_singing_is_not_understood(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesure « avant » (--sans-pratique) : la règle retirée, la pratique n'est plus comprise."""
    from app import query_understanding

    monkeypatch.setattr(query_understanding, "PRACTICE_VERBS", {})
    plan, _ = plan_search("je veux chanter", SearchFilters(), NOW)
    assert understanding_errors(plan, {"pratique": "chanter"}) == ["pratique None au lieu de chanter"]
