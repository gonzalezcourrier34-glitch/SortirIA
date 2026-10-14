"""Configuration d'exécution du workflow LangGraph.

Ce module prépare les dépendances injectées lors de l'exécution du
workflow LangGraph.

Il centralise uniquement l'assemblage des services nécessaires aux
différents nœuds.

Il ne contient aucun paramètre issu du fichier .env. Ceux-ci restent
définis dans app.core.config.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.adapters.embedding_service import EmbeddingService
from app.adapters.lichess_service import LichessService
from app.adapters.llm_service import LLMService
from app.adapters.milvus_service import MilvusService
from app.adapters.mongodb_service import MongoDBService
from app.adapters.stockfish_service import StockfishService
from app.adapters.youtube_service import YoutubeService
from app.services.chess_service import ChessService

# Configuration


def build_workflow_config(
    *,
    thread_id: str,
    chess_service: ChessService,
    stockfish_service: StockfishService,
    lichess_service: LichessService,
    embedding_service: EmbeddingService,
    milvus_service: MilvusService,
    youtube_service: YoutubeService,
    llm_service: LLMService,
    mongodb_service: MongoDBService,
) -> RunnableConfig:
    """Construit la configuration d'exécution du workflow."""

    return {
        "configurable": {
            # LangGraph
            "thread_id": thread_id,
            # Validation
            "chess_service": chess_service,
            # Détection d'ouverture
            "lichess_service": lichess_service,
            # Analyse moteur
            "stockfish_service": stockfish_service,
            # Recherche documentaire
            "embedding_service": embedding_service,
            "milvus_service": milvus_service,
            # Recherche vidéo
            "youtube_service": youtube_service,
            # Génération
            "llm": llm_service,
            # Persistance
            "mongodb_service": mongodb_service,
        }
    }
