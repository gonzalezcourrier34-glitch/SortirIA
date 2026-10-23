"""Utilitaires de fichiers pour les extracteurs de datasets CheckIt.AI.

Résout le dossier d'un dataset, contrôle que chaque fichier y reste confiné et
porte une extension acceptée, et normalise les colonnes d'un DataFrame.

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
    """Normalise les noms de colonnes sans modifier le DataFrame original.

    Parameters
    ----------
    dataframe : Any
        Un DataFrame (tout objet doté de `columns` et `copy`).

    Returns
    -------
    Any
        Une copie dont les noms de colonnes sont en minuscules, sans espaces autour.

    Raises
    ------
    TypeError
        Si l'objet n'a pas l'allure d'un DataFrame.
    """

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
    """Résout et vérifie le dossier racine du dataset.

    Un chemin relatif est rattaché à la racine du projet (`BASE_DIR`).

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (clé `path`).
    source_name : str
        Nom lisible de la source, pour les messages d'erreur.

    Returns
    -------
    Path
        Le chemin absolu du dossier.

    Raises
    ------
    ValueError
        Si aucun chemin n'est configuré ou s'il ne peut pas être résolu.
    FileNotFoundError
        Si le dossier n'existe pas.
    NotADirectoryError
        Si le chemin désigne autre chose qu'un dossier.
    """

    # Lire le chemin configuré
    raw_path = normalize_value(source.get("path"))
    if not raw_path:
        raise ValueError(
            f"Aucun chemin configuré pour le dataset {source_name}."
        )

    # Le rendre absolu par rapport à la racine du projet
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

    # Vérifier qu'il s'agit bien d'un dossier existant
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
    """Vérifie qu'un chemin appartient au dossier du dataset.

    Parameters
    ----------
    file_path : Path
        Chemin à contrôler.
    directory : Path
        Dossier qui doit le contenir.

    Returns
    -------
    bool
        True si le chemin résolu est dans le dossier (protège des `../`).
    """

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
    """Résout et contrôle un fichier configuré dans le dossier du dataset.

    Chaque refus est journalisé en erreur.

    Parameters
    ----------
    dataset_directory : Path
        Dossier du dataset.
    configured_filename : Any
        Nom de fichier lu dans la configuration, relatif au dossier.
    supported_extensions : frozenset[str]
        Extensions acceptées (minuscules, avec le point).
    source_name : str
        Nom lisible de la source, pour les journaux.

    Returns
    -------
    Path | None
        Le chemin du fichier, ou None s'il est absent, vide, hors du dossier,
        d'extension refusée ou introuvable.
    """

    # Résoudre le nom configuré dans le dossier du dataset
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

    # Refuser un fichier hors du dossier, d'extension non prise en charge ou absent
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
    """Conserve uniquement les fichiers valides et autorisés.

    Chaque fichier écarté est journalisé avec sa raison.

    Parameters
    ----------
    files : Iterable[Path]
        Fichiers proposés par l'adaptateur.
    dataset_directory : Path
        Dossier qui doit contenir les fichiers.
    supported_extensions : frozenset[str]
        Extensions acceptées (minuscules, avec le point).
    source_name : str
        Nom lisible de la source, pour les journaux.

    Returns
    -------
    list[Path]
        Les chemins résolus, existants, confinés au dossier, d'extension
        acceptée et sans doublon, dans l'ordre reçu.
    """

    valid_files: list[Path] = []
    seen_files: set[Path] = set()

    for file_path in files:
        # Écarter ce qui n'est pas un chemin ou ne peut pas être résolu
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

        # Écarter les fichiers hors du dossier, absents ou d'extension refusée
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

        # Garder chaque fichier une seule fois
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
    """Recherche puis valide les fichiers fournis par un adaptateur.

    Parameters
    ----------
    adapter : DatasetAdapter
        L'adaptateur, qui fournit la recherche et les extensions acceptées.
    dataset_directory : Path
        Dossier du dataset.
    source : Mapping[str, Any]
        Configuration de la source.
    source_name : str
        Nom lisible de la source, pour les messages.

    Returns
    -------
    list[Path]
        Les fichiers exploitables.

    Raises
    ------
    TypeError
        Si la recherche ne renvoie pas une liste.
    FileNotFoundError
        Si aucun fichier exploitable n'est trouvé.
    """

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