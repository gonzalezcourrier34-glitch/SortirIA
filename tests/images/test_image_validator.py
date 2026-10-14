"""Tests de validation des images locales associées aux articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_validator.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import src.images.image_validator as module
from src.images.image_utils import ImageMetadata
from src.images.image_validator import (
    IMAGE_REJECTION_CONTENT,
    IMAGE_REJECTION_DIMENSIONS,
    IMAGE_REJECTION_EMPTY,
    IMAGE_REJECTION_EXTENSION,
    IMAGE_REJECTION_FORMAT,
    IMAGE_REJECTION_INVALID_ITEM,
    IMAGE_REJECTION_INVALID_PATH,
    IMAGE_REJECTION_MISSING_PATH,
    IMAGE_REJECTION_NOT_FOUND,
    IMAGE_REJECTION_OUTSIDE_DIRECTORY,
    IMAGE_REJECTION_TOO_LARGE,
    IMAGE_REJECTION_TOO_LIGHT,
    IMAGE_REJECTION_UNEXPECTED,
    enrich_article_with_image_metadata,
    format_rejection_summary,
    get_article_image_rejection,
    get_default_allowed_directories,
    get_file_size_rejection,
    get_image_content_rejection,
    get_image_metadata_rejection,
    get_image_path_details,
    get_image_path_rejection,
    has_allowed_extension,
    has_valid_file_size,
    inspect_valid_image,
    is_path_inside_allowed_directories,
    is_path_inside_directory,
    is_path_inside_images_directory,
    is_valid_image_path,
    normalize_allowed_directories,
    resolve_image_path,
    validate_article_image,
    validate_image_content,
    validate_images_for_articles
)


# Données de test

def build_metadata(
    *,
    image_format: str = "JPEG",
    extension: str = ".jpg",
    mime_type: str = "image/jpeg",
    width: int = 800,
    height: int = 600,
    mode: str = "RGB",
    animated: bool = False,
    frame_count: int = 1,
    file_size: int = 50_000,
    file_hash: str = "abc123"
) -> ImageMetadata:
    """Construit des métadonnées d'image pour les tests."""

    return ImageMetadata(
        format=image_format,
        extension=extension,
        mime_type=mime_type,
        width=width,
        height=height,
        mode=mode,
        animated=animated,
        frame_count=frame_count,
        file_size=file_size,
        file_hash=file_hash
    )


def build_article(
    article_id: str = "article-1",
    *,
    image_path: Any = "data/images/image.jpg"
) -> dict[str, Any]:
    """Construit un article minimal pour les tests."""

    return {
        "id": article_id,
        "source": "reuters",
        "title": f"Titre de {article_id}",
        "image_path": image_path
    }


# Résolution des chemins

@pytest.mark.parametrize(
    "image_path",
    [
        None,
        42,
        [],
        {},
        object()
    ]
)
def test_resolve_image_path_rejects_invalid_type(
    image_path: Any
) -> None:
    assert resolve_image_path(image_path) is None


@pytest.mark.parametrize(
    "image_path",
    [
        "",
        " ",
        "\n"
    ]
)
def test_resolve_image_path_rejects_empty_path(
    image_path: str
) -> None:
    assert resolve_image_path(image_path) is None


def test_resolve_image_path_returns_existing_absolute_path(
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")

    result = resolve_image_path(image_path)

    assert result == image_path.resolve()


def test_resolve_image_path_resolves_relative_path_from_base_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    base_directory = tmp_path / "project"
    base_directory.mkdir()

    image_path = base_directory / "data" / "image.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"image")

    monkeypatch.setattr(
        module,
        "BASE_DIR",
        base_directory
    )
    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        tmp_path / "images"
    )

    result = resolve_image_path("data/image.jpg")

    assert result == image_path.resolve()


def test_resolve_image_path_resolves_relative_path_from_images_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    images_directory = tmp_path / "images"
    images_directory.mkdir()

    image_path = images_directory / "reuters" / "image.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"image")

    monkeypatch.setattr(
        module,
        "BASE_DIR",
        tmp_path / "project"
    )
    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        images_directory
    )

    result = resolve_image_path("reuters/image.jpg")

    assert result == image_path.resolve()


