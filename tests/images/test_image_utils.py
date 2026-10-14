"""Tests des fonctions techniques communes de manipulation des images.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import src.images.image_utils as module
from src.images.image_utils import (
    AvifSupport,
    ImageMetadata,
    build_image_metadata,
    compute_file_sha256,
    compute_stream_sha256,
    get_avif_support,
    get_image_extension,
    get_image_mime_type,
    has_avif_support,
    image_matches_extension,
    inspect_image,
    inspect_image_file,
    is_image_large_enough,
    log_image_capabilities,
    normalize_image_extension,
    save_image_content
)


# Utilitaires

def create_image_bytes(
    image_format: str = "PNG",
    size: tuple[int, int] = (320, 240),
    mode: str = "RGB"
) -> bytes:
    """Crée une image valide en mémoire."""

    buffer = BytesIO()

    with Image.new(mode, size) as image:
        image.save(buffer, format=image_format)

    return buffer.getvalue()


def create_image_file(
    path: Path,
    image_format: str = "PNG",
    size: tuple[int, int] = (320, 240),
    mode: str = "RGB"
) -> Path:
    """Crée une image valide sur le disque."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with Image.new(mode, size) as image:
        image.save(path, format=image_format)

    return path


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
    """Construit des métadonnées techniques pour les tests."""

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


# ImageMetadata

def test_image_metadata_pixel_count() -> None:
    metadata = build_metadata(
        width=800,
        height=600
    )

    assert metadata.pixel_count == 480_000


def test_image_metadata_aspect_ratio() -> None:
    metadata = build_metadata(
        width=1920,
        height=1080
    )

    assert metadata.aspect_ratio == 1.777778


def test_image_metadata_aspect_ratio_returns_none_for_zero_height() -> None:
    metadata = build_metadata(
        width=100,
        height=0
    )

    assert metadata.aspect_ratio is None


def test_image_metadata_to_dict() -> None:
    metadata = build_metadata(
        width=640,
        height=480
    )

    result = metadata.to_dict()

    assert result == {
        "format": "JPEG",
        "extension": ".jpg",
        "mime_type": "image/jpeg",
        "width": 640,
        "height": 480,
        "mode": "RGB",
        "animated": False,
        "frame_count": 1,
        "file_size": 50_000,
        "file_hash": "abc123",
        "pixel_count": 307_200,
        "aspect_ratio": 1.333333
    }


# AvifSupport

@pytest.mark.parametrize(
    ("readable", "writable", "expected"),
    [
        (True, True, True),
        (True, False, True),
        (False, True, False),
        (False, False, False)
    ]
)
def test_avif_support_available(
    readable: bool,
    writable: bool,
    expected: bool
) -> None:
    support = AvifSupport(
        readable=readable,
        writable=writable,
        plugin_loaded=True
    )

    assert support.available is expected


def test_get_avif_support_detects_registered_extension(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.Image,
        "registered_extensions",
        lambda: {
            ".jpg": "JPEG",
            ".avif": "AVIF"
        }
    )
    monkeypatch.setattr(
        module.Image,
        "OPEN",
        {}
    )
    monkeypatch.setattr(
        module.Image,
        "SAVE",
        {
            "AVIF": object()
        }
    )
    monkeypatch.setattr(
        module,
        "pillow_avif",
        object()
    )

    support = get_avif_support()

    assert support == AvifSupport(
        readable=True,
        writable=True,
        plugin_loaded=True
    )


def test_get_avif_support_detects_open_registry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.Image,
        "registered_extensions",
        lambda: {}
    )
    monkeypatch.setattr(
        module.Image,
        "OPEN",
        {
            "AVIF": object()
        }
    )
    monkeypatch.setattr(
        module.Image,
        "SAVE",
        {}
    )
    monkeypatch.setattr(
        module,
        "pillow_avif",
        None
    )

    support = get_avif_support()

    assert support == AvifSupport(
        readable=True,
        writable=False,
        plugin_loaded=False
    )


