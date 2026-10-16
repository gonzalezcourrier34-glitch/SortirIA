"""Tests du script de mesure du coût et du temps de réponse (sans base ni Mistral)."""

from __future__ import annotations

from typing import Any

from app import search_service
from scripts import mesure_cout_latence as mesure


def test_questions_come_from_both_evaluation_sets_in_a_fixed_order() -> None:
    items = mesure.load_questions()
    assert len(items) >= 150 and all("question" in item and "filtres" in item for item in items)
    assert mesure.pick(items, 5) == mesure.pick(items, 5)
    assert len(mesure.pick(items, len(items) + 3)) == len(items) + 3     # répétées au-delà du jeu


def test_percentile_matches_postgresql() -> None:
    assert mesure.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert mesure.percentile([10, 20], 0.95) == 19.5
    assert mesure.percentile([], 0.95) == 0.0


def test_one_measure_times_each_step_and_prices_the_answer(monkeypatch) -> None:
    ticks = iter([0.0, 0.1, 0.4, 0.5, 1.5, 1.6])

    models: list[str] = ["modèle de la question précédente"]

    def fake_search(connection, question, filters, services):
        models.append("mistral-small-latest")          # ce que fait la rédaction (writer)
        for step in mesure.STEPS:
            yield search_service.progress(step, "running")
            yield search_service.progress(step, "completed", "ok")
        yield {"event": "completed", "result": {"events": [{"event_id": "e1"}], "tokens": {"input": 1000, "output": 200}}}

    monkeypatch.setattr(mesure, "run_search", fake_search)
    row = mesure.measure_one(None, {"question": "concert", "filtres": {}},
                             search_service.SearchServices(embed=lambda q: "", write=None, log=False),
                             models, clock=lambda: next(ticks))
    assert row["ms"] == 1600.0 and row["etapes_ms"] == {"comprendre": 100.0, "rechercher": 300.0,
                                                         "filtrer": 100.0, "rediger": 1000.0}
    # 1 000 × 0,10 $/M + 200 × 0,30 $/M, plus l'embedding estimé de « concert ».
    assert 0.00016 <= row["cout_usd"] < 0.00017 and row["cache"] is False


def test_summary_and_cost_with_repeated_questions() -> None:
    rows: list[dict[str, Any]] = [
        {"ms": ms, "etapes_ms": {"rediger": ms - 100}, "cache": False, "cartes": 8, "degradee": False, "cout_usd": 0.0004}
        for ms in (1000, 2000, 3000, 4000)]
    summary = mesure.summarize(rows)
    assert summary["mediane_ms"] == 2500 and summary["p95_ms"] == 3850 and summary["cout_1000_usd"] == 0.4
    hit = {"cout_1000_usd": 0.0}
    assert mesure.mixed_cost(summary, hit, 0.5) == 0.2