def test_resolve_image_path_returns_first_candidate_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "BASE_DIR",
        tmp_path
    )
    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        tmp_path / "images"
    )

    result = resolve_image_path("missing/image.jpg")

    assert result == (tmp_path / "missing" / "image.jpg").resolve()


def test_resolve_image_path_handles_resolve_errors(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingPath:
        def __init__(self, value: str) -> None:
            self.value = value

        def expanduser(self) -> FailingPath:
            return self

        def is_absolute(self) -> bool:
            return True

        def resolve(self, strict: bool = False) -> Path:
            raise OSError("Résolution impossible.")

    monkeypatch.setattr(
        module,
        "Path",
        FailingPath
    )

    assert resolve_image_path("image.jpg") is None

# Dossiers autorisés

def test_get_default_allowed_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    images_directory = tmp_path / "images"

    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        images_directory
    )

    result = get_default_allowed_directories()

    assert result == (
        images_directory.resolve(),
    )


def test_normalize_allowed_directories_uses_default_when_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    default_directories = (
        tmp_path / "images",
    )

    monkeypatch.setattr(
        module,
        "get_default_allowed_directories",
        lambda: default_directories
    )

    assert normalize_allowed_directories() == default_directories


def test_normalize_allowed_directories_accepts_single_string(
    tmp_path: Path
) -> None:
    result = normalize_allowed_directories(
        str(tmp_path)
    )

    assert result == (
        tmp_path.resolve(),
    )


def test_normalize_allowed_directories_accepts_single_path(
    tmp_path: Path
) -> None:
    result = normalize_allowed_directories(
        tmp_path
    )

    assert result == (
        tmp_path.resolve(),
    )


def test_normalize_allowed_directories_removes_duplicates(
    tmp_path: Path
) -> None:
    result = normalize_allowed_directories([
        tmp_path,
        str(tmp_path),
        tmp_path
    ])

    assert result == (
        tmp_path.resolve(),
    )


def test_normalize_allowed_directories_ignores_invalid_items(
    tmp_path: Path
) -> None:
    result = normalize_allowed_directories([
        None,
        42,
        tmp_path
    ])

    assert result == (
        tmp_path.resolve(),
    )


def test_normalize_allowed_directories_falls_back_for_invalid_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    default_directories = (
        tmp_path / "images",
    )

    class InvalidIterable:
        def __iter__(self) -> Any:
            raise TypeError("Itération impossible.")

    monkeypatch.setattr(
        module,
        "get_default_allowed_directories",
        lambda: default_directories
    )

    assert normalize_allowed_directories(
        InvalidIterable()
    ) == default_directories


def test_normalize_allowed_directories_falls_back_when_all_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    default_directories = (
        tmp_path / "images",
    )

    monkeypatch.setattr(
        module,
        "get_default_allowed_directories",
        lambda: default_directories
    )

    result = normalize_allowed_directories([
        None,
        42,
        object()
    ])

    assert result == default_directories


# Contrôle d'appartenance

def test_is_path_inside_directory_returns_true(
    tmp_path: Path
) -> None:
    directory = tmp_path / "images"
    file_path = directory / "image.jpg"

    assert is_path_inside_directory(
        file_path,
        directory
    ) is True


def test_is_path_inside_directory_returns_false(
    tmp_path: Path
) -> None:
    directory = tmp_path / "images"
    file_path = tmp_path / "outside" / "image.jpg"

    assert is_path_inside_directory(
        file_path,
        directory
    ) is False


def test_is_path_inside_allowed_directories(
    tmp_path: Path
) -> None:
    allowed_directory = tmp_path / "images"
    file_path = allowed_directory / "source" / "image.jpg"

    assert is_path_inside_allowed_directories(
        file_path,
        [allowed_directory]
    ) is True


def test_is_path_inside_allowed_directories_rejects_outside_path(
    tmp_path: Path
) -> None:
    allowed_directory = tmp_path / "images"
    file_path = tmp_path / "outside" / "image.jpg"

    assert is_path_inside_allowed_directories(
        file_path,
        [allowed_directory]
    ) is False


def test_is_path_inside_allowed_directories_handles_invalid_path() -> None:
    with pytest.raises(AttributeError):
        is_path_inside_allowed_directories(
            None  # type: ignore[arg-type]
        )

def test_is_path_inside_images_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    images_directory = tmp_path / "images"
    image_path = images_directory / "image.jpg"

    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        images_directory
    )

    assert is_path_inside_images_directory(image_path) is True


