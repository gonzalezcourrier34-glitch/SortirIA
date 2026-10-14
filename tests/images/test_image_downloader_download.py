"""Tests du téléchargement unitaire des images CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_downloader_download.py` (commit 93632c5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

import src.images.image_downloader as module
from src.images.image_downloader import (
    IMAGE_STATUS_ALREADY_AVAILABLE,
    IMAGE_STATUS_BLOCKED_REDIRECT,
    IMAGE_STATUS_BLOCKED_URL,
    IMAGE_STATUS_DOWNLOADED,
    IMAGE_STATUS_HTTP_ERROR,
    IMAGE_STATUS_INVALID_ARTICLE,
    IMAGE_STATUS_INVALID_CONTENT,
    IMAGE_STATUS_MISSING_URL,
    IMAGE_STATUS_PROCESSING_ERROR,
    IMAGE_STATUS_REDIRECT_ERROR,
    IMAGE_STATUS_TIMEOUT,
    IMAGE_STATUS_TOO_LARGE,
    IMAGE_STATUS_UNEXPECTED_ERROR,
    IMAGE_STATUS_WRITE_ERROR,
    ImageHttpError,
    ImageTooLargeError,
    InvalidImageContentError,
    UnsafeImageUrlError,
    download_image
)


# Faux objets

class FakeResponse:
    """Simule une réponse HTTP utilisable comme context manager."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self) -> "FakeResponse":
        self.entered = True
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any
    ) -> None:
        self.exited = True


class FakeMetadata:
    """Simule les métadonnées d'une image validée."""

    def __init__(
        self,
        *,
        image_format: str = "JPEG",
        mime_type: str = "image/jpeg",
        width: int = 800,
        height: int = 600,
        aspect_ratio: float = 1.3333,
        mode: str = "RGB",
        file_size: int = 50_000,
        file_hash: str = "hash-123"
    ) -> None:
        self.format = image_format
        self.mime_type = mime_type
        self.width = width
        self.height = height
        self.aspect_ratio = aspect_ratio
        self.mode = mode
        self.file_size = file_size
        self.file_hash = file_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "mode": self.mode,
            "file_size": self.file_size,
            "file_hash": self.file_hash
        }


class FakeFileLock:
    """Simule le verrouillage du fichier final."""

    entered_paths: list[Path] = []

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> "FakeFileLock":
        self.entered_paths.append(self.path)
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any
    ) -> None:
        return None


# Utilitaires

def build_article() -> dict[str, Any]:
    """Construit un article valide pour les tests."""

    return {
        "id": "article-123",
        "source": "Reuters Fact Check",
        "title": "Titre de test",
        "url": "https://example.com/article",
        "image_url": "https://cdn.example.com/image.jpg"
    }


def configure_successful_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> tuple[FakeResponse, FakeMetadata, Path, Path]:
    """Configure toutes les dépendances nécessaires au scénario nominal."""

    response = FakeResponse()
    metadata = FakeMetadata()
    target_directory = tmp_path / "reuters_fact_check"
    temporary_path = target_directory / "temporary.download"
    final_path = target_directory / "reuters_fact_check_article-123.jpg"

    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        tmp_path
    )
    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (True, "")
    )
    monkeypatch.setattr(
        module,
        "ensure_directory_exists",
        lambda path: target_directory
    )
    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: temporary_path
    )
    monkeypatch.setattr(
        module,
        "get_http_session",
        lambda: object()
    )
    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda session, url, referer="": response
    )
    monkeypatch.setattr(
        module,
        "classify_http_response",
        lambda received_response: None
    )
    monkeypatch.setattr(
        module,
        "validate_response_headers",
        lambda received_response: None
    )
    monkeypatch.setattr(
        module,
        "stream_response_to_file",
        lambda received_response, path: 50_000
    )
    monkeypatch.setattr(
        module,
        "inspect_downloaded_image",
        lambda path: metadata
    )
    monkeypatch.setattr(
        module,
        "build_image_path",
        lambda directory, source, article_id, extension: final_path
    )
    monkeypatch.setattr(
        module,
        "FileLock",
        FakeFileLock
    )
    monkeypatch.setattr(
        module,
        "atomic_replace",
        lambda source, destination: None
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: metadata
    )
    monkeypatch.setattr(
        module,
        "get_current_datetime_iso",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: None
    )
    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: 10.0
    )

    return response, metadata, temporary_path, final_path


