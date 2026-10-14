"""DAG de chargement PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_load_dag.py` (commit 93632c5).

Lit les collections produites par checkit_transform, contrôle leur cohérence,
les charge dans PostgreSQL puis produit un rapport léger de chargement.

Les données volumineuses restent dans le dossier partagé du lot. Seuls les
chemins, compteurs et identifiants nécessaires transitent avec les XCom.
"""

from __future__ import annotations

import json
import os
from collections import Counter
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
from src.storage.postgres.postgres_load_service import load_batch_to_postgres
from src.utils.date_utils import get_extraction_date
from src.utils.parsing_utils import parse_boolean, parse_non_negative_integer

logger = get_logger(__name__)


# Configuration

POSTGRES_CONN_ID = os.getenv(
    "CHECKIT_POSTGRES_CONN_ID",
    "checkit_postgres_conn"
)

LOTS_DIRECTORY = Path(
    os.getenv("CHECKIT_SHARED_LOTS_DIR", "/opt/airflow/shared/lots")
)

PREPARATION_TIMEOUT = timedelta(minutes=10)
LOAD_TIMEOUT = timedelta(minutes=25)
FINALIZATION_TIMEOUT = timedelta(minutes=5)

ALLOWED_IMAGE_VALIDATION_STATUSES = {
    "pending",
    "valid",
    "invalid",
    "validation_error"
}


# Gestion du lot

def normalize_batch_id(value: Any) -> str:
    """Transforme un identifiant Airflow en nom de dossier sûr."""

    normalized_value = "".join(
        character if character.isalnum() or character in {"_", "-", "."}
        else "_"
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
    """Résout un fichier d'entrée depuis la configuration ou le lot."""

    configured_path = configuration.get(configuration_key)

    if configured_path:
        return Path(str(configured_path))

    return batch_directory / default_filename


def build_batch_paths(
    *,
    configuration: dict[str, Any],
    batch_directory: Path
) -> dict[str, Path]:
    """Construit les chemins utilisés par le chargement."""

    return {
        "articles": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="articles_file",
            default_filename="02_articles_ready.json"
        ),
        "images": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="images_file",
            default_filename="02_images_ready.json"
        ),
        "labels": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="labels_file",
            default_filename="02_labels_ready.json"
        ),
        "features": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="features_file",
            default_filename="02_features_ready.json"
        ),
        "transformation_report": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="transformation_report_file",
            default_filename="02_transformation_report.json"
        ),
        "extraction_report": resolve_input_file(
            configuration=configuration,
            batch_directory=batch_directory,
            configuration_key="extraction_report_file",
            default_filename="01_extraction_report.json"
        ),
        "load_result": batch_directory / "03_load_result.tmp.json",
        "load_report": batch_directory / "03_load_report.json"
    }


# Lecture et écriture JSON

def read_json_file(
    file_path: Path,
    *,
    expected_type: type,
    optional: bool = False
) -> Any:
    """Lit un fichier JSON et contrôle le type du document."""

    if not file_path.exists():
        if optional:
            return None

        raise FileNotFoundError(f"Fichier introuvable : {file_path}.")

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
        with temporary_path.open(
            "w",
            encoding="utf-8",
            newline="\n"
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
                default=str
            )
            file.write("\n")

        atomic_replace(temporary_path, file_path)

    finally:
        remove_file_if_exists(temporary_path)


# Conversion des métriques

def safe_count(value: Any, default: int = 0) -> int:
    """Convertit une métrique en entier positif."""

    return parse_non_negative_integer(value, default=default)


def safe_float(value: Any) -> float | None:
    """Convertit une valeur numérique sans lever d'erreur."""

    if value in {None, ""}:
        return None

    try:
        converted_value = float(value)

    except (TypeError, ValueError, OverflowError):
        return None

    if converted_value < 0:
        return None

    return converted_value


def get_first_value(
    item: dict[str, Any],
    *field_names: str
) -> Any:
    """Retourne la première valeur renseignée parmi plusieurs champs."""

    for field_name in field_names:
        value = item.get(field_name)

        if value not in {None, ""}:
            return value

    return None


# Validation du lot

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


