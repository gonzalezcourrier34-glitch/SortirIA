"""Contrat commun des lecteurs de « sources-fichiers » (un document par source).

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), calqué sur
`src/extractors/rss/rss_adapter.py` de CheckIt.AI (même découpage : le
moteur orchestre, l'adaptateur sait lire un format). Écrit d'abord pour les
calendriers iCal, puis rendu commun à tous les formats « un fichier par
source » (iCal, programme PDF). Les fonctions de normalisation des
validations sont réutilisées telles quelles depuis le module RSS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.extractors.rss.rss_adapter import normalize_validation_result
from src.utils.extractor_utils import validate_article
from src.utils.value_utils import normalize_value

FileItem = Any
FileSource = Mapping[str, Any]
FileIterator = Callable[[FileSource], Iterable[FileItem]]
FileArticleBuilder = Callable[[FileItem, FileSource], dict[str, Any]]
FileItemValidator = Callable[[FileItem, FileSource], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class FileSourceAdapter:
    """Décrit comment lire un format : ses éléments, et comment en faire un article.

    Le moteur commun (`file_engine`) orchestre ; l'adaptateur ne fournit que les
    trois fonctions propres au format.

    Attributes
    ----------
    iter_items : FileIterator
        Télécharge la source et renvoie ses éléments bruts (événements iCal,
        événements lus dans un PDF...).
    build_article : FileArticleBuilder
        Transforme un élément validé en article standard du moteur.
    validate_item : FileItemValidator | None
        Validation propre au format, avant la construction de l'article.
        Par défaut aucune.
    default_name : str
        Nom du lecteur dans les journaux et le bilan. Par défaut « Fichiers ».
    """

    iter_items: FileIterator
    build_article: FileArticleBuilder
    validate_item: FileItemValidator | None = None
    default_name: str = "Fichiers"

    def __post_init__(self) -> None:
        """Vérifie que les fonctions fournies sont appelables et normalise le nom.

        Raises
        ------
        TypeError
            Si `iter_items` ou `build_article` n'est pas appelable, ou si
            `validate_item` n'est ni appelable ni `None`.
        """
        if not callable(self.iter_items):
            raise TypeError("iter_items doit être une fonction appelable.")
        if not callable(self.build_article):
            raise TypeError("build_article doit être une fonction appelable.")
        if self.validate_item is not None and not callable(self.validate_item):
            raise TypeError("validate_item doit être une fonction appelable ou None.")
        object.__setattr__(self, "default_name", normalize_value(self.default_name) or "Fichiers")


def validate_file_item(item: FileItem, source: FileSource, adapter: FileSourceAdapter) -> tuple[bool, str]:
    """Validation propre à l'adaptateur (avant la construction de l'article).

    Parameters
    ----------
    item : FileItem
        Élément brut renvoyé par `adapter.iter_items`.
    source : FileSource
        Configuration de la source.
    adapter : FileSourceAdapter
        Adaptateur du format ; sans `validate_item`, tout élément est accepté.

    Returns
    -------
    tuple[bool, str]
        Vrai et motif vide si l'élément est accepté, sinon faux et le motif du rejet.
    """

    if adapter.validate_item is None:
        return True, ""
    return normalize_validation_result(
        adapter.validate_item(item, source),
        invalid_reason="validation_entree_invalide",
        rejected_reason="entree_invalide",
    )


def validate_file_article(article: Mapping[str, Any], filters: Mapping[str, Any]) -> tuple[bool, str]:
    """Validation commune à tous les articles du moteur CheckIt.AI.

    Parameters
    ----------
    article : Mapping[str, Any]
        Article construit par l'adaptateur.
    filters : Mapping[str, Any]
        Filtres de la source (mots-clés, longueurs minimales...).

    Returns
    -------
    tuple[bool, str]
        Vrai et motif vide si l'article est accepté, sinon faux et le motif du rejet.
    """

    if not isinstance(article, Mapping):
        return False, "article_invalide"
    return normalize_validation_result(
        validate_article(article, filters),
        invalid_reason="validation_commune_invalide",
        rejected_reason="article_invalide",
    )
