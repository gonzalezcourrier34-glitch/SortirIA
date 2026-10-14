"""DAG minimal de test PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/test_checkit_postgres.py` (commit 93632c5).

Ce DAG manuel vérifie la connexion Airflow, le provider PostgreSQL,
l'accessibilité de la base et l'identité de la connexion utilisée.
"""

from __future__ import annotations

import os
from typing import Any

from airflow import DAG
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.standard.operators.python import PythonOperator
from pendulum import datetime as pendulum_datetime

from src.logger import get_logger

logger = get_logger(__name__)

# Configuration

POSTGRES_CONN_ID = os.getenv(
    "CHECKIT_POSTGRES_CONN_ID",
    "checkit_postgres_conn"
)
EXPECTED_DATABASE = os.getenv(
    "CHECKIT_POSTGRES_DATABASE",
    "checkit"
)
EXPECTED_USER = os.getenv(
    "CHECKIT_POSTGRES_USER",
    "checkit_etl"
)
EXPECTED_SCHEMA = os.getenv(
    "CHECKIT_DATABASE_SCHEMA",
    "checkit"
)


# Test PostgreSQL

def test_postgres_connection() -> dict[str, Any]:
    """Teste la connexion PostgreSQL et valide son contexte."""

    postgres_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    result = postgres_hook.get_first(
        """
        SELECT
            current_database(),
            current_user,
            current_schema(),
            version();
        """
    )

    if not result:
        raise RuntimeError(
            "La requête PostgreSQL n'a retourné aucun résultat."
        )

    database_name, database_user, schema_name, postgres_version = result

    logger.info("Connexion PostgreSQL réussie.")
    logger.info("Connexion Airflow : %s.", POSTGRES_CONN_ID)
    logger.info("Base de données : %s.", database_name)
    logger.info("Utilisateur : %s.", database_user)
    logger.info("Schéma courant : %s.", schema_name)
    logger.info("Version PostgreSQL : %s.", postgres_version)

    errors: list[str] = []

    if database_name != EXPECTED_DATABASE:
        errors.append(
            f"base attendue {EXPECTED_DATABASE!r}, reçue {database_name!r}"
        )

    if database_user != EXPECTED_USER:
        errors.append(
            f"utilisateur attendu {EXPECTED_USER!r}, reçu {database_user!r}"
        )

    if schema_name != EXPECTED_SCHEMA:
        errors.append(
            f"schéma attendu {EXPECTED_SCHEMA!r}, reçu {schema_name!r}"
        )

    if errors:
        raise RuntimeError(
            "Connexion PostgreSQL invalide : " + " ; ".join(errors) + "."
        )

    return {
        "status": "success",
        "connection_id": POSTGRES_CONN_ID,
        "database": database_name,
        "user": database_user,
        "schema": schema_name,
        "version": postgres_version
    }


# DAG

with DAG(
    dag_id="test_checkit_postgres",
    description="Teste la connexion Airflow vers PostgreSQL CheckIt.AI.",
    schedule=None,
    start_date=pendulum_datetime(2026, 7, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["checkit", "postgres", "test"]
) as dag:
    test_connection = PythonOperator(
        task_id="test_postgres_connection",
        python_callable=test_postgres_connection
    )