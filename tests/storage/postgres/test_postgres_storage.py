"""Tests des opérations SQL PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/postgres/test_postgres_storage.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import pytest
from psycopg2.extras import Json

import src.storage.postgres.postgres_storage as module
from src.storage.postgres.postgres_storage import (
    PIPELINE_VERSION,
    PRODUCER_NAME,
    build_source_display_name,
    create_pipeline_run,
    finalize_pipeline_run,
    find_existing_article_id,
    infer_source_type,
    replace_article_features,
    replace_primary_image,
    upsert_article,
    upsert_label,
    upsert_source,
    validate_feature_record,
    validate_label_record
)


# Doubles PostgreSQL

class DummyCursor:
    """Curseur PostgreSQL enregistrant les requêtes exécutées."""

    def __init__(
        self,
        fetch_results: list[Any] | None = None
    ) -> None:
        self.fetch_results = list(fetch_results or [])
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self) -> DummyCursor:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any
    ) -> None:
        return None

    def execute(
        self,
        query: str,
        parameters: tuple[Any, ...] | None = None
    ) -> None:
        self.executions.append((query, parameters))

    def fetchone(self) -> Any:
        if not self.fetch_results:
            return None

        return self.fetch_results.pop(0)


class DummyConnection:
    """Connexion PostgreSQL minimale utilisée par les tests."""

    def __init__(
        self,
        fetch_results: list[Any] | None = None
    ) -> None:
        self.cursor_instance = DummyCursor(fetch_results)
        self.commit_count = 0

    def cursor(self) -> DummyCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commit_count += 1


def get_json_value(value: Any) -> Any:
    """Extrait la valeur Python contenue dans un objet Json."""

    assert isinstance(value, Json)
    return value.adapted


# Sources

@pytest.mark.parametrize(
    ("source_key", "expected"),
    [
        ("openagenda", "api"),
    ]
)
def test_infer_source_type(
    source_key: str,
    expected: str
) -> None:
    assert infer_source_type(source_key) == expected


def test_infer_source_type_rejects_unknown_source() -> None:
    with pytest.raises(
        ValueError,
        match="Type inconnu pour la source 'unknown'"
    ):
        infer_source_type("unknown")


@pytest.mark.parametrize(
    ("source_key", "expected"),
    [
        ("le_monde", "Le Monde"),
        ("guardian_api", "Guardian Api"),
        ("google_fact_check", "Google Fact Check"),
        ("reddit", "Reddit")
    ]
)
def test_build_source_display_name(
    source_key: str,
    expected: str
) -> None:
    assert build_source_display_name(source_key) == expected


def test_upsert_source() -> None:
    connection = DummyConnection(
        fetch_results=[(42,)]
    )

    result = upsert_source(
        connection,
        source_key="openagenda",
        default_language="fr"
    )

    assert result == 42
    assert connection.commit_count == 0
    assert len(connection.cursor_instance.executions) == 1

    query, parameters = connection.cursor_instance.executions[0]

    assert "INSERT INTO checkit.sources" in query
    assert "ON CONFLICT (source_key)" in query
    assert parameters == (
        "openagenda",
        "Openagenda",
        "api",
        "fr"
    )


def test_upsert_source_rejects_missing_returned_id() -> None:
    connection = DummyConnection(
        fetch_results=[None]
    )

    with pytest.raises(
        RuntimeError,
        match="Impossible de créer la source 'openagenda'"
    ):
        upsert_source(
            connection,
            source_key="openagenda"
        )


# Exécutions du pipeline

def test_create_pipeline_run() -> None:
    pipeline_run_id = UUID(
        "12345678-1234-5678-1234-567812345678"
    )
    connection = DummyConnection(
        fetch_results=[(pipeline_run_id,)]
    )

    result = create_pipeline_run(
        connection,
        dag_id="checkit_load",
        airflow_run_id="manual__2026-07-27",
        batch_id="batch-001",
        extracted_count=100,
        transformed_count=80,
        rejected_count=20,
        extraction_duration_seconds=4.5,
        transformation_duration_seconds=2.25,
        images_downloaded_count=50,
        images_valid_count=40,
        images_invalid_count=8,
        images_pending_count=2,
        run_metadata={
            "duplicate_count": 3
        }
    )

    assert result == pipeline_run_id
    assert connection.commit_count == 1

    query, parameters = connection.cursor_instance.executions[0]

    assert "INSERT INTO checkit.pipeline_runs" in query
    assert "RETURNING id" in query

    assert parameters is not None
    assert parameters[:-1] == (
        "checkit_load",
        "manual__2026-07-27",
        PIPELINE_VERSION,
        4.5,
        2.25,
        100,
        80,
        80,
        20,
        50,
        40,
        8,
        2
    )
    assert get_json_value(parameters[-1]) == {
        "batch_id": "batch-001",
        "duplicate_count": 3
    }


def test_create_pipeline_run_allows_metadata_to_override_batch_id() -> None:
    connection = DummyConnection(
        fetch_results=[(UUID(int=1),)]
    )

    create_pipeline_run(
        connection,
        dag_id="dag",
        airflow_run_id="run",
        batch_id="original",
        extracted_count=1,
        transformed_count=1,
        rejected_count=0,
        run_metadata={
            "batch_id": "custom"
        }
    )

    parameters = connection.cursor_instance.executions[0][1]

    assert parameters is not None
    assert get_json_value(parameters[-1]) == {
        "batch_id": "custom"
    }


def test_create_pipeline_run_rejects_missing_result() -> None:
    connection = DummyConnection(
        fetch_results=[None]
    )

    with pytest.raises(
        RuntimeError,
        match="Impossible de créer l'exécution du pipeline"
    ):
        create_pipeline_run(
            connection,
            dag_id="dag",
            airflow_run_id="run",
            batch_id="batch",
            extracted_count=1,
            transformed_count=1,
            rejected_count=0
        )

    assert connection.commit_count == 0


def test_finalize_pipeline_run() -> None:
    pipeline_run_id = UUID(
        "12345678-1234-5678-1234-567812345678"
    )
    connection = DummyConnection()

    finalize_pipeline_run(
        connection,
        pipeline_run_id=pipeline_run_id,
        status="success",
        loaded_count=75,
        load_duration_seconds=1.75,
        images_downloaded_count=50,
        images_valid_count=40,
        images_invalid_count=8,
        images_pending_count=2
    )

    assert connection.commit_count == 1

    query, parameters = connection.cursor_instance.executions[0]

    assert "UPDATE checkit.pipeline_runs" in query
    assert parameters == (
        "success",
        75,
        1.75,
        50,
        40,
        8,
        2,
        None,
        pipeline_run_id
    )


def test_finalize_pipeline_run_records_failure() -> None:
    connection = DummyConnection()
    pipeline_run_id = UUID(int=2)

    finalize_pipeline_run(
        connection,
        pipeline_run_id=pipeline_run_id,
        status="failed",
        loaded_count=0,
        error_message="Erreur PostgreSQL"
    )

    parameters = connection.cursor_instance.executions[0][1]

    assert parameters == (
        "failed",
        0,
        None,
        0,
        0,
        0,
        0,
        "Erreur PostgreSQL",
        pipeline_run_id
    )


# Articles

def test_find_existing_article_id_without_url() -> None:
    connection = DummyConnection()

    assert find_existing_article_id(
        connection,
        None
    ) is None

    assert connection.cursor_instance.executions == []


def test_find_existing_article_id_found() -> None:
    connection = DummyConnection(
        fetch_results=[("database-id",)]
    )

    result = find_existing_article_id(
        connection,
        "https://example.com/article"
    )

    assert result == "database-id"

    query, parameters = connection.cursor_instance.executions[0]

    assert "FROM checkit.articles" in query
    assert parameters == (
        "https://example.com/article",
    )


def test_find_existing_article_id_not_found() -> None:
    connection = DummyConnection(
        fetch_results=[None]
    )

    result = find_existing_article_id(
        connection,
        "https://example.com/article"
    )

    assert result is None


def test_upsert_article_uses_requested_id_without_existing_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection()
    pipeline_run_id = UUID(int=3)

    monkeypatch.setattr(
        module,
        "find_existing_article_id",
        lambda *args, **kwargs: None
    )

    article = {
        "id": "requested-id",
        "title": "Titre",
        "canonical_url": "https://example.com/article",
        "raw_payload": {
            "origin": "test"
        }
    }

    result = upsert_article(
        connection,
        article=article,
        source_id=12,
        pipeline_run_id=pipeline_run_id
    )

    assert result == "requested-id"

    query, parameters = connection.cursor_instance.executions[0]

    assert "INSERT INTO checkit.articles" in query
    assert parameters is not None
    assert parameters[0] == "requested-id"
    assert parameters[1] == 12
    assert parameters[2] == pipeline_run_id
    assert parameters[4] == "Titre"
    assert parameters[15] == "valid"
    assert parameters[17] == PIPELINE_VERSION
    assert get_json_value(parameters[-1]) == {
        "origin": "test"
    }


def test_upsert_article_reuses_existing_canonical_url_id(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection()

    monkeypatch.setattr(
        module,
        "find_existing_article_id",
        lambda *args, **kwargs: "existing-id"
    )

    result = upsert_article(
        connection,
        article={
            "id": "requested-id",
            "title": "Titre",
            "canonical_url": "https://example.com/article"
        },
        source_id=1,
        pipeline_run_id=UUID(int=4)
    )

    assert result == "existing-id"

    parameters = connection.cursor_instance.executions[0][1]

    assert parameters is not None
    assert parameters[0] == "existing-id"


# Images

def test_replace_primary_image() -> None:
    connection = DummyConnection()

    image = {
        "remote_url": "https://example.com/image.jpg",
        "local_path": "data/images/image.jpg",
        "file_name": "image.jpg",
        "file_extension": ".jpg",
        "file_format": "JPEG",
        "mime_type": "image/jpeg",
        "width": 1200,
        "height": 800,
        "is_primary": True,
        "is_valid": True,
        "validation_status": "valid",
        "image_metadata": {
            "animated": False
        }
    }

    replace_primary_image(
        connection,
        image=image,
        article_id="article-1"
    )

    assert len(connection.cursor_instance.executions) == 2

    delete_query, delete_parameters = (
        connection.cursor_instance.executions[0]
    )
    insert_query, insert_parameters = (
        connection.cursor_instance.executions[1]
    )

    assert "DELETE FROM checkit.images" in delete_query
    assert delete_parameters == (
        "article-1",
    )

    assert "INSERT INTO checkit.images" in insert_query
    assert insert_parameters is not None
    assert insert_parameters[0] == "article-1"
    assert insert_parameters[1] == image["remote_url"]
    assert insert_parameters[15] is True
    assert insert_parameters[19] is True
    assert insert_parameters[20] == "valid"
    assert get_json_value(insert_parameters[-1]) == {
        "animated": False
    }


# Labels

@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (
            {
                "label": "true",
                "label_type": "veracity"
            },
            (True, "")
        ),
        (
            {
                "label_type": "veracity"
            },
            (False, "champ label absent")
        ),
        (
            {
                "label": "true"
            },
            (False, "champ label_type absent")
        ),
        (
            {
                "label": "   ",
                "label_type": "veracity"
            },
            (False, "champ label absent")
        )
    ]
)
def test_validate_label_record(
    label: Mapping[str, Any],
    expected: tuple[bool, str]
) -> None:
    assert validate_label_record(label) == expected


def test_upsert_label_ignores_invalid_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection()
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = upsert_label(
        connection,
        label={
            "label_type": "veracity"
        },
        article_id="article-1"
    )

    assert result is False
    assert connection.cursor_instance.executions == []
    assert warnings == [
        (
            "Label ignoré pour l'article %s : %s.",
            "article-1",
            "champ label absent"
        )
    ]


def test_upsert_label_inserts_new_label() -> None:
    connection = DummyConnection(
        fetch_results=[None]
    )

    label = {
        "label": "true",
        "label_type": "veracity",
        "label_source": "dataset",
        "confidence": 0.9,
        "label_metadata": {
            "original": "real"
        }
    }

    result = upsert_label(
        connection,
        label=label,
        article_id="article-1"
    )

    assert result is True
    assert len(connection.cursor_instance.executions) == 2

    select_query, select_parameters = (
        connection.cursor_instance.executions[0]
    )
    insert_query, insert_parameters = (
        connection.cursor_instance.executions[1]
    )

    assert "SELECT id" in select_query
    assert select_parameters == (
        "article-1",
        "true",
        "veracity",
        "dataset"
    )

    assert "INSERT INTO checkit.article_labels" in insert_query
    assert insert_parameters is not None
    assert insert_parameters[:4] == (
        "article-1",
        "true",
        "veracity",
        "dataset"
    )
    assert insert_parameters[6] == 0.9
    assert get_json_value(insert_parameters[-1]) == {
        "original": "real"
    }


def test_upsert_label_updates_existing_label() -> None:
    connection = DummyConnection(
        fetch_results=[(99,)]
    )

    result = upsert_label(
        connection,
        label={
            "label": "false",
            "label_type": "veracity",
            "annotator": "reviewer"
        },
        article_id="article-1"
    )

    assert result is True
    assert len(connection.cursor_instance.executions) == 2

    update_query, update_parameters = (
        connection.cursor_instance.executions[1]
    )

    assert "UPDATE checkit.article_labels" in update_query
    assert update_parameters is not None
    assert update_parameters[0] == "reviewer"
    assert update_parameters[-1] == 99


# Features

@pytest.mark.parametrize(
    ("feature", "expected"),
    [
        (
            {
                "feature_group": "text",
                "feature_name": "word_count",
                "feature_type": "numeric"
            },
            (True, "")
        ),
        (
            {
                "feature_name": "word_count",
                "feature_type": "numeric"
            },
            (False, "champ feature_group absent")
        ),
        (
            {
                "feature_group": "text",
                "feature_type": "numeric"
            },
            (False, "champ feature_name absent")
        ),
        (
            {
                "feature_group": "text",
                "feature_name": "word_count"
            },
            (False, "champ feature_type absent")
        )
    ]
)
def test_validate_feature_record(
    feature: Mapping[str, Any],
    expected: tuple[bool, str]
) -> None:
    assert validate_feature_record(feature) == expected


def test_replace_article_features() -> None:
    connection = DummyConnection()
    pipeline_run_id = UUID(int=5)

    features = [
        {
            "feature_group": "text",
            "feature_name": "word_count",
            "feature_type": "numeric",
            "numeric_value": 42,
            "json_value": {
                "unit": "words"
            },
            "feature_metadata": {
                "source": "transformer"
            }
        },
        {
            "feature_group": "text",
            "feature_name": "language",
            "feature_type": "text",
            "text_value": "fr"
        }
    ]

    result = replace_article_features(
        connection,
        features=features,
        article_id="article-1",
        pipeline_run_id=pipeline_run_id
    )

    assert result == (2, 0)
    assert len(connection.cursor_instance.executions) == 3

    delete_query, delete_parameters = (
        connection.cursor_instance.executions[0]
    )

    assert "DELETE FROM checkit.article_features" in delete_query
    assert delete_parameters == (
        "article-1",
        PRODUCER_NAME
    )

    first_insert_parameters = (
        connection.cursor_instance.executions[1][1]
    )

    assert first_insert_parameters is not None
    assert first_insert_parameters[0] == "article-1"
    assert first_insert_parameters[2] == pipeline_run_id
    assert first_insert_parameters[3:6] == (
        "text",
        "word_count",
        "numeric"
    )
    assert first_insert_parameters[6] == PIPELINE_VERSION
    assert first_insert_parameters[7] == PRODUCER_NAME
    assert first_insert_parameters[8] == PIPELINE_VERSION
    assert first_insert_parameters[9] == 42
    assert get_json_value(first_insert_parameters[12]) == {
        "unit": "words"
    }
    assert get_json_value(first_insert_parameters[-1]) == {
        "source": "transformer"
    }


def test_replace_article_features_counts_invalid_features(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = DummyConnection()
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = replace_article_features(
        connection,
        features=[
            "invalid",
            {
                "feature_group": "text"
            },
            {
                "feature_group": "text",
                "feature_name": "word_count",
                "feature_type": "numeric",
                "numeric_value": 10
            }
        ],
        article_id="article-1",
        pipeline_run_id=UUID(int=6)
    )

    assert result == (1, 2)
    assert len(connection.cursor_instance.executions) == 2

    assert warnings == [
        (
            "Feature ignorée pour l'article %s : format %s.",
            "article-1",
            "str"
        ),
        (
            "Feature ignorée pour l'article %s : %s.",
            "article-1",
            "champ feature_name absent"
        )
    ]


def test_replace_article_features_deletes_even_without_features() -> None:
    connection = DummyConnection()

    result = replace_article_features(
        connection,
        features=[],
        article_id="article-1",
        pipeline_run_id=UUID(int=7)
    )

    assert result == (0, 0)
    assert len(connection.cursor_instance.executions) == 1

    query, parameters = connection.cursor_instance.executions[0]

    assert "DELETE FROM checkit.article_features" in query
    assert parameters == (
        "article-1",
        PRODUCER_NAME
    )