# Enrichissement

def test_enrich_article_with_image_metadata(
    tmp_path: Path
) -> None:
    article = build_article()
    image_path = tmp_path / "image.jpg"
    metadata = build_metadata(
        width=1920,
        height=1080
    )

    enrich_article_with_image_metadata(
        article,
        image_path,
        metadata
    )

    assert article["image_path"] == str(image_path)
    assert article["image_format"] == "JPEG"
    assert article["image_mime_type"] == "image/jpeg"
    assert article["image_width"] == 1920
    assert article["image_height"] == 1080
    assert article["image_aspect_ratio"] == 1.777778
    assert article["image_mode"] == "RGB"
    assert article["image_size_bytes"] == 50_000
    assert article["image_file_hash"] == "abc123"
    assert article["image_metadata"]["pixel_count"] == 2_073_600


def test_enrich_article_with_image_metadata_merges_existing_metadata(
    tmp_path: Path
) -> None:
    article = build_article()
    article["image_metadata"] = {
        "source": "existing",
        "width": 10
    }

    metadata = build_metadata(
        width=800
    )

    enrich_article_with_image_metadata(
        article,
        tmp_path / "image.jpg",
        metadata
    )

    assert article["image_metadata"]["source"] == "existing"
    assert article["image_metadata"]["width"] == 800


def test_enrich_article_with_image_metadata_replaces_invalid_metadata(
    tmp_path: Path
) -> None:
    article = build_article()
    article["image_metadata"] = "invalid"

    enrich_article_with_image_metadata(
        article,
        tmp_path / "image.jpg",
        build_metadata()
    )

    assert isinstance(
        article["image_metadata"],
        dict
    )


# Taille des fichiers

def test_get_file_size_rejection_returns_not_found(
    tmp_path: Path
) -> None:
    image_path = tmp_path / "missing.jpg"

    assert (
        get_file_size_rejection(image_path)
        == IMAGE_REJECTION_NOT_FOUND
    )


def test_get_file_size_rejection_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "empty.jpg"
    image_path.touch()

    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        1000
    )

    assert (
        get_file_size_rejection(image_path)
        == IMAGE_REJECTION_EMPTY
    )


def test_get_file_size_rejection_returns_too_light(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "small.jpg"
    image_path.write_bytes(b"123")

    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        10
    )
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        1000
    )

    assert (
        get_file_size_rejection(image_path)
        == IMAGE_REJECTION_TOO_LIGHT
    )


def test_get_file_size_rejection_returns_too_large(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "large.jpg"
    image_path.write_bytes(b"1234567890")

    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        0
    )
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        5
    )

    assert (
        get_file_size_rejection(image_path)
        == IMAGE_REJECTION_TOO_LARGE
    )


def test_get_file_size_rejection_accepts_valid_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "valid.jpg"
    image_path.write_bytes(b"1234567890")

    monkeypatch.setattr(
        module,
        "MIN_IMAGE_SIZE_BYTES",
        5
    )
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        20
    )

    assert get_file_size_rejection(image_path) == ""


# Validation des métadonnées

def test_get_image_metadata_rejection_returns_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "image_matches_extension",
        lambda metadata, extension, mapping: False
    )

    result = get_image_metadata_rejection(
        image_path,
        build_metadata()
    )

    assert result == IMAGE_REJECTION_FORMAT


def test_get_image_metadata_rejection_returns_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "image_matches_extension",
        lambda metadata, extension, mapping: True
    )
    monkeypatch.setattr(
        module,
        "is_image_large_enough",
        lambda *args, **kwargs: False
    )

    result = get_image_metadata_rejection(
        image_path,
        build_metadata()
    )

    assert result == IMAGE_REJECTION_DIMENSIONS


def test_get_image_metadata_rejection_accepts_valid_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "image_matches_extension",
        lambda metadata, extension, mapping: True
    )
    monkeypatch.setattr(
        module,
        "is_image_large_enough",
        lambda *args, **kwargs: True
    )

    result = get_image_metadata_rejection(
        image_path,
        build_metadata()
    )

    assert result == ""


# Inspection

