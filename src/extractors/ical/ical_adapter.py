"""Contrat commun des adaptateurs iCal.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), calqué sur
`src/extractors/rss/rss_adapter.py` de CheckIt.AI (même découpage : le
moteur orchestre, l'adaptateur sait lire un format). Les fonctions de
normalisation des validations sont réutilisées telles quelles depuis le
module RSS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.extractors.rss.rss_adapter import normalize_validation_result
from src.utils.extractor_utils import validate_article
from src.utils.value_utils import normalize_value

IcalItem = Any
IcalSource = Mapping[str, Any]
IcalIterator = Callable[[IcalSource], Iterable[IcalItem]]
IcalArticleBuilder = Callable[[IcalItem, IcalSource], dict[str, Any]]
IcalItemValidator = Callable[[IcalItem, IcalSource], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class IcalAdapter:
    """Décrit comment lire un calendrier : ses éléments, et comment en faire un article."""

    iter_items: IcalIterator
    build_article: IcalArticleBuilder
    validate_item: IcalItemValidator | None = None
    default_name: str = "iCal"

    def __post_init__(self) -> None:
        if not callable(self.iter_items):
            raise TypeError("iter_items doit être une fonction appelable.")
        if not callable(self.build_article):
            raise TypeError("build_article doit être une fonction appelable.")
        if self.validate_item is not None and not callable(self.validate_item):
            raise TypeError("validate_item doit être une fonction appelable ou None.")
        object.__setattr__(self, "default_name", normalize_value(self.default_name) or "iCal")


def validate_ical_item(item: IcalItem, source: IcalSource, adapter: IcalAdapter) -> tuple[bool, str]:
    """Validation propre à l'adaptateur (avant la construction de l'article)."""

    if adapter.validate_item is None:
        return True, ""
    return normalize_validation_result(
        adapter.validate_item(item, source),
        invalid_reason="validation_entree_invalide",
        rejected_reason="entree_invalide",
    )


def validate_ical_article(article: Mapping[str, Any], filters: Mapping[str, Any]) -> tuple[bool, str]:
    """Validation commune à tous les articles du moteur CheckIt.AI."""

    if not isinstance(article, Mapping):
        return False, "article_invalide"
    return normalize_validation_result(
        validate_article(article, filters),
        invalid_reason="validation_commune_invalide",
        rejected_reason="article_invalide",
    )