def validate_references(
    *,
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    features: list[dict[str, Any]]
) -> None:
    """Vérifie les articles et les références des collections enfants."""

    if not articles:
        raise RuntimeError("Aucun article transformé à charger.")

    invalid_article_indexes = [
        index
        for index, article in enumerate(articles)
        if (
            not isinstance(article, dict)
            or not article.get("id")
            or not article.get("source_key")
            or not article.get("title")
        )
    ]

    if invalid_article_indexes:
        raise ValueError(
            "Article(s) invalide(s) aux index : "
            f"{invalid_article_indexes[:20]}."
        )

    article_ids = {
        str(article["id"])
        for article in articles
    }

    if len(article_ids) != len(articles):
        raise ValueError(
            "Des identifiants d'articles sont dupliqués."
        )

    errors: list[str] = []

    for collection_name, collection in {
        "images": images,
        "labels": labels,
        "features": features
    }.items():
        missing_article_id_count = sum(
            1
            for item in collection
            if (
                not isinstance(item, dict)
                or not item.get("article_id")
            )
        )

        orphan_count = sum(
            1
            for item in collection
            if (
                isinstance(item, dict)
                and item.get("article_id")
                and str(item["article_id"]) not in article_ids
            )
        )

        if missing_article_id_count:
            errors.append(
                f"{collection_name}: "
                f"{missing_article_id_count} élément(s) sans article_id"
            )

        if orphan_count:
            errors.append(
                f"{collection_name}: "
                f"{orphan_count} référence(s) orpheline(s)"
            )

    if errors:
        raise ValueError(
            "Incohérences détectées avant chargement : "
            + " | ".join(errors)
        )


def validate_image_statuses(
    images: list[dict[str, Any]]
) -> None:
    """Vérifie la cohérence des statuts des images."""

    errors: list[str] = []

    for index, image in enumerate(images):
        if not isinstance(image, dict):
            errors.append(
                f"image invalide à l'index {index}"
            )
            continue

        if "is_valid" not in image:
            errors.append(
                f"is_valid absent à l'index {index}"
            )
            continue

        validation_status = str(
            image.get("validation_status") or "pending"
        ).strip().lower()

        validation_error = str(
            image.get("validation_error") or ""
        ).strip()

        is_valid = parse_boolean(
            image.get("is_valid"),
            default=False
        )

        if validation_status not in ALLOWED_IMAGE_VALIDATION_STATUSES:
            errors.append(
                f"statut de validation inconnu "
                f"{validation_status!r} à l'index {index}"
            )
            continue

        if is_valid and validation_status != "valid":
            errors.append(
                f"is_valid=true avec statut={validation_status!r} "
                f"à l'index {index}"
            )

        if not is_valid and validation_status == "valid":
            errors.append(
                f"is_valid=false avec statut='valid' "
                f"à l'index {index}"
            )

        if (
            validation_error
            and validation_status not in {
                "invalid",
                "validation_error"
            }
        ):
            errors.append(
                f"validation_error renseignée avec statut="
                f"{validation_status!r} à l'index {index}"
            )

    if errors:
        raise ValueError(
            "Statuts d'image invalides avant chargement : "
            + " | ".join(errors[:20])
        )


def validate_load_result(load_result: Any) -> dict[str, Any]:
    """Contrôle le résultat retourné par le service PostgreSQL."""

    if not isinstance(load_result, dict):
        raise TypeError(
            "load_batch_to_postgres doit retourner un dictionnaire."
        )

    pipeline_run_id = load_result.get("pipeline_run_id")

    if not pipeline_run_id:
        raise KeyError(
            "Le chargement ne contient pas de pipeline_run_id."
        )

    status = str(
        load_result.get("status", "success")
    ).strip().lower()

    if status not in {"success", "partial_success"}:
        raise RuntimeError(
            f"Le service PostgreSQL a retourné le statut {status!r}."
        )

    return dict(load_result)


# Statistiques des images

def count_images_by_status(
    images: list[dict[str, Any]]
) -> dict[str, int]:
    """Compte les images selon leur téléchargement et leur validation."""

    counts = {
        "total": len(images),
        "downloaded": 0,
        "valid": 0,
        "invalid": 0,
        "pending": 0
    }

    for image in images:
        if not isinstance(image, dict):
            continue

        local_path = str(
            get_first_value(
                image,
                "local_path",
                "image_path"
            )
            or ""
        ).strip()

        validation_status = str(
            image.get("validation_status") or "pending"
        ).strip().lower()

        if local_path:
            counts["downloaded"] += 1

        if validation_status == "valid":
            counts["valid"] += 1

        elif validation_status in {
            "invalid",
            "validation_error"
        }:
            counts["invalid"] += 1

        else:
            counts["pending"] += 1

    return counts


