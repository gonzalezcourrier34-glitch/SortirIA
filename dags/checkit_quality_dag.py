"""DAG de contrôle qualité CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_quality_dag.py` (commit 93632c5).

Lit le rapport de chargement, calcule les KPI dans PostgreSQL, produit le
rapport qualité puis vérifie les métriques selon les seuils configurés.

Les métriques détaillées restent dans le fichier partagé du lot. Seuls les
chemins, identifiants et statuts nécessaires transitent avec les XCom.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from airflow import DAG
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator

from src.logger import get_logger
from src.storage.files.storage_utils import (
    atomic_replace,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)
from src.utils.date_utils import get_extraction_date
from src.utils.parsing_utils import parse_non_negative_integer

logger = get_logger(__name__)


# Configuration

POSTGRES_CONN_ID = os.getenv(
    "CHECKIT_POSTGRES_CONN_ID",
    "checkit_postgres_conn"
)

LOTS_DIRECTORY = Path(
    os.getenv("CHECKIT_SHARED_LOTS_DIR", "/opt/airflow/shared/lots")
)

DEFAULT_QUALITY_THRESHOLDS: dict[str, float] = {
    "minimum_article_count": 1.0,
    "minimum_valid_article_rate": 95.0,
    "maximum_missing_title_rate": 0.0,
    "maximum_missing_content_rate": 5.0,
    "maximum_duplicate_url_rate": 0.0,
    "maximum_invalid_image_rate": 20.0
}

PREPARATION_TIMEOUT = timedelta(minutes=5)
CALCULATION_TIMEOUT = timedelta(minutes=20)
VALIDATION_TIMEOUT = timedelta(minutes=5)


# Gestion du lot

def normalize_batch_id(value: Any) -> str:
    """Transforme un identifiant Airflow en nom de dossier sûr."""

    normalized_value = "".join(
        character if character.isalnum() or character in {"_", "-", "."} else "_"
        for character in str(value)
    )
    return normalized_value.strip("._-") or "unknown_batch"


def resolve_batch_id(context: dict[str, Any]) -> str:
    """Retourne l'identifiant partagé du lot courant."""

    dag_run = context["dag_run"]
    configuration = dict(dag_run.conf or {})

    return normalize_batch_id(
        configuration.get("batch_id")
        or configuration.get("parent_run_id")
        or context["run_id"]
    )


def get_batch_directory(batch_id: str) -> Path:
    """Retourne et prépare le dossier partagé du lot."""

    batch_directory = LOTS_DIRECTORY / batch_id
    ensure_directory_exists(batch_directory)
    return batch_directory


def resolve_input_file(
    *,
    configuration: dict[str, Any],
    batch_directory: Path,
    configuration_key: str,
    default_filename: str
) -> Path:
    """Résout un fichier depuis la configuration ou le dossier du lot."""

    configured_path = configuration.get(configuration_key)

    return (
        Path(str(configured_path))
        if configured_path
        else batch_directory / default_filename
    )


def build_batch_paths(
    *,
    configuration: dict[str, Any],
    batch_directory: Path
) -> dict[str, Path]:
    """Construit les chemins utilisés par le contrôle qualité."""

    return {
        "load_report": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="load_report_file",
            default_filename="03_load_report.json"
        ),
        "quality_report": batch_directory / "04_quality_report.json"
    }


# =============================================================================
# Lecture et écriture JSON

def read_json_file(file_path: Path, *, expected_type: type) -> Any:
    """Lit un document JSON et vérifie son type."""

    if not file_path.exists():
        raise FileNotFoundError(
            f"Fichier JSON introuvable : {file_path}."
        )

    if not file_path.is_file():
        raise FileNotFoundError(
            f"Le chemin n'est pas un fichier : {file_path}."
        )

    try:
        with file_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Document JSON invalide : {file_path}."
        ) from error

    except (OSError, UnicodeDecodeError) as error:
        raise OSError(
            f"Impossible de lire le fichier JSON : {file_path}."
        ) from error

    if not isinstance(payload, expected_type):
        raise TypeError(
            f"Le fichier {file_path.name!r} doit contenir un objet "
            f"de type {expected_type.__name__}."
        )

    return payload


