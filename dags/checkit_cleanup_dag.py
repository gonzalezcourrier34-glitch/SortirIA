"""DAG manuel de nettoyage PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_cleanup_dag.py` (commit 93632c5).

Vide les tables métier du schéma CheckIt.AI sans supprimer le schéma,
ses objets SQL ni les tables internes d'Apache Airflow.

Le nettoyage exige deux confirmations explicites :
- confirm_cleanup = true ;
- confirmation_phrase = DELETE_ALL_CHECKIT_DATA.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, Param

from src.logger import get_logger
from src.storage.files.storage_utils import (
    atomic_replace,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists,
)
from src.utils.date_utils import get_current_datetime
from src.utils.parsing_utils import parse_non_negative_integer

logger = get_logger(__name__)

# Configuration
POSTGRES_CONN_ID = os.getenv(
    "CHECKIT_POSTGRES_CONN_ID", "checkit_postgres_conn"
).strip()
DATABASE_SCHEMA = os.getenv("CHECKIT_DATABASE_SCHEMA", "checkit").strip()
CONFIRMATION_PHRASE = os.getenv(
    "CHECKIT_CLEANUP_CONFIRMATION_PHRASE", "DELETE_ALL_CHECKIT_DATA"
).strip()
REPORT_DIRECTORY = Path(
    os.getenv(
        "CHECKIT_MAINTENANCE_REPORT_DIR",
        "/opt/airflow/shared/maintenance",
    )
)
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLES_TO_CLEAN = (
    "model_predictions",
    "article_features",
    "article_labels",
    "images",
    "articles",
    "pipeline_metrics",
    "etl_logs",
    "dataset_versions",
    "pipeline_runs",
    "sources",
    "pipeline_configuration",
)


# Utilitaires
def validate_sql_identifier(identifier: str, field_name: str) -> str:
    """Valide un identifiant SQL contrôlé par la configuration."""

    normalized_identifier = str(identifier).strip()
    if not normalized_identifier:
        raise ValueError(f"{field_name} ne peut pas être vide.")
    if not SQL_IDENTIFIER_PATTERN.fullmatch(normalized_identifier):
        raise ValueError(
            f"{field_name} contient un identifiant SQL invalide : "
            f"{normalized_identifier!r}."
        )
    return normalized_identifier


def quote_sql_identifier(identifier: str) -> str:
    """Protège un identifiant SQL préalablement validé."""

    return f'"{validate_sql_identifier(identifier, "Identifiant SQL")}"'


def write_json_file(file_path: Path, payload: Any) -> None:
    """Écrit un rapport JSON de manière atomique."""

    ensure_directory_exists(file_path.parent)
    temporary_path = create_temporary_path(file_path)
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, default=str)
            file.write("\n")
        atomic_replace(temporary_path, file_path)
    finally:
        remove_file_if_exists(temporary_path)


# Configuration du lancement
def get_dag_configuration(context: dict[str, Any]) -> dict[str, Any]:
    """Retourne les paramètres effectifs transmis par Airflow."""

    parameters = context.get("params") or {}
    if not isinstance(parameters, dict):
        raise TypeError(
            "Les paramètres effectifs du DAG doivent être un dictionnaire."
        )
    return dict(parameters)


def validate_cleanup_confirmation(configuration: dict[str, Any]) -> None:
    """Vérifie la double confirmation obligatoire."""

    confirm_cleanup = configuration.get("confirm_cleanup")
    confirmation_phrase = str(
        configuration.get("confirmation_phrase", "")
    ).strip()
    if confirm_cleanup is not True:
        raise PermissionError(
            "Nettoyage refusé : confirm_cleanup doit être égal à true."
        )
    if confirmation_phrase != CONFIRMATION_PHRASE:
        raise PermissionError(
            "Nettoyage refusé : confirmation_phrase invalide. "
            f"Valeur attendue : {CONFIRMATION_PHRASE!r}."
        )


# Inspection PostgreSQL
def get_existing_tables(connection: Any) -> list[str]:
    """Retourne les tables configurées réellement présentes."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE';
            """,
            (DATABASE_SCHEMA,),
        )
        existing_tables = {
            str(row[0]) for row in cursor.fetchall() if row and row[0]
        }
    return [table for table in TABLES_TO_CLEAN if table in existing_tables]


def count_table_rows(
    connection: Any,
    table_names: list[str],
) -> dict[str, int]:
    """Compte les lignes des tables contrôlées par la liste interne."""

    quoted_schema = quote_sql_identifier(DATABASE_SCHEMA)
    row_counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table_name in table_names:
            quoted_table = quote_sql_identifier(table_name)
            cursor.execute(
                f"SELECT COUNT(*) FROM {quoted_schema}.{quoted_table};"
            )
            result = cursor.fetchone()
            row_counts[table_name] = parse_non_negative_integer(
                result[0] if result else 0,
                default=0,
            )
    return row_counts


def build_truncate_statement(table_names: list[str]) -> str:
    """Construit le TRUNCATE depuis les tables autorisées."""

    if not table_names:
        raise ValueError("Aucune table fournie pour le nettoyage.")

    unexpected_tables = [
        table for table in table_names if table not in TABLES_TO_CLEAN
    ]
    if unexpected_tables:
        raise ValueError(
            "Tables non autorisées pour le nettoyage : "
            f"{', '.join(unexpected_tables)}."
        )

    quoted_schema = quote_sql_identifier(DATABASE_SCHEMA)
    qualified_tables = ",\n".join(
        f"{quoted_schema}.{quote_sql_identifier(table)}"
        for table in table_names
    )
    return (
        "TRUNCATE TABLE\n"
        f"{qualified_tables}\n"
        "RESTART IDENTITY CASCADE;"
    )


# Nettoyage transactionnel
def clean_database_task(**context: Any) -> dict[str, Any]:
    """Vide les tables métier dans une transaction PostgreSQL unique."""

    validate_sql_identifier(DATABASE_SCHEMA, "CHECKIT_DATABASE_SCHEMA")
    validate_cleanup_confirmation(get_dag_configuration(context))

    started_at = get_current_datetime()
    postgres_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    connection = postgres_hook.get_conn()
    existing_tables: list[str] = []
    missing_tables: list[str] = []
    rows_before: dict[str, int] = {}
    rows_after: dict[str, int] = {}
    total_rows_before = 0

    try:
        connection.autocommit = False

        # Inspecte les tables disponibles
        existing_tables = get_existing_tables(connection)
        if not existing_tables:
            raise RuntimeError(
                "Aucune table métier configurée n'a été trouvée dans "
                f"le schéma {DATABASE_SCHEMA!r}."
            )

        missing_tables = [
            table for table in TABLES_TO_CLEAN if table not in existing_tables
        ]
        if missing_tables:
            logger.warning(
                "Tables configurées mais absentes : %s.",
                ", ".join(missing_tables),
            )

        # Mesure l'état initial
        rows_before = count_table_rows(connection, existing_tables)
        total_rows_before = sum(rows_before.values())
        logger.warning(
            "Nettoyage du schéma %s : %s ligne(s) seront supprimée(s).",
            DATABASE_SCHEMA,
            total_rows_before,
        )
        logger.info("Tables concernées : %s.", ", ".join(existing_tables))

        # Nettoie puis contrôle le résultat
        with connection.cursor() as cursor:
            cursor.execute(build_truncate_statement(existing_tables))

        rows_after = count_table_rows(connection, existing_tables)
        remaining_rows = sum(rows_after.values())
        if remaining_rows:
            raise RuntimeError(
                "Le contrôle après nettoyage a détecté "
                f"{remaining_rows} ligne(s) restante(s)."
            )
        connection.commit()

    except Exception:
        connection.rollback()
        logger.exception(
            "Échec du nettoyage PostgreSQL. "
            "La transaction a été intégralement annulée."
        )
        raise
    finally:
        connection.close()

    # Génère le rapport
    completed_at = get_current_datetime()
    report_file = REPORT_DIRECTORY / (
        f"cleanup_{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report = {
        "dag_id": context["dag"].dag_id,
        "airflow_run_id": context["run_id"],
        "postgres_connection_id": POSTGRES_CONN_ID,
        "database_schema": DATABASE_SCHEMA,
        "status": "success",
        "tables_configured": list(TABLES_TO_CLEAN),
        "tables_cleaned": existing_tables,
        "missing_tables": missing_tables,
        "rows_before": rows_before,
        "rows_after": rows_after,
        "deleted_row_count": total_rows_before,
        "restart_identity": True,
        "cascade": True,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "report_file": str(report_file),
    }
    write_json_file(report_file, report)
    logger.warning(
        "Nettoyage terminé : %s ligne(s) supprimée(s) dans %s table(s).",
        total_rows_before,
        len(existing_tables),
    )
    logger.info("Rapport de nettoyage écrit dans %s.", report_file)
    return report


# Définition du DAG
DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 0,
    "retry_delay": timedelta(minutes=1),
}

with DAG(
    dag_id="checkit_cleanup_database",
    description="Vide manuellement les tables métier PostgreSQL CheckIt.AI.",
    default_args=DEFAULT_ARGS,
    schedule=None,
    start_date=datetime(2026, 7, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=15),
    params={
        "confirm_cleanup": Param(
            default=False,
            type="boolean",
            title="Confirmer le nettoyage",
            description="Active explicitement le nettoyage destructif.",
        ),
        "confirmation_phrase": Param(
            default="",
            type="string",
            title="Phrase de confirmation",
            description="Saisir exactement : DELETE_ALL_CHECKIT_DATA",
        ),
    },
    tags=["checkit", "maintenance", "cleanup", "postgres", "destructive"],
) as dag:
    start = EmptyOperator(task_id="start")
    clean_database = PythonOperator(
        task_id="truncate_checkit_tables",
        python_callable=clean_database_task,
    )
    end = EmptyOperator(task_id="end")

    start >> clean_database >> end