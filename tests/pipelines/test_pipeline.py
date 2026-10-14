"""Tests du pipeline principal d'acquisition multimodale.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/pipelines/test_pipeline.py` (commit 93632c5).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.pipelines.pipeline as module
from src.pipelines.pipeline import (
    DEFAULT_PREPARATION_POLICY,
    PREPARATION_POLICIES,
    finalize_articles,
    get_article_policy,
    group_articles_by_policy,
    prepare_extracted_articles,
    process_images_stage,
    require_article_list,
    run_pipeline,
    stop_pipeline,
    store_articles_stage
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


def make_extraction_result(
    articles: list[dict[str, Any]]
) -> SimpleNamespace:
    """Construit un résultat simulé d'extraction."""

    return SimpleNamespace(articles=articles)


# Configuration

def test_preparation_policies() -> None:
    assert PREPARATION_POLICIES == (
        "acquisition",
        "labeled_reference",
        "multimodal_reference"
    )
    assert DEFAULT_PREPARATION_POLICY == "acquisition"


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
        ("Pipeline arrêté : %s.", "aucun article")
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
        ("Pipeline arrêté : %s.", "raison inconnue")
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
        match="Test : articles doit être une liste"
    ):
        require_article_list(articles, "Test")


def test_require_article_list_returns_same_list() -> None:
    articles = [{"title": "Article"}]

    result = require_article_list(articles, "Test")

    assert result is articles


# Politique de préparation

@pytest.mark.parametrize(
    ("article", "expected"),
    [
        (
            {"role": "acquisition"},
            "acquisition"
        ),
        (
            {"role": "labeled_reference"},
            "labeled_reference"
        ),
        (
            {"role": "multimodal_reference"},
            "multimodal_reference"
        ),
        (
            {"role": " LABELED_REFERENCE "},
            "labeled_reference"
        ),
        (
            {"role": "unknown"},
            "acquisition"
        ),
        (
            {"role": ""},
            "acquisition"
        ),
        (
            {"role": None},
            "acquisition"
        ),
        (
            {},
            "acquisition"
        ),
        (
            "invalid",
            "acquisition"
        ),
        (
            None,
            "acquisition"
        )
    ]
)
def test_get_article_policy(
    article: Any,
    expected: str
) -> None:
    assert get_article_policy(article) == expected


def test_group_articles_by_policy() -> None:
    acquisition = {
        "title": "Acquisition",
        "role": "acquisition"
    }
    labeled = {
        "title": "Référence",
        "role": "labeled_reference"
    }
    multimodal = {
        "title": "Multimodal",
        "role": "multimodal_reference"
    }
    unknown = {
        "title": "Inconnu",
        "role": "other"
    }
    invalid = "invalid"

    result = group_articles_by_policy([
        acquisition,
        labeled,
        multimodal,
        unknown,
        invalid
    ])

    assert result == {
        "acquisition": [
            acquisition,
            unknown,
            invalid
        ],
        "labeled_reference": [labeled],
        "multimodal_reference": [multimodal]
    }


def test_group_articles_by_policy_returns_all_groups() -> None:
    result = group_articles_by_policy([])

    assert result == {
        "acquisition": [],
        "labeled_reference": [],
        "multimodal_reference": []
    }


# Préparation

def test_prepare_extracted_articles_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Préparation"):
        prepare_extracted_articles("invalid")  # type: ignore[arg-type]


def test_prepare_extracted_articles_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "prepare_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = prepare_extracted_articles([])

    assert result == []
    assert calls == []


def test_prepare_extracted_articles_groups_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "a1",
            "role": "acquisition"
        },
        {
            "id": "l1",
            "role": "labeled_reference"
        },
        {
            "id": "m1",
            "role": "multimodal_reference"
        },
        {
            "id": "a2",
            "role": "unknown"
        }
    ]

    calls: list[dict[str, Any]] = []

    def fake_prepare_articles(
        policy_articles: list[Any],
        *,
        policy_name: str,
        remove_duplicates: bool
    ) -> SimpleNamespace:
        calls.append({
            "articles": policy_articles,
            "policy_name": policy_name,
            "remove_duplicates": remove_duplicates
        })

        prepared = [
            {
                **article,
                "prepared": True
            }
            for article in policy_articles
        ]

        return make_preparation_result(prepared)

    monkeypatch.setattr(
        module,
        "prepare_articles",
        fake_prepare_articles
    )

    result = prepare_extracted_articles(
        articles,
        remove_duplicates=False
    )

    assert result == [
        {
            "id": "a1",
            "role": "acquisition",
            "prepared": True
        },
        {
            "id": "a2",
            "role": "unknown",
            "prepared": True
        },
        {
            "id": "l1",
            "role": "labeled_reference",
            "prepared": True
        },
        {
            "id": "m1",
            "role": "multimodal_reference",
            "prepared": True
        }
    ]

    assert calls == [
        {
            "articles": [
                {
                    "id": "a1",
                    "role": "acquisition"
                },
                {
                    "id": "a2",
                    "role": "unknown"
                }
            ],
            "policy_name": "acquisition",
            "remove_duplicates": False
        },
        {
            "articles": [
                {
                    "id": "l1",
                    "role": "labeled_reference"
                }
            ],
            "policy_name": "labeled_reference",
            "remove_duplicates": False
        },
        {
            "articles": [
                {
                    "id": "m1",
                    "role": "multimodal_reference"
                }
            ],
            "policy_name": "multimodal_reference",
            "remove_duplicates": False
        }
    ]