def write_json_file(file_path: Path, payload: Any) -> None:
    """Écrit un document JSON de manière atomique."""

    ensure_directory_exists(file_path.parent)
    temporary_path = create_temporary_path(file_path)

    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, default=str)
            file.write("\n")

        atomic_replace(temporary_path, file_path)

    finally:
        remove_file_if_exists(temporary_path)


# Validation commune

def validate_report_batch_id(
    *,
    report: dict[str, Any],
    expected_batch_id: str,
    report_name: str
) -> None:
    """Vérifie qu'un rapport appartient au lot courant."""

    reported_batch_id = report.get("batch_id")

    if reported_batch_id is None:
        return

    normalized_batch_id = normalize_batch_id(reported_batch_id)

    if normalized_batch_id != expected_batch_id:
        raise ValueError(
            f"Le rapport {report_name!r} appartient au lot "
            f"{normalized_batch_id!r}, mais le lot attendu est "
            f"{expected_batch_id!r}."
        )


def safe_rate(
    numerator: int | float,
    denominator: int | float
) -> float:
    """Calcule un pourcentage sans division par zéro."""

    if not denominator:
        return 0.0

    return round(
        100.0 * float(numerator) / float(denominator),
        2
    )


def safe_count(value: Any) -> int:
    """Convertit une valeur en compteur positif ou nul."""

    return parse_non_negative_integer(value, default=0)


def calculate_total_duration(started_at: Any) -> float:
    """Calcule la durée totale depuis une date ISO 8601."""

    try:
        parsed_start = datetime.fromisoformat(str(started_at))

        if parsed_start.tzinfo is None:
            parsed_start = parsed_start.replace(tzinfo=UTC)

        return round(
            (datetime.now(UTC) - parsed_start.astimezone(UTC)).total_seconds(),
            3
        )

    except (TypeError, ValueError, OverflowError):
        return 0.0


# Seuils qualité

def get_quality_thresholds(context: dict[str, Any]) -> dict[str, float]:
    """Retourne et contrôle les seuils qualité du run."""

    thresholds = dict(DEFAULT_QUALITY_THRESHOLDS)
    dag_run = context["dag_run"]
    configuration = dict(dag_run.conf or {})
    configured_thresholds = configuration.get("quality_thresholds")

    if configured_thresholds is None:
        return thresholds

    if not isinstance(configured_thresholds, dict):
        raise TypeError(
            "quality_thresholds doit être un dictionnaire."
        )

    for threshold_name, threshold_value in configured_thresholds.items():
        if threshold_name not in thresholds:
            logger.warning(
                "Seuil qualité inconnu ignoré : %s.",
                threshold_name
            )
            continue

        try:
            numeric_value = float(threshold_value)

        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                f"Valeur invalide pour le seuil {threshold_name!r}."
            ) from error

        if numeric_value < 0:
            raise ValueError(
                f"Le seuil {threshold_name!r} ne peut pas être négatif."
            )

        if (
            threshold_name != "minimum_article_count"
            and numeric_value > 100
        ):
            raise ValueError(
                f"Le seuil {threshold_name!r} ne peut pas dépasser 100 %."
            )

        thresholds[threshold_name] = numeric_value

    return thresholds


# Requêtes PostgreSQL

def fetch_one(
    cursor: Any,
    query: str,
    parameters: tuple[Any, ...]
) -> tuple[Any, ...]:
    """Exécute une requête et exige une ligne de résultat."""

    cursor.execute(query, parameters)
    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "La requête KPI n'a retourné aucun résultat."
        )

    return row


