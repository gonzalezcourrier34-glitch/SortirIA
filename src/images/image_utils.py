"""Fonctions techniques communes de manipulation des images.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/images/image_utils.py` (commit 93632c5).

Ce module centralise les opérations liées à Pillow :

- la configuration des protections Pillow ;
- la détection du support AVIF ;
- l'ouverture sécurisée des images locales ou en mémoire ;
- la vérification du contenu réel et des fichiers tronqués ;
- l'extraction des métadonnées techniques ;
- le calcul de l'empreinte SHA-256 ;
- le contrôle des dimensions, formats et extensions ;
- la sauvegarde contrôlée d'images reçues en mémoire.

Il ne dépend ni des articles, ni des chemins métier, ni du réseau.
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

try:
    import pillow_avif  # type: ignore[import-not-found]  # noqa: F401
except ImportError:
    pillow_avif = None

from PIL import Image, ImageFile, ImageOps

from config.settings import (
    ALLOW_ANIMATED_IMAGES,
    IMAGE_MAX_FRAME_COUNT,
    IMAGE_MAX_PIXELS
)
from src.logger import get_logger

logger = get_logger(__name__)

Image.MAX_IMAGE_PIXELS = max(int(IMAGE_MAX_PIXELS), 1)
ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.init()

HASH_CHUNK_SIZE = 1024 * 1024

FORMAT_EXTENSIONS = {
    "AVIF": ".avif",
    "BMP": ".bmp",
    "GIF": ".gif",
    "JPEG": ".jpg",
    "PNG": ".png",
    "TIFF": ".tiff",
    "WEBP": ".webp"
}

FORMAT_MIME_TYPES = {
    "AVIF": "image/avif",
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "TIFF": "image/tiff",
    "WEBP": "image/webp"
}


# Métadonnées

@dataclass(frozen=True, slots=True)
class ImageMetadata:
    """Informations techniques extraites d'une image valide."""

    format: str
    extension: str
    mime_type: str
    width: int
    height: int
    mode: str
    animated: bool
    frame_count: int
    file_size: int
    file_hash: str = ""

    @property
    def pixel_count(self) -> int:
        """Retourne le nombre total de pixels."""

        return self.width * self.height

    @property
    def aspect_ratio(self) -> float | None:
        """Retourne le rapport largeur sur hauteur."""

        if self.height <= 0:
            return None

        return round(self.width / self.height, 6)

    def to_dict(self) -> dict[str, Any]:
        """Convertit les métadonnées en dictionnaire sérialisable."""

        metadata = asdict(self)
        metadata["pixel_count"] = self.pixel_count
        metadata["aspect_ratio"] = self.aspect_ratio
        return metadata


@dataclass(frozen=True, slots=True)
class AvifSupport:
    """Décrit les capacités AVIF réellement disponibles dans Pillow."""

    readable: bool
    writable: bool
    plugin_loaded: bool

    @property
    def available(self) -> bool:
        """Indique si les fichiers AVIF peuvent être lus."""

        return self.readable


# Support AVIF

def get_avif_support() -> AvifSupport:
    """Retourne les capacités AVIF enregistrées dans Pillow."""

    Image.init()
    registered_extensions = {
        extension.lower(): str(image_format).upper()
        for extension, image_format in Image.registered_extensions().items()
    }

    return AvifSupport(
        readable=(
            registered_extensions.get(".avif") == "AVIF"
            or "AVIF" in Image.OPEN
        ),
        writable="AVIF" in Image.SAVE,
        plugin_loaded=pillow_avif is not None
    )


def has_avif_support(require_write: bool = False) -> bool:
    """Vérifie que Pillow sait lire, et éventuellement écrire, de l'AVIF."""

    support = get_avif_support()
    return support.readable and (support.writable if require_write else True)


def log_image_capabilities() -> None:
    """Journalise les capacités AVIF de l'installation."""

    support = get_avif_support()
    logger.debug(
        "Support AVIF Pillow : lecture=%s, écriture=%s, plugin=%s.",
        support.readable,
        support.writable,
        support.plugin_loaded
    )


# Formats et extensions

def normalize_image_extension(extension: str | None) -> str:
    """Normalise une extension avec un point initial."""

    if not isinstance(extension, str):
        return ""

    normalized_extension = extension.strip().lower()
    return (
        f".{normalized_extension.lstrip('.')}"
        if normalized_extension
        else ""
    )


def get_image_extension(image_format: str) -> str:
    """Retourne l'extension principale associée à un format Pillow."""

    normalized_format = str(image_format or "").strip().upper()

    if not normalized_format:
        return ""

    configured_extension = FORMAT_EXTENSIONS.get(normalized_format)

    if configured_extension:
        return configured_extension

    return next(
        (
            normalize_image_extension(extension)
            for extension, registered_format
            in Image.registered_extensions().items()
            if str(registered_format).strip().upper() == normalized_format
        ),
        ""
    )


def get_image_mime_type(image_format: str) -> str:
    """Retourne le type MIME principal associé à un format Pillow."""

    normalized_format = str(image_format or "").strip().upper()

    if not normalized_format:
        return ""

    return (
        FORMAT_MIME_TYPES.get(normalized_format)
        or Image.MIME.get(normalized_format, "")
    )


