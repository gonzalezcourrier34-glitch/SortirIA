"""DAG d'extraction multimodale CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `dags/checkit_extract_dag.py` (commit 93632c5).

Exécute les extracteurs sélectionnés, traite les images, écrit les articles
dans un lot partagé et produit un rapport pour les DAG suivants.

Le DAG est volontairement découpé en plusieurs tâches afin de rendre
l'exécution plus lisible dans Airflow et de faciliter les reprises sur erreur.
Les articles ne transitent jamais dans les XCom : seuls des manifestes légers
contenant les chemins des fichiers et les métriques principales sont échangés.
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

from src.extractor_execution_service import EXTRACTORS, extract_all_sources
from src.images.image_service import process_article_images
from src.logger import get_logger
from src.storage.files.storage_utils import (
    atomic_replace,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)
from src.utils.date_utils import get_extraction_date
from src.utils.parsing_utils import parse_boolean

logger = get_logger(__name__)


# Configuration

SHARED_ROOT_DIRECTORY = Path(
    os.getenv("CHECKIT_SHARED_LOTS_DIR", "/opt/airflow/shared/lots")
)

DEFAULT_EXTRACTORS = tuple(EXTRACTORS)

EXTRACTION_TIMEOUT = timedelta(minutes=45)
IMAGE_PROCESSING_TIMEOUT = timedelta(minutes=60)
FINALIZATION_TIMEOUT = timedelta(minutes=10)


# Normalisation

def normalize_batch_id(value: Any) -> str:
    """Transforme un identifiant Airflow en nom de dossier sûr."""

    normalized_value = "".join(
        character if character.isalnum() or character in {"_", "-", "."} else "_"
        for character in str(value)
    )
    return normalized_value.strip("._-") or "unknown_batch"


def get_requested_extractors(configuration: dict[str, Any]) -> list[str]:
    """Retourne les extracteurs demandés dans la configuration du run."""

    configured_extractors = configuration.get("extractors", DEFAULT_EXTRACTORS)

    if not isinstance(configured_extractors, (list, tuple)):
        raise TypeError("La configuration 'extractors' doit être une liste.")

    extractor_names = [
        str(name).strip().lower()
        for name in configured_extractors
        if str(name).strip()
    ]

    if not extractor_names:
        raise ValueError("Aucun extracteur valide n'a été demandé.")

    unknown_extractors = sorted(set(extractor_names) - set(EXTRACTORS))

    if unknown_extractors:
        raise ValueError(
            f"Extracteur(s) inconnu(s) : {', '.join(unknown_extractors)}."
        )

    return list(dict.fromkeys(extractor_names))


def get_batch_id(context: dict[str, Any]) -> str:
    """Retourne l'identifiant normalisé du lot Airflow courant."""

    dag_run = context["dag_run"]
    configuration = dict(dag_run.conf or {})
    raw_batch_id = (
        configuration.get("batch_id")
        or configuration.get("parent_run_id")
        or context["run_id"]
    )
    return normalize_batch_id(raw_batch_id)


# Chemins du lot

def build_batch_paths(batch_id: str) -> dict[str, Path]:
    """Construit tous les chemins utilisés pendant l'extraction."""

    batch_directory = SHARED_ROOT_DIRECTORY / batch_id
    ensure_directory_exists(batch_directory)

    return {
        "batch_directory": batch_directory,
        "raw_articles_file": batch_directory / "00_raw_extracted_articles.json",
        "raw_report_file": batch_directory / "00_raw_extraction_report.json",
        "articles_file": batch_directory / "01_extracted_articles.json",
        "report_file": batch_directory / "01_extraction_report.json"
    }


# Lecture et écriture JSON

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


def read_json_file(file_path: Path) -> Any:
    """Lit un document JSON et produit une erreur explicite si nécessaire."""

    if not file_path.is_file():
        raise FileNotFoundError(f"Fichier JSON introuvable : {file_path}.")

    try:
        with file_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Document JSON invalide : {file_path}."
        ) from error


def read_json_list(file_path: Path) -> list[dict[str, Any]]:
    """Lit une liste d'articles JSON et contrôle sa structure."""

    payload = read_json_file(file_path)

    if not isinstance(payload, list):
        raise TypeError(
            f"Le fichier {file_path} ne contient pas une liste JSON."
        )

    return [
        dict(article)
        for article in payload
        if isinstance(article, dict)
    ]


def read_json_object(file_path: Path) -> dict[str, Any]:
    """Lit un objet JSON et contrôle sa structure."""

    payload = read_json_file(file_path)

    if not isinstance(payload, dict):
        raise TypeError(
            f"Le fichier {file_path} ne contient pas un objet JSON."
        )

    return dict(payload)


# Métriques images

def has_valid_image(article: dict[str, Any]) -> bool:
    """Indique si l'article possède une image finale valide."""

    explicit_status = article.get("image_is_valid")

    if isinstance(explicit_status, bool):
        return explicit_status

    return bool(str(article.get("image_path", "")).strip())


