"""Contrat métier commun des articles CheckIt.AI.

Constantes seulement : champs standards et obligatoires d'un article, alias
acceptés, champs à nettoyer, champs où chercher le texte, l'identifiant ou
l'URL, langues et rôles pris en charge.

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

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "fr",
    "en"
})

# Modifié pour SortirIA : les étiquettes vrai / faux (binaires, numériques,
# LIAR) et les rôles de référence étiquetée de CheckIt.AI ont été retirés.
DATASET_ROLES: frozenset[str] = frozenset({
    "acquisition"
})