# Empreintes

def compute_file_sha256(file_path: str | Path) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier sans le charger en mémoire."""

    try:
        path = Path(file_path)
        digest = hashlib.sha256()

        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)

        return digest.hexdigest()

    except (OSError, TypeError, ValueError) as error:
        logger.debug(
            "Impossible de calculer l'empreinte SHA-256 de %s : %s",
            file_path,
            error
        )
        return ""


def compute_stream_sha256(file: BinaryIO) -> str:
    """Calcule l'empreinte SHA-256 d'un flux en restaurant sa position."""

    try:
        initial_position = file.tell()
    except (AttributeError, OSError, TypeError, ValueError):
        initial_position = 0

    try:
        file.seek(0)
        digest = hashlib.sha256()

        for chunk in iter(lambda: file.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)

        return digest.hexdigest()

    except (AttributeError, OSError, TypeError, ValueError) as error:
        logger.debug(
            "Impossible de calculer l'empreinte SHA-256 du flux : %s",
            error
        )
        return ""

    finally:
        try:
            file.seek(initial_position)
        except (AttributeError, OSError, TypeError, ValueError):
            pass


# Construction des métadonnées

def build_image_metadata(
    image: Image.Image,
    file_size: int = 0,
    file_hash: str = ""
) -> ImageMetadata | None:
    """Construit les métadonnées d'une image ouverte avec Pillow."""

    image_format = str(image.format or "").strip().upper()
    image_mode = str(image.mode or "").strip().upper()

    try:
        width, height = image.size
        animated = bool(getattr(image, "is_animated", False))
        frame_count = max(int(getattr(image, "n_frames", 1) or 1), 1)
    except (AttributeError, OSError, TypeError, ValueError):
        return None

    if not image_format or not image_mode or width <= 0 or height <= 0:
        return None
    if image_format == "AVIF" and not has_avif_support():
        return None
    if animated and not ALLOW_ANIMATED_IMAGES:
        return None
    if frame_count > max(int(IMAGE_MAX_FRAME_COUNT), 1):
        return None

    extension = get_image_extension(image_format)
    mime_type = get_image_mime_type(image_format)

    if not extension or not mime_type:
        return None

    return ImageMetadata(
        format=image_format,
        extension=extension,
        mime_type=mime_type,
        width=int(width),
        height=int(height),
        mode=image_mode,
        animated=animated,
        frame_count=frame_count,
        file_size=max(int(file_size), 0),
        file_hash=str(file_hash or "").strip().lower()
    )


# Inspection

def _inspect_open_image(
    file: BinaryIO,
    file_size: int,
    file_hash: str = ""
) -> ImageMetadata | None:
    """Vérifie un flux image sans décoder inutilement tous ses pixels."""

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        file.seek(0)

        with Image.open(file) as image:
            metadata = build_image_metadata(
                image,
                file_size=file_size,
                file_hash=file_hash
            )

            if metadata is None:
                return None

            image.verify()
            return metadata


def inspect_image(image_path: str | Path) -> ImageMetadata | None:
    """Vérifie une image locale et retourne ses métadonnées techniques."""

    try:
        path = Path(image_path)

        if not path.is_file():
            logger.debug(
                "Fichier image absent ou invalide : %s",
                path
            )
            return None

        file_size = path.stat().st_size

        if file_size <= 0:
            logger.debug(
                "Fichier image vide : %s",
                path
            )
            return None

        with path.open("rb") as image_file:
            metadata = _inspect_open_image(
                image_file,
                file_size=file_size
            )

        if metadata is None:
            logger.debug(
                "Métadonnées d'image invalides ou format inconnu : %s",
                path
            )
            return None

        return ImageMetadata(
            format=metadata.format,
            extension=metadata.extension,
            mime_type=metadata.mime_type,
            width=metadata.width,
            height=metadata.height,
            mode=metadata.mode,
            animated=metadata.animated,
            frame_count=metadata.frame_count,
            file_size=metadata.file_size,
            file_hash=compute_file_sha256(path)
        )

    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning
    ) as error:
        logger.debug(
            "Image bloquée par la limite de pixels %s : %s",
            image_path,
            error
        )
        return None

    except Image.UnidentifiedImageError:
        logger.debug(
            "Le fichier n'est pas reconnu comme une image : %s",
            image_path
        )
        return None

    except (
        AttributeError,
        EOFError,
        OSError,
        TypeError,
        ValueError,
        SyntaxError
    ) as error:
        logger.debug(
            "Impossible d'inspecter l'image %s : %s",
            image_path,
            error
        )
        return None


