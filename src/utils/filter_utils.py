"""Fonctions utilitaires liées aux filtres d'extraction.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/filter_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.paths import SOURCES_FILE
from config.settings import MIN_TEXT_LENGTH, MIN_TITLE_LENGTH, MIN_TOTAL_TEXT_LENGTH
from src.article.processing.article_cleaner import normalize_value
from src.logger import get_logger
from src.utils.parsing_utils import parse_boolean, parse_non_negative_integer
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
    """Retourne la section de filtres adaptée à une source.

    Une source de type « dataset » ou de rôle « dataset » reçoit le profil
    jeu de données ; toutes les autres, le profil d'acquisition.

    Parameters
    ----------
    source : Mapping[str, Any] | None
        La configuration de la source (champs `type`, `role`, `dataset_role`).

    Returns
    -------
    str
        `"dataset_filters"` ou `"acquisition_filters"`.
    """

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
    """Retourne une copie du profil Python adapté à la source.

    Parameters
    ----------
    source : Mapping[str, Any] | None
        La configuration de la source.

    Returns
    -------
    dict[str, Any]
        Une copie du profil de repli défini dans `FALLBACK_FILTERS`.
    """

    return FALLBACK_FILTERS[get_default_filter_section(source)].copy()


# Chargement YAML

def load_default_filters(
    source: Mapping[str, Any] | None,
    file_path: str | Path = SOURCES_FILE
) -> dict[str, Any]:
    """Charge les filtres par défaut adaptés depuis sources.yaml.

    Toute erreur de lecture ou de structure est journalisée et donne un
    dictionnaire vide : le profil Python de repli prend alors le relais.

    Parameters
    ----------
    source : Mapping[str, Any] | None
        La configuration de la source, qui choisit la section à lire.
    file_path : str | Path
        Le fichier YAML à lire. Par défaut `SOURCES_FILE`.

    Returns
    -------
    dict[str, Any]
        Les filtres de la section, ou {} si le fichier est illisible ou mal formé.
    """

    # Choisir la section puis lire le fichier YAML
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

    # Vérifier que le fichier et la section sont bien des dictionnaires
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
    """Normalise les valeurs d'une configuration de filtres.

    Parameters
    ----------
    filters : Mapping[str, Any]
        Les filtres fusionnés, dont les valeurs peuvent être des chaînes.
    fallback_filters : Mapping[str, Any]
        Le profil de repli, qui fournit la valeur des booléens illisibles.

    Returns
    -------
    dict[str, Any]
        Une copie des filtres avec des booléens et des entiers positifs fiables.
    """

    normalized_filters = dict(filters)

    # Convertir les drapeaux en booléens, puis les longueurs en entiers positifs
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
    """Fusionne et normalise les filtres applicables à une source.

    Ordre de priorité, du plus faible au plus fort : profil Python de repli,
    sources.yaml, filtres transmis, puis filtres propres à la source.

    Parameters
    ----------
    source : Mapping[str, Any] | None
        La configuration de la source ; invalide, elle est remplacée par {}.
    default_filters : Mapping[str, Any] | None
        Filtres transmis par l'appelant. Par défaut aucun.
    defaults_file : str | Path
        Le fichier YAML des filtres par défaut. Par défaut `SOURCES_FILE`.

    Returns
    -------
    dict[str, Any]
        Les filtres fusionnés et normalisés.
    """

    # Remplacer une configuration de source invalide par un dictionnaire vide
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

    # Appliquer en dernier les filtres propres à la source, puis normaliser
    source_filters = source.get("filters", {})

    if isinstance(source_filters, Mapping):
        filters.update(source_filters)
    elif source_filters is not None:
        logger.warning(
            "Les filtres de la source %s doivent être un dictionnaire.",
            source.get("name") or source.get("source_id") or "inconnue"
        )

    return normalize_filter_configuration(filters, fallback_filters)