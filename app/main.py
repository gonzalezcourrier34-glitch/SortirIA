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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirige vers la documentation interactive de l'API."""

    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    """L'API répond (contrôle de Docker et du futur déploiement)."""

    return {"status": "ok"}
