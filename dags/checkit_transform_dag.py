"""DAG de transformation des lots CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_transform_dag.py` (commit 93632c5).

Lit le fichier produit par checkit_extract, transforme les articles vers les
collections PostgreSQL et produit les fichiers nécessaires au chargement.

Les données métier volumineuses restent dans le dossier partagé du lot.
Seuls des manifestes légers sont transmis entre les tâches avec les XCom.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from airflow import DAG
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator

from src.logger import get_logger
from src.storage.files.storage_utils import (
    atomic_replace,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)
from src.transformers.database_transformer import (
    transform_articles_for_database
)
from src.utils.date_utils import get_extraction_date
from src.utils.parsing_utils import parse_boolean

logger = get_logger(__name__)


# Configuration

LOTS_DIRECTORY = Path(
    os.getenv(
        "CHECKIT_SHARED_LOTS_DIR",
        "/opt/airflow/shared/lots"
    )
)

PREPARATION_TIMEOUT = timedelta(minutes=5)
TRANSFORMATION_TIMEOUT = timedelta(minutes=40)
FINALIZATION_TIMEOUT = timedelta(minutes=10)


# Gestion du lot

def normalize_batch_id(value: Any) -> str:
    """Transforme un identifiant Airflow en nom de dossier sûr."""

    normalized_value = "".join(
        character
        if character.isalnum() or character in {"_", "-", "."}
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
    context: dict[str, Any],
    batch_directory: Path
) -> Path:
    """Retourne le fichier produit par le DAG d'extraction."""

    dag_run = context["dag_run"]
    configuration = dict(dag_run.conf or {})
    configured_file = configuration.get("articles_file")

    input_file = (
        Path(str(configured_file))
        if configured_file
        else batch_directory / "01_extracted_articles.json"
    )

    if not input_file.exists():
        raise FileNotFoundError(
            f"Fichier d'extraction introuvable pour le lot "
            f"{batch_directory.name} : {input_file}. "
            "Vérifiez que checkit_extract a utilisé le même batch_id."
        )

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Le chemin d'entrée n'est pas un fichier : {input_file}."
        )

    return input_file


def build_output_paths(
    batch_directory: Path
) -> dict[str, Path]:
    """Construit les chemins utilisés pendant la transformation."""

    return {
        "transformed_payload": (
            batch_directory / "02_transformed_payload.tmp.json"
        ),
        "articles": batch_directory / "02_articles_ready.json",
        "images": batch_directory / "02_images_ready.json",
        "labels": batch_directory / "02_labels_ready.json",
        "features": batch_directory / "02_features_ready.json",
        "report": batch_directory / "02_transformation_report.json"
    }


# Lecture et écriture JSON

def read_json_file(file_path: Path) -> Any:
    """Lit un document JSON et produit une erreur explicite."""

    if not file_path.is_file():
        raise FileNotFoundError(
            f"Fichier JSON introuvable : {file_path}."
        )

    try:
        with file_path.open(
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Document JSON invalide : {file_path}."
        ) from error

    except OSError as error:
        raise OSError(
            f"Impossible de lire le fichier : {file_path}."
        ) from error


def read_articles(
    file_path: Path
) -> tuple[list[dict[str, Any]], int]:
    """Lit les articles et compte les éléments de structure invalide."""

    payload = read_json_file(file_path)

    if not isinstance(payload, list):
        raise TypeError(
            "Le fichier d'extraction doit contenir une liste JSON."
        )

    articles = [
        dict(article)
        for article in payload
        if isinstance(article, dict)
    ]

    ignored_count = len(payload) - len(articles)

    return articles, ignored_count


def read_transformed_payload(
    file_path: Path
) -> dict[str, Any]:
    """Lit et contrôle le document intermédiaire de transformation."""

    payload = read_json_file(file_path)

    if not isinstance(payload, dict):
        raise TypeError(
            "Le résultat intermédiaire doit contenir un objet JSON."
        )

    return dict(payload)


def write_json_file(
    file_path: Path,
    payload: Any
) -> None:
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

        atomic_replace(
            temporary_path,
            file_path
        )

    finally:
        remove_file_if_exists(temporary_path)


# Validation

