"""Tests du stockage JSON transactionnel CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/files/test_json_storage.py` (commit 93632c5).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

import src.storage.files.json_storage as module
from src.storage.files.json_storage import (
    JsonLoadResult,
    JsonSourceWriteResult,
    JsonStorageReport,
    backup_corrupted_file,
    group_articles_by_source,
    is_missing_scalar,
    load_articles,
    load_json_file,
    make_json_serializable,
    prepare_articles_for_storage,
    save_articles,
    save_source_articles,
    serialize_json_value,
    verify_temporary_json,
    write_json_file
)


# Dataclasses

def test_json_load_result_defaults() -> None:
    result = JsonLoadResult(status="absent")

    assert result.status == "absent"
    assert result.articles == []
    assert result.error == ""


def test_json_source_write_result_defaults(tmp_path: Path) -> None:
    filepath = tmp_path / "articles.json"
    result = JsonSourceWriteResult(False, filepath)

    assert result.success is False
    assert result.filepath == filepath
    assert result.article_count == 0
    assert result.corrupted_backup is None
    assert result.error == ""


def test_json_storage_report_boolean_value() -> None:
    assert bool(JsonStorageReport(success=True)) is True
    assert bool(JsonStorageReport(success=False)) is False


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


def test_is_missing_scalar_handles_pandas_array_result() -> None:
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


# Sérialisation

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
        (Decimal("1.25"), Decimal("1.25").as_tuple() and 1.25)
    ]
)
def test_serialize_json_value_scalar_values(
    value: Any,
    expected: Any
) -> None:
    assert serialize_json_value(value) == expected


def test_serialize_json_value_path() -> None:
    value = Path("data/articles.json")

    assert serialize_json_value(value) == str(value)


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
def test_serialize_json_value_dates(
    value: Any,
    expected: str
) -> None:
    assert serialize_json_value(value) == expected


def test_serialize_json_value_mapping() -> None:
    value = {
        1: np.int64(4),
        "date": date(2026, 7, 27),
        "missing": pd.NA
    }

    assert serialize_json_value(value) == {
        "1": 4,
        "date": "2026-07-27",
        "missing": None
    }


def test_serialize_json_value_set_is_deterministic() -> None:
    result = serialize_json_value({"b", "a", "c"})

    assert result == ["a", "b", "c"]


def test_serialize_json_value_frozenset() -> None:
    result = serialize_json_value(frozenset({3, 1, 2}))

    assert result == [1, 2, 3]


def test_serialize_json_value_set_falls_back_when_sort_fails(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module.json,
        "dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("Erreur"))
    )

    result = serialize_json_value({"a", "b"})

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
def test_serialize_json_value_sequences(
    value: Any,
    expected: list[Any]
) -> None:
    assert serialize_json_value(value) == expected


def test_serialize_json_value_uses_to_pydatetime() -> None:
    class DateLike:
        def to_pydatetime(self) -> datetime:
            return datetime(2026, 7, 27, tzinfo=UTC)

    assert serialize_json_value(DateLike()) == "2026-07-27T00:00:00+00:00"


def test_serialize_json_value_ignores_invalid_to_pydatetime_and_uses_item() -> None:
    class ItemLike:
        def to_pydatetime(self) -> Any:
            raise ValueError("Date invalide")

        def item(self) -> int:
            return 7

    assert serialize_json_value(ItemLike()) == 7


def test_serialize_json_value_raises_for_unsupported_type() -> None:
    class Unsupported:
        pass

    with pytest.raises(
        TypeError,
        match="Type non sérialisable en JSON : Unsupported"
    ):
        serialize_json_value(Unsupported())


def test_make_json_serializable_delegates(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = object()
    monkeypatch.setattr(module, "serialize_json_value", lambda value: marker)

    assert make_json_serializable("value") is marker


# Préparation

def test_prepare_articles_for_storage(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    normalized = [
        {"id": "1", "value": np.int64(5)},
        {"id": "2", "date": date(2026, 7, 27)}
    ]

    monkeypatch.setattr(
        module,
        "normalize_articles_schema",
        lambda articles: (normalized, 2)
    )

    result, ignored = prepare_articles_for_storage(["raw"])

    assert result == [
        {"id": "1", "value": 5},
        {"id": "2", "date": "2026-07-27"}
    ]
    assert ignored == 2


# Lecture JSON

def test_load_json_file_returns_absent_for_missing_file(
    tmp_path: Path
) -> None:
    result = load_json_file(tmp_path / "missing.json")

    assert result == JsonLoadResult(status="absent")


def test_load_json_file_rejects_directory(tmp_path: Path) -> None:
    result = load_json_file(tmp_path)

    assert result.status == "corrupted"
    assert "n'est pas un fichier" in result.error


def test_load_json_file_reads_mapping_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "article.json"
    filepath.write_text(
        json.dumps({"id": "1", "source": "Reuters"}),
        encoding="utf-8"
    )

    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: (articles, 0)
    )

    result = load_json_file(filepath)

    assert result.status == "valid"
    assert result.articles == [{"id": "1", "source": "Reuters"}]


def test_load_json_file_reads_list_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.json"
    filepath.write_text(
        json.dumps([{"id": "1"}, {"id": "2"}]),
        encoding="utf-8"
    )

    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: (articles, 0)
    )

    result = load_json_file(filepath)

    assert result.status == "valid"
    assert result.articles == [{"id": "1"}, {"id": "2"}]


def test_load_json_file_ignores_non_mapping_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.json"
    filepath.write_text(
        json.dumps([{"id": "1"}, None, "invalid", 42]),
        encoding="utf-8"
    )

    warnings: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: (articles, 0)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = load_json_file(filepath)

    assert result.status == "valid"
    assert result.articles == [{"id": "1"}]
    assert warnings == [
        (
            "%s élément(s) invalide(s) ignoré(s) dans %s.",
            3,
            filepath
        )
    ]


@pytest.mark.parametrize(
    ("content", "error_fragment"),
    [
        ("{invalid", ""),
        ("42", "Racine JSON non prise en charge : int"),
        ('"texte"', "Racine JSON non prise en charge : str")
    ]
)
def test_load_json_file_detects_corruption(
    tmp_path: Path,
    content: str,
    error_fragment: str
) -> None:
    filepath = tmp_path / "corrupted.json"
    filepath.write_text(content, encoding="utf-8")

    result = load_json_file(filepath)

    assert result.status == "corrupted"
    if error_fragment:
        assert error_fragment in result.error


def test_load_json_file_handles_unicode_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.json"
    filepath.write_bytes(b"\xff\xfe\xfd")

    result = load_json_file(filepath)

    assert result.status == "corrupted"
    assert result.error


# Sauvegarde des fichiers corrompus

def test_backup_corrupted_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.json"
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
        "articles.json.corrupted.20260727T103000000000Z.abcdef12.bak"
    )
    assert backup.read_text(encoding="utf-8") == "corrupted"


# Vérification temporaire

def test_verify_temporary_json_accepts_valid_list(
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.json"
    filepath.write_text(
        json.dumps([{"id": "1"}, {"id": "2"}]),
        encoding="utf-8"
    )

    verify_temporary_json(filepath, 2)


def test_verify_temporary_json_rejects_non_list(
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.json"
    filepath.write_text(json.dumps({"id": "1"}), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="ne contient pas une liste"
    ):
        verify_temporary_json(filepath, 1)


def test_verify_temporary_json_rejects_wrong_count(
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.json"
    filepath.write_text(json.dumps([{"id": "1"}]), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"1 article\(s\), 2 attendu\(s\)"
    ):
        verify_temporary_json(filepath, 2)


def test_verify_temporary_json_rejects_non_dict_article(
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.json"
    filepath.write_text(json.dumps([{"id": "1"}, 2]), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="article non dictionnaire"
    ):
        verify_temporary_json(filepath, 2)


# Écriture transactionnelle

def test_write_json_file_writes_and_replaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "source" / "articles.json"
    temporary_path = tmp_path / "source" / ".temporary.json"

    replaced: list[tuple[Path, Path]] = []
    removed: list[Path] = []

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda target: temporary_path
    )
    monkeypatch.setattr(
        module,
        "atomic_replace",
        lambda source, target: (
            target.write_bytes(source.read_bytes()),
            replaced.append((source, target))
        )
    )
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed.append(path)
    )

    write_json_file([{"id": "1", "title": "Été"}], filepath)

    assert replaced == [(temporary_path, filepath)]
    assert removed == [temporary_path]
    assert json.loads(filepath.read_text(encoding="utf-8")) == [
        {"id": "1", "title": "Été"}
    ]


def test_write_json_file_cleans_temporary_file_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "articles.json"
    temporary_path = tmp_path / ".temporary.json"
    removed: list[Path] = []

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda target: temporary_path
    )
    monkeypatch.setattr(
        module,
        "verify_temporary_json",
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
        write_json_file([{"id": "1"}], filepath)

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
    filepath = source_directory / "reuters_20260727.json"

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
        "load_json_file",
        lambda path: JsonLoadResult(
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
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(
        module,
        "write_json_file",
        lambda articles, path: written.append((articles, path))
    )

    result = save_source_articles(
        "Reuters",
        [{"id": "new"}],
        "20260727"
    )

    assert result == JsonSourceWriteResult(
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
        "load_json_file",
        lambda path: JsonLoadResult("corrupted", error="broken")
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
        "deduplicate_articles",
        lambda articles: articles
    )
    monkeypatch.setattr(module, "write_json_file", lambda articles, path: None)

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
        "load_json_file",
        lambda path: JsonLoadResult("corrupted", error="broken")
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
        "Sauvegarde du JSON corrompu impossible : Accès refusé"
    )


def test_save_source_articles_returns_failure_without_articles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = configure_source_paths(monkeypatch, tmp_path)

    monkeypatch.setattr(
        module,
        "load_json_file",
        lambda path: JsonLoadResult("absent")
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: ([], 1)
    )
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: []
    )

    result = save_source_articles(
        "Reuters",
        [{"invalid": True}],
        "20260727"
    )

    assert result == JsonSourceWriteResult(
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
        json.JSONDecodeError("JSON invalide", "x", 0)
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
        "load_json_file",
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
        "Aucun article fourni au stockage JSON."
    ]


def test_save_articles_rejects_unusable_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "prepare_articles_for_storage",
        lambda articles: ([], 2)
    )

    result = save_articles([None, "invalid"])

    assert result.success is False
    assert result.received == 2
    assert result.normalized == 0
    assert result.ignored == 2
    assert result.errors == [
        "Aucun article exploitable à sauvegarder au format JSON."
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
        "prepare_articles_for_storage",
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
    ) -> JsonSourceWriteResult:
        return JsonSourceWriteResult(
            True,
            tmp_path / f"{source}.json",
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
        tmp_path / "reuters.json",
        tmp_path / "guardian.json"
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
        "prepare_articles_for_storage",
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
    ) -> JsonSourceWriteResult:
        if source == "reuters":
            return JsonSourceWriteResult(
                True,
                tmp_path / "reuters.json",
                article_count=1
            )
        return JsonSourceWriteResult(
            False,
            tmp_path / "guardian.json",
            error="Erreur disque"
        )

    monkeypatch.setattr(module, "save_source_articles", fake_save)

    result = save_articles(articles)

    assert result.success is False
    assert result.files_saved == 1
    assert result.files_failed == 1
    assert result.articles_written == 1
    assert result.errors == [
        f"{tmp_path / 'guardian.json'}: Erreur disque"
    ]


# Chargement public

def test_load_articles_from_specific_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    raw_directory = tmp_path / "raw"
    source_directory = raw_directory / "reuters"
    source_directory.mkdir(parents=True)
    first = source_directory / "a.json"
    second = source_directory / "b.json"
    first.write_text("[]", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(module, "RAW_DATA_DIR", raw_directory)
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "reuters"
    )
    monkeypatch.setattr(
        module,
        "load_json_file",
        lambda filepath: JsonLoadResult(
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
        {"file": "a.json"},
        {"file": "b.json"}
    ]


def test_load_articles_returns_empty_for_missing_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(module, "RAW_DATA_DIR", tmp_path)
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
    raw_directory = tmp_path / "raw"
    first = raw_directory / "reuters" / "a.json"
    second = raw_directory / "guardian" / "b.json"
    corrupted = raw_directory / "bbc" / "c.json"

    for filepath in (first, second, corrupted):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("[]", encoding="utf-8")

    warnings: list[tuple[Any, ...]] = []

    def fake_load(filepath: Path) -> JsonLoadResult:
        if filepath == corrupted:
            return JsonLoadResult("corrupted", error="JSON invalide")
        return JsonLoadResult("valid", [{"file": filepath.name}])

    monkeypatch.setattr(module, "RAW_DATA_DIR", raw_directory)
    monkeypatch.setattr(module, "load_json_file", fake_load)
    monkeypatch.setattr(
        module,
        "deduplicate_articles",
        lambda articles: list(reversed(articles))
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = load_articles()

    assert result == [
        {"file": "a.json"},
        {"file": "b.json"}
    ]
    assert warnings == [
        (
            "JSON corrompu ignoré : %s (%s)",
            corrupted,
            "JSON invalide"
        )
    ]


def test_load_articles_returns_empty_when_no_valid_articles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "corrupted.json"
    filepath.write_text("invalid", encoding="utf-8")

    monkeypatch.setattr(module, "RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        module,
        "load_json_file",
        lambda path: JsonLoadResult("corrupted", error="broken")
    )

    assert load_articles() == []