@pytest.mark.parametrize(
    ("support", "require_write", "expected"),
    [
        (
            AvifSupport(True, True, True),
            False,
            True
        ),
        (
            AvifSupport(True, False, True),
            False,
            True
        ),
        (
            AvifSupport(True, False, True),
            True,
            False
        ),
        (
            AvifSupport(False, True, True),
            False,
            False
        ),
        (
            AvifSupport(False, True, True),
            True,
            False
        )
    ]
)
def test_has_avif_support(
    monkeypatch: pytest.MonkeyPatch,
    support: AvifSupport,
    require_write: bool,
    expected: bool
) -> None:
    monkeypatch.setattr(
        module,
        "get_avif_support",
        lambda: support
    )

    assert has_avif_support(require_write=require_write) is expected


def test_log_image_capabilities(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    support = AvifSupport(
        readable=True,
        writable=False,
        plugin_loaded=True
    )
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "get_avif_support",
        lambda: support
    )
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda *args, **kwargs: calls.append(args)
    )

    log_image_capabilities()

    assert len(calls) == 1
    assert calls[0][1:] == (
        True,
        False,
        True
    )


# Extensions

@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        ("jpg", ".jpg"),
        (".JPG", ".jpg"),
        ("  PNG  ", ".png"),
        ("...webp", ".webp"),
        ("", ""),
        ("   ", ""),
        (None, ""),
        (123, "")
    ]
)
def test_normalize_image_extension(
    extension: Any,
    expected: str
) -> None:
    assert normalize_image_extension(extension) == expected


@pytest.mark.parametrize(
    ("image_format", "expected"),
    [
        ("JPEG", ".jpg"),
        ("jpeg", ".jpg"),
        (" PNG ", ".png"),
        ("WEBP", ".webp"),
        ("GIF", ".gif"),
        ("TIFF", ".tiff"),
        ("BMP", ".bmp"),
        ("AVIF", ".avif"),
        ("", ""),
        (None, "")
    ]
)
def test_get_image_extension_configured_formats(
    image_format: Any,
    expected: str
) -> None:
    assert get_image_extension(image_format) == expected


def test_get_image_extension_uses_pillow_registry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.Image,
        "registered_extensions",
        lambda: {
            ".custom": "CUSTOM"
        }
    )

    assert get_image_extension("custom") == ".custom"


def test_get_image_extension_returns_empty_for_unknown_format(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.Image,
        "registered_extensions",
        lambda: {
            ".jpg": "JPEG"
        }
    )

    assert get_image_extension("UNKNOWN") == ""


@pytest.mark.parametrize(
    ("image_format", "expected"),
    [
        ("JPEG", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        (" PNG ", "image/png"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
        ("TIFF", "image/tiff"),
        ("BMP", "image/bmp"),
        ("AVIF", "image/avif"),
        ("", ""),
        (None, "")
    ]
)
def test_get_image_mime_type_configured_formats(
    image_format: Any,
    expected: str
) -> None:
    assert get_image_mime_type(image_format) == expected


def test_get_image_mime_type_uses_pillow_registry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.Image,
        "MIME",
        {
            "CUSTOM": "image/custom"
        }
    )

    assert get_image_mime_type("custom") == "image/custom"


def test_get_image_mime_type_returns_empty_for_unknown_format(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.Image,
        "MIME",
        {}
    )

    assert get_image_mime_type("UNKNOWN") == ""


# Empreinte des fichiers

def test_compute_file_sha256(tmp_path: Path) -> None:
    content = b"CheckIt.AI image content"
    path = tmp_path / "image.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()

    assert compute_file_sha256(path) == expected


def test_compute_file_sha256_reads_multiple_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "HASH_CHUNK_SIZE",
        4
    )

    content = b"0123456789abcdef"
    path = tmp_path / "image.bin"
    path.write_bytes(content)

    assert compute_file_sha256(path) == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "file_path",
    [
        "missing-file.jpg",
        None,
        object()
    ]
)
def test_compute_file_sha256_returns_empty_for_invalid_file(
    file_path: Any
) -> None:
    assert compute_file_sha256(file_path) == ""


