"""Validation structurelle des payloads PostgreSQL CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/postgres/postgres_payload_validator.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any


def validate_payload_references(
    *,
    articles: list[dict[str, Any]],
    images: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    features: list[dict[str, Any]]
) -> None:
    """Vérifie la structure du payload et les références vers les articles."""

    if not articles:
        raise RuntimeError("Le payload ne contient aucun article.")

    malformed_articles = [
        index for index, article in enumerate(articles)
        if not isinstance(article, dict)
        or not article.get("id")
        or not article.get("source_key")
        or not article.get("title")
    ]

    if malformed_articles:
        raise ValueError(
            "Article(s) invalide(s) aux index : "
            f"{malformed_articles[:20]}."
        )

    article_ids = [str(article["id"]) for article in articles]

    if len(set(article_ids)) != len(article_ids):
        raise ValueError(
            "Le payload contient des identifiants d'articles dupliqués."
        )

    known_article_ids = set(article_ids)
    errors: list[str] = []

    # Vérifie la structure et les références des entités liées.
    for collection_name, collection in {
        "images": images,
        "labels": labels,
        "features": features
    }.items():
        malformed_count = sum(
            1 for item in collection
            if not isinstance(item, dict) or not item.get("article_id")
        )
        orphan_count = sum(
            1 for item in collection
            if isinstance(item, dict)
            and item.get("article_id")
            and str(item["article_id"]) not in known_article_ids
        )

        if malformed_count:
            errors.append(
                f"{collection_name}: {malformed_count} élément(s) mal formé(s)"
            )
        if orphan_count:
            errors.append(
                f"{collection_name}: {orphan_count} référence(s) orpheline(s)"
            )

    if errors:
        raise ValueError("Payload incohérent : " + " | ".join(errors))