def fetch_quality_metrics(pipeline_run_id: str) -> dict[str, Any]:
    """Calcule les KPI du lot chargé dans PostgreSQL."""

    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    connection = hook.get_conn()

    try:
        with connection.cursor() as cursor:
            pipeline_row = fetch_one(
                cursor,
                """
                SELECT
                    id,
                    dag_id,
                    airflow_run_id,
                    pipeline_name,
                    pipeline_version,
                    status,
                    started_at,
                    finished_at,
                    extraction_duration_seconds,
                    transformation_duration_seconds,
                    load_duration_seconds,
                    duration_seconds,
                    extracted_count,
                    transformed_count,
                    valid_count,
                    rejected_count,
                    loaded_count,
                    images_downloaded_count,
                    images_valid_count,
                    images_invalid_count,
                    error_message
                FROM checkit.pipeline_runs
                WHERE id = %s;
                """,
                (pipeline_run_id,)
            )

            pipeline_columns = [
                description.name
                for description in cursor.description
            ]
            pipeline_metrics = dict(
                zip(pipeline_columns, pipeline_row, strict=True)
            )

            article_row = fetch_one(
                cursor,
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (
                        WHERE data_quality_status = 'valid'
                    ),
                    COUNT(*) FILTER (
                        WHERE title IS NULL OR BTRIM(title) = ''
                    ),
                    COUNT(*) FILTER (
                        WHERE content IS NULL OR BTRIM(content) = ''
                    ),
                    COUNT(*) FILTER (
                        WHERE canonical_url IS NULL
                           OR BTRIM(canonical_url) = ''
                    ),
                    COUNT(*) FILTER (
                        WHERE language IS NULL OR BTRIM(language) = ''
                    )
                FROM checkit.articles
                WHERE pipeline_run_id = %s;
                """,
                (pipeline_run_id,)
            )

            (
                article_count,
                valid_article_count,
                missing_title_count,
                missing_content_count,
                missing_canonical_url_count,
                missing_language_count
            ) = map(safe_count, article_row)

            duplicate_row = fetch_one(
                cursor,
                """
                SELECT COALESCE(SUM(duplicate_count - 1), 0)
                FROM (
                    SELECT canonical_url, COUNT(*) AS duplicate_count
                    FROM checkit.articles
                    WHERE pipeline_run_id = %s
                      AND canonical_url IS NOT NULL
                      AND BTRIM(canonical_url) <> ''
                    GROUP BY canonical_url
                    HAVING COUNT(*) > 1
                ) AS duplicate_urls;
                """,
                (pipeline_run_id,)
            )
            duplicate_url_count = safe_count(duplicate_row[0])

            image_row = fetch_one(
                cursor,
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (
                        WHERE images.is_valid IS TRUE
                    ),
                    COUNT(*) FILTER (
                        WHERE images.is_valid IS FALSE
                    ),
                    COUNT(*) FILTER (
                        WHERE images.is_valid IS NULL
                    ),
                    COUNT(*) FILTER (
                        WHERE images.local_path IS NULL
                           OR BTRIM(images.local_path) = ''
                    )
                FROM checkit.images AS images
                INNER JOIN checkit.articles AS articles
                    ON articles.id = images.article_id
                WHERE articles.pipeline_run_id = %s;
                """,
                (pipeline_run_id,)
            )

            (
                image_count,
                valid_image_count,
                invalid_image_count,
                unknown_image_status_count,
                missing_local_path_count
            ) = map(safe_count, image_row)

            label_row = fetch_one(
                cursor,
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (
                        WHERE labels.is_ground_truth IS TRUE
                    )
                FROM checkit.article_labels AS labels
                INNER JOIN checkit.articles AS articles
                    ON articles.id = labels.article_id
                WHERE articles.pipeline_run_id = %s;
                """,
                (pipeline_run_id,)
            )
            label_count, ground_truth_label_count = map(
                safe_count,
                label_row
            )

            feature_row = fetch_one(
                cursor,
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (
                        WHERE features.feature_name = 'is_multimodal'
                          AND features.boolean_value IS TRUE
                    )
                FROM checkit.article_features AS features
                INNER JOIN checkit.articles AS articles
                    ON articles.id = features.article_id
                WHERE articles.pipeline_run_id = %s;
                """,
                (pipeline_run_id,)
            )
            feature_count, multimodal_article_count = map(
                safe_count,
                feature_row
            )

            cursor.execute(
                """
                SELECT COALESCE(language, 'unknown'), COUNT(*)
                FROM checkit.articles
                WHERE pipeline_run_id = %s
                GROUP BY COALESCE(language, 'unknown')
                ORDER BY COUNT(*) DESC, COALESCE(language, 'unknown');
                """,
                (pipeline_run_id,)
            )
            language_distribution = {
                str(language): safe_count(count)
                for language, count in cursor.fetchall()
            }

            cursor.execute(
                """
                SELECT
                    sources.source_key,
                    sources.display_name,
                    COUNT(*)
                FROM checkit.articles AS articles
                INNER JOIN checkit.sources AS sources
                    ON sources.id = articles.source_id
                WHERE articles.pipeline_run_id = %s
                GROUP BY sources.source_key, sources.display_name
                ORDER BY COUNT(*) DESC, sources.source_key;
                """,
                (pipeline_run_id,)
            )
            source_distribution = [
                {
                    "source_key": str(source_key),
                    "display_name": str(display_name),
                    "article_count": safe_count(count)
                }
                for source_key, display_name, count in cursor.fetchall()
            ]

    finally:
        connection.close()

    evaluated_image_count = valid_image_count + invalid_image_count

    metrics: dict[str, Any] = {
        "pipeline": pipeline_metrics,
        "articles": {
            "article_count": article_count,
            "valid_article_count": valid_article_count,
            "missing_title_count": missing_title_count,
            "missing_content_count": missing_content_count,
            "missing_canonical_url_count": missing_canonical_url_count,
            "missing_language_count": missing_language_count,
            "duplicate_url_count": duplicate_url_count
        },
        "images": {
            "image_count": image_count,
            "evaluated_image_count": evaluated_image_count,
            "valid_image_count": valid_image_count,
            "invalid_image_count": invalid_image_count,
            "unknown_image_status_count": unknown_image_status_count,
            "missing_local_path_count": missing_local_path_count
        },
        "labels": {
            "label_count": label_count,
            "ground_truth_label_count": ground_truth_label_count
        },
        "features": {
            "feature_count": feature_count,
            "multimodal_article_count": multimodal_article_count
        },
        "distributions": {
            "languages": language_distribution,
            "sources": source_distribution
        }
    }

    metrics["rates"] = {
        "valid_article_rate": safe_rate(
            valid_article_count,
            article_count
        ),
        "missing_title_rate": safe_rate(
            missing_title_count,
            article_count
        ),
        "missing_content_rate": safe_rate(
            missing_content_count,
            article_count
        ),
        "missing_canonical_url_rate": safe_rate(
            missing_canonical_url_count,
            article_count
        ),
        "duplicate_url_rate": safe_rate(
            duplicate_url_count,
            article_count
        ),
        "article_with_image_rate": safe_rate(
            image_count,
            article_count
        ),
        "valid_image_rate": safe_rate(
            valid_image_count,
            evaluated_image_count
        ),
        "invalid_image_rate": safe_rate(
            invalid_image_count,
            evaluated_image_count
        ),
        "unknown_image_status_rate": safe_rate(
            unknown_image_status_count,
            image_count
        ),
        "missing_local_path_rate": safe_rate(
            missing_local_path_count,
            image_count
        ),
        "labeled_article_rate": safe_rate(
            label_count,
            article_count
        ),
        "ground_truth_article_rate": safe_rate(
            ground_truth_label_count,
            article_count
        ),
        "multimodal_article_rate": safe_rate(
            multimodal_article_count,
            article_count
        )
    }

    return metrics