def calculate_total_duration(started_at: Any) -> float:
    """Calcule la durée du lot depuis une date ISO 8601."""

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


# Tâche 1 : extraction

def run_sources_extraction_task(**context: Any) -> dict[str, Any]:
    """Exécute les extracteurs et écrit les articles bruts du lot."""

    task_started_at = perf_counter()
    batch_started_at = datetime.now(UTC)
    dag_run = context["dag_run"]
    configuration = dict(dag_run.conf or {})
    batch_id = get_batch_id(context)
    paths = build_batch_paths(batch_id)
    extractor_names = get_requested_extractors(configuration)
    require_image = parse_boolean(
        configuration.get("require_image"),
        default=False
    )

    logger.info(
        "Démarrage du lot %s avec les extracteurs : %s.",
        batch_id,
        ", ".join(extractor_names)
    )
    logger.info("Image valide obligatoire : %s.", require_image)

    # Le service commun isole les erreurs des extracteurs afin qu'une source
    # facultative indisponible ne fasse pas échouer inutilement tout le lot.
    extraction_result = extract_all_sources(
        extractor_names=extractor_names,
        fail_if_empty=True,
        fail_on_extractor_error=False
    )
    extracted_articles = extraction_result.articles

    if not extracted_articles:
        raise RuntimeError("Aucun article n'a été extrait.")

    extracted_count = len(extracted_articles)

    # Les articles sont stockés hors XCom pour éviter de saturer la base Airflow.
    write_json_file(paths["raw_articles_file"], extracted_articles)

    report_payload = {
        key: value
        for key, value in extraction_result.to_dict().items()
        if key != "articles"
    }
    report_payload.update({
        "batch_id": batch_id,
        "step": "extraction",
        "step_status": "success",
        "require_image": require_image,
        "requested_extractors": extractor_names,
        "extracted_article_count": extracted_count,
        "raw_articles_file": str(paths["raw_articles_file"]),
        "articles_file": str(paths["articles_file"]),
        "report_file": str(paths["report_file"]),
        "batch_started_at": batch_started_at.isoformat(),
        "extraction_completed_at": get_extraction_date(),
        "extraction_task_duration_seconds": round(
            perf_counter() - task_started_at,
            3
        )
    })
    write_json_file(paths["raw_report_file"], report_payload)

    logger.info(
        "Extraction du lot %s terminée : %s article(s) extrait(s).",
        batch_id,
        extracted_count
    )

    # Seul ce manifeste léger est transmis à la tâche suivante.
    return {
        "batch_id": batch_id,
        "batch_started_at": batch_started_at.isoformat(),
        "require_image": require_image,
        "raw_articles_file": str(paths["raw_articles_file"]),
        "raw_report_file": str(paths["raw_report_file"]),
        "articles_file": str(paths["articles_file"]),
        "report_file": str(paths["report_file"]),
        "extracted_article_count": extracted_count,
        "successful_extractors": extraction_result.successful_extractors,
        "failed_extractors": extraction_result.failed_extractors
    }


# Tâche 2 : traitement des images

def run_image_processing_task(**context: Any) -> dict[str, Any]:
    """Traite les images et écrit les articles finaux de l'extraction."""

    task_started_at = perf_counter()
    task_instance = context["ti"]
    extraction_manifest = task_instance.xcom_pull(
        task_ids="extract_sources"
    )

    if not isinstance(extraction_manifest, dict):
        raise RuntimeError(
            "Le manifeste de la tâche d'extraction est absent ou invalide."
        )

    batch_id = normalize_batch_id(extraction_manifest.get("batch_id"))
    raw_articles_file = Path(
        str(extraction_manifest.get("raw_articles_file", ""))
    )
    articles_file = Path(
        str(extraction_manifest.get("articles_file", ""))
    )
    require_image = parse_boolean(
        extraction_manifest.get("require_image"),
        default=False
    )

    articles = read_json_list(raw_articles_file)

    if not articles:
        raise RuntimeError(
            f"Aucun article brut disponible pour le lot {batch_id}."
        )

    logger.info(
        "Début du traitement des images du lot %s pour %s article(s).",
        batch_id,
        len(articles)
    )

    # Les images sont facultatives par défaut afin de ne pas supprimer les
    # articles textuels provenant de sources sans illustration exploitable.
    processed_articles = process_article_images(
        articles=articles,
        require_image=require_image
    )

    if not processed_articles:
        raise RuntimeError(
            "Aucun article exploitable après le traitement des images."
        )

    processed_count = len(processed_articles)
    extracted_count = len(articles)
    valid_image_count = sum(
        has_valid_image(article)
        for article in processed_articles
    )
    rejected_count = max(extracted_count - processed_count, 0)
    articles_without_valid_image_count = processed_count - valid_image_count

    write_json_file(articles_file, processed_articles)

    logger.info(
        "Images du lot %s traitées : %s conservé(s), %s rejeté(s), "
        "%s image(s) valide(s).",
        batch_id,
        processed_count,
        rejected_count,
        valid_image_count
    )

    return {
        "batch_id": batch_id,
        "articles_file": str(articles_file),
        "processed_article_count": processed_count,
        "rejected_after_image_processing_count": rejected_count,
        "valid_image_count": valid_image_count,
        "articles_without_valid_image_count": (
            articles_without_valid_image_count
        ),
        "image_task_duration_seconds": round(
            perf_counter() - task_started_at,
            3
        ),
        "image_processing_completed_at": get_extraction_date()
    }