def build_image_metrics(
    images: list[dict[str, Any]]
) -> dict[str, Any]:
    """Calcule les KPI à partir des métadonnées déjà disponibles."""

    valid_images = [
        image
        for image in images
        if (
            isinstance(image, dict)
            and str(
                image.get("validation_status") or ""
            ).strip().lower() == "valid"
            and parse_boolean(
                image.get("is_valid"),
                default=False
            )
        )
    ]

    widths: list[float] = []
    heights: list[float] = []
    aspect_ratios: list[float] = []
    file_sizes: list[float] = []

    format_counts: Counter[str] = Counter()
    mime_type_counts: Counter[str] = Counter()

    animated_count = 0
    hashed_count = 0

    for image in valid_images:
        width = safe_float(
            get_first_value(
                image,
                "width",
                "image_width"
            )
        )
        height = safe_float(
            get_first_value(
                image,
                "height",
                "image_height"
            )
        )
        aspect_ratio = safe_float(
            get_first_value(
                image,
                "aspect_ratio",
                "image_aspect_ratio"
            )
        )
        file_size = safe_float(
            get_first_value(
                image,
                "file_size",
                "file_size_bytes",
                "image_size_bytes"
            )
        )

        image_format = str(
            get_first_value(
                image,
                "format",
                "image_format"
            )
            or ""
        ).strip().upper()

        mime_type = str(
            get_first_value(
                image,
                "mime_type",
                "image_mime_type"
            )
            or ""
        ).strip().lower()

        file_hash = str(
            get_first_value(
                image,
                "file_hash",
                "image_file_hash"
            )
            or ""
        ).strip()

        is_animated = parse_boolean(
            get_first_value(
                image,
                "animated",
                "is_animated",
                "image_animated"
            ),
            default=False
        )

        if width is not None and width > 0:
            widths.append(width)

        if height is not None and height > 0:
            heights.append(height)

        if aspect_ratio is None and width and height:
            aspect_ratio = width / height

        if aspect_ratio is not None and aspect_ratio > 0:
            aspect_ratios.append(aspect_ratio)

        if file_size is not None and file_size > 0:
            file_sizes.append(file_size)

        if image_format:
            format_counts[image_format] += 1

        if mime_type:
            mime_type_counts[mime_type] += 1

        if file_hash:
            hashed_count += 1

        if is_animated:
            animated_count += 1

    total_size_bytes = round(sum(file_sizes))

    return {
        "valid_images_with_metadata": len(valid_images),
        "hashed_images": hashed_count,
        "animated_images": animated_count,
        "total_size_bytes": total_size_bytes,
        "average_size_bytes": (
            round(total_size_bytes / len(file_sizes))
            if file_sizes
            else 0
        ),
        "average_width": (
            round(sum(widths) / len(widths), 2)
            if widths
            else 0
        ),
        "average_height": (
            round(sum(heights) / len(heights), 2)
            if heights
            else 0
        ),
        "average_aspect_ratio": (
            round(
                sum(aspect_ratios) / len(aspect_ratios),
                4
            )
            if aspect_ratios
            else 0
        ),
        "formats": dict(format_counts),
        "mime_types": dict(mime_type_counts)
    }


# Métadonnées du pipeline

def build_pipeline_metadata(
    *,
    context: dict[str, Any],
    batch_id: str,
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    transformation_report: dict[str, Any],
    extraction_report: dict[str, Any] | None
) -> dict[str, Any]:
    """Prépare les métadonnées du chargement PostgreSQL."""

    extraction_report = extraction_report or {}

    extracted_count = safe_count(
        extraction_report.get(
            "extracted_article_count",
            extraction_report.get(
                "total_articles",
                transformation_report.get(
                    "input_article_count",
                    len(articles)
                )
            )
        ),
        default=len(articles)
    )

    transformed_count = safe_count(
        transformation_report.get(
            "output_article_count",
            len(articles)
        ),
        default=len(articles)
    )

    removed_article_count = safe_count(
        transformation_report.get(
            "removed_article_count",
            transformation_report.get(
                "duplicate_count",
                0
            )
        )
    )

    image_counts = count_images_by_status(images)
    image_metrics = build_image_metrics(images)

    return {
        "dag_id": context["dag"].dag_id,
        "airflow_run_id": context["run_id"],
        "batch_id": batch_id,
        "extracted_count": extracted_count,
        "transformed_count": transformed_count,
        "rejected_count": removed_article_count,
        "removed_article_count": removed_article_count,
        "extraction_duration_seconds": extraction_report.get(
            "duration_seconds"
        ),
        "transformation_duration_seconds": transformation_report.get(
            "duration_seconds"
        ),
        "images_downloaded_count": image_counts["downloaded"],
        "images_valid_count": image_counts["valid"],
        "images_invalid_count": image_counts["invalid"],
        "images_pending_count": image_counts["pending"],
        "run_metadata": {
            "batch_id": batch_id,
            "removed_article_count": removed_article_count,
            "image_counts": image_counts,
            "image_metrics": image_metrics
        }
    }


