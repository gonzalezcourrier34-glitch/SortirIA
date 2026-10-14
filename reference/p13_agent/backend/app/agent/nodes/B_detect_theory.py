"""Détection d'une ouverture d'échecs dans le workflow LangGraph.

Ce nœud interroge ``LichessService`` afin d'identifier une ouverture connue,
puis enrichit l'état partagé avec ses statistiques, sa théorie et ses
variantes.

Il ne modifie jamais directement l'état et ne contient aucune logique HTTP :
la communication avec Lichess reste déléguée au service applicatif.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.adapters.lichess_service import LichessService
from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.constants import (
    ERROR_CONFIGURATION,
    ERROR_LICHESS_UNAVAILABLE,
    ERROR_OPENING_NOT_FOUND,
    ERROR_UNEXPECTED,
)
from app.core.exceptions import LichessError, OpeningNotFoundError
from app.core.logging import get_logger
from app.schemas.chess.opening import (
    OpeningDetails,
    OpeningStatistics,
    OpeningTheory,
    OpeningVariation,
)
from app.schemas.chess.position import FenRequest
from app.schemas.common.enums import (
    AnalysisStatus,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowError, WorkflowWarning

logger = get_logger(__name__)


# Configuration

LICHESS_SERVICE_KEY = "lichess_service"
MAX_CONTEXT_VARIATIONS = 5


# Services


def _get_lichess_service(config: RunnableConfig) -> LichessService | None:
    """Retourne le service Lichess configuré avec un type vérifié."""
    service = get_configured_service(
        config, LICHESS_SERVICE_KEY, expected_type=LichessService
    )

    if service is None:
        return None

    if not isinstance(service, LichessService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de LichessService.",
            LICHESS_SERVICE_KEY,
            type(service).__name__,
        )
        return None

    return service


# Statuts


def _get_partial_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut dégradé applicable au workflow."""
    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.PARTIAL_SUCCESS


# Résumés


def _build_opening_summary(opening_details: OpeningDetails) -> str:
    """Construit un résumé factuel de l'ouverture détectée."""
    opening = opening_details.opening
    sections = [f"Ouverture : {opening.name}.", f"Code ECO : {opening.eco}."]

    if opening.variation:
        sections.append(f"Variante : {opening.variation}.")

    if opening_details.variations:
        sections.append(
            f"Variantes connues disponibles : {len(opening_details.variations)}."
        )

    return " ".join(sections)


def _build_statistics_context(statistics: OpeningStatistics | None) -> str | None:
    """Construit le contexte des statistiques d'une ouverture."""
    if statistics is None:
        return None

    return (
        "Statistiques globales : "
        f"{statistics.games} parties, "
        f"{statistics.white_win_rate:.1f}% de victoires blanches, "
        f"{statistics.draw_rate:.1f}% de nulles et "
        f"{statistics.black_win_rate:.1f}% de victoires noires."
    )


def _build_theory_context(theory: OpeningTheory | None) -> str | None:
    """Construit le contexte pédagogique d'une ouverture."""
    if theory is None:
        return None

    sections = [f"Présentation théorique : {theory.overview}"]
    theory_sections = (
        ("Idées stratégiques", theory.strategic_ideas),
        ("Motifs tactiques", theory.tactical_patterns),
        ("Plans typiques des Blancs", theory.typical_plans_white),
        ("Plans typiques des Noirs", theory.typical_plans_black),
        ("Erreurs fréquentes", theory.common_mistakes),
    )

    for title, values in theory_sections:
        if values:
            sections.append(f"{title} : {'; '.join(values)}.")

    return "\n".join(sections)


def _build_variations_context(variations: list[OpeningVariation]) -> str | None:
    """Construit le contexte des principales variantes connues."""
    if not variations:
        return None

    variation_lines: list[str] = []

    for variation in variations[:MAX_CONTEXT_VARIATIONS]:
        moves = " ".join(variation.moves) or "suite non précisée"
        variation_lines.append(f"- {variation.name} ({variation.eco}) : {moves}.")

    return "Variantes principales :\n" + "\n".join(variation_lines)


def _build_opening_context(opening_details: OpeningDetails) -> str:
    """Construit le contexte factuel d'une ouverture connue."""
    opening = opening_details.opening
    sections = [f"Ouverture : {opening.name}.", f"Code ECO : {opening.eco}."]

    if opening.variation:
        sections.append(f"Variante : {opening.variation}.")

    if opening.family:
        sections.append(f"Famille : {opening.family}.")

    if opening.moves:
        sections.append(f"Suite principale : {' '.join(opening.moves)}.")

    if opening.description:
        sections.append(f"Description : {opening.description}")

    statistics_context = _build_statistics_context(opening_details.statistics)
    theory_context = _build_theory_context(opening_details.theory)
    variations_context = _build_variations_context(opening_details.variations)

    for context in (statistics_context, theory_context, variations_context):
        if context:
            sections.append(context)

    if theory_context is None:
        sections.append("Aucune théorie pédagogique détaillée n'est disponible.")

    if variations_context is None:
        sections.append("Aucune variante complète n'est disponible.")

    return "\n\n".join(sections)