def test_prepare_extracted_articles_skips_empty_groups(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_prepare_articles(
        articles: list[Any],
        *,
        policy_name: str,
        remove_duplicates: bool
    ) -> SimpleNamespace:
        calls.append(policy_name)
        return make_preparation_result(articles)

    monkeypatch.setattr(
        module,
        "prepare_articles",
        fake_prepare_articles
    )

    result = prepare_extracted_articles([
        {
            "id": "1",
            "role": "labeled_reference"
        }
    ])

    assert result == [
        {
            "id": "1",
            "role": "labeled_reference"
        }
    ]
    assert calls == ["labeled_reference"]


def test_prepare_extracted_articles_logs_rejection_reasons(
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
            rejection_reasons={"titre_absent": 1}
        )
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: logged_messages.append(args)
    )

    result = prepare_extracted_articles([
        {
            "id": "1",
            "role": "acquisition"
        },
        {
            "id": "2",
            "role": "acquisition"
        }
    ])

    assert result == []
    assert (
        "Motifs de rejet %s : %s.",
        "acquisition",
        {"titre_absent": 1}
    ) in logged_messages


# Images

def test_process_images_stage_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Traitement des images"):
        process_images_stage(
            "invalid",  # type: ignore[arg-type]
            require_image=False
        )


def test_process_images_stage_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "process_article_images",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = process_images_stage(
        [],
        require_image=False
    )

    assert result == []
    assert calls == []


