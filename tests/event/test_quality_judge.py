"""Tests du juge de qualité (sans appel réel à Mistral)."""

from __future__ import annotations

import json

from agent.watch import check_quality
from src.event import quality_judge as judge

ROWS = [{"event_id": str(i), "title": f"Événement {i}"} for i in range(4)]


def verdict(event_id: str, ok: bool = True, **changes) -> dict:
    return {"id": event_id, "coherent": True, "est_une_sortie": True, "publics_corrects": True,
            "categorie_correcte": ok, "probleme": "" if ok else "catégorie : atelier, pas concert", **changes}


def test_scores_and_problems() -> None:
    answer = json.dumps({"verdicts": [verdict("0"), verdict("1"), verdict("2", ok=False),
                                      verdict("3", est_une_sortie=False, probleme="offre d'emploi"),
                                      {"id": "x", "coherent": "peut-être"}]})
    verdicts = judge.parse_verdicts(answer, ["0", "1", "2", "3"])
    assert sorted(verdicts) == ["0", "1", "2", "3"] and verdicts["2"].score == 0.75
    summary = judge.summarize(verdicts, ROWS)
    assert summary["checked"] == 4 and summary["score"] == 0.875
    assert summary["categorie_correcte"] == 0.75 and summary["est_une_sortie"] == 0.75
    assert summary["problems"] == ["Événement 2 : catégorie : atelier, pas concert", "Événement 3 : offre d'emploi"]


def test_nothing_judged() -> None:
    assert judge.summarize({}, ROWS) == {"checked": 0, "score": None}
    assert judge.parse_verdicts("refus", ["0"]) == {}


def test_quality_alert() -> None:
    assert check_quality({"score": 0.9, "checked": 20}, minimum=0.8) == []
    assert check_quality({}, minimum=0.8) == []
    alert = check_quality({"score": 0.7, "checked": 20, "problems": ["Bal : pas une sortie"]}, minimum=0.8)
    assert alert == ["Qualité en baisse : score 70% sur 20 événements jugés (minimum 80%). Exemples : Bal : pas une sortie."]


def test_judge_sees_what_the_user_sees() -> None:
    text = judge.build_judge_text([{"event_id": "e1", "title": "Bal folk", "category_norm": "soiree",
                                    "audiences": ["soiree"], "summary": "Danses trad.", "city": "Marseillan",
                                    "description": "Bal avec initiation."}])
    assert "Catégorie : soiree" in text and "Publics : soiree" in text and "Résumé : Danses trad." in text


def test_judge_knows_the_closed_category_list() -> None:
    """16/10/2026 : sans la liste, le juge reprochait « marche » au lieu de « brocante »."""

    from src.event.event_enrichment import CATEGORIES

    assert all(category in judge.JUDGE_PROMPT for category in CATEGORIES)
    assert "brocante" in judge.JUDGE_PROMPT and '{"verdicts": [{"id"' in judge.JUDGE_PROMPT
