"""Tests du service d'orchestration des images des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_service.py` (commit 93632c5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import src.images.image_service as module
from config.constants import (
    IMAGE_STATUS_INVALID_RESULT,
    IMAGE_STATUS_LOCAL_VALID,
    IMAGE_STATUS_MISSING_URL,
    IMAGE_STATUS_NOT_REQUESTED,
    IMAGE_VALIDATION_STATUS_ERROR,
    IMAGE_VALIDATION_STATUS_INVALID,
    IMAGE_VALIDATION_STATUS_VALID
)
from src.images.image_service import (
    download_articles_safely,
    find_existing_article_image,
    get_current_iso_datetime,
    get_image_download_status,
    get_image_validation_status,
    log_image_batch_summary,
    normalize_optional_text,
    prepare_articles_for_image_download,
    process_article_images,
    set_image_download_result,
    set_image_validation_result,
    validate_downloaded_articles
)


# Données de test

def build_article(
    article_id: str = "article-1",
    *,
    source: str = "Reuters",
    image_url: str = "https://example.com/image.jpg",
    image_path: str = ""
) -> dict[str, Any]:
    """Construit un article minimal pour les tests."""

    return {
        "id": article_id,
        "source": source,
        "title": f"Titre de {article_id}",
        "image_url": image_url,
        "image_path": image_path
    }


# Normalisation
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" texte ", "texte"),
        (Path("images/test.jpg"), str(Path("images/test.jpg"))),
        ("", ""),
        ("   ", ""),
        (None, ""),
        (123, ""),
        ([], ""),
        ({}, "")
    ]
)
def test_normalize_optional_text(
    value: Any,
    expected: str
) -> None:
    assert normalize_optional_text(value) == expected


def test_normalize_optional_text_handles_path_conversion_error() -> None:
    class InvalidPath:
        def __str__(self) -> str:
            raise OSError("Conversion impossible.")

    assert normalize_optional_text(InvalidPath()) == ""


def test_get_current_iso_datetime_returns_utc_datetime() -> None:
    result = get_current_iso_datetime()

    assert result.endswith("+00:00")
    assert "T" in result


def test_set_image_download_result() -> None:
    article: dict[str, Any] = {}

    set_image_download_result(
        article,
        " downloaded ",
        " erreur "
    )

    assert article["image_download_status"] == "downloaded"
    assert article["image_download_error"] == "erreur"


def test_set_image_validation_result() -> None:
    article: dict[str, Any] = {}

    set_image_validation_result(
        article,
        " valid ",
        " erreur "
    )

    assert article["image_validation_status"] == "valid"
    assert article["image_validation_error"] == "erreur"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" downloaded ", "downloaded"),
        ("", ""),
        (None, ""),
        (42, "")
    ]
)
def test_get_image_download_status(
    value: Any,
    expected: str
) -> None:
    article = {
        "image_download_status": value
    }

    assert get_image_download_status(article) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" valid ", "valid"),
        ("", ""),
        (None, ""),
        (42, "")
    ]
)
def test_get_image_validation_status(
    value: Any,
    expected: str
) -> None:
    article = {
        "image_validation_status": value
    }

    assert get_image_validation_status(article) == expected


# Recherche locale

def test_find_existing_article_image_rejects_invalid_article() -> None:
    assert find_existing_article_image(None) is None  # type: ignore[arg-type]


def test_find_existing_article_image_rejects_missing_identifier(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_article_id",
        lambda value: ""
    )

    assert find_existing_article_image(build_article()) is None


def test_find_existing_article_image_rejects_missing_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_article_id",
        lambda value: "article-1"
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: ""
    )

    assert find_existing_article_image(build_article()) is None


def test_find_existing_article_image_returns_none_when_directory_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        tmp_path
    )
    monkeypatch.setattr(
        module,
        "normalize_article_id",
        lambda value: "article-1"
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "reuters"
    )

    assert find_existing_article_image(build_article()) is None


def test_find_existing_article_image_returns_first_valid_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    source_directory = tmp_path / "reuters"
    source_directory.mkdir()

    first_candidate = source_directory / "reuters_article-1.jpg"
    second_candidate = source_directory / "reuters_article-1.png"

    first_candidate.write_bytes(b"jpg")
    second_candidate.write_bytes(b"png")

    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        tmp_path
    )
    monkeypatch.setattr(
        module,
        "normalize_article_id",
        lambda value: "article-1"
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "reuters"
    )
    monkeypatch.setattr(
        module,
        "is_valid_image_path",
        lambda path, allowed_directories=None: path == second_candidate
    )

    result = find_existing_article_image(build_article())

    assert result == second_candidate


def test_find_existing_article_image_passes_allowed_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    source_directory = tmp_path / "reuters"
    source_directory.mkdir()

    candidate = source_directory / "reuters_article-1.jpg"
    candidate.write_bytes(b"image")

    allowed_directories = [
        tmp_path
    ]
    calls: list[tuple[Path, Any]] = []

    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        tmp_path
    )
    monkeypatch.setattr(
        module,
        "normalize_article_id",
        lambda value: "article-1"
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "reuters"
    )

    def fake_is_valid_image_path(
        path: Path,
        directories: Any
    ) -> bool:
        calls.append((path, directories))
        return True

    monkeypatch.setattr(
        module,
        "is_valid_image_path",
        fake_is_valid_image_path
    )

    result = find_existing_article_image(
        build_article(),
        allowed_directories
    )

    assert result == candidate
    assert calls == [
        (
            candidate,
            allowed_directories
        )
    ]


def test_find_existing_article_image_handles_os_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingTargetDirectory:
        def is_dir(self) -> bool:
            raise OSError("Accès impossible.")

    class FailingImagesDirectory:
        def __truediv__(self, value: str) -> FailingTargetDirectory:
            return FailingTargetDirectory()

    monkeypatch.setattr(
        module,
        "IMAGES_DIR",
        FailingImagesDirectory()
    )
    monkeypatch.setattr(
        module,
        "normalize_article_id",
        lambda value: "article-1"
    )
    monkeypatch.setattr(
        module,
        "normalize_source_name",
        lambda value: "reuters"
    )

    assert find_existing_article_image(build_article()) is None

# Préparation

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "articles",
        42
    ]
)
def test_prepare_articles_for_image_download_rejects_invalid_collection(
    articles: Any
) -> None:
    assert prepare_articles_for_image_download(articles) == []


def test_prepare_articles_for_image_download_handles_empty_list() -> None:
    assert prepare_articles_for_image_download([]) == []


def test_prepare_articles_for_image_download_ignores_invalid_items() -> None:
    result = prepare_articles_for_image_download([
        None,
        "article",
        42
    ])

    assert result == []


def test_prepare_articles_for_image_download_marks_missing_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "find_existing_article_image",
        lambda article, allowed_directories=None: None
    )

    article = build_article(
        image_path=""
    )

    result = prepare_articles_for_image_download([
        article
    ])

    assert len(result) == 1
    assert result[0]["image_path"] == ""
    assert result[0]["image_is_valid"] is False
    assert result[0]["image_download_status"] == IMAGE_STATUS_NOT_REQUESTED
    assert result[0]["image_download_error"] == ""
    assert result[0]["image_validation_status"] == ""
    assert result[0]["image_validation_error"] == ""


def test_prepare_articles_for_image_download_uses_existing_local_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    existing_path = tmp_path / "image.jpg"

    monkeypatch.setattr(
        module,
        "find_existing_article_image",
        lambda article, allowed_directories=None: existing_path
    )
    monkeypatch.setattr(
        module,
        "is_valid_image_path",
        lambda path, allowed_directories=None: True
    )

    result = prepare_articles_for_image_download([
        build_article(image_path="")
    ])

    assert result[0]["image_path"] == str(existing_path)
    assert result[0]["image_is_valid"] is True
    assert result[0]["image_download_status"] == IMAGE_STATUS_LOCAL_VALID
    assert (
        result[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_VALID
    )


def test_prepare_articles_for_image_download_preserves_valid_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "find_existing_article_image",
        lambda *args, **kwargs: pytest.fail(
            "La recherche locale ne devait pas être appelée."
        )
    )
    monkeypatch.setattr(
        module,
        "is_valid_image_path",
        lambda path, allowed_directories=None: True
    )

    result = prepare_articles_for_image_download([
        build_article(
            image_path="data/images/image.jpg"
        )
    ])

    assert result[0]["image_path"] == "data/images/image.jpg"
    assert result[0]["image_is_valid"] is True
    assert result[0]["image_download_status"] == IMAGE_STATUS_LOCAL_VALID


def test_prepare_articles_for_image_download_clears_invalid_path(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_image_path",
        lambda path, allowed_directories=None: False
    )

    result = prepare_articles_for_image_download([
        build_article(
            image_path="data/images/invalid.jpg"
        )
    ])

    assert result[0]["image_path"] == ""
    assert result[0]["image_is_valid"] is False
    assert result[0]["image_download_status"] == IMAGE_STATUS_NOT_REQUESTED
    assert (
        result[0]["image_download_error"]
        == "Le chemin d'image locale est invalide."
    )
    assert (
        result[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_INVALID
    )


def test_prepare_articles_for_image_download_handles_validation_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_image_path",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(RuntimeError("Erreur de validation."))
    )

    result = prepare_articles_for_image_download([
        build_article(
            image_path="data/images/image.jpg"
        )
    ])

    assert result[0]["image_path"] == ""
    assert result[0]["image_is_valid"] is False
    assert (
        result[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_INVALID
    )


def test_prepare_articles_for_image_download_does_not_modify_input(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    original = article.copy()

    monkeypatch.setattr(
        module,
        "find_existing_article_image",
        lambda *args, **kwargs: None
    )

    prepare_articles_for_image_download([
        article
    ])

    assert article == original


# Téléchargement sécurisé

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "articles",
        42
    ]
)
def test_download_articles_safely_rejects_invalid_collection(
    articles: Any
) -> None:
    assert download_articles_safely(articles) == []


def test_download_articles_safely_handles_empty_list() -> None:
    assert download_articles_safely([]) == []


def test_download_articles_safely_ignores_invalid_items() -> None:
    assert download_articles_safely([
        None,
        42,
        "article"
    ]) == []


def test_download_articles_safely_preserves_local_valid_image(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: pytest.fail(
            "Le téléchargeur ne devait pas être appelé."
        )
    )

    article = build_article()
    article["image_is_valid"] = True

    result = download_articles_safely([
        article
    ])

    assert len(result) == 1
    assert result[0]["image_download_status"] == IMAGE_STATUS_LOCAL_VALID
    assert result[0]["image_is_valid"] is True


def test_download_articles_safely_handles_missing_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: pytest.fail(
            "Le téléchargeur ne devait pas être appelé."
        )
    )

    article = build_article(
        image_url=""
    )
    article["image_is_valid"] = False

    result = download_articles_safely([
        article
    ])

    assert result[0]["image_path"] == ""
    assert result[0]["image_is_valid"] is False
    assert result[0]["image_download_status"] == IMAGE_STATUS_MISSING_URL
    assert (
        result[0]["image_download_error"]
        == "Aucune URL d'image distante n'est disponible."
    )


def test_download_articles_safely_downloads_remote_images(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    article["image_is_valid"] = False

    def fake_download(
        articles: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {
                **articles[0],
                "image_path": "data/images/image.jpg",
                "image_download_status": "downloaded"
            }
        ]

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        fake_download
    )

    result = download_articles_safely([
        article
    ])

    assert result[0]["image_path"] == "data/images/image.jpg"
    assert result[0]["image_download_status"] == "downloaded"
    assert result[0]["image_is_valid"] is False


def test_download_articles_safely_preserves_order(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    first = build_article("article-1")
    first["image_is_valid"] = True

    second = build_article("article-2")
    second["image_is_valid"] = False

    third = build_article(
        "article-3",
        image_url=""
    )
    third["image_is_valid"] = False

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: [
            {
                **articles[0],
                "image_download_status": "downloaded"
            }
        ]
    )

    result = download_articles_safely([
        first,
        second,
        third
    ])

    assert [
        article["id"]
        for article in result
    ] == [
        "article-1",
        "article-2",
        "article-3"
    ]


def test_download_articles_safely_marks_missing_status_as_invalid_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    article["image_is_valid"] = False

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: [
            {
                **articles[0],
                "image_path": "data/images/image.jpg"
            }
        ]
    )

    result = download_articles_safely([
        article
    ])

    assert result[0]["image_path"] == ""
    assert (
        result[0]["image_download_status"]
        == IMAGE_STATUS_INVALID_RESULT
    )
    assert (
        result[0]["image_download_error"]
        == "Le téléchargeur n'a retourné aucun statut exploitable."
    )


def test_download_articles_safely_handles_missing_download_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    article["image_is_valid"] = False

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: []
    )

    result = download_articles_safely([
        article
    ])

    assert len(result) == 1
    assert result[0]["image_download_status"] == IMAGE_STATUS_INVALID_RESULT


def test_download_articles_safely_handles_invalid_download_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    article["image_is_valid"] = False

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: [
            None
        ]
    )

    result = download_articles_safely([
        article
    ])

    assert result[0]["image_download_status"] == IMAGE_STATUS_INVALID_RESULT


def test_download_articles_safely_handles_batch_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    article["image_is_valid"] = False

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: (
            _ for _ in ()
        ).throw(RuntimeError("Erreur du lot."))
    )

    result = download_articles_safely([
        article
    ])

    assert len(result) == 1
    assert result[0]["image_download_status"] == IMAGE_STATUS_INVALID_RESULT


def test_download_articles_safely_does_not_modify_input(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(
        image_url=""
    )
    article["image_is_valid"] = False
    original = article.copy()

    monkeypatch.setattr(
        module,
        "download_images_for_articles",
        lambda articles: []
    )

    download_articles_safely([
        article
    ])

    assert article == original


# Validation

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "articles",
        42
    ]
)
def test_validate_downloaded_articles_rejects_invalid_collection(
    articles: Any
) -> None:
    assert validate_downloaded_articles(
        articles,
        require_image=False
    ) == (
        [],
        0,
        0,
        0
    )


def test_validate_downloaded_articles_ignores_invalid_items() -> None:
    result = validate_downloaded_articles(
        [
            None,
            "article",
            42
        ],
        require_image=False
    )

    assert result == (
        [],
        0,
        0,
        3
    )


def test_validate_downloaded_articles_keeps_valid_image(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article_image",
        lambda article, allowed_directories=None: True
    )
    monkeypatch.setattr(
        module,
        "get_current_iso_datetime",
        lambda: "2026-07-28T10:00:00+00:00"
    )

    articles, valid_count, invalid_count, ignored_count = (
        validate_downloaded_articles(
            [
                build_article(
                    image_path="data/images/image.jpg"
                )
            ],
            require_image=True
        )
    )

    assert len(articles) == 1
    assert valid_count == 1
    assert invalid_count == 0
    assert ignored_count == 0
    assert articles[0]["image_is_valid"] is True
    assert (
        articles[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_VALID
    )
    assert (
        articles[0]["image_validated_at"]
        == "2026-07-28T10:00:00+00:00"
    )


def test_validate_downloaded_articles_keeps_invalid_image_in_tolerant_mode(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article_image",
        lambda article, allowed_directories=None: False
    )

    articles, valid_count, invalid_count, ignored_count = (
        validate_downloaded_articles(
            [
                build_article(
                    image_path="data/images/invalid.jpg"
                )
            ],
            require_image=False
        )
    )

    assert len(articles) == 1
    assert valid_count == 0
    assert invalid_count == 1
    assert ignored_count == 0
    assert articles[0]["image_path"] == ""
    assert articles[0]["image_is_valid"] is False
    assert (
        articles[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_INVALID
    )
    assert (
        articles[0]["image_validation_error"]
        == "L'image locale est absente ou invalide."
    )


def test_validate_downloaded_articles_rejects_invalid_image_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article_image",
        lambda article, allowed_directories=None: False
    )

    articles, valid_count, invalid_count, ignored_count = (
        validate_downloaded_articles(
            [
                build_article(
                    image_path="data/images/invalid.jpg"
                )
            ],
            require_image=True
        )
    )

    assert articles == []
    assert valid_count == 0
    assert invalid_count == 1
    assert ignored_count == 0


def test_validate_downloaded_articles_handles_validation_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article_image",
        lambda article, allowed_directories=None: (
            _ for _ in ()
        ).throw(ValueError("Image illisible."))
    )

    articles, valid_count, invalid_count, ignored_count = (
        validate_downloaded_articles(
            [
                build_article(
                    image_path="data/images/image.jpg"
                )
            ],
            require_image=False
        )
    )

    assert len(articles) == 1
    assert valid_count == 0
    assert invalid_count == 1
    assert ignored_count == 0
    assert (
        articles[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_ERROR
    )
    assert (
        articles[0]["image_validation_error"]
        == "ValueError: Image illisible."
    )


def test_validate_downloaded_articles_preserves_error_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    article["image_validation_status"] = IMAGE_VALIDATION_STATUS_ERROR
    article["image_validation_error"] = "Erreur existante."

    monkeypatch.setattr(
        module,
        "validate_article_image",
        lambda article, allowed_directories=None: False
    )

    articles, _, _, _ = validate_downloaded_articles(
        [
            article
        ],
        require_image=False
    )

    assert (
        articles[0]["image_validation_status"]
        == IMAGE_VALIDATION_STATUS_ERROR
    )
    assert (
        articles[0]["image_validation_error"]
        == "Erreur existante."
    )


def test_validate_downloaded_articles_does_not_modify_input(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    original = article.copy()

    monkeypatch.setattr(
        module,
        "validate_article_image",
        lambda article, allowed_directories=None: True
    )

    validate_downloaded_articles(
        [
            article
        ],
        require_image=False
    )

    assert article == original


# Journalisation

def test_log_image_batch_summary_logs_main_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    info_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    log_image_batch_summary(
        input_count=5,
        prepared_count=4,
        downloaded_articles=[],
        output_count=3,
        valid_count=2,
        invalid_count=1,
        ignored_count=0,
        require_image=False
    )

    assert len(info_calls) == 1
    assert info_calls[0][1:] == (
        5,
        4,
        3,
        2,
        1
    )


def test_log_image_batch_summary_logs_statuses(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    info_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    log_image_batch_summary(
        input_count=2,
        prepared_count=2,
        downloaded_articles=[
            {
                "image_download_status": "downloaded"
            },
            {
                "image_download_status": "missing_url"
            }
        ],
        output_count=2,
        valid_count=1,
        invalid_count=1,
        ignored_count=0,
        require_image=False
    )

    assert len(info_calls) == 2
    assert "downloaded=1" in info_calls[1][1]
    assert "missing_url=1" in info_calls[1][1]


def test_log_image_batch_summary_logs_ignored_items(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warning_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append(args)
    )

    log_image_batch_summary(
        input_count=2,
        prepared_count=1,
        downloaded_articles=[],
        output_count=1,
        valid_count=1,
        invalid_count=0,
        ignored_count=1,
        require_image=False
    )

    assert warning_calls[0][1] == 1


def test_log_image_batch_summary_logs_strict_rejections(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    info_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    log_image_batch_summary(
        input_count=3,
        prepared_count=3,
        downloaded_articles=[],
        output_count=1,
        valid_count=1,
        invalid_count=2,
        ignored_count=0,
        require_image=True
    )

    assert len(info_calls) == 2
    assert info_calls[1][1] == 2


# Orchestration

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "articles",
        42
    ]
)
def test_process_article_images_rejects_invalid_collection(
    articles: Any
) -> None:
    assert process_article_images(articles) == []


def test_process_article_images_handles_empty_list() -> None:
    assert process_article_images([]) == []


def test_process_article_images_stops_after_empty_preparation(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "prepare_articles_for_image_download",
        lambda articles, allowed_directories=None: []
    )
    monkeypatch.setattr(
        module,
        "download_articles_safely",
        lambda articles: pytest.fail(
            "Le téléchargement ne devait pas être appelé."
        )
    )

    result = process_article_images([
        build_article()
    ])

    assert result == []


def test_process_article_images_stops_after_empty_download(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = [
        build_article()
    ]

    monkeypatch.setattr(
        module,
        "prepare_articles_for_image_download",
        lambda articles, allowed_directories=None: prepared
    )
    monkeypatch.setattr(
        module,
        "download_articles_safely",
        lambda articles: []
    )
    monkeypatch.setattr(
        module,
        "validate_downloaded_articles",
        lambda *args, **kwargs: pytest.fail(
            "La validation ne devait pas être appelée."
        )
    )

    result = process_article_images([
        build_article()
    ])

    assert result == []


def test_process_article_images_runs_complete_pipeline(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    input_articles = [
        build_article()
    ]
    prepared_articles = [
        {
            **input_articles[0],
            "prepared": True
        }
    ]
    downloaded_articles = [
        {
            **prepared_articles[0],
            "downloaded": True
        }
    ]
    processed_articles = [
        {
            **downloaded_articles[0],
            "processed": True
        }
    ]

    calls: list[tuple[str, Any]] = []

    def fake_prepare(
        articles: list[Any],
        allowed_directories: Any = None
    ) -> list[dict[str, Any]]:
        calls.append(("prepare", allowed_directories))
        return prepared_articles

    def fake_download(
        articles: list[Any]
    ) -> list[dict[str, Any]]:
        calls.append(("download", articles))
        return downloaded_articles

    def fake_validate(
        articles: list[Any],
        require_image: bool,
        allowed_directories: Any = None
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        calls.append((
            "validate",
            require_image,
            allowed_directories
        ))
        return processed_articles, 1, 0, 0

    summary_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "prepare_articles_for_image_download",
        fake_prepare
    )
    monkeypatch.setattr(
        module,
        "download_articles_safely",
        fake_download
    )
    monkeypatch.setattr(
        module,
        "validate_downloaded_articles",
        fake_validate
    )
    monkeypatch.setattr(
        module,
        "log_image_batch_summary",
        lambda **kwargs: summary_calls.append(kwargs)
    )

    allowed_directories = [
        "data/images"
    ]

    result = process_article_images(
        input_articles,
        require_image=True,
        allowed_directories=allowed_directories
    )

    assert result == processed_articles
    assert calls[0] == (
        "prepare",
        allowed_directories
    )
    assert calls[1] == (
        "download",
        prepared_articles
    )
    assert calls[2] == (
        "validate",
        True,
        allowed_directories
    )
    assert summary_calls == [
        {
            "input_count": 1,
            "prepared_count": 1,
            "downloaded_articles": downloaded_articles,
            "output_count": 1,
            "valid_count": 1,
            "invalid_count": 0,
            "ignored_count": 0,
            "require_image": True
        }
    ]


def test_process_article_images_converts_require_image_to_bool(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_require_image: list[bool] = []

    monkeypatch.setattr(
        module,
        "prepare_articles_for_image_download",
        lambda articles, allowed_directories=None: [
            build_article()
        ]
    )
    monkeypatch.setattr(
        module,
        "download_articles_safely",
        lambda articles: articles
    )

    def fake_validate(
        articles: list[Any],
        require_image: bool,
        allowed_directories: Any = None
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        captured_require_image.append(require_image)
        return articles, 0, 1, 0

    monkeypatch.setattr(
        module,
        "validate_downloaded_articles",
        fake_validate
    )
    monkeypatch.setattr(
        module,
        "log_image_batch_summary",
        lambda **kwargs: None
    )

    process_article_images(
        [
            build_article()
        ],
        require_image=1
    )

    assert captured_require_image == [
        True
    ]