# Empreinte des flux

def test_compute_stream_sha256() -> None:
    content = b"image stream content"
    stream = BytesIO(content)

    assert compute_stream_sha256(stream) == hashlib.sha256(content).hexdigest()


def test_compute_stream_sha256_restores_position() -> None:
    stream = BytesIO(b"0123456789")
    stream.seek(5)

    compute_stream_sha256(stream)

    assert stream.tell() == 5


def test_compute_stream_sha256_reads_from_start() -> None:
    content = b"0123456789"
    stream = BytesIO(content)
    stream.seek(8)

    result = compute_stream_sha256(stream)

    assert result == hashlib.sha256(content).hexdigest()


class InvalidStream:
    """Simule un flux inutilisable."""

    def tell(self) -> int:
        raise OSError("Position indisponible.")

    def seek(self, *args: Any) -> None:
        raise OSError("Déplacement impossible.")

    def read(self, *args: Any) -> bytes:
        raise OSError("Lecture impossible.")


def test_compute_stream_sha256_returns_empty_for_invalid_stream() -> None:
    assert compute_stream_sha256(InvalidStream()) == ""  # type: ignore[arg-type]


# Construction des métadonnées

def test_build_image_metadata() -> None:
    with Image.new("RGB", (640, 480)) as image:
        image.format = "PNG"

        metadata = build_image_metadata(
            image,
            file_size=1000,
            file_hash="ABCDEF"
        )

    assert metadata == ImageMetadata(
        format="PNG",
        extension=".png",
        mime_type="image/png",
        width=640,
        height=480,
        mode="RGB",
        animated=False,
        frame_count=1,
        file_size=1000,
        file_hash="abcdef"
    )


def test_build_image_metadata_normalizes_negative_file_size() -> None:
    with Image.new("RGB", (10, 10)) as image:
        image.format = "JPEG"

        metadata = build_image_metadata(
            image,
            file_size=-50
        )

    assert metadata is not None
    assert metadata.file_size == 0


def test_build_image_metadata_rejects_missing_format() -> None:
    with Image.new("RGB", (10, 10)) as image:
        image.format = None

        assert build_image_metadata(image) is None


def test_build_image_metadata_rejects_missing_mode() -> None:
    class FakeImage:
        format = "PNG"
        mode = ""
        size = (10, 10)
        is_animated = False
        n_frames = 1

    assert build_image_metadata(FakeImage()) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "size",
    [
        (0, 10),
        (10, 0),
        (-1, 10),
        (10, -1)
    ]
)
def test_build_image_metadata_rejects_invalid_dimensions(
    size: tuple[int, int]
) -> None:
    class FakeImage:
        format = "PNG"
        mode = "RGB"
        is_animated = False
        n_frames = 1

    image = FakeImage()
    image.size = size

    assert build_image_metadata(image) is None  # type: ignore[arg-type]


def test_build_image_metadata_rejects_unsupported_avif(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "has_avif_support",
        lambda require_write=False: False
    )

    with Image.new("RGB", (10, 10)) as image:
        image.format = "AVIF"

        assert build_image_metadata(image) is None


def test_build_image_metadata_rejects_animation_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ALLOW_ANIMATED_IMAGES",
        False
    )

    class FakeImage:
        format = "GIF"
        mode = "P"
        size = (100, 100)
        is_animated = True
        n_frames = 2

    assert build_image_metadata(FakeImage()) is None  # type: ignore[arg-type]


def test_build_image_metadata_accepts_animation_when_enabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ALLOW_ANIMATED_IMAGES",
        True
    )
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_FRAME_COUNT",
        10
    )

    class FakeImage:
        format = "GIF"
        mode = "P"
        size = (100, 100)
        is_animated = True
        n_frames = 2

    metadata = build_image_metadata(FakeImage())  # type: ignore[arg-type]

    assert metadata is not None
    assert metadata.animated is True
    assert metadata.frame_count == 2


