"""Stockage transactionnel des articles au format CSV.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/files/csv_storage.py` (commit 93632c5).
"""

from __future__ import annotations

import csv
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

from config.paths import PROCESSED_DATA_DIR
from src.article.schema.article_schema import STANDARD_ARTICLE_FIELDS
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
ensure_directory_exists(PROCESSED_DATA_DIR)

CsvFileStatus = Literal["absent", "valid", "corrupted"]


@dataclass(slots=True)
class CsvLoadResult:
    """Résultat de lecture d'un fichier CSV."""

    status: CsvFileStatus
    articles: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass(slots=True)
class CsvSourceWriteResult:
    """Résultat d'écriture du fichier CSV d'une source."""

    success: bool
    filepath: Path
    article_count: int = 0
    corrupted_backup: Path | None = None
    error: str = ""


@dataclass(slots=True)
class CsvStorageReport:
    """Rapport complet d'une opération de stockage CSV."""

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


def serialize_complex_value(value: Any) -> Any:
    """Convertit récursivement une valeur complexe pour une cellule CSV."""

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

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()

    if isinstance(value, Mapping):
        return {
            str(key): serialize_complex_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (set, frozenset)):
        items = [
            serialize_complex_value(item)
            for item in value
        ]

        try:
            return sorted(
                items,
                key=lambda item: json.dumps(
                    item,
                    sort_keys=True,
                    ensure_ascii=False
                )
            )
        except (TypeError, ValueError):
            return items

    if isinstance(
        value,
        (list, tuple, np.ndarray, pd.Series, pd.Index)
    ):
        items = (
            value.tolist()
            if hasattr(value, "tolist")
            else list(value)
        )

        return [
            serialize_complex_value(item)
            for item in items
        ]

    item_method = getattr(value, "item", None)

    if callable(item_method):
        try:
            return serialize_complex_value(item_method())
        except (TypeError, ValueError, AttributeError):
            pass

    return str(value)

def serialize_csv_value(value: Any) -> str:
    """Sérialise explicitement une valeur dans une cellule CSV."""

    serialized_value = serialize_complex_value(value)

    if serialized_value is None:
        return ""

    if isinstance(serialized_value, str):
        return serialized_value

    if isinstance(serialized_value, (dict, list)):
        return json.dumps(serialized_value, ensure_ascii=False, sort_keys=True)

    return str(serialized_value)


def prepare_articles_for_storage(
    articles: list[Any]
) -> tuple[list[dict[str, str]], int]:
    """Normalise puis sérialise les articles pour le CSV."""

    normalized_articles, ignored_count = normalize_articles_schema(articles)
    prepared_articles = [
        {
            field: serialize_csv_value(article.get(field))
            for field in STANDARD_ARTICLE_FIELDS
        }
        for article in normalized_articles
    ]
    return prepared_articles, ignored_count


def create_empty_dataframe() -> pd.DataFrame:
    """Retourne un DataFrame vide conforme au schéma standard."""

    return pd.DataFrame(columns=list(STANDARD_ARTICLE_FIELDS))


def prepare_dataframe(articles: list[Any]) -> pd.DataFrame:
    """Transforme des articles en DataFrame CSV ordonné."""

    prepared_articles, ignored_count = prepare_articles_for_storage(articles)

    if ignored_count:
        logger.warning("%s élément(s) ignoré(s) pendant la préparation CSV.", ignored_count)

    if not prepared_articles:
        return create_empty_dataframe()

    return pd.DataFrame(prepared_articles, columns=list(STANDARD_ARTICLE_FIELDS)).fillna("")


# Lecture et récupération

def load_csv_file(filepath: Path) -> CsvLoadResult:
    """Charge prudemment un CSV et distingue absence et corruption."""

    try:
        if not filepath.exists():
            return CsvLoadResult(status="absent")

        if not filepath.is_file():
            return CsvLoadResult(
                status="corrupted",
                error=f"Le chemin CSV n'est pas un fichier : {filepath}"
            )

        dataframe = pd.read_csv(
            filepath,
            encoding="utf-8",
            dtype=str,
            keep_default_na=False,
            na_filter=False
        )

    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError
    ) as error:
        return CsvLoadResult(status="corrupted", error=str(error))

    missing_columns = [
        field for field in STANDARD_ARTICLE_FIELDS
        if field not in dataframe.columns
    ]

    if missing_columns:
        return CsvLoadResult(
            status="corrupted",
            error=f"Colonnes standards absentes : {', '.join(missing_columns)}"
        )

    records = dataframe[list(STANDARD_ARTICLE_FIELDS)].to_dict(orient="records")
    prepared_articles, _ = prepare_articles_for_storage(records)
    return CsvLoadResult(status="valid", articles=prepared_articles)


def backup_corrupted_file(filepath: Path) -> Path:
    """Crée une copie récupérable d'un fichier CSV corrompu."""

    timestamp = get_current_datetime().strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = filepath.with_name(
        f"{filepath.name}.corrupted.{timestamp}.{uuid4().hex[:8]}.bak"
    )
    shutil.copy2(filepath, backup_path)
    return backup_path


# Fusion et écriture transactionnelle

