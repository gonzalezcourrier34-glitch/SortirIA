"""Tests des fichiers et métadonnées du téléchargement d'images CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_downloader_files.py` (commit 93632c5).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import src.images.image_downloader as module
from src.images.image_downloader import (
    DOWNLOAD_IMAGE_FORMATS,
    ImageTooLargeError,
    InvalidImageContentError,
    apply_image_metadata,
    build_image_path,
    get_article_referer,
    get_current_datetime_iso,
    inspect_downloaded_image,
    normalize_article_id,
    normalize_image_url,
    set_download_status,
    stream_response_to_file
)


# Faux objets

class FakeResponse:
    """Simule une réponse HTTP contenant des blocs binaires."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.requested_chunk_size: int | None = None

    def iter_content(self, chunk_size: int) -> list[bytes]:
        """Retourne les blocs configurés."""

        self.requested_chunk_size = chunk_size
        return self.chunks


class FakeImageMetadata:
    """Simule les métadonnées retournées par image_utils."""

    def __init__(
        self,
        *,
        image_format: str = "JPEG",
        mime_type: str = "image/jpeg",
        width: int = 800,
        height: int = 600,
        aspect_ratio: float = 1.3333,
        mode: str = "RGB",
        file_size: int = 50_000,
        file_hash: str = "abc123"
    ) -> None:
        self.format = image_format
        self.mime_type = mime_type
        self.width = width
        self.height = height
        self.aspect_ratio = aspect_ratio
        self.mode = mode
        self.file_size = file_size
        self.file_hash = file_hash

    def to_dict(self) -> dict[str, Any]:
        """Retourne une représentation sérialisable."""

        return {
            "format": self.format,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "mode": self.mode,
            "file_size": self.file_size,
            "file_hash": self.file_hash
        }


# Normalisation de l'identifiant

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("article-123", "article-123"),
        ("article_123", "article_123"),
        ("Article 123", "Article_123"),
        ("article/test", "article_test"),
        ("article:test", "article_test"),
        ("  article   test  ", "article_test"),
        ("élection française", "élection_française"),
        (123, "123"),
        ("", ""),
        (None, "")
    ]
)
def test_normalize_article_id(
    value: Any,
    expected: str
) -> None:
    assert normalize_article_id(value) == expected


def test_normalize_article_id_removes_repeated_separators() -> None:
    result = normalize_article_id(
        "___article///test:::image___"
    )

    assert result == "article_test_image"


def test_normalize_article_id_limits_length(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_IMAGE_FILENAME_ID_LENGTH",
        10
    )

    result = normalize_article_id(
        "identifiant_article_tres_long"
    )

    assert result == "identifian"
    assert len(result) == 10


def test_normalize_article_id_returns_empty_after_cleanup() -> None:
    assert normalize_article_id("///:::***") == ""


# Normalisation des URL

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "HTTPS://EXAMPLE.COM/image.jpg",
            "https://example.com/image.jpg"
        ),
        (
            "http://EXAMPLE.COM/image.jpg",
            "http://example.com/image.jpg"
        ),
        (
            "https://example.com:443/image.jpg",
            "https://example.com/image.jpg"
        ),
        (
            "http://example.com:80/image.jpg",
            "http://example.com/image.jpg"
        ),
        (
            "https://example.com:8443/image.jpg",
            "https://example.com:8443/image.jpg"
        ),
        (
            "https://example.com/image.jpg#fragment",
            "https://example.com/image.jpg"
        ),
        (
            "https://example.com/image.jpg?size=large#fragment",
            "https://example.com/image.jpg?size=large"
        ),
        (
            "",
            ""
        ),
        (
            None,
            ""
        )
    ]
)
def test_normalize_image_url(
    url: Any,
    expected: str
) -> None:
    assert normalize_image_url(url) == expected


def test_normalize_image_url_preserves_path_and_query() -> None:
    result = normalize_image_url(
        "HTTPS://EXAMPLE.COM/Images/Test.JPG?width=800&height=600"
    )

    assert result == (
        "https://example.com/Images/Test.JPG?width=800&height=600"
    )


