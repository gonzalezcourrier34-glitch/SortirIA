"""Tests du chargeur métier PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/postgres/test_postgres_loader.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

import src.storage.postgres.postgres_loader as module
from src.storage.postgres.postgres_loader import load_transformed_payload


# Données communes

@pytest.fixture
def pipeline_run_id() -> UUID:
    """Retourne un identifiant de pipeline stable."""

    return UUID(
        "12345678-1234-5678-1234-567812345678"
    )


@pytest.fixture
def articles() -> list[dict[str, Any]]:
    """Retourne deux articles appartenant à la même source."""

    return [
        {
            "id": "article-1",
            "source_key": "reuters",
            "language": "fr",
            "title": "Premier article"
        },
        {
            "id": "article-2",
            "source_key": "reuters",
            "language": "fr",
            "title": "Deuxième article"
        }
    ]


@pytest.fixture
def images() -> list[dict[str, Any]]:
    """Retourne une image pour le premier article."""

    return [
        {
            "article_id": "article-1",
            "local_path": "data/images/article-1.jpg"
        }
    ]


@pytest.fixture
def labels() -> list[dict[str, Any]]:
    """Retourne plusieurs labels associés aux articles."""

    return [
        {
            "article_id": "article-1",
            "label": "true"
        },
        {
            "article_id": "article-1",
            "label": "verified"
        },
        {
            "article_id": "article-2",
            "label": "false"
        }
    ]


@pytest.fixture
def features() -> list[dict[str, Any]]:
    """Retourne plusieurs features associées aux articles."""

    return [
        {
            "article_id": "article-1",
            "name": "title_length",
            "value": 15
        },
        {
            "article_id": "article-1",
            "name": "word_count",
            "value": 3
        },
        {
            "article_id": "article-2",
            "name": "title_length",
            "value": 17
        }
    ]


# Validation du payload

def test_load_transformed_payload_validates_references_first(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    operations: list[str] = []

    def fake_validate(**kwargs: Any) -> None:
        operations.append("validate")

    def fake_upsert_source(*args: Any, **kwargs: Any) -> int:
        operations.append("source")
        return 1

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        fake_validate
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        fake_upsert_source
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (0, 0)
    )

    load_transformed_payload(
        object(),
        articles=[
            {
                "id": "article-1",
                "source_key": "reuters"
            }
        ],
        images=[],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert operations == [
        "validate",
        "source"
    ]


def test_load_transformed_payload_stops_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: (_ for _ in ()).throw(
            ValueError("Références invalides")
        )
    )

    source_calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda *args, **kwargs: source_calls.append(kwargs)
    )

    with pytest.raises(
        ValueError,
        match="Références invalides"
    ):
        load_transformed_payload(
            object(),
            articles=[],
            images=[],
            labels=[],
            features=[],
            pipeline_run_id=pipeline_run_id
        )

    assert source_calls == []


# Chargement nominal

def test_load_transformed_payload_loads_all_entities(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID,
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    features: list[dict[str, Any]]
) -> None:
    connection = object()

    validation_calls: list[dict[str, Any]] = []
    source_calls: list[dict[str, Any]] = []
    article_calls: list[dict[str, Any]] = []
    image_calls: list[dict[str, Any]] = []
    label_calls: list[dict[str, Any]] = []
    feature_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: validation_calls.append(kwargs)
    )

    def fake_upsert_source(
        connection: Any,
        **kwargs: Any
    ) -> int:
        source_calls.append(kwargs)
        return 42

    def fake_upsert_article(
        connection: Any,
        **kwargs: Any
    ) -> int:
        article_calls.append(kwargs)

        if kwargs["article"]["id"] == "article-1":
            return 101

        return 102

    def fake_replace_image(
        connection: Any,
        **kwargs: Any
    ) -> None:
        image_calls.append(kwargs)

    def fake_upsert_label(
        connection: Any,
        **kwargs: Any
    ) -> bool:
        label_calls.append(kwargs)
        return True

    def fake_replace_features(
        connection: Any,
        **kwargs: Any
    ) -> tuple[int, int]:
        feature_calls.append(kwargs)
        return len(kwargs["features"]), 0

    monkeypatch.setattr(
        module,
        "upsert_source",
        fake_upsert_source
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        fake_upsert_article
    )
    monkeypatch.setattr(
        module,
        "replace_primary_image",
        fake_replace_image
    )
    monkeypatch.setattr(
        module,
        "upsert_label",
        fake_upsert_label
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        fake_replace_features
    )

    result = load_transformed_payload(
        connection,
        articles=articles,
        images=images,
        labels=labels,
        features=features,
        pipeline_run_id=pipeline_run_id
    )

    assert validation_calls == [
        {
            "articles": articles,
            "images": images,
            "labels": labels,
            "features": features
        }
    ]

    assert source_calls == [
        {
            "source_key": "reuters",
            "default_language": "fr"
        }
    ]

    assert article_calls == [
        {
            "article": articles[0],
            "source_id": 42,
            "pipeline_run_id": pipeline_run_id
        },
        {
            "article": articles[1],
            "source_id": 42,
            "pipeline_run_id": pipeline_run_id
        }
    ]

    assert image_calls == [
        {
            "image": images[0],
            "article_id": 101
        }
    ]

    assert label_calls == [
        {
            "label": labels[0],
            "article_id": 101
        },
        {
            "label": labels[1],
            "article_id": 101
        },
        {
            "label": labels[2],
            "article_id": 102
        }
    ]

    assert feature_calls == [
        {
            "features": [
                features[0],
                features[1]
            ],
            "article_id": 101,
            "pipeline_run_id": pipeline_run_id
        },
        {
            "features": [
                features[2]
            ],
            "article_id": 102,
            "pipeline_run_id": pipeline_run_id
        }
    ]

    assert result == {
        "loaded_articles": 2,
        "loaded_images": 1,
        "loaded_labels": 3,
        "loaded_features": 3,
        "ignored_labels": 0,
        "ignored_features": 0
    }


# Cache des sources

def test_load_transformed_payload_reuses_source_cache(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID,
    articles: list[dict[str, Any]]
) -> None:
    source_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda connection, **kwargs: (
            source_calls.append(kwargs)
            or 7
        )
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (0, 0)
    )

    load_transformed_payload(
        object(),
        articles=articles,
        images=[],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert source_calls == [
        {
            "source_key": "reuters",
            "default_language": "fr"
        }
    ]


def test_load_transformed_payload_loads_distinct_sources(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    articles = [
        {
            "id": "article-1",
            "source_key": "reuters",
            "language": "fr"
        },
        {
            "id": "article-2",
            "source_key": "guardian",
            "language": "en"
        }
    ]

    source_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )

    def fake_upsert_source(
        connection: Any,
        **kwargs: Any
    ) -> int:
        source_calls.append(kwargs)
        return len(source_calls)

    monkeypatch.setattr(
        module,
        "upsert_source",
        fake_upsert_source
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (0, 0)
    )

    load_transformed_payload(
        object(),
        articles=articles,
        images=[],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert source_calls == [
        {
            "source_key": "reuters",
            "default_language": "fr"
        },
        {
            "source_key": "guardian",
            "default_language": "en"
        }
    ]


# Images

def test_load_transformed_payload_uses_last_image_for_article(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    articles = [
        {
            "id": "article-1",
            "source_key": "reuters"
        }
    ]
    images = [
        {
            "article_id": "article-1",
            "local_path": "first.jpg"
        },
        {
            "article_id": "article-1",
            "local_path": "second.jpg"
        }
    ]

    image_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda *args, **kwargs: 1
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_primary_image",
        lambda connection, **kwargs: image_calls.append(kwargs)
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (0, 0)
    )

    result = load_transformed_payload(
        object(),
        articles=articles,
        images=images,
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert image_calls == [
        {
            "image": images[1],
            "article_id": 10
        }
    ]
    assert result["loaded_images"] == 1


def test_load_transformed_payload_ignores_unmatched_image(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    image_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda *args, **kwargs: 1
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_primary_image",
        lambda connection, **kwargs: image_calls.append(kwargs)
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (0, 0)
    )

    result = load_transformed_payload(
        object(),
        articles=[
            {
                "id": "article-1",
                "source_key": "reuters"
            }
        ],
        images=[
            {
                "article_id": "article-2",
                "local_path": "image.jpg"
            }
        ],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert image_calls == []
    assert result["loaded_images"] == 0


# Labels et features ignorés

def test_load_transformed_payload_counts_ignored_entities(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda *args, **kwargs: 1
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "upsert_label",
        lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (1, 2)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = load_transformed_payload(
        object(),
        articles=[
            {
                "id": "article-1",
                "source_key": "reuters"
            }
        ],
        images=[],
        labels=[
            {
                "article_id": "article-1",
                "label": "invalid"
            }
        ],
        features=[
            {
                "article_id": "article-1",
                "name": "valid"
            },
            {
                "article_id": "article-1",
                "name": "invalid-1"
            },
            {
                "article_id": "article-1",
                "name": "invalid-2"
            }
        ],
        pipeline_run_id=pipeline_run_id
    )

    assert result == {
        "loaded_articles": 1,
        "loaded_images": 0,
        "loaded_labels": 0,
        "loaded_features": 1,
        "ignored_labels": 1,
        "ignored_features": 2
    }

    assert warnings == [
        (
            "%s label(s) mal formé(s) ignoré(s).",
            1
        ),
        (
            "%s feature(s) mal formée(s) ignorée(s).",
            2
        )
    ]


def test_load_transformed_payload_does_not_log_zero_ignored_counts(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda *args, **kwargs: 1
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda *args, **kwargs: (0, 0)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    load_transformed_payload(
        object(),
        articles=[
            {
                "id": "article-1",
                "source_key": "reuters"
            }
        ],
        images=[],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert warnings == []


# Cas sans entité liée

def test_load_transformed_payload_calls_feature_replacement_with_empty_list(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    feature_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "upsert_source",
        lambda *args, **kwargs: 1
    )
    monkeypatch.setattr(
        module,
        "upsert_article",
        lambda *args, **kwargs: 10
    )
    monkeypatch.setattr(
        module,
        "replace_article_features",
        lambda connection, **kwargs: (
            feature_calls.append(kwargs)
            or (0, 0)
        )
    )

    result = load_transformed_payload(
        object(),
        articles=[
            {
                "id": "article-1",
                "source_key": "reuters"
            }
        ],
        images=[],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert feature_calls == [
        {
            "features": [],
            "article_id": 10,
            "pipeline_run_id": pipeline_run_id
        }
    ]

    assert result == {
        "loaded_articles": 1,
        "loaded_images": 0,
        "loaded_labels": 0,
        "loaded_features": 0,
        "ignored_labels": 0,
        "ignored_features": 0
    }


def test_load_transformed_payload_returns_zero_counts_for_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_run_id: UUID
) -> None:
    monkeypatch.setattr(
        module,
        "validate_payload_references",
        lambda **kwargs: None
    )

    result = load_transformed_payload(
        object(),
        articles=[],
        images=[],
        labels=[],
        features=[],
        pipeline_run_id=pipeline_run_id
    )

    assert result == {
        "loaded_articles": 0,
        "loaded_images": 0,
        "loaded_labels": 0,
        "loaded_features": 0,
        "ignored_labels": 0,
        "ignored_features": 0
    }