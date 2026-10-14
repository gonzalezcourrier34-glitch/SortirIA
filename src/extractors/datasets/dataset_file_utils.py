"""Utilitaires de fichiers pour les extracteurs de datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/datasets/dataset_file_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from config.paths import BASE_DIR
from src.extractors.datasets.dataset_adapter import DatasetAdapter
from src.logger import get_logger
from src.utils.extractor_utils import normalize_value

logger = get_logger(__name__)


# DataFrame

def normalize_dataframe_columns(dataframe: Any) -> Any:
    """Normalise les noms de colonnes sans modifier le DataFrame original."""

    if (
        dataframe is None
        or not hasattr(dataframe, "columns")
        or not hasattr(dataframe, "copy")
    ):
        raise TypeError("Un DataFrame valide est attendu.")

    normalized = dataframe.copy()
    normalized.columns = [
        str(column).strip().lower()
        for column in dataframe.columns
    ]
    return normalized


# Dossiers

def resolve_dataset_directory(
    source: Mapping[str, Any],
    source_name: str,
) -> Path:
    """Résout et vérifie le dossier racine du dataset."""

    raw_path = normalize_value(source.get("path"))
    if not raw_path:
        raise ValueError(
            f"Aucun chemin configuré pour le dataset {source_name}."
        )

    try:
        directory = Path(raw_path).expanduser()
        if not directory.is_absolute():
            directory = BASE_DIR / directory
        directory = directory.resolve(strict=False)
    except (TypeError, ValueError, OSError, RuntimeError) as error:
        raise ValueError(
            f"Impossible de résoudre le chemin du dataset "
            f"{source_name} : {error}"
        ) from error

    if not directory.exists():
        raise FileNotFoundError(
            f"Dossier du dataset {source_name} introuvable : {directory}"
        )
    if not directory.is_dir():
        raise NotADirectoryError(
            f"Le chemin configuré pour {source_name} "
            f"n'est pas un dossier : {directory}"
        )

    return directory


def is_path_inside_directory(
    file_path: Path,
    directory: Path,
) -> bool:
    """Vérifie qu'un chemin appartient au dossier du dataset."""

    try:
        file_path.resolve(strict=False).relative_to(
            directory.resolve(strict=False)
        )
        return True
    except (ValueError, OSError, RuntimeError):
        return False


# Fichiers configurés

def resolve_configured_dataset_file(
    dataset_directory: Path,
    configured_filename: Any,
    supported_extensions: frozenset[str],
    source_name: str,
) -> Path | None:
    """Résout et contrôle un fichier configuré dans le dossier du dataset."""

    filename = normalize_value(configured_filename)
    if not filename:
        return None

    try:
        directory = dataset_directory.resolve(strict=False)
        candidate = (directory / filename).resolve(strict=False)
    except (TypeError, ValueError, OSError, RuntimeError) as error:
        logger.error(
            "Impossible de résoudre le fichier configuré pour %s : %s",
            source_name,
            error,
        )
        return None

    if not is_path_inside_directory(candidate, directory):
        logger.error(
            "Le fichier configuré sort du dossier de %s : %s",
            source_name,
            filename,
        )
        return None

    if candidate.suffix.lower() not in supported_extensions:
        logger.error(
            "Extension non prise en charge pour %s : %s",
            source_name,
            candidate.suffix.lower() or "sans extension",
        )
        return None

    if not candidate.is_file():
        logger.error(
            "Fichier configuré introuvable pour %s : %s",
            source_name,
            candidate,
        )
        return None

    return candidate


# Validation

def validate_dataset_files(
    files: Iterable[Path],
    dataset_directory: Path,
    supported_extensions: frozenset[str],
    source_name: str,
) -> list[Path]:
    """Conserve uniquement les fichiers valides et autorisés."""

    valid_files: list[Path] = []
    seen_files: set[Path] = set()

    for file_path in files:
        if not isinstance(file_path, Path):
            logger.warning(
                "Chemin invalide ignoré pour %s : %s.",
                source_name,
                type(file_path).__name__,
            )
            continue

        try:
            resolved_path = file_path.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            logger.warning(
                "Chemin impossible à résoudre pour %s : %s (%s)",
                source_name,
                file_path,
                error,
            )
            continue

        if not is_path_inside_directory(
            resolved_path,
            dataset_directory,
        ):
            logger.warning(
                "Fichier situé hors du dossier de %s : %s",
                source_name,
                resolved_path,
            )
            continue

        if not resolved_path.is_file():
            logger.warning(
                "Fichier absent ou invalide pour %s : %s",
                source_name,
                resolved_path,
            )
            continue

        if resolved_path.suffix.lower() not in supported_extensions:
            logger.warning(
                "Extension non prise en charge pour %s : %s",
                source_name,
                resolved_path.suffix.lower() or "sans extension",
            )
            continue

        if resolved_path in seen_files:
            logger.debug(
                "Fichier déjà sélectionné pour %s : %s",
                source_name,
                resolved_path,
            )
            continue

        seen_files.add(resolved_path)
        valid_files.append(resolved_path)

    return valid_files


# Découverte

def get_dataset_files(
    adapter: DatasetAdapter,
    dataset_directory: Path,
    source: Mapping[str, Any],
    source_name: str,
) -> list[Path]:
    """Recherche puis valide les fichiers fournis par un adaptateur."""

    discovered_files = adapter.find_files(dataset_directory, source)
    if not isinstance(discovered_files, list):
        raise TypeError(
            f"La recherche de fichiers de {source_name} "
            f"doit retourner une liste."
        )

    dataset_files = validate_dataset_files(
        discovered_files,
        dataset_directory,
        adapter.supported_extensions,
        source_name,
    )
    if not dataset_files:
        raise FileNotFoundError(
            f"Aucun fichier exploitable trouvé pour {source_name}."
        )

    return dataset_files