# Tâche 3 : finalisation du lot

def finalize_extraction_batch_task(**context: Any) -> dict[str, Any]:
    """Produit le rapport final et nettoie les fichiers intermédiaires."""

    task_started_at = perf_counter()
    task_instance = context["ti"]
    extraction_manifest = task_instance.xcom_pull(
        task_ids="extract_sources"
    )
    image_manifest = task_instance.xcom_pull(
        task_ids="process_images"
    )

    if not isinstance(extraction_manifest, dict):
        raise RuntimeError("Le manifeste d'extraction est absent ou invalide.")

    if not isinstance(image_manifest, dict):
        raise RuntimeError("Le manifeste des images est absent ou invalide.")

    batch_id = normalize_batch_id(extraction_manifest.get("batch_id"))
    raw_report_file = Path(
        str(extraction_manifest.get("raw_report_file", ""))
    )
    raw_articles_file = Path(
        str(extraction_manifest.get("raw_articles_file", ""))
    )
    report_file = Path(
        str(extraction_manifest.get("report_file", ""))
    )
    articles_file = Path(
        str(image_manifest.get("articles_file", ""))
    )

    if not articles_file.is_file():
        raise FileNotFoundError(
            f"Le fichier final des articles est introuvable : {articles_file}."
        )

    report_payload = read_json_object(raw_report_file)
    extraction_status = report_payload.get("status", "unknown")

    report_payload.update({
        "status": "success",
        "extraction_status": extraction_status,
        "step": "extraction",
        "step_status": "success",
        "processed_article_count": image_manifest[
            "processed_article_count"
        ],
        "rejected_after_image_processing_count": image_manifest[
            "rejected_after_image_processing_count"
        ],
        "valid_image_count": image_manifest["valid_image_count"],
        "articles_without_valid_image_count": image_manifest[
            "articles_without_valid_image_count"
        ],
        "articles_file": str(articles_file),
        "report_file": str(report_file),
        "image_task_duration_seconds": image_manifest[
            "image_task_duration_seconds"
        ],
        "finalization_task_duration_seconds": round(
            perf_counter() - task_started_at,
            3
        ),
        "duration_seconds": calculate_total_duration(
            extraction_manifest.get("batch_started_at")
        ),
        "completed_at": get_extraction_date()
    })
    write_json_file(report_file, report_payload)

    # Les fichiers intermédiaires ne sont supprimés qu'après l'écriture réussie
    # du rapport final afin de préserver les possibilités de reprise sur erreur.
    remove_file_if_exists(raw_articles_file)
    remove_file_if_exists(raw_report_file)

    final_manifest = {
        "batch_id": batch_id,
        "status": "success",
        "articles_file": str(articles_file),
        "report_file": str(report_file),
        "article_count": image_manifest["processed_article_count"],
        "valid_image_count": image_manifest["valid_image_count"],
        "successful_extractors": extraction_manifest.get(
            "successful_extractors",
            0
        ),
        "failed_extractors": extraction_manifest.get(
            "failed_extractors",
            0
        )
    }

    logger.info(
        "Lot %s finalisé : %s article(s), %s image(s) valide(s).",
        batch_id,
        final_manifest["article_count"],
        final_manifest["valid_image_count"]
    )
    logger.info(
        "Fichier transmis à la transformation : %s.",
        articles_file
    )

    return final_manifest


# Définition du DAG

DEFAULT_ARGS = {
    "owner": "checkit_ai",
    "retries": 1,
    "retry_delay": timedelta(minutes=1)
}

with DAG(
    dag_id="checkit_extract",
    description=(
        "Extrait les articles, traite les images et produit un lot partagé."
    ),
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 7, 1, tzinfo=UTC),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    tags=["checkit", "etl", "extract", "multimodal"]
) as dag:

    start = EmptyOperator(task_id="start")

    extract_sources = PythonOperator(
        task_id="extract_sources",
        python_callable=run_sources_extraction_task,
        execution_timeout=EXTRACTION_TIMEOUT
    )

    process_images = PythonOperator(
        task_id="process_images",
        python_callable=run_image_processing_task,
        execution_timeout=IMAGE_PROCESSING_TIMEOUT
    )

    finalize_batch = PythonOperator(
        task_id="finalize_batch",
        python_callable=finalize_extraction_batch_task,
        execution_timeout=FINALIZATION_TIMEOUT
    )

    end = EmptyOperator(task_id="end")

    start >> extract_sources >> process_images >> finalize_batch >> end