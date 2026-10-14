"""
Application principale FastAPI pour l'API RAG OpenAgenda.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/main.py` (commit 18f7ce1).

Ce module expose les endpoints REST de l'application. Il permet :

- de vérifier l'état de l'API
- de poser une question au système RAG
- de reconstruire l'index documentaire
- d'interroger un endpoint de debug pour l'évaluation

L'application s'appuie sur les services internes suivants :

- document_service pour charger les événements OpenAgenda
- rag_service pour orchestrer le pipeline RAG
- retrieval_service pour filtrer et reranker les documents candidats
- memory_service pour gérer la mémoire conversationnelle courte
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from app.config import DEFAULT_SCOPE, DEFAULT_ZONE
from app.document_service import load_documents
from app.rag_service import RAGService
from app.schemas import (
    AskRequest,
    AskResponse,
    DebugResponse,
    HealthResponse,
    RebuildRequest,
    RebuildResponse,
)
from app.security import require_api_key


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


rag_service = RAGService()


def _initialize_rag_service() -> None:
    """
    Initialise le service RAG au démarrage de l'application.

    Stratégie :
    - réinitialiser la mémoire conversationnelle courte
    - si l'index est déjà chargé en mémoire, ne rien faire
    - sinon, charger l'index depuis le disque s'il existe
    - sinon, construire un index par défaut à partir du corpus par défaut
    """
    rag_service.memory_service.reset_memory()
    logger.info("Mémoire conversationnelle réinitialisée.")

    if rag_service.is_index_loaded():
        logger.info("Index déjà chargé en mémoire.")
        return

    if rag_service.index_dir.exists() and any(rag_service.index_dir.iterdir()):
        rag_service.load_index()
        rag_service.set_documents(
            load_documents(
                zone=DEFAULT_ZONE,
                scope=DEFAULT_SCOPE,
            )
        )
        rag_service.zone = DEFAULT_ZONE
        rag_service.scope = DEFAULT_SCOPE
        logger.info("Index FAISS chargé depuis le disque.")
        return

    logger.info("Aucun index trouvé. Construction d'un index par défaut...")

    documents = load_documents(
        zone=DEFAULT_ZONE,
        scope=DEFAULT_SCOPE,
    )

    if not documents:
        logger.warning(
            "Aucun document chargé au démarrage. "
            "L'API restera fonctionnelle mais sans index."
        )
        return

    rag_service.zone = DEFAULT_ZONE
    rag_service.scope = DEFAULT_SCOPE
    n_docs = rag_service.rebuild_index(documents)
    logger.info("Index construit avec %s documents.", n_docs)


def _raise_http_from_exception(
    exc: Exception,
    *,
    user_log_message: str,
    server_log_message: str,
) -> None:
    """
    Convertit une exception Python en HTTPException cohérente pour l'API.

    Règles :
    - FileNotFoundError -> 503
    - ValueError -> 400
    - autres erreurs -> 500
    """
    if isinstance(exc, FileNotFoundError):
        logger.exception("%s : index indisponible", server_log_message)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if isinstance(exc, ValueError):
        logger.warning("%s : %s", user_log_message, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.exception("%s : erreur interne", server_log_message)
    raise HTTPException(
        status_code=500,
        detail="Erreur interne du serveur.",
    ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialise le service RAG au démarrage de l'application
    puis journalise l'arrêt propre de l'application.
    """
    try:
        _initialize_rag_service()
    except Exception:
        logger.exception("Erreur lors de l'initialisation du service RAG.")

    yield

    logger.info("Arrêt de l'application RAG.")


app = FastAPI(
    title="OpenAgenda RAG API",
    description=(
        "API REST locale pour interroger un assistant RAG "
        "de recommandation d'événements culturels"
    ),
    version="1.2.0",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """
    Redirige vers la documentation interactive de l'API.
    """
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """
    Retourne l'état minimal de l'API.
    """
    return HealthResponse(
        status="ok",
        index_loaded=rag_service.is_index_loaded(),
    )


@app.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_api_key)],
)
def ask(payload: AskRequest) -> AskResponse:
    """
    Exécute le pipeline RAG standard pour répondre à une question.

    La requête peut inclure un `session_id` afin de conserver
    une mémoire conversationnelle courte sur la session en cours.
    """
    try:
        return rag_service.ask(
            question=payload.question,
            session_id=payload.session_id,
        )
    except Exception as exc:
        _raise_http_from_exception(
            exc,
            user_log_message="Erreur utilisateur /ask",
            server_log_message="Erreur serveur /ask",
        )


@app.post(
    "/ask/debug",
    response_model=DebugResponse,
    dependencies=[Depends(require_api_key)],
)
def ask_debug(payload: AskRequest) -> DebugResponse:
    """
    Exécute le pipeline RAG avec retour détaillé pour le debug.

    La requête peut inclure un `session_id` afin d'inspecter
    la mémoire conversationnelle courte associée.
    """
    try:
        debug_data = rag_service.ask_debug(
            question=payload.question,
            session_id=payload.session_id,
        )
        return DebugResponse(**debug_data)
    except Exception as exc:
        _raise_http_from_exception(
            exc,
            user_log_message="Erreur utilisateur /ask/debug",
            server_log_message="Erreur serveur /ask/debug",
        )


@app.post(
    "/rebuild",
    response_model=RebuildResponse,
    dependencies=[Depends(require_api_key)],
)
def rebuild(payload: RebuildRequest) -> RebuildResponse:
    """
    Reconstruit complètement l'index documentaire.
    """
    try:
        zone = payload.zone or DEFAULT_ZONE
        scope = payload.scope or DEFAULT_SCOPE

        logger.info("Rebuild demandé | zone=%s | scope=%s", zone, scope)

        documents = load_documents(zone=zone, scope=scope)

        if not documents:
            raise ValueError("Aucun document trouvé pour cette zone.")

        rag_service.zone = zone
        rag_service.scope = scope
        n_docs = rag_service.rebuild_index(documents)

        return RebuildResponse(
            status="success",
            message=f"Index reconstruit avec {n_docs} documents.",
            n_docs_indexed=n_docs,
        )

    except Exception as exc:
        _raise_http_from_exception(
            exc,
            user_log_message="Erreur validation /rebuild",
            server_log_message="Erreur serveur /rebuild",
        )