# Violations qualité

def add_maximum_violation(
    *,
    violations: list[str],
    rate_name: str,
    label: str,
    rates: dict[str, Any],
    thresholds: dict[str, Any]
) -> None:
    """Ajoute une violation lorsqu'un taux dépasse son maximum."""

    threshold_name = f"maximum_{rate_name}"

    if threshold_name not in thresholds:
        raise KeyError(
            f"Seuil qualité absent : {threshold_name}."
        )

    try:
        current_value = float(rates.get(rate_name, 0.0))
        maximum_value = float(thresholds[threshold_name])

    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"Valeur invalide pour le contrôle {rate_name!r}."
        ) from error

    if current_value > maximum_value:
        violations.append(
            f"{label} : {current_value} % > {maximum_value} %."
        )


def find_quality_violations(
    *,
    metrics: dict[str, Any],
    thresholds: dict[str, Any]
) -> list[str]:
    """Compare les métriques aux seuils et retourne les violations."""

    rates = metrics.get("rates")
    articles = metrics.get("articles")
    images = metrics.get("images")

    if not isinstance(rates, dict):
        raise TypeError(
            "Les taux qualité sont absents ou invalides."
        )

    if not isinstance(articles, dict):
        raise TypeError(
            "Les métriques d'articles sont absentes ou invalides."
        )

    if not isinstance(images, dict):
        raise TypeError(
            "Les métriques d'images sont absentes ou invalides."
        )

    violations: list[str] = []
    article_count = safe_count(articles.get("article_count"))

    try:
        minimum_article_count = float(
            thresholds["minimum_article_count"]
        )
        minimum_valid_rate = float(
            thresholds["minimum_valid_article_rate"]
        )
        valid_article_rate = float(
            rates.get("valid_article_rate", 0.0)
        )

    except KeyError as error:
        raise KeyError(
            f"Seuil qualité absent : {error.args[0]}."
        ) from error

    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            "Les métriques ou seuils minimums sont invalides."
        ) from error

    if article_count < minimum_article_count:
        violations.append(
            "Nombre d'articles insuffisant : "
            f"{article_count} < {minimum_article_count}."
        )

    if valid_article_rate < minimum_valid_rate:
        violations.append(
            "Taux d'articles valides insuffisant : "
            f"{valid_article_rate} % < {minimum_valid_rate} %."
        )

    add_maximum_violation(
        violations=violations,
        rate_name="missing_title_rate",
        label="Taux de titres manquants trop élevé",
        rates=rates,
        thresholds=thresholds
    )
    add_maximum_violation(
        violations=violations,
        rate_name="missing_content_rate",
        label="Taux de contenus manquants trop élevé",
        rates=rates,
        thresholds=thresholds
    )
    add_maximum_violation(
        violations=violations,
        rate_name="duplicate_url_rate",
        label="Taux d'URL dupliquées trop élevé",
        rates=rates,
        thresholds=thresholds
    )

    # Le taux d'images invalides n'est contrôlé que lorsqu'au moins une image
    # possède un statut de validation exploitable.
    if safe_count(images.get("evaluated_image_count")) > 0:
        add_maximum_violation(
            violations=violations,
            rate_name="invalid_image_rate",
            label="Taux d'images invalides trop élevé",
            rates=rates,
            thresholds=thresholds
        )

    return violations


