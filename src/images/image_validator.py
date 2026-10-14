"""Validation des images locales associées aux articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/images/image_validator.py` (commit 93632c5).

Ce module prend en charge :

- la résolution sécurisée des chemins locaux ;
- la restriction aux dossiers autorisés ;
- le contrôle des extensions et de la taille des fichiers ;
- la validation du format réel et des dimensions ;
- l'enrichissement des articles avec les métadonnées techniques ;
- l'agrégation des motifs de rejet d'une collection.

Les opérations Pillow sont déléguées à image_utils.
Ce module ne télécharge aucune image et n'effectue aucune requête réseau.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from config.constants import (
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_LOCAL_IMAGE_FORMATS
)
from config.paths import BASE_DIR, IMAGES_DIR
from config.settings import (
    IMAGE_MAX_SIZE_BYTES,
    MIN_IMAGE_HEIGHT,
    MIN_IMAGE_PIXEL_COUNT,
    MIN_IMAGE_SIZE_BYTES,
    MIN_IMAGE_WIDTH
)
from src.article.article_utils import get_article_identifier
from src.images.image_utils import (
    ImageMetadata,
    image_matches_extension,
    inspect_image,
    is_image_large_enough
)
from src.logger import get_logger

logger = get_logger(__name__)

LOCAL_IMAGE_FORMATS = {
    **SUPPORTED_LOCAL_IMAGE_FORMATS,
    ".avif": ("AVIF",)
}
LOCAL_IMAGE_EXTENSIONS = frozenset({
    *SUPPORTED_IMAGE_EXTENSIONS,
    ".avif"
})

IMAGE_REJECTION_INVALID_ITEM = "format_article"
IMAGE_REJECTION_MISSING_PATH = "chemin_absent"
IMAGE_REJECTION_INVALID_PATH = "chemin_invalide"
IMAGE_REJECTION_OUTSIDE_DIRECTORY = "hors_dossier"
IMAGE_REJECTION_NOT_FOUND = "introuvable"
IMAGE_REJECTION_EXTENSION = "extension"
IMAGE_REJECTION_EMPTY = "vide"
IMAGE_REJECTION_TOO_LIGHT = "trop_legere"
IMAGE_REJECTION_TOO_LARGE = "trop_volumineuse"
IMAGE_REJECTION_CONTENT = "contenu"
IMAGE_REJECTION_FORMAT = "format_image"
IMAGE_REJECTION_DIMENSIONS = "dimensions"
IMAGE_REJECTION_UNEXPECTED = "erreur_inattendue"

REJECTION_LABELS = {
    IMAGE_REJECTION_INVALID_ITEM: "format article",
    IMAGE_REJECTION_MISSING_PATH: "chemin absent",
    IMAGE_REJECTION_INVALID_PATH: "chemin invalide",
    IMAGE_REJECTION_OUTSIDE_DIRECTORY: "hors dossier autorisé",
    IMAGE_REJECTION_NOT_FOUND: "fichier introuvable",
    IMAGE_REJECTION_EXTENSION: "extension",
    IMAGE_REJECTION_EMPTY: "fichier vide",
    IMAGE_REJECTION_TOO_LIGHT: "trop légère",
    IMAGE_REJECTION_TOO_LARGE: "trop volumineuse",
    IMAGE_REJECTION_CONTENT: "contenu invalide",
    IMAGE_REJECTION_FORMAT: "format réel",
    IMAGE_REJECTION_DIMENSIONS: "dimensions",
    IMAGE_REJECTION_UNEXPECTED: "erreur inattendue"
}


# Chemins

def resolve_image_path(image_path: str | Path | None) -> Path | None:
    """Résout un chemin d'image local de manière stable."""

    if not isinstance(image_path, (str, Path)):
        return None

    try:
        normalized_path = str(image_path).strip()
    except (OSError, TypeError, ValueError):
        return None

    if not normalized_path:
        return None

    try:
        path = Path(normalized_path).expanduser()
    except (OSError, TypeError, ValueError):
        return None

    candidates = (
        (path,)
        if path.is_absolute()
        else (BASE_DIR / path, IMAGES_DIR / path, path)
    )

    for candidate in candidates:
        try:
            resolved_path = candidate.resolve(strict=False)

            if resolved_path.exists():
                return resolved_path

        except (OSError, RuntimeError):
            continue

    try:
        return candidates[0].resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def get_default_allowed_directories() -> tuple[Path, ...]:
    """Retourne les dossiers d'images autorisés par défaut."""

    try:
        return (IMAGES_DIR.expanduser().resolve(strict=False),)
    except (OSError, RuntimeError):
        return (IMAGES_DIR,)


