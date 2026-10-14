"""Tests des outils communs d'écriture transactionnelle CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/files/test_storage_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.storage.files.storage_utils as module
from src.storage.files.storage_utils import (
    FileLock,
    FileLockError,
    FileLockTimeoutError,
    FileWriteError,
    atomic_replace,
    build_source_directory,
    create_daily_filename,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)


# Objets de test

class DummyParentPath:
    """Parent factice permettant de simuler mkdir()."""

    def __init__(self, error: OSError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[bool, bool]] = []

    def mkdir(self, parents: bool, exist_ok: bool) -> None:
        self.calls.append((parents, exist_ok))

        if self.error is not None:
            raise self.error

    def __str__(self) -> str:
        return "dummy-parent"


class DummyLockPath:
    """Chemin de verrou factice limité aux opérations testées."""

    def __init__(
        self,
        *,
        parent: DummyParentPath | None = None,
        modified_at: float = 0.0,
        stat_error: OSError | None = None,
        unlink_error: OSError | None = None,
        missing_on_unlink: bool = False
    ) -> None:
        self.parent = parent or DummyParentPath()
        self.modified_at = modified_at
        self.stat_error = stat_error
        self.unlink_error = unlink_error
        self.missing_on_unlink = missing_on_unlink
        self.unlink_calls = 0

    def stat(self) -> SimpleNamespace:
        if self.stat_error is not None:
            raise self.stat_error

        return SimpleNamespace(st_mtime=self.modified_at)

    def unlink(self) -> None:
        self.unlink_calls += 1

        if self.missing_on_unlink:
            raise FileNotFoundError("Verrou absent")

        if self.unlink_error is not None:
            raise self.unlink_error

    def __fspath__(self) -> str:
        return "dummy.lock"

    def __str__(self) -> str:
        return "dummy.lock"


# Exceptions

def test_file_lock_timeout_error_inherits_expected_errors() -> None:
    error = FileLockTimeoutError("timeout")

    assert isinstance(error, FileLockError)
    assert isinstance(error, TimeoutError)
    assert isinstance(error, OSError)


def test_file_write_error_inherits_os_error() -> None:
    assert isinstance(FileWriteError("write"), OSError)


# Initialisation du verrou

def test_file_lock_initializes_paths_and_delays(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(module, "FILE_LOCK_SUFFIX", ".lock")

    lock = FileLock(
        tmp_path / "articles.json",
        timeout_seconds=2.5,
        poll_interval_seconds=0.2,
        stale_after_seconds=10
    )

    assert lock.target_path == tmp_path / "articles.json"
    assert lock.lock_path == tmp_path / "articles.json.lock"
    assert lock.timeout_seconds == 2.5
    assert lock.poll_interval_seconds == 0.2
    assert lock.stale_after_seconds == 10
    assert lock._file_descriptor is None


def test_file_lock_clamps_negative_delays(tmp_path: Path) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        timeout_seconds=-5,
        poll_interval_seconds=-1,
        stale_after_seconds=-10
    )

    assert lock.timeout_seconds == 0.0
    assert lock.poll_interval_seconds == 0.01
    assert lock.stale_after_seconds == 0.0


def test_file_lock_stale_delay_is_at_least_timeout(tmp_path: Path) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        timeout_seconds=10,
        stale_after_seconds=2
    )

    assert lock.stale_after_seconds == 10


# Suppression des verrous obsolètes

def test_remove_stale_lock_returns_when_file_missing(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "articles.json")

    lock._remove_stale_lock()

    assert lock.lock_path.exists() is False


def test_remove_stale_lock_keeps_recent_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        stale_after_seconds=300
    )
    fake_path = DummyLockPath(modified_at=900.0)
    lock.lock_path = fake_path  # type: ignore[assignment]

    monkeypatch.setattr(module.time, "time", lambda: 1000.0)

    lock._remove_stale_lock()

    assert fake_path.unlink_calls == 0


def test_remove_stale_lock_deletes_old_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        stale_after_seconds=100
    )
    fake_path = DummyLockPath(modified_at=800.0)
    lock.lock_path = fake_path  # type: ignore[assignment]

    monkeypatch.setattr(module.time, "time", lambda: 1000.0)

    lock._remove_stale_lock()

    assert fake_path.unlink_calls == 1


def test_remove_stale_lock_ignores_stat_error(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "articles.json")
    fake_path = DummyLockPath(stat_error=OSError("Erreur lecture"))
    lock.lock_path = fake_path  # type: ignore[assignment]

    lock._remove_stale_lock()

    assert fake_path.unlink_calls == 0


def test_remove_stale_lock_ignores_unlink_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        stale_after_seconds=0
    )
    fake_path = DummyLockPath(
        modified_at=0.0,
        unlink_error=OSError("Erreur suppression")
    )
    lock.lock_path = fake_path  # type: ignore[assignment]

    monkeypatch.setattr(module.time, "time", lambda: 1000.0)

    lock._remove_stale_lock()

    assert fake_path.unlink_calls == 1


def test_remove_stale_lock_ignores_missing_file_during_unlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        stale_after_seconds=0
    )
    fake_path = DummyLockPath(
        modified_at=0.0,
        missing_on_unlink=True
    )
    lock.lock_path = fake_path  # type: ignore[assignment]

    monkeypatch.setattr(module.time, "time", lambda: 1000.0)

    lock._remove_stale_lock()

    assert fake_path.unlink_calls == 1


# Métadonnées

def test_write_metadata_requires_descriptor(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "articles.json")

    with pytest.raises(
        FileLockError,
        match="descripteur du verrou n'est pas disponible"
    ):
        lock._write_metadata()


def test_write_metadata_writes_expected_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock._file_descriptor = 42
    writes: list[tuple[int, bytes]] = []
    syncs: list[int] = []

    monkeypatch.setattr(module, "FILE_LOCK_PID_FIELD", "pid")
    monkeypatch.setattr(module, "FILE_LOCK_CREATED_AT_FIELD", "created_at")
    monkeypatch.setattr(module, "FILE_LOCK_METADATA_ENCODING", "utf-8")
    monkeypatch.setattr(module.os, "getpid", lambda: 1234)
    monkeypatch.setattr(module.time, "time", lambda: 1000.5)
    monkeypatch.setattr(
        module.os,
        "write",
        lambda descriptor, data: writes.append((descriptor, data)) or len(data)
    )
    monkeypatch.setattr(
        module.os,
        "fsync",
        lambda descriptor: syncs.append(descriptor)
    )

    lock._write_metadata()

    assert writes == [
        (
            42,
            b"pid=1234\ncreated_at=1000.5\n"
        )
    ]
    assert syncs == [42]


def test_write_metadata_releases_and_raises_on_write_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock._file_descriptor = 42
    released: list[bool] = []

    monkeypatch.setattr(
        module.os,
        "write",
        lambda descriptor, data: (_ for _ in ()).throw(
            OSError("Erreur écriture")
        )
    )
    monkeypatch.setattr(
        lock,
        "release",
        lambda: released.append(True)
    )

    with pytest.raises(
        FileLockError,
        match="Impossible d'écrire le verrou"
    ):
        lock._write_metadata()

    assert released == [True]


def test_write_metadata_ignores_release_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock._file_descriptor = 42

    monkeypatch.setattr(
        module.os,
        "write",
        lambda descriptor, data: (_ for _ in ()).throw(
            OSError("Erreur écriture")
        )
    )
    monkeypatch.setattr(
        lock,
        "release",
        lambda: (_ for _ in ()).throw(
            FileLockError("Erreur libération")
        )
    )

    with pytest.raises(
        FileLockError,
        match="Impossible d'écrire le verrou"
    ):
        lock._write_metadata()


def test_write_metadata_releases_and_raises_on_fsync_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock._file_descriptor = 42
    released: list[bool] = []

    monkeypatch.setattr(
        module.os,
        "write",
        lambda descriptor, data: len(data)
    )
    monkeypatch.setattr(
        module.os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(
            OSError("Erreur synchronisation")
        )
    )
    monkeypatch.setattr(
        lock,
        "release",
        lambda: released.append(True)
    )

    with pytest.raises(FileLockError):
        lock._write_metadata()

    assert released == [True]


# Acquisition

def test_acquire_creates_lock_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "subdir" / "articles.json")
    descriptors: list[int] = []
    metadata_calls: list[bool] = []

    monkeypatch.setattr(
        module.os,
        "open",
        lambda path, flags: descriptors.append(99) or 99
    )
    monkeypatch.setattr(
        lock,
        "_write_metadata",
        lambda: metadata_calls.append(True)
    )
    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    lock.acquire()

    assert lock.lock_path.parent.exists()
    assert lock._file_descriptor == 99
    assert descriptors == [99]
    assert metadata_calls == [True]


def test_acquire_uses_expected_open_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    calls: list[tuple[Any, int]] = []

    def fake_open(path: Any, flags: int) -> int:
        calls.append((path, flags))
        return 12

    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(lock, "_write_metadata", lambda: None)
    monkeypatch.setattr(module.os, "open", fake_open)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    lock.acquire()

    assert calls == [
        (
            lock.lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )
    ]


def test_acquire_raises_when_parent_creation_fails(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "subdir" / "articles.json")
    parent = DummyParentPath(OSError("Accès refusé"))
    lock.lock_path = DummyLockPath(parent=parent)  # type: ignore[assignment]

    with pytest.raises(
        FileLockError,
        match="Impossible de créer le dossier du verrou"
    ):
        lock.acquire()

    assert parent.calls == [(True, True)]


def test_acquire_retries_until_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        timeout_seconds=1,
        poll_interval_seconds=0.1
    )
    attempts = iter([
        FileExistsError(),
        FileExistsError(),
        77
    ])
    sleeps: list[float] = []
    metadata_calls: list[bool] = []
    monotonic_values = iter([0.0, 0.1, 0.2])

    def fake_open(path: Path, flags: int) -> int:
        value = next(attempts)

        if isinstance(value, Exception):
            raise value

        return value

    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(module.os, "open", fake_open)
    monkeypatch.setattr(
        lock,
        "_write_metadata",
        lambda: metadata_calls.append(True)
    )
    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: next(monotonic_values)
    )
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: sleeps.append(seconds)
    )

    lock.acquire()

    assert lock._file_descriptor == 77
    assert sleeps == [0.1, 0.1]
    assert metadata_calls == [True]


def test_acquire_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        timeout_seconds=0,
        poll_interval_seconds=0.1
    )

    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(
        module.os,
        "open",
        lambda path, flags: (_ for _ in ()).throw(
            FileExistsError()
        )
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: 1.0)

    with pytest.raises(
        FileLockTimeoutError,
        match="Impossible d'acquérir le verrou"
    ):
        lock.acquire()


def test_acquire_sleeps_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(
        tmp_path / "articles.json",
        timeout_seconds=1,
        poll_interval_seconds=0.25
    )
    attempts = iter([FileExistsError(), 8])
    sleeps: list[float] = []
    monotonic_values = iter([0.0, 0.1])

    def fake_open(path: Path, flags: int) -> int:
        value = next(attempts)

        if isinstance(value, Exception):
            raise value

        return value

    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(lock, "_write_metadata", lambda: None)
    monkeypatch.setattr(module.os, "open", fake_open)
    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: next(monotonic_values)
    )
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: sleeps.append(seconds)
    )

    lock.acquire()

    assert sleeps == [0.25]


def test_acquire_propagates_file_lock_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")

    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(module.os, "open", lambda path, flags: 42)
    monkeypatch.setattr(
        lock,
        "_write_metadata",
        lambda: (_ for _ in ()).throw(
            FileLockError("Erreur metadata")
        )
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    with pytest.raises(FileLockError, match="Erreur metadata"):
        lock.acquire()


def test_acquire_wraps_os_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")

    monkeypatch.setattr(lock, "_remove_stale_lock", lambda: None)
    monkeypatch.setattr(
        module.os,
        "open",
        lambda path, flags: (_ for _ in ()).throw(
            PermissionError("Accès refusé")
        )
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)

    with pytest.raises(
        FileLockError,
        match="Impossible de créer le verrou"
    ):
        lock.acquire()


# Libération

def test_release_closes_descriptor_and_removes_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock.lock_path.write_text("lock", encoding="utf-8")
    lock._file_descriptor = 42
    closed: list[int] = []

    monkeypatch.setattr(
        module.os,
        "close",
        lambda descriptor: closed.append(descriptor)
    )

    lock.release()

    assert closed == [42]
    assert lock._file_descriptor is None
    assert lock.lock_path.exists() is False


def test_release_without_descriptor_removes_lock(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock.lock_path.write_text("lock", encoding="utf-8")

    lock.release()

    assert lock._file_descriptor is None
    assert lock.lock_path.exists() is False


def test_release_ignores_missing_lock_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock._file_descriptor = 42

    monkeypatch.setattr(module.os, "close", lambda descriptor: None)

    lock.release()

    assert lock._file_descriptor is None


def test_release_raises_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock.lock_path.write_text("lock", encoding="utf-8")
    lock._file_descriptor = 42

    monkeypatch.setattr(
        module.os,
        "close",
        lambda descriptor: (_ for _ in ()).throw(
            OSError("Erreur fermeture")
        )
    )

    with pytest.raises(
        FileLockError,
        match="Impossible de libérer le verrou"
    ):
        lock.release()

    assert lock._file_descriptor is None
    assert lock.lock_path.exists() is False


def test_release_raises_when_unlink_fails(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "articles.json")
    fake_path = DummyLockPath(
        unlink_error=OSError("Erreur suppression")
    )
    lock.lock_path = fake_path  # type: ignore[assignment]

    with pytest.raises(
        FileLockError,
        match="Impossible de libérer le verrou"
    ):
        lock.release()

    assert fake_path.unlink_calls == 1


def test_release_keeps_close_error_when_unlink_also_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    lock._file_descriptor = 42
    fake_path = DummyLockPath(
        unlink_error=OSError("Erreur suppression")
    )
    lock.lock_path = fake_path  # type: ignore[assignment]

    monkeypatch.setattr(
        module.os,
        "close",
        lambda descriptor: (_ for _ in ()).throw(
            OSError("Erreur fermeture")
        )
    )

    with pytest.raises(FileLockError) as error:
        lock.release()

    assert isinstance(error.value.__cause__, OSError)
    assert str(error.value.__cause__) == "Erreur fermeture"
    assert fake_path.unlink_calls == 1


def test_release_ignores_missing_lock_with_fake_path(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "articles.json")
    fake_path = DummyLockPath(missing_on_unlink=True)
    lock.lock_path = fake_path  # type: ignore[assignment]

    lock.release()

    assert fake_path.unlink_calls == 1


# Gestionnaire de contexte

def test_file_lock_context_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")
    calls: list[str] = []

    monkeypatch.setattr(
        lock,
        "acquire",
        lambda: calls.append("acquire")
    )
    monkeypatch.setattr(
        lock,
        "release",
        lambda: calls.append("release")
    )

    with lock as acquired:
        assert acquired is lock
        calls.append("body")

    assert calls == ["acquire", "body", "release"]


def test_file_lock_exit_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")

    monkeypatch.setattr(lock, "release", lambda: None)

    result = lock.__exit__(None, None, None)

    assert result is False


def test_file_lock_exit_does_not_mask_active_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")

    monkeypatch.setattr(lock, "acquire", lambda: None)
    monkeypatch.setattr(
        lock,
        "release",
        lambda: (_ for _ in ()).throw(
            FileLockError("Erreur libération")
        )
    )

    with pytest.raises(ValueError, match="Erreur métier"):
        with lock:
            raise ValueError("Erreur métier")


def test_file_lock_exit_raises_release_error_without_active_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "articles.json")

    monkeypatch.setattr(lock, "acquire", lambda: None)
    monkeypatch.setattr(
        lock,
        "release",
        lambda: (_ for _ in ()).throw(
            FileLockError("Erreur libération")
        )
    )

    with pytest.raises(FileLockError, match="Erreur libération"):
        with lock:
            pass


# Chemins temporaires

def test_create_temporary_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(module, "TEMPORARY_FILE_SUFFIX", ".tmp")
    monkeypatch.setattr(
        module,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef123456")
    )

    result = create_temporary_path(tmp_path / "articles.json")

    assert result == tmp_path / "articles.json.abcdef123456.tmp"


def test_create_temporary_path_accepts_string(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "TEMPORARY_FILE_SUFFIX", ".tmp")
    monkeypatch.setattr(
        module,
        "uuid4",
        lambda: SimpleNamespace(hex="1234")
    )

    result = create_temporary_path("data/articles.csv")

    assert result == Path("data/articles.csv.1234.tmp")


def test_create_temporary_path_produces_unique_names(
    tmp_path: Path
) -> None:
    first = create_temporary_path(tmp_path / "articles.json")
    second = create_temporary_path(tmp_path / "articles.json")

    assert first != second
    assert first.parent == second.parent == tmp_path


# Suppression

def test_remove_file_if_exists_deletes_file(tmp_path: Path) -> None:
    filepath = tmp_path / "temporary.tmp"
    filepath.write_text("data", encoding="utf-8")

    remove_file_if_exists(filepath)

    assert filepath.exists() is False


def test_remove_file_if_exists_ignores_missing_file(tmp_path: Path) -> None:
    remove_file_if_exists(tmp_path / "missing.tmp")


def test_remove_file_if_exists_propagates_other_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    filepath = tmp_path / "temporary.tmp"
    filepath.write_text("data", encoding="utf-8")

    def fake_unlink() -> None:
        raise PermissionError("Accès refusé")

    fake_path = SimpleNamespace(unlink=fake_unlink)

    monkeypatch.setattr(module, "Path", lambda value: fake_path)

    with pytest.raises(PermissionError, match="Accès refusé"):
        remove_file_if_exists(filepath)


# Remplacement atomique

def test_atomic_replace_rejects_missing_temporary_file(
    tmp_path: Path
) -> None:
    temporary = tmp_path / "missing.tmp"
    target = tmp_path / "target.json"

    with pytest.raises(
        FileWriteError,
        match="Le fichier temporaire n'existe pas"
    ):
        atomic_replace(temporary, target)


def test_atomic_replace_rejects_temporary_directory(
    tmp_path: Path
) -> None:
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    target = tmp_path / "target.json"

    with pytest.raises(
        FileWriteError,
        match="Le fichier temporaire n'existe pas"
    ):
        atomic_replace(temporary, target)


def test_atomic_replace_creates_directory_and_replaces(
    tmp_path: Path
) -> None:
    temporary = tmp_path / "temporary.tmp"
    target = tmp_path / "nested" / "target.json"
    temporary.write_text("new content", encoding="utf-8")

    atomic_replace(temporary, target)

    assert temporary.exists() is False
    assert target.read_text(encoding="utf-8") == "new content"


def test_atomic_replace_overwrites_existing_target(
    tmp_path: Path
) -> None:
    temporary = tmp_path / "temporary.tmp"
    target = tmp_path / "target.json"
    temporary.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")

    atomic_replace(temporary, target)

    assert temporary.exists() is False
    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_replace_wraps_directory_creation_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary = SimpleNamespace(is_file=lambda: True)
    parent = DummyParentPath(OSError("Accès refusé"))
    target = SimpleNamespace(
        parent=parent,
        __str__=lambda self: "target.json"
    )
    paths = iter([temporary, target])

    monkeypatch.setattr(module, "Path", lambda value: next(paths))

    with pytest.raises(
        FileWriteError,
        match="Impossible de remplacer atomiquement"
    ):
        atomic_replace("temporary.tmp", "target.json")


def test_atomic_replace_wraps_os_replace_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    temporary = tmp_path / "temporary.tmp"
    target = tmp_path / "target.json"
    temporary.write_text("data", encoding="utf-8")

    monkeypatch.setattr(
        module.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(
            OSError("Erreur remplacement")
        )
    )

    with pytest.raises(
        FileWriteError,
        match="Impossible de remplacer atomiquement"
    ):
        atomic_replace(temporary, target)


# Dossiers

def test_ensure_directory_exists_creates_directory(
    tmp_path: Path
) -> None:
    directory = tmp_path / "nested" / "directory"

    result = ensure_directory_exists(directory)

    assert result == directory
    assert directory.is_dir()


def test_ensure_directory_exists_accepts_existing_directory(
    tmp_path: Path
) -> None:
    directory = tmp_path / "existing"
    directory.mkdir()

    result = ensure_directory_exists(directory)

    assert result == directory


def test_ensure_directory_exists_accepts_string(
    tmp_path: Path
) -> None:
    directory = tmp_path / "string-directory"

    result = ensure_directory_exists(str(directory))

    assert result == directory
    assert directory.exists()


def test_ensure_directory_exists_propagates_os_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = DummyParentPath(OSError("Accès refusé"))

    monkeypatch.setattr(module, "Path", lambda value: parent)

    with pytest.raises(OSError, match="Accès refusé"):
        ensure_directory_exists("forbidden")


# Noms de fichiers

@pytest.mark.parametrize(
    ("source", "extension", "execution_date", "expected"),
    [
        ("reuters", "json", "20260727", "reuters_20260727.json"),
        ("guardian", ".csv", "20260727", "guardian_20260727.csv"),
        ("le_monde", "..json", "20260727", "le_monde_20260727.json"),
        ("source", "", "20260727", "source_20260727.")
    ]
)
def test_create_daily_filename(
    source: str,
    extension: str,
    execution_date: str,
    expected: str
) -> None:
    assert create_daily_filename(
        source,
        extension,
        execution_date
    ) == expected


# Dossiers par source

def test_build_source_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    expected = tmp_path / "reuters"
    calls: list[Path] = []

    def fake_ensure(path: Path) -> Path:
        calls.append(path)
        return expected

    monkeypatch.setattr(module, "ensure_directory_exists", fake_ensure)

    result = build_source_directory(tmp_path, "reuters")

    assert result == expected
    assert calls == [expected]


def test_build_source_directory_accepts_string_root(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Path] = []

    def fake_ensure(path: Path) -> Path:
        captured.append(path)
        return path

    monkeypatch.setattr(module, "ensure_directory_exists", fake_ensure)

    result = build_source_directory("data/processed", "guardian")

    assert result == Path("data/processed") / "guardian"
    assert captured == [Path("data/processed") / "guardian"]


def test_build_source_directory_creates_real_directory(
    tmp_path: Path
) -> None:
    result = build_source_directory(tmp_path, "guardian")

    assert result == tmp_path / "guardian"
    assert result.is_dir()