def validate_transformed_payload(
    payload: dict[str, Any]
) -> None:
    """Vérifie les collections produites par le transformateur."""

    required_collections = (
        "articles",
        "images",
        "labels",
        "features"
    )

    for collection_name in required_collections:
        if collection_name not in payload:
            raise KeyError(
                f"Collection manquante dans la transformation : "
                f"{collection_name}."
            )

        if not isinstance(payload[collection_name], list):
            raise TypeError(
                f"La collection {collection_name!r} doit être une liste."
            )

    article_ids = {
        str(article["id"])
        for article in payload["articles"]
        if (
            isinstance(article, dict)
            and article.get("id") is not None
        )
    }

    if payload["articles"] and not article_ids:
        raise ValueError(
            "Aucun identifiant d'article valide après transformation."
        )

    for collection_name in (
        "images",
        "labels",
        "features"
    ):
        orphan_count = sum(
            1
            for item in payload[collection_name]
            if (
                not isinstance(item, dict)
                or item.get("article_id") is None
                or str(item["article_id"]) not in article_ids
            )
        )

        if orphan_count:
            raise ValueError(
                f"{orphan_count} référence(s) orpheline(s) "
                f"dans la collection {collection_name}."
            )


# Statistiques

def count_images_by_status(
    images: list[dict[str, Any]]
) -> dict[str, int]:
    """Compte les images selon leur téléchargement et leur validation."""

    counts = {
        "downloaded": 0,
        "valid": 0,
        "invalid": 0,
        "pending": 0
    }

    for image in images:
        if not isinstance(image, dict):
            continue

        local_path = str(
            image.get("local_path")
            or image.get("image_path")
            or ""
        ).strip()

        validation_status = str(
            image.get("validation_status")
            or ""
        ).strip().lower()

        is_valid = parse_boolean(
            image.get("is_valid"),
            default=False
        )

        if local_path:
            counts["downloaded"] += 1

        if validation_status == "valid" or (
            not validation_status
            and is_valid
        ):
            counts["valid"] += 1

        elif validation_status in {
            "invalid",
            "validation_error"
        }:
            counts["invalid"] += 1

        else:
            counts["pending"] += 1

    return counts


def count_multimodal_articles(
    features: list[dict[str, Any]]
) -> int:
    """Compte les articles identifiés comme multimodaux."""

    article_ids = {
        str(feature["article_id"])
        for feature in features
        if (
            isinstance(feature, dict)
            and feature.get("article_id") is not None
            and feature.get("feature_name") == "is_multimodal"
            and parse_boolean(
                feature.get("boolean_value"),
                default=False
            )
        )
    }

    return len(article_ids)


def calculate_total_duration(started_at: Any) -> float:
    """Calcule la durée totale depuis une date ISO 8601."""

    try:
        parsed_start = datetime.fromisoformat(
            str(started_at)
        )

        if parsed_start.tzinfo is None:
            parsed_start = parsed_start.replace(
                tzinfo=UTC
            )

        return round(
            (
                datetime.now(UTC)
                - parsed_start.astimezone(UTC)
            ).total_seconds(),
            3
        )

    except (
        TypeError,
        ValueError,
        OverflowError
    ):
        return 0.0


# Tâche 1 : préparation du lot

def prepare_transformation_batch_task(
    **context: Any
) -> dict[str, Any]:
    """Prépare et contrôle le fichier d'entrée de la transformation."""

    started_at = perf_counter()
    batch_started_at = datetime.now(UTC)

    batch_id = resolve_batch_id(context)
    batch_directory = get_batch_directory(batch_id)
    input_file = resolve_input_file(
        context,
        batch_directory
    )
    output_paths = build_output_paths(
        batch_directory
    )

    logger.info(
        "Préparation de la transformation du lot %s depuis %s.",
        batch_id,
        input_file
    )

    articles, ignored_count = read_articles(
        input_file
    )

    if not articles:
        raise RuntimeError(
            "Le fichier d'extraction ne contient aucun article exploitable."
        )

    article_count = len(articles)

    logger.info(
        "Lot %s préparé : %s article(s) exploitable(s), "
        "%s élément(s) ignoré(s).",
        batch_id,
        article_count,
        ignored_count
    )

    # Seuls les chemins et les compteurs sont placés dans le XCom.
    return {
        "batch_id": batch_id,
        "batch_started_at": batch_started_at.isoformat(),
        "batch_directory": str(batch_directory),
        "input_file": str(input_file),
        "transformed_payload_file": str(
            output_paths["transformed_payload"]
        ),
        "articles_file": str(
            output_paths["articles"]
        ),
        "images_file": str(
            output_paths["images"]
        ),
        "labels_file": str(
            output_paths["labels"]
        ),
        "features_file": str(
            output_paths["features"]
        ),
        "report_file": str(
            output_paths["report"]
        ),
        "input_article_count": article_count,
        "ignored_items_count": ignored_count,
        "preparation_task_duration_seconds": round(
            perf_counter() - started_at,
            3
        )
    }


# Tâche 2 : transformation

