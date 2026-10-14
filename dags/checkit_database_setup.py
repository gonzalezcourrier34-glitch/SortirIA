"""DAG d'initialisation PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_database_setup.py` (commit 93632c5).

Ce DAG manuel installe les objets SQL nécessaires au projet puis vérifie
la présence du schéma, des tables, des vues et des routines principales.
Il reste indépendant du pipeline ETL quotidien.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from airflow import DAG
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator
from pendulum import datetime as pendulum_datetime

from src.logger import get_logger
from src.utils.parsing_utils import parse_non_negative_integer

logger = get_logger(__name__)

# Configuration

POSTGRES_ADMIN_CONN_ID = os.getenv(
    "CHECKIT_POSTGRES_ADMIN_CONN_ID",
    "checkit_postgres_admin"
)
SQL_DIRECTORY = Path(
    os.getenv("CHECKIT_SQL_DIR", "/opt/airflow/checkit_ai/sql")
)

SQL_SCRIPTS = (
    ("00_database", "00_database.sql"),
    ("01_articles", "01_articles.sql"),
    ("02_image_labels", "02_image_labels.sql"),
    ("03_features_predictions", "03_features_predictions.sql"),
    ("07_functions", "07_functions.sql"),
    ("04_indexes", "04_indexes.sql"),
    ("05_views", "05_views.sql"),
    ("08_procedures", "08_procedures.sql"),
    ("09_permissions", "09_permissions.sql")
)

REQUIRED_TABLES = {
    "articles",
    "images",
    "pipeline_runs",
    "sources"
}


# Scripts SQL

def read_sql_file(filename: str) -> str:
    """Lit un script SQL et ignore les commandes réservées à psql."""

    sql_path = SQL_DIRECTORY / filename

    if not sql_path.is_file():
        raise FileNotFoundError(f"Script SQL introuvable : {sql_path}")

    try:
        sql_lines = sql_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise OSError(f"Impossible de lire le script SQL : {sql_path}") from error

    executable_lines = [
        line for line in sql_lines
        if not line.lstrip().startswith("\\")
    ]
    sql_content = "\n".join(executable_lines).strip()

    if not sql_content:
        raise ValueError(f"Le script SQL est vide : {sql_path}")

    return sql_content


def execute_sql_script(filename: str) -> None:
    """Exécute un script SQL avec la connexion administrateur."""

    logger.info("Début de l'exécution du script %s.", filename)
    sql_content = read_sql_file(filename)

    postgres_hook = PostgresHook(postgres_conn_id=POSTGRES_ADMIN_CONN_ID)
    postgres_hook.run(sql=sql_content, autocommit=False)

    logger.info("Script %s exécuté avec succès.", filename)


# Vérification

def get_first_count(postgres_hook: PostgresHook, query: str) -> int:
    """Exécute une requête de comptage et normalise son résultat."""

    result = postgres_hook.get_first(query)
    return parse_non_negative_integer(
        result[0] if result else 0,
        default=0
    )


def get_schema_tables(postgres_hook: PostgresHook) -> set[str]:
    """Retourne les tables présentes dans le schéma checkit."""

    records = postgres_hook.get_records(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'checkit'
          AND table_type = 'BASE TABLE';
        """
    )
    return {str(record[0]) for record in records if record and record[0]}


def verify_database() -> dict[str, Any]:
    """Vérifie la présence des principaux objets PostgreSQL."""

    postgres_hook = PostgresHook(postgres_conn_id=POSTGRES_ADMIN_CONN_ID)

    schema_result = postgres_hook.get_first(
        """
        SELECT EXISTS(
            SELECT 1
            FROM information_schema.schemata
            WHERE schema_name = 'checkit'
        );
        """
    )
    schema_exists = bool(schema_result and schema_result[0])

    if not schema_exists:
        raise RuntimeError("Le schéma PostgreSQL 'checkit' est introuvable.")

    tables = get_schema_tables(postgres_hook)
    missing_tables = sorted(REQUIRED_TABLES - tables)

    view_count = get_first_count(
        postgres_hook,
        """
        SELECT COUNT(*)
        FROM information_schema.views
        WHERE table_schema = 'checkit';
        """
    )
    routine_count = get_first_count(
        postgres_hook,
        """
        SELECT COUNT(*)
        FROM information_schema.routines
        WHERE routine_schema = 'checkit';
        """
    )
    index_count = get_first_count(
        postgres_hook,
        """
        SELECT COUNT(*)
        FROM pg_indexes
        WHERE schemaname = 'checkit';
        """
    )

    logger.info(
        "Objets détectés : %s table(s), %s vue(s), %s routine(s), %s index.",
        len(tables),
        view_count,
        routine_count,
        index_count
    )

    if missing_tables:
        raise RuntimeError(
            "Table(s) PostgreSQL obligatoire(s) absente(s) : "
            + ", ".join(missing_tables)
        )

    return {
        "schema": "checkit",
        "table_count": len(tables),
        "view_count": view_count,
        "routine_count": routine_count,
        "index_count": index_count,
        "required_tables": sorted(REQUIRED_TABLES)
    }


# DAG

DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 1,
    "retry_delay": timedelta(seconds=30)
}

with DAG(
    dag_id="checkit_database_setup",
    description=(
        "Installe et vérifie les objets PostgreSQL nécessaires à CheckIt.AI."
    ),
    default_args=DEFAULT_ARGS,
    start_date=pendulum_datetime(2026, 7, 1, tz="UTC"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["checkit", "database", "setup", "postgres"]
) as dag:
    start = EmptyOperator(task_id="start")
    previous_task = start

    for task_id, filename in SQL_SCRIPTS:
        current_task = PythonOperator(
            task_id=task_id,
            python_callable=execute_sql_script,
            op_kwargs={"filename": filename}
        )
        previous_task >> current_task
        previous_task = current_task

    verify = PythonOperator(
        task_id="verify_database",
        python_callable=verify_database
    )
    end = EmptyOperator(task_id="end")

    previous_task >> verify >> end