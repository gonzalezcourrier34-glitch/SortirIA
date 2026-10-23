"""Routes de l'API lues par l'interface Angular (`frontend/`).

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), sur le
modèle de l'API de Chess Agent (projet 13) : préfixe `/api/v1`, recherche
diffusée en Server-Sent Events (`event: progress`, puis `event: completed`).
La logique est dans `app/search_service.py` (recherche), `app/search_detail.py`
(détail, avis), `app/search_cards.py` (offres, sources) et `app/search_log.py`
(pouces, santé).

    POST /api/v1/recherche/stream          question + filtres -> étapes, puis réponse
    GET  /api/v1/evenements/{event_id}     détail d'un événement et avis sur son lieu
    POST /api/v1/evenements/{event_id}/avis  avis d'un visiteur (étoiles, commentaire)
    POST /api/v1/avis/a-donner             « Vos sorties à noter »
    GET  /api/v1/partenaires/a-ne-pas-manquer
    GET  /api/v1/sources                   sources qui ont des événements proposables
    POST /api/v1/reponses/{request_id}/avis  pouce haut / pouce bas sur une réponse
    GET  /api/v1/sante                     l'API et la base répondent, âge de la dernière collecte

Ces routes sont publiques (pas de clé API) : l'interface est ouverte à tous.
Elles sont limitées par visiteur, et la recherche aussi pour tout le service
(`app/rate_limit.py`, 16/10/2026) : réponse 429 au-delà. La notation des
clients, elle, reste derrière la clé (`app/ratings_api.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import rate_limit, search_cards, search_detail, search_log, search_service
from app.search_filters import SearchFilters

router = APIRouter(prefix="/api/v1", tags=["interface"])


class SearchRequest(BaseModel):
    """Recherche envoyée par l'interface.

    Attributes
    ----------
    question : str
        La phrase du visiteur (500 caractères au plus), vide si seuls des filtres sont cochés.
    public : Literal["famille", "enfant", "soiree", "ados", "seniors"] | None
        Public coché dans l'interface, ou aucun.
    zone : Literal["montpellier", "thau"] | None
        Zone cochée, ou aucune.
    period : Literal["aujourdhui", "weekend", "7jours", "3mois"]
        Période cochée ; par défaut les 3 prochains mois.
    free_only : bool
        Vrai pour ne garder que les sorties gratuites.
    context : str
        Demande précédente de la conversation, renvoyée telle quelle par l'interface.
    follow_up : bool
        Vrai si la question vient d'une relance cliquée sous la réponse.
    """

    question: str = Field(default="", max_length=500)
    public: Literal["famille", "enfant", "soiree", "ados", "seniors"] | None = None
    zone: Literal["montpellier", "thau"] | None = None
    period: Literal["aujourdhui", "weekend", "7jours", "3mois"] = "3mois"
    free_only: bool = False
    # Demande précédente de la conversation (renvoyée par l'API dans `context`) :
    # une relance (« et pour les enfants ? ») garde ce qui avait été demandé.
    context: str = Field(default="", max_length=1000)
    # Vrai quand la question vient d'une relance cliquée sous la réponse :
    # c'est toujours une suite de la demande précédente.
    follow_up: bool = False


def get_connection() -> Iterator[Any]:
    """Connexion PostgreSQL pour une requête (même règle que les notes).

    Yields
    ------
    Any
        La connexion ouverte, fermée après la réponse.
    """

    from agent.services import connect_postgres

    with connect_postgres() as connection:
        yield connection


def get_connection_factory() -> Callable[[], AbstractContextManager[Any]]:
    """Fournit la fabrique de connexions, pour ouvrir la connexion DANS le flux.

    Ouvre la connexion DANS le flux : une dépendance FastAPI « yield »
    peut être refermée avant la fin d'une réponse diffusée (selon la version).

    Returns
    -------
    Callable[[], AbstractContextManager[Any]]
        `connect_postgres`, à appeler dans un `with` pendant la diffusion.
    """

    from agent.services import connect_postgres

    return connect_postgres


def get_search_services() -> search_service.SearchServices:
    """Services réels (Mistral) ; remplacés par des faux dans les tests.

    Returns
    -------
    search_service.SearchServices
        Les services de compréhension, de plongement et de rédaction.
    """

    return search_service.SearchServices()


@router.post("/recherche/stream", dependencies=[Depends(rate_limit.limit_search)])
def search_stream(
    payload: SearchRequest,
    connect: Callable[[], AbstractContextManager[Any]] = Depends(get_connection_factory),  # noqa: B008
    services: search_service.SearchServices = Depends(get_search_services),  # noqa: B008
) -> StreamingResponse:
    """Recherche diffusée étape par étape (text/event-stream).

    Une erreur survenue pendant la diffusion ne peut plus changer le code HTTP :
    elle est envoyée dans le flux sous forme d'un événement `error`.

    Parameters
    ----------
    payload : SearchRequest
        La question, les filtres cochés et le contexte de la conversation.
    connect : Callable[[], AbstractContextManager[Any]]
        Fabrique de connexions PostgreSQL, ouverte dans le flux.
    services : search_service.SearchServices
        Services Mistral (ou faux services dans les tests).

    Returns
    -------
    StreamingResponse
        Le flux Server-Sent Events (`progress`, puis `completed` ou `error`).
    """

    filters = SearchFilters(public=payload.public, zone=payload.zone,
                            period=payload.period, free_only=payload.free_only)

    def stream() -> Iterator[str]:
        """Produit les événements SSE de la recherche, ou un événement d'erreur."""
        try:
            with connect() as connection:
                for event in search_service.run_search(connection, payload.question, filters, services,
                                                          context=payload.context,
                                                          follow_up=payload.follow_up):
                    yield search_service.sse(event)
        except Exception:  # noqa: BLE001 (le flux est déjà ouvert : l'erreur part dans le flux)
            yield search_service.sse({"event": "error",
                                      "message": "La recherche n'a pas pu aboutir. Réessayez dans un instant."})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/evenements/{event_id}", dependencies=[Depends(rate_limit.limit_reads)])