def test_inspect_valid_image_returns_size_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "get_file_size_rejection",
        lambda path: IMAGE_REJECTION_TOO_LIGHT
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: pytest.fail(
            "L'inspection ne devait pas être appelée."
        )
    )

    result = inspect_valid_image(image_path)

    assert result == (
        None,
        IMAGE_REJECTION_TOO_LIGHT
    )


def test_inspect_valid_image_returns_content_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "get_file_size_rejection",
        lambda path: ""
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: None
    )

    result = inspect_valid_image(image_path)

    assert result == (
        None,
        IMAGE_REJECTION_CONTENT
    )


def test_inspect_valid_image_returns_metadata_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    metadata = build_metadata()

    monkeypatch.setattr(
        module,
        "get_file_size_rejection",
        lambda path: ""
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )
    monkeypatch.setattr(
        module,
        "get_image_metadata_rejection",
        lambda path, current_metadata: IMAGE_REJECTION_FORMAT
    )

    result = inspect_valid_image(image_path)

    assert result == (
        None,
        IMAGE_REJECTION_FORMAT
    )


def test_inspect_valid_image_returns_valid_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    metadata = build_metadata()

    monkeypatch.setattr(
        module,
        "get_file_size_rejection",
        lambda path: ""
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )
    monkeypatch.setattr(
        module,
        "get_image_metadata_rejection",
        lambda path, current_metadata: ""
    )

    result = inspect_valid_image(image_path)

    assert result == (
        metadata,
        ""
    )


def test_get_image_content_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "inspect_valid_image",
        lambda path: (
            None,
            IMAGE_REJECTION_CONTENT
        )
    )

    assert (
        get_image_content_rejection(tmp_path / "image.jpg")
        == IMAGE_REJECTION_CONTENT
    )


# Détails des chemins

def test_get_image_path_details_returns_invalid_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda path: None
    )

    result = get_image_path_details("image.jpg")

    assert result == (
        None,
        None,
        IMAGE_REJECTION_INVALID_PATH
    )


def test_get_image_path_details_returns_outside_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda path: image_path
    )
    monkeypatch.setattr(
        module,
        "is_path_inside_allowed_directories",
        lambda path, allowed=None: False
    )

    result = get_image_path_details("image.jpg")

    assert result == (
        image_path,
        None,
        IMAGE_REJECTION_OUTSIDE_DIRECTORY
    )


def test_get_image_path_details_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "missing.jpg"

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda path: image_path
    )
    monkeypatch.setattr(
        module,
        "is_path_inside_allowed_directories",
        lambda path, allowed=None: True
    )

    result = get_image_path_details("image.jpg")

    assert result == (
        image_path,
        None,
        IMAGE_REJECTION_NOT_FOUND
    )


@pytest.mark.parametrize(
    "filename",
    [
        "image",
        "image.txt",
        "image.exe"
    ]
)
def test_get_image_path_details_rejects_extension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    filename: str
) -> None:
    image_path = tmp_path / filename
    image_path.write_bytes(b"image")

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda path: image_path
    )
    monkeypatch.setattr(
        module,
        "is_path_inside_allowed_directories",
        lambda path, allowed=None: True
    )

    result = get_image_path_details(filename)

    assert result == (
        image_path,
        None,
        IMAGE_REJECTION_EXTENSION
    )


def test_get_image_path_details_returns_inspection_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    metadata = build_metadata()

    monkeypatch.setattr(
        module,
        "resolve_image_path",
        lambda path: image_path
    )
    monkeypatch.setattr(
        module,
        "is_path_inside_allowed_directories",
        lambda path, allowed=None: True
    )
    monkeypatch.setattr(
        module,
        "inspect_valid_image",
        lambda path: (
            metadata,
            ""
        )
    )

    result = get_image_path_details("image.jpg")

    assert result == (
        image_path,
        metadata,
        ""
    )


def test_get_image_path_rejection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            Path("image.jpg"),
            None,
            IMAGE_REJECTION_CONTENT
        )
    )

    assert (
        get_image_path_rejection("image.jpg")
        == IMAGE_REJECTION_CONTENT
    )


# Rejet d'article

def test_get_article_image_rejection_rejects_invalid_article() -> None:
    assert (
        get_article_image_rejection(None)  # type: ignore[arg-type]
        == IMAGE_REJECTION_INVALID_ITEM
    )


