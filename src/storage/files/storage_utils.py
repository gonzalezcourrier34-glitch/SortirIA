"""
Outils communs pour les écritures transactionnelles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/files/storage_utils.py` (commit 93632c5).

Ce module centralise :

- le verrouillage interprocessus ;
- la création d'un fichier temporaire unique ;
- le remplacement atomique du fichier cible ;
- le nettoyage du fichier temporaire.

La synchronisation avec flush() et os.fsync() doit être effectuée
par le module qui écrit réellement le contenu temporaire.

Ce module ne connaît ni le format JSON ni le format CSV.
"""

from __future__ import annotations

import os
import time
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from config.constants import (
    FILE_LOCK_CREATED_AT_FIELD,
    FILE_LOCK_METADATA_ENCODING,
    FILE_LOCK_PID_FIELD,
    FILE_LOCK_SUFFIX,
    TEMPORARY_FILE_SUFFIX,
)
from config.settings import STORAGE_LOCK_TIMEOUT


class FileLockError(OSError):
    """Erreur générale liée à la gestion d'un verrou."""


class FileLockTimeoutError(FileLockError, TimeoutError):
    """Levée lorsqu'un verrou ne peut pas être acquis à temps."""


class FileWriteError(OSError):
    """Levée lorsqu'une écriture transactionnelle échoue."""


class FileLock(AbstractContextManager["FileLock"]):
    """Verrou interprocessus associé à un fichier cible."""

    def __init__(
        self,
        target_path: str | Path,
        timeout_seconds: float = STORAGE_LOCK_TIMEOUT,
        poll_interval_seconds: float = 0.1,
        stale_after_seconds: float = 300.0,
    ) -> None:
        # Prépare les chemins et les délais du verrou
        self.target_path = Path(target_path)
        self.lock_path = self.target_path.with_name(f"{self.target_path.name}{FILE_LOCK_SUFFIX}")
        self.timeout_seconds = max(float(timeout_seconds), 0.0)
        self.poll_interval_seconds = max(float(poll_interval_seconds), 0.01)
        self.stale_after_seconds = max(float(stale_after_seconds), self.timeout_seconds)
        self._file_descriptor: int | None = None

    def _remove_stale_lock(self) -> None:
        """Supprime un verrou devenu trop ancien."""

        # Calcule l'âge du verrou sans conclure en cas d'erreur de lecture
        try:
            lock_age = time.time() - self.lock_path.stat().st_mtime
        except (FileNotFoundError, OSError):
            return

        if lock_age < self.stale_after_seconds:
            return

        # Supprime uniquement un verrou considéré comme obsolète
        try:
            self.lock_path.unlink()
        except (FileNotFoundError, OSError):
            pass

    def _write_metadata(self) -> None:
        """Écrit les métadonnées minimales du verrou."""

        if self._file_descriptor is None:
            raise FileLockError("Le descripteur du verrou n'est pas disponible.")

        metadata = (
            f"{FILE_LOCK_PID_FIELD}={os.getpid()}\n"
            f"{FILE_LOCK_CREATED_AT_FIELD}={time.time()}\n"
        ).encode(FILE_LOCK_METADATA_ENCODING)

        # Écrit et synchronise les métadonnées du verrou sur le disque
        try:
            os.write(self._file_descriptor, metadata)
            os.fsync(self._file_descriptor)
        except OSError as error:
            try:
                self.release()
            except FileLockError:
                pass
            raise FileLockError(f"Impossible d'écrire le verrou {self.lock_path}.") from error

    def acquire(self) -> None:
        """Acquiert le verrou ou lève une erreur après expiration."""

        # Crée le dossier contenant le verrou
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise FileLockError(
                f"Impossible de créer le dossier du verrou {self.lock_path.parent}."
            ) from error

        deadline = time.monotonic() + self.timeout_seconds

        # Tente une création exclusive jusqu'à expiration du délai
        while True:
            self._remove_stale_lock()

            try:
                self._file_descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                self._write_metadata()
                return

            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise FileLockTimeoutError(
                        f"Impossible d'acquérir le verrou {self.lock_path} dans le délai prévu."
                    )
                time.sleep(self.poll_interval_seconds)

            except FileLockError:
                raise

            except OSError as error:
                raise FileLockError(f"Impossible de créer le verrou {self.lock_path}.") from error

    def release(self) -> None:
        """Ferme le descripteur puis supprime le fichier de verrou."""

        close_error: OSError | None = None

        # Ferme le descripteur du verrou
        if self._file_descriptor is not None:
            try:
                os.close(self._file_descriptor)
            except OSError as error:
                close_error = error
            finally:
                self._file_descriptor = None

        # Supprime le fichier de verrou
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            if close_error is None:
                close_error = error

        if close_error is not None:
            raise FileLockError(f"Impossible de libérer le verrou {self.lock_path}.") from close_error

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        # Libère le verrou sans masquer une exception déjà active
        try:
            self.release()
        except FileLockError:
            if exception is None:
                raise
        return False


def create_temporary_path(target_path: str | Path) -> Path:
    """Crée un chemin temporaire unique associé au fichier cible."""

    # Ajoute un identifiant unique pour éviter les collisions entre workers
    target = Path(target_path)
    return target.with_name(f"{target.name}.{uuid4().hex}{TEMPORARY_FILE_SUFFIX}")


def remove_file_if_exists(file_path: str | Path) -> None:
    """Supprime un fichier lorsqu'il existe."""

    # Ignore uniquement l'absence normale du fichier
    try:
        Path(file_path).unlink()
    except FileNotFoundError:
        pass


def atomic_replace(temporary_path: str | Path, target_path: str | Path) -> None:
    """Remplace atomiquement le fichier cible par le temporaire."""

    temporary, target = Path(temporary_path), Path(target_path)

    # Vérifie la présence du fichier temporaire avant remplacement
    if not temporary.is_file():
        raise FileWriteError(f"Le fichier temporaire n'existe pas : {temporary}.")

    # Crée le dossier cible puis effectue le remplacement atomique
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, target)
    except OSError as error:
        raise FileWriteError(f"Impossible de remplacer atomiquement {target}.") from error


def ensure_directory_exists(path: str | Path) -> Path:
    """Crée un dossier s'il n'existe pas encore."""

    # Crée toute l'arborescence nécessaire
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def create_daily_filename(source: str, extension: str, execution_date: str) -> str:
    """Construit le nom quotidien d'un fichier de stockage."""

    # Normalise l'extension avant de construire le nom
    return f"{source}_{execution_date}.{extension.lstrip('.')}"


def build_source_directory(root_directory: str | Path, source: str) -> Path:
    """Crée et retourne le dossier associé à une source."""

    # Crée le sous-dossier propre à la source
    return ensure_directory_exists(Path(root_directory) / source)