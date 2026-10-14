"""Opérations SQL PostgreSQL élémentaires pour CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/postgres/postgres_storage.py` (commit 93632c5).
Modifié pour SortirIA : correspondance des sources réduite à OpenAgenda.

La validation du payload, le chargement métier et l'orchestration
transactionnelle sont délégués aux modules spécialisés.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg2.extensions import connection as Connection
from psycopg2.extras import Json

from src.logger import get_logger

logger = get_logger(__name__)

PIPELINE_VERSION = "1.0"
PRODUCER_NAME = "database_transformer"

# Type de chaque source connue, utilisé quand l'article ne le précise pas.
# Modifié pour SortirIA : les sources de presse et de fake news de CheckIt.AI
# ont été retirées ; on ajoute ici chaque source d'événements branchée.
SOURCE_TYPE_MAPPING: dict[str, str] = {
    "openagenda": "api",
}


def infer_source_type(source_key: str) -> str:
    """Détermine le type d'une source depuis son identifiant."""

    source_type = SOURCE_TYPE_MAPPING.get(source_key)
    if source_type is None:
        raise ValueError(f"Type inconnu pour la source {source_key!r}.")
    return source_type


def build_source_display_name(source_key: str) -> str:
    """Produit un nom lisible depuis un identifiant de source."""

    return source_key.replace("_", " ").title()


def create_pipeline_run(
    connection: Connection,
    *,
    dag_id: str,
    airflow_run_id: str,
    batch_id: str,
    extracted_count: int,
    transformed_count: int,
    rejected_count: int,
    extraction_duration_seconds: float | None = None,
    transformation_duration_seconds: float | None = None,
    images_downloaded_count: int = 0,
    images_valid_count: int = 0,
    images_invalid_count: int = 0,
    images_pending_count: int = 0,
    run_metadata: Mapping[str, Any] | None = None
) -> UUID:
    """Crée une exécution de pipeline avec le statut running."""

    metadata = {
        "batch_id": batch_id,
        **dict(run_metadata or {})
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO checkit.pipeline_runs (
                dag_id,
                airflow_run_id,
                pipeline_name,
                pipeline_version,
                status,
                extraction_duration_seconds,
                transformation_duration_seconds,
                extracted_count,
                transformed_count,
                valid_count,
                rejected_count,
                images_downloaded_count,
                images_valid_count,
                images_invalid_count,
                images_pending_count,
                run_metadata
            )
            VALUES (
                %s,
                %s,
                'checkit_etl',
                %s,
                'running',
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            RETURNING id;
            """,
            (
                dag_id,
                airflow_run_id,
                PIPELINE_VERSION,
                extraction_duration_seconds,
                transformation_duration_seconds,
                extracted_count,
                transformed_count,
                transformed_count,
                rejected_count,
                images_downloaded_count,
                images_valid_count,
                images_invalid_count,
                images_pending_count,
                Json(metadata)
            )
        )
        result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "Impossible de créer l'exécution du pipeline."
        )

    # Conserve la trace du run même si le chargement métier échoue ensuite.
    connection.commit()

    return result[0]


def finalize_pipeline_run(
    connection: Connection,
    *,
    pipeline_run_id: UUID,
    status: str,
    loaded_count: int,
    load_duration_seconds: float | None = None,
    images_downloaded_count: int = 0,
    images_valid_count: int = 0,
    images_invalid_count: int = 0,
    images_pending_count: int = 0,
    error_message: str | None = None
) -> None:
    """Finalise une exécution de pipeline."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE checkit.pipeline_runs
            SET
                status = %s,
                loaded_count = %s,
                load_duration_seconds = %s,
                images_downloaded_count = %s,
                images_valid_count = %s,
                images_invalid_count = %s,
                images_pending_count = %s,
                finished_at = CURRENT_TIMESTAMP,
                duration_seconds = EXTRACT(
                    EPOCH FROM (CURRENT_TIMESTAMP - started_at)
                ),
                error_message = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s;
            """,
            (
                status,
                loaded_count,
                load_duration_seconds,
                images_downloaded_count,
                images_valid_count,
                images_invalid_count,
                images_pending_count,
                error_message,
                pipeline_run_id
            )
        )

    connection.commit()

def upsert_source(
    connection: Connection,
    *,
    source_key: str,
    default_language: str | None = None,
) -> int:
    """Crée ou met à jour une source."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO checkit.sources (
                source_key, display_name, source_type,
                default_language, is_active
            )
            VALUES (%s, %s, %s, %s, TRUE)
            ON CONFLICT (source_key)
            DO UPDATE SET
                display_name = EXCLUDED.display_name,
                source_type = EXCLUDED.source_type,
                default_language = COALESCE(
                    EXCLUDED.default_language,
                    checkit.sources.default_language
                ),
                is_active = TRUE,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id;
            """,
            (
                source_key,
                build_source_display_name(source_key),
                infer_source_type(source_key),
                default_language,
            ),
        )
        result = cursor.fetchone()

    if result is None:
        raise RuntimeError(f"Impossible de créer la source {source_key!r}.")
    return int(result[0])


def find_existing_article_id(
    connection: Connection,
    canonical_url: str | None,
) -> str | None:
    """Recherche un article grâce à son URL canonique."""

    if not canonical_url:
        return None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM checkit.articles
            WHERE canonical_url = %s
            LIMIT 1;
            """,
            (canonical_url,),
        )
        result = cursor.fetchone()

    return str(result[0]) if result else None


