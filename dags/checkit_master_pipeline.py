"""DAG maître du pipeline ETL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_master_pipeline.py` (commit 93632c5).
Modifié pour SortirIA : laissé en lancement manuel (la nuit, c'est sortiria_nightly_dag qui tourne).

Orchestre successivement l'extraction, la transformation, le chargement
PostgreSQL et le contrôle qualité d'un même lot partagé.

Le DAG maître transmet uniquement une configuration JSON légère aux DAGs
enfants. Les données métier restent dans le stockage partagé du pipeline.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from airflow import DAG
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.trigger_dagrun import (
    TriggerDagRunOperator
)
from pendulum import datetime as pendulum_datetime


# =============================================================================
# Configuration générale
# =============================================================================

DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 1,
    "retry_delay": timedelta(minutes=1)
}

MASTER_DAG_TIMEOUT = timedelta(hours=4)

EXTRACT_DAG_TIMEOUT = timedelta(minutes=90)
TRANSFORM_DAG_TIMEOUT = timedelta(minutes=45)
LOAD_DAG_TIMEOUT = timedelta(minutes=30)
QUALITY_DAG_TIMEOUT = timedelta(minutes=30)


# =============================================================================
# Configuration commune des DAGs enfants
# =============================================================================

COMMON_CONFIGURATION: dict[str, Any] = {
    "batch_id": "{{ run_id }}",
    "parent_dag_id": "{{ dag.dag_id }}",
    "parent_run_id": "{{ run_id }}",

    # logical_date peut être absente lors d'un déclenchement manuel REST.
    # Dans ce cas, la valeur JSON transmise au DAG enfant sera null.
    "logical_date": (
        "{{ dag_run.logical_date.isoformat() "
        "if dag_run.logical_date else none }}"
    ),

    # run_after représente le moment à partir duquel le run peut démarrer.
    # Il reste disponible même lorsque logical_date vaut null.
    "triggered_at": (
        "{{ dag_run.run_after.isoformat() "
        "if dag_run.run_after else none }}"
    )
}

EXTRACT_CONFIGURATION: dict[str, Any] = {
    **COMMON_CONFIGURATION,
    "require_image": False
}

TRANSFORM_CONFIGURATION: dict[str, Any] = {
    **COMMON_CONFIGURATION
}

LOAD_CONFIGURATION: dict[str, Any] = {
    **COMMON_CONFIGURATION
}

QUALITY_CONFIGURATION: dict[str, Any] = {
    **COMMON_CONFIGURATION,
    "quality_thresholds": {
        "minimum_article_count": 1,
        "minimum_valid_article_rate": 95.0,
        "maximum_missing_title_rate": 0.0,
        "maximum_missing_content_rate": 5.0,
        "maximum_duplicate_url_rate": 0.0,
        "maximum_invalid_image_rate": 20.0
    }
}


# =============================================================================
# Création des déclencheurs
# =============================================================================

def create_child_trigger(
    *,
    task_id: str,
    child_dag_id: str,
    configuration: dict[str, Any],
    execution_timeout: timedelta
) -> TriggerDagRunOperator:
    """Crée un déclencheur synchrone pour un DAG enfant."""

    return TriggerDagRunOperator(
        task_id=task_id,
        trigger_dag_id=child_dag_id,
        trigger_run_id=f"{child_dag_id}__{{{{ run_id }}}}",
        conf=configuration,

        # Le DAG enfant hérite de la date logique du DAG maître.
        # La valeur reste nulle lorsque le run maître n'en possède pas.
        logical_date=(
            "{{ dag_run.logical_date.isoformat() "
            "if dag_run.logical_date else none }}"
        ),

        wait_for_completion=True,
        deferrable=True,
        poke_interval=10,
        allowed_states=["success"],
        failed_states=["failed"],
        fail_when_dag_is_paused=True,
        reset_dag_run=True,
        execution_timeout=execution_timeout
    )


# =============================================================================
# Définition du DAG
# =============================================================================

with DAG(
    dag_id="checkit_master_pipeline",
    description=(
        "Orchestre l'extraction, la transformation, le chargement "
        "PostgreSQL et le contrôle qualité."
    ),
    default_args=DEFAULT_ARGS,
    # Lancement manuel uniquement : la collecte nocturne de SortirIA passe
    # désormais par dags/sortiria_nightly_dag.py (agent LangGraph).
    schedule=None,
    start_date=pendulum_datetime(2026, 10, 13, tz="Europe/Paris"),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=MASTER_DAG_TIMEOUT,
    render_template_as_native_obj=True,
    tags=["checkit", "master", "etl", "multimodal"]
) as dag:

    start = EmptyOperator(task_id="start")

    # Étape 1 : extrait les articles et prépare leurs images.
    trigger_extract = create_child_trigger(
        task_id="trigger_extract",
        child_dag_id="checkit_extract",
        configuration=EXTRACT_CONFIGURATION,
        execution_timeout=EXTRACT_DAG_TIMEOUT
    )

    # Étape 2 : transforme les articles en collections PostgreSQL.
    trigger_transform = create_child_trigger(
        task_id="trigger_transform",
        child_dag_id="checkit_transform",
        configuration=TRANSFORM_CONFIGURATION,
        execution_timeout=TRANSFORM_DAG_TIMEOUT
    )

    # Étape 3 : charge les collections transformées dans PostgreSQL.
    trigger_load = create_child_trigger(
        task_id="trigger_load",
        child_dag_id="checkit_load",
        configuration=LOAD_CONFIGURATION,
        execution_timeout=LOAD_DAG_TIMEOUT
    )

    # Étape 4 : calcule et valide les indicateurs qualité.
    trigger_quality = create_child_trigger(
        task_id="trigger_quality",
        child_dag_id="checkit_quality",
        configuration=QUALITY_CONFIGURATION,
        execution_timeout=QUALITY_DAG_TIMEOUT
    )

    end = EmptyOperator(task_id="end")

    (
        start
        >> trigger_extract
        >> trigger_transform
        >> trigger_load
        >> trigger_quality
        >> end
    )