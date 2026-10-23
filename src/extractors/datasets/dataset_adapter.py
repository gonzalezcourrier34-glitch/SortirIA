"""Contrat commun des adaptateurs de datasets CheckIt.AI.

Un `DatasetAdapter` regroupe les fonctions propres à un dataset (trouver les
fichiers, parcourir les éléments, construire et valider un article) ; les
fonctions du module les appellent sans qu'une erreur isolée bloque l'extraction.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/datasets/dataset_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from src.logger import get_logger
from src.utils.extractor_utils import normalize_value

logger = get_logger(__name__)


# Types utilisés par les adaptateurs

DatasetItem = Any

FindFilesCallable = Callable[[Path, Mapping[str, Any]], list[Path]]

IterItemsCallable = Callable[
    [list[Path], Mapping[str, Any]],
    Iterator[tuple[str, int, DatasetItem]]
]

BuildArticleCallable = Callable[
    [DatasetItem, int, str, Mapping[str, Any]],
    dict[str, Any]
]

ValidateItemCallable = Callable[
    [DatasetItem, Mapping[str, Any], Mapping[str, Any]],
    tuple[bool, str]
]

# Adaptateur propre à un dataset

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatasetAdapter:
    """Regroupe les fonctions propres à un dataset.

    Attributes
    ----------
    source_id : str
        Identifiant de la source ; obligatoire.
    default_name : str
        Nom lisible par défaut ; reprend `source_id` s'il est vide.
    supported_extensions : frozenset[str]
        Extensions de fichiers acceptées, normalisées en minuscules avec un point.
    find_files : FindFilesCallable
        Trouve les fichiers à lire dans un dossier selon la configuration.
    iter_items : IterItemsCallable
        Parcourt les fichiers et produit `(identifiant, index, élément)`.
    build_article : BuildArticleCallable
        Transforme un élément brut en article.
    validate_item : ValidateItemCallable | None
        Validation propre au dataset, renvoyant `(valide, raison)` ; facultative.
    """

    source_id: str
    default_name: str
    supported_extensions: frozenset[str]
    find_files: FindFilesCallable
    iter_items: IterItemsCallable
    build_article: BuildArticleCallable
    validate_item: ValidateItemCallable | None = None

    def __post_init__(self) -> None:
        """Normalise et valide l'adaptateur.

        Raises
        ------
        ValueError
            Si l'identifiant est vide ou si aucune extension n'est valide.
        TypeError
            Si une des fonctions de l'adaptateur n'est pas appelable.
        """

        # Normaliser l'identifiant, le nom et les extensions (« csv » -> « .csv »)
        source_id = normalize_value(self.source_id)
        default_name = normalize_value(self.default_name) or source_id
        extensions = frozenset(
            extension if extension.startswith(".") else f".{extension}"
            for value in self.supported_extensions
            if (extension := normalize_value(value).lower())
        )

        # Valider les champs et les fonctions fournies
        if not source_id:
            raise ValueError(
                "L'identifiant d'un DatasetAdapter ne peut pas être vide."
            )
        if not extensions:
            raise ValueError(
                f"Aucune extension prise en charge pour {source_id}."
            )
        if not callable(self.find_files):
            raise TypeError("find_files doit être une fonction appelable.")
        if not callable(self.iter_items):
            raise TypeError("iter_items doit être une fonction appelable.")
        if not callable(self.build_article):
            raise TypeError("build_article doit être une fonction appelable.")
        if self.validate_item is not None and not callable(self.validate_item):
            raise TypeError(
                "validate_item doit être une fonction appelable ou None."
            )

        # Réécrire les valeurs normalisées sur l'objet figé
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "default_name", default_name)
        object.__setattr__(
            self,
            "supported_extensions",
            extensions
        )
        
# Construction et validation des éléments

def build_article_from_item(
    adapter: DatasetAdapter,
    item: DatasetItem,
    item_index: int,
    item_identifier: str,
    source: Mapping[str, Any],
    source_name: str
) -> dict[str, Any]:
    """Construit un article sans bloquer toute l'extraction.

    Parameters
    ----------
    adapter : DatasetAdapter
        L'adaptateur du dataset.
    item : DatasetItem
        L'élément brut lu dans le fichier.
    item_index : int
        Position de l'élément dans le fichier.
    item_identifier : str
        Identifiant du fichier ou de la portion d'où vient l'élément.
    source : Mapping[str, Any]
        Configuration de la source.
    source_name : str
        Nom lisible de la source, pour les journaux.

    Returns
    -------
    dict[str, Any]
        L'article construit, ou un dictionnaire vide si la construction échoue
        ou ne renvoie pas un dictionnaire.
    """

    try:
        article = adapter.build_article(
            item,
            item_index,
            item_identifier,
            source
        )
    except (TypeError, ValueError, AttributeError, KeyError, OSError) as error:
        logger.warning(
            "Impossible de construire l'élément %s de %s pour %s : %s",
            item_index,
            item_identifier,
            source_name,
            error
        )
        return {}

    if not isinstance(article, dict):
        logger.warning(
            "L'adaptateur %s a retourné un type invalide : %s.",
            adapter.source_id,
            type(article).__name__
        )
        return {}

    return article

def validate_adapter_item(
    adapter: DatasetAdapter,
    item: DatasetItem,
    filters: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Lance la validation propre au dataset si elle existe.

    Parameters
    ----------
    adapter : DatasetAdapter
        L'adaptateur du dataset.
    item : DatasetItem
        L'élément brut à valider.
    filters : Mapping[str, Any]
        Filtres de la source.
    source : Mapping[str, Any]
        Configuration de la source.

    Returns
    -------
    tuple[bool, str]
        `(True, "")` sans validateur ; sinon `(valide, raison)`, ou
        `(False, "invalid_specific_validation")` si le validateur échoue ou
        renvoie un format inattendu.
    """

    if adapter.validate_item is None:
        return True, ""

    try:
        result = adapter.validate_item(item, filters, source)
    except (TypeError, ValueError, AttributeError, KeyError, OSError) as error:
        logger.warning(
            "Erreur pendant la validation de %s : %s",
            adapter.source_id,
            error
        )
        return False, "invalid_specific_validation"

    if not isinstance(result, tuple) or len(result) != 2:
        logger.warning(
            "Le validateur de %s doit retourner un tuple (bool, raison).",
            adapter.source_id
        )
        return False, "invalid_specific_validation"

    valid, reason = result
    return bool(valid), normalize_value(reason)