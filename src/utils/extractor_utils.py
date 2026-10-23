"""Fonctions générales communes aux extracteurs CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/extractor_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from src.article.processing.article_cleaner import normalize_value
from src.article.processing.article_transformer import normalize_dataset_role
from src.article.schema.article_schema_normalizer import normalize_article_schema
from src.article.validation.article_validator import validate_article_with_reason
from src.logger import get_logger
from src.utils.date_utils import convert_date_to_iso, get_extraction_date
from src.utils.parsing_utils import parse_non_negative_integer, parse_optional_float
from src.utils.value_utils import normalize_whitespace

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
    """Construit un article conforme au schéma standard CheckIt.AI.

    Chaque champ est nettoyé, la date est convertie en ISO 8601 UTC, et un
    identifiant court (8 caractères d'un MD5) est calculé à partir de
    l'identifiant fourni, à défaut de l'URL, à défaut de « source:titre:date ».

    Parameters
    ----------
    identifier : Any
        Identifiant propre à la source ; vide, l'URL ou le titre prennent le relais.
    source : Any
        Nom de la source d'où vient l'article.
    title : Any
        Titre de l'article. Par défaut vide.
    text : Any
        Texte de l'article. Par défaut vide.
    image_url : Any
        Adresse de l'image associée. Par défaut vide.
    image_path : Any
        Chemin local de l'image téléchargée. Par défaut vide.
    published_at : Any
        Date de publication, sous n'importe quel format lisible. Par défaut vide.
    url : Any
        Adresse de l'article. Par défaut vide.
    author : Any
        Auteur. Par défaut vide.
    language : Any
        Code de langue, mis en minuscules. Par défaut vide.
    category : Any
        Catégorie de l'article. Par défaut vide.
    label : Any
        Étiquette (par exemple vrai / faux). Par défaut vide.
    role : Any
        Rôle dans le jeu de données ; prioritaire sur `dataset_role`.
    dataset_role : Any
        Ancien nom du rôle, utilisé si `role` est vide.
    metadata : Mapping[str, Any] | None
        Métadonnées libres, copiées telles quelles. Par défaut aucune.

    Returns
    -------
    dict[str, Any]
        L'article normalisé selon le schéma standard.

    Raises
    ------
    ValueError
        Si l'article n'a ni identifiant, ni URL, ni titre.
    """

    # Nettoyer les champs qui servent à identifier l'article
    normalized_source = normalize_value(source)
    normalized_title = normalize_whitespace(title)
    normalized_text = normalize_whitespace(text)
    normalized_url = normalize_value(url)
    normalized_published_at = convert_date_to_iso(published_at)
    normalized_identifier = normalize_value(identifier) or normalized_url

    # À défaut d'identifiant et d'URL, en fabriquer un depuis source, titre et date
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

    # Calculer l'identifiant court et le rôle dans le jeu de données
    article_id = hashlib.md5(
        normalized_identifier.encode("utf-8"),
        usedforsecurity=False
    ).hexdigest()[:8]

    normalized_role = normalize_dataset_role(
        normalize_value(role) or normalize_value(dataset_role)
    )

    # Assembler l'article puis l'aligner sur le schéma standard
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
        "label": normalize_value(label),
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
    """Retourne la première valeur textuelle non vide trouvée.

    Parameters
    ----------
    row : Any
        La ligne à lire ; si ce n'est pas un dictionnaire, la valeur par défaut
        est renvoyée.
    candidates : Sequence[str]
        Les noms de champs à essayer, dans l'ordre.
    default : str
        Valeur renvoyée si aucun champ n'est rempli. Par défaut vide.

    Returns
    -------
    str
        La première valeur nettoyée non vide, sinon `default` nettoyé.
    """

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
    """Retourne la première valeur numérique exploitable trouvée.

    Parameters
    ----------
    row : Any
        La ligne à lire ; si ce n'est pas un dictionnaire, renvoie None.
    candidates : Sequence[str]
        Les noms de champs à essayer, dans l'ordre.

    Returns
    -------
    float | None
        Le premier nombre lisible, ou None si aucun champ n'en contient.
    """

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
    """Valide un article avec le validateur métier central.

    Parameters
    ----------
    article : Mapping[str, Any]
        L'article à contrôler.
    filters : Mapping[str, Any] | None
        Les filtres de collecte à respecter. Par défaut aucun.

    Returns
    -------
    tuple[bool, str]
        Vrai si l'article est accepté, et le motif de rejet le cas échéant
        (« filtres_invalides » si `filters` n'est pas un dictionnaire).
    """

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
    """Journalise le bilan d'extraction et les motifs de rejet.

    Parameters
    ----------
    source_name : str
        Nom de la source ; « source_inconnue » s'il est vide.
    extracted_count : int
        Nombre de publications retenues.
    processed_count : int
        Nombre de publications analysées.
    rejection_stats : Counter[str]
        Nombre de rejets par motif ; rien n'est journalisé s'il est vide.
    """

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