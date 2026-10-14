"""Tests du service de chargement PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/postgres/test_postgres_load_service.py` (commit 93632c5).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

import src.storage.postgres.postgres_load_service as module
from src.storage.postgres.postgres_load_service import (
    build_run_metadata,
    count_images,
    finalize_failed_pipeline_run,
    load_batch_to_postgres,
    resolve_image_count,
    rollback_connection
)


# Connexion simulée

class DummyConnection:
    """Connexion PostgreSQL minimale utilisée par les tests."""

    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.commit_count = 0
        self.rollback_count = 0
        self.operations: list[str] = []

    def commit(self) -> None:
        self.operations.append("commit")
        self.commit_count += 1

        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.operations.append("rollback")
        self.rollback_count += 1

        if self.rollback_error is not None:
            raise self.rollback_error


# Données communes

@pytest.fixture
def pipeline_metadata() -> dict[str, Any]:
    """Retourne les métadonnées minimales d'un pipeline."""

    return {
        "dag_id": "checkit_load",
        "airflow_run_id": "manual__2026-07-27",
        "batch_id": "batch_20260727",
        "extracted_count": 10,
        "transformed_count": 8,
        "rejected_count": 2,
        "duplicate_count": 1,
        "extraction_duration_seconds": 4.25,
        "transformation_duration_seconds": 2.5,
        "run_metadata": {
            "source_count": 3
        }
    }


@pytest.fixture
def articles() -> list[dict[str, Any]]:
    """Retourne un article minimal."""

    return [
        {
            "id": "article-1",
            "source": "Reuters",
            "title": "Titre de test"
        }
    ]


@pytest.fixture
def images() -> list[dict[str, Any]]:
    """Retourne plusieurs états d'image."""

    return [
        {
            "local_path": "data/images/valid.jpg",
            "validation_status": "valid"
        },
        {
            "image_path": "data/images/invalid.jpg",
            "validation_status": "invalid"
        },
        {
            "validation_status": "validation_error"
        },
        {
            "validation_status": "pending"
        }
    ]


# Compteurs des images

def test_count_images() -> None:
    images = [
        {
            "local_path": "image-1.jpg",
            "validation_status": "valid"
        },
        {
            "image_path": "image-2.jpg",
            "validation_status": "invalid"
        },
        {
            "local_path": "",
            "validation_status": "validation_error"
        },
        {
            "validation_status": "pending"
        },
        {
            "local_path": "image-3.jpg",
            "validation_status": ""
        },
        "invalid"
    ]

    result = count_images(images)

    assert result == {
        "downloaded": 3,
        "valid": 1,
        "invalid": 2,
        "pending": 2
    }


def test_count_images_accepts_missing_values() -> None:
    result = count_images([
        {},
        {
            "local_path": None,
            "image_path": None,
            "validation_status": None
        }
    ])

    assert result == {
        "downloaded": 0,
        "valid": 0,
        "invalid": 0,
        "pending": 2
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("valid", {"valid": 1, "invalid": 0, "pending": 0}),
        ("VALID", {"valid": 1, "invalid": 0, "pending": 0}),
        ("invalid", {"valid": 0, "invalid": 1, "pending": 0}),
        (
            "validation_error",
            {"valid": 0, "invalid": 1, "pending": 0}
        ),
        ("pending", {"valid": 0, "invalid": 0, "pending": 1}),
        ("unknown", {"valid": 0, "invalid": 0, "pending": 1})
    ]
)
def test_count_images_classifies_validation_status(
    status: str,
    expected: dict[str, int]
) -> None:
    result = count_images([
        {
            "validation_status": status
        }
    ])

    assert result["valid"] == expected["valid"]
    assert result["invalid"] == expected["invalid"]
    assert result["pending"] == expected["pending"]


# Résolution des compteurs

def test_resolve_image_count_uses_metadata_value() -> None:
    result = resolve_image_count(
        {
            "images_valid_count": "12"
        },
        "images_valid_count",
        5
    )

    assert result == 12


def test_resolve_image_count_uses_calculated_fallback() -> None:
    result = resolve_image_count(
        {},
        "images_valid_count",
        5
    )

    assert result == 5


def test_resolve_image_count_rejects_invalid_metadata() -> None:
    result = resolve_image_count(
        {
            "images_valid_count": "invalid"
        },
        "images_valid_count",
        5
    )

    assert result == 5


# Métadonnées du pipeline