def test_normalize_image_url_handles_invalid_port() -> None:
    url = "https://example.com:invalid/image.jpg"

    with pytest.raises(ValueError):
        normalize_image_url(url)


def test_normalize_image_url_handles_unusual_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "urlparse",
        lambda value: (_ for _ in ()).throw(TypeError("URL invalide"))
    )

    result = normalize_image_url(
        "https://example.com/image.jpg"
    )

    assert result == "https://example.com/image.jpg"


# Statuts

def test_set_download_status_sets_status_and_error() -> None:
    article: dict[str, Any] = {}

    set_download_status(
        article,
        "downloaded",
        "aucune erreur"
    )

    assert article == {
        "image_download_status": "downloaded",
        "image_download_error": "aucune erreur"
    }


def test_set_download_status_normalizes_values() -> None:
    article: dict[str, Any] = {}

    set_download_status(
        article,
        "  downloaded  ",
        "  erreur temporaire  "
    )

    assert article == {
        "image_download_status": "downloaded",
        "image_download_error": "erreur temporaire"
    }


def test_set_download_status_accepts_empty_error() -> None:
    article: dict[str, Any] = {
        "image_download_error": "ancienne erreur"
    }

    set_download_status(article, "success")

    assert article["image_download_status"] == "success"
    assert article["image_download_error"] == ""


# Referer

@pytest.mark.parametrize(
    ("article", "expected"),
    [
        (
            {
                "url": "https://example.com/article",
                "article_url": "https://backup.example.com/article",
                "source_url": "https://source.example.com"
            },
            "https://example.com/article"
        ),
        (
            {
                "url": "",
                "article_url": "https://example.com/article"
            },
            "https://example.com/article"
        ),
        (
            {
                "url": "invalid",
                "article_url": "",
                "source_url": "https://example.com/source"
            },
            "https://example.com/source"
        ),
        (
            {
                "url": "invalid",
                "article_url": "ftp://example.com/article",
                "source_url": ""
            },
            ""
        ),
        (
            {},
            ""
        )
    ]
)
def test_get_article_referer(
    article: dict[str, Any],
    expected: str
) -> None:
    assert get_article_referer(article) == expected


def test_get_article_referer_uses_first_valid_field(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    checked_values: list[str] = []

    def fake_is_valid_http_url(value: str) -> bool:
        checked_values.append(value)
        return value == "https://valid.example.com"

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fake_is_valid_http_url
    )

    article = {
        "url": "invalid",
        "article_url": "https://valid.example.com",
        "source_url": "https://unused.example.com"
    }

    result = get_article_referer(article)

    assert result == "https://valid.example.com"
    assert checked_values == [
        "invalid",
        "https://valid.example.com"
    ]


# Date UTC

def test_get_current_datetime_iso_returns_utc_datetime() -> None:
    result = get_current_datetime_iso()
    parsed = datetime.fromisoformat(result)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_get_current_datetime_iso_returns_iso_format() -> None:
    result = get_current_datetime_iso()

    assert "T" in result
    assert result.endswith("+00:00")


# Métadonnées

def test_apply_image_metadata_sets_article_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    metadata = FakeImageMetadata()

    monkeypatch.setattr(
        module,
        "get_current_datetime_iso",
        lambda: "2026-07-27T10:00:00+00:00"
    )

    article: dict[str, Any] = {}

    apply_image_metadata(
        article,
        image_path,
        metadata,  # type: ignore[arg-type]
        125
    )

    assert article["image_path"] == str(image_path)
    assert article["image_format"] == "JPEG"
    assert article["image_mime_type"] == "image/jpeg"
    assert article["image_width"] == 800
    assert article["image_height"] == 600
    assert article["image_aspect_ratio"] == 1.3333
    assert article["image_mode"] == "RGB"
    assert article["image_size_bytes"] == 50_000
    assert article["image_file_hash"] == "abc123"
    assert article["image_download_duration_ms"] == 125
    assert article["image_downloaded_at"] == (
        "2026-07-27T10:00:00+00:00"
    )