def test_process_images_stage_calls_image_service(
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

    result = process_images_stage(
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


def test_process_images_stage_preserves_none_directories(
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

    result = process_images_stage(
        [{"id": "1"}],
        require_image=False,
        allowed_directories=None
    )

    assert result == [{"id": "1"}]
    assert received_directories == [None]


# Validation finale

def test_finalize_articles_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Validation finale"):
        finalize_articles(
            "invalid",  # type: ignore[arg-type]
            require_image=False
        )


def test_finalize_articles_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "validate_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = finalize_articles(
        [],
        require_image=False
    )

    assert result == []
    assert calls == []


def test_finalize_articles_calls_validator(
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

    result = finalize_articles(
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


# Stockage

def test_store_articles_stage_rejects_invalid_collection() -> None:
    with pytest.raises(TypeError, match="Stockage"):
        store_articles_stage(
            "invalid"  # type: ignore[arg-type]
        )


def test_store_articles_stage_returns_false_for_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "save_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = store_articles_stage([])

    assert result is False
    assert calls == []


def test_store_articles_stage_returns_false_when_no_format(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "save_articles",
        lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = store_articles_stage(
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
def test_store_articles_stage_calls_storage_service(
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

    result = store_articles_stage(
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


def test_store_articles_stage_propagates_storage_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "save_articles",
        lambda *args, **kwargs: False
    )

    result = store_articles_stage([{"id": "1"}])

    assert result is False


# Pipeline principal

def test_run_pipeline_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extracted = [{"id": "1"}]
    prepared = [{"id": "1", "prepared": True}]
    with_images = [{"id": "1", "image_download_status": "success"}]
    final = [{"id": "1", "valid": True}]
    calls: list[tuple[str, Any]] = []

    def fake_extract_all_sources(**kwargs: Any) -> SimpleNamespace:
        calls.append(("extract", kwargs))
        return make_extraction_result(extracted)

    def fake_prepare(
        articles: list[Any],
        remove_duplicates: bool
    ) -> list[dict[str, Any]]:
        calls.append((
            "prepare",
            {
                "articles": articles,
                "remove_duplicates": remove_duplicates
            }
        ))
        return prepared

    def fake_process_images(
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

    monkeypatch.setattr(
        module,
        "extract_all_sources",
        fake_extract_all_sources
    )
    monkeypatch.setattr(
        module,
        "prepare_extracted_articles",
        fake_prepare
    )
    monkeypatch.setattr(
        module,
        "process_images_stage",
        fake_process_images
    )
    monkeypatch.setattr(
        module,
        "finalize_articles",
        fake_finalize
    )
    monkeypatch.setattr(
        module,
        "store_articles_stage",
        fake_store
    )

    result = run_pipeline(
        extractor_names=["rss", "newsapi"],
        require_image=True,
        allowed_directories=["data/images"],
        save_json=True,
        save_csv=False,
        fail_on_extractor_error=True
    )

    assert result is final

    assert calls == [
        (
            "extract",
            {
                "extractor_names": ["rss", "newsapi"],
                "fail_if_empty": False,
                "fail_on_extractor_error": True
            }
        ),
        (
            "prepare",
            {
                "articles": extracted,
                "remove_duplicates": True
            }
        ),
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
    ("stage_name", "expected_reason"),
    [
        (
            "extraction",
            "aucun article extrait"
        ),
        (
            "preparation",
            "aucun article après préparation"
        ),
        (
            "images",
            "aucun article après traitement des images"
        ),
        (
            "validation",
            "aucun article après validation finale"
        )
    ]
)
def test_run_pipeline_stops_when_stage_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    stage_name: str,
    expected_reason: str
) -> None:
    articles = [{"id": "1"}]
    stopped_reasons: list[str] = []
    storage_calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "extract_all_sources",
        lambda **kwargs: make_extraction_result(
            [] if stage_name == "extraction" else articles
        )
    )
    monkeypatch.setattr(
        module,
        "prepare_extracted_articles",
        lambda received, remove_duplicates=True: (
            []
            if stage_name == "preparation"
            else articles
        )
    )
    monkeypatch.setattr(
        module,
        "process_images_stage",
        lambda received, require_image, allowed_directories=None: (
            []
            if stage_name == "images"
            else articles
        )
    )
    monkeypatch.setattr(
        module,
        "finalize_articles",
        lambda received, require_image: (
            []
            if stage_name == "validation"
            else articles
        )
    )
    monkeypatch.setattr(
        module,
        "store_articles_stage",
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

    result = run_pipeline()

    assert result == []
    assert stopped_reasons == [expected_reason]
    assert storage_calls == []


def test_run_pipeline_returns_articles_when_storage_fails(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [{"id": "1"}]
    logged_errors: list[str] = []

    monkeypatch.setattr(
        module,
        "extract_all_sources",
        lambda **kwargs: make_extraction_result(articles)
    )
    monkeypatch.setattr(
        module,
        "prepare_extracted_articles",
        lambda received, remove_duplicates=True: articles
    )
    monkeypatch.setattr(
        module,
        "process_images_stage",
        lambda received, require_image, allowed_directories=None: articles
    )
    monkeypatch.setattr(
        module,
        "finalize_articles",
        lambda received, require_image: articles
    )
    monkeypatch.setattr(
        module,
        "store_articles_stage",
        lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda message: logged_errors.append(message)
    )

    result = run_pipeline()

    assert result is articles
    assert logged_errors == [
        "Pipeline terminé avec une erreur de stockage."
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
def test_run_pipeline_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
    failing_stage: str
) -> None:
    articles = [{"id": "1"}]
    logged_exceptions: list[tuple[str, BaseException]] = []

    def raise_if_stage(stage: str) -> None:
        if failing_stage == stage:
            raise RuntimeError(f"Erreur {stage}")

    def fake_extract(**kwargs: Any) -> SimpleNamespace:
        raise_if_stage("extraction")
        return make_extraction_result(articles)

    def fake_prepare(
        received: list[Any],
        remove_duplicates: bool = True
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

    monkeypatch.setattr(
        module,
        "extract_all_sources",
        fake_extract
    )
    monkeypatch.setattr(
        module,
        "prepare_extracted_articles",
        fake_prepare
    )
    monkeypatch.setattr(
        module,
        "process_images_stage",
        fake_images
    )
    monkeypatch.setattr(
        module,
        "finalize_articles",
        fake_validation
    )
    monkeypatch.setattr(
        module,
        "store_articles_stage",
        fake_storage
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda message, error: logged_exceptions.append(
            (message, error)
        )
    )

    result = run_pipeline()

    assert result == []
    assert len(logged_exceptions) == 1

    message, error = logged_exceptions[0]

    assert message == (
        "Erreur inattendue pendant le pipeline principal : %s"
    )
    assert isinstance(error, RuntimeError)
    assert str(error) == f"Erreur {failing_stage}"


def test_run_pipeline_uses_default_arguments(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extraction_calls: list[dict[str, Any]] = []

    def fake_extract(**kwargs: Any) -> SimpleNamespace:
        extraction_calls.append(kwargs)
        return make_extraction_result([])

    monkeypatch.setattr(
        module,
        "extract_all_sources",
        fake_extract
    )

    result = run_pipeline()

    assert result == []
    assert extraction_calls == [
        {
            "extractor_names": None,
            "fail_if_empty": False,
            "fail_on_extractor_error": False
        }
    ]