# Tâche 1 : préparation du lot

def prepare_quality_batch_task(**context: Any) -> dict[str, Any]:
    """Contrôle le rapport de chargement et prépare le calcul des KPI."""

    started_at = perf_counter()
    batch_started_at = datetime.now(UTC)
    batch_id = resolve_batch_id(context)
    dag_run = context["dag_run"]
    configuration = dict(dag_run.conf or {})
    batch_directory = get_batch_directory(batch_id)
    paths = build_batch_paths(
        configuration=configuration,
        batch_directory=batch_directory
    )

    logger.info(
        "Préparation du contrôle qualité du lot %s.",
        batch_id
    )

    load_report = read_json_file(
        paths["load_report"],
        expected_type=dict
    )

    validate_report_batch_id(
        report=load_report,
        expected_batch_id=batch_id,
        report_name=paths["load_report"].name
    )

    pipeline_run_id = load_report.get("pipeline_run_id")

    if pipeline_run_id is None or not str(pipeline_run_id).strip():
        raise KeyError(
            "pipeline_run_id est absent du rapport de chargement."
        )

    load_status = str(
        load_report.get("status", "")
    ).strip().lower()

    if load_status not in {"success", "partial_success"}:
        raise RuntimeError(
            f"Le rapport de chargement possède le statut {load_status!r}."
        )

    thresholds = get_quality_thresholds(context)

    logger.info(
        "Lot %s prêt pour le contrôle qualité, pipeline_run_id=%s.",
        batch_id,
        pipeline_run_id
    )

    return {
        "batch_id": batch_id,
        "batch_started_at": batch_started_at.isoformat(),
        "pipeline_run_id": str(pipeline_run_id),
        "load_report_file": str(paths["load_report"]),
        "quality_report_file": str(paths["quality_report"]),
        "thresholds": thresholds,
        "preparation_task_duration_seconds": round(
            perf_counter() - started_at,
            3
        )
    }


