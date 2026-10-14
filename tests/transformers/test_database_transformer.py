"""Tests du transformateur PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/transformers/test_database_transformer.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import src.transformers.database_transformer as module
from src.transformers.database_transformer import (
    CONTAINER_DATA_DIRECTORY,
    FEATURE_FIELDS,
    TRANSFORMATION_VERSION,
    build_article_id,
    build_feature_record,
    compute_sha256,
    get_remote_file_name,
    make_json_safe,
    map_article_record,
    map_feature_records,
    map_image_record,
    map_label_record,
    normalize_dataset_role,
    normalize_local_image_path,
    normalize_required_text,
    normalize_source_key,
    normalize_url,
    transform_articles_for_database
)


# Normalisation commune

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("  article  ", "article"),
        (42, "42"),
        (False, "")
    ]
)
def test_normalize_required_text(value: Any, expected: str) -> None:
    assert normalize_required_text(value) == expected


def test_make_json_safe_preserves_serializable_value() -> None:
    value = {"items": [1, 2, 3], "active": True}

    result = make_json_safe(value)

    assert result is value


def test_make_json_safe_converts_non_serializable_values() -> None:
    value = {
        "path": Path("data/article.json"),
        "date": datetime(2026, 7, 27, tzinfo=UTC)
    }

    result = make_json_safe(value)

    assert result == {
        "path": str(Path("data/article.json")),
        "date": "2026-07-27 00:00:00+00:00"
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Le Monde International RSS", "le_monde_international"),
        ("the_guardian_world", "guardian_world"),
        ("France Info", "franceinfo"),
        ("NewsData.io", "newsdata"),
        ("BBC Technology", "bbc_technology"),
        ("  Source spéciale !  ", "source_sp_ciale"),
        ("", "unknown"),
        (None, "unknown")
    ]
)
def test_normalize_source_key(value: Any, expected: str) -> None:
    assert normalize_source_key(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "HTTPS://Example.COM",
            "https://example.com/"
        ),
        (
            "https://Example.com/news/article?id=4#section",
            "https://example.com/news/article?id=4"
        ),
        (
            "http://example.com",
            "http://example.com/"
        ),
        (None, None),
        ("", None),
        ("ftp://example.com/file", None),
        ("example.com/article", None),
        ("https:///article", None)
    ]
)
def test_normalize_url(value: Any, expected: str | None) -> None:
    assert normalize_url(value) == expected


def test_normalize_url_handles_urlsplit_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "urlsplit",
        lambda value: (_ for _ in ()).throw(ValueError("Erreur simulée"))
    )

    assert normalize_url("https://example.com") is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_compute_sha256_returns_none_for_empty_value(value: Any) -> None:
    assert compute_sha256(value) is None


def test_compute_sha256_returns_expected_hash() -> None:
    value = "CheckIt.AI"

    assert compute_sha256(value) == hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def test_build_article_id_preserves_existing_identifier() -> None:
    article = {"id": "article-123"}

    assert build_article_id(article) == "article-123"


def test_build_article_id_truncates_existing_identifier() -> None:
    article = {"id": "a" * 100}

    result = build_article_id(article)

    assert result == "a" * 64


def test_build_article_id_uses_canonical_url_first() -> None:
    article = {
        "canonical_url": "https://example.com/canonical",
        "url": "https://example.com/original",
        "title": "Titre"
    }

    expected = hashlib.sha256(
        b"https://example.com/canonical"
    ).hexdigest()[:32]

    assert build_article_id(article) == expected


def test_build_article_id_uses_original_url() -> None:
    article = {
        "url": "https://example.com/article",
        "title": "Titre"
    }

    expected = hashlib.sha256(
        b"https://example.com/article"
    ).hexdigest()[:32]

    assert build_article_id(article) == expected


def test_build_article_id_uses_title_when_url_is_invalid() -> None:
    article = {
        "url": "url-invalide",
        "title": "Titre de référence"
    }

    expected = hashlib.sha256(
        b"Titre de r\xc3\xa9f\xc3\xa9rence"
    ).hexdigest()[:32]

    assert build_article_id(article) == expected


def test_build_article_id_uses_serialized_payload_as_last_fallback() -> None:
    article = {"metadata": {"path": Path("data/file.json")}}

    identity = json.dumps(
        make_json_safe(article),
        sort_keys=True,
        ensure_ascii=False
    )
    expected = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    assert build_article_id(article) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("acquisition", "acquisition"),
        ("LABELED_REFERENCE", "labeled_reference"),
        (" multimodal_reference ", "multimodal_reference"),
        ("social_reference", "social_reference"),
        ("fact_check_reference", "fact_check_reference"),
        ("unknown_role", "acquisition"),
        (None, "acquisition")
    ]
)
def test_normalize_dataset_role(value: Any, expected: str) -> None:
    assert normalize_dataset_role(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (
            "data/images/article.jpg",
            str(CONTAINER_DATA_DIRECTORY / "images/article.jpg")
        ),
        (
            "/opt/airflow/checkit_ai/data/images/article.jpg",
            str(CONTAINER_DATA_DIRECTORY / "images/article.jpg")
        ),
        (
            r"C:\project\data\images\article.jpg",
            str(CONTAINER_DATA_DIRECTORY / "images/article.jpg")
        ),
        (
            "images/article.jpg",
            str(CONTAINER_DATA_DIRECTORY / "images/article.jpg")
        )
    ]
)
def test_normalize_local_image_path(
    value: Any,
    expected: str | None
) -> None:
    assert normalize_local_image_path(value) == expected


def test_normalize_local_image_path_preserves_absolute_path(
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    assert normalize_local_image_path(image_path) == image_path.as_posix()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/images/article.jpg", "article.jpg"),
        ("https://example.com/images/", "images"),
        ("https://example.com", None),
        (None, None)
    ]
)
def test_get_remote_file_name(
    value: str | None,
    expected: str | None
) -> None:
    assert get_remote_file_name(value) == expected


def test_get_remote_file_name_handles_urlsplit_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "urlsplit",
        lambda value: (_ for _ in ()).throw(TypeError("Erreur simulée"))
    )

    assert get_remote_file_name("https://example.com/image.jpg") is None


# Mapping des articles

def test_map_article_record_builds_complete_record(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "convert_optional_date_to_iso",
        lambda value: {
            "2026-07-25": "2026-07-25T00:00:00+00:00",
            "2026-07-26": "2026-07-26T00:00:00+00:00"
        }.get(value)
    )

    article = {
        "id": "article-1",
        "source_id": "NewsData.io",
        "source": "NewsData",
        "external_id": "external-1",
        "title": "  Titre de l'article  ",
        "text": "  Contenu principal  ",
        "url": "HTTPS://Example.COM/article#fragment",
        "canonical_url": "https://example.com/canonical",
        "author": "Auteur",
        "language": "fr",
        "category": "actualité",
        "published_at": "2026-07-25",
        "extraction_date": "2026-07-26",
        "dataset_role": "labeled_reference",
        "data_quality_status": "VALID",
        "rejection_reason": "aucune"
    }

    result = map_article_record(
        article,
        batch_id="batch-1",
        transformed_at="2026-07-27T10:00:00+00:00"
    )

    assert result["id"] == "article-1"
    assert result["source_key"] == "newsdata"
    assert result["batch_id"] == "batch-1"
    assert result["external_id"] == "external-1"
    assert result["title"] == "Titre de l'article"
    assert result["content"] == "Contenu principal"
    assert result["original_url"] == "https://example.com/article"
    assert result["canonical_url"] == "https://example.com/canonical"
    assert result["author"] == "Auteur"
    assert result["language"] == "fr"
    assert result["category"] == "actualité"
    assert result["published_at"] == "2026-07-25T00:00:00+00:00"
    assert result["extracted_at"] == "2026-07-26T00:00:00+00:00"
    assert result["transformed_at"] == "2026-07-27T10:00:00+00:00"
    assert result["dataset_role"] == "labeled_reference"
    assert result["data_quality_status"] == "valid"
    assert result["rejection_reason"] == "aucune"
    assert result["transformation_version"] == TRANSFORMATION_VERSION
    assert result["title_hash"] == compute_sha256("Titre de l'article")
    assert result["content_hash"] == compute_sha256("Contenu principal")
    assert result["canonical_url_hash"] == compute_sha256(
        "https://example.com/canonical"
    )
    assert result["raw_payload"] == article


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "Texte"),
        ("content", "Contenu"),
        ("summary", "Résumé"),
        ("description", "Description")
    ]
)
def test_map_article_record_uses_content_fallbacks(
    field: str,
    value: str
) -> None:
    article = {"id": "article-1", "title": "Titre", field: value}

    result = map_article_record(article, "batch", "date")

    assert result["content"] == value


def test_map_article_record_uses_original_url_as_canonical_url() -> None:
    article = {
        "id": "article-1",
        "title": "Titre",
        "url": "https://example.com/article"
    }

    result = map_article_record(article, "batch", "date")

    assert result["canonical_url"] == "https://example.com/article"


def test_map_article_record_uses_transformed_at_as_extraction_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "convert_optional_date_to_iso",
        lambda value: None
    )

    result = map_article_record(
        {"id": "article-1", "title": "Titre"},
        "batch",
        "2026-07-27T10:00:00+00:00"
    )

    assert result["extracted_at"] == "2026-07-27T10:00:00+00:00"


def test_map_article_record_uses_default_values() -> None:
    result = map_article_record({}, "batch", "date")

    assert result["source_key"] == "unknown"
    assert result["title"] == ""
    assert result["content"] == ""
    assert result["dataset_role"] == "acquisition"
    assert result["data_quality_status"] == "valid"


# Mapping des images

def test_map_image_record_returns_none_without_image_information() -> None:
    assert map_image_record({}, "article-1") is None


def test_map_image_record_builds_complete_valid_image() -> None:
    article = {
        "image_url": "https://example.com/images/photo.JPG#fragment",
        "image_path": "data/images/photo.JPG",
        "image_width": "800",
        "image_height": "600",
        "image_is_valid": "true",
        "image_validation_error": "ancienne erreur",
        "image_format": "JPEG",
        "image_mime_type": "image/jpeg",
        "image_size_bytes": "12345",
        "image_file_hash": "hash",
        "perceptual_hash": "phash",
        "image_download_duration_ms": "150",
        "blur_score": "2.5",
        "brightness_score": "120.5",
        "entropy_score": "7.25",
        "image_downloaded_at": "2026-07-25",
        "image_validated_at": "2026-07-26",
        "image_metadata": {"animated": False}
    }

    result = map_image_record(article, "article-1")

    assert result is not None
    assert result["article_id"] == "article-1"
    assert result["remote_url"] == "https://example.com/images/photo.JPG"
    assert result["local_path"] == str(
        CONTAINER_DATA_DIRECTORY / "images/photo.JPG"
    )
    assert result["file_name"] == "photo.JPG"
    assert result["file_extension"] == ".jpg"
    assert result["file_format"] == "JPEG"
    assert result["mime_type"] == "image/jpeg"
    assert result["width"] == 800
    assert result["height"] == 600
    assert result["aspect_ratio"] == 1.333333
    assert result["size_bytes"] == 12345
    assert result["file_hash"] == "hash"
    assert result["perceptual_hash"] == "phash"
    assert result["download_duration_ms"] == 150
    assert result["image_position"] == 0
    assert result["is_primary"] is True
    assert result["blur_score"] == 2.5
    assert result["brightness_score"] == 120.5
    assert result["entropy_score"] == 7.25
    assert result["is_valid"] is True
    assert result["validation_status"] == module.IMAGE_VALIDATION_STATUS_VALID
    assert result["validation_error"] is None
    assert result["association_status"] == "technical_match"
    assert result["association_score"] == 1.0
    assert result["association_method"] == "filename"
    assert result["associated_at"] is None
    assert result["image_metadata"] == {"animated": False}


def test_map_image_record_uses_remote_file_name_without_local_path() -> None:
    result = map_image_record(
        {"image_url": "https://example.com/images/photo.png"},
        "article-1"
    )

    assert result is not None
    assert result["file_name"] == "photo.png"
    assert result["file_extension"] == ".png"
    assert result["association_status"] == "unchecked"
    assert result["association_score"] is None
    assert result["association_method"] is None


def test_map_image_record_sets_none_without_extension() -> None:
    result = map_image_record(
        {"image_url": "https://example.com/images/photo"},
        "article-1"
    )

    assert result is not None
    assert result["file_extension"] is None


@pytest.mark.parametrize(
    "download_status",
    [
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
    ]
)
def test_map_image_record_marks_failed_download_as_invalid(
    download_status: str
) -> None:
    result = map_image_record(
        {
            "image_url": "https://example.com/image.jpg",
            "image_download_status": download_status
        },
        "article-1"
    )

    assert result is not None
    assert result["validation_status"] == (
        module.IMAGE_VALIDATION_STATUS_INVALID
    )


def test_map_image_record_preserves_error_status() -> None:
    result = map_image_record(
        {
            "image_url": "https://example.com/image.jpg",
            "image_validation_status": module.IMAGE_VALIDATION_STATUS_ERROR,
            "image_is_valid": True
        },
        "article-1"
    )

    assert result is not None
    assert result["validation_status"] == module.IMAGE_VALIDATION_STATUS_ERROR


def test_map_image_record_marks_explicit_invalid_status() -> None:
    result = map_image_record(
        {
            "image_url": "https://example.com/image.jpg",
            "image_validation_status": module.IMAGE_VALIDATION_STATUS_INVALID
        },
        "article-1"
    )

    assert result is not None
    assert result["validation_status"] == (
        module.IMAGE_VALIDATION_STATUS_INVALID
    )


def test_map_image_record_marks_validation_error_as_invalid() -> None:
    result = map_image_record(
        {
            "image_url": "https://example.com/image.jpg",
            "image_validation_error": "Image corrompue"
        },
        "article-1"
    )

    assert result is not None
    assert result["validation_status"] == (
        module.IMAGE_VALIDATION_STATUS_INVALID
    )
    assert result["validation_error"] == "Image corrompue"


def test_map_image_record_uses_download_error_as_fallback() -> None:
    result = map_image_record(
        {
            "image_url": "https://example.com/image.jpg",
            "image_download_error": "Téléchargement impossible"
        },
        "article-1"
    )

    assert result is not None
    assert result["validation_error"] == "Téléchargement impossible"


def test_map_image_record_marks_untreated_image_as_pending() -> None:
    result = map_image_record(
        {"image_url": "https://example.com/image.jpg"},
        "article-1"
    )

    assert result is not None
    assert result["validation_status"] == (
        module.IMAGE_VALIDATION_STATUS_PENDING
    )


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (800, 600, 1.333333),
        ("1920", "1080", 1.777778),
        (0, 600, None),
        (800, 0, None),
        (None, None, None),
        ("invalid", "600", None)
    ]
)
def test_map_image_record_computes_aspect_ratio(
    width: Any,
    height: Any,
    expected: float | None
) -> None:
    result = map_image_record(
        {
            "image_url": "https://example.com/image.jpg",
            "image_width": width,
            "image_height": height
        },
        "article-1"
    )

    assert result is not None
    assert result["aspect_ratio"] == expected


# Mapping des labels

def test_map_label_record_returns_none_without_label() -> None:
    assert map_label_record({}, "article-1") is None


@pytest.mark.parametrize(
    ("role", "expected_type", "expected_method"),
    [
        (
            "fact_check_reference",
            "fact_check",
            "external_fact_check"
        ),
        (
            "labeled_reference",
            "dataset",
            "dataset_import"
        ),
        (
            "multimodal_reference",
            "dataset",
            "dataset_import"
        ),
        (
            "social_reference",
            "dataset",
            "dataset_import"
        ),
        (
            "acquisition",
            "manual",
            "manual_review"
        ),
        (
            "unknown",
            "manual",
            "manual_review"
        )
    ]
)
def test_map_label_record_selects_label_strategy(
    role: str,
    expected_type: str,
    expected_method: str
) -> None:
    result = map_label_record(
        {
            "label": "fake",
            "dataset_role": role,
            "source": "Source"
        },
        "article-1"
    )

    assert result is not None
    assert result["label_type"] == expected_type
    assert result["annotation_method"] == expected_method


def test_map_label_record_builds_complete_record() -> None:
    article = {
        "label": "fake",
        "dataset_role": "labeled_reference",
        "source": "ISOT",
        "annotator": "dataset",
        "label_confidence": "0.95",
        "is_ground_truth": "false",
        "label_is_active": "false",
        "label_notes": "annotation importée",
        "label_metadata": {"original_label": "FAKE"}
    }

    result = map_label_record(article, "article-1")

    assert result == {
        "article_id": "article-1",
        "label": "fake",
        "label_type": "dataset",
        "label_source": "ISOT",
        "annotator": "dataset",
        "annotation_method": "dataset_import",
        "confidence": 0.95,
        "is_ground_truth": False,
        "is_active": False,
        "notes": "annotation importée",
        "label_metadata": {"original_label": "FAKE"}
    }


def test_map_label_record_defaults_ground_truth_for_reference_role() -> None:
    result = map_label_record(
        {
            "label": "real",
            "dataset_role": "labeled_reference"
        },
        "article-1"
    )

    assert result is not None
    assert result["is_ground_truth"] is True
    assert result["is_active"] is True


def test_map_label_record_defaults_ground_truth_to_false_for_acquisition() -> None:
    result = map_label_record(
        {
            "label": "real",
            "dataset_role": "acquisition"
        },
        "article-1"
    )

    assert result is not None
    assert result["is_ground_truth"] is False


# Features

def test_build_feature_record_builds_numeric_feature() -> None:
    result = build_feature_record(
        "article-1",
        "title_length",
        "numeric",
        "42"
    )

    assert result["article_id"] == "article-1"
    assert result["feature_name"] == "title_length"
    assert result["feature_type"] == "numeric"
    assert result["numeric_value"] == 42.0
    assert result["boolean_value"] is None
    assert result["feature_version"] == TRANSFORMATION_VERSION


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        (1, True),
        (0, False)
    ]
)
def test_build_feature_record_builds_boolean_feature(
    value: Any,
    expected: bool
) -> None:
    result = build_feature_record(
        "article-1",
        "has_title",
        "boolean",
        value
    )

    assert result["boolean_value"] is expected
    assert result["numeric_value"] is None


def test_build_feature_record_rejects_unknown_type() -> None:
    with pytest.raises(
        ValueError,
        match="Type de feature non supporté"
    ):
        build_feature_record(
            "article-1",
            "feature",
            "text",
            "valeur"
        )


def test_map_feature_records_maps_supported_fields() -> None:
    article = {
        "has_title": True,
        "title_length": 42,
        "text_length": None,
        "unknown_feature": 100
    }

    result = map_feature_records(article, "article-1")

    assert len(result) == 2
    assert [record["feature_name"] for record in result] == [
        "has_title",
        "title_length"
    ]


def test_map_feature_records_maps_all_declared_features() -> None:
    article = {
        name: True if feature_type == "boolean" else 1
        for name, feature_type in FEATURE_FIELDS.items()
    }

    result = map_feature_records(article, "article-1")

    assert len(result) == len(FEATURE_FIELDS)
    assert {record["feature_name"] for record in result} == set(
        FEATURE_FIELDS
    )


# Transformation complète

def test_transform_articles_for_database_builds_complete_payload(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_extraction_date",
        lambda: "2026-07-27T10:00:00+00:00"
    )

    articles = [
        {
            "id": "article-1",
            "source": "Reuters",
            "title": "Article avec image et label",
            "text": "Contenu",
            "url": "https://example.com/article-1",
            "image_url": "https://example.com/image.jpg",
            "label": "real",
            "dataset_role": "labeled_reference",
            "has_title": True,
            "title_length": 27
        }
    ]

    result = transform_articles_for_database(articles, "batch-1")

    assert set(result) == {"articles", "images", "labels", "features"}
    assert len(result["articles"]) == 1
    assert len(result["images"]) == 1
    assert len(result["labels"]) == 1
    assert len(result["features"]) == 2
    assert result["articles"][0]["batch_id"] == "batch-1"
    assert (
        result["articles"][0]["transformed_at"]
        == "2026-07-27T10:00:00+00:00"
    )


def test_transform_articles_for_database_skips_non_dict_values() -> None:
    articles: list[Any] = [
        None,
        "article",
        42,
        [],
        {"id": "article-1", "title": "Titre"}
    ]

    result = transform_articles_for_database(articles, "batch")

    assert len(result["articles"]) == 1


def test_transform_articles_for_database_skips_duplicate_ids() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Premier article",
            "url": "https://example.com/first"
        },
        {
            "id": "article-1",
            "title": "Deuxième article",
            "url": "https://example.com/second"
        }
    ]

    result = transform_articles_for_database(articles, "batch")

    assert len(result["articles"]) == 1
    assert result["articles"][0]["title"] == "Premier article"


def test_transform_articles_for_database_skips_duplicate_urls() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Premier article",
            "url": "https://example.com/article"
        },
        {
            "id": "article-2",
            "title": "Deuxième article",
            "canonical_url": "https://example.com/article"
        }
    ]

    result = transform_articles_for_database(articles, "batch")

    assert len(result["articles"]) == 1


def test_transform_articles_for_database_accepts_same_missing_url() -> None:
    articles = [
        {"id": "article-1", "title": "Premier article"},
        {"id": "article-2", "title": "Deuxième article"}
    ]

    result = transform_articles_for_database(articles, "batch")

    assert len(result["articles"]) == 2


def test_transform_articles_for_database_does_not_add_optional_records() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Article simple"
        }
    ]

    result = transform_articles_for_database(articles, "batch")

    assert len(result["articles"]) == 1
    assert result["images"] == []
    assert result["labels"] == []
    assert result["features"] == []


def test_transform_articles_for_database_preserves_article_order() -> None:
    articles = [
        {"id": "article-1", "title": "Premier"},
        {"id": "article-2", "title": "Deuxième"},
        {"id": "article-3", "title": "Troisième"}
    ]

    result = transform_articles_for_database(articles, "batch")

    assert [
        article["id"]
        for article in result["articles"]
    ] == [
        "article-1",
        "article-2",
        "article-3"
    ]