"""Adaptation des articles enrichis au modèle PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/transformers/database_transformer.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.utils.date_utils import (
    convert_optional_date_to_iso,
    get_extraction_date,
)

from src.article.schema.article_schema import DATASET_ROLES
from src.utils.parsing_utils import (
    normalize_optional_text, parse_boolean, parse_optional_float,
    parse_optional_integer
)

from config.constants import (
    IMAGE_VALIDATION_STATUS_ERROR,
    IMAGE_VALIDATION_STATUS_INVALID,
    IMAGE_VALIDATION_STATUS_VALID,
    IMAGE_VALIDATION_STATUS_PENDING
)

TRANSFORMATION_VERSION = "1.0"
FEATURE_PRODUCER_NAME = "database_transformer"
CONTAINER_DATA_DIRECTORY = Path("/opt/airflow/checkit_ai/data")

SOURCE_KEY_ALIASES = {
    "le_monde_international_rss": "le_monde_international",
    "the_guardian_world": "guardian_world",
    "france_info": "franceinfo",
    "newsdata_io": "newsdata"
}

FEATURE_FIELDS: dict[str, str] = {
    "has_title": "boolean",
    "has_text": "boolean",
    "title_length": "numeric",
    "text_length": "numeric",
    "total_text_length": "numeric",
    "title_word_count": "numeric",
    "text_word_count": "numeric",
    "total_word_count": "numeric",
    "has_publication_date": "boolean",
    "publication_year": "numeric",
    "publication_month": "numeric",
    "publication_day": "numeric",
    "publication_hour": "numeric",
    "publication_weekday": "numeric",
    "has_image_url": "boolean",
    "has_image_path": "boolean",
    "image_exists": "boolean",
    "image_is_valid": "boolean",
    "text_image_association_valid": "boolean",
    "has_url": "boolean",
    "has_author": "boolean",
    "has_label": "boolean",
    "is_labeled": "boolean",
    "is_multimodal": "boolean"
}


# Normalisation commune
def normalize_required_text(value: Any) -> str:
    return str(value or "").strip()


def make_json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def normalize_source_key(value: Any) -> str:
    source_key = re.sub(
        r"[^a-z0-9]+", "_", normalize_required_text(value).lower()
    ).strip("_") or "unknown"
    return SOURCE_KEY_ALIASES.get(source_key, source_key)


def normalize_url(value: Any) -> str | None:
    url = normalize_optional_text(value)
    if url is None:
        return None
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(),
        parsed.path or "/", parsed.query, ""
    ))


def compute_sha256(value: Any) -> str | None:
    value = normalize_optional_text(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def build_article_id(article: dict[str, Any]) -> str:
    existing_id = normalize_optional_text(article.get("id"))
    if existing_id:
        return existing_id[:64]
    identity = (
        normalize_url(article.get("canonical_url") or article.get("url"))
        or normalize_optional_text(article.get("title"))
        or json.dumps(make_json_safe(article), sort_keys=True, ensure_ascii=False)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def normalize_dataset_role(value: Any) -> str:
    role = (normalize_optional_text(value) or "").lower()
    return role if role in DATASET_ROLES else "acquisition"


def normalize_local_image_path(value: Any) -> str | None:
    image_path = normalize_optional_text(value)
    if image_path is None:
        return None
    portable = image_path.replace("\\", "/")
    lower = portable.lower()
    if "/data/" in lower:
        relative = portable[lower.find("/data/") + len("/data/"):].lstrip("/")
        return str(CONTAINER_DATA_DIRECTORY / relative)
    if lower.startswith("data/"):
        return str(CONTAINER_DATA_DIRECTORY / portable[len("data/"):].lstrip("/"))
    return portable if Path(portable).is_absolute() else str(
        CONTAINER_DATA_DIRECTORY / portable.lstrip("/")
    )


def get_remote_file_name(remote_url: str | None) -> str | None:
    if remote_url is None:
        return None
    try:
        return Path(urlsplit(remote_url).path).name or None
    except (TypeError, ValueError):
        return None


# Mapping PostgreSQL
def map_article_record(
    article: dict[str, Any], batch_id: str, transformed_at: str
) -> dict[str, Any]:
    article_id = build_article_id(article)
    title = normalize_required_text(article.get("title"))
    content = normalize_required_text(
        article.get("text") or article.get("content")
        or article.get("summary") or article.get("description")
    )
    original_url = normalize_url(article.get("url"))
    canonical_url = normalize_url(article.get("canonical_url")) or original_url

    return {
        "id": article_id,
        "source_key": normalize_source_key(
            article.get("source_id") or article.get("source")
        ),
        "batch_id": batch_id,
        "external_id": normalize_optional_text(article.get("external_id")),
        "title": title,
        "content": content,
        "original_url": original_url,
        "canonical_url": canonical_url,
        "author": normalize_optional_text(article.get("author")),
        "language": normalize_optional_text(article.get("language")),
        "category": normalize_optional_text(article.get("category")),
        "published_at": convert_optional_date_to_iso(article.get("published_at")),
        "extracted_at": (
            convert_optional_date_to_iso(
                article.get("extraction_date") or article.get("extracted_at")
            ) or transformed_at
        ),
        "transformed_at": transformed_at,
        "dataset_role": normalize_dataset_role(article.get("dataset_role")),
        "data_quality_status": (
            normalize_optional_text(article.get("data_quality_status")) or "valid"
        ).lower(),
        "rejection_reason": normalize_optional_text(article.get("rejection_reason")),
        "transformation_version": TRANSFORMATION_VERSION,
        "title_hash": compute_sha256(title),
        "content_hash": compute_sha256(content),
        "canonical_url_hash": compute_sha256(canonical_url),
        "raw_payload": make_json_safe(article)
    }


def map_image_record(
    article: dict[str, Any],
    article_id: str
) -> dict[str, Any] | None:
    """Transforme les informations d'image pour PostgreSQL."""

    remote_url = normalize_url(article.get("image_url"))
    local_path = normalize_local_image_path(article.get("image_path"))

    if remote_url is None and local_path is None:
        return None

    file_name = (
        Path(local_path).name
        if local_path
        else get_remote_file_name(remote_url)
    )

    file_extension = Path(file_name).suffix.lower() if file_name else None

    if not file_extension:
        file_extension = None

    width = parse_optional_integer(article.get("image_width"))
    height = parse_optional_integer(article.get("image_height"))
    is_valid = parse_boolean(article.get("image_is_valid"), default=False)

    validation_error = normalize_optional_text(
        article.get("image_validation_error")
        or article.get("image_download_error")
    )
    download_status = (
        normalize_optional_text(article.get("image_download_status")) or ""
    ).lower()

    # Une image reste en attente uniquement lorsqu'aucun traitement n'a encore
    # fourni de résultat. Toute erreur connue produit un statut invalide.
    failed_download_statuses = {
        "blocked_redirect",
        "blocked_url",
        "error",
        "failed",
        "forbidden",
        "http_error",
        "invalid",
        "invalid_content",
        "invalid_path",
        "invalid_url",
        "not_found",
        "processing_error",
        "quota_exceeded",
        "redirect_error",
        "server_error",
        "timeout",
        "too_large",
        "unexpected_error",
        "write_error"
    }

    source_validation_status = (
        normalize_optional_text(article.get("image_validation_status")) or ""
    ).lower()

    if source_validation_status == IMAGE_VALIDATION_STATUS_ERROR:
        validation_status = IMAGE_VALIDATION_STATUS_ERROR
    elif is_valid:
        validation_status = IMAGE_VALIDATION_STATUS_VALID
        validation_error = None
    elif (
        source_validation_status == IMAGE_VALIDATION_STATUS_INVALID
        or validation_error
        or download_status in failed_download_statuses
    ):
        validation_status = IMAGE_VALIDATION_STATUS_INVALID
    else:
        validation_status = IMAGE_VALIDATION_STATUS_PENDING
    
    # L'association actuelle est uniquement technique : le nom du fichier
    # contient l'identifiant de l'article. Elle ne constitue pas encore une
    # vérification sémantique entre le texte et l'image.
    association_status = "technical_match" if local_path else "unchecked"
    association_method = "filename" if local_path else None
    association_score = 1.0 if local_path else None

    return {
        "article_id": article_id,
        "remote_url": remote_url,
        "local_path": local_path,
        "file_name": file_name,
        "file_extension": file_extension,
        "file_format": normalize_optional_text(article.get("image_format")),
        "mime_type": normalize_optional_text(article.get("image_mime_type")),
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 6) if width and height else None,
        "size_bytes": parse_optional_integer(article.get("image_size_bytes")),
        "file_hash": normalize_optional_text(article.get("image_file_hash")),
        "perceptual_hash": normalize_optional_text(article.get("perceptual_hash")),
        "download_duration_ms": parse_optional_integer(
            article.get("image_download_duration_ms")
        ),
        "image_position": 0,
        "is_primary": True,
        "blur_score": parse_optional_float(article.get("blur_score")),
        "brightness_score": parse_optional_float(article.get("brightness_score")),
        "entropy_score": parse_optional_float(article.get("entropy_score")),
        "is_valid": is_valid,
        "validation_status": validation_status,
        "validation_error": validation_error,
        "association_status": association_status,
        "association_score": association_score,
        "association_method": association_method,
        "downloaded_at": convert_optional_date_to_iso(
            article.get("image_downloaded_at")
        ),
        "validated_at": convert_optional_date_to_iso(
            article.get("image_validated_at")
        ),
        "associated_at": None,
        "image_metadata": make_json_safe(article.get("image_metadata", {}))
    }