# Tâche 2 : calcul des KPI

def calculate_quality_task(**context: Any) -> dict[str, Any]:
    """Calcule les KPI PostgreSQL et écrit le rapport intermédiaire."""

    started_at = perf_counter()
    task_instance = context["ti"]
    batch_manifest = task_instance.xcom_pull(
        task_ids="prepare_batch"
    )

    if not isinstance(batch_manifest, dict):
        raise RuntimeError(
            "Le manifeste de préparation est absent ou invalide."
        )

    batch_id = normalize_batch_id(batch_manifest.get("batch_id"))
    pipeline_run_id = str(
        batch_manifest.get("pipeline_run_id", "")
    ).strip()
    load_report_file = Path(
        str(batch_manifest.get("load_report_file", ""))
    )
    quality_report_file = Path(
        str(batch_manifest.get("quality_report_file", ""))
    )
    thresholds = batch_manifest.get("thresholds")

    if not pipeline_run_id:
        raise KeyError(
            "pipeline_run_id est absent du manifeste de préparation."
        )

    if not isinstance(thresholds, dict):
        raise TypeError(
            "Les seuils qualité du manifeste sont invalides."
        )

    # Le rapport est relu afin de détecter une modification entre les tâches.
    load_report = read_json_file(
        load_report_file,
        expected_type=dict
    )

    validate_report_batch_id(
        report=load_report,
        expected_batch_id=batch_id,
        report_name=load_report_file.name
    )

    if str(load_report.get("pipeline_run_id", "")).strip() != pipeline_run_id:
        raise ValueError(
            "Le pipeline_run_id du rapport de chargement a changé."
        )

    logger.info(
        "Calcul des KPI du lot %s.",
        batch_id
    )

    metrics = fetch_quality_metrics(pipeline_run_id)
    calculation_duration = round(perf_counter() - started_at, 3)

    report = {
        "batch_id": batch_id,
        "pipeline_run_id": pipeline_run_id,
        "status": "calculated",
        "step": "quality",
        "step_status": "calculated",
        "load_report_file": str(load_report_file),
        "quality_report_file": str(quality_report_file),
        "metrics": metrics,
        "thresholds": thresholds,
        "preparation_task_duration_seconds": batch_manifest[
            "preparation_task_duration_seconds"
        ],
        "calculation_task_duration_seconds": calculation_duration,
        "generated_at": get_extraction_date()
    }
    write_json_file(quality_report_file, report)

    logger.info(
        "KPI du lot %s calculés : %s article(s), %.2f %% valide(s), "
        "%.2f %% multimodal(aux).",
        batch_id,
        metrics["articles"]["article_count"],
        metrics["rates"]["valid_article_rate"],
        metrics["rates"]["multimodal_article_rate"]
    )

    return {
        "batch_id": batch_id,
        "pipeline_run_id": pipeline_run_id,
        "quality_report_file": str(quality_report_file),
        "article_count": metrics["articles"]["article_count"],
        "valid_article_rate": metrics["rates"]["valid_article_rate"],
        "invalid_image_rate": metrics["rates"]["invalid_image_rate"],
        "calculation_task_duration_seconds": calculation_duration
    }


# Tâche 3 : validation des seuils

