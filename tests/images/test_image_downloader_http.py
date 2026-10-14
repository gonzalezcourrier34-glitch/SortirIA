"""Tests HTTP du téléchargement des images CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_downloader_http.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

import src.images.image_downloader as module
from src.images.image_downloader import (
    IMAGE_STATUS_FORBIDDEN,
    IMAGE_STATUS_HTTP_ERROR,
    IMAGE_STATUS_NOT_FOUND,
    IMAGE_STATUS_QUOTA_EXCEEDED,
    IMAGE_STATUS_SERVER_ERROR,
    ImageHttpError,
    ImageTooLargeError,
    InvalidImageContentError,
    classify_http_response,
    get_content_length,
    get_content_type,
    looks_like_html,
    validate_response_headers
)


# Faux objets HTTP

class FakeResponse:
    """Simule une réponse HTTP minimale."""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, Any] | None = None
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}


# Classification HTTP

@pytest.mark.parametrize(
    "status_code",
    [
        200,
        201,
        204,
        206,
        299
    ]
)
def test_classify_http_response_accepts_success_status(
    status_code: int
) -> None:
    response = FakeResponse(status_code=status_code)

    classify_http_response(
        response  # type: ignore[arg-type]
    )


def test_classify_http_response_classifies_not_found() -> None:
    response = FakeResponse(status_code=404)

    with pytest.raises(
        ImageHttpError,
        match="Image distante introuvable"
    ) as error_info:
        classify_http_response(
            response  # type: ignore[arg-type]
        )

    assert error_info.value.status == IMAGE_STATUS_NOT_FOUND


@pytest.mark.parametrize(
    "status_code",
    [
        401,
        403
    ]
)
def test_classify_http_response_classifies_forbidden(
    status_code: int
) -> None:
    response = FakeResponse(status_code=status_code)

    with pytest.raises(
        ImageHttpError,
        match=f"HTTP {status_code}"
    ) as error_info:
        classify_http_response(
            response  # type: ignore[arg-type]
        )

    assert error_info.value.status == IMAGE_STATUS_FORBIDDEN


def test_classify_http_response_classifies_quota_error() -> None:
    response = FakeResponse(status_code=429)

    with pytest.raises(
        ImageHttpError,
        match="Quota distant dépassé"
    ) as error_info:
        classify_http_response(
            response  # type: ignore[arg-type]
        )

    assert error_info.value.status == IMAGE_STATUS_QUOTA_EXCEEDED


@pytest.mark.parametrize(
    "status_code",
    [
        500,
        502,
        503,
        504,
        599
    ]
)
def test_classify_http_response_classifies_server_error(
    status_code: int
) -> None:
    response = FakeResponse(status_code=status_code)

    with pytest.raises(
        ImageHttpError,
        match=f"HTTP {status_code}"
    ) as error_info:
        classify_http_response(
            response  # type: ignore[arg-type]
        )

    assert error_info.value.status == IMAGE_STATUS_SERVER_ERROR


@pytest.mark.parametrize(
    "status_code",
    [
        300,
        304,
        400,
        405,
        409,
        410,
        418,
        422,
        499
    ]
)
def test_classify_http_response_classifies_generic_http_error(
    status_code: int
) -> None:
    response = FakeResponse(status_code=status_code)

    with pytest.raises(
        ImageHttpError,
        match=f"HTTP {status_code}"
    ) as error_info:
        classify_http_response(
            response  # type: ignore[arg-type]
        )

    assert error_info.value.status == IMAGE_STATUS_HTTP_ERROR


def test_classify_http_response_converts_status_to_integer() -> None:
    response = FakeResponse(status_code="404")  # type: ignore[arg-type]

    with pytest.raises(ImageHttpError) as error_info:
        classify_http_response(
            response  # type: ignore[arg-type]
        )

    assert error_info.value.status == IMAGE_STATUS_NOT_FOUND


def test_classify_http_response_rejects_invalid_status() -> None:
    response = FakeResponse(status_code="invalid")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        classify_http_response(
            response  # type: ignore[arg-type]
        )


# Exception HTTP métier

def test_image_http_error_stores_status_and_message() -> None:
    error = ImageHttpError(
        IMAGE_STATUS_HTTP_ERROR,
        "Erreur HTTP de test."
    )

    assert str(error) == "Erreur HTTP de test."
    assert error.status == IMAGE_STATUS_HTTP_ERROR
    assert isinstance(error, requests.HTTPError)


# Type de contenu

@pytest.mark.parametrize(
    ("header_value", "expected"),
    [
        (
            "image/jpeg",
            "image/jpeg"
        ),
        (
            "image/png; charset=binary",
            "image/png"
        ),
        (
            " IMAGE/WEBP ",
            "image/webp"
        ),
        (
            "application/octet-stream",
            "application/octet-stream"
        ),
        (
            "text/html; charset=utf-8",
            "text/html"
        ),
        (
            "",
            ""
        )
    ]
)
def test_get_content_type(
    header_value: str,
    expected: str
) -> None:
    response = FakeResponse(
        headers={
            "Content-Type": header_value
        }
    )

    result = get_content_type(
        response  # type: ignore[arg-type]
    )

    assert result == expected


def test_get_content_type_without_header() -> None:
    response = FakeResponse(headers={})

    result = get_content_type(
        response  # type: ignore[arg-type]
    )

    assert result == ""


# Taille annoncée

@pytest.mark.parametrize(
    ("header_value", "expected"),
    [
        (
            "0",
            0
        ),
        (
            "1",
            1
        ),
        (
            "1024",
            1024
        ),
        (
            2048,
            2048
        ),
        (
            "-500",
            0
        ),
        (
            "",
            0
        ),
        (
            "invalid",
            0
        ),
        (
            None,
            0
        )
    ]
)
def test_get_content_length(
    header_value: Any,
    expected: int
) -> None:
    response = FakeResponse(
        headers={
            "Content-Length": header_value
        }
    )

    result = get_content_length(
        response  # type: ignore[arg-type]
    )

    assert result == expected


def test_get_content_length_without_header() -> None:
    response = FakeResponse(headers={})

    result = get_content_length(
        response  # type: ignore[arg-type]
    )

    assert result == 0


def test_get_content_length_handles_overflow() -> None:
    class OverflowingValue:
        def __int__(self) -> int:
            raise OverflowError("valeur trop grande")

    response = FakeResponse(
        headers={
            "Content-Length": OverflowingValue()
        }
    )

    result = get_content_length(
        response  # type: ignore[arg-type]
    )

    assert result == 0


def test_get_content_length_handles_type_error() -> None:
    response = FakeResponse(
        headers={
            "Content-Length": object()
        }
    )

    result = get_content_length(
        response  # type: ignore[arg-type]
    )

    assert result == 0


# Validation des en-têtes

@pytest.mark.parametrize(
    "content_type",
    [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/avif",
        "image/svg+xml",
        "application/octet-stream",
        ""
    ]
)
def test_validate_response_headers_accepts_allowed_content_types(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": content_type,
            "Content-Length": "5000"
        }
    )

    validate_response_headers(
        response  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "content_type",
    [
        "text/html",
        "text/plain",
        "application/json",
        "application/xml",
        "video/mp4",
        "audio/mpeg"
    ]
)
def test_validate_response_headers_rejects_invalid_content_type(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": content_type,
            "Content-Length": "500"
        }
    )

    with pytest.raises(
        InvalidImageContentError,
        match="Content-Type inattendu"
    ):
        validate_response_headers(
            response  # type: ignore[arg-type]
        )


def test_validate_response_headers_ignores_content_type_parameters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": "image/jpeg; charset=binary",
            "Content-Length": "1000"
        }
    )

    validate_response_headers(
        response  # type: ignore[arg-type]
    )


def test_validate_response_headers_rejects_too_large_content(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": "image/jpeg",
            "Content-Length": "10001"
        }
    )

    with pytest.raises(
        ImageTooLargeError,
        match="Taille annoncée : 10001 octets"
    ):
        validate_response_headers(
            response  # type: ignore[arg-type]
        )


def test_validate_response_headers_accepts_exact_maximum_size(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": "image/jpeg",
            "Content-Length": "10000"
        }
    )

    validate_response_headers(
        response  # type: ignore[arg-type]
    )


def test_validate_response_headers_accepts_missing_length(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": "image/png"
        }
    )

    validate_response_headers(
        response  # type: ignore[arg-type]
    )


def test_validate_response_headers_accepts_invalid_length(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        10_000
    )

    response = FakeResponse(
        headers={
            "Content-Type": "image/png",
            "Content-Length": "invalid"
        }
    )

    validate_response_headers(
        response  # type: ignore[arg-type]
    )


def test_validate_response_headers_normalizes_invalid_maximum(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGE_MAX_SIZE_BYTES",
        0
    )

    response = FakeResponse(
        headers={
            "Content-Type": "image/jpeg",
            "Content-Length": "2"
        }
    )

    with pytest.raises(
        ImageTooLargeError,
        match="limite : 1"
    ):
        validate_response_headers(
            response  # type: ignore[arg-type]
        )


# Détection HTML et XML

@pytest.mark.parametrize(
    "content",
    [
        b"<!doctype html><html></html>",
        b"<!DOCTYPE HTML><html></html>",
        b"<html><body>Erreur</body></html>",
        b"<head><title>Erreur</title></head>",
        b"<body>Acces refuse</body>",
        b"<?xml version='1.0'?>",
        b"   <html><body>Erreur</body></html>",
        b"\n\t<!doctype html><html></html>"
    ]
)
def test_looks_like_html_detects_html_or_xml(
    content: bytes
) -> None:
    assert looks_like_html(content) is True


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff\xe0",
        b"RIFFxxxxWEBP",
        b"GIF89a",
        b"<svg xmlns='http://www.w3.org/2000/svg'>",
        b"random binary content",
        b"some text before <html>"
    ]
)
def test_looks_like_html_accepts_non_html_content(
    content: bytes
) -> None:
    assert looks_like_html(content) is False


def test_looks_like_html_is_case_insensitive() -> None:
    assert looks_like_html(
        b"  <HTML><BODY>Erreur</BODY></HTML>"
    ) is True


def test_looks_like_html_only_checks_prefix() -> None:
    content = (
        b"\x89PNG\r\n\x1a\n"
        b"<html><body>texte integre</body></html>"
    )

    assert looks_like_html(content) is False


# Exceptions de validation

def test_image_too_large_error_is_value_error() -> None:
    error = ImageTooLargeError("Image trop grande.")

    assert str(error) == "Image trop grande."
    assert isinstance(error, ValueError)


def test_invalid_image_content_error_is_value_error() -> None:
    error = InvalidImageContentError("Image invalide.")

    assert str(error) == "Image invalide."
    assert isinstance(error, ValueError)