def upsert_article(
    connection: Connection,
    *,
    article: Mapping[str, Any],
    source_id: int,
    pipeline_run_id: UUID,
) -> str:
    """Insère ou met à jour un article."""

    requested_article_id = str(article["id"])
    database_article_id = (
        find_existing_article_id(connection, article.get("canonical_url"))
        or requested_article_id
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO checkit.articles (
                id, source_id, pipeline_run_id, external_id,
                title, content, original_url, canonical_url,
                author, language, category, published_at,
                extracted_at, transformed_at, dataset_role,
                data_quality_status, rejection_reason,
                transformation_version, title_hash, content_hash,
                canonical_url_hash, raw_payload
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (id)
            DO UPDATE SET
                source_id = EXCLUDED.source_id,
                pipeline_run_id = EXCLUDED.pipeline_run_id,
                external_id = EXCLUDED.external_id,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                original_url = EXCLUDED.original_url,
                canonical_url = EXCLUDED.canonical_url,
                author = EXCLUDED.author,
                language = EXCLUDED.language,
                category = EXCLUDED.category,
                published_at = EXCLUDED.published_at,
                extracted_at = EXCLUDED.extracted_at,
                transformed_at = EXCLUDED.transformed_at,
                dataset_role = EXCLUDED.dataset_role,
                data_quality_status = EXCLUDED.data_quality_status,
                rejection_reason = EXCLUDED.rejection_reason,
                transformation_version = EXCLUDED.transformation_version,
                title_hash = EXCLUDED.title_hash,
                content_hash = EXCLUDED.content_hash,
                canonical_url_hash = EXCLUDED.canonical_url_hash,
                raw_payload = EXCLUDED.raw_payload,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (
                database_article_id,
                source_id,
                pipeline_run_id,
                article.get("external_id"),
                article["title"],
                article.get("content", ""),
                article.get("original_url"),
                article.get("canonical_url"),
                article.get("author"),
                article.get("language"),
                article.get("category"),
                article.get("published_at"),
                article.get("extracted_at"),
                article.get("transformed_at"),
                article.get("dataset_role"),
                article.get("data_quality_status", "valid"),
                article.get("rejection_reason"),
                article.get("transformation_version", PIPELINE_VERSION),
                article.get("title_hash"),
                article.get("content_hash"),
                article.get("canonical_url_hash"),
                Json(article.get("raw_payload", {})),
            ),
        )

    return database_article_id


def replace_primary_image(
    connection: Connection,
    *,
    image: Mapping[str, Any],
    article_id: str,
) -> None:
    """Remplace l'image principale d'un article."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM checkit.images
            WHERE article_id = %s
              AND is_primary = TRUE;
            """,
            (article_id,),
        )
        cursor.execute(
            """
            INSERT INTO checkit.images (
                article_id, remote_url, local_path, file_name,
                file_extension, file_format, mime_type, width,
                height, aspect_ratio, size_bytes, file_hash,
                perceptual_hash, download_duration_ms, image_position,
                is_primary, blur_score, brightness_score, entropy_score,
                is_valid, validation_status, validation_error,
                association_status, association_score, association_method,
                downloaded_at, validated_at, associated_at, image_metadata
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            );
            """,
            (
                article_id,
                image.get("remote_url"),
                image.get("local_path"),
                image.get("file_name"),
                image.get("file_extension"),
                image.get("file_format"),
                image.get("mime_type"),
                image.get("width"),
                image.get("height"),
                image.get("aspect_ratio"),
                image.get("size_bytes"),
                image.get("file_hash"),
                image.get("perceptual_hash"),
                image.get("download_duration_ms"),
                image.get("image_position", 0),
                image.get("is_primary", True),
                image.get("blur_score"),
                image.get("brightness_score"),
                image.get("entropy_score"),
                image.get("is_valid", False),
                image.get("validation_status", "pending"),
                image.get("validation_error"),
                image.get("association_status", "unchecked"),
                image.get("association_score"),
                image.get("association_method"),
                image.get("downloaded_at"),
                image.get("validated_at"),
                image.get("associated_at"),
                Json(image.get("image_metadata", {})),
            ),
        )