def validate_quality_task(**context: Any) -> dict[str, Any]:
    """Compare les KPI aux seuils et finalise le rapport qualité."""

    started_at = perf_counter()
    task_instance = context["ti"]
    batch_manifest = task_instance.xcom_pull(
        task_ids="prepare_batch"
    )
    quality_manifest = task_instance.xcom_pull(
        task_ids="calculate_quality_kpis"
    )

    if not isinstance(batch_manifest, dict):
        raise RuntimeError(
            "Le manifeste de préparation est absent ou invalide."
        )

    if not isinstance(quality_manifest, dict):
        raise RuntimeError(
            "Le manifeste de calcul qualité est absent ou invalide."
        )

    batch_id = normalize_batch_id(batch_manifest.get("batch_id"))
    quality_report_file = Path(
        str(quality_manifest.get("quality_report_file", ""))
    )
    report = read_json_file(
        quality_report_file,
        expected_type=dict
    )

    validate_report_batch_id(
        report=report,
        expected_batch_id=batch_id,
        report_name=quality_report_file.name
    )

    metrics = report.get("metrics")
    thresholds = report.get("thresholds")

    if not isinstance(metrics, dict):
        raise TypeError(
            "Le rapport ne contient pas de métriques valides."
        )

    if not isinstance(thresholds, dict):
        raise TypeError(
            "Le rapport ne contient pas de seuils valides."
        )

    violations = find_quality_violations(
        metrics=metrics,
        thresholds=thresholds
    )
    validation_duration = round(perf_counter() - started_at, 3)
    total_duration = calculate_total_duration(
        batch_manifest.get("batch_started_at")
    )
    quality_status = "failed" if violations else "passed"

    report.update({
        "status": quality_status,
        "step_status": quality_status,
        "quality_status": quality_status,
        "violations": violations,
        "validation_task_duration_seconds": validation_duration,
        "duration_seconds": total_duration,
        "validated_at": get_extraction_date()
    })
    write_json_file(quality_report_file, report)

    if violations:
        for violation in violations:
            logger.error(
                "Violation qualité du lot %s : %s",
                batch_id,
                violation
            )

        raise RuntimeError(
            "Le lot ne respecte pas les seuils qualité : "
            + " | ".join(violations)
        )

    logger.info(
        "Tous les contrôles qualité du lot %s sont validés en "
        "%.3f seconde(s).",
        batch_id,
        total_duration
    )

    return {
        "batch_id": batch_id,
        "pipeline_run_id": report["pipeline_run_id"],
        "quality_status": "passed",
        "quality_report_file": str(quality_report_file),
        "article_count": metrics["articles"]["article_count"],
        "valid_article_rate": metrics["rates"]["valid_article_rate"],
        "invalid_image_rate": metrics["rates"]["invalid_image_rate"]
    }


# Définition du DAG

DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 1,
    "retry_delay": timedelta(minutes=1)
}

with DAG(
    dag_id="checkit_quality",
    description="Calcule et contrôle les KPI qualité du pipeline.",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 7, 1, tzinfo=UTC),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    tags=["checkit", "etl", "quality", "kpi", "postgres"]
) as dag:

    start = EmptyOperator(task_id="start")

    # Étape 1 : contrôle le rapport de chargement et prépare les seuils.
    prepare_batch = PythonOperator(
        task_id="prepare_batch",
        python_callable=prepare_quality_batch_task,
        execution_timeout=PREPARATION_TIMEOUT
    )

    # Étape 2 : calcule les KPI du lot depuis PostgreSQL.
    calculate_quality = PythonOperator(
        task_id="calculate_quality_kpis",
        python_callable=calculate_quality_task,
        execution_timeout=CALCULATION_TIMEOUT
    )

    # Étape 3 : compare les KPI aux seuils configurés.
    validate_quality = PythonOperator(
        task_id="validate_quality_thresholds",
        python_callable=validate_quality_task,
        execution_timeout=VALIDATION_TIMEOUT
    )

    end = EmptyOperator(task_id="end")

    (
        start
        >> prepare_batch
        >> calculate_quality
        >> validate_quality
        >> end
    )