def test_apply_image_metadata_creates_nested_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = FakeImageMetadata()

    monkeypatch.setattr(
        module,
        "get_current_datetime_iso",
        lambda: "2026-07-27T11:30:00+00:00"
    )

    article: dict[str, Any] = {}

    apply_image_metadata(
        article,
        tmp_path / "image.jpg",
        metadata,  # type: ignore[arg-type]
        42
    )

    assert article["image_metadata"] == {
        "format": "JPEG",
        "mime_type": "image/jpeg",
        "width": 800,
        "height": 600,
        "aspect_ratio": 1.3333,
        "mode": "RGB",
        "file_size": 50_000,
        "file_hash": "abc123",
        "download_duration_ms": 42,
        "downloaded_at": "2026-07-27T11:30:00+00:00"
    }


def test_apply_image_metadata_preserves_existing_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = FakeImageMetadata(
        width=1200,
        height=800
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime_iso",
        lambda: "2026-07-27T12:00:00+00:00"
    )

    article: dict[str, Any] = {
        "image_metadata": {
            "original_url": "https://example.com/image.jpg",
            "custom_field": "value"
        }
    }

    apply_image_metadata(
        article,
        tmp_path / "image.jpg",
        metadata,  # type: ignore[arg-type]
        80
    )

    assert article["image_metadata"]["original_url"] == (
        "https://example.com/image.jpg"
    )
    assert article["image_metadata"]["custom_field"] == "value"
    assert article["image_metadata"]["width"] == 1200
    assert article["image_metadata"]["height"] == 800


@pytest.mark.parametrize(
    "existing_metadata",
    [
        None,
        "",
        [],
        "invalid",
        123
    ]
)
def test_apply_image_metadata_replaces_invalid_existing_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    existing_metadata: Any
) -> None:
    metadata = FakeImageMetadata()

    monkeypatch.setattr(
        module,
        "get_current_datetime_iso",
        lambda: "2026-07-27T12:30:00+00:00"
    )

    article = {
        "image_metadata": existing_metadata
    }

    apply_image_metadata(
        article,
        tmp_path / "image.jpg",
        metadata,  # type: ignore[arg-type]
        20
    )

    assert isinstance(article["image_metadata"], dict)
    assert article["image_metadata"]["format"] == "JPEG"


# Téléchargement en streaming

def test_stream_response_to_file_writes_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "IMAGE_DOWNLOAD_CHUNK_SIZE",
        2048
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([
        b"\x89PNG\r\n\x1a\n",
        b"image-data",
        b"end"
    ])
    target = tmp_path / "image.download"

    result = stream_response_to_file(
        response,  # type: ignore[arg-type]
        target
    )

    assert result == len(b"\x89PNG\r\n\x1a\nimage-dataend")
    assert target.read_bytes() == b"\x89PNG\r\n\x1a\nimage-dataend"
    assert response.requested_chunk_size == 2048


def test_stream_response_to_file_ignores_empty_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([
        b"",
        b"\xff\xd8\xff",
        b"",
        b"image"
    ])
    target = tmp_path / "image.download"

    result = stream_response_to_file(
        response,  # type: ignore[arg-type]
        target
    )

    assert result == 8
    assert target.read_bytes() == b"\xff\xd8\xffimage"


def test_stream_response_to_file_normalizes_small_chunk_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "IMAGE_DOWNLOAD_CHUNK_SIZE",
        10
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([b"image"])
    target = tmp_path / "image.download"

    stream_response_to_file(
        response,  # type: ignore[arg-type]
        target
    )

    assert response.requested_chunk_size == 1024


def test_stream_response_to_file_rejects_too_large_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([
        b"12345",
        b"67890",
        b"X"
    ])
    target = tmp_path / "image.download"

    with pytest.raises(
        ImageTooLargeError,
        match="Taille maximale dépassée"
    ):
        stream_response_to_file(
            response,  # type: ignore[arg-type]
            target
        )

    assert target.exists()
    assert target.read_bytes() == b"1234567890"