def test_build_image_metadata_rejects_too_many_frames(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "ALLOW_ANIMATED_IMAGES",
        True
    )
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_FRAME_COUNT",
        2
    )

    class FakeImage:
        format = "GIF"
        mode = "P"
        size = (100, 100)
        is_animated = True
        n_frames = 3

    assert build_image_metadata(FakeImage()) is None  # type: ignore[arg-type]


def test_build_image_metadata_rejects_unknown_extension(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_extension",
        lambda image_format: ""
    )

    with Image.new("RGB", (10, 10)) as image:
        image.format = "CUSTOM"

        assert build_image_metadata(image) is None


def test_build_image_metadata_rejects_unknown_mime_type(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_image_extension",
        lambda image_format: ".custom"
    )
    monkeypatch.setattr(
        module,
        "get_image_mime_type",
        lambda image_format: ""
    )

    with Image.new("RGB", (10, 10)) as image:
        image.format = "CUSTOM"

        assert build_image_metadata(image) is None


# Inspection des fichiers

def test_inspect_image_returns_metadata(tmp_path: Path) -> None:
    path = create_image_file(
        tmp_path / "image.png",
        image_format="PNG",
        size=(320, 240)
    )

    metadata = inspect_image(path)

    assert metadata is not None
    assert metadata.format == "PNG"
    assert metadata.extension == ".png"
    assert metadata.mime_type == "image/png"
    assert metadata.width == 320
    assert metadata.height == 240
    assert metadata.mode == "RGB"
    assert metadata.animated is False
    assert metadata.frame_count == 1
    assert metadata.file_size == path.stat().st_size
    assert metadata.file_hash == compute_file_sha256(path)


def test_inspect_image_accepts_string_path(tmp_path: Path) -> None:
    path = create_image_file(
        tmp_path / "image.jpg",
        image_format="JPEG"
    )

    metadata = inspect_image(str(path))

    assert metadata is not None
    assert metadata.format == "JPEG"


@pytest.mark.parametrize(
    "image_path",
    [
        "missing.png",
        None,
        object()
    ]
)
def test_inspect_image_rejects_invalid_path(
    image_path: Any
) -> None:
    assert inspect_image(image_path) is None


def test_inspect_image_rejects_directory(tmp_path: Path) -> None:
    assert inspect_image(tmp_path) is None


def test_inspect_image_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.png"
    path.touch()

    assert inspect_image(path) is None


def test_inspect_image_rejects_non_image_file(tmp_path: Path) -> None:
    path = tmp_path / "fake.jpg"
    path.write_text(
        "<html>Not an image</html>",
        encoding="utf-8"
    )

    assert inspect_image(path) is None


