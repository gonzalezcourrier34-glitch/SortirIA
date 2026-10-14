"""Stockage transactionnel des articles au format JSON.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/files/json_storage.py` (commit 93632c5).
"""

from __future__ import annotations

import json
import math
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
import pandas as pd

from config.paths import RAW_DATA_DIR

from src.article.schema.article_schema_normalizer import normalize_articles_schema
from src.article.processing.article_deduplicator import deduplicate_articles

from src.logger import get_logger
from src.utils.date_utils import get_current_datetime
from src.utils.path_utils import normalize_source_name
from src.storage.files.storage_utils import (
    FileLock,
    FileLockTimeoutError,
    atomic_replace,
    build_source_directory,
    create_daily_filename,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)

logger = get_logger(__name__)
ensure_directory_exists(RAW_DATA_DIR)

JsonFileStatus = Literal["absent", "valid", "corrupted"]


@dataclass(slots=True)
class JsonLoadResult:
    """Résultat de lecture d'un fichier JSON."""

    status: JsonFileStatus
    articles: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass(slots=True)
class JsonSourceWriteResult:
    """Résultat d'écriture du fichier JSON d'une source."""

    success: bool
    filepath: Path
    article_count: int = 0
    corrupted_backup: Path | None = None
    error: str = ""


@dataclass(slots=True)
class JsonStorageReport:
    """Rapport complet d'une opération de stockage JSON."""

    success: bool = False
    received: int = 0
    normalized: int = 0
    ignored: int = 0
    files_attempted: int = 0
    files_saved: int = 0
    files_failed: int = 0
    articles_written: int = 0
    saved_files: list[Path] = field(default_factory=list)
    corrupted_backups: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success


# Sérialisation

def is_missing_scalar(value: Any) -> bool:
    """Détecte les valeurs scalaires absentes NumPy ou Pandas."""

    if value is None or value is pd.NA or value is pd.NaT:
        return True

    if isinstance(value, (float, np.floating)):
        return math.isnan(float(value))

    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False

    return isinstance(missing, (bool, np.bool_)) and bool(missing)


