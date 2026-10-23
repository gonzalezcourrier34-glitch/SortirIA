"""Les 125 recherches courantes (config/eval_mots.yaml) sont comprises (15/10/2026).

Ce test tourne sans base : il vérifie la compréhension de chaque recherche
(type, public, quand, gratuit…). La pertinence des propositions se mesure sur
la vraie base : uv run python -m scripts.eval_recherche --fichier config/eval_mots.yaml
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.search_filters import PARIS, SearchFilters
from app.search_service import plan_search
from scripts.eval_recherche import card_errors, understanding_errors

WORDS_FILE = Path(__file__).resolve().parents[2] / "config" / "eval_mots.yaml"
QUESTIONS = yaml.safe_load(WORDS_FILE.read_text(encoding="utf-8"))["questions"]
NOW = datetime(2026, 10, 15, 18, tzinfo=PARIS)


def test_distinct_common_searches() -> None:
    questions = [item["question"].lower() for item in QUESTIONS]
    assert len(questions) == 125 and len(set(questions)) == 125


@pytest.mark.parametrize("item", QUESTIONS, ids=[item["question"] for item in QUESTIONS])
def test_each_common_search_is_understood(item: dict) -> None:
    plan, _ = plan_search(item["question"], SearchFilters(), NOW)
    assert understanding_errors(plan, item.get("compris") or {}) == []
    # Rien n'est ignoré : la recherche comprend un type, un mot à chercher ou une contrainte.
    understood = (plan.categories or plan.keywords or plan.public or plan.free_only or plan.max_price
                  or plan.setting or plan.evening or plan.period_label != "les 3 prochains mois")
    assert understood, f"« {item['question']} » : rien de compris"


def test_type_check_follows_the_search_rules() -> None:
    plan, _ = plan_search("jeux", SearchFilters(), NOW)
    card = {"title": "Atelier jeux en famille", "category": "atelier", "summary": "", "place": "",
            "start_at": NOW.isoformat(), "end_at": None, "audiences": [], "price": "3 €"}
    assert card_errors(card, plan, {"types": ["jeux"]}, []) == []           # type écrit dans le titre
    circus = {**card, "title": "Stage de cirque vacances"}
    plan, _ = plan_search("cirque", SearchFilters(), NOW)
    assert card_errors(circus, plan, {"types": ["spectacle"]}, ["cirque"]) == []   # le mot précis suffit
    assert card_errors({**card, "title": "Atelier poterie"}, plan, {"types": ["spectacle"]}, ["cirque"]) == [
        "mots", "type"]