@pytest.mark.parametrize(
    "image_path",
    [
        None,
        42,
        [],
        {}
    ]
)
def test_get_article_image_rejection_rejects_invalid_path(
    image_path: Any
) -> None:
    article = build_article(
        image_path=image_path
    )

    assert (
        get_article_image_rejection(article)
        == IMAGE_REJECTION_INVALID_PATH
    )


@pytest.mark.parametrize(
    "image_path",
    [
        "",
        " ",
        "\n"
    ]
)
def test_get_article_image_rejection_rejects_missing_path(
    image_path: str
) -> None:
    article = build_article(
        image_path=image_path
    )

    assert (
        get_article_image_rejection(article)
        == IMAGE_REJECTION_MISSING_PATH
    )


def test_get_article_image_rejection_uses_path_rejection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_rejection",
        lambda path, allowed=None: IMAGE_REJECTION_EXTENSION
    )

    assert (
        get_article_image_rejection(build_article())
        == IMAGE_REJECTION_EXTENSION
    )


# API booléenne

@pytest.mark.parametrize(
    ("image_path", "expected"),
    [
        (Path("image.jpg"), True),
        (Path("image.jpeg"), True),
        (Path("image.png"), True),
        (Path("image.webp"), True),
        (Path("image.avif"), True),
        (Path("image.txt"), False),
        (Path("image"), False),
        (None, False),
        ("image.jpg", False)
    ]
)
def test_has_allowed_extension(
    image_path: Any,
    expected: bool
) -> None:
    assert has_allowed_extension(image_path) is expected


def test_has_valid_file_size_returns_true(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_file_size_rejection",
        lambda path: ""
    )

    assert has_valid_file_size(Path("image.jpg")) is True


def test_has_valid_file_size_returns_false(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_file_size_rejection",
        lambda path: IMAGE_REJECTION_TOO_LARGE
    )

    assert has_valid_file_size(Path("image.jpg")) is False


def test_has_valid_file_size_rejects_invalid_type() -> None:
    assert has_valid_file_size("image.jpg") is False  # type: ignore[arg-type]


def test_validate_image_content_returns_true(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_content_rejection",
        lambda path: ""
    )

    assert validate_image_content(Path("image.jpg")) is True


def test_validate_image_content_returns_false(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_content_rejection",
        lambda path: IMAGE_REJECTION_CONTENT
    )

    assert validate_image_content(Path("image.jpg")) is False


def test_validate_image_content_rejects_invalid_type() -> None:
    assert validate_image_content("image.jpg") is False  # type: ignore[arg-type]


def test_is_valid_image_path_returns_true(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_rejection",
        lambda path, allowed=None: ""
    )

    assert is_valid_image_path("image.jpg") is True


def test_is_valid_image_path_returns_false(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_rejection",
        lambda path, allowed=None: IMAGE_REJECTION_CONTENT
    )

    assert is_valid_image_path("image.jpg") is False


# Validation d'article

def test_validate_article_image_rejects_invalid_article() -> None:
    assert validate_article_image(None) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "image_path",
    [
        None,
        42,
        [],
        {}
    ]
)
def test_validate_article_image_rejects_invalid_path(
    image_path: Any
) -> None:
    article = build_article(
        image_path=image_path
    )

    assert validate_article_image(article) is False


@pytest.mark.parametrize(
    "image_path",
    [
        "",
        " ",
        "\n"
    ]
)
def test_validate_article_image_rejects_missing_path(
    image_path: str
) -> None:
    article = build_article(
        image_path=image_path
    )

    assert validate_article_image(article) is False


def test_validate_article_image_rejects_path_rejection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            Path("image.jpg"),
            None,
            IMAGE_REJECTION_CONTENT
        )
    )

    assert validate_article_image(build_article()) is False


def test_validate_article_image_rejects_missing_metadata(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            Path("image.jpg"),
            None,
            ""
        )
    )

    assert validate_article_image(build_article()) is False


def test_validate_article_image_enriches_valid_article(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    metadata = build_metadata()

    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            image_path,
            metadata,
            ""
        )
    )

    article = build_article()

    result = validate_article_image(article)

    assert result is True
    assert article["image_path"] == str(image_path)
    assert article["image_format"] == "JPEG"
    assert article["image_width"] == 800


# Résumé des rejets