def test_stream_response_to_file_accepts_exact_maximum_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([
        b"12345",
        b"67890"
    ])
    target = tmp_path / "image.download"

    result = stream_response_to_file(
        response,  # type: ignore[arg-type]
        target
    )

    assert result == 10
    assert target.read_bytes() == b"1234567890"


@pytest.mark.parametrize(
    "content",
    [
        b"<!doctype html><html></html>",
        b"  <html><body>Erreur</body></html>",
        b"<?xml version='1.0'?>"
    ]
)
def test_stream_response_to_file_rejects_html_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([content])
    target = tmp_path / "image.download"

    with pytest.raises(
        InvalidImageContentError,
        match="page HTML ou XML"
    ):
        stream_response_to_file(
            response,  # type: ignore[arg-type]
            target
        )


def test_stream_response_to_file_detects_html_across_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([
        b"  ",
        b"<ht",
        b"ml><body>Erreur</body></html>"
    ])
    target = tmp_path / "image.download"

    with pytest.raises(
        InvalidImageContentError,
        match="page HTML ou XML"
    ):
        stream_response_to_file(
            response,  # type: ignore[arg-type]
            target
        )


def test_stream_response_to_file_rejects_empty_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([
        b"",
        b""
    ])
    target = tmp_path / "image.download"

    with pytest.raises(
        InvalidImageContentError,
        match="Contenu distant vide"
    ):
        stream_response_to_file(
            response,  # type: ignore[arg-type]
            target
        )


def test_stream_response_to_file_rejects_content_below_minimum_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        100
    )

    response = FakeResponse([
        b"\x89PNG",
        b"small"
    ])
    target = tmp_path / "image.download"

    with pytest.raises(
        InvalidImageContentError,
        match="Image trop légère"
    ):
        stream_response_to_file(
            response,  # type: ignore[arg-type]
            target
        )


def test_stream_response_to_file_accepts_exact_minimum_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        10
    )

    response = FakeResponse([
        b"1234567890"
    ])
    target = tmp_path / "image.download"

    result = stream_response_to_file(
        response,  # type: ignore[arg-type]
        target
    )

    assert result == 10


def test_stream_response_to_file_normalizes_negative_minimum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        -100
    )

    response = FakeResponse([b"x"])
    target = tmp_path / "image.download"

    result = stream_response_to_file(
        response,  # type: ignore[arg-type]
        target
    )

    assert result == 1


def test_stream_response_to_file_normalizes_invalid_maximum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        0
    )
    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )

    response = FakeResponse([b"ab"])
    target = tmp_path / "image.download"

    with pytest.raises(ImageTooLargeError):
        stream_response_to_file(
            response,  # type: ignore[arg-type]
            target
        )


# Inspection de l'image

def test_inspect_downloaded_image_returns_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = FakeImageMetadata(
        image_format="PNG",
        width=500,
        height=400
    )
    inspected_paths: list[Path] = []
    size_checks: list[dict[str, Any]] = []

    def fake_inspect_image(path: Path) -> FakeImageMetadata:
        inspected_paths.append(path)
        return metadata

    def fake_is_large_enough(
        received_metadata: Any,
        *,
        min_width: int,
        min_height: int,
        min_pixel_count: int
    ) -> bool:
        size_checks.append({
            "metadata": received_metadata,
            "min_width": min_width,
            "min_height": min_height,
            "min_pixel_count": min_pixel_count
        })
        return True

    monkeypatch.setattr(
        module,
        "inspect_image",
        fake_inspect_image
    )
    monkeypatch.setattr(
        module,
        "is_image_large_enough",
        fake_is_large_enough
    )
    monkeypatch.setattr(module, "MIN_IMAGE_WIDTH", 224)
    monkeypatch.setattr(module, "MIN_IMAGE_HEIGHT", 224)
    monkeypatch.setattr(module, "MIN_IMAGE_PIXEL_COUNT", 50_176)

    image_path = tmp_path / "image.download"

    result = inspect_downloaded_image(image_path)

    assert result is metadata
    assert inspected_paths == [image_path]
    assert size_checks == [
        {
            "metadata": metadata,
            "min_width": 224,
            "min_height": 224,
            "min_pixel_count": 50_176
        }
    ]


