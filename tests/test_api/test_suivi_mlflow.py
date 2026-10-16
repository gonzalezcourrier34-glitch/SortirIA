"""Envoi des évaluations dans MLflow (scripts/suivi_mlflow.py) et paramètres du juge."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from scripts import eval_pertinence
from scripts.suivi_mlflow import log_run


@pytest.fixture
def fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []
    module = types.ModuleType("mlflow")
    module.set_tracking_uri = lambda uri: calls.append(("uri", uri))
    module.set_experiment = lambda name: calls.append(("experience", name))

    @contextmanager
    def start_run(run_name: str):
        calls.append(("run", run_name))
        yield

    module.start_run = start_run
    module.log_params = lambda params: calls.append(("params", params))
    module.log_metrics = lambda metrics: calls.append(("metrics", metrics))
    module.log_artifact = lambda path: calls.append(("artifact", Path(path).name))
    monkeypatch.setitem(sys.modules, "mlflow", module)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_ACTIF", raising=False)
    return calls


def test_log_run_skips_empty_metrics_and_missing_files(fake_mlflow: list, tmp_path: Path) -> None:
    grid = tmp_path / "grille.csv"
    grid.write_text("x", encoding="utf-8")
    message = log_run("essai", "lancement", {"note": "n" * 300}, {"a": 0.9, "b": None, "c": ""},
                      [grid, tmp_path / "absent.csv"])
    assert "enregistré" in message
    calls = dict(fake_mlflow)
    assert calls["experience"] == "essai" and calls["metrics"] == {"a": 0.9}
    assert len(calls["params"]["note"]) == 250 and calls["artifact"] == "grille.csv"


def test_log_run_never_raises(fake_mlflow: list, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_ACTIF", "false")
    assert "désactivé" in log_run("essai", "x", {}, {})
    monkeypatch.setenv("MLFLOW_ACTIF", "true")
    sys.modules["mlflow"].set_experiment = lambda name: (_ for _ in ()).throw(RuntimeError("panne"))
    assert "indisponible" in log_run("essai", "x", {}, {})


def test_judge_record_keeps_model_prompt_and_agreement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERTINENCE_JUDGE_MODEL", "mistral-large-latest")
    report = {"questions": 50, "cartes_jugees": 139, "pertinence_at_3": 0.978, "questions_satisfaites": 1.0,
              "juge": {"score": {"pertinence_at_3": 0.986},
                       "accord": {"accord": 0.986, "kappa": 0.5, "ac1": 0.985, "rejets_humains": 3,
                                  "rejets_retrouves": 1}}}
    params, metrics, files = eval_pertinence.mlflow_record(report, tmp_path / "grille.csv",
                                                           tmp_path / "grille_juge.csv")
    assert params["juge_modele"] == "mistral-large-latest" and params["juge_consigne"] == "v7"
    assert metrics["ac1_juge"] == 0.985 and metrics["accord_juge"] == 0.986
    assert [path.name for path in files] == ["grille.csv", "grille_juge.csv"]
    params, metrics, files = eval_pertinence.mlflow_record({**report, "juge": None}, tmp_path / "g.csv", None)
    assert "juge_modele" not in params and set(metrics) == {"pertinence_at_3", "questions_satisfaites"}
