"""API SortirIA (FastAPI).

**Provenance** : le fichier d'origine vient de l'Assistant RAG OpenAgenda
(projet 6, commit 18f7ce1). Le 15/10/2026, ses routes héritées (`/ask`,
`/ask/debug`, `/rebuild`, index FAISS construit au démarrage) ont été retirées
avec tout l'ancien RAG : la recherche de SortirIA lit PostgreSQL et pgvector
(`app/search_service.py`). Historique : `PROVENANCE.md` et l'historique git.

Routes :

- `/api/v1/...` : l'interface (recherche diffusée, détail, avis, partenaires),
  voir `app/interface_api.py` et `docs/interface.md` ;
- `/events/{event_id}/notes` : notes des clients, protégées par la clé API
  (`app/ratings_api.py`) ;
- `/health` : l'API répond ;
- `/` : redirige vers la documentation interactive (`/docs`).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.interface_api import router as interface_router
from app.ratings_api import router as ratings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

app = FastAPI(
    title="SortirIA",
    description="Assistant de sorties autour de Montpellier et du bassin de Thau.",
    version="1.0.0",
)
app.include_router(interface_router)
app.include_router(ratings_router)


class NoIndexingOfTheData:
    """Middleware ASGI qui interdit l'indexation des réponses de `/api/`.

    Les données (offres des partenaires, agendas réunis) ne sont pas à indexer
    ni à archiver (16/10/2026) : en-tête X-Robots-Tag sur /api/, en complément
    de `frontend/public/robots.txt` (que seuls les robots honnêtes respectent ;
    la limite de débit fait le reste). Middleware ASGI pur : la recherche
    diffusée (Server-Sent Events) passe sans être mise en mémoire tampon.

    Parameters
    ----------
    app : Any
        L'application ASGI enveloppée (FastAPI et ses autres middlewares).

    Attributes
    ----------
    HEADER : tuple[bytes, bytes]
        L'en-tête ajouté aux réponses : `X-Robots-Tag: noindex, nofollow, noarchive`.
    """

    HEADER = (b"x-robots-tag", b"noindex, nofollow, noarchive")

    def __init__(self, app: Any) -> None:
        """Garde l'application enveloppée (paramètres : voir la classe)."""
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Traite une requête ASGI et ajoute l'en-tête aux réponses HTTP de `/api/`.

        Les autres requêtes (WebSocket, lifespan, chemins hors `/api/`) passent
        sans changement.

        Parameters
        ----------
        scope : dict[str, Any]
            La description ASGI de la connexion (type, chemin, en-têtes).
        receive : Any
            La fonction ASGI qui lit les messages entrants.
        send : Any
            La fonction ASGI qui envoie les messages de réponse.
        """
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: dict[str, Any]) -> None:
            """Ajoute l'en-tête X-Robots-Tag au début de la réponse, puis l'envoie."""
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), self.HEADER]
            await send(message)

        await self.app(scope, receive, send_with_header)


app.add_middleware(NoIndexingOfTheData)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirige vers la documentation interactive de l'API.

    Returns
    -------
    RedirectResponse
        Une redirection vers `/docs`.
    """

    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    """Indique que l'API répond (contrôle de Docker et du futur déploiement).

    Returns
    -------
    dict[str, str]
        Toujours `{"status": "ok"}`.
    """

    return {"status": "ok"}