def test_build_run_metadata(
    pipeline_metadata: dict[str, Any]
) -> None:
    result = build_run_metadata(pipeline_metadata)

    assert result == {
        "duplicate_count": 1,
        "source_count": 3
    }


def test_build_run_metadata_handles_missing_metadata() -> None:
    result = build_run_metadata({
        "duplicate_count": "2"
    })

    assert result == {
        "duplicate_count": 2
    }


@pytest.mark.parametrize(
    "run_metadata",
    [
        None,
        "invalid",
        42,
        [],
        ()
    ]
)
def test_build_run_metadata_ignores_invalid_mapping(
    run_metadata: Any
) -> None:
    result = build_run_metadata({
        "duplicate_count": 3,
        "run_metadata": run_metadata
    })

    assert result == {
        "duplicate_count": 3
    }


def test_build_run_metadata_allows_custom_duplicate_count_override() -> None:
    result = build_run_metadata({
        "duplicate_count": 2,
        "run_metadata": {
            "duplicate_count": 9,
            "source_count": 4
        }
    })

    assert result == {
        "duplicate_count": 9,
        "source_count": 4
    }


# Rollback

def test_rollback_connection() -> None:
    connection = DummyConnection()
    error = RuntimeError("Erreur simulée")

    rollback_connection(connection, error)

    assert connection.rollback_count == 1
    assert connection.operations == ["rollback"]
    assert error.__notes__ == [] if hasattr(error, "__notes__") else True


def test_rollback_connection_adds_note_on_failure() -> None:
    connection = DummyConnection(
        rollback_error=RuntimeError("Rollback impossible")
    )
    error = RuntimeError("Erreur principale")

    rollback_connection(connection, error)

    assert connection.rollback_count == 1
    assert error.__notes__ == [
        "Rollback PostgreSQL échoué : Rollback impossible"
    ]


# Finalisation d'un échec

def test_finalize_failed_pipeline_run_commits(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection()
    pipeline_run_id = uuid4()
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        lambda connection, **kwargs: calls.append(kwargs)
    )

    error = RuntimeError("Chargement impossible")

    finalize_failed_pipeline_run(
        connection,
        pipeline_run_id=pipeline_run_id,
        duration_seconds=1.5,
        images_downloaded_count=4,
        images_valid_count=2,
        images_invalid_count=1,
        images_pending_count=1,
        error=error
    )

    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert calls == [
        {
            "pipeline_run_id": pipeline_run_id,
            "status": "failed",
            "loaded_count": 0,
            "load_duration_seconds": 1.5,
            "images_downloaded_count": 4,
            "images_valid_count": 2,
            "images_invalid_count": 1,
            "images_pending_count": 1,
            "error_message": "Chargement impossible"
        }
    ]


def test_finalize_failed_pipeline_run_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection()
    pipeline_run_id = uuid4()

    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Finalisation impossible")
        )
    )

    error = RuntimeError("Erreur principale")

    finalize_failed_pipeline_run(
        connection,
        pipeline_run_id=pipeline_run_id,
        duration_seconds=1.5,
        images_downloaded_count=4,
        images_valid_count=2,
        images_invalid_count=1,
        images_pending_count=1,
        error=error
    )

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert error.__notes__ == [
        "La finalisation du pipeline_run a échoué : "
        "Finalisation impossible"
    ]


def test_finalize_failed_pipeline_run_handles_rollback_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection(
        rollback_error=RuntimeError("Rollback impossible")
    )

    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Finalisation impossible")
        )
    )

    error = RuntimeError("Erreur principale")

    finalize_failed_pipeline_run(
        connection,
        pipeline_run_id=uuid4(),
        duration_seconds=1.5,
        images_downloaded_count=4,
        images_valid_count=2,
        images_invalid_count=1,
        images_pending_count=1,
        error=error
    )

    assert connection.rollback_count == 1
    assert error.__notes__ == [
        "La finalisation du pipeline_run a échoué : "
        "Finalisation impossible"
    ]


# Chargement complet

def test_load_batch_to_postgres_rejects_empty_articles(
    pipeline_metadata: dict[str, Any]
) -> None:
    connection = DummyConnection()

    with pytest.raises(
        RuntimeError,
        match="Aucun article à charger dans PostgreSQL"
    ):
        load_batch_to_postgres(
            connection,
            articles=[],
            images=[],
            labels=[],
            features=[],
            pipeline_metadata=pipeline_metadata
        )

    assert connection.commit_count == 0
    assert connection.rollback_count == 0


