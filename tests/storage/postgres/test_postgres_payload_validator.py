"""Tests du validateur de payloads PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/postgres/test_postgres_payload_validator.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.storage.postgres.postgres_payload_validator import (
    validate_payload_references
)


# Données communes

@pytest.fixture
def articles() -> list[dict[str, Any]]:
    """Retourne deux articles valides."""

    return [
        {
            "id": "article-1",
            "source_key": "reuters",
            "title": "Premier article"
        },
        {
            "id": "article-2",
            "source_key": "guardian",
            "title": "Deuxième article"
        }
    ]


@pytest.fixture
def images() -> list[dict[str, Any]]:
    """Retourne une image liée à un article connu."""

    return [
        {
            "article_id": "article-1",
            "local_path": "data/images/article-1.jpg"
        }
    ]


@pytest.fixture
def labels() -> list[dict[str, Any]]:
    """Retourne un label lié à un article connu."""

    return [
        {
            "article_id": "article-1",
            "label": "true"
        }
    ]


@pytest.fixture
def features() -> list[dict[str, Any]]:
    """Retourne une feature liée à un article connu."""

    return [
        {
            "article_id": "article-2",
            "name": "title_length",
            "value": 17
        }
    ]


# Payload valide

def test_validate_payload_references_accepts_valid_payload(
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    features: list[dict[str, Any]]
) -> None:
    result = validate_payload_references(
        articles=articles,
        images=images,
        labels=labels,
        features=features
    )

    assert result is None


def test_validate_payload_references_accepts_empty_related_entities(
    articles: list[dict[str, Any]]
) -> None:
    result = validate_payload_references(
        articles=articles,
        images=[],
        labels=[],
        features=[]
    )

    assert result is None


# Articles absents

def test_validate_payload_references_rejects_empty_articles() -> None:
    with pytest.raises(
        RuntimeError,
        match="Le payload ne contient aucun article"
    ):
        validate_payload_references(
            articles=[],
            images=[],
            labels=[],
            features=[]
        )


# Articles mal formés

@pytest.mark.parametrize(
    "invalid_article",
    [
        None,
        "article",
        42,
        [],
        {},
        {
            "source_key": "reuters",
            "title": "Article sans identifiant"
        },
        {
            "id": "article-1",
            "title": "Article sans source"
        },
        {
            "id": "article-1",
            "source_key": "reuters"
        },
        {
            "id": "",
            "source_key": "reuters",
            "title": "Identifiant vide"
        },
        {
            "id": "article-1",
            "source_key": "",
            "title": "Source vide"
        },
        {
            "id": "article-1",
            "source_key": "reuters",
            "title": ""
        }
    ]
)
def test_validate_payload_references_rejects_malformed_article(
    invalid_article: Any
) -> None:
    with pytest.raises(
        ValueError,
        match=r"Article\(s\) invalide\(s\) aux index : \[0\]"
    ):
        validate_payload_references(
            articles=[invalid_article],
            images=[],
            labels=[],
            features=[]
        )


def test_validate_payload_references_reports_all_malformed_article_indexes(
) -> None:
    articles = [
        {
            "id": "article-1",
            "source_key": "reuters",
            "title": "Article valide"
        },
        {},
        {
            "id": "article-3",
            "source_key": "guardian"
        },
        "invalid",
        {
            "id": "article-5",
            "source_key": "bbc",
            "title": "Autre article valide"
        }
    ]

    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=[],
            labels=[],
            features=[]
        )

    assert str(error.value) == (
        "Article(s) invalide(s) aux index : [1, 2, 3]."
    )


def test_validate_payload_references_limits_reported_article_indexes() -> None:
    articles = [{} for _ in range(25)]

    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=[],
            labels=[],
            features=[]
        )

    expected_indexes = list(range(20))

    assert str(error.value) == (
        "Article(s) invalide(s) aux index : "
        f"{expected_indexes}."
    )


# Identifiants dupliqués

def test_validate_payload_references_rejects_duplicate_article_ids() -> None:
    articles = [
        {
            "id": "article-1",
            "source_key": "reuters",
            "title": "Premier article"
        },
        {
            "id": "article-1",
            "source_key": "guardian",
            "title": "Deuxième article"
        }
    ]

    with pytest.raises(
        ValueError,
        match=(
            "Le payload contient des identifiants "
            "d'articles dupliqués"
        )
    ):
        validate_payload_references(
            articles=articles,
            images=[],
            labels=[],
            features=[]
        )


def test_validate_payload_references_normalizes_ids_as_strings() -> None:
    articles = [
        {
            "id": 1,
            "source_key": "reuters",
            "title": "Premier article"
        },
        {
            "id": "1",
            "source_key": "guardian",
            "title": "Deuxième article"
        }
    ]

    with pytest.raises(
        ValueError,
        match=(
            "Le payload contient des identifiants "
            "d'articles dupliqués"
        )
    ):
        validate_payload_references(
            articles=articles,
            images=[],
            labels=[],
            features=[]
        )


# Entités liées mal formées

@pytest.mark.parametrize(
    ("collection_name", "collection"),
    [
        (
            "images",
            [
                {},
                {
                    "local_path": "image.jpg"
                },
                "invalid"
            ]
        ),
        (
            "labels",
            [
                {},
                {
                    "label": "true"
                },
                None
            ]
        ),
        (
            "features",
            [
                {},
                {
                    "name": "title_length",
                    "value": 10
                },
                42
            ]
        )
    ]
)
def test_validate_payload_references_rejects_malformed_related_entities(
    articles: list[dict[str, Any]],
    collection_name: str,
    collection: list[Any]
) -> None:
    collections = {
        "images": [],
        "labels": [],
        "features": []
    }
    collections[collection_name] = collection

    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=collections["images"],
            labels=collections["labels"],
            features=collections["features"]
        )

    assert str(error.value) == (
        "Payload incohérent : "
        f"{collection_name}: 3 élément(s) mal formé(s)"
    )


@pytest.mark.parametrize(
    "collection_name",
    [
        "images",
        "labels",
        "features"
    ]
)
def test_validate_payload_references_rejects_missing_article_id(
    articles: list[dict[str, Any]],
    collection_name: str
) -> None:
    collections = {
        "images": [],
        "labels": [],
        "features": []
    }
    collections[collection_name] = [
        {
            "article_id": ""
        },
        {
            "article_id": None
        }
    ]

    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=collections["images"],
            labels=collections["labels"],
            features=collections["features"]
        )

    assert str(error.value) == (
        "Payload incohérent : "
        f"{collection_name}: 2 élément(s) mal formé(s)"
    )


# Références orphelines

@pytest.mark.parametrize(
    "collection_name",
    [
        "images",
        "labels",
        "features"
    ]
)
def test_validate_payload_references_rejects_orphan_references(
    articles: list[dict[str, Any]],
    collection_name: str
) -> None:
    collections = {
        "images": [],
        "labels": [],
        "features": []
    }
    collections[collection_name] = [
        {
            "article_id": "unknown-1"
        },
        {
            "article_id": "unknown-2"
        }
    ]

    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=collections["images"],
            labels=collections["labels"],
            features=collections["features"]
        )

    assert str(error.value) == (
        "Payload incohérent : "
        f"{collection_name}: 2 référence(s) orpheline(s)"
    )


def test_validate_payload_references_normalizes_related_ids_as_strings(
) -> None:
    articles = [
        {
            "id": 123,
            "source_key": "reuters",
            "title": "Article numérique"
        }
    ]

    result = validate_payload_references(
        articles=articles,
        images=[
            {
                "article_id": "123"
            }
        ],
        labels=[
            {
                "article_id": 123
            }
        ],
        features=[],
    )

    assert result is None


# Regroupement des erreurs

def test_validate_payload_references_combines_all_errors(
    articles: list[dict[str, Any]]
) -> None:
    images = [
        {},
        {
            "article_id": "unknown-image"
        }
    ]
    labels = [
        {
            "label": "true"
        },
        {
            "article_id": "unknown-label"
        }
    ]
    features = [
        None,
        {
            "article_id": "unknown-feature"
        }
    ]

    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=images,
            labels=labels,
            features=features
        )

    assert str(error.value) == (
        "Payload incohérent : "
        "images: 1 élément(s) mal formé(s) | "
        "images: 1 référence(s) orpheline(s) | "
        "labels: 1 élément(s) mal formé(s) | "
        "labels: 1 référence(s) orpheline(s) | "
        "features: 1 élément(s) mal formé(s) | "
        "features: 1 référence(s) orpheline(s)"
    )


def test_validate_payload_references_does_not_count_malformed_as_orphan(
    articles: list[dict[str, Any]]
) -> None:
    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=[
                {},
                {
                    "article_id": None
                }
            ],
            labels=[],
            features=[]
        )

    assert str(error.value) == (
        "Payload incohérent : "
        "images: 2 élément(s) mal formé(s)"
    )


def test_validate_payload_references_preserves_collection_order(
    articles: list[dict[str, Any]]
) -> None:
    with pytest.raises(ValueError) as error:
        validate_payload_references(
            articles=articles,
            images=[
                {
                    "article_id": "unknown"
                }
            ],
            labels=[
                {
                    "article_id": "unknown"
                }
            ],
            features=[
                {
                    "article_id": "unknown"
                }
            ]
        )

    assert str(error.value) == (
        "Payload incohérent : "
        "images: 1 référence(s) orpheline(s) | "
        "labels: 1 référence(s) orpheline(s) | "
        "features: 1 référence(s) orpheline(s)"
    )