# Entrées invalides

@pytest.mark.parametrize(
    "article",
    [
        None,
        "",
        [],
        (),
        123,
        object()
    ]
)
def test_download_image_rejects_non_dictionary(
    article: Any
) -> None:
    assert download_image(article) == {}


def test_download_image_does_not_modify_original_article() -> None:
    article = {
        "id": "",
        "source": "test",
        "image_url": ""
    }
    original = article.copy()

    result = download_image(article)

    assert article == original
    assert result is not article


# Image déjà disponible

def test_download_image_keeps_existing_image_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "article-1",
        "source": "test",
        "image_url": "https://example.com/image.jpg",
        "image_path": "data/images/existing.jpg",
        "image_download_error": "ancienne erreur"
    }

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: pytest.fail(
            "L'URL ne devait pas être validée."
        )
    )

    result = download_image(article)

    assert result["image_path"] == "data/images/existing.jpg"
    assert result["image_download_status"] == (
        IMAGE_STATUS_ALREADY_AVAILABLE
    )
    assert result["image_download_error"] == ""


def test_download_image_normalizes_existing_image_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "article-1",
        "source": "test",
        "image_path": "  data/images/existing.jpg  ",
        "image_url": "https://example.com/image.jpg"
    }

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: pytest.fail(
            "L'URL ne devait pas être validée."
        )
    )

    result = download_image(article)

    assert result["image_download_status"] == (
        IMAGE_STATUS_ALREADY_AVAILABLE
    )


# Identifiant et URL manquants

@pytest.mark.parametrize(
    "article_id",
    [
        None,
        "",
        " ",
        "///***"
    ]
)
def test_download_image_rejects_missing_article_id(
    monkeypatch: pytest.MonkeyPatch,
    article_id: Any
) -> None:
    article = {
        "id": article_id,
        "source": "test",
        "image_url": "https://example.com/image.jpg"
    }

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: pytest.fail(
            "L'URL ne devait pas être validée."
        )
    )

    result = download_image(article)

    assert result["image_path"] == ""
    assert result["image_download_status"] == (
        IMAGE_STATUS_INVALID_ARTICLE
    )
    assert result["image_download_error"] == (
        "Identifiant article absent."
    )


@pytest.mark.parametrize(
    "image_url",
    [
        None,
        "",
        " "
    ]
)
def test_download_image_handles_missing_image_url(
    monkeypatch: pytest.MonkeyPatch,
    image_url: Any
) -> None:
    article = {
        "id": "article-1",
        "source": "test",
        "image_url": image_url
    }

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: pytest.fail(
            "La validation de sécurité ne devait pas être appelée."
        )
    )

    result = download_image(article)

    assert result["image_path"] == ""
    assert result["image_download_status"] == (
        IMAGE_STATUS_MISSING_URL
    )
    assert result["image_download_error"] == ""


# URL bloquée

def test_download_image_blocks_unsafe_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: (
            False,
            "Destination interne interdite."
        )
    )
    monkeypatch.setattr(
        module,
        "ensure_directory_exists",
        lambda path: pytest.fail(
            "Le dossier ne devait pas être créé."
        )
    )

    result = download_image(article)

    assert result["image_path"] == ""
    assert result["image_download_status"] == IMAGE_STATUS_BLOCKED_URL
    assert result["image_download_error"] == (
        "Destination interne interdite."
    )


# Scénario nominal