def calculate_total_duration(started_at: Any) -> float:
    """Calcule la durée totale depuis une date ISO 8601."""

    try:
        parsed_start = datetime.fromisoformat(str(started_at))

        if parsed_start.tzinfo is None:
            parsed_start = parsed_start.replace(tzinfo=UTC)

        return round(
            (
                datetime.now(UTC)
                - parsed_start.astimezone(UTC)
            ).total_seconds(),
            3
        )

    except (TypeError, ValueError, OverflowError):
        return 0.0


# Préparation du lot

def prepare_load_batch_task(**context: Any) -> dict[str, Any]:
    """Lit, contrôle et prépare les collections à charger."""

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
        "Préparation du chargement PostgreSQL du lot %s.",
        batch_id
    )

    articles = read_json_file(
        paths["articles"],
        expected_type=list
    )
    images = read_json_file(
        paths["images"],
        expected_type=list
    )
    labels = read_json_file(
        paths["labels"],
        expected_type=list
    )
    features = read_json_file(
        paths["features"],
        expected_type=list
    )
    transformation_report = read_json_file(
        paths["transformation_report"],
        expected_type=dict
    )
    extraction_report = read_json_file(
        paths["extraction_report"],
        expected_type=dict,
        optional=True
    )

    validate_report_batch_id(
        report=transformation_report,
        expected_batch_id=batch_id,
        report_name=paths["transformation_report"].name
    )

    if extraction_report is not None:
        validate_report_batch_id(
            report=extraction_report,
            expected_batch_id=batch_id,
            report_name=paths["extraction_report"].name
        )

    validate_references(
        articles=articles,
        images=images,
        labels=labels,
        features=features
    )
    validate_image_statuses(images)

    image_counts = count_images_by_status(images)
    image_metrics = build_image_metrics(images)

    logger.info(
        "Lot %s prêt : %s article(s), %s image(s), "
        "%s téléchargée(s), %s valide(s), %s invalide(s), "
        "%s en attente, %s label(s), %s feature(s).",
        batch_id,
        len(articles),
        image_counts["total"],
        image_counts["downloaded"],
        image_counts["valid"],
        image_counts["invalid"],
        image_counts["pending"],
        len(labels),
        len(features)
    )

    return {
        "batch_id": batch_id,
        "batch_started_at": batch_started_at.isoformat(),
        "articles_file": str(paths["articles"]),
        "images_file": str(paths["images"]),
        "labels_file": str(paths["labels"]),
        "features_file": str(paths["features"]),
        "transformation_report_file": str(
            paths["transformation_report"]
        ),
        "extraction_report_file": str(
            paths["extraction_report"]
        ),
        "load_result_file": str(paths["load_result"]),
        "load_report_file": str(paths["load_report"]),
        "article_count": len(articles),
        "image_count": image_counts["total"],
        "downloaded_image_count": image_counts["downloaded"],
        "valid_image_count": image_counts["valid"],
        "invalid_image_count": image_counts["invalid"],
        "pending_image_count": image_counts["pending"],
        "image_metrics": image_metrics,
        "label_count": len(labels),
        "feature_count": len(features),
        "preparation_task_duration_seconds": round(
            perf_counter() - started_at,
            3
        )
    }


# Chargement PostgreSQL

