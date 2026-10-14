"""Fonctions générales communes aux extracteurs CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/extractor_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from src.article.processing.article_cleaner import (
    normalize_value
)
from src.article.processing.article_transformer import (
    normalize_dataset_role,
    normalize_label
)
from src.article.schema.article_schema_normalizer import normalize_article_schema
from src.article.validation.article_validator import validate_article_with_reason
from src.logger import get_logger
from src.utils.value_utils import normalize_whitespace
from src.utils.date_utils import convert_date_to_iso, get_extraction_date
from src.utils.parsing_utils import (
    parse_non_negative_integer,
    parse_optional_float
)

logger = get_logger(__name__)

__all__ = [
    "build_standard_article",
    "get_numeric_value",
    "get_value",
    "log_extraction_summary",
    "validate_article"
]


# Construction

def build_standard_article(
    *,
    identifier: Any,
    source: Any,
    title: Any = "",
    text: Any = "",
    image_url: Any = "",
    image_path: Any = "",
    published_at: Any = "",
    url: Any = "",
    author: Any = "",
    language: Any = "",
    category: Any = "",
    label: Any = "",
    role: Any = "",
    dataset_role: Any = "",
    metadata: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Construit un article conforme au schéma standard CheckIt.AI."""

    normalized_source = normalize_value(source)
    normalized_title = normalize_whitespace(title)
    normalized_text = normalize_whitespace(text)
    normalized_url = normalize_value(url)
    normalized_published_at = convert_date_to_iso(published_at)
    normalized_identifier = normalize_value(identifier) or normalized_url

    if not normalized_identifier:
        if not normalized_title:
            raise ValueError(
                "Impossible de construire un article sans identifiant, URL ou titre."
            )

        normalized_identifier = ":".join(
            value
            for value in (
                normalized_source,
                normalized_title,
                normalized_published_at
            )
            if value
        )

    article_id = hashlib.md5(
        normalized_identifier.encode("utf-8"),
        usedforsecurity=False
    ).hexdigest()[:8]

    normalized_role = normalize_dataset_role(
        normalize_value(role) or normalize_value(dataset_role)
    )

    article = {
        "id": article_id,
        "source": normalized_source,
        "title": normalized_title,
        "text": normalized_text,
        "image_url": normalize_value(image_url),
        "image_path": normalize_value(image_path),
        "published_at": normalized_published_at,
        "url": normalized_url,
        "author": normalize_value(author),
        "language": normalize_value(language).lower(),
        "category": normalize_value(category),
        "label": normalize_label(label),
        "role": normalized_role,
        "extracted_at": get_extraction_date(),
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {}
    }

    return normalize_article_schema(article)


# Lecture

def get_value(
    row: Any,
    candidates: Sequence[str],
    default: str = ""
) -> str:
    """Retourne la première valeur textuelle non vide trouvée."""

    if not isinstance(row, Mapping):
        return normalize_value(default)

    for field in candidates:
        value = normalize_value(row.get(field))

        if value:
            return value

    return normalize_value(default)


def get_numeric_value(
    row: Any,
    candidates: Sequence[str]
) -> float | None:
    """Retourne la première valeur numérique exploitable trouvée."""

    if not isinstance(row, Mapping):
        return None

    for field in candidates:
        number = parse_optional_float(row.get(field))

        if number is not None:
            return number

    return None


# Validation

def validate_article(
    article: Mapping[str, Any],
    filters: Mapping[str, Any] | None = None
) -> tuple[bool, str]:
    """Valide un article avec le validateur métier central."""

    if filters is not None and not isinstance(filters, Mapping):
        return False, "filtres_invalides"

    return validate_article_with_reason(article, filters)


# Journalisation

def log_extraction_summary(
    source_name: str,
    extracted_count: int,
    processed_count: int,
    rejection_stats: Counter[str]
) -> None:
    """Journalise le bilan d'extraction et les motifs de rejet."""

    normalized_source = normalize_value(source_name) or "source_inconnue"
    safe_extracted_count = parse_non_negative_integer(extracted_count)
    safe_processed_count = parse_non_negative_integer(processed_count)

    logger.info(
        "%s : %s publication(s) extraite(s) sur %s analysée(s).",
        normalized_source,
        safe_extracted_count,
        safe_processed_count
    )

    if rejection_stats:
        logger.info(
            "%s : motifs de rejet : %s.",
            normalized_source,
            dict(rejection_stats)
        )