def event_detail(event_id: str, visiteur: str | None = Query(default=None, min_length=16, max_length=100),
                 connection: Any = Depends(get_connection)) -> dict[str, Any]:  # noqa: B008
    """Détail d'un événement encore proposable, ou passé depuis moins de 7 jours.

    Détail d'un événement encore proposable, ou passé depuis moins de
    7 jours (pour pouvoir le noter). `visiteur` : identifiant aléatoire du
    navigateur, pour lui renvoyer son propre avis (jamais stocké en clair).

    Parameters
    ----------
    event_id : str
        Identifiant de l'événement.
    visiteur : str | None
        Identifiant aléatoire du navigateur (16 à 100 caractères), facultatif.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    dict[str, Any]
        Le détail de l'événement, avec les avis sur son lieu et celui du visiteur.

    Raises
    ------
    HTTPException
        Code 404 si l'événement est introuvable ou passé depuis trop longtemps.
    """

    from src.event.event_ratings import user_key

    detail = search_detail.event_detail(connection, event_id,
                                         visitor_key=user_key(visiteur) if visiteur else None)
    if detail is None:
        raise HTTPException(status_code=404, detail="Événement introuvable ou passé.")
    return detail


class RatingRequest(BaseModel):
    """Avis d'un visiteur (étoiles et commentaire facultatif).

    `session_id` est un identifiant aléatoire gardé par le navigateur : il
    n'est jamais stocké en clair (empreinte salée, src/event/event_ratings.py).

    Attributes
    ----------
    session_id : str
        Identifiant aléatoire du navigateur (16 à 100 caractères).
    stars : int
        Nombre d'étoiles, de 1 à 5.
    comment : str
        Commentaire facultatif, 300 caractères au plus.
    """

    session_id: str = Field(min_length=16, max_length=100)
    stars: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=300)


class ToRateRequest(BaseModel):
    """Événements consultés par le visiteur (gardés par son navigateur).

    Attributes
    ----------
    session_id : str
        Identifiant aléatoire du navigateur (16 à 100 caractères).
    event_ids : list[str]
        Identifiants des événements consultés, 50 au plus.
    """

    session_id: str = Field(min_length=16, max_length=100)
    event_ids: list[str] = Field(default_factory=list, max_length=50)