def inspect_image_file(file: BinaryIO) -> ImageMetadata | None:
    """Vérifie une image en mémoire et restaure la position du flux."""

    try:
        initial_position = file.tell()
    except (AttributeError, OSError, TypeError, ValueError):
        initial_position = 0

    try:
        file.seek(0, 2)
        file_size = file.tell()

        if file_size <= 0:
            logger.debug("Image en mémoire vide.")
            return None

        file_hash = compute_stream_sha256(file)

        return _inspect_open_image(
            file,
            file_size=file_size,
            file_hash=file_hash
        )

    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        logger.debug(
            "Image en mémoire bloquée par la limite de pixels : %s",
            error
        )
    except Image.UnidentifiedImageError:
        logger.debug(
            "Le contenu en mémoire n'est pas reconnu comme une image."
        )
    except (
        AttributeError,
        EOFError,
        OSError,
        TypeError,
        ValueError,
        SyntaxError
    ) as error:
        logger.debug(
            "Impossible d'inspecter l'image en mémoire : %s",
            error
        )
    finally:
        try:
            file.seek(initial_position)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    return None


# Validation technique

def is_image_large_enough(
    metadata: ImageMetadata,
    min_width: int,
    min_height: int,
    min_pixel_count: int = 0
) -> bool:
    """Applique une règle souple adaptée aux miniatures multimodales."""

    if not isinstance(metadata, ImageMetadata):
        return False

    minimum_width = max(int(min_width), 0)
    minimum_height = max(int(min_height), 0)
    minimum_pixel_count = max(int(min_pixel_count), 0)

    if minimum_pixel_count and metadata.pixel_count < minimum_pixel_count:
        return False

    return not (
        minimum_width
        and minimum_height
        and metadata.width < minimum_width
        and metadata.height < minimum_height
    )


def image_matches_extension(
    metadata: ImageMetadata,
    extension: str,
    extension_mapping: Mapping[str, Collection[str]]
) -> bool:
    """Vérifie la cohérence entre l'extension et le format réel détecté."""

    if (
        not isinstance(metadata, ImageMetadata)
        or not isinstance(extension, str)
        or not isinstance(extension_mapping, Mapping)
    ):
        return False

    normalized_extension = normalize_image_extension(extension)

    if not normalized_extension:
        return False

    configured_formats = (
        extension_mapping.get(normalized_extension)
        or extension_mapping.get(normalized_extension.lstrip("."))
        or ()
    )
    expected_formats = {
        str(image_format).strip().upper()
        for image_format in configured_formats
        if str(image_format).strip()
    }

    return metadata.format in expected_formats


# Sauvegarde

def save_image_content(
    image_content: bytes,
    image_path: str | Path,
    extension: str
) -> bool:
    """Prépare et enregistre une image reçue sous forme d'octets."""

    if not isinstance(image_content, bytes) or not image_content:
        logger.debug("Contenu d'image absent ou invalide.")
        return False

    normalized_extension = normalize_image_extension(extension)
    save_parameters = {
        ".avif": ("AVIF", {"quality": 80}),
        ".jpg": ("JPEG", {"quality": 90, "optimize": True}),
        ".jpeg": ("JPEG", {"quality": 90, "optimize": True}),
        ".png": ("PNG", {"optimize": True}),
        ".webp": ("WEBP", {"quality": 90, "method": 6})
    }
    parameters = save_parameters.get(normalized_extension)

    if parameters is None:
        logger.debug(
            "Extension d'image non prise en charge : %s",
            normalized_extension
        )
        return False

    try:
        destination_path = Path(image_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, TypeError, ValueError) as error:
        logger.debug(
            "Chemin de sauvegarde d'image invalide : %s",
            error
        )
        return False

    image_format, options = parameters
    Image.init()

    if image_format == "AVIF" and not has_avif_support(require_write=True):
        logger.debug("Écriture du format AVIF indisponible dans Pillow.")
        return False

    if image_format not in Image.SAVE:
        logger.debug(
            "Écriture du format %s indisponible dans Pillow.",
            image_format
        )
        return False

    try:
        with BytesIO(image_content) as image_buffer:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)

                with Image.open(image_buffer) as verification_image:
                    verification_image.verify()

                image_buffer.seek(0)

                with Image.open(image_buffer) as source_image:
                    source_image.load()
                    prepared_image = ImageOps.exif_transpose(source_image)
                    image_to_save = prepared_image

                    if (
                        image_format == "JPEG"
                        and prepared_image.mode not in {"RGB", "L"}
                    ):
                        image_to_save = prepared_image.convert("RGB")

                    try:
                        image_to_save.save(
                            destination_path,
                            format=image_format,
                            **options
                        )
                    finally:
                        if image_to_save is not prepared_image:
                            image_to_save.close()
                        if prepared_image is not source_image:
                            prepared_image.close()

    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        logger.debug(
            "Image non enregistrée à cause de la limite de pixels : %s",
            error
        )
        return False
    except Image.UnidentifiedImageError:
        logger.debug(
            "Le contenu à enregistrer n'est pas une image reconnue."
        )
        return False
    except (
        EOFError,
        OSError,
        TypeError,
        ValueError,
        SyntaxError
    ) as error:
        logger.debug(
            "Impossible d'enregistrer l'image %s : %s",
            destination_path,
            error
        )
        return False

    if inspect_image(destination_path) is not None:
        return True

    try:
        destination_path.unlink(missing_ok=True)
    except OSError as error:
        logger.debug(
            "Impossible de supprimer l'image invalide %s : %s",
            destination_path,
            error
        )

    return False