# Mises à jour


def _build_success_update(
    state: ChessAnalysisState, opening: OpeningDetails
) -> StateUpdate:
    """Construit la mise à jour après une détection réussie."""
    current_step = WorkflowStep.DETECT_THEORY
    workflow_context = state.workflow_context.model_copy(
        update={
            "opening_summary": _build_opening_summary(opening),
            "opening_context": _build_opening_context(opening),
        }
    )

    return {
        "status": state.status,
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "opening": opening,
        "workflow_context": workflow_context,
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


def _build_warning_update(
    state: ChessAnalysisState,
    warning: WorkflowWarning,
    *,
    status: AnalysisStatus | None = None,
) -> StateUpdate:
    """Construit une mise à jour contenant un avertissement."""
    current_step = WorkflowStep.DETECT_THEORY
    workflow_context = state.workflow_context.model_copy(
        update={"opening_summary": None, "opening_context": None}
    )

    return {
        "status": state.status if status is None else status,
        "current_step": current_step,
        # L'enrichissement facultatif est terminé malgré l'avertissement.
        "completed_steps": append_completed_step(state, current_step),
        "opening": None,
        "workflow_context": workflow_context,
        "errors": list(state.errors),
        "warnings": [*state.warnings, warning],
    }


def _build_error_update(state: ChessAnalysisState, error: WorkflowError) -> StateUpdate:
    """Construit la mise à jour après un échec bloquant."""
    workflow_context = state.workflow_context.model_copy(
        update={"opening_summary": None, "opening_context": None}
    )

    return {
        "status": AnalysisStatus.FAILED,
        "current_step": WorkflowStep.DETECT_THEORY,
        # Une étape échouée n'est jamais ajoutée aux étapes terminées.
        "completed_steps": list(state.completed_steps),
        "opening": None,
        "workflow_context": workflow_context,
        "errors": [*state.errors, error],
        "warnings": list(state.warnings),
    }


def _build_missing_service_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour lorsque LichessService est indisponible."""
    message = "LichessService est absent ou invalide dans la configuration LangGraph."

    logger.error(message)

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.DETECT_THEORY,
            code=ERROR_CONFIGURATION,
            message=message,
            recoverable=False,
        ),
    )


def _build_unexpected_error_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour après une erreur inattendue."""
    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.DETECT_THEORY,
            code=ERROR_UNEXPECTED,
            message=("Une erreur inattendue a empêché la détection de l'ouverture."),
            recoverable=False,
        ),
    )


# API publique


async def detect_theory(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Détecte une ouverture connue avec Lichess."""
    current_step = WorkflowStep.DETECT_THEORY

    logger.debug("Recherche d'une ouverture pour la position analysée.")

    lichess_service = _get_lichess_service(config)

    if lichess_service is None:
        emit_progress(
            step=current_step,
            service=ServiceType.LICHESS,
            status=WorkflowStepStatus.FAILED,
            message="LichessService indisponible.",
        )

        return _build_missing_service_update(state)

    try:
        request = FenRequest(fen=state.fen)

        emit_progress(
            step=current_step,
            service=ServiceType.LICHESS,
            status=WorkflowStepStatus.RUNNING,
            message="Recherche de l'ouverture en cours.",
        )

        opening = await lichess_service.detect_opening(request)

        emit_progress(
            step=current_step,
            service=ServiceType.LICHESS,
            status=WorkflowStepStatus.COMPLETED,
            message="Ouverture détectée.",
        )

    except OpeningNotFoundError:
        emit_progress(
            step=current_step,
            service=ServiceType.LICHESS,
            status=WorkflowStepStatus.WARNING,
            message="Aucune ouverture connue détectée.",
        )

        logger.info("Aucune ouverture connue n'a été détectée.")

        return _build_warning_update(
            state,
            WorkflowWarning(
                step=current_step,
                code=ERROR_OPENING_NOT_FOUND,
                message=("La position ne correspond à aucune ouverture connue."),
            ),
        )

    except LichessError as error:
        emit_progress(
            step=current_step,
            service=ServiceType.LICHESS,
            status=WorkflowStepStatus.WARNING,
            message="Service Lichess indisponible.",
        )

        logger.warning("Le service Lichess est indisponible : %s", error)

        return _build_warning_update(
            state,
            WorkflowWarning(
                step=current_step, code=ERROR_LICHESS_UNAVAILABLE, message=str(error)
            ),
            status=_get_partial_success_status(state),
        )

    except Exception:
        emit_progress(
            step=current_step,
            service=ServiceType.LICHESS,
            status=WorkflowStepStatus.FAILED,
            message=("Une erreur inattendue a interrompu la détection de l'ouverture."),
        )

        logger.exception("Erreur inattendue lors de la détection d'une ouverture.")

        return _build_unexpected_error_update(state)

    logger.info("Ouverture détectée : %s.", opening.opening.name)

    return _build_success_update(state, opening)