def test_inspect_downloaded_image_rejects_invalid_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: None
    )

    with pytest.raises(
        InvalidImageContentError,
        match="n'est pas une image valide"
    ):
        inspect_downloaded_image(
            tmp_path / "invalid.download"
        )


@pytest.mark.parametrize(
    "image_format",
    [
        "",
        "UNKNOWN",
        "TIFF",
        "BMP"
    ]
)
def test_inspect_downloaded_image_rejects_unsupported_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image_format: str
) -> None:
    metadata = FakeImageMetadata(
        image_format=image_format
    )

    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )

    with pytest.raises(
        InvalidImageContentError,
        match="Format d'image non pris en charge"
    ):
        inspect_downloaded_image(
            tmp_path / "image.download"
        )


def test_inspect_downloaded_image_rejects_invalid_extension_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = FakeImageMetadata(
        image_format="JPEG"
    )

    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )
    monkeypatch.setitem(
        DOWNLOAD_IMAGE_FORMATS,
        "JPEG",
        "jpg"
    )

    with pytest.raises(
        InvalidImageContentError,
        match="Format d'image non pris en charge"
    ):
        inspect_downloaded_image(
            tmp_path / "image.download"
        )


def test_inspect_downloaded_image_rejects_small_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = FakeImageMetadata(
        image_format="JPEG",
        width=100,
        height=80
    )

    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )
    monkeypatch.setattr(
        module,
        "is_image_large_enough",
        lambda *args, **kwargs: False
    )
    monkeypatch.setattr(module, "MIN_IMAGE_WIDTH", 224)
    monkeypatch.setattr(module, "MIN_IMAGE_HEIGHT", 224)

    with pytest.raises(
        InvalidImageContentError,
        match="Image trop petite"
    ) as error_info:
        inspect_downloaded_image(
            tmp_path / "image.download"
        )

    assert "100x80" in str(error_info.value)
    assert "224x224" in str(error_info.value)


@pytest.mark.parametrize(
    "image_format",
    sorted(DOWNLOAD_IMAGE_FORMATS)
)
def test_inspect_downloaded_image_accepts_supported_formats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image_format: str
) -> None:
    metadata = FakeImageMetadata(
        image_format=image_format
    )

    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )
    monkeypatch.setattr(
        module,
        "is_image_large_enough",
        lambda *args, **kwargs: True
    )

    result = inspect_downloaded_image(
        tmp_path / "image.download"
    )

    assert result is metadata


# Construction du chemin final

def test_build_image_path() -> None:
    result = build_image_path(
        Path("data/images/reuters"),
        "reuters",
        "article_123",
        ".jpg"
    )

    assert result == Path(
        "data/images/reuters/reuters_article_123.jpg"
    )


@pytest.mark.parametrize(
    ("extension", "expected_name"),
    [
        (".jpg", "source_article.jpg"),
        (".png", "source_article.png"),
        (".webp", "source_article.webp"),
        (".avif", "source_article.avif")
    ]
)
def test_build_image_path_with_supported_extensions(
    extension: str,
    expected_name: str
) -> None:
    result = build_image_path(
        Path("images"),
        "source",
        "article",
        extension
    )

    assert result == Path("images") / expected_name


def test_build_image_path_preserves_target_directory(
    tmp_path: Path
) -> None:
    target_directory = tmp_path / "nested" / "images"

    result = build_image_path(
        target_directory,
        "guardian",
        "abc-123",
        ".webp"
    )

    assert result.parent == target_directory
    assert result.name == "guardian_abc-123.webp"