def map_label_record(
    article: dict[str, Any], article_id: str
) -> dict[str, Any] | None:
    label = normalize_optional_text(article.get("label"))
    if label is None:
        return None

    role = normalize_dataset_role(article.get("dataset_role"))
    if role == "fact_check_reference":
        label_type, method = "fact_check", "external_fact_check"
    elif role in {
        "labeled_reference", "multimodal_reference", "social_reference"
    }:
        label_type, method = "dataset", "dataset_import"
    else:
        label_type, method = "manual", "manual_review"

    return {
        "article_id": article_id,
        "label": label,
        "label_type": label_type,
        "label_source": normalize_optional_text(article.get("source")),
        "annotator": normalize_optional_text(article.get("annotator")),
        "annotation_method": method,
        "confidence": parse_optional_float(article.get("label_confidence")),
        "is_ground_truth": parse_boolean(
            article.get("is_ground_truth"),
            default=role in DATASET_ROLES - {"acquisition"}
        ),
        "is_active": parse_boolean(article.get("label_is_active"), default=True),
        "notes": normalize_optional_text(article.get("label_notes")),
        "label_metadata": make_json_safe(article.get("label_metadata", {}))
    }


def build_feature_record(
    article_id: str, feature_name: str, feature_type: str, value: Any
) -> dict[str, Any]:
    record = {
        "article_id": article_id,
        "image_id": None,
        "pipeline_run_id": None,
        "feature_group": "metadata",
        "feature_name": feature_name,
        "feature_type": feature_type,
        "feature_version": TRANSFORMATION_VERSION,
        "producer_name": FEATURE_PRODUCER_NAME,
        "producer_version": TRANSFORMATION_VERSION,
        "numeric_value": None,
        "text_value": None,
        "boolean_value": None,
        "json_value": None,
        "vector_path": None,
        "vector_dimension": None,
        "confidence": None,
        "feature_metadata": {}
    }
    if feature_type == "numeric":
        record["numeric_value"] = parse_optional_float(value)
    elif feature_type == "boolean":
        record["boolean_value"] = parse_boolean(value)
    else:
        raise ValueError(f"Type de feature non supporté : {feature_type!r}.")
    return record


def map_feature_records(
    article: dict[str, Any], article_id: str
) -> list[dict[str, Any]]:
    return [
        build_feature_record(article_id, name, feature_type, article[name])
        for name, feature_type in FEATURE_FIELDS.items()
        if article.get(name) is not None
    ]


# Transformation complète
def transform_articles_for_database(
    articles_to_transform: list[dict[str, Any]], batch_id: str
) -> dict[str, list[dict[str, Any]]]:
    transformed_at = get_extraction_date()
    payload = {"articles": [], "images": [], "labels": [], "features": []}
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    for article in articles_to_transform:
        if not isinstance(article, dict):
            continue

        article_record = map_article_record(article, batch_id, transformed_at)
        article_id = article_record["id"]
        canonical_url = article_record["canonical_url"]

        if article_id in seen_ids or canonical_url and canonical_url in seen_urls:
            continue

        seen_ids.add(article_id)
        if canonical_url:
            seen_urls.add(canonical_url)

        payload["articles"].append(article_record)
        image_record = map_image_record(article, article_id)
        label_record = map_label_record(article, article_id)

        if image_record:
            payload["images"].append(image_record)
        if label_record:
            payload["labels"].append(label_record)

        payload["features"].extend(map_feature_records(article, article_id))

    return payload