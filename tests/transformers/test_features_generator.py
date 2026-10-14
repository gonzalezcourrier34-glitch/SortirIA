"""Tests du générateur de variables dérivées CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/transformers/test_features_generator.py` (commit 93632c5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.transformers.features_generator as module
from src.transformers.features_generator import (
    apply_inspected_image_metadata,
    apply_stored_image_metadata,
    enrich_article,
    enrich_articles,
    generate_business_features,
    generate_image_features,
    generate_temporal_features,
    generate_text_features,
    get_first_available_value,
    get_stored_image_metadata,
    has_complete_image_metadata,
    validate_text_image_association
)


# Texte

@pytest.mark.parametrize(
    ("article", "expected"),
    [
        (
            {},
            {
                "has_title": False,
                "has_text": False,
                "title_length": 0,
                "text_length": 0,
                "total_text_length": 0,
                "title_word_count": 0,
                "text_word_count": 0,
                "total_word_count": 0
            }
        ),
        (
            {
                "title": "Un titre",
                "text": "Un contenu complet"
            },
            {
                "has_title": True,
                "has_text": True,
                "title_length": 8,
                "text_length": 18,
                "total_text_length": 26,
                "title_word_count": 2,
                "text_word_count": 3,
                "total_word_count": 5
            }
        )
    ]
)
def test_generate_text_features(
    article: dict[str, Any],
    expected: dict[str, Any]
) -> None:
    assert generate_text_features(article) == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "Texte principal"),
        ("content", "Contenu alternatif"),
        ("summary", "Résumé disponible"),
        ("description", "Description disponible")
    ]
)
def test_generate_text_features_uses_content_fallbacks(
    field: str,
    value: str
) -> None:
    result = generate_text_features({field: value})

    assert result["has_text"] is True
    assert result["text_length"] == len(value)
    assert result["text_word_count"] == len(value.split())


def test_generate_text_features_prefers_text() -> None:
    result = generate_text_features({
        "text": "Texte principal",
        "content": "Contenu secondaire",
        "summary": "Résumé"
    })

    assert result["text_length"] == len("Texte principal")


def test_generate_text_features_normalizes_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_normalize(value: Any) -> str:
        calls.append(value)
        return str(value or "").strip().upper()

    monkeypatch.setattr(module, "normalize_value", fake_normalize)

    result = generate_text_features({
        "title": " titre ",
        "text": " texte "
    })

    assert calls == [" titre ", " texte "]
    assert result["title_length"] == 5
    assert result["text_length"] == 5


# Dates

def test_generate_temporal_features_without_date(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "parse_datetime", lambda value: None)

    result = generate_temporal_features({"published_at": "invalide"})

    assert result == {
        "has_publication_date": False,
        "publication_year": None,
        "publication_month": None,
        "publication_day": None,
        "publication_hour": None,
        "publication_weekday": None
    }


def test_generate_temporal_features_with_date(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = datetime(2026, 7, 27, 14, 30, tzinfo=UTC)
    monkeypatch.setattr(module, "parse_datetime", lambda value: parsed)

    result = generate_temporal_features({
        "published_at": "2026-07-27T14:30:00Z"
    })

    assert result == {
        "has_publication_date": True,
        "publication_year": 2026,
        "publication_month": 7,
        "publication_day": 27,
        "publication_hour": 14,
        "publication_weekday": 0
    }


def test_generate_temporal_features_passes_raw_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[Any] = []

    def fake_parse(value: Any) -> None:
        received.append(value)
        return None

    monkeypatch.setattr(module, "parse_datetime", fake_parse)

    generate_temporal_features({"published_at": 1234567890})

    assert received == [1234567890]


# Métadonnées stockées

def test_get_stored_image_metadata_returns_mapping() -> None:
    metadata = {"width": 800}

    assert get_stored_image_metadata({
        "image_metadata": metadata
    }) is metadata


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        "",
        [],
        42,
        True
    ]
)
def test_get_stored_image_metadata_rejects_non_mapping(
    metadata: Any
) -> None:
    assert get_stored_image_metadata({
        "image_metadata": metadata
    }) == {}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((None, "", 0, "value"), 0),
        ((None, "", False, "value"), False),
        ((None, "", "value", "other"), "value"),
        ((None, "", None), None)
    ]
)
def test_get_first_available_value(
    values: tuple[Any, ...],
    expected: Any
) -> None:
    assert get_first_available_value(*values) == expected


def test_apply_stored_image_metadata_prefers_article_values() -> None:
    features: dict[str, Any] = {}
    article = {
        "image_width": "800",
        "image_height": "600",
        "image_aspect_ratio": "1.333333",
        "image_format": "JPEG",
        "image_mime_type": "image/jpeg",
        "image_size_bytes": "10000",
        "image_file_hash": "article-hash",
        "image_metadata": {
            "width": 320,
            "height": 240,
            "aspect_ratio": 1.5,
            "format": "PNG",
            "mime_type": "image/png",
            "file_size": 5000,
            "file_hash": "metadata-hash"
        }
    }

    apply_stored_image_metadata(features, article)

    assert features == {
        "image_width": 800,
        "image_height": 600,
        "image_aspect_ratio": 1.333333,
        "image_format": "JPEG",
        "image_mime_type": "image/jpeg",
        "image_size_bytes": 10000,
        "image_file_hash": "article-hash"
    }


def test_apply_stored_image_metadata_uses_metadata_fallbacks() -> None:
    features: dict[str, Any] = {}
    article = {
        "image_metadata": {
            "width": "320",
            "height": "240",
            "aspect_ratio": "1.333333",
            "format": "PNG",
            "mime_type": "image/png",
            "file_size": "5000",
            "file_hash": "metadata-hash"
        }
    }

    apply_stored_image_metadata(features, article)

    assert features == {
        "image_width": 320,
        "image_height": 240,
        "image_aspect_ratio": 1.333333,
        "image_format": "PNG",
        "image_mime_type": "image/png",
        "image_size_bytes": 5000,
        "image_file_hash": "metadata-hash"
    }


def test_apply_stored_image_metadata_uses_size_bytes_fallback() -> None:
    features: dict[str, Any] = {}

    apply_stored_image_metadata(
        features,
        {"image_metadata": {"size_bytes": "1234"}}
    )

    assert features["image_size_bytes"] == 1234


def test_apply_stored_image_metadata_handles_missing_values() -> None:
    features: dict[str, Any] = {}

    apply_stored_image_metadata(features, {})

    assert features == {
        "image_width": None,
        "image_height": None,
        "image_aspect_ratio": None,
        "image_format": "",
        "image_mime_type": "",
        "image_size_bytes": None,
        "image_file_hash": ""
    }


def test_apply_inspected_image_metadata() -> None:
    features: dict[str, Any] = {}
    metadata = SimpleNamespace(
        width=1920,
        height=1080,
        aspect_ratio=1.777778,
        format="JPEG",
        mime_type="image/jpeg",
        file_size=120000,
        file_hash="hash"
    )

    apply_inspected_image_metadata(features, metadata)

    assert features == {
        "image_width": 1920,
        "image_height": 1080,
        "image_aspect_ratio": 1.777778,
        "image_format": "JPEG",
        "image_mime_type": "image/jpeg",
        "image_size_bytes": 120000,
        "image_file_hash": "hash"
    }


@pytest.mark.parametrize(
    ("features", "expected"),
    [
        (
            {
                "image_width": 800,
                "image_height": 600,
                "image_format": "JPEG",
                "image_size_bytes": 1000
            },
            True
        ),
        (
            {
                "image_width": None,
                "image_height": 600,
                "image_format": "JPEG",
                "image_size_bytes": 1000
            },
            False
        ),
        (
            {
                "image_width": 800,
                "image_height": None,
                "image_format": "JPEG",
                "image_size_bytes": 1000
            },
            False
        ),
        (
            {
                "image_width": 800,
                "image_height": 600,
                "image_format": "",
                "image_size_bytes": 1000
            },
            False
        ),
        (
            {
                "image_width": 800,
                "image_height": 600,
                "image_format": "JPEG",
                "image_size_bytes": None
            },
            False
        )
    ]
)
def test_has_complete_image_metadata(
    features: dict[str, Any],
    expected: bool
) -> None:
    assert has_complete_image_metadata(features) is expected


# Association technique

@pytest.mark.parametrize(
    ("article_id", "image_path", "expected"),
    [
        ("article-123", Path("/tmp/article-123.jpg"), True),
        ("ARTICLE-123", Path("/tmp/article-123_image.jpg"), True),
        ("article-123", Path("/tmp/other.jpg"), False),
        ("", Path("/tmp/article-123.jpg"), False),
        (None, Path("/tmp/article-123.jpg"), False),
        ("article-123", None, False)
    ]
)
def test_validate_text_image_association(
    article_id: Any,
    image_path: Path | None,
    expected: bool
) -> None:
    assert validate_text_image_association(
        article_id,
        image_path
    ) is expected


# Images

def base_image_features() -> dict[str, Any]:
    return {
        "has_image_url": False,
        "has_image_path": False,
        "image_exists": False,
        "image_is_valid": False,
        "image_width": None,
        "image_height": None,
        "image_aspect_ratio": None,
        "image_format": "",
        "image_mime_type": "",
        "image_size_bytes": None,
        "image_file_hash": "",
        "text_image_association_valid": False
    }


def test_generate_image_features_without_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "resolve_image_path", lambda value: None)

    result = generate_image_features({
        "image_url": "https://example.com/image.jpg"
    })

    expected = base_image_features()
    expected["has_image_url"] = True

    assert result == expected


def test_generate_image_features_with_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "missing.jpg"
    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )

    result = generate_image_features({
        "image_path": str(image_path)
    })

    expected = base_image_features()
    expected["has_image_path"] = True

    assert result == expected


def test_generate_image_features_uses_complete_stored_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "article-1.jpg"
    image_path.write_bytes(b"image")

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )

    inspected = False

    def fake_inspect(path: Path) -> None:
        nonlocal inspected
        inspected = True
        return None

    monkeypatch.setattr(module, "inspect_image", fake_inspect)

    result = generate_image_features({
        "id": "article-1",
        "image_path": str(image_path),
        "image_is_valid": True,
        "image_width": 800,
        "image_height": 600,
        "image_format": "JPEG",
        "image_size_bytes": 5000
    })

    assert inspected is False
    assert result["image_exists"] is True
    assert result["image_is_valid"] is True
    assert result["text_image_association_valid"] is True
    assert result["image_width"] == 800


def test_generate_image_features_inspects_when_validation_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "article-1.jpg"
    image_path.write_bytes(b"image")

    metadata = SimpleNamespace(
        width=1024,
        height=768,
        aspect_ratio=1.333333,
        format="JPEG",
        mime_type="image/jpeg",
        file_size=5000,
        file_hash="hash"
    )

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )
    monkeypatch.setattr(module, "inspect_image", lambda path: metadata)

    result = generate_image_features({
        "id": "article-1",
        "image_path": str(image_path)
    })

    assert result["image_exists"] is True
    assert result["image_is_valid"] is True
    assert result["image_width"] == 1024
    assert result["image_height"] == 768
    assert result["image_format"] == "JPEG"


def test_generate_image_features_inspects_when_metadata_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "article-1.jpg"
    image_path.write_bytes(b"image")

    metadata = SimpleNamespace(
        width=640,
        height=480,
        aspect_ratio=1.333333,
        format="PNG",
        mime_type="image/png",
        file_size=3000,
        file_hash="hash"
    )

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )
    monkeypatch.setattr(module, "inspect_image", lambda path: metadata)

    result = generate_image_features({
        "id": "article-1",
        "image_path": str(image_path),
        "image_is_valid": False,
        "image_width": 640
    })

    assert result["image_is_valid"] is False
    assert result["image_height"] == 480
    assert result["image_format"] == "PNG"


def test_generate_image_features_marks_inspection_failure_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "article-1.jpg"
    image_path.write_bytes(b"image")

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )
    monkeypatch.setattr(module, "inspect_image", lambda path: None)

    result = generate_image_features({
        "id": "article-1",
        "image_path": str(image_path)
    })

    assert result["image_exists"] is True
    assert result["image_is_valid"] is False


def test_generate_image_features_preserves_explicit_false_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "article-1.jpg"
    image_path.write_bytes(b"image")

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: SimpleNamespace(
            width=800,
            height=600,
            aspect_ratio=1.333333,
            format="JPEG",
            mime_type="image/jpeg",
            file_size=5000,
            file_hash="hash"
        )
    )

    result = generate_image_features({
        "id": "article-1",
        "image_path": str(image_path),
        "image_is_valid": False
    })

    assert result["image_is_valid"] is False


def test_generate_image_features_reads_file_size_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "article-1.jpg"
    image_path.write_bytes(b"123456789")

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: image_path
    )
    monkeypatch.setattr(module, "inspect_image", lambda path: None)

    result = generate_image_features({
        "id": "article-1",
        "image_path": str(image_path)
    })

    assert result["image_size_bytes"] == 9


def test_generate_image_features_logs_stat_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakePath:
        stem = "article-1"

        def is_file(self) -> bool:
            return True

        def stat(self) -> Any:
            raise OSError("Erreur simulée")

        def __str__(self) -> str:
            return "article-1.jpg"

    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda value: FakePath()
    )
    monkeypatch.setattr(module, "inspect_image", lambda path: None)
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = generate_image_features({
        "id": "article-1",
        "image_path": "article-1.jpg"
    })

    assert result["image_size_bytes"] is None
    assert len(warnings) == 1
    assert warnings[0][0] == "Taille d'image illisible %s : %s"


# Métier

@pytest.mark.parametrize(
    ("article", "expected"),
    [
        (
            {},
            {
                "has_url": False,
                "has_author": False,
                "has_label": False,
                "is_labeled": False,
                "is_multimodal": False
            }
        ),
        (
            {
                "url": "https://example.com",
                "author": "Auteur",
                "label": "fake",
                "title": "Titre",
                "image_is_valid": True
            },
            {
                "has_url": True,
                "has_author": True,
                "has_label": True,
                "is_labeled": True,
                "is_multimodal": True
            }
        )
    ]
)
def test_generate_business_features(
    article: dict[str, Any],
    expected: dict[str, Any]
) -> None:
    assert generate_business_features(article) == expected


@pytest.mark.parametrize(
    "field",
    ["text", "content", "summary", "description"]
)
def test_generate_business_features_accepts_text_fallbacks(
    field: str
) -> None:
    result = generate_business_features({
        field: "Contenu",
        "image_is_valid": True
    })

    assert result["is_multimodal"] is True


def test_generate_business_features_requires_valid_image() -> None:
    result = generate_business_features({
        "title": "Titre",
        "image_is_valid": False
    })

    assert result["is_multimodal"] is False


# Enrichissement

def test_enrich_article_rejects_non_mapping() -> None:
    assert enrich_article(
        "article",
        "2026-07-27",
        "1.0"
    ) == {}


def test_enrich_article_combines_all_feature_groups(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "generate_text_features",
        lambda article: {"text_feature": 1}
    )
    monkeypatch.setattr(
        module,
        "generate_temporal_features",
        lambda article: {"date_feature": 2}
    )
    monkeypatch.setattr(
        module,
        "generate_image_features",
        lambda article: {"image_feature": 3}
    )
    monkeypatch.setattr(
        module,
        "generate_business_features",
        lambda article: {"business_feature": 4}
    )

    article = {"id": "article-1"}

    result = enrich_article(
        article,
        "2026-07-27T10:00:00+00:00",
        "1.0"
    )

    assert result == {
        "id": "article-1",
        "text_feature": 1,
        "date_feature": 2,
        "image_feature": 3,
        "business_feature": 4,
        "transformation_date": "2026-07-27T10:00:00+00:00",
        "transformation_version": "1.0"
    }
    assert article == {"id": "article-1"}


def test_enrich_article_business_features_use_generated_image_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "generate_image_features",
        lambda article: {"image_is_valid": True}
    )

    result = enrich_article(
        {"title": "Titre"},
        "date",
        "version"
    )

    assert result["image_is_valid"] is True
    assert result["is_multimodal"] is True


def test_enrich_articles_returns_empty_list_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    assert enrich_articles([], "date", "version") == []
    assert warnings == [("Aucun article à enrichir.",)]


def test_enrich_articles_enriches_valid_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    infos: list[tuple[Any, ...]] = []
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "enrich_article",
        lambda article, date, version: (
            {"id": article["id"], "date": date, "version": version}
            if isinstance(article, dict)
            else {}
        )
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: infos.append(args)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = enrich_articles(
        [{"id": "a"}, {"id": "b"}],
        "date",
        "version"
    )

    assert result == [
        {"id": "a", "date": "date", "version": "version"},
        {"id": "b", "date": "date", "version": "version"}
    ]
    assert infos == [("%s article(s) enrichi(s).", 2)]
    assert warnings == []


def test_enrich_articles_ignores_invalid_elements(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    infos: list[tuple[Any, ...]] = []
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "enrich_article",
        lambda article, date, version: (
            {"id": article["id"]}
            if isinstance(article, dict)
            else {}
        )
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: infos.append(args)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = enrich_articles(
        [{"id": "a"}, None, "invalid", {"id": "b"}],
        "date",
        "version"
    )

    assert result == [{"id": "a"}, {"id": "b"}]
    assert infos == [("%s article(s) enrichi(s).", 2)]
    assert warnings == [("%s élément(s) ignoré(s).", 2)]