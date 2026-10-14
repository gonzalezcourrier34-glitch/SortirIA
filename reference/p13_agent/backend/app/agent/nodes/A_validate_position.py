"""Validation initiale d'une position d'échecs dans LangGraph.

Ce nœud valide la position FEN reçue, puis construit sa représentation
structurée pour les étapes suivantes du workflow.

Il ne modifie jamais directement l'état et ne contient aucune logique
échiquéenne : la validation métier reste déléguée à ``ChessService``.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.constants import ERROR_CONFIGURATION, ERROR_INVALID_FEN, ERROR_UNEXPECTED
from app.core.exceptions import InvalidFenError
from app.core.logging import get_logger
from app.schemas.chess.position import BoardPosition, FenRequest
from app.schemas.common.enums import (
    AnalysisStatus,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowError
from app.services.chess_service import ChessService

logger = get_logger(__name__)


# Configuration

CHESS_SERVICE_KEY = "chess_service"


# Services


def _get_chess_service(config: RunnableConfig) -> ChessService | None:
    """Retourne le service d'échecs configuré avec un type vérifié."""
    service = get_configured_service(
        config, CHESS_SERVICE_KEY, expected_type=ChessService
    )

    if service is None:
        return None

    if not isinstance(service, ChessService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de ChessService.",
            CHESS_SERVICE_KEY,
            type(service).__name__,
        )
        return None

    return service


# Résumés


def _build_position_summary(position: BoardPosition) -> str:
    """Construit un résumé factuel de la position validée."""
    return (
        "La position FEN est valide au coup "
        f"{position.fullmove_number} et peut être analysée."
    )


# Mises à jour


def _build_error_update(state: ChessAnalysisState, error: WorkflowError) -> StateUpdate:
    """Construit la mise à jour retournée après un échec."""
    return {
        "status": AnalysisStatus.FAILED,
        "current_step": WorkflowStep.VALIDATE_POSITION,
        # Une étape échouée n'est jamais ajoutée aux étapes terminées.
        "completed_steps": list(state.completed_steps),
        "errors": [*state.errors, error],
        "warnings": list(state.warnings),
    }


def _build_success_update(
    state: ChessAnalysisState, position: BoardPosition
) -> StateUpdate:
    """Construit la mise à jour après une validation réussie."""
    current_step = WorkflowStep.VALIDATE_POSITION
    workflow_context = state.workflow_context.model_copy(
        update={"position_summary": _build_position_summary(position)}
    )

    return {
        # La transition du statut global relève du workflow complet.
        "status": state.status,
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "position": position,
        "workflow_context": workflow_context,
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


def _build_missing_service_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour lorsque ChessService est indisponible."""
    message = "ChessService est absent ou invalide dans la configuration LangGraph."

    logger.error(message)

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.VALIDATE_POSITION,
            code=ERROR_CONFIGURATION,
            message=message,
            recoverable=False,
        ),
    )


def _build_invalid_fen_update(
    state: ChessAnalysisState, error: InvalidFenError | ValidationError
) -> StateUpdate:
    """Construit la mise à jour retournée pour une FEN invalide."""
    logger.warning("Position FEN invalide.")

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.VALIDATE_POSITION,
            code=ERROR_INVALID_FEN,
            message=str(error),
            recoverable=False,
        ),
    )


def _build_unexpected_error_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour retournée après une erreur inattendue."""
    message = "Une erreur inattendue a empêché la validation de la position."

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.VALIDATE_POSITION,
            code=ERROR_UNEXPECTED,
            message=message,
            recoverable=False,
        ),
    )


# Validation


async def validate_position(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Valide la position FEN reçue par le workflow."""
    logger.debug("Validation de la position FEN.")

    chess_service = _get_chess_service(config)

    if chess_service is None:
        emit_progress(
            step=WorkflowStep.VALIDATE_POSITION,
            service=ServiceType.CHESS,
            status=WorkflowStepStatus.FAILED,
            message="ChessService indisponible.",
        )

        return _build_missing_service_update(state)

    try:
        request = FenRequest(fen=state.fen)

        emit_progress(
            step=WorkflowStep.VALIDATE_POSITION,
            service=ServiceType.CHESS,
            status=WorkflowStepStatus.RUNNING,
            message="Validation de la position en cours.",
        )

        position = chess_service.get_position(request)

        emit_progress(
            step=WorkflowStep.VALIDATE_POSITION,
            service=ServiceType.CHESS,
            status=WorkflowStepStatus.COMPLETED,
            message="Position validée.",
        )

    except (InvalidFenError, ValidationError) as error:
        emit_progress(
            step=WorkflowStep.VALIDATE_POSITION,
            service=ServiceType.CHESS,
            status=WorkflowStepStatus.FAILED,
            message="Position FEN invalide.",
        )

        return _build_invalid_fen_update(state, error)

    except Exception:
        emit_progress(
            step=WorkflowStep.VALIDATE_POSITION,
            service=ServiceType.CHESS,
            status=WorkflowStepStatus.FAILED,
            message=(
                "Une erreur inattendue a interrompu la validation de la position."
            ),
        )

        logger.exception("Erreur inattendue lors de la validation de la position FEN.")

        return _build_unexpected_error_update(state)

    logger.info("Position FEN validée.")

    return _build_success_update(state, position)
