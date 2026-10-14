"""Contrat commun des adaptateurs RSS CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/rss/rss_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from src.utils.extractor_utils import validate_article
from src.utils.value_utils import normalize_value


# Types

RssItem = Any
RssSource = Mapping[str, Any]
RssIterator = Callable[[RssSource], Iterable[RssItem]]
RssArticleBuilder = Callable[[RssItem, RssSource], dict[str, Any]]
RssItemValidator = Callable[[RssItem, RssSource], tuple[bool, str]]
RssArticleValidator = Callable[
    [Mapping[str, Any], RssSource],
    tuple[bool, str]
]


# Contexte attendu

class RssValidationContext(Protocol):
    """Décrit les données nécessaires aux validations RSS."""

    source: Mapping[str, Any]
    filters: Mapping[str, Any]


# Adaptateur

@dataclass(frozen=True, slots=True)
class RssAdapter:
    """Décrit le comportement propre au lecteur RSS ou Atom."""

    iter_items: RssIterator
    build_article: RssArticleBuilder
    validate_item: RssItemValidator | None = None
    validate_article: RssArticleValidator | None = None
    default_name: str = "RSS"

    def __post_init__(self) -> None:
        """Normalise et valide le contrat de l'adaptateur."""

        # Validation des callbacks obligatoires
        if not callable(self.iter_items):
            raise TypeError("iter_items doit être une fonction appelable.")

        if not callable(self.build_article):
            raise TypeError("build_article doit être une fonction appelable.")

        # Validation des callbacks facultatifs
        if self.validate_item is not None and not callable(self.validate_item):
            raise TypeError(
                "validate_item doit être une fonction appelable ou None."
            )

        if (
            self.validate_article is not None
            and not callable(self.validate_article)
        ):
            raise TypeError(
                "validate_article doit être une fonction appelable ou None."
            )

        # Normalisation du nom affiché
        object.__setattr__(
            self,
            "default_name",
            normalize_value(self.default_name) or "RSS"
        )


# Normalisation des validations

def normalize_validation_result(
    result: Any,
    invalid_reason: str,
    rejected_reason: str
) -> tuple[bool, str]:
    """Normalise le résultat retourné par un callback de validation."""

    # Un callback doit toujours retourner exactement (bool, str).
    if not isinstance(result, tuple) or len(result) != 2:
        return False, invalid_reason

    valid, reason = result

    # Une valeur comme "False" ne doit pas être interprétée comme True.
    if not isinstance(valid, bool):
        return False, invalid_reason

    normalized_reason = normalize_value(reason)

    if valid:
        return True, ""

    return False, normalized_reason or rejected_reason


# Validation des entrées

def validate_rss_item(
    item: RssItem,
    context: RssValidationContext,
    adapter: RssAdapter
) -> tuple[bool, str]:
    """Applique la validation spécifique d'une entrée RSS."""

    if not isinstance(adapter, RssAdapter):
        raise TypeError("adapter doit être une instance de RssAdapter.")

    if adapter.validate_item is None:
        return True, ""

    # Les exceptions du callback remontent volontairement au moteur RSS.
    # Le moteur pourra alors arrêter et qualifier correctement la source.
    result = adapter.validate_item(item, context.source)

    return normalize_validation_result(
        result=result,
        invalid_reason="validation_entree_invalide",
        rejected_reason="entree_invalide"
    )


# Validation des articles

def validate_rss_article(
    article: Mapping[str, Any],
    context: RssValidationContext,
    adapter: RssAdapter
) -> tuple[bool, str]:
    """Applique les validations spécifique puis commune."""

    if not isinstance(adapter, RssAdapter):
        raise TypeError("adapter doit être une instance de RssAdapter.")

    if not isinstance(article, Mapping):
        return False, "article_invalide"

    # Validation propre au flux ou à l'adaptateur
    if adapter.validate_article is not None:
        # Les exceptions remontent au moteur afin de ne pas transformer
        # silencieusement une panne technique en simple rejet métier.
        result = adapter.validate_article(article, context.source)

        valid, reason = normalize_validation_result(
            result=result,
            invalid_reason="validation_article_invalide",
            rejected_reason="article_invalide"
        )

        if not valid:
            return False, reason

    # Validation commune à tous les articles CheckIt.AI
    result = validate_article(article, context.filters)

    return normalize_validation_result(
        result=result,
        invalid_reason="validation_commune_invalide",
        rejected_reason="article_invalide"
    )