def load_postgresql_task(**context: Any) -> dict[str, Any]:
    """Charge les collections préparées dans PostgreSQL."""

    started_at = perf_counter()
    task_instance = context["ti"]

    batch_manifest = task_instance.xcom_pull(
        task_ids="prepare_batch"
    )

    if not isinstance(batch_manifest, dict):
        raise RuntimeError(
            "Le manifeste de préparation est absent ou invalide."
        )

    batch_id = normalize_batch_id(
        batch_manifest.get("batch_id")
    )

    articles_file = Path(
        str(batch_manifest.get("articles_file", ""))
    )
    images_file = Path(
        str(batch_manifest.get("images_file", ""))
    )
    labels_file = Path(
        str(batch_manifest.get("labels_file", ""))
    )
    features_file = Path(
        str(batch_manifest.get("features_file", ""))
    )
    transformation_report_file = Path(
        str(
            batch_manifest.get(
                "transformation_report_file",
                ""
            )
        )
    )
    extraction_report_file = Path(
        str(
            batch_manifest.get(
                "extraction_report_file",
                ""
            )
        )
    )
    load_result_file = Path(
        str(batch_manifest.get("load_result_file", ""))
    )

    articles = read_json_file(
        articles_file,
        expected_type=list
    )
    images = read_json_file(
        images_file,
        expected_type=list
    )
    labels = read_json_file(
        labels_file,
        expected_type=list
    )
    features = read_json_file(
        features_file,
        expected_type=list
    )
    transformation_report = read_json_file(
        transformation_report_file,
        expected_type=dict
    )
    extraction_report = read_json_file(
        extraction_report_file,
        expected_type=dict,
        optional=True
    )

    # Les contrôles sont rejoués avant l'écriture en base.
    validate_references(
        articles=articles,
        images=images,
        labels=labels,
        features=features
    )
    validate_image_statuses(images)

    pipeline_metadata = build_pipeline_metadata(
        context=context,
        batch_id=batch_id,
        articles=articles,
        images=images,
        transformation_report=transformation_report,
        extraction_report=extraction_report
    )

    logger.info(
        "Chargement PostgreSQL du lot %s : %s article(s), "
        "%s image(s), %s label(s), %s feature(s).",
        batch_id,
        len(articles),
        len(images),
        len(labels),
        len(features)
    )

    postgres_hook = PostgresHook(
        postgres_conn_id=POSTGRES_CONN_ID
    )
    connection = postgres_hook.get_conn()

    try:
        load_result = load_batch_to_postgres(
            connection,
            articles=articles,
            images=images,
            labels=labels,
            features=features,
            pipeline_metadata=pipeline_metadata
        )

    finally:
        connection.close()

    load_result = validate_load_result(load_result)

    duration_seconds = round(
        perf_counter() - started_at,
        3
    )

    result_payload = {
        "batch_id": batch_id,
        "pipeline_run_id": str(
            load_result["pipeline_run_id"]
        ),
        "status": str(
            load_result.get("status", "success")
        ).strip().lower(),
        "pipeline_metadata": pipeline_metadata,
        "loaded_articles": safe_count(
            load_result.get("loaded_articles")
        ),
        "loaded_images": safe_count(
            load_result.get("loaded_images")
        ),
        "loaded_labels": safe_count(
            load_result.get("loaded_labels")
        ),
        "loaded_features": safe_count(
            load_result.get("loaded_features")
        ),
        "load_task_duration_seconds": duration_seconds,
        "load_completed_at": get_extraction_date()
    }

    write_json_file(
        load_result_file,
        result_payload
    )

    logger.info(
        "Lot %s chargé : %s article(s), %s image(s), "
        "%s label(s), %s feature(s) en %.3f seconde(s).",
        batch_id,
        result_payload["loaded_articles"],
        result_payload["loaded_images"],
        result_payload["loaded_labels"],
        result_payload["loaded_features"],
        duration_seconds
    )

    return {
        "batch_id": batch_id,
        "pipeline_run_id": result_payload["pipeline_run_id"],
        "status": result_payload["status"],
        "load_result_file": str(load_result_file),
        "loaded_articles": result_payload["loaded_articles"],
        "loaded_images": result_payload["loaded_images"],
        "loaded_labels": result_payload["loaded_labels"],
        "loaded_features": result_payload["loaded_features"]
    }


# Finalisation du lot

