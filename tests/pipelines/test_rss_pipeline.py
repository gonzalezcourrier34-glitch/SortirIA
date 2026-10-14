"""Tests du pipeline local RSS de CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/pipelines/test_rss_pipeline.py` (commit 93632c5).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.pipelines.rss_pipeline as module
from src.pipelines.rss_pipeline import (
    extract_rss_articles,
    finalize_rss_articles,
    prepare_rss_articles,
    process_rss_images,
    require_article_list,
    run_rss_pipeline,
    stop_pipeline,
    store_rss_articles
)


# Utilitaires

def make_preparation_result(
    articles: list[dict[str, Any]],
    *,
    received: int | None = None,
    normalized: int | None = None,
    cleaned: int | None = None,
    duplicates: int = 0,
    invalid: int = 0,
    rejection_reasons: dict[str, int] | None = None
) -> SimpleNamespace:
    """Construit un résultat simulé de préparation."""

    article_count = len(articles)

    report = SimpleNamespace(
        received=article_count if received is None else received,
        normalized=article_count if normalized is None else normalized,
        cleaned=article_count if cleaned is None else cleaned,
        duplicates=duplicates,
        invalid=invalid,
        kept=article_count,
        rejection_reasons=rejection_reasons or {}
    )

    return SimpleNamespace(
        articles=articles,
        report=report
    )


# Arrêt du pipeline

def test_stop_pipeline_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda message, reason: logged_messages.append(
            (message, reason)
        )
    )

    result = stop_pipeline("aucun article")

    assert result == []
    assert logged_messages == [
        ("Pipeline RSS arrêté : %s.", "aucun article")
    ]


def test_stop_pipeline_normalizes_empty_message(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda message, reason: logged_messages.append(
            (message, reason)
        )
    )

    result = stop_pipeline("")

    assert result == []
    assert logged_messages == [
        ("Pipeline RSS arrêté : %s.", "raison inconnue")
    ]


# Validation du format

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
def test_require_article_list_rejects_invalid_value(
    articles: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="Test : une liste était attendue"
    ):
        require_article_list(articles, "Test")


def test_require_article_list_returns_same_list() -> None:
    articles = [{"title": "Article RSS"}]

    result = require_article_list(articles, "Test")

    assert result is articles


# Extraction RSS

def test_extract_rss_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [{"id": "1"}]

    monkeypatch.setattr(
        module,
        "extract_all_articles",
        lambda: articles
    )

    result = extract_rss_articles()

    assert result is articles


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        {},
        (),
        "articles"
    ]
)
def test_extract_rss_articles_rejects_invalid_result(
    monkeypatch: pytest.MonkeyPatch,
    invalid_result: Any
) -> None:
    monkeypatch.setattr(
        module,
        "extract_all_articles",
        lambda: invalid_result
    )

    with pytest.raises(
        TypeError,
        match="Extraction RSS"
    ):
        extract_rss_articles()


# Préparation RSS

def test_prepare_rss_articles_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Préparation RSS"):
        prepare_rss_articles("invalid")  # type: ignore[arg-type]


def test_prepare_rss_articles_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "prepare_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = prepare_rss_articles([])

    assert result == []
    assert calls == []


def test_prepare_rss_articles_calls_preparation_service(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [{"id": "1"}]
    prepared = [{"id": "1", "prepared": True}]
    calls: list[dict[str, Any]] = []

    def fake_prepare_articles(
        received_articles: list[Any],
        *,
        policy_name: str,
        remove_duplicates: bool
    ) -> SimpleNamespace:
        calls.append({
            "articles": received_articles,
            "policy_name": policy_name,
            "remove_duplicates": remove_duplicates
        })

        return make_preparation_result(prepared)

    monkeypatch.setattr(
        module,
        "prepare_articles",
        fake_prepare_articles
    )

    result = prepare_rss_articles(articles)

    assert result is prepared
    assert calls == [
        {
            "articles": articles,
            "policy_name": "acquisition",
            "remove_duplicates": True
        }
    ]


def test_prepare_rss_articles_logs_rejection_reasons(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "prepare_articles",
        lambda articles, **kwargs: make_preparation_result(
            [],
            received=2,
            normalized=2,
            cleaned=1,
            invalid=1,
            rejection_reasons={"texte_trop_court": 1}
        )
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: logged_messages.append(args)
    )

    result = prepare_rss_articles([
        {"id": "1"},
        {"id": "2"}
    ])

    assert result == []
    assert (
        "Motifs de rejet pendant la préparation RSS : %s.",
        {"texte_trop_court": 1}
    ) in logged_messages


def test_prepare_rss_articles_does_not_log_empty_rejections(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "prepare_articles",
        lambda articles, **kwargs: make_preparation_result(
            articles,
            rejection_reasons={}
        )
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: logged_messages.append(args)
    )

    result = prepare_rss_articles([{"id": "1"}])

    assert result == [{"id": "1"}]
    assert not any(
        message
        and message[0]
        == "Motifs de rejet pendant la préparation RSS : %s."
        for message in logged_messages
    )


# Images RSS

def test_process_rss_images_rejects_invalid_collection() -> None:
    with pytest.raises(
        TypeError,
        match="Traitement des images RSS"
    ):
        process_rss_images(
            "invalid",  # type: ignore[arg-type]
            require_image=False
        )


def test_process_rss_images_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "process_article_images",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = process_rss_images(
        [],
        require_image=False
    )

    assert result == []
    assert calls == []


def test_process_rss_images_calls_image_service(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [{"id": "1"}]
    expected = [{"id": "1", "image_download_status": "success"}]
    calls: list[dict[str, Any]] = []

    def fake_process_article_images(
        received_articles: list[Any],
        *,
        require_image: bool,
        allowed_directories: tuple[str | Path, ...] | None
    ) -> list[dict[str, Any]]:
        calls.append({
            "articles": received_articles,
            "require_image": require_image,
            "allowed_directories": allowed_directories
        })
        return expected

    monkeypatch.setattr(
        module,
        "process_article_images",
        fake_process_article_images
    )

    result = process_rss_images(
        articles,
        require_image=True,
        allowed_directories=[
            "/tmp/images",
            Path("/tmp/shared")
        ]
    )

    assert result is expected
    assert calls == [
        {
            "articles": articles,
            "require_image": True,
            "allowed_directories": (
                "/tmp/images",
                Path("/tmp/shared")
            )
        }
    ]


def test_process_rss_images_preserves_none_directories(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received_directories: list[Any] = []

    def fake_process(
        articles: list[Any],
        *,
        require_image: bool,
        allowed_directories: Any
    ) -> list[dict[str, Any]]:
        received_directories.append(allowed_directories)
        return articles

    monkeypatch.setattr(
        module,
        "process_article_images",
        fake_process
    )

    result = process_rss_images(
        [{"id": "1"}],
        require_image=False,
        allowed_directories=None
    )

    assert result == [{"id": "1"}]
    assert received_directories == [None]


# Validation finale

def test_finalize_rss_articles_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Validation finale RSS"):
        finalize_rss_articles(
            "invalid",  # type: ignore[arg-type]
            require_image=False
        )


def test_finalize_rss_articles_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "validate_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = finalize_rss_articles(
        [],
        require_image=False
    )

    assert result == []
    assert calls == []


def test_finalize_rss_articles_calls_validator(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [{"id": "1"}, {"id": "2"}]
    expected = [{"id": "1"}]
    calls: list[dict[str, Any]] = []

    def fake_validate_articles(
        received_articles: list[Any],
        *,
        require_image: bool
    ) -> list[dict[str, Any]]:
        calls.append({
            "articles": received_articles,
            "require_image": require_image
        })
        return expected

    monkeypatch.setattr(
        module,
        "validate_articles",
        fake_validate_articles
    )

    result = finalize_rss_articles(
        articles,
        require_image=True
    )

    assert result is expected
    assert calls == [
        {
            "articles": articles,
            "require_image": True
        }
    ]


# Stockage RSS

def test_store_rss_articles_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Stockage RSS"):
        store_rss_articles(
            "invalid"  # type: ignore[arg-type]
        )


def test_store_rss_articles_returns_false_for_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "save_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = store_rss_articles([])

    assert result is False
    assert calls == []


def test_store_rss_articles_returns_false_when_no_format(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "save_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = store_rss_articles(
        [{"id": "1"}],
        save_json=False,
        save_csv=False
    )

    assert result is False
    assert calls == []


@pytest.mark.parametrize(
    ("save_json", "save_csv"),
    [
        (True, True),
        (True, False),
        (False, True)
    ]
)
def test_store_rss_articles_calls_storage_service(
    monkeypatch: pytest.MonkeyPatch,
    save_json: bool,
    save_csv: bool
) -> None:
    articles = [{"id": "1"}]
    calls: list[dict[str, Any]] = []

    def fake_save_articles(
        received_articles: list[Any],
        *,
        save_json: bool,
        save_csv: bool
    ) -> bool:
        calls.append({
            "articles": received_articles,
            "save_json": save_json,
            "save_csv": save_csv
        })
        return True

    monkeypatch.setattr(
        module,
        "save_articles",
        fake_save_articles
    )

    result = store_rss_articles(
        articles,
        save_json=save_json,
        save_csv=save_csv
    )

    assert result is True
    assert calls == [
        {
            "articles": articles,
            "save_json": save_json,
            "save_csv": save_csv
        }
    ]


def test_store_rss_articles_propagates_storage_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "save_articles",
        lambda *args, **kwargs: False
    )

    result = store_rss_articles([{"id": "1"}])

    assert result is False


# Pipeline principal

def test_run_rss_pipeline_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extracted = [{"id": "1"}]
    prepared = [{"id": "1", "prepared": True}]
    with_images = [{"id": "1", "image_download_status": "success"}]
    final = [{"id": "1", "valid": True}]
    calls: list[tuple[str, Any]] = []

    def fake_extract() -> list[dict[str, Any]]:
        calls.append(("extract", None))
        return extracted

    def fake_prepare(
        articles: list[Any]
    ) -> list[dict[str, Any]]:
        calls.append(("prepare", articles))
        return prepared

    def fake_images(
        articles: list[Any],
        require_image: bool,
        allowed_directories: Any
    ) -> list[dict[str, Any]]:
        calls.append((
            "images",
            {
                "articles": articles,
                "require_image": require_image,
                "allowed_directories": allowed_directories
            }
        ))
        return with_images

    def fake_finalize(
        articles: list[Any],
        require_image: bool
    ) -> list[dict[str, Any]]:
        calls.append((
            "finalize",
            {
                "articles": articles,
                "require_image": require_image
            }
        ))
        return final

    def fake_store(
        articles: list[Any],
        save_json: bool,
        save_csv: bool
    ) -> bool:
        calls.append((
            "store",
            {
                "articles": articles,
                "save_json": save_json,
                "save_csv": save_csv
            }
        ))
        return True

    monkeypatch.setattr(module, "extract_rss_articles", fake_extract)
    monkeypatch.setattr(module, "prepare_rss_articles", fake_prepare)
    monkeypatch.setattr(module, "process_rss_images", fake_images)
    monkeypatch.setattr(module, "finalize_rss_articles", fake_finalize)
    monkeypatch.setattr(module, "store_rss_articles", fake_store)

    result = run_rss_pipeline(
        require_image=True,
        allowed_directories=["data/images"],
        save_json=True,
        save_csv=False
    )

    assert result is final
    assert calls == [
        ("extract", None),
        ("prepare", extracted),
        (
            "images",
            {
                "articles": prepared,
                "require_image": True,
                "allowed_directories": ["data/images"]
            }
        ),
        (
            "finalize",
            {
                "articles": with_images,
                "require_image": True
            }
        ),
        (
            "store",
            {
                "articles": final,
                "save_json": True,
                "save_csv": False
            }
        )
    ]


@pytest.mark.parametrize(
    ("stage_name", "require_image", "expected_reason"),
    [
        (
            "extraction",
            False,
            "aucun article extrait"
        ),
        (
            "preparation",
            False,
            "aucun article exploitable après préparation"
        ),
        (
            "images",
            False,
            "aucun article après traitement des images"
        ),
        (
            "images",
            True,
            "aucune image valide"
        ),
        (
            "validation",
            False,
            "aucun article après validation finale"
        )
    ]
)
def test_run_rss_pipeline_stops_when_stage_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    stage_name: str,
    require_image: bool,
    expected_reason: str
) -> None:
    articles = [{"id": "1"}]
    stopped_reasons: list[str] = []
    storage_calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "extract_rss_articles",
        lambda: [] if stage_name == "extraction" else articles
    )
    monkeypatch.setattr(
        module,
        "prepare_rss_articles",
        lambda received: (
            [] if stage_name == "preparation" else articles
        )
    )
    monkeypatch.setattr(
        module,
        "process_rss_images",
        lambda received, require_image, allowed_directories=None: (
            [] if stage_name == "images" else articles
        )
    )
    monkeypatch.setattr(
        module,
        "finalize_rss_articles",
        lambda received, require_image: (
            [] if stage_name == "validation" else articles
        )
    )
    monkeypatch.setattr(
        module,
        "store_rss_articles",
        lambda *args, **kwargs: storage_calls.append(
            (args, kwargs)
        )
    )

    def fake_stop_pipeline(message: str) -> list[dict[str, Any]]:
        stopped_reasons.append(message)
        return []

    monkeypatch.setattr(
        module,
        "stop_pipeline",
        fake_stop_pipeline
    )

    result = run_rss_pipeline(require_image=require_image)

    assert result == []
    assert stopped_reasons == [expected_reason]
    assert storage_calls == []


def test_run_rss_pipeline_returns_articles_when_storage_fails(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [{"id": "1"}]
    logged_errors: list[str] = []

    monkeypatch.setattr(
        module,
        "extract_rss_articles",
        lambda: articles
    )
    monkeypatch.setattr(
        module,
        "prepare_rss_articles",
        lambda received: articles
    )
    monkeypatch.setattr(
        module,
        "process_rss_images",
        lambda received, require_image, allowed_directories=None: articles
    )
    monkeypatch.setattr(
        module,
        "finalize_rss_articles",
        lambda received, require_image: articles
    )
    monkeypatch.setattr(
        module,
        "store_rss_articles",
        lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda message: logged_errors.append(message)
    )

    result = run_rss_pipeline()

    assert result is articles
    assert logged_errors == [
        "Pipeline RSS terminé avec une erreur de stockage."
    ]


@pytest.mark.parametrize(
    "failing_stage",
    [
        "extraction",
        "preparation",
        "images",
        "validation",
        "storage"
    ]
)
def test_run_rss_pipeline_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
    failing_stage: str
) -> None:
    articles = [{"id": "1"}]
    logged_exceptions: list[tuple[str, BaseException]] = []

    def raise_if_stage(stage: str) -> None:
        if failing_stage == stage:
            raise RuntimeError(f"Erreur {stage}")

    def fake_extract() -> list[dict[str, Any]]:
        raise_if_stage("extraction")
        return articles

    def fake_prepare(
        received: list[Any]
    ) -> list[dict[str, Any]]:
        raise_if_stage("preparation")
        return articles

    def fake_images(
        received: list[Any],
        require_image: bool,
        allowed_directories: Any = None
    ) -> list[dict[str, Any]]:
        raise_if_stage("images")
        return articles

    def fake_validation(
        received: list[Any],
        require_image: bool
    ) -> list[dict[str, Any]]:
        raise_if_stage("validation")
        return articles

    def fake_storage(
        received: list[Any],
        save_json: bool = True,
        save_csv: bool = True
    ) -> bool:
        raise_if_stage("storage")
        return True

    monkeypatch.setattr(module, "extract_rss_articles", fake_extract)
    monkeypatch.setattr(module, "prepare_rss_articles", fake_prepare)
    monkeypatch.setattr(module, "process_rss_images", fake_images)
    monkeypatch.setattr(module, "finalize_rss_articles", fake_validation)
    monkeypatch.setattr(module, "store_rss_articles", fake_storage)

    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda message, error: logged_exceptions.append(
            (message, error)
        )
    )

    result = run_rss_pipeline()

    assert result == []
    assert len(logged_exceptions) == 1

    message, error = logged_exceptions[0]

    assert message == (
        "Erreur inattendue pendant le pipeline RSS : %s"
    )
    assert isinstance(error, RuntimeError)
    assert str(error) == f"Erreur {failing_stage}"


def test_run_rss_pipeline_uses_default_arguments(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "extract_rss_articles",
        lambda: []
    )

    result = run_rss_pipeline()

    assert result == []
    assert calls == []