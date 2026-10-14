"""Routage conditionnel du workflow LangGraph.

Ce module détermine les prochaines étapes du workflow selon :

- les options d'exécution ;
- la présence d'une ouverture connue ;
- le statut global de l'analyse.

Il ne contient aucune logique métier.

Il n'appelle directement aucun service externe.

Le workflow distingue deux branches après l'analyse moteur :

- une ouverture connue est enrichie par le contexte documentaire
  et les vidéos ;
- une position inconnue reçoit une analyse pédagogique spécifique
  avant la génération de la réponse finale.
"""

from __future__ import annotations

from typing import Literal

from app.agent.state import ChessAnalysisState
from app.schemas.common.enums import AnalysisStatus

# Types

ValidationRoute = Literal[
    "detect_theory",
    "engine_analysis",
    "retrieve_context",
    "retrieve_videos",
    "generate_response",
    "save_analysis",
    "end",
]

TheoryDetectionRoute = Literal[
    "engine_analysis",
    "retrieve_context",
    "retrieve_videos",
    "generate_response",
    "save_analysis",
    "end",
]

EngineRoute = Literal[
    "unknown_position_analysis",
    "retrieve_context",
    "retrieve_videos",
    "generate_response",
    "save_analysis",
    "end",
]

UnknownPositionRoute = Literal["generate_response", "save_analysis", "end"]

ContextRoute = Literal["retrieve_videos", "generate_response", "save_analysis", "end"]

VideosRoute = Literal["generate_response", "save_analysis", "end"]

ResponseRoute = Literal["save_analysis", "end"]


# Vérification


def _workflow_has_failed(state: ChessAnalysisState) -> bool:
    """Indique si le workflow est interrompu."""

    return state.status is AnalysisStatus.FAILED


# Étapes finales


def _route_to_output(
    state: ChessAnalysisState,
) -> Literal["generate_response", "save_analysis", "end"]:
    """Choisit la prochaine étape de sortie."""

    options = state.options

    if options.generate_response:
        return "generate_response"

    if options.save_analysis:
        return "save_analysis"

    return "end"


# Enrichissements


def _route_known_opening(
    state: ChessAnalysisState,
) -> Literal[
    "retrieve_context", "retrieve_videos", "generate_response", "save_analysis", "end"
]:
    """Choisit le prochain enrichissement d'une ouverture connue."""

    options = state.options

    if options.include_context:
        return "retrieve_context"

    if options.include_videos:
        return "retrieve_videos"

    return _route_to_output(state)


def _route_after_context_retrieval(
    state: ChessAnalysisState,
) -> Literal["retrieve_videos", "generate_response", "save_analysis", "end"]:
    """Choisit la suite après la recherche documentaire."""

    if state.options.include_videos:
        return "retrieve_videos"

    return _route_to_output(state)


# Validation


def route_after_validation(state: ChessAnalysisState) -> ValidationRoute:
    """Choisit l'étape suivant la validation."""

    if _workflow_has_failed(state):
        return "end"

    options = state.options

    if options.include_opening:
        return "detect_theory"

    if options.include_stockfish:
        return "engine_analysis"

    return _route_to_output(state)


# Détection


def route_after_theory_detection(state: ChessAnalysisState) -> TheoryDetectionRoute:
    """Choisit l'étape suivant la détection d'ouverture."""

    if _workflow_has_failed(state):
        return "end"

    if state.options.include_stockfish:
        return "engine_analysis"

    if state.opening is not None:
        return _route_known_opening(state)

    return _route_to_output(state)


# Analyse moteur


def route_after_engine_analysis(state: ChessAnalysisState) -> EngineRoute:
    """Choisit la branche suivant l'analyse moteur."""

    if _workflow_has_failed(state):
        return "end"

    options = state.options

    if not options.include_opening:
        return _route_to_output(state)

    if state.opening is None:
        return "unknown_position_analysis"

    return _route_known_opening(state)


# Position inconnue


def route_after_unknown_position_analysis(
    state: ChessAnalysisState,
) -> UnknownPositionRoute:
    """Choisit l'étape suivant l'analyse d'une position inconnue."""

    if _workflow_has_failed(state):
        return "end"

    return _route_to_output(state)


# Contexte


def route_after_context(state: ChessAnalysisState) -> ContextRoute:
    """Choisit l'étape suivant la recherche documentaire."""

    if _workflow_has_failed(state):
        return "end"

    return _route_after_context_retrieval(state)


# Vidéos


def route_after_videos(state: ChessAnalysisState) -> VideosRoute:
    """Choisit l'étape suivant la recherche de vidéos."""

    if _workflow_has_failed(state):
        return "end"

    return _route_to_output(state)


# Réponse


def route_after_response(state: ChessAnalysisState) -> ResponseRoute:
    """Choisit l'étape suivant la génération de la réponse."""

    if _workflow_has_failed(state):
        return "end"

    if state.options.save_analysis:
        return "save_analysis"

    return "end"