def finalize_load_batch_task(**context: Any) -> dict[str, Any]:
    """Produit le rapport final du chargement PostgreSQL."""

    started_at = perf_counter()
    task_instance = context["ti"]

    batch_manifest = task_instance.xcom_pull(
        task_ids="prepare_batch"
    )
    load_manifest = task_instance.xcom_pull(
        task_ids="load_postgresql"
    )

    if not isinstance(batch_manifest, dict):
        raise RuntimeError(
            "Le manifeste de préparation est absent ou invalide."
        )

    if not isinstance(load_manifest, dict):
        raise RuntimeError(
            "Le manifeste de chargement est absent ou invalide."
        )

    batch_id = normalize_batch_id(
        batch_manifest.get("batch_id")
    )

    load_result_file = Path(
        str(load_manifest.get("load_result_file", ""))
    )
    load_report_file = Path(
        str(batch_manifest.get("load_report_file", ""))
    )

    load_result = read_json_file(
        load_result_file,
        expected_type=dict
    )

    if normalize_batch_id(
        load_result.get("batch_id")
    ) != batch_id:
        raise ValueError(
            "Le résultat intermédiaire n'appartient pas "
            "au lot courant."
        )

    finalization_duration = round(
        perf_counter() - started_at,
        3
    )

    total_duration = calculate_total_duration(
        batch_manifest.get("batch_started_at")
    )

    report = {
        "batch_id": batch_id,
        "pipeline_run_id": str(
            load_result["pipeline_run_id"]
        ),
        "status": load_result.get(
            "status",
            "success"
        ),
        "step": "load",
        "step_status": "success",
        "input_counts": {
            "articles": batch_manifest["article_count"],
            "images": batch_manifest["image_count"],
            "labels": batch_manifest["label_count"],
            "features": batch_manifest["feature_count"]
        },
        "image_counts": {
            "total": batch_manifest["image_count"],
            "downloaded": batch_manifest[
                "downloaded_image_count"
            ],
            "valid": batch_manifest["valid_image_count"],
            "invalid": batch_manifest[
                "invalid_image_count"
            ],
            "pending": batch_manifest[
                "pending_image_count"
            ]
        },
        "image_metrics": batch_manifest["image_metrics"],
        "pipeline_metadata": load_result[
            "pipeline_metadata"
        ],
        "loaded_articles": load_result[
            "loaded_articles"
        ],
        "loaded_images": load_result[
            "loaded_images"
        ],
        "loaded_labels": load_result[
            "loaded_labels"
        ],
        "loaded_features": load_result[
            "loaded_features"
        ],
        "preparation_task_duration_seconds": batch_manifest[
            "preparation_task_duration_seconds"
        ],
        "load_task_duration_seconds": load_result[
            "load_task_duration_seconds"
        ],
        "finalization_task_duration_seconds": (
            finalization_duration
        ),
        "duration_seconds": total_duration,
        "completed_at": get_extraction_date()
    }

    write_json_file(
        load_report_file,
        report
    )

    # Suppression après écriture réussie du rapport final.
    remove_file_if_exists(load_result_file)

    final_manifest = {
        "batch_id": batch_id,
        "pipeline_run_id": report["pipeline_run_id"],
        "status": report["status"],
        "report_file": str(load_report_file),
        "loaded_articles": report["loaded_articles"],
        "loaded_images": report["loaded_images"],
        "loaded_labels": report["loaded_labels"],
        "loaded_features": report["loaded_features"]
    }

    logger.info(
        "Chargement du lot %s finalisé : rapport écrit dans %s.",
        batch_id,
        load_report_file
    )

    return final_manifest


# Définition du DAG

DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 1,
    "retry_delay": timedelta(minutes=1)
}

with DAG(
    dag_id="checkit_load",
    description="Charge les entités transformées dans PostgreSQL.",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 7, 1, tzinfo=UTC),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=40),
    tags=["checkit", "etl", "load", "postgres"]
) as dag:

    start = EmptyOperator(
        task_id="start"
    )

    # Lit et contrôle les collections transformées.
    prepare_batch = PythonOperator(
        task_id="prepare_batch",
        python_callable=prepare_load_batch_task,
        execution_timeout=PREPARATION_TIMEOUT
    )

    # Charge les collections dans PostgreSQL.
    load_postgresql = PythonOperator(
        task_id="load_postgresql",
        python_callable=load_postgresql_task,
        execution_timeout=LOAD_TIMEOUT
    )

    # Produit le rapport final du chargement.
    finalize_batch = PythonOperator(
        task_id="finalize_batch",
        python_callable=finalize_load_batch_task,
        execution_timeout=FINALIZATION_TIMEOUT
    )

    end = EmptyOperator(
        task_id="end"
    )

    start >> prepare_batch >> load_postgresql >> finalize_batch >> end