def test_load_batch_to_postgres_success(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_metadata: dict[str, Any],
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]]
) -> None:
    connection = DummyConnection()
    pipeline_run_id = UUID(
        "12345678-1234-5678-1234-567812345678"
    )

    operations: list[str] = []
    create_calls: list[dict[str, Any]] = []
    load_calls: list[dict[str, Any]] = []
    finalize_calls: list[dict[str, Any]] = []

    def fake_create(
        connection: DummyConnection,
        **kwargs: Any
    ) -> UUID:
        operations.append("create")
        create_calls.append(kwargs)
        return pipeline_run_id

    def fake_load(
        connection: DummyConnection,
        **kwargs: Any
    ) -> dict[str, int]:
        operations.append("load")
        load_calls.append(kwargs)
        return {
            "loaded_articles": 1,
            "loaded_images": 4,
            "loaded_labels": 1,
            "loaded_features": 1
        }

    def fake_finalize(
        connection: DummyConnection,
        **kwargs: Any
    ) -> None:
        operations.append("finalize")
        finalize_calls.append(kwargs)

    original_commit = connection.commit

    def tracked_commit() -> None:
        operations.append("commit")
        original_commit()

    connection.commit = tracked_commit  # type: ignore[method-assign]

    monkeypatch.setattr(
        module,
        "create_pipeline_run",
        fake_create
    )
    monkeypatch.setattr(
        module,
        "load_transformed_payload",
        fake_load
    )
    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        fake_finalize
    )
    monkeypatch.setattr(
        module,
        "perf_counter",
        iter([10.0, 12.345]).__next__
    )

    result = load_batch_to_postgres(
        connection,
        articles=articles,
        images=images,
        labels=[{"article_id": "article-1", "label": "true"}],
        features=[{"article_id": "article-1", "title_length": 13}],
        pipeline_metadata=pipeline_metadata
    )

    assert operations == [
        "create",
        "load",
        "finalize",
        "commit"
    ]

    assert connection.commit_count == 1
    assert connection.rollback_count == 0

    assert create_calls == [
        {
            "dag_id": "checkit_load",
            "airflow_run_id": "manual__2026-07-27",
            "batch_id": "batch_20260727",
            "extracted_count": 10,
            "transformed_count": 8,
            "rejected_count": 2,
            "extraction_duration_seconds": 4.25,
            "transformation_duration_seconds": 2.5,
            "images_downloaded_count": 2,
            "images_valid_count": 1,
            "images_invalid_count": 2,
            "images_pending_count": 1,
            "run_metadata": {
                "duplicate_count": 1,
                "source_count": 3
            }
        }
    ]

    assert load_calls == [
        {
            "articles": articles,
            "images": images,
            "labels": [
                {
                    "article_id": "article-1",
                    "label": "true"
                }
            ],
            "features": [
                {
                    "article_id": "article-1",
                    "title_length": 13
                }
            ],
            "pipeline_run_id": pipeline_run_id
        }
    ]

    assert finalize_calls == [
        {
            "pipeline_run_id": pipeline_run_id,
            "status": "success",
            "loaded_count": 1,
            "load_duration_seconds": 2.345,
            "images_downloaded_count": 2,
            "images_valid_count": 1,
            "images_invalid_count": 2,
            "images_pending_count": 1
        }
    ]

    assert result == {
        "pipeline_run_id": str(pipeline_run_id),
        "status": "success",
        "load_duration_seconds": 2.345,
        "images_downloaded_count": 2,
        "images_valid_count": 1,
        "images_invalid_count": 2,
        "images_pending_count": 1,
        "loaded_articles": 1,
        "loaded_images": 4,
        "loaded_labels": 1,
        "loaded_features": 1
    }


