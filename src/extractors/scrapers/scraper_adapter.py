"""Contrats et adaptateur communs aux extracteurs HTML CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.utils.extractor_utils import normalize_value

ScraperItem = Any
ScraperSource = Mapping[str, Any]
ScraperIterator = Callable[[ScraperSource], Iterable[ScraperItem]]
ScraperArticleBuilder = Callable[[ScraperItem, ScraperSource], dict[str, Any]]
ScraperItemValidator = Callable[[ScraperItem, ScraperSource], tuple[bool, str]]
ScraperArticleValidator = Callable[
    [Mapping[str, Any], ScraperSource],
    tuple[bool, str]
]


# Adaptateur

@dataclass(frozen=True, slots=True)
class ScraperAdapter:
    """Décrit le comportement propre à un scraper HTML.

    Attributes
    ----------
    source_id : str
        Identifiant de la source ; ne peut pas être vide.
    default_name : str
        Nom affiché ; vide, il reprend `source_id`.
    iter_items : ScraperIterator
        Renvoie les éléments bruts de la page (ou des pages) à lire.
    build_article : ScraperArticleBuilder
        Transforme un élément en article standard.
    validate_item : ScraperItemValidator | None
        Validation d'un élément avant construction. Par défaut aucune.
    validate_article : ScraperArticleValidator | None
        Validation propre de l'article construit. Par défaut aucune.
    """

    source_id: str
    default_name: str
    iter_items: ScraperIterator
    build_article: ScraperArticleBuilder
    validate_item: ScraperItemValidator | None = None
    validate_article: ScraperArticleValidator | None = None

    def __post_init__(self) -> None:
        """Normalise et valide l'adaptateur.

        Raises
        ------
        ValueError
            Si `source_id` est vide.
        TypeError
            Si une fonction obligatoire n'est pas appelable, ou si une fonction
            facultative n'est ni appelable ni `None`.
        """

        source_id = normalize_value(self.source_id)
        default_name = normalize_value(self.default_name) or source_id

        if not source_id:
            raise ValueError(
                "L'identifiant d'un ScraperAdapter ne peut pas être vide."
            )
        if not callable(self.iter_items):
            raise TypeError("iter_items doit être une fonction appelable.")
        if not callable(self.build_article):
            raise TypeError("build_article doit être une fonction appelable.")
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

        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "default_name", default_name)