def transform_payload_task(
    **context: Any
) -> dict[str, Any]:
    """Transforme les articles et écrit un document intermédiaire validé."""

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
    input_file = Path(
        str(batch_manifest.get("input_file", ""))
    )
    transformed_payload_file = Path(
        str(
            batch_manifest.get(
                "transformed_payload_file",
                ""
            )
        )
    )

    raw_articles, ignored_count = read_articles(
        input_file
    )

    if not raw_articles:
        raise RuntimeError(
            f"Aucun article à transformer pour le lot {batch_id}."
        )

    logger.info(
        "Début de la transformation du lot %s : %s article(s).",
        batch_id,
        len(raw_articles)
    )

    transformed_payload = transform_articles_for_database(
        articles_to_transform=raw_articles,
        batch_id=batch_id
    )

    if not isinstance(transformed_payload, dict):
        raise TypeError(
            "Le transformateur doit retourner un dictionnaire."
        )

    validate_transformed_payload(
        transformed_payload
    )

    articles = transformed_payload["articles"]
    images = transformed_payload["images"]
    labels = transformed_payload["labels"]
    features = transformed_payload["features"]

    if not articles:
        raise RuntimeError(
            "La transformation n'a produit aucun article exploitable."
        )

    input_count = len(raw_articles)
    article_count = len(articles)
    removed_article_count = max(
        input_count - article_count,
        0
    )

    image_counts = count_images_by_status(
        images
    )
    multimodal_count = count_multimodal_articles(
        features
    )

    # Le document intermédiaire reste dans le stockage partagé.
    write_json_file(
        transformed_payload_file,
        transformed_payload
    )

    logger.info(
        "Lot %s transformé : %s article(s), %s image(s), "
        "%s téléchargée(s), %s valide(s), %s invalide(s), "
        "%s en attente, %s label(s), %s feature(s).",
        batch_id,
        article_count,
        len(images),
        image_counts["downloaded"],
        image_counts["valid"],
        image_counts["invalid"],
        image_counts["pending"],
        len(labels),
        len(features)
    )

    return {
        "batch_id": batch_id,
        "transformed_payload_file": str(
            transformed_payload_file
        ),
        "input_article_count": input_count,
        "output_article_count": article_count,
        "removed_article_count": removed_article_count,
        "ignored_items_count": max(
            ignored_count,
            int(
                batch_manifest.get(
                    "ignored_items_count",
                    0
                )
            )
        ),
        "image_count": len(images),
        "downloaded_image_count": image_counts["downloaded"],
        "valid_image_count": image_counts["valid"],
        "invalid_image_count": image_counts["invalid"],
        "pending_image_count": image_counts["pending"],
        "label_count": len(labels),
        "feature_count": len(features),
        "multimodal_count": multimodal_count,
        "transformation_task_duration_seconds": round(
            perf_counter() - started_at,
            3
        ),
        "transformation_completed_at": get_extraction_date()
    }


# Tâche 3 : finalisation