def test_inspect_image_rejects_truncated_image(tmp_path: Path) -> None:
    content = create_image_bytes(
        image_format="PNG",
        size=(200, 200)
    )
    path = tmp_path / "truncated.png"
    path.write_bytes(content[: len(content) // 2])

    assert inspect_image(path) is None


# Inspection des flux

def test_inspect_image_file_returns_metadata() -> None:
    content = create_image_bytes(
        image_format="PNG",
        size=(400, 300)
    )
    stream = BytesIO(content)

    metadata = inspect_image_file(stream)

    assert metadata is not None
    assert metadata.format == "PNG"
    assert metadata.width == 400
    assert metadata.height == 300
    assert metadata.file_size == len(content)
    assert metadata.file_hash == hashlib.sha256(content).hexdigest()


def test_inspect_image_file_restores_position() -> None:
    content = create_image_bytes()
    stream = BytesIO(content)
    stream.seek(5)

    inspect_image_file(stream)

    assert stream.tell() == 5


def test_inspect_image_file_rejects_empty_stream() -> None:
    assert inspect_image_file(BytesIO()) is None


def test_inspect_image_file_rejects_invalid_content() -> None:
    stream = BytesIO(b"<html>invalid image</html>")

    assert inspect_image_file(stream) is None


def test_inspect_image_file_rejects_truncated_content() -> None:
    content = create_image_bytes()
    stream = BytesIO(content[: len(content) // 2])

    assert inspect_image_file(stream) is None


def test_inspect_image_file_rejects_invalid_stream() -> None:
    assert inspect_image_file(InvalidStream()) is None  # type: ignore[arg-type]


# Validation des dimensions

def test_is_image_large_enough_accepts_large_image() -> None:
    metadata = build_metadata(
        width=800,
        height=600
    )

    assert is_image_large_enough(
        metadata,
        min_width=224,
        min_height=224,
        min_pixel_count=50_176
    ) is True


def test_is_image_large_enough_rejects_small_pixel_count() -> None:
    metadata = build_metadata(
        width=300,
        height=100
    )

    assert is_image_large_enough(
        metadata,
        min_width=224,
        min_height=224,
        min_pixel_count=50_000
    ) is False


def test_is_image_large_enough_accepts_portrait_image() -> None:
    metadata = build_metadata(
        width=180,
        height=500
    )

    assert is_image_large_enough(
        metadata,
        min_width=224,
        min_height=224
    ) is True


def test_is_image_large_enough_accepts_landscape_image() -> None:
    metadata = build_metadata(
        width=500,
        height=180
    )

    assert is_image_large_enough(
        metadata,
        min_width=224,
        min_height=224
    ) is True


def test_is_image_large_enough_rejects_both_dimensions_too_small() -> None:
    metadata = build_metadata(
        width=180,
        height=180
    )

    assert is_image_large_enough(
        metadata,
        min_width=224,
        min_height=224
    ) is False


def test_is_image_large_enough_accepts_disabled_limits() -> None:
    metadata = build_metadata(
        width=1,
        height=1
    )

    assert is_image_large_enough(
        metadata,
        min_width=0,
        min_height=0,
        min_pixel_count=0
    ) is True


def test_is_image_large_enough_normalizes_negative_limits() -> None:
    metadata = build_metadata(
        width=1,
        height=1
    )

    assert is_image_large_enough(
        metadata,
        min_width=-1,
        min_height=-1,
        min_pixel_count=-1
    ) is True


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        object()
    ]
)
def test_is_image_large_enough_rejects_invalid_metadata(
    metadata: Any
) -> None:
    assert is_image_large_enough(
        metadata,
        min_width=224,
        min_height=224
    ) is False


# Cohérence format et extension

@pytest.mark.parametrize(
    ("extension", "mapping", "expected"),
    [
        (
            ".jpg",
            {".jpg": {"JPEG"}},
            True
        ),
        (
            "jpg",
            {"jpg": {"JPEG"}},
            True
        ),
        (
            ".jpeg",
            {".jpeg": ["JPEG"]},
            True
        ),
        (
            ".png",
            {".png": {"PNG"}},
            False
        ),
        (
            ".jpg",
            {".jpg": {"jpeg", "jpg"}},
            True
        ),
        (
            ".jpg",
            {},
            False
        ),
        (
            "",
            {".jpg": {"JPEG"}},
            False
        )
    ]
)
def test_image_matches_extension(
    extension: str,
    mapping: dict[str, Any],
    expected: bool
) -> None:
    metadata = build_metadata(
        image_format="JPEG"
    )

    assert image_matches_extension(
        metadata,
        extension,
        mapping
    ) is expected


@pytest.mark.parametrize(
    ("metadata", "extension", "mapping"),
    [
        (
            None,
            ".jpg",
            {".jpg": {"JPEG"}}
        ),
        (
            build_metadata(),
            None,
            {".jpg": {"JPEG"}}
        ),
        (
            build_metadata(),
            ".jpg",
            None
        ),
        (
            build_metadata(),
            ".jpg",
            []
        )
    ]
)
def test_image_matches_extension_rejects_invalid_arguments(
    metadata: Any,
    extension: Any,
    mapping: Any
) -> None:
    assert image_matches_extension(
        metadata,
        extension,
        mapping
    ) is False


# Sauvegarde

@pytest.mark.parametrize(
    ("extension", "expected_format"),
    [
        (".jpg", "JPEG"),
        (".jpeg", "JPEG"),
        (".png", "PNG"),
        (".webp", "WEBP")
    ]
)
def test_save_image_content(
    tmp_path: Path,
    extension: str,
    expected_format: str
) -> None:
    content = create_image_bytes(
        image_format="PNG",
        size=(320, 240)
    )
    path = tmp_path / f"saved{extension}"

    result = save_image_content(
        content,
        path,
        extension
    )

    assert result is True
    assert path.is_file()

    metadata = inspect_image(path)

    assert metadata is not None
    assert metadata.format == expected_format
    assert metadata.width == 320
    assert metadata.height == 240


def test_save_image_content_creates_parent_directory(
    tmp_path: Path
) -> None:
    path = tmp_path / "nested" / "images" / "image.png"
    content = create_image_bytes()

    result = save_image_content(
        content,
        path,
        ".png"
    )

    assert result is True
    assert path.is_file()


def test_save_image_content_converts_rgba_to_rgb_for_jpeg(
    tmp_path: Path
) -> None:
    content = create_image_bytes(
        image_format="PNG",
        mode="RGBA"
    )
    path = tmp_path / "image.jpg"

    result = save_image_content(
        content,
        path,
        ".jpg"
    )

    assert result is True

    metadata = inspect_image(path)

    assert metadata is not None
    assert metadata.format == "JPEG"
    assert metadata.mode == "RGB"


@pytest.mark.parametrize(
    "content",
    [
        None,
        b"",
        "image",
        bytearray(b"image")
    ]
)
def test_save_image_content_rejects_invalid_content(
    tmp_path: Path,
    content: Any
) -> None:
    assert save_image_content(
        content,
        tmp_path / "image.png",
        ".png"
    ) is False


@pytest.mark.parametrize(
    "extension",
    [
        "",
        ".gif",
        ".bmp",
        ".tiff",
        ".unknown"
    ]
)
def test_save_image_content_rejects_unsupported_extension(
    tmp_path: Path,
    extension: str
) -> None:
    content = create_image_bytes()

    assert save_image_content(
        content,
        tmp_path / "image.bin",
        extension
    ) is False


def test_save_image_content_rejects_invalid_image_content(
    tmp_path: Path
) -> None:
    path = tmp_path / "image.png"

    result = save_image_content(
        b"<html>not an image</html>",
        path,
        ".png"
    )

    assert result is False
    assert not path.exists()


def test_save_image_content_rejects_truncated_image(
    tmp_path: Path
) -> None:
    content = create_image_bytes()
    truncated_content = content[: len(content) // 2]
    path = tmp_path / "image.png"

    result = save_image_content(
        truncated_content,
        path,
        ".png"
    )

    assert result is False


def test_save_image_content_rejects_avif_without_write_support(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "has_avif_support",
        lambda require_write=False: False
    )

    content = create_image_bytes()
    path = tmp_path / "image.avif"

    result = save_image_content(
        content,
        path,
        ".avif"
    )

    assert result is False
    assert not path.exists()


def test_save_image_content_rejects_unavailable_pillow_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    content = create_image_bytes()
    path = tmp_path / "image.webp"

    monkeypatch.setattr(
        module.Image,
        "SAVE",
        {
            key: value
            for key, value in module.Image.SAVE.items()
            if key != "WEBP"
        }
    )

    result = save_image_content(
        content,
        path,
        ".webp"
    )

    assert result is False
    assert not path.exists()
    

def test_save_image_content_removes_invalid_saved_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    content = create_image_bytes()
    path = tmp_path / "image.png"

    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda image_path: None
    )

    result = save_image_content(
        content,
        path,
        ".png"
    )

    assert result is False
    assert not path.exists()


def test_save_image_content_handles_invalid_destination(
    tmp_path: Path
) -> None:
    content = create_image_bytes()
    invalid_destination = tmp_path / "directory"
    invalid_destination.mkdir()

    result = save_image_content(
        content,
        invalid_destination,
        ".png"
    )

    assert result is False