"""Utilitaires textuels pour les extracteurs de datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/datasets/dataset_text_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.article.processing.article_cleaner import clean_text
from src.utils.extractor_utils import get_value, normalize_value
from src.utils.url_utils import is_valid_http_url


# Titre

def get_dataset_title(
    item: Mapping[str, Any],
    title_fields: Sequence[str],
) -> str:
    """Retourne le premier titre exploitable parmi les champs fournis."""

    if not isinstance(item, Mapping):
        raise TypeError(
            "item doit être une structure de données associative."
        )

    return clean_text(get_value(item, title_fields))


# Texte

def get_dataset_text(
    item: Mapping[str, Any],
    text_fields: Sequence[str],
    fallback_fields: Sequence[str] = (),
) -> str:
    """Retourne le meilleur texte disponible, puis applique un repli."""

    if not isinstance(item, Mapping):
        raise TypeError(
            "item doit être une structure de données associative."
        )

    text = clean_text(get_value(item, text_fields))
    return text or clean_text(get_value(item, fallback_fields))


# URL

def get_dataset_url(
    item: Mapping[str, Any],
    url_fields: Sequence[str],
) -> str:
    """Retourne la première URL valide en ajoutant HTTPS si nécessaire."""

    if not isinstance(item, Mapping):
        raise TypeError(
            "item doit être une structure de données associative."
        )

    url = normalize_value(get_value(item, url_fields))
    if not url:
        return ""

    if url.startswith("//"):
        url = f"https:{url}"
    elif not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"

    return url if is_valid_http_url(url) else ""