def finalize_transformation_batch_task(
    **context: Any
) -> dict[str, Any]:
    """Écrit les collections finales et le rapport de transformation."""

    started_at = perf_counter()
    task_instance = context["ti"]

    batch_manifest = task_instance.xcom_pull(
        task_ids="prepare_batch"
    )
    transformation_manifest = task_instance.xcom_pull(
        task_ids="transform_payload"
    )

    if not isinstance(batch_manifest, dict):
        raise RuntimeError(
            "Le manifeste de préparation est absent ou invalide."
        )

    if not isinstance(transformation_manifest, dict):
        raise RuntimeError(
            "Le manifeste de transformation est absent ou invalide."
        )

    batch_id = normalize_batch_id(
        batch_manifest.get("batch_id")
    )
    input_file = Path(
        str(batch_manifest.get("input_file", ""))
    )
    transformed_payload_file = Path(
        str(
            transformation_manifest.get(
                "transformed_payload_file",
                ""
            )
        )
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
    report_file = Path(
        str(batch_manifest.get("report_file", ""))
    )

    transformed_payload = read_transformed_payload(
        transformed_payload_file
    )
    validate_transformed_payload(
        transformed_payload
    )

    output_paths = {
        "articles": articles_file,
        "images": images_file,
        "labels": labels_file,
        "features": features_file
    }

    # Chaque collection est écrite atomiquement dans son fichier final.
    for collection_name, file_path in output_paths.items():
        write_json_file(
            file_path,
            transformed_payload[collection_name]
        )

    finalization_duration = round(
        perf_counter() - started_at,
        3
    )
    total_duration = calculate_total_duration(
        batch_manifest.get("batch_started_at")
    )
    completed_at = get_extraction_date()

    report = {
        "batch_id": batch_id,
        "status": "success",
        "step": "transformation",
        "step_status": "success",
        "input_file": str(input_file),
        "input_article_count": transformation_manifest[
            "input_article_count"
        ],
        "output_article_count": transformation_manifest[
            "output_article_count"
        ],
        "removed_article_count": transformation_manifest[
            "removed_article_count"
        ],
        "ignored_items_count": transformation_manifest[
            "ignored_items_count"
        ],
        "image_count": transformation_manifest[
            "image_count"
        ],
        "downloaded_image_count": transformation_manifest[
            "downloaded_image_count"
        ],
        "valid_image_count": transformation_manifest[
            "valid_image_count"
        ],
        "invalid_image_count": transformation_manifest[
            "invalid_image_count"
        ],
        "pending_image_count": transformation_manifest[
            "pending_image_count"
        ],
        "label_count": transformation_manifest[
            "label_count"
        ],
        "feature_count": transformation_manifest[
            "feature_count"
        ],
        "multimodal_count": transformation_manifest[
            "multimodal_count"
        ],
        "articles_file": str(articles_file),
        "images_file": str(images_file),
        "labels_file": str(labels_file),
        "features_file": str(features_file),
        "report_file": str(report_file),
        "preparation_task_duration_seconds": batch_manifest[
            "preparation_task_duration_seconds"
        ],
        "transformation_task_duration_seconds": transformation_manifest[
            "transformation_task_duration_seconds"
        ],
        "finalization_task_duration_seconds": finalization_duration,
        "duration_seconds": total_duration,
        "completed_at": completed_at
    }

    write_json_file(
        report_file,
        report
    )

    # L'intermédiaire est supprimé seulement après la réussite complète.
    remove_file_if_exists(
        transformed_payload_file
    )

    final_manifest = {
        "batch_id": batch_id,
        "status": "success",
        "articles_file": str(articles_file),
        "images_file": str(images_file),
        "labels_file": str(labels_file),
        "features_file": str(features_file),
        "report_file": str(report_file),
        "article_count": transformation_manifest[
            "output_article_count"
        ],
        "removed_article_count": transformation_manifest[
            "removed_article_count"
        ],
        "image_count": transformation_manifest[
            "image_count"
        ],
        "downloaded_image_count": transformation_manifest[
            "downloaded_image_count"
        ],
        "valid_image_count": transformation_manifest[
            "valid_image_count"
        ],
        "invalid_image_count": transformation_manifest[
            "invalid_image_count"
        ],
        "pending_image_count": transformation_manifest[
            "pending_image_count"
        ],
        "label_count": transformation_manifest[
            "label_count"
        ],
        "feature_count": transformation_manifest[
            "feature_count"
        ],
        "multimodal_count": transformation_manifest[
            "multimodal_count"
        ]
    }

    logger.info(
        "Lot %s finalisé : %s article(s), %s image(s), "
        "%s téléchargée(s), %s valide(s), %s invalide(s), "
        "%s en attente, %s label(s), %s feature(s) "
        "en %.3f seconde(s).",
        batch_id,
        final_manifest["article_count"],
        final_manifest["image_count"],
        final_manifest["downloaded_image_count"],
        final_manifest["valid_image_count"],
        final_manifest["invalid_image_count"],
        final_manifest["pending_image_count"],
        final_manifest["label_count"],
        final_manifest["feature_count"],
        total_duration
    )

    logger.info(
        "Collections prêtes pour le chargement PostgreSQL : %s.",
        articles_file.parent
    )

    return final_manifest


# Définition du DAG

DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 1,
    "retry_delay": timedelta(minutes=1)
}

with DAG(
    dag_id="checkit_transform",
    description=(
        "Transforme le lot extrait vers les entités PostgreSQL."
    ),
    default_args=DEFAULT_ARGS,
    start_date=datetime(
        2026,
        7,
        1,
        tzinfo=UTC
    ),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=55),
    tags=[
        "checkit",
        "etl",
        "transform",
        "postgres"
    ]
) as dag:

    start = EmptyOperator(
        task_id="start"
    )

    # Étape 1 : contrôle le lot extrait et prépare ses chemins.
    prepare_batch = PythonOperator(
        task_id="prepare_batch",
        python_callable=prepare_transformation_batch_task,
        execution_timeout=PREPARATION_TIMEOUT
    )

    # Étape 2 : transforme les articles vers PostgreSQL.
    transform_payload = PythonOperator(
        task_id="transform_payload",
        python_callable=transform_payload_task,
        execution_timeout=TRANSFORMATION_TIMEOUT
    )

    # Étape 3 : écrit les collections finales et le rapport.
    finalize_batch = PythonOperator(
        task_id="finalize_batch",
        python_callable=finalize_transformation_batch_task,
        execution_timeout=FINALIZATION_TIMEOUT
    )

    end = EmptyOperator(
        task_id="end"
    )

    start >> prepare_batch >> transform_payload >> finalize_batch >> end