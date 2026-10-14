"""Calcul des variables dérivées des articles CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/transformers/features_generator.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.article.processing.article_cleaner import normalize_value
from src.images.image_utils import ImageMetadata, inspect_image
from src.images.image_validator import resolve_image_path
from src.logger import get_logger
from src.utils.date_utils import parse_datetime
from src.utils.parsing_utils import (
    parse_optional_float,
    parse_optional_integer
)

logger = get_logger(__name__)


# Texte

def generate_text_features(article: Mapping[str, Any]) -> dict[str, Any]:
    """Calcule les variables dérivées du titre et du contenu."""

    title = normalize_value(article.get("title"))
    text = normalize_value(
        article.get("text")
        or article.get("content")
        or article.get("summary")
        or article.get("description")
    )

    title_word_count = len(title.split()) if title else 0
    text_word_count = len(text.split()) if text else 0

    return {
        "has_title": bool(title),
        "has_text": bool(text),
        "title_length": len(title),
        "text_length": len(text),
        "total_text_length": len(title) + len(text),
        "title_word_count": title_word_count,
        "text_word_count": text_word_count,
        "total_word_count": title_word_count + text_word_count
    }


# Date

def generate_temporal_features(
    article: Mapping[str, Any]
) -> dict[str, Any]:
    """Calcule les variables temporelles de publication."""

    published_at = parse_datetime(article.get("published_at"))

    if published_at is None:
        return {
            "has_publication_date": False,
            "publication_year": None,
            "publication_month": None,
            "publication_day": None,
            "publication_hour": None,
            "publication_weekday": None
        }

    return {
        "has_publication_date": True,
        "publication_year": published_at.year,
        "publication_month": published_at.month,
        "publication_day": published_at.day,
        "publication_hour": published_at.hour,
        "publication_weekday": published_at.weekday()
    }


# Métadonnées des images

def get_stored_image_metadata(
    article: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Retourne les métadonnées d'image déjà présentes dans l'article."""

    metadata = article.get("image_metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def get_first_available_value(*values: Any) -> Any:
    """Retourne la première valeur définie."""

    return next(
        (
            value
            for value in values
            if value is not None and value != ""
        ),
        None
    )


def apply_stored_image_metadata(
    features: dict[str, Any],
    article: Mapping[str, Any]
) -> None:
    """Réutilise les métadonnées calculées pendant le téléchargement."""

    metadata = get_stored_image_metadata(article)

    width = get_first_available_value(
        article.get("image_width"),
        metadata.get("width")
    )
    height = get_first_available_value(
        article.get("image_height"),
        metadata.get("height")
    )
    aspect_ratio = get_first_available_value(
        article.get("image_aspect_ratio"),
        metadata.get("aspect_ratio")
    )
    size_bytes = get_first_available_value(
        article.get("image_size_bytes"),
        metadata.get("file_size"),
        metadata.get("size_bytes")
    )

    features.update({
        "image_width": parse_optional_integer(width),
        "image_height": parse_optional_integer(height),
        "image_aspect_ratio": parse_optional_float(aspect_ratio),
        "image_format": normalize_value(
            get_first_available_value(
                article.get("image_format"),
                metadata.get("format")
            )
        ),
        "image_mime_type": normalize_value(
            get_first_available_value(
                article.get("image_mime_type"),
                metadata.get("mime_type")
            )
        ),
        "image_size_bytes": parse_optional_integer(size_bytes),
        "image_file_hash": normalize_value(
            get_first_available_value(
                article.get("image_file_hash"),
                metadata.get("file_hash")
            )
        )
    })


def apply_inspected_image_metadata(
    features: dict[str, Any],
    metadata: ImageMetadata
) -> None:
    """Ajoute les métadonnées issues de l'inspection du fichier."""

    features.update({
        "image_width": metadata.width,
        "image_height": metadata.height,
        "image_aspect_ratio": metadata.aspect_ratio,
        "image_format": metadata.format,
        "image_mime_type": metadata.mime_type,
        "image_size_bytes": metadata.file_size,
        "image_file_hash": metadata.file_hash
    })


def has_complete_image_metadata(features: Mapping[str, Any]) -> bool:
    """Indique si les principales métadonnées sont déjà disponibles."""

    return all((
        features.get("image_width") is not None,
        features.get("image_height") is not None,
        bool(features.get("image_format")),
        features.get("image_size_bytes") is not None
    ))


# Association article-image

def validate_text_image_association(
    article_id: Any,
    image_path: Path | None
) -> bool:
    """Vérifie l'association technique entre un article et son image."""

    normalized_article_id = normalize_value(article_id).lower()

    return bool(
        image_path
        and normalized_article_id
        and normalized_article_id in image_path.stem.lower()
    )


# Image

def generate_image_features(
    article: Mapping[str, Any]
) -> dict[str, Any]:
    """Calcule les variables dérivées de l'image d'un article."""

    raw_path = article.get("image_path")
    image_path = resolve_image_path(raw_path)

    features: dict[str, Any] = {
        "has_image_url": bool(normalize_value(article.get("image_url"))),
        "has_image_path": bool(normalize_value(raw_path)),
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

    apply_stored_image_metadata(features, article)

    if image_path is None or not image_path.is_file():
        return features

    features["image_exists"] = True
    features["text_image_association_valid"] = (
        validate_text_image_association(
            article.get("id"),
            image_path
        )
    )

    existing_validation = article.get("image_is_valid")
    metadata_complete = has_complete_image_metadata(features)

    # Une nouvelle inspection est nécessaire si la validation ou les
    # principales métadonnées ne sont pas encore disponibles.
    inspection_required = (
        not isinstance(existing_validation, bool)
        or not metadata_complete
    )

    metadata = inspect_image(image_path) if inspection_required else None

    if metadata is not None:
        apply_inspected_image_metadata(features, metadata)

    if isinstance(existing_validation, bool):
        features["image_is_valid"] = existing_validation
    else:
        features["image_is_valid"] = metadata is not None

    # La taille réelle est récupérée si elle reste absente des métadonnées.
    if features["image_size_bytes"] is None:
        try:
            features["image_size_bytes"] = image_path.stat().st_size
        except OSError as error:
            logger.warning(
                "Taille d'image illisible %s : %s",
                image_path,
                error
            )

    return features


# Métier

def generate_business_features(
    article: Mapping[str, Any]
) -> dict[str, Any]:
    """Calcule les indicateurs métier de l'article."""

    title = normalize_value(article.get("title"))
    text = normalize_value(
        article.get("text")
        or article.get("content")
        or article.get("summary")
        or article.get("description")
    )
    label = normalize_value(article.get("label"))

    return {
        "has_url": bool(normalize_value(article.get("url"))),
        "has_author": bool(normalize_value(article.get("author"))),
        "has_label": bool(label),
        "is_labeled": bool(label),
        "is_multimodal": bool(
            (title or text)
            and article.get("image_is_valid") is True
        )
    }


# Enrichissement

def enrich_article(
    article: Mapping[str, Any],
    transformation_date: str,
    transformation_version: str
) -> dict[str, Any]:
    """Enrichit un article avec toutes ses variables dérivées."""

    if not isinstance(article, Mapping):
        return {}

    enriched = dict(article)

    enriched.update(generate_text_features(enriched))
    enriched.update(generate_temporal_features(enriched))
    enriched.update(generate_image_features(enriched))
    enriched.update(generate_business_features(enriched))

    enriched["transformation_date"] = transformation_date
    enriched["transformation_version"] = transformation_version

    return enriched


def enrich_articles(
    articles: list[Any],
    transformation_date: str,
    transformation_version: str
) -> list[dict[str, Any]]:
    """Enrichit une liste d'articles."""

    if not articles:
        logger.warning("Aucun article à enrichir.")
        return []

    enriched_articles = [
        enriched_article
        for article in articles
        if (
            enriched_article := enrich_article(
                article,
                transformation_date,
                transformation_version
            )
        )
    ]

    ignored_count = len(articles) - len(enriched_articles)

    logger.info(
        "%s article(s) enrichi(s).",
        len(enriched_articles)
    )

    if ignored_count:
        logger.warning(
            "%s élément(s) ignoré(s).",
            ignored_count
        )

    return enriched_articles