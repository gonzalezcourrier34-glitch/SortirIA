"""Contrat commun des adaptateurs d'API CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/apis/api_adapter.py` (commit 93632c5).

Ce module définit uniquement les types, le modèle ApiAdapter et les fonctions
chargées de transformer ou valider les éléments propres à chaque fournisseur.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from src.extractors.core.extractor_results import ExtractorResult
from src.logger import get_logger
from src.utils.extractor_utils import normalize_value

logger = get_logger(__name__)


# Types utilisés par les adaptateurs

ApiItem = Any
ApiExtractionResult = list[dict[str, Any]] | ExtractorResult

# Parcourt les éléments retournés par une API.
IterApiItemsCallable = Callable[
    [Mapping[str, Any], int],
    Iterator[tuple[str, int, ApiItem]]
]

# Transforme un élément brut en article standard.
BuildApiArticleCallable = Callable[
    [ApiItem, int, str, Mapping[str, Any]],
    dict[str, Any]
]

# Valide facultativement un élément selon les règles de son API.
ValidateApiItemCallable = Callable[
    [ApiItem, Mapping[str, Any], Mapping[str, Any]],
    tuple[bool, str]
]


# Adaptateur propre à une API

@dataclass(frozen=True, slots=True)
class ApiAdapter:
    """Regroupe les fonctions propres au format d'une API.

    Le moteur générique (`api_extractor`) s'occupe des appels HTTP, de la
    pagination et des filtres ; l'adaptateur ne sait que parcourir une réponse
    et en tirer des articles.

    Attributes
    ----------
    source_id : str
        Identifiant de la source (normalisé, non vide).
    default_name : str
        Nom affiché par défaut ; reprend `source_id` s'il est vide.
    iter_items : IterApiItemsCallable
        Parcourt une réponse d'API et donne (identifiant, rang, élément brut).
    build_article : BuildApiArticleCallable
        Transforme un élément brut en article standard.
    validate_item : ValidateApiItemCallable | None
        Validation propre à l'API, facultative. Par défaut `None`.
    """

    source_id: str
    default_name: str
    iter_items: IterApiItemsCallable
    build_article: BuildApiArticleCallable
    validate_item: ValidateApiItemCallable | None = None

    def __post_init__(self) -> None:
        """Normalise l'identité et valide les fonctions de l'adaptateur.

        La classe étant figée, les valeurs normalisées sont réécrites avec
        `object.__setattr__`.

        Raises
        ------
        ValueError
            Si l'identifiant de la source est vide.
        TypeError
            Si une des fonctions fournies n'est pas appelable.
        """

        source_id = normalize_value(self.source_id)
        default_name = normalize_value(self.default_name) or source_id

        if not source_id:
            raise ValueError(
                "L'identifiant d'un ApiAdapter ne peut pas être vide."
            )

        if not callable(self.iter_items):
            raise TypeError("iter_items doit être une fonction appelable.")

        if not callable(self.build_article):
            raise TypeError("build_article doit être une fonction appelable.")

        if self.validate_item is not None and not callable(self.validate_item):
            raise TypeError(
                "validate_item doit être une fonction appelable ou None."
            )

        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "default_name", default_name)


def build_article_from_api_item(
    adapter: ApiAdapter,
    item: ApiItem,
    item_index: int,
    item_identifier: str,
    source: Mapping[str, Any],
    source_name: str
) -> dict[str, Any]:
    """Transforme un élément brut d'API en article CheckIt.AI.

    Une erreur de l'adaptateur est journalisée et donne un article vide,
    pour ne pas interrompre l'extraction des autres éléments.

    Parameters
    ----------
    adapter : ApiAdapter
        L'adaptateur de l'API.
    item : ApiItem
        L'élément brut renvoyé par l'API.
    item_index : int
        Rang de l'élément dans la réponse.
    item_identifier : str
        Identifiant de l'élément, pour le journal.
    source : Mapping[str, Any]
        La configuration de la source.
    source_name : str
        Nom de la source, pour le journal.

    Returns
    -------
    dict[str, Any]
        L'article construit, ou un dictionnaire vide en cas d'erreur.
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
            "Impossible de construire l'élément %s (%s) de %s : %s",
            item_index,
            item_identifier,
            source_name,
            error
        )
        return {}

    if not isinstance(article, dict):
        logger.warning(
            "L'adaptateur %s a retourné un %s au lieu d'un dictionnaire.",
            adapter.source_id,
            type(article).__name__
        )
        return {}

    return article


def validate_api_item(
    adapter: ApiAdapter,
    item: ApiItem,
    filters: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Applique la validation spécifique définie par l'adaptateur.

    Parameters
    ----------
    adapter : ApiAdapter
        L'adaptateur de l'API ; sans `validate_item`, tout élément est accepté.
    item : ApiItem
        L'élément brut à valider.
    filters : Mapping[str, Any]
        Les filtres de la source.
    source : Mapping[str, Any]
        La configuration de la source.

    Returns
    -------
    tuple[bool, str]
        Vrai et une chaîne vide si l'élément est retenu, sinon faux et le motif
        (`validation_specifique_invalide` ou `resultat_validation_invalide` en
        cas d'erreur de l'adaptateur).
    """

    if adapter.validate_item is None:
        return True, ""

    try:
        result = adapter.validate_item(item, filters, source)
    except (TypeError, ValueError, AttributeError, KeyError, OSError) as error:
        logger.warning(
            "Erreur de validation spécifique pour %s : %s",
            adapter.source_id,
            error
        )
        return False, "validation_specifique_invalide"

    if not isinstance(result, tuple) or len(result) != 2:
        logger.warning(
            "La validation de %s a retourné un résultat invalide.",
            adapter.source_id
        )
        return False, "resultat_validation_invalide"

    valid, reason = result
    return bool(valid), normalize_value(reason)