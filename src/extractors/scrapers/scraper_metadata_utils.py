"""Extraction des métadonnées HTML communes aux scrapers.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_metadata_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import extruct
from w3lib.html import get_base_url

from src.extractors.scrapers.scraper_selector_utils import clean_text
from src.logger import get_logger

logger = get_logger(__name__)

ARTICLE_TYPES: frozenset[str] = frozenset({
    "article",
    "newsarticle",
    "reportagearticle",
    "analysisnewsarticle",
    "opinionnewsarticle",
    "reviewnewsarticle",
    "backgroundnewsarticle",
    "blogposting"
})


def empty_metadata() -> dict[str, list[Any]]:
    """Retourne une structure de métadonnées vide.

    Returns
    -------
    dict[str, list[Any]]
        Les clés `json-ld` et `opengraph`, chacune avec une liste vide.
    """

    return {"json-ld": [], "opengraph": []}


def extract_metadata(raw_html: str, page_url: str) -> dict[str, list[Any]]:
    """Extrait les métadonnées JSON-LD et Open Graph.

    Utilise `extruct` en mode uniforme ; une page illisible donne une
    structure vide au lieu d'une erreur.

    Parameters
    ----------
    raw_html : str
        HTML brut de la page.
    page_url : str
        URL de la page, pour résoudre l'URL de base et les liens relatifs.

    Returns
    -------
    dict[str, list[Any]]
        Les listes d'objets `json-ld` et `opengraph` (éventuellement vides).
    """

    # Lancer extruct, en tolérant le HTML mal formé
    try:
        metadata = extruct.extract(
            raw_html,
            base_url=get_base_url(raw_html, page_url),
            syntaxes=["json-ld", "opengraph"],
            uniform=True
        )
    except (TypeError, ValueError, AttributeError) as error:
        logger.debug("Métadonnées HTML non exploitables pour %s : %s", page_url, error)
        return empty_metadata()

    # Ne garder que des listes pour les deux syntaxes attendues
    if not isinstance(metadata, dict):
        return empty_metadata()

    json_ld = metadata.get("json-ld", [])
    open_graph = metadata.get("opengraph", [])

    return {
        "json-ld": json_ld if isinstance(json_ld, list) else [],
        "opengraph": open_graph if isinstance(open_graph, list) else []
    }


def flatten_json_ld(value: Any) -> list[dict[str, Any]]:
    """Aplatit récursivement une structure JSON-LD.

    Chaque objet est gardé tel quel, et les objets de son `@graph` sont
    ajoutés à la suite ; les listes sont parcourues élément par élément.

    Parameters
    ----------
    value : Any
        Objet, liste ou valeur JSON-LD.

    Returns
    -------
    list[dict[str, Any]]
        Tous les objets trouvés, dans l'ordre de parcours.
    """

    objects: list[dict[str, Any]] = []

    if isinstance(value, Mapping):
        current = dict(value)
        objects.append(current)

        if "@graph" in current:
            objects.extend(flatten_json_ld(current["@graph"]))

    elif isinstance(value, list):
        for item in value:
            objects.extend(flatten_json_ld(item))

    return objects


def get_json_ld_objects(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Retourne tous les objets JSON-LD.

    Parameters
    ----------
    metadata : Mapping[str, Any]
        Métadonnées produites par `extract_metadata`.

    Returns
    -------
    list[dict[str, Any]]
        Les objets JSON-LD aplatis, `@graph` compris.
    """

    items = metadata.get("json-ld", [])
    if not isinstance(items, list):
        return []

    objects: list[dict[str, Any]] = []

    for item in items:
        objects.extend(flatten_json_ld(item))

    return objects


def normalize_json_ld_types(value: Any) -> set[str]:
    """Normalise la valeur @type.

    Parameters
    ----------
    value : Any
        Valeur `@type` : une chaîne ou une liste de chaînes.

    Returns
    -------
    set[str]
        Les types nettoyés en minuscules ; vide si la valeur est inattendue.
    """

    values = [value] if isinstance(value, str) else value

    if not isinstance(values, (list, tuple, set)):
        return set()

    return {clean_text(item).lower() for item in values if clean_text(item)}


def get_article_json_ld(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Retourne le meilleur objet JSON-LD d'article.

    Parmi les objets dont le type figure dans `ARTICLE_TYPES`, retient celui
    qui a, par ordre d'importance, un corps, un titre, une date et une image.

    Parameters
    ----------
    metadata : Mapping[str, Any]
        Métadonnées produites par `extract_metadata`.

    Returns
    -------
    dict[str, Any]
        L'objet retenu, ou un dictionnaire vide s'il n'y a aucun article.
    """

    candidates = [
        item
        for item in get_json_ld_objects(metadata)
        if normalize_json_ld_types(item.get("@type")) & ARTICLE_TYPES
    ]

    if not candidates:
        return {}

    return max(
        candidates,
        key=lambda item: (
            bool(item.get("articleBody")),
            bool(item.get("headline")),
            bool(item.get("datePublished")),
            bool(item.get("image"))
        )
    )


def get_open_graph(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Retourne le premier objet Open Graph exploitable.

    Parameters
    ----------
    metadata : Mapping[str, Any]
        Métadonnées produites par `extract_metadata`.

    Returns
    -------
    dict[str, Any]
        Une copie du premier objet Open Graph, ou un dictionnaire vide.
    """

    items = metadata.get("opengraph", [])

    if not isinstance(items, list):
        return {}

    for item in items:
        if isinstance(item, Mapping):
            return dict(item)

    return {}