def serialize_json_value(value: Any) -> Any:
    """Convertit récursivement une valeur en structure compatible JSON."""

    if is_missing_scalar(value):
        return None

    if isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, np.number):
        return value.item()

    if isinstance(value, Mapping):
        return {
            str(key): serialize_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (set, frozenset)):
        items = [serialize_json_value(item) for item in value]
        try:
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
        except (TypeError, ValueError):
            return items

    if isinstance(value, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        items = value.tolist() if hasattr(value, "tolist") else list(value)
        return [serialize_json_value(item) for item in items]

    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().isoformat()
        except (AttributeError, TypeError, ValueError):
            pass

    if hasattr(value, "item"):
        try:
            return serialize_json_value(value.item())
        except (TypeError, ValueError):
            pass

    raise TypeError(f"Type non sérialisable en JSON : {type(value).__name__}")


def make_json_serializable(value: Any) -> Any:
    """Retourne une structure entièrement sérialisable en JSON."""

    return serialize_json_value(value)


def prepare_articles_for_storage(
    articles: list[Any]
) -> tuple[list[dict[str, Any]], int]:
    """Normalise les articles avant leur stockage."""

    normalized_articles, ignored_count = normalize_articles_schema(articles)
    serializable_articles = [
        make_json_serializable(article)
        for article in normalized_articles
    ]
    return serializable_articles, ignored_count


# Lecture et récupération

def load_json_file(filepath: Path) -> JsonLoadResult:
    """Charge prudemment un JSON et distingue absence et corruption."""

    try:
        if not filepath.exists():
            return JsonLoadResult(status="absent")

        if not filepath.is_file():
            return JsonLoadResult(
                status="corrupted",
                error=f"Le chemin JSON n'est pas un fichier : {filepath}"
            )

        with filepath.open("r", encoding="utf-8") as file:
            data = json.load(file)

    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        return JsonLoadResult(status="corrupted", error=str(error))

    if isinstance(data, Mapping):
        items: list[Any] = [dict(data)]
    elif isinstance(data, list):
        items = data
    else:
        return JsonLoadResult(
            status="corrupted",
            error=f"Racine JSON non prise en charge : {type(data).__name__}"
        )

    articles = [dict(item) for item in items if isinstance(item, Mapping)]
    ignored_count = len(items) - len(articles)

    if ignored_count:
        logger.warning("%s élément(s) invalide(s) ignoré(s) dans %s.", ignored_count, filepath)

    normalized_articles, _ = prepare_articles_for_storage(articles)
    return JsonLoadResult(status="valid", articles=normalized_articles)


def backup_corrupted_file(filepath: Path) -> Path:
    """Crée une copie récupérable d'un fichier corrompu."""

    timestamp = get_current_datetime().strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = filepath.with_name(
        f"{filepath.name}.corrupted.{timestamp}.{uuid4().hex[:8]}.bak"
    )
    shutil.copy2(filepath, backup_path)
    return backup_path


# Écriture transactionnelle

def verify_temporary_json(filepath: Path, expected_count: int) -> None:
    """Vérifie que le JSON temporaire est relisible et complet."""

    with filepath.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("Le JSON temporaire ne contient pas une liste.")

    if len(data) != expected_count:
        raise ValueError(
            f"Le JSON temporaire contient {len(data)} article(s), "
            f"{expected_count} attendu(s)."
        )

    if any(not isinstance(article, dict) for article in data):
        raise ValueError("Le JSON temporaire contient un article non dictionnaire.")


def write_json_file(articles: list[dict[str, Any]], filepath: Path) -> None:
    """Écrit, synchronise, vérifie puis remplace atomiquement un JSON."""

    ensure_directory_exists(filepath.parent)
    temporary_path = create_temporary_path(filepath)

    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(
                articles,
                file,
                ensure_ascii=False,
                indent=4,
                allow_nan=False,
                default=serialize_json_value
            )
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        verify_temporary_json(temporary_path, len(articles))
        atomic_replace(temporary_path, filepath)

    finally:
        remove_file_if_exists(temporary_path)


def save_source_articles(
    source: str,
    articles: list[dict[str, Any]],
    execution_date: str
) -> JsonSourceWriteResult:
    """Fusionne, déduplique et sauvegarde les articles d'une source."""

    normalized_source = normalize_source_name(source)
    source_directory = build_source_directory(RAW_DATA_DIR, normalized_source)
    filename = create_daily_filename(normalized_source, "json", execution_date)
    filepath = source_directory / filename
    corrupted_backup: Path | None = None

    try:
        with FileLock(filepath):
            load_result = load_json_file(filepath)

            if load_result.status == "corrupted":
                try:
                    corrupted_backup = backup_corrupted_file(filepath)
                    logger.warning(
                        "Fichier JSON corrompu sauvegardé avant remplacement : %s.",
                        corrupted_backup
                    )
                except OSError as error:
                    message = f"Sauvegarde du JSON corrompu impossible : {error}"
                    logger.error("%s", message)
                    return JsonSourceWriteResult(False, filepath, error=message)

            normalized_articles, ignored_count = prepare_articles_for_storage(articles)

            if ignored_count:
                logger.warning(
                    "%s élément(s) ignoré(s) avant l'écriture JSON de %s.",
                    ignored_count,
                    normalized_source
                )

            merged_articles = deduplicate_articles(
                load_result.articles + normalized_articles
            )
            merged_articles, _ = prepare_articles_for_storage(merged_articles)

            if not merged_articles:
                return JsonSourceWriteResult(
                    False,
                    filepath,
                    corrupted_backup=corrupted_backup,
                    error="Aucun article exploitable à écrire."
                )

            write_json_file(merged_articles, filepath)

        logger.debug("%s article(s) présent(s) dans %s.", len(merged_articles), filepath)
        return JsonSourceWriteResult(
            True,
            filepath,
            article_count=len(merged_articles),
            corrupted_backup=corrupted_backup
        )

    except FileLockTimeoutError as error:
        return JsonSourceWriteResult(False, filepath, error=str(error))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.error("Erreur lors de la sauvegarde JSON dans %s : %s", filepath, error)
        return JsonSourceWriteResult(False, filepath, error=str(error))


# Service public

def group_articles_by_source(
    articles: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Regroupe les articles selon leur source normalisée."""

    grouped_articles: dict[str, list[dict[str, Any]]] = {}

    for article in articles:
        source = normalize_source_name(article.get("source", "unknown"))
        grouped_articles.setdefault(source, []).append(article)

    return grouped_articles


def save_articles(articles: list[Any]) -> JsonStorageReport:
    """Sauvegarde les articles dans un JSON quotidien par source."""

    report = JsonStorageReport(received=len(articles) if isinstance(articles, list) else 0)

    if not isinstance(articles, list) or not articles:
        report.errors.append("Aucun article fourni au stockage JSON.")
        return report

    normalized_articles, ignored_count = prepare_articles_for_storage(articles)
    report.normalized = len(normalized_articles)
    report.ignored = ignored_count

    if not normalized_articles:
        report.errors.append("Aucun article exploitable à sauvegarder au format JSON.")
        return report

    execution_date = get_current_datetime().strftime("%Y%m%d")
    grouped_articles = group_articles_by_source(normalized_articles)
    report.files_attempted = len(grouped_articles)

    for source, source_articles in grouped_articles.items():
        result = save_source_articles(source, source_articles, execution_date)

        if result.success:
            report.files_saved += 1
            report.articles_written += result.article_count
            report.saved_files.append(result.filepath)
        else:
            report.files_failed += 1
            report.errors.append(f"{result.filepath}: {result.error}")

        if result.corrupted_backup is not None:
            report.corrupted_backups.append(result.corrupted_backup)

    report.success = report.files_saved > 0 and report.files_failed == 0
    return report


def load_articles(source: str | None = None) -> list[dict[str, Any]]:
    """Charge et déduplique les articles enregistrés dans les JSON."""

    if source:
        directory = RAW_DATA_DIR / normalize_source_name(source)
        filepaths = sorted(directory.glob("*.json")) if directory.exists() else []
    else:
        filepaths = sorted(RAW_DATA_DIR.rglob("*.json"))

    articles: list[dict[str, Any]] = []

    for filepath in filepaths:
        result = load_json_file(filepath)

        if result.status == "valid":
            articles.extend(result.articles)
        elif result.status == "corrupted":
            logger.warning("JSON corrompu ignoré : %s (%s)", filepath, result.error)

    return deduplicate_articles(articles) if articles else []