def test_download_image_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    response, metadata, temporary_path, final_path = (
        configure_successful_download(
            monkeypatch,
            tmp_path
        )
    )

    counter_values = iter([
        10.0,
        10.125
    ])
    monkeypatch.setattr(
        module,
        "perf_counter",
        lambda: next(counter_values)
    )

    removed_paths: list[Path] = []
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed_paths.append(path)
    )

    replaced_paths: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        module,
        "atomic_replace",
        lambda source, destination: replaced_paths.append(
            (source, destination)
        )
    )

    result = download_image(build_article())

    assert response.entered is True
    assert response.exited is True
    assert replaced_paths == [
        (
            temporary_path,
            final_path
        )
    ]
    assert FakeFileLock.entered_paths[-1] == final_path
    assert removed_paths == [temporary_path]

    assert result["image_path"] == str(final_path)
    assert result["image_download_status"] == IMAGE_STATUS_DOWNLOADED
    assert result["image_download_error"] == ""
    assert result["image_format"] == "JPEG"
    assert result["image_mime_type"] == "image/jpeg"
    assert result["image_width"] == 800
    assert result["image_height"] == 600
    assert result["image_size_bytes"] == 50_000
    assert result["image_file_hash"] == "hash-123"
    assert result["image_download_duration_ms"] == 125
    assert result["image_downloaded_at"] == (
        "2026-07-27T12:00:00+00:00"
    )
    assert result["image_metadata"]["download_duration_ms"] == 125


def test_download_image_uses_normalized_source_and_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    calls: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda source: "normalized_source"
    )
    monkeypatch.setattr(
        module,
        "ensure_directory_exists",
        lambda path: calls.setdefault(
            "directory",
            path
        ) or path
    )
    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: calls.setdefault(
            "temporary",
            path
        ) or path
    )
    monkeypatch.setattr(
        module,
        "build_image_path",
        lambda directory, source, article_id, extension: (
            calls.update({
                "build_directory": directory,
                "source": source,
                "article_id": article_id,
                "extension": extension
            })
            or tmp_path / "final.jpg"
        )
    )

    article = build_article()
    article["id"] = "Article / 123"
    article["source"] = "Source Test"

    result = download_image(article)

    assert calls["directory"] == tmp_path / "normalized_source"
    assert calls["source"] == "normalized_source"
    assert calls["article_id"] == "Article_123"
    assert calls["extension"] == ".jpg"
    assert result["image_download_status"] == IMAGE_STATUS_DOWNLOADED


def test_download_image_passes_referer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    calls: list[dict[str, Any]] = []

    def fake_request(
        session: Any,
        image_url: str,
        referer: str = ""
    ) -> FakeResponse:
        calls.append({
            "session": session,
            "image_url": image_url,
            "referer": referer
        })
        return FakeResponse()

    fake_session = object()

    monkeypatch.setattr(
        module,
        "get_http_session",
        lambda: fake_session
    )
    monkeypatch.setattr(
        module,
        "request_image_response",
        fake_request
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_DOWNLOADED
    assert calls == [
        {
            "session": fake_session,
            "image_url": "https://cdn.example.com/image.jpg",
            "referer": "https://example.com/article"
        }
    ]


def test_download_image_calls_processing_steps_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    response, metadata, temporary_path, final_path = (
        configure_successful_download(
            monkeypatch,
            tmp_path
        )
    )
    calls: list[str] = []

    monkeypatch.setattr(
        module,
        "classify_http_response",
        lambda received_response: calls.append("classify")
    )
    monkeypatch.setattr(
        module,
        "validate_response_headers",
        lambda received_response: calls.append("headers")
    )
    monkeypatch.setattr(
        module,
        "stream_response_to_file",
        lambda received_response, path: (
            calls.append("stream")
            or 50_000
        )
    )
    monkeypatch.setattr(
        module,
        "inspect_downloaded_image",
        lambda path: (
            calls.append("inspect_temporary")
            or metadata
        )
    )
    monkeypatch.setattr(
        module,
        "atomic_replace",
        lambda source, destination: calls.append("replace")
    )
    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: (
            calls.append("inspect_final")
            or metadata
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_DOWNLOADED
    assert calls == [
        "classify",
        "headers",
        "stream",
        "inspect_temporary",
        "replace",
        "inspect_final"
    ]
    assert temporary_path != final_path


# Image finale invalide

def test_download_image_removes_invalid_final_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    _, _, temporary_path, final_path = configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "inspect_image",
        lambda path: None
    )

    removed_paths: list[Path] = []
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed_paths.append(path)
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_INVALID_CONTENT
    )
    assert "image finale est invalide" in (
        result["image_download_error"].lower()
    )
    assert removed_paths == [
        final_path,
        temporary_path
    ]


