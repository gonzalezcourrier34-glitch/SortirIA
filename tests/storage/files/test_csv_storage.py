"""Tests du stockage CSV transactionnel CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/files/test_csv_storage.py` (commit 93632c5).
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

import src.storage.files.csv_storage as module
from src.storage.files.csv_storage import (
    CsvLoadResult,
    CsvSourceWriteResult,
    CsvStorageReport,
    backup_corrupted_file,
    create_empty_dataframe,
    group_articles_by_source,
    is_missing_scalar,
    load_articles,
    load_csv_file,
    merge_articles,
    prepare_articles_for_storage,
    prepare_dataframe,
    save_articles,
    save_source_articles,
    serialize_complex_value,
    serialize_csv_value,
    verify_temporary_csv,
    write_csv_file
)


# Dataclasses

def test_csv_load_result_defaults() -> None:
    result = CsvLoadResult(status="absent")

    assert result.status == "absent"
    assert result.articles == []
    assert result.error == ""


def test_csv_source_write_result_defaults(tmp_path: Path) -> None:
    filepath = tmp_path / "articles.csv"
    result = CsvSourceWriteResult(False, filepath)

    assert result.success is False
    assert result.filepath == filepath
    assert result.article_count == 0
    assert result.corrupted_backup is None
    assert result.error == ""


def test_csv_storage_report_boolean_value() -> None:
    assert bool(CsvStorageReport(success=True)) is True
    assert bool(CsvStorageReport(success=False)) is False


# Valeurs manquantes

@pytest.mark.parametrize(
    "value",
    [
        None,
        pd.NA,
        pd.NaT,
        float("nan"),
        np.float32("nan"),
        np.float64("nan")
    ]
)
def test_is_missing_scalar_detects_missing_values(value: Any) -> None:
    assert is_missing_scalar(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        0,
        False,
        1.5,
        np.float64(2.5),
        "article"
    ]
)
def test_is_missing_scalar_rejects_present_values(value: Any) -> None:
    assert is_missing_scalar(value) is False


def test_is_missing_scalar_handles_array_result() -> None:
    assert is_missing_scalar(np.array([1, 2])) is False


def test_is_missing_scalar_handles_pd_isna_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.pd,
        "isna",
        lambda value: (_ for _ in ()).throw(TypeError("Erreur simulée"))
    )

    assert is_missing_scalar(object()) is False


# Sérialisation complexe

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (pd.NA, None),
        (pd.NaT, None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        ("texte", "texte"),
        (42, 42),
        (True, True),
        (1.25, 1.25),
        (np.int64(5), 5),
        (np.bool_(True), True),
        (Decimal("1.25"), 1.25)
    ]
)
def test_serialize_complex_value_scalars(
    value: Any,
    expected: Any
) -> None:
    assert serialize_complex_value(value) == expected


def test_serialize_complex_value_path() -> None:
    value = Path("data/articles.csv")

    assert serialize_complex_value(value) == str(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 27, 10, 30, tzinfo=UTC),
            "2026-07-27T10:30:00+00:00"
        ),
        (
            date(2026, 7, 27),
            "2026-07-27"
        ),
        (
            pd.Timestamp("2026-07-27T10:30:00Z"),
            "2026-07-27T10:30:00+00:00"
        )
    ]
)
def test_serialize_complex_value_dates(
    value: Any,
    expected: str
) -> None:
    assert serialize_complex_value(value) == expected


def test_serialize_complex_value_mapping() -> None:
    value = {
        1: np.int64(4),
        "date": date(2026, 7, 27),
        "missing": pd.NA
    }

    assert serialize_complex_value(value) == {
        "1": 4,
        "date": "2026-07-27",
        "missing": None
    }


def test_serialize_complex_value_set_is_deterministic() -> None:
    assert serialize_complex_value({"b", "a", "c"}) == ["a", "b", "c"]


def test_serialize_complex_value_frozenset() -> None:
    assert serialize_complex_value(frozenset({3, 1, 2})) == [1, 2, 3]


def test_serialize_complex_value_set_falls_back_when_sort_fails(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.json,
        "dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("Erreur"))
    )

    result = serialize_complex_value({"a", "b"})

    assert sorted(result) == ["a", "b"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([np.int64(1), np.int64(2)], [1, 2]),
        ((1, 2), [1, 2]),
        (np.array([1, 2]), [1, 2]),
        (pd.Series([1, 2]), [1, 2]),
        (pd.Index([1, 2]), [1, 2])
    ]
)
def test_serialize_complex_value_sequences(
    value: Any,
    expected: list[Any]
) -> None:
    assert serialize_complex_value(value) == expected


def test_serialize_complex_value_uses_item() -> None:
    class ItemLike:
        def item(self) -> int:
            return 7

    assert serialize_complex_value(ItemLike()) == 7


def test_serialize_complex_value_falls_back_to_string() -> None:
    class StringLike:
        def __str__(self) -> str:
            return "custom-value"

    assert serialize_complex_value(StringLike()) == "custom-value"


def test_serialize_complex_value_ignores_invalid_item() -> None:
    class InvalidItem:
        def item(self) -> Any:
            raise ValueError("Erreur")

        def __str__(self) -> str:
            return "fallback"

    assert serialize_complex_value(InvalidItem()) == "fallback"


# Cellules CSV

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (pd.NA, ""),
        ("texte", "texte"),
        (42, "42"),
        (True, "True"),
        (1.25, "1.25")
    ]
)
def test_serialize_csv_value_simple_values(
    value: Any,
    expected: str
) -> None:
    assert serialize_csv_value(value) == expected


def test_serialize_csv_value_mapping() -> None:
    value = {"b": 2, "a": 1}

    assert serialize_csv_value(value) == '{"a": 1, "b": 2}'


def test_serialize_csv_value_list() -> None:
    value = ["économie", "science"]

    assert serialize_csv_value(value) == '["économie", "science"]'


# Préparation des articles

def test_prepare_articles_for_storage(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title", "metadata")
    )
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (
            [
                {
                    "id": "1",
                    "title": "Titre",
                    "metadata": {"score": np.int64(5)}
                }
            ],
            2
        )
    )

    prepared, ignored = prepare_articles_for_storage(["raw"])

    assert prepared == [
        {
            "id": "1",
            "title": "Titre",
            "metadata": '{"score": 5}'
        }
    ]
    assert ignored == 2


def test_prepare_articles_for_storage_adds_missing_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "STANDARD_ARTICLE_FIELDS", ("id", "title"))
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: ([{"id": "1"}], 0)
    )

    prepared, ignored = prepare_articles_for_storage([{"id": "1"}])

    assert prepared == [{"id": "1", "title": ""}]
    assert ignored == 0


# DataFrame

def test_create_empty_dataframe(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "source", "title")
    )

    dataframe = create_empty_dataframe()

    assert dataframe.empty
    assert list(dataframe.columns) == ["id", "source", "title"]


def test_prepare_dataframe_returns_empty_dataframe(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: ([], 0)
    )

    dataframe = prepare_dataframe([])

    assert dataframe.empty
    assert list(dataframe.columns) == ["id", "title"]


def test_prepare_dataframe_builds_ordered_dataframe(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: (
            [{"title": "Titre", "id": "1"}],
            0
        )
    )

    dataframe = prepare_dataframe([{"id": "1"}])

    assert list(dataframe.columns) == ["id", "title"]
    assert dataframe.to_dict(orient="records") == [
        {"id": "1", "title": "Titre"}
    ]


def test_prepare_dataframe_logs_ignored_items(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(module, "STANDARD_ARTICLE_FIELDS", ("id",))
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: ([{"id": "1"}], 3)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    prepare_dataframe([{"id": "1"}])

    assert warnings == [
        (
            "%s élément(s) ignoré(s) pendant la préparation CSV.",
            3
        )
    ]


# Lecture CSV

def test_load_csv_file_returns_absent_for_missing_file(
    tmp_path: Path
) -> None:
    result = load_csv_file(tmp_path / "missing.csv")

    assert result == CsvLoadResult(status="absent")


def test_load_csv_file_rejects_directory(tmp_path: Path) -> None:
    result = load_csv_file(tmp_path)

    assert result.status == "corrupted"
    assert "n'est pas un fichier" in result.error


def test_load_csv_file_reads_valid_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.csv"
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    filepath.write_text(
        "id,title\n1,Titre\n2,Autre\n",
        encoding="utf-8"
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda records: (records, 0)
    )

    result = load_csv_file(filepath)

    assert result.status == "valid"
    assert result.articles == [
        {"id": "1", "title": "Titre"},
        {"id": "2", "title": "Autre"}
    ]


def test_load_csv_file_reorders_standard_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.csv"
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    filepath.write_text(
        "title,id,extra\nTitre,1,value\n",
        encoding="utf-8"
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda records: (records, 0)
    )

    result = load_csv_file(filepath)

    assert result.status == "valid"
    assert result.articles == [{"id": "1", "title": "Titre"}]


def test_load_csv_file_detects_missing_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.csv"
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title", "source")
    )
    filepath.write_text("id,title\n1,Titre\n", encoding="utf-8")

    result = load_csv_file(filepath)

    assert result.status == "corrupted"
    assert result.error == "Colonnes standards absentes : source"


@pytest.mark.parametrize(
    "error",
    [
        OSError("Erreur disque"),
        UnicodeDecodeError("utf-8", b"x", 0, 1, "Erreur"),
        ValueError("Valeur invalide"),
        TypeError("Type invalide"),
        pd.errors.ParserError("CSV invalide"),
        pd.errors.EmptyDataError("CSV vide")
    ]
)
def test_load_csv_file_handles_read_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception
) -> None:
    filepath = tmp_path / "articles.csv"
    filepath.write_text("id\n1\n", encoding="utf-8")

    monkeypatch.setattr(
        module.pd,
        "read_csv",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    result = load_csv_file(filepath)

    assert result.status == "corrupted"
    assert result.error == str(error)


# Sauvegarde des fichiers corrompus

def test_backup_corrupted_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.csv"
    filepath.write_text("corrupted", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: datetime(2026, 7, 27, 10, 30, tzinfo=UTC)
    )
    monkeypatch.setattr(
        module,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef1234567890")
    )

    backup = backup_corrupted_file(filepath)

    assert backup.name == (
        "articles.csv.corrupted.20260727T103000000000Z.abcdef12.bak"
    )
    assert backup.read_text(encoding="utf-8") == "corrupted"


# Fusion

def test_merge_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = [{"id": "old"}]
    new = [{"id": "new"}]

    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: [{"id": "merged"}]
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: ([{"id": "prepared"}], 0)
    )

    result = merge_articles(existing, new)

    assert result == [{"id": "prepared"}]


# Vérification temporaire

def test_verify_temporary_csv_accepts_valid_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.csv"
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    filepath.write_text(
        "id,title\n1,Titre\n2,Autre\n",
        encoding="utf-8"
    )

    verify_temporary_csv(filepath, 2)


def test_verify_temporary_csv_rejects_wrong_column_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.csv"
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    filepath.write_text(
        "title,id\nTitre,1\n",
        encoding="utf-8"
    )

    with pytest.raises(
        ValueError,
        match="ne respecte pas l'ordre standard"
    ):
        verify_temporary_csv(filepath, 1)


def test_verify_temporary_csv_rejects_wrong_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.csv"
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    filepath.write_text(
        "id,title\n1,Titre\n",
        encoding="utf-8"
    )

    with pytest.raises(
        ValueError,
        match=r"1 article\(s\), 2 attendu\(s\)"
    ):
        verify_temporary_csv(filepath, 2)


# Écriture transactionnelle

def test_write_csv_file_writes_and_replaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "source" / "articles.csv"
    temporary_path = tmp_path / "source" / ".temporary.csv"

    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        ("id", "title")
    )
    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda target: temporary_path
    )

    replaced: list[tuple[Path, Path]] = []
    removed: list[Path] = []

    def fake_replace(source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes())
        replaced.append((source, target))

    monkeypatch.setattr(module, "atomic_replace", fake_replace)
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed.append(path)
    )

    write_csv_file(
        [{"id": "1", "title": "Été"}],
        filepath
    )

    assert replaced == [(temporary_path, filepath)]
    assert removed == [temporary_path]

    dataframe = pd.read_csv(
        filepath,
        dtype=str,
        keep_default_na=False
    )
    assert dataframe.to_dict(orient="records") == [
        {"id": "1", "title": "Été"}
    ]


def test_write_csv_file_cleans_temporary_file_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.csv"
    temporary_path = tmp_path / ".temporary.csv"
    removed: list[Path] = []

    monkeypatch.setattr(module, "STANDARD_ARTICLE_FIELDS", ("id",))
    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda target: temporary_path
    )
    monkeypatch.setattr(
        module,
        "verify_temporary_csv",
        lambda path, count: (_ for _ in ()).throw(
            ValueError("Erreur simulée")
        )
    )
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed.append(path)
    )

    with pytest.raises(ValueError, match="Erreur simulée"):
        write_csv_file([{"id": "1"}], filepath)

    assert removed == [temporary_path]


# Sauvegarde par source

class DummyLock:
    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath

    def __enter__(self) -> "DummyLock":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def configure_source_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> Path:
    source_directory = tmp_path / "reuters"
    filepath = source_directory / "reuters_20260727.csv"

    monkeypatch.setattr(module, "normalize_source_name", lambda value: "reuters")
    monkeypatch.setattr(
        module,
        "build_source_directory",
        lambda root, source: source_directory
    )
    monkeypatch.setattr(
        module,
        "create_daily_filename",
        lambda source, extension, execution_date: filepath.name
    )
    monkeypatch.setattr(module, "FileLock", DummyLock)

    return filepath


def test_save_source_articles_saves_merged_articles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)
    written: list[tuple[list[dict[str, Any]], Path]] = []

    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda path: CsvLoadResult(
            "valid",
            [{"id": "old"}]
        )
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: (articles, 0)
    )
    monkeypatch.setattr(
        module,
        "merge_articles",
        lambda existing, new: existing + new
    )
    monkeypatch.setattr(
        module,
        "write_csv_file",
        lambda articles, path: written.append((articles, path))
    )

    result = save_source_articles(
        "Reuters",
        [{"id": "new"}],
        "20260727"
    )

    assert result == CsvSourceWriteResult(
        True,
        filepath,
        article_count=2
    )
    assert written == [
        ([{"id": "old"}, {"id": "new"}], filepath)
    ]


def test_save_source_articles_backs_up_corrupted_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)
    backup = tmp_path / "backup.bak"

    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda path: CsvLoadResult("corrupted", error="broken")
    )
    monkeypatch.setattr(
        module,
        "backup_corrupted_file",
        lambda path: backup
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: (articles, 0)
    )
    monkeypatch.setattr(
        module,
        "merge_articles",
        lambda existing, new: new
    )
    monkeypatch.setattr(module, "write_csv_file", lambda articles, path: None)

    result = save_source_articles(
        "Reuters",
        [{"id": "new"}],
        "20260727"
    )

    assert result.success is True
    assert result.corrupted_backup == backup


def test_save_source_articles_returns_failure_when_backup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)

    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda path: CsvLoadResult("corrupted", error="broken")
    )
    monkeypatch.setattr(
        module,
        "backup_corrupted_file",
        lambda path: (_ for _ in ()).throw(OSError("Accès refusé"))
    )

    result = save_source_articles(
        "Reuters",
        [{"id": "new"}],
        "20260727"
    )

    assert result.success is False
    assert result.filepath == filepath
    assert result.error == (
        "Sauvegarde du CSV corrompu impossible : Accès refusé"
    )


def test_save_source_articles_returns_failure_without_articles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)

    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda path: CsvLoadResult("absent")
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: ([], 1)
    )
    monkeypatch.setattr(
        module,
        "merge_articles",
        lambda existing, new: []
    )

    result = save_source_articles(
        "Reuters",
        [{"invalid": True}],
        "20260727"
    )

    assert result == CsvSourceWriteResult(
        False,
        filepath,
        error="Aucun article exploitable à écrire."
    )


def test_save_source_articles_handles_lock_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)

    class TimeoutLock:
        def __init__(self, path: Path) -> None:
            pass

        def __enter__(self) -> None:
            raise module.FileLockTimeoutError("Verrou indisponible")

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(module, "FileLock", TimeoutLock)

    result = save_source_articles(
        "Reuters",
        [{"id": "1"}],
        "20260727"
    )

    assert result.success is False
    assert result.filepath == filepath
    assert result.error == "Verrou indisponible"


@pytest.mark.parametrize(
    "error",
    [
        OSError("Erreur disque"),
        TypeError("Type invalide"),
        ValueError("Valeur invalide"),
        pd.errors.ParserError("CSV invalide")
    ]
)
def test_save_source_articles_handles_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)

    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda path: (_ for _ in ()).throw(error)
    )

    result = save_source_articles(
        "Reuters",
        [{"id": "1"}],
        "20260727"
    )

    assert result.success is False
    assert result.filepath == filepath
    assert result.error == str(error)


# Regroupement

def test_group_articles_by_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: str(value).strip().lower().replace(" ", "_")
    )

    result = group_articles_by_source([
        {"id": "1", "source": "Le Monde"},
        {"id": "2", "source": "Le Monde"},
        {"id": "3", "source": "Reuters"},
        {"id": "4"}
    ])

    assert result == {
        "le_monde": [
            {"id": "1", "source": "Le Monde"},
            {"id": "2", "source": "Le Monde"}
        ],
        "reuters": [
            {"id": "3", "source": "Reuters"}
        ],
        "unknown": [
            {"id": "4"}
        ]
    }


# Service public

@pytest.mark.parametrize("articles", [None, {}, (), [], "article"])
def test_save_articles_rejects_invalid_input(articles: Any) -> None:
    result = save_articles(articles)

    assert result.success is False
    assert result.received == 0
    assert result.errors == [
        "Aucun article fourni au stockage CSV."
    ]


def test_save_articles_rejects_unusable_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: ([], 2)
    )

    result = save_articles([None, "invalid"])

    assert result.success is False
    assert result.received == 2
    assert result.normalized == 0
    assert result.ignored == 2
    assert result.errors == [
        "Aucun article exploitable à sauvegarder au format CSV."
    ]


def test_save_articles_builds_success_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    articles = [
        {"id": "1", "source": "Reuters"},
        {"id": "2", "source": "Guardian"}
    ]
    backup = tmp_path / "corrupted.bak"

    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda values: (values, 0)
    )
    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: datetime(2026, 7, 27, tzinfo=UTC)
    )
    monkeypatch.setattr(
        module,
        "group_articles_by_source",
        lambda values: {
            "reuters": [values[0]],
            "guardian": [values[1]]
        }
    )

    def fake_save(
        source: str,
        source_articles: list[dict[str, Any]],
        execution_date: str
    ) -> CsvSourceWriteResult:
        return CsvSourceWriteResult(
            True,
            tmp_path / f"{source}.csv",
            article_count=len(source_articles),
            corrupted_backup=backup if source == "reuters" else None
        )

    monkeypatch.setattr(module, "save_source_articles", fake_save)

    result = save_articles(articles)

    assert result.success is True
    assert result.received == 2
    assert result.normalized == 2
    assert result.ignored == 0
    assert result.files_attempted == 2
    assert result.files_saved == 2
    assert result.files_failed == 0
    assert result.articles_written == 2
    assert result.saved_files == [
        tmp_path / "reuters.csv",
        tmp_path / "guardian.csv"
    ]
    assert result.corrupted_backups == [backup]
    assert result.errors == []


def test_save_articles_builds_partial_failure_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    articles = [
        {"id": "1", "source": "Reuters"},
        {"id": "2", "source": "Guardian"}
    ]

    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda values: (values, 0)
    )
    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: datetime(2026, 7, 27, tzinfo=UTC)
    )
    monkeypatch.setattr(
        module,
        "group_articles_by_source",
        lambda values: {
            "reuters": [values[0]],
            "guardian": [values[1]]
        }
    )

    def fake_save(
        source: str,
        source_articles: list[dict[str, Any]],
        execution_date: str
    ) -> CsvSourceWriteResult:
        if source == "reuters":
            return CsvSourceWriteResult(
                True,
                tmp_path / "reuters.csv",
                article_count=1
            )
        return CsvSourceWriteResult(
            False,
            tmp_path / "guardian.csv",
            error="Erreur disque"
        )

    monkeypatch.setattr(module, "save_source_articles", fake_save)

    result = save_articles(articles)

    assert result.success is False
    assert result.files_saved == 1
    assert result.files_failed == 1
    assert result.articles_written == 1
    assert result.errors == [
        f"{tmp_path / 'guardian.csv'}: Erreur disque"
    ]


# Chargement public

def test_load_articles_from_specific_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    processed_directory = tmp_path / "processed"
    source_directory = processed_directory / "reuters"
    source_directory.mkdir(parents=True)
    first = source_directory / "a.csv"
    second = source_directory / "b.csv"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")

    monkeypatch.setattr(module, "PROCESSED_DATA_DIR", processed_directory)
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "reuters"
    )
    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda filepath: CsvLoadResult(
            "valid",
            [{"file": filepath.name}]
        )
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )

    result = load_articles("Reuters")

    assert result == [
        {"file": "a.csv"},
        {"file": "b.csv"}
    ]


def test_load_articles_returns_empty_for_missing_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(module, "PROCESSED_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "missing"
    )

    assert load_articles("Missing") == []


def test_load_articles_reads_all_sources_and_ignores_corruption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    processed_directory = tmp_path / "processed"
    first = processed_directory / "reuters" / "a.csv"
    second = processed_directory / "guardian" / "b.csv"
    corrupted = processed_directory / "bbc" / "c.csv"

    for filepath in (first, second, corrupted):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("", encoding="utf-8")

    warnings: list[tuple[Any, ...]] = []

    def fake_load(filepath: Path) -> CsvLoadResult:
        if filepath == corrupted:
            return CsvLoadResult("corrupted", error="CSV invalide")
        return CsvLoadResult("valid", [{"file": filepath.name}])

    monkeypatch.setattr(
        module,
        "PROCESSED_DATA_DIR",
        processed_directory
    )
    monkeypatch.setattr(module, "load_csv_file", fake_load)
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = load_articles()

    assert result == [
        {"file": "a.csv"},
        {"file": "b.csv"}
    ]
    assert warnings == [
        (
            "CSV corrompu ignoré : %s (%s)",
            corrupted,
            "CSV invalide"
        )
    ]


def test_load_articles_returns_empty_when_no_valid_articles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "corrupted.csv"
    filepath.write_text("invalid", encoding="utf-8")

    monkeypatch.setattr(module, "PROCESSED_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        module,
        "load_csv_file",
        lambda path: CsvLoadResult("corrupted", error="broken")
    )

    assert load_articles() == []