def merge_articles(
    existing_articles: list[dict[str, Any]],
    new_articles: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Fusionne, déduplique puis remet les articles au format CSV."""

    merged_articles = deduplicate_articles(existing_articles + new_articles)
    prepared_articles, _ = prepare_articles_for_storage(merged_articles)
    return prepared_articles


def verify_temporary_csv(filepath: Path, expected_count: int) -> None:
    """Vérifie que le CSV temporaire est relisible et complet."""

    dataframe = pd.read_csv(
        filepath,
        encoding="utf-8",
        dtype=str,
        keep_default_na=False,
        na_filter=False
    )

    if list(dataframe.columns) != list(STANDARD_ARTICLE_FIELDS):
        raise ValueError("Le CSV temporaire ne respecte pas l'ordre standard des colonnes.")

    if len(dataframe) != expected_count:
        raise ValueError(
            f"Le CSV temporaire contient {len(dataframe)} article(s), "
            f"{expected_count} attendu(s)."
        )


def write_csv_file(articles: list[dict[str, str]], filepath: Path) -> None:
    """Écrit, synchronise, vérifie puis remplace atomiquement un CSV."""

    ensure_directory_exists(filepath.parent)
    temporary_path = create_temporary_path(filepath)
    dataframe = pd.DataFrame(articles, columns=list(STANDARD_ARTICLE_FIELDS)).fillna("")

    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as file:
            dataframe.to_csv(
                file,
                index=False,
                columns=list(STANDARD_ARTICLE_FIELDS),
                encoding="utf-8",
                na_rep="",
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n"
            )
            file.flush()
            os.fsync(file.fileno())

        verify_temporary_csv(temporary_path, len(articles))
        atomic_replace(temporary_path, filepath)

    finally:
        remove_file_if_exists(temporary_path)


def save_source_articles(
    source: str,
    articles: list[dict[str, Any]],
    execution_date: str
) -> CsvSourceWriteResult:
    """Fusionne, déduplique et sauvegarde les articles d'une source."""

    normalized_source = normalize_source_name(source)
    source_directory = build_source_directory(PROCESSED_DATA_DIR, normalized_source)
    filename = create_daily_filename(normalized_source, "csv", execution_date)
    filepath = source_directory / filename
    corrupted_backup: Path | None = None

    try:
        with FileLock(filepath):
            load_result = load_csv_file(filepath)

            if load_result.status == "corrupted":
                try:
                    corrupted_backup = backup_corrupted_file(filepath)
                    logger.warning(
                        "Fichier CSV corrompu sauvegardé avant remplacement : %s.",
                        corrupted_backup
                    )
                except OSError as error:
                    message = f"Sauvegarde du CSV corrompu impossible : {error}"
                    logger.error("%s", message)
                    return CsvSourceWriteResult(False, filepath, error=message)

            normalized_articles, ignored_count = prepare_articles_for_storage(articles)

            if ignored_count:
                logger.warning(
                    "%s élément(s) ignoré(s) avant l'écriture CSV de %s.",
                    ignored_count,
                    normalized_source
                )

            merged_articles = merge_articles(load_result.articles, normalized_articles)

            if not merged_articles:
                return CsvSourceWriteResult(
                    False,
                    filepath,
                    corrupted_backup=corrupted_backup,
                    error="Aucun article exploitable à écrire."
                )

            write_csv_file(merged_articles, filepath)

        logger.debug("%s article(s) présent(s) dans %s.", len(merged_articles), filepath)
        return CsvSourceWriteResult(
            True,
            filepath,
            article_count=len(merged_articles),
            corrupted_backup=corrupted_backup
        )

    except FileLockTimeoutError as error:
        return CsvSourceWriteResult(False, filepath, error=str(error))
    except (OSError, TypeError, ValueError, pd.errors.ParserError) as error:
        logger.error("Erreur lors de la sauvegarde CSV dans %s : %s", filepath, error)
        return CsvSourceWriteResult(False, filepath, error=str(error))


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


def save_articles(articles: list[Any]) -> CsvStorageReport:
    """Sauvegarde les articles dans un CSV quotidien par source."""

    report = CsvStorageReport(received=len(articles) if isinstance(articles, list) else 0)

    if not isinstance(articles, list) or not articles:
        report.errors.append("Aucun article fourni au stockage CSV.")
        return report

    normalized_articles, ignored_count = normalize_articles_schema(articles)
    report.normalized = len(normalized_articles)
    report.ignored = ignored_count

    if not normalized_articles:
        report.errors.append("Aucun article exploitable à sauvegarder au format CSV.")
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
    """Charge et déduplique les articles enregistrés dans les CSV."""

    if source:
        directory = (
            PROCESSED_DATA_DIR
            / normalize_source_name(source)
        )

        filepaths = (
            sorted(
                directory.glob("*.csv"),
                key=lambda filepath: filepath.name
            )
            if directory.exists()
            else []
        )
    else:
        filepaths = sorted(
            PROCESSED_DATA_DIR.rglob("*.csv"),
            key=lambda filepath: (
                filepath.name,
                filepath.as_posix()
            )
        )

    articles: list[dict[str, Any]] = []

    for filepath in filepaths:
        result = load_csv_file(filepath)

        if result.status == "valid":
            articles.extend(result.articles)

        elif result.status == "corrupted":
            logger.warning(
                "CSV corrompu ignoré : %s (%s)",
                filepath,
                result.error
            )

    return (
        deduplicate_articles(articles)
        if articles
        else []
    )