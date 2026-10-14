"""Service d'orchestration du chargement PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/postgres/postgres_load_service.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import Any
from uuid import UUID

from psycopg2.extensions import connection as Connection

from src.logger import get_logger
from src.storage.postgres.postgres_loader import load_transformed_payload
from src.storage.postgres.postgres_storage import (
    create_pipeline_run,
    finalize_pipeline_run
)
from src.utils.parsing_utils import (
    parse_non_negative_integer,
    parse_optional_float
)

logger = get_logger(__name__)


# Compteurs des images

def count_images(
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
            or "pending"
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


def resolve_image_count(
    pipeline_metadata: Mapping[str, Any],
    field_name: str,
    calculated_value: int
) -> int:
    """Utilise le compteur du DAG ou la valeur recalculée en secours."""

    return parse_non_negative_integer(
        pipeline_metadata.get(
            field_name,
            calculated_value
        ),
        default=calculated_value
    )


# Métadonnées du pipeline

def build_run_metadata(
    pipeline_metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Prépare les métadonnées complémentaires du pipeline."""

    raw_metadata = pipeline_metadata.get(
        "run_metadata",
        {}
    )

    run_metadata = (
        dict(raw_metadata)
        if isinstance(raw_metadata, Mapping)
        else {}
    )

    return {
        "duplicate_count": parse_non_negative_integer(
            pipeline_metadata.get(
                "duplicate_count",
                0
            )
        ),
        **run_metadata
    }


# Gestion des erreurs

def rollback_connection(
    connection: Connection,
    error: Exception
) -> None:
    """Annule la transaction et ajoute le détail en cas d'échec."""

    try:
        connection.rollback()

    except Exception as rollback_error:
        error.add_note(
            f"Rollback PostgreSQL échoué : {rollback_error}"
        )


def finalize_failed_pipeline_run(
    connection: Connection,
    *,
    pipeline_run_id: UUID,
    duration_seconds: float,
    images_downloaded_count: int,
    images_valid_count: int,
    images_invalid_count: int,
    images_pending_count: int,
    error: Exception
) -> None:
    """Finalise un pipeline en échec dans une nouvelle transaction."""

    try:
        finalize_pipeline_run(
            connection,
            pipeline_run_id=pipeline_run_id,
            status="failed",
            loaded_count=0,
            load_duration_seconds=duration_seconds,
            images_downloaded_count=images_downloaded_count,
            images_valid_count=images_valid_count,
            images_invalid_count=images_invalid_count,
            images_pending_count=images_pending_count,
            error_message=str(error)
        )
        connection.commit()

    except Exception as finalization_error:
        try:
            connection.rollback()

        except Exception:
            pass

        error.add_note(
            "La finalisation du pipeline_run a échoué : "
            f"{finalization_error}"
        )


# Chargement du lot

def load_batch_to_postgres(
    connection: Connection,
    *,
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    features: list[dict[str, Any]],
    pipeline_metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Orchestre le chargement PostgreSQL complet d'un lot."""

    if not articles:
        raise RuntimeError(
            "Aucun article à charger dans PostgreSQL."
        )

    calculated_image_counts = count_images(images)

    images_downloaded_count = resolve_image_count(
        pipeline_metadata,
        "images_downloaded_count",
        calculated_image_counts["downloaded"]
    )
    images_valid_count = resolve_image_count(
        pipeline_metadata,
        "images_valid_count",
        calculated_image_counts["valid"]
    )
    images_invalid_count = resolve_image_count(
        pipeline_metadata,
        "images_invalid_count",
        calculated_image_counts["invalid"]
    )
    images_pending_count = resolve_image_count(
        pipeline_metadata,
        "images_pending_count",
        calculated_image_counts["pending"]
    )

    started_at = perf_counter()
    pipeline_run_id: UUID | None = None

    try:
        pipeline_run_id = create_pipeline_run(
            connection,
            dag_id=str(
                pipeline_metadata["dag_id"]
            ),
            airflow_run_id=str(
                pipeline_metadata["airflow_run_id"]
            ),
            batch_id=str(
                pipeline_metadata["batch_id"]
            ),
            extracted_count=parse_non_negative_integer(
                pipeline_metadata.get(
                    "extracted_count",
                    len(articles)
                ),
                default=len(articles)
            ),
            transformed_count=parse_non_negative_integer(
                pipeline_metadata.get(
                    "transformed_count",
                    len(articles)
                ),
                default=len(articles)
            ),
            rejected_count=parse_non_negative_integer(
                pipeline_metadata.get(
                    "rejected_count",
                    0
                )
            ),
            extraction_duration_seconds=parse_optional_float(
                pipeline_metadata.get(
                    "extraction_duration_seconds"
                )
            ),
            transformation_duration_seconds=parse_optional_float(
                pipeline_metadata.get(
                    "transformation_duration_seconds"
                )
            ),
            images_downloaded_count=images_downloaded_count,
            images_valid_count=images_valid_count,
            images_invalid_count=images_invalid_count,
            images_pending_count=images_pending_count,
            run_metadata=build_run_metadata(
                pipeline_metadata
            )
        )

        load_result = load_transformed_payload(
            connection,
            articles=articles,
            images=images,
            labels=labels,
            features=features,
            pipeline_run_id=pipeline_run_id
        )

        duration_seconds = round(
            perf_counter() - started_at,
            3
        )

        finalize_pipeline_run(
            connection,
            pipeline_run_id=pipeline_run_id,
            status="success",
            loaded_count=load_result["loaded_articles"],
            load_duration_seconds=duration_seconds,
            images_downloaded_count=images_downloaded_count,
            images_valid_count=images_valid_count,
            images_invalid_count=images_invalid_count,
            images_pending_count=images_pending_count
        )

        connection.commit()

        logger.info(
            "Lot PostgreSQL chargé : %s article(s), "
            "%s image(s) téléchargée(s), %s valide(s), "
            "%s invalide(s), %s en attente.",
            load_result["loaded_articles"],
            images_downloaded_count,
            images_valid_count,
            images_invalid_count,
            images_pending_count
        )

        return {
            "pipeline_run_id": str(pipeline_run_id),
            "status": "success",
            "load_duration_seconds": duration_seconds,
            "images_downloaded_count": images_downloaded_count,
            "images_valid_count": images_valid_count,
            "images_invalid_count": images_invalid_count,
            "images_pending_count": images_pending_count,
            **load_result
        }

    except Exception as error:
        rollback_connection(
            connection,
            error
        )

        duration_seconds = round(
            perf_counter() - started_at,
            3
        )

        if pipeline_run_id is not None:
            finalize_failed_pipeline_run(
                connection,
                pipeline_run_id=pipeline_run_id,
                duration_seconds=duration_seconds,
                images_downloaded_count=images_downloaded_count,
                images_valid_count=images_valid_count,
                images_invalid_count=images_invalid_count,
                images_pending_count=images_pending_count,
                error=error
            )

        else:
            logger.exception(
                "La création du pipeline_run PostgreSQL a échoué : %s",
                error
            )

        raise