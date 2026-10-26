"""Tests de la surveillance de la dérive (agent/drift.py, 19/10/2026).

Le calcul et la restitution sont testés sans base ; les requêtes, sur un vrai
PostgreSQL quand SORTIRIA_TEST_DATABASE_URL vise une base « test » (comme
tests/test_api/test_monitoring.py).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any

import pytest

from agent import drift, nodes, tracking
from agent.report import build_report_email
from agent.services import CollectionServices
from src.event.event_normalizer import PARIS

MONDAY = date(2026, 10, 19)
THURSDAY = date(2026, 10, 15)


def rows(name: str, reference: dict[str, int], current: dict[str, int]) -> list[tuple[str, str, str, int]]:
    return ([(name, "reference", value, n) for value, n in reference.items()]
            + [(name, "courant", value, n) for value, n in current.items()])


SAME_ZONES = {"montpellier": 60, "bassin_de_thau": 30, "non_precisee": 10}


def report_with(question_rows: list, n_ref: int = 200, n_cur: int = 50, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "question_rows": question_rows,
        "question_counts": {"reference": n_ref, "courant": n_cur},
        "question_measures": {"reference": {"latence_p95_ms": 6000.0, "part_sans_mistral": 0.01},
                              "courant": {"latence_p95_ms": 6200.0, "part_sans_mistral": 0.01}},
        "feedback": {"reference": {"avis": 40, "part_pouces_haut": 0.8},
                     "courant": {"avis": 25, "part_pouces_haut": 0.78}},
        "collect_rows": rows("source", {"openagenda": 700, "datatourisme": 2800}, {"openagenda": 180, "datatourisme": 690}),
        "collect_measures": {"reference": {"nuits": 28, "taux_rejet": 0.02, "score_juge": 0.92},
                             "courant": {"nuits": 7, "taux_rejet": 0.03, "score_juge": 0.9}},
        "minimum": 30,
    }
    arguments.update(overrides)
    return drift.build_report(**arguments)


def indicator(report: dict[str, Any], family: str, name: str) -> dict[str, Any]:
    return next(item for item in report["indicateurs"] if item["famille"] == family and item["nom"] == name)


# ---------------------------------------------------------------------------
# PSI
# ---------------------------------------------------------------------------

def test_psi_is_zero_for_identical_distributions_and_ignores_scale() -> None:
    assert drift.psi({"a": 10, "b": 30}, {"a": 10, "b": 30}) == pytest.approx(0.0)
    # Mêmes parts, effectifs différents : pas de dérive.
    assert drift.psi({"a": 100, "b": 300}, {"a": 10, "b": 30}) == pytest.approx(0.0)


def test_psi_is_symmetric_and_grows_with_the_shift() -> None:
    small = drift.psi({"a": 50, "b": 50}, {"a": 55, "b": 45})
    large = drift.psi({"a": 50, "b": 50}, {"a": 80, "b": 20})
    assert 0 < small < drift.PSI_MODERATE < large
    assert drift.psi({"a": 80, "b": 20}, {"a": 50, "b": 50}) == pytest.approx(large)


def test_psi_handles_a_category_missing_from_one_window() -> None:
    value = drift.psi({"a": 50, "b": 50}, {"a": 50, "b": 40, "nouvelle": 10})
    assert value > 0 and value != float("inf")


def test_psi_levels() -> None:
    assert drift.psi_level(0.05) == drift.STABLE
    assert drift.psi_level(0.10) == drift.MODERATE
    assert drift.psi_level(0.3) == drift.STRONG


def test_largest_changes_explain_the_drift() -> None:
    changes = drift.largest_changes({"montpellier": 80, "bassin_de_thau": 20}, {"montpellier": 50, "bassin_de_thau": 50})
    assert {change["valeur"] for change in changes} == {"montpellier", "bassin_de_thau"}
    assert changes[0]["reference"] in (0.8, 0.2) and abs(changes[0]["courant"] - changes[0]["reference"]) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def test_stable_week() -> None:
    report = report_with(rows("zone", {k: v * 4 for k, v in SAME_ZONES.items()}, SAME_ZONES))
    assert report["statut"] == drift.STATUS_STABLE
    assert indicator(report, "questions", "zone")["niveau"] == drift.STABLE
    assert indicator(report, "collecte", "source")["niveau"] == drift.STABLE
    assert drift.drift_alerts(report) == []


def test_questions_moving_to_another_zone_is_a_strong_drift() -> None:
    report = report_with(rows("zone", {"montpellier": 160, "bassin_de_thau": 40}, {"montpellier": 15, "bassin_de_thau": 35}))
    zone = indicator(report, "questions", "zone")
    assert zone["niveau"] == drift.STRONG and zone["psi"] > drift.PSI_STRONG
    assert report["statut"] == drift.STATUS_DRIFT
    alerts = drift.drift_alerts(report)
    assert len(alerts) == 1 and "zone demandée" in alerts[0] and "bassin_de_thau 20 % → 70 %" in alerts[0]


def test_more_unanswered_questions_is_an_output_drift() -> None:
    report = report_with(rows("type_de_reponse", {"exacte": 180, "introuvable": 20}, {"exacte": 25, "introuvable": 25}))
    assert indicator(report, "reponses", "type_de_reponse")["niveau"] == drift.STRONG


def test_low_traffic_is_not_judged() -> None:
    """Moins de 30 questions dans la semaine : pas de fausse alerte."""

    report = report_with(rows("zone", {"montpellier": 160, "bassin_de_thau": 40}, {"bassin_de_thau": 12}),
                         n_cur=12)
    zone = indicator(report, "questions", "zone")
    assert zone["niveau"] == drift.INSUFFICIENT and zone["psi"] is None
    assert indicator(report, "reponses", "latence_p95_ms")["niveau"] == drift.INSUFFICIENT
    # La collecte, elle, reste jugée : elle ne dépend pas des visiteurs.
    assert indicator(report, "collecte", "source")["niveau"] == drift.STABLE
    assert drift.drift_alerts(report) == []


def test_nothing_to_judge() -> None:
    report = drift.build_report(question_rows=[], question_counts={}, question_measures={}, feedback={},
                                collect_rows=[], collect_measures={}, minimum=30)
    assert report["statut"] == drift.INSUFFICIENT
    assert all(item["niveau"] == drift.INSUFFICIENT for item in report["indicateurs"])


def test_minimum_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DERIVE_MIN_QUESTIONS", "5")
    assert drift.min_questions() == 5
    monkeypatch.setenv("DERIVE_MIN_QUESTIONS", "beaucoup")
    assert drift.min_questions() == drift.DEFAULT_MIN_QUESTIONS


def test_measures_detect_slower_answers_and_lower_quality() -> None:
    report = report_with([], question_measures={
        "reference": {"latence_p95_ms": 6000.0, "part_sans_mistral": 0.0},
        "courant": {"latence_p95_ms": 9500.0, "part_sans_mistral": 0.06}},
        collect_measures={"reference": {"nuits": 28, "taux_rejet": 0.02, "score_juge": 0.95},
                          "courant": {"nuits": 7, "taux_rejet": 0.02, "score_juge": 0.80}})
    assert indicator(report, "reponses", "latence_p95_ms")["niveau"] == drift.STRONG
    assert indicator(report, "reponses", "part_sans_mistral")["niveau"] == drift.MODERATE
    assert indicator(report, "collecte", "score_juge")["niveau"] == drift.STRONG
    assert indicator(report, "collecte", "taux_rejet")["niveau"] == drift.STABLE
    assert any("6000 → 9500 ms" in alert for alert in drift.drift_alerts(report))


def test_thumbs_up_need_enough_feedback() -> None:
    few = report_with([], feedback={"reference": {"avis": 40, "part_pouces_haut": 0.8},
                                    "courant": {"avis": 5, "part_pouces_haut": 0.2}})
    assert indicator(few, "reponses", "part_pouces_haut")["niveau"] == drift.INSUFFICIENT
    many = report_with([], feedback={"reference": {"avis": 40, "part_pouces_haut": 0.8},
                                     "courant": {"avis": 30, "part_pouces_haut": 0.6}})
    assert indicator(many, "reponses", "part_pouces_haut")["niveau"] == drift.STRONG


def test_report_is_json_serialisable() -> None:
    report = report_with(rows("zone", {"montpellier": 160, "bassin_de_thau": 40}, {"montpellier": 15, "bassin_de_thau": 35}))
    assert json.loads(json.dumps(report)) == report


# ---------------------------------------------------------------------------
# Restitution : mail, MLflow, nuit de l'agent
# ---------------------------------------------------------------------------

STRONG_REPORT = report_with(rows("zone", {"montpellier": 160, "bassin_de_thau": 40},
                                 {"montpellier": 15, "bassin_de_thau": 35}))


def test_mail_section() -> None:
    lines = "\n".join(drift.report_lines(STRONG_REPORT))
    assert "Dérive : DÉRIVE (7 derniers jours comparés aux 28 précédents)" in lines
    assert "- forte : zone demandée : PSI" in lines
    assert "- stables :" in lines and "part de chaque source" in lines
    assert "monitoring/README.md" in lines
    assert drift.report_lines({}) == []


def test_report_email_contains_the_drift_on_monday() -> None:
    _, body = build_report_email({"status": "success", "drift_report": STRONG_REPORT})
    assert "Dérive : DÉRIVE" in body
    _, body = build_report_email({"status": "success"})
    assert "Dérive" not in body


def test_mlflow_metrics() -> None:
    metrics = drift.mlflow_metrics(STRONG_REPORT)
    assert metrics["derive/statut"] == 2.0
    assert metrics["derive/questions/zone/psi"] > drift.PSI_STRONG
    assert metrics["derive/reponses/latence_p95_ms/courant"] == 6200.0
    assert "derive/questions/zone/psi" in tracking.run_metrics({"drift": STRONG_REPORT})


def services_on(day: date) -> CollectionServices:
    return CollectionServices(extract=lambda names: None, connect=lambda: None, embed=lambda texts: [],
                              now=lambda: datetime(day.year, day.month, day.day, 3, tzinfo=PARIS))


def test_strong_drift_becomes_an_alert_on_monday_only() -> None:
    state = {"events": [], "drift": STRONG_REPORT, "dry_run": True}
    monday = nodes.watch_collection(state, {"configurable": {"services": services_on(MONDAY)}})
    assert monday["drift_report"] == STRONG_REPORT
    assert any(alert.startswith("Dérive forte (questions)") for alert in monday["watch_alerts"])
    thursday = nodes.watch_collection(state, {"configurable": {"services": services_on(THURSDAY)}})
    assert thursday["drift_report"] == {}
    assert not any(alert.startswith("Dérive") for alert in thursday["watch_alerts"])


def test_drift_failure_never_stops_the_night() -> None:
    class Broken:
        def connect(self):
            raise ConnectionError("base injoignable")

    services = CollectionServices(extract=lambda names: None, connect=Broken().connect, embed=lambda texts: [])
    assert nodes.measure_drift(services) == {}


# ---------------------------------------------------------------------------
# Requêtes sur un vrai PostgreSQL
# ---------------------------------------------------------------------------

TEST_URL = os.environ.get("SORTIRIA_TEST_DATABASE_URL", "")


@pytest.fixture()
def db() -> Any:
    from scripts.jeu_test_recherche import is_test_database

    if not is_test_database(TEST_URL):
        pytest.skip("SORTIRIA_TEST_DATABASE_URL absente (ou base dont le nom ne contient pas « test »)")
    psycopg2 = pytest.importorskip("psycopg2")
    connection = psycopg2.connect(TEST_URL)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS sortiria CASCADE")
    from scripts.init_db import apply_schema

    apply_schema(connection)
    yield connection
    connection.close()


def test_fetch_drift_on_postgresql(db: Any) -> None:
    def question(days_ago: int, zone: str, exact: bool = True, status: str = "ok") -> tuple[Any, ...]:
        constraints = {"zone": None, "compris": {"zone": zone, "public": "famille", "categories": ["concert"],
                                                 "moment": None, "soir": days_ago % 2 == 0, "gratuit": False},
                       "recherche": {"exact": exact, "elargi": [], "sujet_introuvable": False}}
        return (days_ago, json.dumps(constraints), status, 3000 + days_ago)

    rows_to_insert = ([question(10 + i % 20, "montpellier") for i in range(80)]
                      + [question(10 + i % 20, "bassin_de_thau") for i in range(20)]
                      + [question(i % 6, "bassin_de_thau", exact=i % 3 != 0) for i in range(40)]
                      + [question(i % 6, "montpellier") for i in range(10)])
    with db.cursor() as cursor:
        for days_ago, constraints, status, latency in rows_to_insert:
            cursor.execute("""INSERT INTO sortiria.rag_requests (asked_at, question, constraints, status, latency_ms)
                              VALUES (now() - make_interval(days => %s), 'q', %s, %s, %s)""",
                           (days_ago, constraints, status, latency))
        # Mes questions de test (26/10/2026) : ignorées, sinon Thau passerait en tête
        for days_ago, constraints, status, latency in [question(1, "bassin_de_thau")] * 30:
            cursor.execute("""INSERT INTO sortiria.rag_requests (asked_at, question, constraints, status, latency_ms)
                              VALUES (now() - make_interval(days => %s), '[test] un concert', %s, %s, %s)""",
                           (days_ago, constraints, status, latency))
        cursor.execute("""INSERT INTO sortiria.sources (source_id, name, kind, licence, attribution)
                          VALUES ('openagenda', 'OpenAgenda', 'api', 'test', 'test') ON CONFLICT DO NOTHING""")
        for days_ago in range(1, 30):
            cursor.execute("""INSERT INTO sortiria.collect_runs (started_at, status, events_extracted, events_rejected,
                              rejection_reasons, quality_score)
                              VALUES (now() - make_interval(days => %s), 'success', 100, 2,
                                      '{"pas_une_sortie": 2}', 0.9) RETURNING run_id""", (days_ago,))
            run_id = cursor.fetchone()[0]
            cursor.execute("""INSERT INTO sortiria.collect_run_sources (run_id, source_id, status, events_kept)
                              VALUES (%s, 'openagenda', 'ok', 98)""", (run_id,))
        report = drift.fetch_drift(cursor.connection, minimum=30)

    assert report["questions"] == {"reference": 100, "courant": 50, "minimum": 30}
    zone = indicator(report, "questions", "zone")
    assert zone["niveau"] == drift.STRONG                       # Montpellier 80 % → 20 %
    assert indicator(report, "reponses", "type_de_reponse")["niveau"] in (drift.MODERATE, drift.STRONG)
    assert indicator(report, "questions", "public")["niveau"] == drift.STABLE
    assert indicator(report, "collecte", "source")["niveau"] == drift.STABLE
    assert report["nuits"] == {"reference": 23, "courant": 6}
    # Le rapport s'enregistre dans collect_runs et le tableau Grafana le relit.
    from monitoring.grafana import construire_tableau_modeles as tableau
    from src.event import event_repository as repository

    repository.record_collect_run(db, {"status": "success", "drift": report})
    with db.cursor() as cursor:
        cursor.execute(tableau.DRIFT_STATUS)
        assert cursor.fetchone()[0] == 2
        cursor.execute(tableau.DRIFT_LATEST)
        first = cursor.fetchone()
        assert first[2] == drift.STRONG