def validate_label_record(label: Mapping[str, Any]) -> tuple[bool, str]:
    """Vérifie les champs obligatoires d'un label."""

    label_value = label.get("label")
    label_type = label.get("label_type")

    if label_value is None or not str(label_value).strip():
        return False, "champ label absent"
    if label_type is None or not str(label_type).strip():
        return False, "champ label_type absent"
    return True, ""


def upsert_label(
    connection: Connection,
    *,
    label: Mapping[str, Any],
    article_id: str,
) -> bool:
    """Insère un label valide et ignore un enregistrement mal formé."""

    is_valid, reason = validate_label_record(label)
    if not is_valid:
        logger.warning("Label ignoré pour l'article %s : %s.", article_id, reason)
        return False

    label_value = label.get("label")
    label_type = label.get("label_type")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM checkit.article_labels
            WHERE article_id = %s
              AND label = %s
              AND label_type = %s
              AND label_source IS NOT DISTINCT FROM %s
            LIMIT 1;
            """,
            (
                article_id,
                label_value,
                label_type,
                label.get("label_source"),
            ),
        )
        existing_label = cursor.fetchone()
        values = (
            label.get("annotator"),
            label.get("annotation_method"),
            label.get("confidence"),
            label.get("is_ground_truth", False),
            label.get("is_active", True),
            label.get("notes"),
            Json(label.get("label_metadata", {})),
        )

        if existing_label:
            cursor.execute(
                """
                UPDATE checkit.article_labels
                SET
                    annotator = %s,
                    annotation_method = %s,
                    confidence = %s,
                    is_ground_truth = %s,
                    is_active = %s,
                    notes = %s,
                    label_metadata = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s;
                """,
                (*values, existing_label[0]),
            )
        else:
            cursor.execute(
                """
                INSERT INTO checkit.article_labels (
                    article_id, label, label_type, label_source,
                    annotator, annotation_method, confidence,
                    is_ground_truth, is_active, notes, label_metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    article_id,
                    label_value,
                    label_type,
                    label.get("label_source"),
                    *values,
                ),
            )

    return True


def validate_feature_record(feature: Mapping[str, Any]) -> tuple[bool, str]:
    """Vérifie les champs obligatoires d'une feature."""

    for field in ("feature_group", "feature_name", "feature_type"):
        value = feature.get(field)
        if value is None or not str(value).strip():
            return False, f"champ {field} absent"
    return True, ""


def replace_article_features(
    connection: Connection,
    *,
    features: list[Mapping[str, Any]],
    article_id: str,
    pipeline_run_id: UUID,
) -> tuple[int, int]:
    """Remplace les features valides produites pour un article."""

    loaded_count = ignored_count = 0

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM checkit.article_features
            WHERE article_id = %s
              AND producer_name = %s;
            """,
            (article_id, PRODUCER_NAME),
        )

        for feature in features:
            if not isinstance(feature, Mapping):
                ignored_count += 1
                logger.warning(
                    "Feature ignorée pour l'article %s : format %s.",
                    article_id,
                    type(feature).__name__,
                )
                continue

            is_valid, reason = validate_feature_record(feature)
            if not is_valid:
                ignored_count += 1
                logger.warning(
                    "Feature ignorée pour l'article %s : %s.",
                    article_id,
                    reason,
                )
                continue

            cursor.execute(
                """
                INSERT INTO checkit.article_features (
                    article_id, image_id, pipeline_run_id,
                    feature_group, feature_name, feature_type,
                    feature_version, producer_name, producer_version,
                    numeric_value, text_value, boolean_value, json_value,
                    vector_path, vector_dimension, confidence,
                    feature_metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                );
                """,
                (
                    article_id,
                    feature.get("image_id"),
                    pipeline_run_id,
                    feature.get("feature_group"),
                    feature.get("feature_name"),
                    feature.get("feature_type"),
                    feature.get("feature_version", PIPELINE_VERSION),
                    feature.get("producer_name", PRODUCER_NAME),
                    feature.get("producer_version", PIPELINE_VERSION),
                    feature.get("numeric_value"),
                    feature.get("text_value"),
                    feature.get("boolean_value"),
                    (
                        Json(feature.get("json_value"))
                        if feature.get("json_value") is not None
                        else None
                    ),
                    feature.get("vector_path"),
                    feature.get("vector_dimension"),
                    feature.get("confidence"),
                    Json(feature.get("feature_metadata", {})),
                ),
            )
            loaded_count += 1

    return loaded_count, ignored_count