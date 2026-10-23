"""
Fonctions utilitaires liées aux chemins et noms de fichiers.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/path_utils.py` (commit 93632c5).

Ce module normalise des valeurs destinées à devenir des noms de sources,
de dossiers, de fichiers ou des composants sûrs d'un chemin.

Il ne crée, ne déplace et ne supprime aucun fichier ou dossier.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath
from typing import Any

from src.utils.parsing_utils import parse_positive_integer


# Valeurs par défaut

DEFAULT_SOURCE_NAME = "unknown"
DEFAULT_FILENAME = "file"

MAX_SOURCE_NAME_LENGTH = 120
MAX_FILENAME_LENGTH = 255


# Contraintes des systèmes de fichiers

WINDOWS_RESERVED_FILENAMES: frozenset[str] = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10))
})

INVALID_SOURCE_PATTERN = re.compile(r"[^\w-]+", flags=re.UNICODE)
INVALID_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WHITESPACE_PATTERN = re.compile(r"\s+")
MULTIPLE_UNDERSCORES_PATTERN = re.compile(r"_+")
MULTIPLE_HYPHENS_PATTERN = re.compile(r"-+")


# Normalisation commune

def normalize_unicode(value: Any) -> str:
    """Convertit une valeur en texte Unicode normalisé NFC.

    Parameters
    ----------
    value : Any
        La valeur à convertir ; None donne une chaîne vide.

    Returns
    -------
    str
        Le texte en forme NFC (accents composés).
    """

    if value is None:
        return ""

    return unicodedata.normalize("NFC", str(value))


def collapse_separators(value: str) -> str:
    """Réduit les suites de soulignements et de tirets.

    Parameters
    ----------
    value : str
        Le texte à simplifier.

    Returns
    -------
    str
        Le texte où « ___ » devient « _ » et « --- » devient « - ».
    """

    value = MULTIPLE_UNDERSCORES_PATTERN.sub("_", value)
    return MULTIPLE_HYPHENS_PATTERN.sub("-", value)


def clean_path_component(value: str) -> str:
    """Supprime les extrémités interdites ou dangereuses.

    Parameters
    ----------
    value : str
        Le composant de chemin à nettoyer.

    Returns
    -------
    str
        Le composant sans espaces, points, soulignements ni tirets aux
        extrémités ; vide s'il ne restait que « . » ou « .. ».
    """

    cleaned_value = value.strip(" ._-")
    return "" if cleaned_value in {"", ".", ".."} else cleaned_value


def protect_windows_reserved_name(name: str, prefix: str) -> str:
    """Préfixe un nom réservé par Windows.

    Les noms comme « con » ou « com1 », même suivis d'une extension, sont
    interdits sous Windows : ils reçoivent un préfixe.

    Parameters
    ----------
    name : str
        Le nom à protéger.
    prefix : str
        Le préfixe ajouté devant un nom réservé, séparé par « _ ».

    Returns
    -------
    str
        Le nom inchangé, ou préfixé s'il est réservé ; vide si `name` est vide.
    """

    normalized_name = normalize_unicode(name).strip()

    if not normalized_name:
        return ""

    base_name = normalized_name.split(".", maxsplit=1)[0]
    base_name = base_name.rstrip(" .").casefold()

    return (
        f"{prefix}_{normalized_name}"
        if base_name in WINDOWS_RESERVED_FILENAMES
        else normalized_name
    )


# Limitation des longueurs

def truncate_name(name: str, max_length: int) -> str:
    """Tronque un nom à la longueur maximale autorisée.

    Parameters
    ----------
    name : str
        Le nom à tronquer.
    max_length : int
        La longueur maximale ; une valeur invalide vaut 1.

    Returns
    -------
    str
        Le nom limité à `max_length` caractères.
    """

    safe_max_length = parse_positive_integer(max_length, default=1)
    return name[:safe_max_length]


def truncate_filename(filename: str, max_length: int) -> str:
    """Tronque un nom de fichier en préservant son extension.

    Si l'extension est absente ou aussi longue que la limite, le nom est
    simplement coupé.

    Parameters
    ----------
    filename : str
        Le nom de fichier à tronquer.
    max_length : int
        La longueur maximale ; une valeur invalide vaut 1.

    Returns
    -------
    str
        Le nom de fichier d'au plus `max_length` caractères.
    """

    safe_max_length = parse_positive_integer(max_length, default=1)

    if len(filename) <= safe_max_length:
        return filename

    suffix = PurePath(filename).suffix

    if not suffix or len(suffix) >= safe_max_length:
        return filename[:safe_max_length]

    stem_length = safe_max_length - len(suffix)
    truncated_stem = filename[:-len(suffix)][:stem_length]

    return f"{truncated_stem}{suffix}"


# Normalisation des sources

def normalize_source_name(
    source: Any,
    max_length: int = MAX_SOURCE_NAME_LENGTH
) -> str:
    """Transforme une source en nom de dossier sûr et stable.

    Parameters
    ----------
    source : Any
        Le nom de la source.
    max_length : int
        La longueur maximale du résultat. Par défaut 120.

    Returns
    -------
    str
        Un nom en minuscules, fait de lettres, chiffres, « _ » et « - » ;
        « unknown » si rien d'utilisable ne reste.
    """

    # Mettre en minuscules et remplacer les caractères interdits
    normalized_source = normalize_unicode(source).strip().casefold()

    if not normalized_source:
        return DEFAULT_SOURCE_NAME

    normalized_source = INVALID_SOURCE_PATTERN.sub("_", normalized_source)
    normalized_source = collapse_separators(normalized_source)
    normalized_source = clean_path_component(normalized_source)

    if not normalized_source:
        return DEFAULT_SOURCE_NAME

    # Éviter les noms réservés par Windows, puis limiter la longueur
    normalized_source = protect_windows_reserved_name(
        normalized_source,
        prefix="source"
    )
    normalized_source = truncate_name(normalized_source, max_length)
    normalized_source = clean_path_component(normalized_source)

    return normalized_source or DEFAULT_SOURCE_NAME


# Normalisation des fichiers

def normalize_filename(
    filename: Any,
    max_length: int = MAX_FILENAME_LENGTH
) -> str:
    """Transforme une valeur en nom de fichier sûr.

    Parameters
    ----------
    filename : Any
        Le nom de fichier proposé.
    max_length : int
        La longueur maximale du résultat, extension comprise. Par défaut 255.

    Returns
    -------
    str
        Un nom en minuscules sans caractère interdit, espace ni point initial ;
        « file » si rien d'utilisable ne reste.
    """

    # Mettre en minuscules et remplacer caractères interdits et espaces
    normalized_filename = normalize_unicode(filename).strip().casefold()

    if not normalized_filename:
        return DEFAULT_FILENAME

    normalized_filename = INVALID_FILENAME_PATTERN.sub(
        "_",
        normalized_filename
    )
    normalized_filename = WHITESPACE_PATTERN.sub("_", normalized_filename)
    normalized_filename = collapse_separators(normalized_filename)
    normalized_filename = normalized_filename.lstrip(".")
    normalized_filename = clean_path_component(normalized_filename)

    if not normalized_filename:
        return DEFAULT_FILENAME

    # Éviter les noms réservés par Windows, puis tronquer en gardant l'extension
    normalized_filename = protect_windows_reserved_name(
        normalized_filename,
        prefix="file"
    )
    normalized_filename = truncate_filename(normalized_filename, max_length)
    normalized_filename = clean_path_component(normalized_filename)

    return normalized_filename or DEFAULT_FILENAME