"""Tests du suivi de l'agent : métriques, durées des nœuds, MLflow (simulé).

**Provenance** : nouveau fichier écrit pour SortirIA (14/10/2026). MLflow est
remplacé par un faux module : on vérifie ce que l'agent lui envoie.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from agent import tracking
from agent.graph import run_nightly_collection
from agent.services import ExtractionOutput

from .test_agent import FakeDatabase, make_article, make_services

PDF_STATS = {"name": "Programmes PDF", "type": "document", "status": "success", "analyzed": 193, "kept": 145,
             "rejected": 48, "requests": 1, "duration_s": 7.2,
             "rejection_reasons": {"doublon": 40, "champs_llm_invalides": 7, "titre_absent_du_texte": 1}}


class FakeMlflow(types.ModuleType):
    """Faux module mlflow : garde tout ce qu'on lui envoie."""

    def __init__(self) -> None:
        super().__init__("mlflow")
        self.calls: dict[str, Any] = {"params": {}, "metrics": {}, "tags": {}, "dicts": {}}
        self.langchain = types.SimpleNamespace(autolog=lambda: self.calls.setdefault("autolog", True))

    def set_tracking_uri(self, uri): self.calls["uri"] = uri
    def get_experiment_by_name(self, name): return None
    def create_experiment(self, name, artifact_location=None): self.calls["experiment"] = name
    def set_experiment(self, name): pass
    def start_run(self, run_name=""):
        self.calls["run_name"] = run_name
        return types.SimpleNamespace(info=types.SimpleNamespace(run_id="abc123"))
    def log_params(self, params): self.calls["params"].update(params)
    def log_param(self, key, value): self.calls["params"][key] = value
    def log_metrics(self, metrics): self.calls["metrics"].update(metrics)
    def set_tags(self, tags): self.calls["tags"].update(tags)
    def log_dict(self, data, name): self.calls["dicts"][name] = data
    def end_run(self, status="FINISHED"): self.calls["end"] = status


@pytest.fixture
def fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> FakeMlflow:
    module = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", module)
    return module


def test_metric_keys_are_accepted_by_mlflow() -> None:
    assert tracking.metric_key("extracteur", "OpenAgenda - Montpellier (34)", "kept") == \
        "extracteur/OpenAgenda - Montpellier _34_/kept"


def test_run_metrics_include_pdf_quality_cost_coverage_and_durations() -> None:
    state = {
        "sources": ["openagenda", "document"], "failed_sources": ["openagenda"],
        "source_attempts": {"openagenda": 2, "document": 1},
        "events": [{"source": "document"}, {"source": "document"}],
        "preparation": {"events_extracted": 10, "events_rejected": 1},
        "extractor_stats": [PDF_STATS],
        "llm_usage": {"mistral-embed": {"input": 500, "output": 0}},
        "coverage": {"bassin_de_thau": {"famille": 4, "soiree": 1}},
        "node_durations": {"extract_sources": 90.5, "load_events": 2.5},
        "cost_usd": 0.04, "alerts": ["a", "b"],
    }
    metrics = tracking.run_metrics(state)
    assert metrics["rejection_rate"] == 0.1 and metrics["alerts_count"] == 2
    assert metrics["source/openagenda/failed"] == 1.0 and metrics["source/openagenda/attempts"] == 2.0
    assert metrics["source/document/events"] == 2.0
    assert metrics["pdf/inventions_rattrapees"] == 1.0
    assert metrics["pdf/taux_rejet_llm"] == pytest.approx(8 / 193)
    assert metrics["extracteur/Programmes PDF/rejet/doublon"] == 40.0
    assert metrics["tokens/mistral-embed/input"] == 500.0 and metrics["couverture/bassin_de_thau/soiree"] == 1.0
    assert metrics["duree/total"] == 93.0 and metrics["cost_usd"] == 0.04


def test_a_night_is_one_mlflow_run(fake_mlflow: FakeMlflow) -> None:
    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(
        articles=[make_article(1, "Concert")], extractor_stats=[PDF_STATS],
        usage={"mistral-small-latest": {"input": 100, "output": 50}}), db)
    services.tracker = tracking.MlflowTracker("sqlite:///test.db")
    state = run_nightly_collection(services)

    calls = fake_mlflow.calls
    assert calls["experiment"] == "sortiria-collecte" and calls.get("autolog") is True
    assert calls["params"]["document_prompt_version"] and calls["params"]["sources_lancees"] == "openagenda"
    assert calls["metrics"]["events_kept"] == 1.0 and "duree/extract_sources" in calls["metrics"]
    assert calls["tags"]["status"] == "success" and calls["end"] == "FINISHED"
    assert calls["dicts"]["bilan.json"]["extractor_stats"] == [PDF_STATS]
    # Le numéro de l'exécution MLflow est enregistré avec le bilan en base.
    inserted = next(params for kind, sql, params in db.calls if kind == "execute" and sql == "INSERT"
                    and isinstance(params, dict) and "mlflow_run_id" in params)
    assert inserted["mlflow_run_id"] == "abc123"
    assert set(state["node_durations"]) == {"plan_sources", "extract_sources", "prepare", "load_events", "judge_quality", "watch"}


def test_tracking_errors_never_break_the_night(fake_mlflow: FakeMlflow) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("serveur MLflow injoignable")

    fake_mlflow.start_run = broken
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), FakeDatabase())
    services.tracker = tracking.MlflowTracker("sqlite:///test.db")
    state = run_nightly_collection(services)
    assert state["status"] == "success" and services.tracker.run_id == ""


def test_unreachable_server_is_skipped_quickly(fake_mlflow: FakeMlflow, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def refused(*args, **kwargs):
        raise httpx.ConnectError("connexion refusée")

    monkeypatch.setattr(httpx, "get", refused)
    tracker = tracking.MlflowTracker("http://localhost:5000")
    tracker.start({"started_at": "2026-10-14T02:00"})
    assert tracker.run_id == "" and "run_name" not in fake_mlflow.calls     # aucun appel à MLflow


def test_tracker_can_be_disabled(monkeypatch: pytest.MonkeyPatch, fake_mlflow: FakeMlflow) -> None:
    monkeypatch.setenv("MLFLOW_ACTIF", "false")
    assert isinstance(tracking.build_tracker(), tracking.NullTracker)
    monkeypatch.setenv("MLFLOW_ACTIF", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    tracker = tracking.build_tracker()
    assert isinstance(tracker, tracking.MlflowTracker) and tracker.tracking_uri == "http://localhost:5000"
