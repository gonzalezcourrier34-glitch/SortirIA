"""Tests du rapport Evidently mensuel (scripts/rapport_evidently.py), sans Evidently ni base."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from scripts import rapport_evidently as rapport


class FakeConnection:
    """Rend les lignes préparées pour chaque requête, comme un curseur psycopg2."""

    def __init__(self, questions: list[tuple], nights: list[tuple]) -> None:
        self.answers = {"rag_requests": (["fenetre", *rapport.QUESTION_CATEGORIES, *rapport.QUESTION_NUMBERS],
                                         questions),
                        "collect_runs": (["fenetre", *rapport.COLLECT_NUMBERS], nights)}
        self.params: list[dict[str, Any]] = []

    def cursor(self) -> FakeConnection:
        return self

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.params.append(params)
        table = "rag_requests" if "rag_requests" in sql else "collect_runs"
        columns, self._rows = self.answers[table]
        self.description = [(name,) for name in columns]

    def fetchall(self) -> list[tuple]:
        return self._rows


def question(window: str, zone: str = "thau", latency: int = 2000) -> tuple:
    return (window, zone, "famille", "concert", "soir", "non", "exacte", "non", latency)


def night(window: str, score: float = 0.9) -> tuple:
    return (window, 1200, 0.05, score, 0.005, 2450)


# ----------------------------------------------------------------------------
# Les deux mois
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(("today", "reference", "current", "end"), [
    (date(2026, 11, 1), date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)),
    (date(2026, 1, 1), date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)),
    (date(2026, 2, 15), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)),
])
def test_month_windows_compare_the_last_month_to_the_one_before(today, reference, current, end) -> None:
    windows = rapport.month_windows(today=today)
    assert (windows.reference_start, windows.current_start, windows.current_end) == (reference, current, end)


def test_month_windows_for_a_chosen_month() -> None:
    windows = rapport.month_windows("2026-10")
    assert windows.label == "2026-10 contre 2026-09" and windows.current_end == date(2026, 11, 1)
    with pytest.raises(ValueError):
        rapport.month_windows("octobre")


def test_query_params_are_midnights_in_paris() -> None:
    params = rapport.query_params(rapport.month_windows("2026-10"))
    assert [params[key].isoformat() for key in ("start", "split", "end")] == [
        "2026-09-01T00:00:00+02:00", "2026-10-01T00:00:00+02:00", "2026-11-01T00:00:00+01:00"]


def test_test_questions_are_excluded_like_the_rest_of_the_project() -> None:
    # Le script n'importe pas le projet : sa copie du filtre doit rester identique.
    from src.event.event_repository import NOT_A_TEST_QUESTION

    assert rapport.NOT_A_TEST_QUESTION == NOT_A_TEST_QUESTION
    assert rapport.NOT_A_TEST_QUESTION in rapport.QUESTIONS_SQL


# ----------------------------------------------------------------------------
# Tableaux et garde-fou
# ----------------------------------------------------------------------------

def test_split_keeps_the_columns_of_each_month() -> None:
    frame = pd.DataFrame([question("reference"), question("courant"), question("courant", "montpellier")],
                         columns=["fenetre", *rapport.QUESTION_CATEGORIES, *rapport.QUESTION_NUMBERS])
    reference, current = rapport.split(frame, ["zone", "latence_ms"])
    assert list(reference.columns) == ["zone", "latence_ms"]
    assert len(reference) == 1 and current["zone"].tolist() == ["thau", "montpellier"]
    empty_reference, empty_current = rapport.split(pd.DataFrame(), ["zone"])
    assert empty_reference.empty and empty_current.empty


def test_to_numbers_reads_decimals_and_missing_values() -> None:
    from decimal import Decimal

    frame = rapport.to_numbers(pd.DataFrame({"taux_rejet": [Decimal("0.125"), None]}), ["taux_rejet"])
    assert frame["taux_rejet"].iloc[0] == 0.125 and pd.isna(frame["taux_rejet"].iloc[1])


def test_drifted_columns_reads_the_evidently_summary() -> None:
    details = {"metrics": [{"metric_name": "DriftedColumnsCount(drift_share=0.5)",
                            "value": {"count": 2.0, "share": 0.2857}},
                           {"metric_name": "ValueDrift(column=zone)", "value": 0.03}]}
    assert rapport.drifted_columns(details) == {"colonnes_en_derive": 2, "part_en_derive": 0.286}
    assert rapport.drifted_columns({"metrics": []}) == {"colonnes_en_derive": None, "part_en_derive": None}


def test_low_traffic_writes_a_page_instead_of_a_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 93 questions en octobre, rien en septembre : pas de comparaison, et Evidently n'est pas appelé.
    def no_evidently(*args, **kwargs):
        raise AssertionError("Evidently ne doit pas tourner sous le minimum")

    monkeypatch.setattr(rapport, "evidently_drift", no_evidently)
    connection = FakeConnection([question("courant")] * 93, [night("courant")] * 31)
    summary = rapport.build_reports(connection, rapport.month_windows("2026-10"), tmp_path)

    assert [item["statut"] for item in summary["jeux"]] == ["donnees_insuffisantes", "donnees_insuffisantes"]
    page = (tmp_path / "questions.html").read_text(encoding="utf-8")
    assert "pas assez de données" in page and "0 questions en référence, 93 questions ce mois-ci" in page
    assert json.loads((tmp_path / "resume.json").read_text(encoding="utf-8"))["mois"] == "2026-10"
    assert "pas assez de données (minimum 100 par mois)" in (tmp_path / "resume.md").read_text(encoding="utf-8")


def test_enough_data_runs_evidently_on_both_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_drift(reference, current, categorical, numerical, html_path):
        calls.append({"reference": len(reference), "current": len(current), "categorical": list(categorical),
                      "numerical": list(numerical), "dtypes": current.dtypes.astype(str).to_dict()})
        html_path.write_text("<html>rapport</html>", encoding="utf-8")
        return {"colonnes_en_derive": 1, "part_en_derive": 0.125}

    monkeypatch.setattr(rapport, "evidently_drift", fake_drift)
    connection = FakeConnection([question("reference")] * 120 + [question("courant", latency=3000)] * 150,
                                [night("reference")] * 30 + [night("courant", 0.94)] * 31)
    summary = rapport.build_reports(connection, rapport.month_windows("2026-10"), tmp_path)

    assert [item["statut"] for item in summary["jeux"]] == ["compare", "compare"]
    assert calls[0]["reference"] == 120 and calls[0]["current"] == 150
    assert calls[0]["categorical"] == rapport.QUESTION_CATEGORIES and calls[0]["numerical"] == ["latence_ms"]
    assert calls[1]["categorical"] == [] and calls[1]["dtypes"]["score_juge"] == "float64"
    assert "1 colonne(s) en dérive sur 8" in (tmp_path / "resume.md").read_text(encoding="utf-8")
    # Les bornes des requêtes : septembre et octobre, coupés au 1er octobre.
    assert connection.params[0]["split"].isoformat() == "2026-10-01T00:00:00+02:00"
