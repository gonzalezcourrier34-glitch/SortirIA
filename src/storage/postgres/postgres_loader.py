"""Chargement métier des entités PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/postgres/postgres_loader.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from psycopg2.extensions import connection as Connection

from src.logger import get_logger
from src.storage.postgres.postgres_payload_validator import validate_payload_references
from src.storage.postgres.postgres_storage import (
    replace_article_features,
    replace_primary_image,
    upsert_article,
    upsert_label,
    upsert_source,
)

logger = get_logger(__name__)


def load_transformed_payload(
    connection: Connection,
    *,
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    features: list[dict[str, Any]],
    pipeline_run_id: UUID,
) -> dict[str, int]:
    """Charge les entités d'un lot dans PostgreSQL."""

    validate_payload_references(
        articles=articles,
        images=images,
        labels=labels,
        features=features,
    )

    image_by_article = {str(image["article_id"]): image for image in images}
    labels_by_article: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    features_by_article: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for label in labels:
        labels_by_article[str(label.get("article_id"))].append(label)

    for feature in features:
        features_by_article[str(feature.get("article_id"))].append(feature)

    source_cache: dict[str, int] = {}
    loaded_articles = loaded_images = loaded_labels = loaded_features = 0
    ignored_labels = ignored_features = 0

    for article in articles:
        source_key = str(article["source_key"])

        if source_key not in source_cache:
            source_cache[source_key] = upsert_source(
                connection,
                source_key=source_key,
                default_language=article.get("language"),
            )

        requested_article_id = str(article["id"])

        database_article_id = upsert_article(
            connection,
            article=article,
            source_id=source_cache[source_key],
            pipeline_run_id=pipeline_run_id,
        )
        loaded_articles += 1

        image = image_by_article.get(requested_article_id)
        if image is not None:
            replace_primary_image(
                connection,
                image=image,
                article_id=database_article_id,
            )
            loaded_images += 1

        for label in labels_by_article.get(requested_article_id, []):
            if upsert_label(
                connection,
                label=label,
                article_id=database_article_id,
            ):
                loaded_labels += 1
            else:
                ignored_labels += 1

        added, ignored = replace_article_features(
            connection,
            features=features_by_article.get(requested_article_id, []),
            article_id=database_article_id,
            pipeline_run_id=pipeline_run_id,
        )
        loaded_features += added
        ignored_features += ignored

    if ignored_labels:
        logger.warning("%s label(s) mal formé(s) ignoré(s).", ignored_labels)

    if ignored_features:
        logger.warning("%s feature(s) mal formée(s) ignorée(s).", ignored_features)

    return {
        "loaded_articles": loaded_articles,
        "loaded_images": loaded_images,
        "loaded_labels": loaded_labels,
        "loaded_features": loaded_features,
        "ignored_labels": ignored_labels,
        "ignored_features": ignored_features,
    }