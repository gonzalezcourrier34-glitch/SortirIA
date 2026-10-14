"""Émission des événements de progression du workflow.

Ce module fournit une abstraction légère permettant aux nœuds
LangGraph de signaler l'activité réelle des services utilisés.

Il ne dépend ni de FastAPI ni du transport HTTP.
"""

from __future__ import annotations

from langgraph.config import get_stream_writer

from app.schemas.common.enums import (
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)

# Émission


def emit_progress(
    *,
    step: WorkflowStep,
    service: ServiceType | None,
    status: WorkflowStepStatus,
    message: str | None = None,
) -> None:
    """Émet un événement de progression LangGraph."""

    writer = get_stream_writer()

    writer(
        {
            "step": step.value,
            "service": (service.value if service is not None else None),
            "status": status.value,
            "message": message,
        }
    )
