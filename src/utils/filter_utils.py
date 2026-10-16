"""Fonctions utilitaires liées aux filtres d'extraction.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/filter_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.paths import SOURCES_FILE
from config.settings import (
    MIN_TEXT_LENGTH,
    MIN_TITLE_LENGTH,
    MIN_TOTAL_TEXT_LENGTH
)
from src.article.processing.article_cleaner import normalize_value
from src.logger import get_logger
from src.utils.parsing_utils import (
    parse_boolean,
    parse_non_negative_integer
)
from src.utils.yaml_utils import load_yaml_file

logger = get_logger(__name__)


# Profils de filtres

ACQUISITION_FILTER_SECTION = "acquisition_filters"
DATASET_FILTER_SECTION = "dataset_filters"

FALLBACK_FILTERS: dict[str, dict[str, Any]] = {
    ACQUISITION_FILTER_SECTION: {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_url": True,
        "min_title_length": MIN_TITLE_LENGTH,
        "min_text_length": MIN_TEXT_LENGTH,
        "min_total_text_length": MIN_TOTAL_TEXT_LENGTH,
        "remove_deleted_content": True,
        "remove_duplicates": True,
        "validate_urls": True
    },
    DATASET_FILTER_SECTION: {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_url": False,
        "min_title_length": MIN_TITLE_LENGTH,
        "min_text_length": MIN_TEXT_LENGTH,
        "min_total_text_length": MIN_TOTAL_TEXT_LENGTH,
        "remove_deleted_content": True,
        "remove_duplicates": True,
        "validate_urls": True
    }
}

BOOLEAN_FILTER_FIELDS: tuple[str, ...] = (
    "require_title",
    "require_text",
    "require_image",
    "require_url",
    "remove_deleted_content",
    "remove_duplicates",
    "validate_urls"
)

INTEGER_FILTER_FIELDS: dict[str, int] = {
    "min_title_length": MIN_TITLE_LENGTH,
    "min_text_length": MIN_TEXT_LENGTH,
    "min_total_text_length": MIN_TOTAL_TEXT_LENGTH
}

DATASET_FILTER_ROLES: frozenset[str] = frozenset({
    "dataset"
})


# Sélection du profil

def get_default_filter_section(
    source: Mapping[str, Any] | None
) -> str:
    """Retourne la section de filtres adaptée à une source."""

    if not isinstance(source, Mapping):
        return ACQUISITION_FILTER_SECTION

    source_type = normalize_value(source.get("type")).casefold()
    role = normalize_value(
        source.get("role") or source.get("dataset_role")
    ).casefold()

    return (
        DATASET_FILTER_SECTION
        if source_type == "dataset" or role in DATASET_FILTER_ROLES
        else ACQUISITION_FILTER_SECTION
    )


def get_fallback_filters(
    source: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Retourne une copie du profil Python adapté à la source."""

    return FALLBACK_FILTERS[get_default_filter_section(source)].copy()


# Chargement YAML

def load_default_filters(
    source: Mapping[str, Any] | None,
    file_path: str | Path = SOURCES_FILE
) -> dict[str, Any]:
    """Charge les filtres par défaut adaptés depuis sources.yaml."""

    section = get_default_filter_section(source)

    try:
        configuration = load_yaml_file(file_path)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        logger.warning(
            "Impossible de charger les filtres depuis %s : %s",
            file_path,
            error
        )
        return {}

    if not isinstance(configuration, Mapping):
        logger.error(
            "Le fichier %s doit contenir un dictionnaire YAML.",
            file_path
        )
        return {}

    filters = configuration.get(section, {})

    if not isinstance(filters, Mapping):
        logger.error(
            "La section '%s' de %s doit être un dictionnaire.",
            section,
            file_path
        )
        return {}

    return dict(filters)


# Normalisation

def normalize_filter_configuration(
    filters: Mapping[str, Any],
    fallback_filters: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalise les valeurs d'une configuration de filtres."""

    normalized_filters = dict(filters)

    for field in BOOLEAN_FILTER_FIELDS:
        normalized_filters[field] = parse_boolean(
            normalized_filters.get(field),
            bool(fallback_filters.get(field, False))
        )

    for field, default in INTEGER_FILTER_FIELDS.items():
        normalized_filters[field] = parse_non_negative_integer(
            normalized_filters.get(field),
            default
        )

    return normalized_filters


# Configuration publique

def get_filter_configuration(
    source: Mapping[str, Any] | None,
    default_filters: Mapping[str, Any] | None = None,
    defaults_file: str | Path = SOURCES_FILE
) -> dict[str, Any]:
    """Fusionne et normalise les filtres applicables à une source."""

    if not isinstance(source, Mapping):
        if source is not None:
            logger.warning(
                "Configuration de source invalide : %s.",
                type(source).__name__
            )
        source = {}

    # Priorité : repli Python < sources.yaml < filtres transmis < source.
    fallback_filters = get_fallback_filters(source)
    filters = fallback_filters.copy()
    filters.update(load_default_filters(source, defaults_file))

    if isinstance(default_filters, Mapping):
        filters.update(default_filters)
    elif default_filters is not None:
        logger.warning(
            "Les filtres par défaut transmis doivent être un dictionnaire."
        )

    source_filters = source.get("filters", {})

    if isinstance(source_filters, Mapping):
        filters.update(source_filters)
    elif source_filters is not None:
        logger.warning(
            "Les filtres de la source %s doivent être un dictionnaire.",
            source.get("name") or source.get("source_id") or "inconnue"
        )

    return normalize_filter_configuration(filters, fallback_filters)