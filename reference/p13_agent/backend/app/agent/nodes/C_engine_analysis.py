"""Analyse Stockfish d'une position dans le workflow LangGraph.

Ce nœud délègue l'analyse moteur à ``StockfishService``, enrichit le résultat
avec des synthèses factuelles, puis retourne une mise à jour partielle de
l'état partagé.

Il ne communique pas directement avec le moteur UCI et ne modifie jamais
l'état reçu.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.adapters.stockfish_service import StockfishService
from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.constants import ERROR_CONFIGURATION, ERROR_UNEXPECTED
from app.core.exceptions import StockfishError
from app.core.logging import get_logger
from app.schemas.analysis.evaluation import PositionEvaluation
from app.schemas.chess.position import FenRequest
from app.schemas.common.enums import (
    AnalysisStatus,
    EvaluationType,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowError

logger = get_logger(__name__)


# Configuration

STOCKFISH_SERVICE_KEY = "stockfish_service"


# Services


def _get_stockfish_service(config: RunnableConfig) -> StockfishService | None:
    """Retourne le service Stockfish configuré avec un type vérifié."""
    service = get_configured_service(
        config, STOCKFISH_SERVICE_KEY, expected_type=StockfishService
    )

    if service is None:
        return None

    if not isinstance(service, StockfishService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de StockfishService.",
            STOCKFISH_SERVICE_KEY,
            type(service).__name__,
        )
        return None

    return service


# Statuts


def _get_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut applicable après une analyse réussie."""
    # Une réussite locale ne doit pas masquer une dégradation antérieure.
    if state.status is AnalysisStatus.PARTIAL_SUCCESS:
        return AnalysisStatus.PARTIAL_SUCCESS

    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.SUCCESS


# Résumés


def _format_score(score: float, evaluation_type: EvaluationType) -> str:
    """Formate un score Stockfish sans ajouter d'interprétation."""
    if evaluation_type is EvaluationType.MATE:
        return f"mat en {int(score)}"

    return f"{score:.0f} centipions"


def _build_engine_summary(evaluation: PositionEvaluation) -> str:
    """Construit une synthèse factuelle de l'analyse Stockfish."""
    engine = evaluation.engine
    best_move = engine.best_move
    engine_evaluation = engine.evaluation
    principal_variation = engine.principal_variation
    formatted_engine_score = _format_score(
        engine_evaluation.score, engine_evaluation.evaluation_type
    )

    sections = [
        (f"Évaluation retournée : {formatted_engine_score}."),
        f"Profondeur d'analyse : {engine_evaluation.depth}.",
    ]

    if best_move is not None:
        sections.insert(
            0,
            (
                "Meilleur coup retourné par Stockfish : "
                f"{best_move.san} ({best_move.uci})."
            ),
        )

    if engine_evaluation.nodes is not None:
        sections.append(f"Nœuds analysés : {engine_evaluation.nodes}.")

    if engine_evaluation.time_ms is not None:
        sections.append(f"Temps d'analyse : {engine_evaluation.time_ms} ms.")

    if principal_variation.moves:
        moves = " ".join(principal_variation.moves)
        sections.append(f"Variante principale calculée : {moves}.")

    if engine.alternatives:
        alternatives = ", ".join(
            (
                f"{alternative.san} "
                f"({_format_score(alternative.score, alternative.evaluation_type)})"
            )
            for alternative in engine.alternatives
        )
        sections.append(f"Alternatives retournées : {alternatives}.")

    return " ".join(sections)


def _build_principal_variation_summary(evaluation: PositionEvaluation) -> str | None:
    """Décrit factuellement la variante principale calculée."""
    principal_variation = evaluation.engine.principal_variation

    if not principal_variation.moves:
        return None

    moves = " ".join(principal_variation.moves)
    variation_evaluation = principal_variation.evaluation
    formatted_score = _format_score(
        variation_evaluation.score, variation_evaluation.evaluation_type
    )

    return (
        "Variante principale calculée par Stockfish "
        f"à la profondeur {variation_evaluation.depth} : {moves}. "
        f"Évaluation associée : {formatted_score}."
    )


