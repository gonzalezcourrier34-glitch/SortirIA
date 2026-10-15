"""DAG Airflow de la collecte nocturne de SortirIA.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026), sur le
modèle des DAG de CheckIt.AI (projet 12).

Chaque nuit à 2 h (heure de Paris), Airflow lance l'agent LangGraph de
`agent/graph.py`. Toute la logique est dans l'agent : le DAG ne fait que le
déclencher et faire échouer la tâche si la collecte a échoué, pour que
l'échec soit visible dans l'interface Airflow.

En production sur Azure, la même fonction est lancée par une tâche planifiée
(Azure Container Apps Job) avec `python -m agent`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from pendulum import datetime as pendulum_datetime


def run_collection(**_: Any) -> dict[str, Any]:
    """Lance l'agent et renvoie son bilan (visible dans XCom)."""

    from agent.graph import run_nightly_collection

    state = run_nightly_collection()
    summary = {
        key: state.get(key)
        for key in (
            "status", "sources", "preparation", "events_upserted", "events_removed",
            "events_expired", "events_embedded", "source_drops", "purged", "coverage", "cost_usd",
            "alerts", "run_id",
        )
    }
    if state.get("status") == "failed":
        raise RuntimeError(f"Collecte nocturne en échec : {summary}")
    return summary


with DAG(
    dag_id="sortiria_nightly_collection",
    description="Collecte nocturne des événements de Montpellier et du bassin de Thau (agent LangGraph).",
    schedule="0 2 * * *",
    start_date=pendulum_datetime(2026, 10, 13, tz="Europe/Paris"),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=1),
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10)},
    tags=["sortiria", "collecte", "agent"],
) as dag:
    PythonOperator(task_id="run_nightly_collection", python_callable=run_collection)
