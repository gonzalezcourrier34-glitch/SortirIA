"""Tests unitaires du contrat métier des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/schema/test_article_schema.py` (commit 93632c5).
"""

from __future__ import annotations

from src.article.schema import article_schema as module


# Champs standards

def test_standard_article_fields_contains_expected_fields() -> None:
    assert module.STANDARD_ARTICLE_FIELDS == (
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


def test_required_fields_are_in_standard_fields() -> None:
    assert set(module.REQUIRED_FIELDS).issubset(
        module.STANDARD_ARTICLE_FIELDS
    )


def test_optional_fields_are_standard_fields_minus_required() -> None:
    expected = tuple(
        field
        for field in module.STANDARD_ARTICLE_FIELDS
        if field not in module.REQUIRED_FIELDS
    )

    assert module.OPTIONAL_FIELDS == expected


def test_required_and_optional_fields_cover_standard_fields() -> None:
    assert set(module.REQUIRED_FIELDS).isdisjoint(
        module.OPTIONAL_FIELDS
    )
    assert (
        set(module.REQUIRED_FIELDS)
        | set(module.OPTIONAL_FIELDS)
    ) == set(module.STANDARD_ARTICLE_FIELDS)

def test_optional_fields_preserve_standard_field_order() -> None:
    expected = tuple(
        field
        for field in module.STANDARD_ARTICLE_FIELDS
        if field not in module.REQUIRED_FIELDS
    )

    assert module.OPTIONAL_FIELDS == expected
    
def test_standard_fields_are_unique() -> None:
    assert len(module.STANDARD_ARTICLE_FIELDS) == len(
        set(module.STANDARD_ARTICLE_FIELDS)
    )


# Champs texte

def test_text_fields_to_clean_are_standard_or_known_extra_fields() -> None:
    allowed = set(module.STANDARD_ARTICLE_FIELDS) | {
        "summary",
        "description"
    }

    assert set(module.TEXT_FIELDS_TO_CLEAN).issubset(allowed)


def test_article_text_fields_are_subset_of_text_fields_to_clean() -> None:
    assert set(module.ARTICLE_TEXT_FIELDS).issubset(
        module.TEXT_FIELDS_TO_CLEAN
    )


# Alias

def test_article_field_aliases_target_standard_fields() -> None:
    assert set(module.ARTICLE_FIELD_ALIASES.values()).issubset(
        module.STANDARD_ARTICLE_FIELDS
    )


def test_article_field_aliases_keys_are_unique() -> None:
    assert len(module.ARTICLE_FIELD_ALIASES) == len(
        set(module.ARTICLE_FIELD_ALIASES)
    )


# Champs d'identification

def test_identifier_fields_are_unique() -> None:
    assert len(module.ARTICLE_IDENTIFIER_FIELDS) == len(
        set(module.ARTICLE_IDENTIFIER_FIELDS)
    )


def test_identifier_fields_are_not_empty() -> None:
    assert module.ARTICLE_IDENTIFIER_FIELDS


# URL

def test_article_url_fields_are_unique() -> None:
    assert len(module.ARTICLE_URL_FIELDS) == len(
        set(module.ARTICLE_URL_FIELDS)
    )


def test_article_url_fields_are_identifier_fields() -> None:
    assert set(module.ARTICLE_URL_FIELDS).issubset(
        module.ARTICLE_IDENTIFIER_FIELDS
    )


# Catégories

def test_article_categories_is_not_empty() -> None:
    assert module.ARTICLE_CATEGORIES


def test_article_categories_are_lowercase() -> None:
    assert all(
        category == category.lower()
        for category in module.ARTICLE_CATEGORIES
    )


def test_article_categories_are_unique() -> None:
    assert len(module.ARTICLE_CATEGORIES) == len(
        set(module.ARTICLE_CATEGORIES)
    )


# Langues

def test_supported_languages_contains_fr_and_en() -> None:
    assert module.SUPPORTED_LANGUAGES == {
        "fr",
        "en"
    }


def test_supported_languages_are_lowercase() -> None:
    assert all(
        language == language.lower()
        for language in module.SUPPORTED_LANGUAGES
    )


# Labels

def test_binary_labels_contains_expected_values() -> None:
    assert module.BINARY_LABELS == {
        "fake",
        "real"
    }


def test_numeric_binary_labels_contains_expected_values() -> None:
    assert module.NUMERIC_BINARY_LABELS == {
        "0",
        "1"
    }


def test_liar_labels_are_lowercase() -> None:
    assert all(
        label == label.lower()
        for label in module.LIAR_LABELS
    )


# Dataset roles

def test_dataset_roles_are_lowercase() -> None:
    assert all(
        role == role.lower()
        for role in module.DATASET_ROLES
    )


def test_dataset_roles_are_unique() -> None:
    assert len(module.DATASET_ROLES) == len(
        set(module.DATASET_ROLES)
    )


def test_dataset_roles_contains_expected_values() -> None:
    assert module.DATASET_ROLES == {
        "acquisition",
        "labeled_reference",
        "multimodal_reference",
        "fact_check_reference",
        "social_reference"
    }