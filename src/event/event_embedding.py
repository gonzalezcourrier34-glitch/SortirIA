"""Préparation des embeddings des événements.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Le texte
indexé reprend l'idée du « texte enrichi » de l'Assistant RAG OpenAgenda
(projet 6) : titre, catégorie, lieu et description dans un seul texte.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Les embeddings ont une taille d'entrée limitée : on coupe les descriptions trop longues.
MAX_DESCRIPTION_CHARS = 1500


def build_embedding_text(event: Mapping[str, Any]) -> str:
    """Texte envoyé au modèle d'embeddings pour un événement.

    Exemple : « Concert Jazz au Jam. Catégorie : concert. Lieu : Le Jam,
    Montpellier. Soirée jazz avec le Blue Mood Quartet. »

    La description est coupée à `MAX_DESCRIPTION_CHARS` caractères.

    Parameters
    ----------
    event : Mapping[str, Any]
        L'événement (`title`, `category_norm` ou `category`, `summary`,
        `venue_name`, `city`, `description`).

    Returns
    -------
    str
        Les morceaux renseignés, joints par « . » et terminés par un point.
    """

    parts = [str(event.get("title") or "").strip()]
    # Catégorie unifiée et résumé de l'enrichissement Mistral, quand ils existent.
    category = event.get("category_norm") or event.get("category")
    if category:
        parts.append(f"Catégorie : {category}")
    if event.get("summary"):
        parts.append(str(event["summary"]).strip())
    place = ", ".join(str(value) for value in (event.get("venue_name"), event.get("city")) if value)
    if place:
        parts.append(f"Lieu : {place}")
    description = str(event.get("description") or "").strip()
    if description:
        parts.append(description[:MAX_DESCRIPTION_CHARS])
    return ". ".join(part.rstrip(".") for part in parts if part) + "."


def to_pgvector(vector: Sequence[float]) -> str:
    """Format texte accepté par pgvector : « [0.1,0.2,...] » (casté en vector en SQL).

    Parameters
    ----------
    vector : Sequence[float]
        Le vecteur d'embedding.

    Returns
    -------
    str
        Le vecteur en texte, sept chiffres significatifs par valeur.
    """

    return "[" + ",".join(f"{float(value):.7g}" for value in vector) + "]"
