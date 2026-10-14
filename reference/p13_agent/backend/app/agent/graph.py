"""Construction du workflow LangGraph d'analyse échiquéenne.

Ce module centralise :

- l'enregistrement explicite des nœuds ;
- les transitions fixes et conditionnelles ;
- la compilation du graphe ;
- la préparation de sa configuration d'exécution.

Il ne contient aucune logique métier. Les services sont injectés aux nœuds
par la section ``configurable`` du ``RunnableConfig``.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.adapters.embedding_service import EmbeddingService
from app.adapters.lichess_service import LichessService
from app.adapters.llm_service import LLMService
from app.adapters.milvus_service import MilvusService
from app.adapters.mongodb_service import MongoDBService
from app.adapters.stockfish_service import StockfishService
from app.adapters.youtube_service import YoutubeService
from app.agent.nodes.A_validate_position import validate_position
from app.agent.nodes.B_detect_theory import detect_theory
from app.agent.nodes.C_engine_analysis import engine_analysis
from app.agent.nodes.D_unknown_position_analysis import unknown_position_analysis
from app.agent.nodes.E_retrieve_context import retrieve_context
from app.agent.nodes.F_retrieve_videos import retrieve_videos
from app.agent.nodes.G_generate_response import generate_response
from app.agent.nodes.H_save_analysis import save_analysis
from app.agent.routing import (
    route_after_context,
    route_after_engine_analysis,
    route_after_response,
    route_after_theory_detection,
    route_after_unknown_position_analysis,
    route_after_validation,
    route_after_videos,
)
from app.agent.state import ChessAnalysisState
from app.services.chess_service import ChessService
from app.services.vector_search_service import VectorSearchService

# Types

type GraphBuilder = StateGraph[
    ChessAnalysisState, None, ChessAnalysisState, ChessAnalysisState
]

type ChessAnalysisGraph = CompiledStateGraph[
    ChessAnalysisState, None, ChessAnalysisState, ChessAnalysisState
]

type RouteMap = dict[Hashable, str]


class GraphConfigurable(TypedDict):
    """Décrit les services injectés dans la configuration LangGraph."""

    chess_service: ChessService
    stockfish_service: StockfishService
    lichess_service: LichessService
    embedding_service: EmbeddingService
    milvus_service: MilvusService
    vector_search_service: VectorSearchService
    youtube_service: YoutubeService
    llm: LLMService
    mongodb_service: MongoDBService


# Nœuds

VALIDATE_POSITION_NODE = "validate_position"
DETECT_THEORY_NODE = "detect_theory"
ENGINE_ANALYSIS_NODE = "engine_analysis"
UNKNOWN_POSITION_ANALYSIS_NODE = "unknown_position_analysis"
RETRIEVE_CONTEXT_NODE = "retrieve_context"
RETRIEVE_VIDEOS_NODE = "retrieve_videos"
GENERATE_RESPONSE_NODE = "generate_response"
SAVE_ANALYSIS_NODE = "save_analysis"


# Routes

END_ROUTE = "end"


# Dépendances


@dataclass(frozen=True, slots=True)
class GraphDependencies:
    """Regroupe les services utilisés par le workflow."""

    chess_service: ChessService
    stockfish_service: StockfishService
    lichess_service: LichessService
    embedding_service: EmbeddingService
    milvus_service: MilvusService
    vector_search_service: VectorSearchService
    llm_service: LLMService
    youtube_service: YoutubeService
    mongodb_service: MongoDBService


# Utilitaires


def _build_route_map(*destinations: str) -> RouteMap:
    """Construit une table de routes terminée par la sortie du graphe."""
    route_map: RouteMap = {}

    for destination in destinations:
        route_map[destination] = destination

    route_map[END_ROUTE] = END
    return route_map


# Enregistrement


def _register_nodes(graph_builder: GraphBuilder) -> None:
    """Enregistre tous les nœuds du workflow."""
    graph_builder.add_node(VALIDATE_POSITION_NODE, validate_position)
    graph_builder.add_node(DETECT_THEORY_NODE, detect_theory)
    graph_builder.add_node(ENGINE_ANALYSIS_NODE, engine_analysis)
    graph_builder.add_node(UNKNOWN_POSITION_ANALYSIS_NODE, unknown_position_analysis)
    graph_builder.add_node(RETRIEVE_CONTEXT_NODE, retrieve_context)
    graph_builder.add_node(RETRIEVE_VIDEOS_NODE, retrieve_videos)
    graph_builder.add_node(GENERATE_RESPONSE_NODE, generate_response)
    graph_builder.add_node(SAVE_ANALYSIS_NODE, save_analysis)


def _register_edges(graph_builder: GraphBuilder) -> None:
    """Enregistre les transitions fixes et conditionnelles du workflow."""
    graph_builder.add_edge(START, VALIDATE_POSITION_NODE)

    graph_builder.add_conditional_edges(
        VALIDATE_POSITION_NODE,
        route_after_validation,
        _build_route_map(
            DETECT_THEORY_NODE,
            ENGINE_ANALYSIS_NODE,
            RETRIEVE_CONTEXT_NODE,
            RETRIEVE_VIDEOS_NODE,
            GENERATE_RESPONSE_NODE,
            SAVE_ANALYSIS_NODE,
        ),
    )
    graph_builder.add_conditional_edges(
        DETECT_THEORY_NODE,
        route_after_theory_detection,
        _build_route_map(
            ENGINE_ANALYSIS_NODE,
            RETRIEVE_CONTEXT_NODE,
            RETRIEVE_VIDEOS_NODE,
            GENERATE_RESPONSE_NODE,
            SAVE_ANALYSIS_NODE,
        ),
    )
    graph_builder.add_conditional_edges(
        ENGINE_ANALYSIS_NODE,
        route_after_engine_analysis,
        _build_route_map(
            UNKNOWN_POSITION_ANALYSIS_NODE,
            RETRIEVE_CONTEXT_NODE,
            RETRIEVE_VIDEOS_NODE,
            GENERATE_RESPONSE_NODE,
            SAVE_ANALYSIS_NODE,
        ),
    )
    graph_builder.add_conditional_edges(
        UNKNOWN_POSITION_ANALYSIS_NODE,
        route_after_unknown_position_analysis,
        _build_route_map(GENERATE_RESPONSE_NODE, SAVE_ANALYSIS_NODE),
    )
    graph_builder.add_conditional_edges(
        RETRIEVE_CONTEXT_NODE,
        route_after_context,
        _build_route_map(
            RETRIEVE_VIDEOS_NODE, GENERATE_RESPONSE_NODE, SAVE_ANALYSIS_NODE
        ),
    )
    graph_builder.add_conditional_edges(
        RETRIEVE_VIDEOS_NODE,
        route_after_videos,
        _build_route_map(GENERATE_RESPONSE_NODE, SAVE_ANALYSIS_NODE),
    )
    graph_builder.add_conditional_edges(
        GENERATE_RESPONSE_NODE,
        route_after_response,
        _build_route_map(SAVE_ANALYSIS_NODE),
    )

    graph_builder.add_edge(SAVE_ANALYSIS_NODE, END)


# Construction


def build_graph() -> ChessAnalysisGraph:
    """Construit et compile le workflow LangGraph."""
    graph_builder: GraphBuilder = StateGraph(ChessAnalysisState)
    _register_nodes(graph_builder)
    _register_edges(graph_builder)
    return graph_builder.compile()


def build_graph_config(dependencies: GraphDependencies) -> RunnableConfig:
    """Construit la configuration d'exécution du workflow."""
    configurable: GraphConfigurable = {
        "chess_service": dependencies.chess_service,
        "stockfish_service": dependencies.stockfish_service,
        "lichess_service": dependencies.lichess_service,
        "embedding_service": dependencies.embedding_service,
        "milvus_service": dependencies.milvus_service,
        "vector_search_service": dependencies.vector_search_service,
        "youtube_service": dependencies.youtube_service,
        "llm": dependencies.llm_service,
        "mongodb_service": dependencies.mongodb_service,
    }

    return {"configurable": dict(configurable)}