# URL et redirections

def test_download_image_handles_unsafe_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    _, _, temporary_path, _ = configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            UnsafeImageUrlError(
                "Redirection interne bloquée.",
                status=IMAGE_STATUS_BLOCKED_REDIRECT
            )
        )
    )

    removed_paths: list[Path] = []
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed_paths.append(path)
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_BLOCKED_REDIRECT
    )
    assert result["image_download_error"] == (
        "Redirection interne bloquée."
    )
    assert removed_paths == [temporary_path]


def test_download_image_handles_too_many_redirects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            requests.TooManyRedirects(
                "Trop de redirections."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_REDIRECT_ERROR
    )
    assert result["image_download_error"] == (
        "Trop de redirections."
    )


# Taille et contenu

def test_download_image_handles_too_large_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "stream_response_to_file",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            ImageTooLargeError(
                "Image supérieure à la limite."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_TOO_LARGE
    assert result["image_download_error"] == (
        "Image supérieure à la limite."
    )


def test_download_image_handles_invalid_headers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "validate_response_headers",
        lambda response: (
            _ for _ in ()
        ).throw(
            InvalidImageContentError(
                "Content-Type inattendu : text/html"
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_INVALID_CONTENT
    )
    assert "text/html" in result["image_download_error"]


def test_download_image_handles_invalid_stream_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "stream_response_to_file",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            InvalidImageContentError(
                "La ressource contient une page HTML."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_INVALID_CONTENT
    )
    assert result["image_download_error"] == (
        "La ressource contient une page HTML."
    )


def test_download_image_handles_invalid_inspected_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "inspect_downloaded_image",
        lambda path: (
            _ for _ in ()
        ).throw(
            InvalidImageContentError(
                "Format non pris en charge."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_INVALID_CONTENT
    )
    assert result["image_download_error"] == (
        "Format non pris en charge."
    )


# Timeout et HTTP

def test_download_image_handles_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            requests.Timeout(
                "Délai dépassé."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_TIMEOUT
    assert result["image_download_error"] == "Délai dépassé."


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (
            module.IMAGE_STATUS_NOT_FOUND,
            "Image distante introuvable."
        ),
        (
            module.IMAGE_STATUS_FORBIDDEN,
            "Accès interdit."
        ),
        (
            module.IMAGE_STATUS_QUOTA_EXCEEDED,
            "Quota dépassé."
        ),
        (
            module.IMAGE_STATUS_SERVER_ERROR,
            "Erreur serveur."
        ),
        (
            IMAGE_STATUS_HTTP_ERROR,
            "Erreur HTTP."
        )
    ]
)
def test_download_image_handles_classified_http_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    message: str
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "classify_http_response",
        lambda response: (
            _ for _ in ()
        ).throw(
            ImageHttpError(
                status,
                message
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == status
    assert result["image_download_error"] == message


@pytest.mark.parametrize(
    "exception",
    [
        requests.ConnectionError("Connexion impossible."),
        requests.HTTPError("Erreur HTTP requests."),
        requests.RequestException("Erreur réseau.")
    ]
)
def test_download_image_handles_request_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception: requests.RequestException
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(exception)
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_HTTP_ERROR
    assert result["image_download_error"] == str(exception)


# Erreurs de traitement

@pytest.mark.parametrize(
    "exception",
    [
        ValueError("Valeur invalide."),
        ValueError("Extension inconnue.")
    ]
)
def test_download_image_handles_value_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception: ValueError
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "build_image_path",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(exception)
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_PROCESSING_ERROR
    )
    assert result["image_download_error"] == str(exception)


@pytest.mark.parametrize(
    "operation",
    [
        "atomic_replace"
    ]
)
def test_download_image_handles_os_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        operation,
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            OSError(
                f"Erreur disque dans {operation}."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_WRITE_ERROR
    assert result["image_download_error"] == (
        f"Erreur disque dans {operation}."
    )

def test_download_image_propagates_directory_creation_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "ensure_directory_exists",
        lambda path: (
            _ for _ in ()
        ).throw(
            OSError(
                "Erreur disque dans ensure_directory_exists."
            )
        )
    )

    with pytest.raises(
        OSError,
        match="Erreur disque dans ensure_directory_exists"
    ):
        download_image(build_article())


def test_download_image_propagates_temporary_path_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: (
            _ for _ in ()
        ).throw(
            OSError(
                "Erreur disque dans create_temporary_path."
            )
        )
    )

    with pytest.raises(
        OSError,
        match="Erreur disque dans create_temporary_path"
    ):
        download_image(build_article())
        
def test_download_image_handles_file_lock_os_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    class FailingFileLock:
        def __init__(self, path: Path) -> None:
            self.path = path

        def __enter__(self) -> None:
            raise OSError("Impossible de verrouiller le fichier.")

        def __exit__(
            self,
            exc_type: Any,
            exc_value: Any,
            traceback: Any
        ) -> None:
            return None

    monkeypatch.setattr(
        module,
        "FileLock",
        FailingFileLock
    )

    result = download_image(build_article())

    assert result["image_download_status"] == IMAGE_STATUS_WRITE_ERROR
    assert result["image_download_error"] == (
        "Impossible de verrouiller le fichier."
    )


def test_download_image_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(
            RuntimeError(
                "Erreur inattendue de test."
            )
        )
    )

    result = download_image(build_article())

    assert result["image_download_status"] == (
        IMAGE_STATUS_UNEXPECTED_ERROR
    )
    assert result["image_download_error"] == (
        "Erreur inattendue de test."
    )


# Nettoyage du temporaire

@pytest.mark.parametrize(
    "exception",
    [
        ImageTooLargeError("Trop grande."),
        InvalidImageContentError("Invalide."),
        requests.Timeout("Timeout."),
        requests.ConnectionError("Connexion."),
        ValueError("Valeur."),
        OSError("Disque."),
        RuntimeError("Inattendue.")
    ]
)
def test_download_image_always_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception: Exception
) -> None:
    _, _, temporary_path, _ = configure_successful_download(
        monkeypatch,
        tmp_path
    )

    monkeypatch.setattr(
        module,
        "request_image_response",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(exception)
    )

    removed_paths: list[Path] = []
    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed_paths.append(path)
    )

    download_image(build_article())

    assert removed_paths == [temporary_path]


def test_download_image_does_not_remove_before_download_setup(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    removed_paths: list[Path] = []

    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed_paths.append(path)
    )

    article = {
        "id": "",
        "source": "test",
        "image_url": "https://example.com/image.jpg"
    }

    result = download_image(article)

    assert result["image_download_status"] == (
        IMAGE_STATUS_INVALID_ARTICLE
    )
    assert removed_paths == []


# Valeurs initiales

def test_download_image_resets_previous_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "article-1",
        "source": "test",
        "image_url": "",
        "image_download_status": IMAGE_STATUS_DOWNLOADED,
        "image_download_error": "ancienne erreur"
    }

    monkeypatch.setattr(
        module,
        "validate_remote_image_url",
        lambda url: pytest.fail(
            "La validation ne devait pas être appelée."
        )
    )

    result = download_image(article)

    assert result["image_download_status"] == IMAGE_STATUS_MISSING_URL
    assert result["image_download_error"] == ""


def test_download_image_sets_empty_path_before_processing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "article-1",
        "source": "test",
        "image_url": ""
    }

    result = download_image(article)

    assert "image_path" in result
    assert result["image_path"] == ""