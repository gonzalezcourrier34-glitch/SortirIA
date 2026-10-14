"""Nœud d'analyse pédagogique d'une position inconnue.

Ce module prépare un contexte pédagogique lorsqu'aucune ouverture connue
n'a été détectée par Lichess. Il exploite exclusivement l'évaluation déjà
produite par Stockfish et ne réalise aucun appel réseau.

Le nœud transforme l'état courant en une mise à jour partielle destinée à
être fusionnée par LangGraph. Il ne calcule aucun coup et ne réinterprète
pas les résultats du moteur.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.agent.state import ChessAnalysisState, StateUpdate, WorkflowContext
from app.agent.utils.workflow_utils import append_completed_step
from app.core.logging import get_logger
from app.schemas.analysis.evaluation import PositionEvaluation
from app.schemas.common.enums import AnalysisStatus, EvaluationType, WorkflowStep

logger = get_logger(__name__)


# Statuts


def _get_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut après une préparation réussie."""
    # Une réussite locale ne doit pas masquer une dégradation ou un
    # échec enregistré par une étape précédente.
    if state.status is AnalysisStatus.PARTIAL_SUCCESS:
        return AnalysisStatus.PARTIAL_SUCCESS

    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.SUCCESS


# Formatage


def _format_score(score: float, evaluation_type: EvaluationType) -> str:
    """Formate un score Stockfish sans l'interpréter."""
    if evaluation_type is EvaluationType.MATE:
        return f"mat en {int(score)}"

    return f"{score:.0f} centipions"


def _format_moves(moves: list[str]) -> str | None:
    """Formate une suite de coups lorsqu'elle est disponible."""
    normalized_moves = [move.strip() for move in moves if move.strip()]

    if not normalized_moves:
        return None

    return " ".join(normalized_moves)


# Construction du contexte


def _append_best_move(sections: list[str], evaluation: PositionEvaluation) -> None:
    """Ajoute le meilleur coup calculé par Stockfish."""
    best_move = evaluation.engine.best_move

    if best_move is None:
        sections.append("Meilleur coup calculé par Stockfish :\n- Non disponible.")
        return

    formatted_score = _format_score(best_move.score, best_move.evaluation_type)
    sections.append(
        "Meilleur coup calculé par Stockfish :\n"
        f"- SAN : {best_move.san}\n"
        f"- UCI : {best_move.uci}\n"
        f"- Score : {formatted_score}\n"
        f"- Profondeur : {best_move.depth}"
    )


def _append_evaluation(sections: list[str], evaluation: PositionEvaluation) -> None:
    """Ajoute l'évaluation globale calculée par Stockfish."""
    engine_evaluation = evaluation.engine.evaluation

    if engine_evaluation is None:
        sections.append("Évaluation moteur :\n- Non disponible.")
        return

    formatted_score = _format_score(
        engine_evaluation.score, engine_evaluation.evaluation_type
    )
    lines = [
        f"- Score : {formatted_score}",
        f"- Profondeur : {engine_evaluation.depth}",
    ]

    if engine_evaluation.nodes is not None:
        lines.append(f"- Nœuds analysés : {engine_evaluation.nodes}")

    if engine_evaluation.time_ms is not None:
        lines.append(f"- Temps d'analyse : {engine_evaluation.time_ms} ms")

    sections.append("Évaluation moteur :\n" + "\n".join(lines))


def _append_principal_variation(
    sections: list[str], evaluation: PositionEvaluation
) -> None:
    """Ajoute la variante principale calculée."""
    principal_variation = evaluation.engine.principal_variation
    moves = _format_moves(principal_variation.moves)

    if moves is None:
        return

    variation_evaluation = principal_variation.evaluation
    formatted_score = _format_score(
        variation_evaluation.score, variation_evaluation.evaluation_type
    )
    lines = [
        f"- Coups : {moves}",
        f"- Score : {formatted_score}",
        f"- Profondeur : {variation_evaluation.depth}",
    ]

    if principal_variation.explanation:
        lines.append(f"- Description : {principal_variation.explanation}")

    sections.append("Variante principale :\n" + "\n".join(lines))


def _append_alternatives(sections: list[str], evaluation: PositionEvaluation) -> None:
    """Ajoute les alternatives calculées par Stockfish."""
    alternatives = evaluation.engine.alternatives

    if not alternatives:
        return

    values: list[str] = []

    for alternative in alternatives:
        formatted_score = _format_score(alternative.score, alternative.evaluation_type)
        lines = [
            f"- {alternative.san} ({alternative.uci})",
            f"  Score : {formatted_score}",
            f"  Profondeur : {alternative.depth}",
        ]
        moves = _format_moves(alternative.principal_variation)

        if moves is not None:
            lines.append(f"  Variante : {moves}")

        values.append("\n".join(lines))

    sections.append("Alternatives calculées :\n" + "\n\n".join(values))


def _append_summary(sections: list[str], evaluation: PositionEvaluation) -> None:
    """Ajoute la synthèse moteur lorsqu'elle est disponible."""
    if not evaluation.summary:
        return

    sections.append(f"Synthèse moteur :\n{evaluation.summary}")


def _build_unknown_position_context(evaluation: PositionEvaluation) -> str:
    """Construit le contexte pédagogique de la position inconnue."""
    sections = [
        ("La position ne correspond à aucune ouverture connue retournée par Lichess.")
    ]

    _append_best_move(sections, evaluation)
    _append_evaluation(sections, evaluation)
    _append_principal_variation(sections, evaluation)
    _append_alternatives(sections, evaluation)
    _append_summary(sections, evaluation)

    return "\n\n".join(sections)


# Contexte du workflow


def _build_workflow_context(
    state: ChessAnalysisState, unknown_position_context: str
) -> WorkflowContext:
    """Construit le contexte pédagogique de la position inconnue."""
    return state.workflow_context.model_copy(
        update={"unknown_position_context": unknown_position_context}
    )


# Construction des mises à jour


def _build_success_update(
    state: ChessAnalysisState, unknown_position_context: str
) -> StateUpdate:
    """Construit la mise à jour après une préparation réussie."""
    current_step = WorkflowStep.UNKNOWN_POSITION_ANALYSIS

    return {
        "status": _get_success_status(state),
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "workflow_context": _build_workflow_context(state, unknown_position_context),
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


# Analyse


async def unknown_position_analysis(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Prépare l'analyse pédagogique d'une position inconnue."""
    del config

    logger.debug("Préparation de l'analyse d'une position inconnue.")

    # Le routage réserve ce nœud aux positions sans ouverture connue.
    if state.opening is not None:
        raise RuntimeError(
            "Le nœud unknown_position_analysis doit être exécuté "
            "uniquement lorsqu'aucune ouverture n'a été détectée."
        )

    # Une évaluation absente après engine_analysis révèle une incohérence
    # dans le routage du workflow.
    if state.evaluation is None:
        raise RuntimeError(
            "Le nœud unknown_position_analysis nécessite une évaluation Stockfish."
        )

    unknown_position_context = _build_unknown_position_context(state.evaluation)

    logger.info(
        "Analyse pédagogique de la position inconnue préparée avec %s alternative(s).",
        len(state.evaluation.engine.alternatives),
    )

    return _build_success_update(state, unknown_position_context)
