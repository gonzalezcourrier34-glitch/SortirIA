"""Routes de l'API pour les notes des clients sur les événements.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Séparé de
`app/main.py` (hérité du projet RAG, refait au bloc 2) pour être repris tel
quel dans la nouvelle API. La logique est dans `src/event/event_ratings.py`.

    POST /events/{event_id}/notes   {"session_id": "...", "stars": 4, "comment": "Super soirée"}
    GET  /events/{event_id}/notes   -> moyenne, nombre de notes, commentaires publiés

Notation ouverte du début de l'événement à 7 jours après sa fin ; une note
par client (identifiant de session, jamais stocké en clair) et par événement.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.security import require_api_key

router = APIRouter(prefix="/events", tags=["notes des clients"], dependencies=[Depends(require_api_key)])


class RatingRequest(BaseModel):
    """Note envoyée par un service partenaire (site, application).

    Attributes
    ----------
    session_id : str
        Identifiant de session du client (8 caractères au moins), anonymisé avant stockage.
    stars : int
        Nombre d'étoiles, de 1 à 5.
    comment : str
        Commentaire facultatif, 300 caractères au plus.
    """

    session_id: str = Field(min_length=8, description="Identifiant de session du client (anonymisé avant stockage)")
    stars: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=300)


def get_connection() -> Iterator[Any]:
    """Connexion PostgreSQL pour une requête (validée à la fin, annulée en cas d'erreur).

    Yields
    ------
    Any
        La connexion ouverte, fermée après la réponse.
    """

    from agent.services import connect_postgres

    with connect_postgres() as connection:
        yield connection


@router.post("/{event_id}/notes", status_code=status.HTTP_201_CREATED)
def rate_event(event_id: str, payload: RatingRequest, connection: Any = Depends(get_connection)) -> dict[str, Any]:  # noqa: B008
    """Enregistre ou met à jour la note d'un client sur un événement.

    Parameters
    ----------
    event_id : str
        Identifiant de l'événement noté.
    payload : RatingRequest
        La note : session du client, étoiles, commentaire.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    dict[str, Any]
        Le résultat de `submit_rating` (statut de l'enregistrement).

    Raises
    ------
    HTTPException
        Code 409 si la notation n'est pas ouverte (avant le début, ou plus de
        7 jours après la fin).
    """

    from src.event.event_ratings import submit_rating

    result = submit_rating(connection, event_id, payload.session_id, payload.stars, payload.comment)
    if result["status"] == "hors_delai":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Notation ouverte du début de l'événement à 7 jours après sa fin.",
        )
    return result


@router.get("/{event_id}/notes")
def event_ratings(event_id: str, connection: Any = Depends(get_connection)) -> dict[str, Any]:  # noqa: B008
    """Renvoie la moyenne, le nombre de notes et les commentaires publiés d'un événement.

    Parameters
    ----------
    event_id : str
        Identifiant de l'événement.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    dict[str, Any]
        `event_id` suivi du résumé calculé par `summarize_ratings`.
    """

    from src.event import event_repository as repository
    from src.event.event_ratings import summarize_ratings

    return {"event_id": event_id, **summarize_ratings(repository.fetch_event_ratings(connection, event_id))}