def test_load_batch_to_postgres_uses_metadata_image_counts(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_metadata: dict[str, Any],
    articles: list[dict[str, Any]]
) -> None:
    connection = DummyConnection()
    pipeline_run_id = uuid4()
    create_calls: list[dict[str, Any]] = []

    pipeline_metadata.update({
        "images_downloaded_count": 20,
        "images_valid_count": 15,
        "images_invalid_count": 3,
        "images_pending_count": 2
    })

    def fake_create(
        connection: DummyConnection,
        **kwargs: Any
    ) -> UUID:
        create_calls.append(kwargs)
        return pipeline_run_id

    monkeypatch.setattr(
        module,
        "create_pipeline_run",
        fake_create
    )
    monkeypatch.setattr(
        module,
        "load_transformed_payload",
        lambda *args, **kwargs: {
            "loaded_articles": 1
        }
    )
    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        lambda *args, **kwargs: None
    )

    result = load_batch_to_postgres(
        connection,
        articles=articles,
        images=[],
        labels=[],
        features=[],
        pipeline_metadata=pipeline_metadata
    )

    assert create_calls[0]["images_downloaded_count"] == 20
    assert create_calls[0]["images_valid_count"] == 15
    assert create_calls[0]["images_invalid_count"] == 3
    assert create_calls[0]["images_pending_count"] == 2

    assert result["images_downloaded_count"] == 20
    assert result["images_valid_count"] == 15
    assert result["images_invalid_count"] == 3
    assert result["images_pending_count"] == 2


def test_load_batch_to_postgres_rolls_back_and_finalizes_failure(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_metadata: dict[str, Any],
    articles: list[dict[str, Any]]
) -> None:
    connection = DummyConnection()
    pipeline_run_id = uuid4()

    finalize_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "create_pipeline_run",
        lambda *args, **kwargs: pipeline_run_id
    )
    monkeypatch.setattr(
        module,
        "load_transformed_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Échec du chargement")
        )
    )

    def fake_finalize(
        connection: DummyConnection,
        **kwargs: Any
    ) -> None:
        finalize_calls.append(kwargs)

    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        fake_finalize
    )
    monkeypatch.setattr(
        module,
        "perf_counter",
        iter([20.0, 21.5]).__next__
    )

    with pytest.raises(
        RuntimeError,
        match="Échec du chargement"
    ):
        load_batch_to_postgres(
            connection,
            articles=articles,
            images=[],
            labels=[],
            features=[],
            pipeline_metadata=pipeline_metadata
        )

    assert connection.rollback_count == 1
    assert connection.commit_count == 1

    assert finalize_calls == [
        {
            "pipeline_run_id": pipeline_run_id,
            "status": "failed",
            "loaded_count": 0,
            "load_duration_seconds": 1.5,
            "images_downloaded_count": 0,
            "images_valid_count": 0,
            "images_invalid_count": 0,
            "images_pending_count": 0,
            "error_message": "Échec du chargement"
        }
    ]


def test_load_batch_to_postgres_handles_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_metadata: dict[str, Any],
    articles: list[dict[str, Any]]
) -> None:
    connection = DummyConnection()
    logged_errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "create_pipeline_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Création impossible")
        )
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args: logged_errors.append(args)
    )

    with pytest.raises(
        RuntimeError,
        match="Création impossible"
    ):
        load_batch_to_postgres(
            connection,
            articles=articles,
            images=[],
            labels=[],
            features=[],
            pipeline_metadata=pipeline_metadata
        )

    assert connection.rollback_count == 1
    assert connection.commit_count == 0
    assert len(logged_errors) == 1

    message, exception = logged_errors[0]

    assert message == (
        "La création du pipeline_run PostgreSQL a échoué : %s"
    )
    assert isinstance(exception, RuntimeError)
    assert str(exception) == "Création impossible"


def test_load_batch_to_postgres_rolls_back_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_metadata: dict[str, Any],
    articles: list[dict[str, Any]]
) -> None:
    connection = DummyConnection(
        commit_error=RuntimeError("Commit impossible")
    )
    pipeline_run_id = uuid4()
    finalize_statuses: list[str] = []

    monkeypatch.setattr(
        module,
        "create_pipeline_run",
        lambda *args, **kwargs: pipeline_run_id
    )
    monkeypatch.setattr(
        module,
        "load_transformed_payload",
        lambda *args, **kwargs: {
            "loaded_articles": 1
        }
    )

    def fake_finalize(
        connection: DummyConnection,
        **kwargs: Any
    ) -> None:
        finalize_statuses.append(kwargs["status"])

    monkeypatch.setattr(
        module,
        "finalize_pipeline_run",
        fake_finalize
    )

    with pytest.raises(
        RuntimeError,
        match="Commit impossible"
    ):
        load_batch_to_postgres(
            connection,
            articles=articles,
            images=[],
            labels=[],
            features=[],
            pipeline_metadata=pipeline_metadata
        )

    assert connection.rollback_count >= 1
    assert finalize_statuses == [
        "success",
        "failed"
    ]