def normalize_allowed_directories(
    allowed_directories: Iterable[str | Path] | None = None
) -> tuple[Path, ...]:
    """Normalise les dossiers dans lesquels les images sont autorisées."""

    default_directories = get_default_allowed_directories()

    if allowed_directories is None:
        return default_directories

    if isinstance(allowed_directories, (str, Path)):
        directories: tuple[Any, ...] = (allowed_directories,)
    else:
        try:
            directories = tuple(allowed_directories)
        except TypeError:
            logger.debug(
                "Collection de dossiers autorisés invalide : %s.",
                type(allowed_directories).__name__
            )
            return default_directories

    normalized_directories: list[Path] = []

    for directory in directories:
        if not isinstance(directory, (str, Path)):
            logger.debug(
                "Dossier autorisé ignoré : type %s.",
                type(directory).__name__
            )
            continue

        try:
            directory_path = (
                Path(directory)
                .expanduser()
                .resolve(strict=False)
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            logger.debug(
                "Dossier autorisé impossible à normaliser %s : %s",
                directory,
                error
            )
            continue

        if directory_path not in normalized_directories:
            normalized_directories.append(directory_path)

    return tuple(normalized_directories) or default_directories


def is_path_inside_directory(file_path: Path, directory: Path) -> bool:
    """Vérifie qu'un chemin appartient à un dossier donné."""

    try:
        file_path.relative_to(directory)
        return True
    except ValueError:
        return False


def is_path_inside_allowed_directories(
    file_path: Path,
    allowed_directories: Iterable[str | Path] | None = None
) -> bool:
    """Vérifie qu'un fichier appartient à un dossier autorisé."""

    try:
        resolved_path = file_path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False

    return any(
        is_path_inside_directory(resolved_path, directory)
        for directory in normalize_allowed_directories(allowed_directories)
    )


def is_path_inside_images_directory(image_path: Path) -> bool:
    """Vérifie qu'une image appartient au dossier principal des images."""

    return is_path_inside_allowed_directories(
        image_path,
        (IMAGES_DIR,)
    )


# Métadonnées

def enrich_article_with_image_metadata(
    article: dict[str, Any],
    image_path: Path,
    metadata: ImageMetadata
) -> None:
    """Enrichit un article avec les métadonnées de son image valide."""

    metadata_dict = metadata.to_dict()

    existing_metadata = article.get("image_metadata")
    merged_metadata = (
        dict(existing_metadata)
        if isinstance(existing_metadata, dict)
        else {}
    )
    merged_metadata.update(metadata_dict)

    article["image_path"] = str(image_path)
    article["image_format"] = metadata.format
    article["image_mime_type"] = metadata.mime_type
    article["image_width"] = metadata.width
    article["image_height"] = metadata.height
    article["image_aspect_ratio"] = metadata.aspect_ratio
    article["image_mode"] = metadata.mode
    article["image_size_bytes"] = metadata.file_size
    article["image_file_hash"] = metadata.file_hash
    article["image_metadata"] = merged_metadata


# Validation détaillée

def get_file_size_rejection(image_path: Path) -> str:
    """Retourne le motif de rejet lié à la taille du fichier."""

    try:
        file_size = image_path.stat().st_size
    except OSError:
        return IMAGE_REJECTION_NOT_FOUND

    minimum_size = max(int(MIN_IMAGE_SIZE_BYTES), 0)
    maximum_size = max(int(IMAGE_MAX_SIZE_BYTES), 1)

    if file_size <= 0:
        return IMAGE_REJECTION_EMPTY
    if minimum_size and file_size < minimum_size:
        return IMAGE_REJECTION_TOO_LIGHT
    if file_size > maximum_size:
        return IMAGE_REJECTION_TOO_LARGE

    return ""


def get_image_metadata_rejection(
    image_path: Path,
    metadata: ImageMetadata
) -> str:
    """Retourne le motif de rejet lié aux métadonnées de l'image."""

    extension = image_path.suffix.strip().lower()

    if not image_matches_extension(
        metadata,
        extension,
        LOCAL_IMAGE_FORMATS
    ):
        return IMAGE_REJECTION_FORMAT

    if not is_image_large_enough(
        metadata,
        min_width=MIN_IMAGE_WIDTH,
        min_height=MIN_IMAGE_HEIGHT,
        min_pixel_count=MIN_IMAGE_PIXEL_COUNT
    ):
        return IMAGE_REJECTION_DIMENSIONS

    return ""


def inspect_valid_image(
    image_path: Path
) -> tuple[ImageMetadata | None, str]:
    """Inspecte une image et retourne ses métadonnées ou son motif de rejet."""

    size_rejection = get_file_size_rejection(image_path)

    if size_rejection:
        return None, size_rejection

    metadata = inspect_image(image_path)

    if metadata is None:
        return None, IMAGE_REJECTION_CONTENT

    metadata_rejection = get_image_metadata_rejection(
        image_path,
        metadata
    )

    if metadata_rejection:
        return None, metadata_rejection

    return metadata, ""


def get_image_content_rejection(image_path: Path) -> str:
    """Retourne le motif de rejet lié au contenu réel de l'image."""

    _, rejection = inspect_valid_image(image_path)
    return rejection


def get_image_path_details(
    image_path: str | Path,
    allowed_directories: Iterable[str | Path] | None = None
) -> tuple[Path | None, ImageMetadata | None, str]:
    """Retourne le chemin résolu, les métadonnées et le motif de rejet."""

    resolved_path = resolve_image_path(image_path)

    if resolved_path is None:
        return None, None, IMAGE_REJECTION_INVALID_PATH

    if not is_path_inside_allowed_directories(
        resolved_path,
        allowed_directories
    ):
        return resolved_path, None, IMAGE_REJECTION_OUTSIDE_DIRECTORY

    try:
        if not resolved_path.is_file():
            return resolved_path, None, IMAGE_REJECTION_NOT_FOUND
    except OSError:
        return resolved_path, None, IMAGE_REJECTION_NOT_FOUND

    extension = resolved_path.suffix.strip().lower()

    if (
        not extension
        or extension not in LOCAL_IMAGE_EXTENSIONS
        or extension not in LOCAL_IMAGE_FORMATS
    ):
        return resolved_path, None, IMAGE_REJECTION_EXTENSION

    metadata, rejection = inspect_valid_image(resolved_path)
    return resolved_path, metadata, rejection


def get_image_path_rejection(
    image_path: str | Path,
    allowed_directories: Iterable[str | Path] | None = None
) -> str:
    """Retourne le motif précis de rejet d'un chemin d'image."""

    _, _, rejection = get_image_path_details(
        image_path,
        allowed_directories
    )
    return rejection


def get_article_image_rejection(
    article: dict[str, Any],
    allowed_directories: Iterable[str | Path] | None = None
) -> str:
    """Retourne le motif précis de rejet de l'image d'un article."""

    if not isinstance(article, dict):
        return IMAGE_REJECTION_INVALID_ITEM

    image_path = article.get("image_path")

    if not isinstance(image_path, (str, Path)):
        return IMAGE_REJECTION_INVALID_PATH

    try:
        if not str(image_path).strip():
            return IMAGE_REJECTION_MISSING_PATH
    except (OSError, TypeError, ValueError):
        return IMAGE_REJECTION_INVALID_PATH

    return get_image_path_rejection(
        image_path,
        allowed_directories
    )


# API booléenne

def has_allowed_extension(image_path: Path) -> bool:
    """Vérifie que l'extension du fichier est autorisée."""

    if not isinstance(image_path, Path):
        return False

    extension = image_path.suffix.strip().lower()

    return (
        bool(extension)
        and extension in LOCAL_IMAGE_EXTENSIONS
        and extension in LOCAL_IMAGE_FORMATS
    )


def has_valid_file_size(image_path: Path) -> bool:
    """Vérifie que le fichier respecte les limites de taille."""

    return (
        isinstance(image_path, Path)
        and not get_file_size_rejection(image_path)
    )


def validate_image_content(image_path: Path) -> bool:
    """Vérifie le format réel et les métadonnées d'une image locale."""

    return (
        isinstance(image_path, Path)
        and not get_image_content_rejection(image_path)
    )


def is_valid_image_path(
    image_path: str | Path,
    allowed_directories: Iterable[str | Path] | None = None
) -> bool:
    """Vérifie qu'un chemin correspond à une image locale valide."""

    return not get_image_path_rejection(
        image_path,
        allowed_directories
    )


def validate_article_image(
    article: dict[str, Any],
    allowed_directories: Iterable[str | Path] | None = None
) -> bool:
    """Valide l'image d'un article et l'enrichit avec ses métadonnées."""

    if not isinstance(article, dict):
        return False

    image_path = article.get("image_path")
    resolved_path: Path | None = None
    metadata: ImageMetadata | None = None

    if not isinstance(image_path, (str, Path)):
        rejection = IMAGE_REJECTION_INVALID_PATH
    else:
        try:
            normalized_path = str(image_path).strip()
        except (OSError, TypeError, ValueError):
            normalized_path = ""

        if not normalized_path:
            rejection = IMAGE_REJECTION_MISSING_PATH
        else:
            resolved_path, metadata, rejection = get_image_path_details(
                normalized_path,
                allowed_directories
            )

    if rejection:
        logger.debug(
            "Image rejetée pour l'article [%s] : %s.",
            get_article_identifier(article),
            REJECTION_LABELS.get(rejection, rejection)
        )
        return False

    if resolved_path is None or metadata is None:
        return False

    enrich_article_with_image_metadata(
        article,
        resolved_path,
        metadata
    )
    return True


# Résumé

def format_rejection_summary(rejections: Counter[str]) -> str:
    """Construit le résumé compact des motifs de rejet."""

    return ", ".join(
        f"{REJECTION_LABELS.get(reason, reason)}={count}"
        for reason, count in rejections.most_common()
    ) or "aucun"


def validate_images_for_articles(
    articles: list[Any],
    allowed_directories: Iterable[str | Path] | None = None
) -> list[dict[str, Any]]:
    """Conserve les articles avec image valide et agrège les motifs de rejet."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide pour la validation des images : %s.",
            type(articles).__name__
        )
        return []

    if not articles:
        logger.info("Validation images : aucun article à évaluer.")
        return []

    valid_articles: list[dict[str, Any]] = []
    rejections: Counter[str] = Counter()

    for article in articles:
        if not isinstance(article, dict):
            rejections[IMAGE_REJECTION_INVALID_ITEM] += 1
            continue

        validated_article = article.copy()

        try:
            image_path = validated_article.get("image_path")

            if not isinstance(image_path, (str, Path)):
                rejections[IMAGE_REJECTION_INVALID_PATH] += 1
                continue

            try:
                normalized_path = str(image_path).strip()
            except (OSError, TypeError, ValueError):
                rejections[IMAGE_REJECTION_INVALID_PATH] += 1
                continue

            if not normalized_path:
                rejections[IMAGE_REJECTION_MISSING_PATH] += 1
                continue

            resolved_path, metadata, rejection = get_image_path_details(
                normalized_path,
                allowed_directories
            )

            if rejection:
                rejections[rejection] += 1
                continue

            if resolved_path is None or metadata is None:
                rejections[IMAGE_REJECTION_CONTENT] += 1
                continue

            enrich_article_with_image_metadata(
                validated_article,
                resolved_path,
                metadata
            )
            valid_articles.append(validated_article)

        except Exception as error:
            rejections[IMAGE_REJECTION_UNEXPECTED] += 1
            logger.debug(
                "Erreur inattendue pendant la validation de [%s] : %s",
                get_article_identifier(validated_article),
                error,
                exc_info=True
            )

    logger.info(
        "Validation images : analysées=%s, valides=%s, rejetées=%s, "
        "motifs=[%s].",
        len(articles),
        len(valid_articles),
        sum(rejections.values()),
        format_rejection_summary(rejections)
    )

    return valid_articles