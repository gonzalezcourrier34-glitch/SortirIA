"""Utilitaires communs du workflow LangGraph."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.agent.state import ChessAnalysisState
from app.schemas.common.enums import WorkflowStep


def get_configured_service[ServiceType](
    config: RunnableConfig, key: str, expected_type: type[ServiceType]
) -> ServiceType | None:
    """Retourne un service configuré après vérification de son type."""
    configurable = config.get("configurable", {})
    service = configurable.get(key)

    if service is None:
        return None

    if not isinstance(service, expected_type):
        raise TypeError(
            f"La dépendance {key!r} doit être de type "
            f"{expected_type.__name__}, et non "
            f"{type(service).__name__}."
        )

    return service


def append_completed_step(
    state: ChessAnalysisState, step: WorkflowStep
) -> list[WorkflowStep]:
    """Ajoute une étape terminée sans créer de doublon."""
    completed_steps = list(state.completed_steps)

    if step not in completed_steps:
        completed_steps.append(step)

    return completed_steps
