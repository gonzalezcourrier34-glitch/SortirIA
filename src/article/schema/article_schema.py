"""Contrat métier commun des articles CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/schema/article_schema.py` (commit 93632c5).
"""

from __future__ import annotations

# Champs standards

STANDARD_ARTICLE_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "text",
    "url",
    "image_url",
    "image_path",
    "source",
    "source_id",
    "source_type",
    "author",
    "category",
    "language",
    "country",
    "label",
    "published_at",
    "extracted_at",
    "role",
    "metadata"
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "source"
)

OPTIONAL_FIELDS: tuple[str, ...] = tuple(
    field
    for field in STANDARD_ARTICLE_FIELDS
    if field not in REQUIRED_FIELDS
)

TEXT_FIELDS_TO_CLEAN: tuple[str, ...] = (
    "title",
    "text",
    "summary",
    "description",
    "author",
    "category"
)

ARTICLE_FIELD_ALIASES: dict[str, str] = {
    "link": "url",
    "extraction_date": "extracted_at",
    "dataset_role": "role"
}

ARTICLE_TEXT_FIELDS: tuple[str, ...] = (
    "text",
    "summary",
    "description"
)

ARTICLE_IDENTIFIER_FIELDS: tuple[str, ...] = (
    "id",
    "url",
    "link",
    "title"
)

ARTICLE_URL_FIELDS: tuple[str, ...] = (
    "url",
    "link"
)

# Valeurs métier

ARTICLE_CATEGORIES: frozenset[str] = frozenset({
    "general",
    "international",
    "world",
    "nation",
    "politics",
    "business",
    "economy",
    "technology",
    "science",
    "health",
    "environment",
    "education",
    "crime",
    "entertainment",
    "sports",
    "social",
    "fact_check"
})

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "fr",
    "en"
})

BINARY_LABELS: frozenset[str] = frozenset({
    "fake",
    "real"
})

NUMERIC_BINARY_LABELS: frozenset[str] = frozenset({
    "0",
    "1"
})

LIAR_LABELS: frozenset[str] = frozenset({
    "pants-fire",
    "false",
    "barely-true",
    "half-true",
    "mostly-true",
    "true"
})

DATASET_ROLES: frozenset[str] = frozenset({
    "acquisition",
    "labeled_reference",
    "multimodal_reference",
    "fact_check_reference",
    "social_reference"
})