@router.post("/evenements/{event_id}/avis", status_code=201, dependencies=[Depends(rate_limit.limit_writes)])
def rate_event(event_id: str, payload: RatingRequest,
               connection: Any = Depends(get_connection)) -> dict[str, Any]:  # noqa: B008
    """Enregistre (ou remplace) l'avis du visiteur.

    Le commentaire est modéré par Mistral Moderation avant d'être publié.

    Parameters
    ----------
    event_id : str
        Identifiant de l'événement noté.
    payload : RatingRequest
        L'avis : identifiant du navigateur, étoiles, commentaire.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    dict[str, Any]
        Le statut de l'enregistrement et celui du commentaire (« aucun » s'il n'y en a pas).

    Raises
    ------
    HTTPException
        Code 409 si l'avis est hors délai (avant le début, ou plus de 7 jours après la fin).
    """

    from src.event.event_ratings import submit_rating

    result = submit_rating(connection, event_id, payload.session_id, payload.stars, payload.comment)
    if result["status"] == "hors_delai":
        raise HTTPException(status_code=409,
                            detail="Avis possible du début de l'événement jusqu'à 7 jours après sa fin.")
    connection.commit()
    return {"status": result["status"], "comment_status": result.get("comment_status", "aucun")}


@router.post("/avis/a-donner", dependencies=[Depends(rate_limit.limit_reads)])
def to_rate(payload: ToRateRequest, connection: Any = Depends(get_connection)) -> list[dict[str, Any]]:  # noqa: B008
    """« Vos sorties à noter » : parmi les événements consultés, ceux notables maintenant.

    Parameters
    ----------
    payload : ToRateRequest
        L'identifiant du navigateur et les événements qu'il a consultés.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    list[dict[str, Any]]
        Les événements que le visiteur peut noter maintenant.
    """

    return search_detail.events_to_rate(connection, payload.event_ids, payload.session_id)


class AnswerFeedback(BaseModel):
    """Avis sur une réponse : utile (1) ou pas (-1), avec un mot facultatif.

    Attributes
    ----------
    rating : Literal[-1, 1]
        1 pour pouce haut, -1 pour pouce bas.
    comment : str
        Mot facultatif, 300 caractères au plus.
    """

    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=300)


@router.post("/reponses/{request_id}/avis", status_code=201,
             dependencies=[Depends(rate_limit.limit_writes)])
def answer_feedback(request_id: int, payload: AnswerFeedback,
                    connection: Any = Depends(get_connection)) -> dict[str, str]:  # noqa: B008
    """Pouce haut / pouce bas sur une réponse de SortirIA (16/10/2026).

    Parameters
    ----------
    request_id : int
        Identifiant de la réponse, renvoyé par la recherche.
    payload : AnswerFeedback
        Le pouce et le mot facultatif.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    dict[str, str]
        `{"status": "enregistre"}`.

    Raises
    ------
    HTTPException
        Code 404 si la réponse est introuvable ou trop ancienne.
    """

    if not search_log.record_feedback(connection, request_id, payload.rating, payload.comment):
        raise HTTPException(status_code=404, detail="Réponse introuvable ou trop ancienne.")
    return {"status": "enregistre"}


@router.get("/partenaires/a-ne-pas-manquer", dependencies=[Depends(rate_limit.limit_reads)])
def highlights(limit: int = Query(default=3, ge=1, le=10),
               connection: Any = Depends(get_connection)) -> list[dict[str, Any]]:  # noqa: B008
    """Prochains événements des partenaires (mis en avant d'abord).

    Parameters
    ----------
    limit : int
        Nombre d'événements, de 1 à 10. Par défaut 3.
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    list[dict[str, Any]]
        Les événements « à ne pas manquer ».
    """

    return search_cards.partner_highlights(connection, limit)


@router.get("/sources", dependencies=[Depends(rate_limit.limit_reads)])
def sources(connection: Any = Depends(get_connection)) -> list[dict[str, Any]]:  # noqa: B008
    """Sources qui ont au moins un événement proposable.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    list[dict[str, Any]]
        Pour chaque source : son nom et son nombre d'événements proposables.
    """

    return search_cards.active_sources(connection)


@router.get("/sante", dependencies=[Depends(rate_limit.limit_reads)])
def health(connection: Any = Depends(get_connection)) -> dict[str, Any]:  # noqa: B008
    """L'API répond, la base aussi, et la collecte de la nuit est passée.

    Seule route de santé joignable de l'extérieur (le site ne transmet que
    `/api/...`) : la surveillance GitHub l'appelle toutes les 3 h
    (`.github/workflows/surveillance.yml`).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL fournie par `get_connection`.

    Returns
    -------
    dict[str, Any]
        `api` à « ok », `base` d'après un vrai `SELECT 1` (« ok » ou « erreur »),
        et l'état de la dernière collecte.
    """

    return {"api": "ok", "base": search_log.database_status(connection),
            "collecte": search_log.collect_health(connection)}