def _enrich_evaluation(evaluation: PositionEvaluation) -> PositionEvaluation:
    """Ajoute les descriptions factuelles à l'évaluation."""
    principal_variation = evaluation.engine.principal_variation.model_copy(
        update={
            "explanation": _build_principal_variation_summary(evaluation),
        }
    )
    engine = evaluation.engine.model_copy(
        update={"principal_variation": principal_variation}
    )
    enriched_evaluation = evaluation.model_copy(update={"engine": engine})

    return enriched_evaluation.model_copy(
        update={"summary": _build_engine_summary(enriched_evaluation)}
    )


# Mises à jour


def _build_error_update(state: ChessAnalysisState, error: WorkflowError) -> StateUpdate:
    """Construit la mise à jour retournée après un échec."""
    return {
        "status": AnalysisStatus.FAILED,
        "current_step": WorkflowStep.ENGINE_ANALYSIS,
        # Une étape échouée n'est jamais ajoutée aux étapes terminées.
        "completed_steps": list(state.completed_steps),
        "errors": [*state.errors, error],
        "warnings": list(state.warnings),
    }


def _build_success_update(
    state: ChessAnalysisState, evaluation: PositionEvaluation
) -> StateUpdate:
    """Construit la mise à jour après une analyse réussie."""
    current_step = WorkflowStep.ENGINE_ANALYSIS
    engine_context = evaluation.summary or ("Aucune synthèse moteur n'est disponible.")
    workflow_context = state.workflow_context.model_copy(
        update={"engine_context": engine_context}
    )

    return {
        "status": _get_success_status(state),
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "evaluation": evaluation,
        "workflow_context": workflow_context,
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


def _build_missing_service_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour lorsque StockfishService est indisponible."""
    message = "StockfishService est absent ou invalide dans la configuration LangGraph."

    logger.error(message)

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.ENGINE_ANALYSIS,
            code=ERROR_CONFIGURATION,
            message=message,
            recoverable=False,
        ),
    )


def _build_stockfish_error_update(
    state: ChessAnalysisState, error: StockfishError
) -> StateUpdate:
    """Construit la mise à jour après une erreur Stockfish connue."""
    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.ENGINE_ANALYSIS,
            code=error.code,
            message=str(error),
            recoverable=error.retryable,
        ),
    )


def _build_unexpected_error_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour après une erreur inattendue."""
    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.ENGINE_ANALYSIS,
            code=ERROR_UNEXPECTED,
            message=("Une erreur inattendue a empêché l'analyse de la position."),
            recoverable=False,
        ),
    )


# API publique


async def engine_analysis(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Analyse une position avec Stockfish."""
    logger.debug("Démarrage de l'analyse Stockfish.")

    stockfish_service = _get_stockfish_service(config)

    if stockfish_service is None:
        emit_progress(
            step=WorkflowStep.ENGINE_ANALYSIS,
            service=ServiceType.STOCKFISH,
            status=WorkflowStepStatus.FAILED,
            message="StockfishService indisponible.",
        )

        return _build_missing_service_update(state)

    try:
        request = FenRequest(fen=state.fen)

        emit_progress(
            step=WorkflowStep.ENGINE_ANALYSIS,
            service=ServiceType.STOCKFISH,
            status=WorkflowStepStatus.RUNNING,
            message="Analyse Stockfish en cours.",
        )

        evaluation = await stockfish_service.analyze_position(request)

        emit_progress(
            step=WorkflowStep.ENGINE_ANALYSIS,
            service=ServiceType.STOCKFISH,
            status=WorkflowStepStatus.COMPLETED,
            message="Analyse Stockfish terminée.",
        )

        enriched_evaluation = _enrich_evaluation(evaluation)

        logger.info(
            "Analyse Stockfish terminée à la profondeur %s.",
            enriched_evaluation.engine.evaluation.depth,
        )

    except StockfishError as error:
        emit_progress(
            step=WorkflowStep.ENGINE_ANALYSIS,
            service=ServiceType.STOCKFISH,
            status=WorkflowStepStatus.FAILED,
            message="Analyse Stockfish impossible.",
        )

        logger.warning("Analyse Stockfish impossible : %s", error)

        return _build_stockfish_error_update(state, error)

    except Exception:
        emit_progress(
            step=WorkflowStep.ENGINE_ANALYSIS,
            service=ServiceType.STOCKFISH,
            status=WorkflowStepStatus.FAILED,
            message=("Une erreur inattendue a interrompu l'analyse Stockfish."),
        )

        logger.exception("Erreur inattendue durant l'analyse Stockfish.")

        return _build_unexpected_error_update(state)

    return _build_success_update(state, enriched_evaluation)