def test_format_rejection_summary() -> None:
    rejections = Counter({
        IMAGE_REJECTION_MISSING_PATH: 3,
        IMAGE_REJECTION_CONTENT: 2,
        "custom": 1
    })

    result = format_rejection_summary(rejections)

    assert result == (
        "chemin absent=3, contenu invalide=2, custom=1"
    )


def test_format_rejection_summary_returns_aucun() -> None:
    assert format_rejection_summary(Counter()) == "aucun"


# Validation de collection

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "articles",
        42
    ]
)
def test_validate_images_for_articles_rejects_invalid_collection(
    articles: Any
) -> None:
    assert validate_images_for_articles(articles) == []


def test_validate_images_for_articles_handles_empty_list() -> None:
    assert validate_images_for_articles([]) == []


def test_validate_images_for_articles_ignores_invalid_items() -> None:
    result = validate_images_for_articles([
        None,
        "article",
        42
    ])

    assert result == []


def test_validate_images_for_articles_rejects_invalid_image_path() -> None:
    result = validate_images_for_articles([
        build_article(
            image_path=None
        )
    ])

    assert result == []


def test_validate_images_for_articles_rejects_missing_image_path() -> None:
    result = validate_images_for_articles([
        build_article(
            image_path=""
        )
    ])

    assert result == []


def test_validate_images_for_articles_rejects_path_details_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            Path("image.jpg"),
            None,
            IMAGE_REJECTION_CONTENT
        )
    )

    result = validate_images_for_articles([
        build_article()
    ])

    assert result == []


def test_validate_images_for_articles_rejects_missing_metadata(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            Path("image.jpg"),
            None,
            ""
        )
    )

    result = validate_images_for_articles([
        build_article()
    ])

    assert result == []


def test_validate_images_for_articles_keeps_and_enriches_valid_article(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    image_path = tmp_path / "image.jpg"
    metadata = build_metadata()

    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            image_path,
            metadata,
            ""
        )
    )

    article = build_article()
    original = article.copy()

    result = validate_images_for_articles([
        article
    ])

    assert len(result) == 1
    assert result[0]["image_path"] == str(image_path)
    assert result[0]["image_format"] == "JPEG"
    assert result[0]["image_width"] == 800
    assert article == original


def test_validate_images_for_articles_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = build_metadata()

    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda path, allowed=None: (
            tmp_path / Path(path).name,
            metadata,
            ""
        )
    )

    result = validate_images_for_articles([
        build_article(
            "article-1",
            image_path="first.jpg"
        ),
        build_article(
            "article-2",
            image_path="second.jpg"
        )
    ])

    assert [
        article["id"]
        for article in result
    ] == [
        "article-1",
        "article-2"
    ]


def test_validate_images_for_articles_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(RuntimeError("Erreur inattendue."))
    )

    result = validate_images_for_articles([
        build_article()
    ])

    assert result == []


def test_validate_images_for_articles_logs_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    metadata = build_metadata()
    info_calls: list[tuple[Any, ...]] = []

    def fake_get_details(
        image_path: str,
        allowed_directories: Any = None
    ) -> tuple[Path | None, ImageMetadata | None, str]:
        if image_path == "valid.jpg":
            return (
                tmp_path / "valid.jpg",
                metadata,
                ""
            )

        return (
            tmp_path / "invalid.jpg",
            None,
            IMAGE_REJECTION_CONTENT
        )

    monkeypatch.setattr(
        module,
        "get_image_path_details",
        fake_get_details
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    result = validate_images_for_articles([
        build_article(
            "valid",
            image_path="valid.jpg"
        ),
        build_article(
            "invalid",
            image_path="invalid.jpg"
        ),
        None
    ])

    assert len(result) == 1
    assert len(info_calls) == 1
    assert info_calls[0][1:] == (
        3,
        1,
        2,
        "contenu invalide=1, format article=1"
    )


def test_validate_images_for_articles_counts_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_summary: list[str] = []

    monkeypatch.setattr(
        module,
        "get_image_path_details",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(RuntimeError("Erreur."))
    )
    monkeypatch.setattr(
        module,
        "format_rejection_summary",
        lambda rejections: (
            captured_summary.append(
                str(dict(rejections))
            )
            or "résumé"
        )
    )

    validate_images_for_articles([
        build_article()
    ])

    assert captured_summary == [
        str({
            IMAGE_REJECTION_UNEXPECTED: 1
        })
    ]