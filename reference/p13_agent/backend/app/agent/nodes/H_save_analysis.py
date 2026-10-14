"""Sauvegarde finale d'une analyse produite par le workflow LangGraph.

Ce nœud construit un ``AnalysisRecord`` à partir de l'état final, puis délègue
sa persistance à ``MongoDBService``. La sauvegarde est idempotente grâce au
``request_id`` et une indisponibilité de MongoDB dégrade le résultat sans
supprimer l'analyse déjà produite.

Le module ne recrée aucune donnée métier et ne modifie jamais directement
l'état reçu : il retourne uniquement une mise à jour partielle pour LangGraph.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from app.adapters.mongodb_service import MongoDBService
from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.constants import ERROR_CONFIGURATION, ERROR_UNEXPECTED
from app.core.exceptions import DatabaseError
from app.core.logging import get_logger
from app.schemas.analysis.analysis import AnalysisRecord, AnalysisSaveResult
from app.schemas.common.enums import (
    AnalysisStatus,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowWarning

logger = get_logger(__name__)


# Configuration

MONGODB_SERVICE_KEY = "mongodb_service"
ANALYSIS_IDENTIFIER_NAMESPACE = "chess-agent-analysis"
UNEXPECTED_SAVE_MESSAGE = "Une erreur inattendue a empêché la sauvegarde de l'analyse."


# Types

type JsonObject = dict[str, JsonValue]

JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)


# Services


def _get_mongodb_service(config: RunnableConfig) -> MongoDBService | None:
    """Retourne le service MongoDB configuré avec un type vérifié."""
    service = get_configured_service(
        config, MONGODB_SERVICE_KEY, expected_type=MongoDBService
    )

    if service is None:
        return None

    if not isinstance(service, MongoDBService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de MongoDBService.",
            MONGODB_SERVICE_KEY,
            type(service).__name__,
        )
        return None

    return service


# Statuts


def _get_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut final après une sauvegarde réussie."""
    # Une réussite locale ne doit pas masquer une dégradation antérieure.
    if state.status is AnalysisStatus.PARTIAL_SUCCESS:
        return AnalysisStatus.PARTIAL_SUCCESS

    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.SUCCESS


def _get_partial_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut final après une sauvegarde dégradée."""
    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.PARTIAL_SUCCESS


# Normalisation


def _normalize_optional_text(value: str | None) -> str | None:
    """Retourne une chaîne facultative normalisée."""
    if value is None:
        return None

    normalized_value = " ".join(value.split())
    return normalized_value or None


def _get_request_id(state: ChessAnalysisState) -> str | None:
    """Retourne l'identifiant normalisé de la requête."""
    value = state.metadata.request_id

    if value is None:
        return None

    return _normalize_optional_text(str(value))


# Identifiants


def _build_analysis_id(request_id: str) -> str:
    """Construit un identifiant d'analyse stable."""
    namespaced_request_id = f"{ANALYSIS_IDENTIFIER_NAMESPACE}:{request_id}"
    return str(uuid5(NAMESPACE_URL, namespaced_request_id))


# Dates


def _get_created_at(state: ChessAnalysisState, *, default: datetime) -> datetime:
    """Retourne la première date de création disponible."""
    for field_name in ("created_at", "started_at", "requested_at"):
        value = getattr(state.metadata, field_name, None)

        if isinstance(value, datetime):
            return value

    return default


# Sérialisation


def _serialize_json_object(value: object) -> JsonObject | None:
    """Retourne un objet compatible JSON ou ignore une valeur invalide."""
    if isinstance(value, BaseModel):
        candidate: object = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        candidate = dict(value)
    else:
        return None

    try:
        return JSON_OBJECT_ADAPTER.validate_python(candidate)
    except ValidationError:
        logger.warning(
            "Valeur %s ignorée car elle n'est pas sérialisable en JSON.",
            type(value).__name__,
        )
        return None


def _serialize_model(value: BaseModel | None) -> JsonObject | None:
    """Sérialise un modèle Pydantic facultatif."""
    if value is None:
        return None

    return _serialize_json_object(value)


# Construction


def _build_analysis_record(
    state: ChessAnalysisState, request_id: str
) -> AnalysisRecord:
    """Construit le document complet destiné à MongoDB."""
    saved_at = datetime.now(UTC)

    return AnalysisRecord(
        id=_build_analysis_id(request_id),
        request_id=request_id,
        fen=state.fen,
        moves=list(state.moves),
        question=_normalize_optional_text(state.question),
        response_language=state.options.response_language,
        status=_get_success_status(state),
        position=state.position,
        opening=state.opening,
        evaluation=state.evaluation,
        # Le contexte RAG contient déjà toutes les métadonnées Wikichess.
        retrieval_context=state.retrieval_context,
        videos=list(state.videos),
        response=_normalize_optional_text(state.response),
        engine_analysis=_serialize_model(state.engine_analysis),
        workflow_context=state.workflow_context.model_dump(mode="json"),
        metadata=state.metadata.model_dump(mode="json"),
        current_step=WorkflowStep.SAVE_ANALYSIS,
        completed_steps=append_completed_step(state, WorkflowStep.SAVE_ANALYSIS),
        warnings=list(state.warnings),
        errors=list(state.errors),
        created_at=_get_created_at(state, default=saved_at),
        saved_at=saved_at,
    )


# Mises à jour


def _build_success_update(
    state: ChessAnalysisState, result: AnalysisSaveResult
) -> StateUpdate:
    """Construit la mise à jour après une sauvegarde réussie."""
    current_step = WorkflowStep.SAVE_ANALYSIS

    return {
        "status": _get_success_status(state),
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "analysis_id": result.analysis_id,
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


def _build_warning_update(
    state: ChessAnalysisState, warning: WorkflowWarning
) -> StateUpdate:
    """Construit la mise à jour après un échec de persistance."""
    current_step = WorkflowStep.SAVE_ANALYSIS

    return {
        "status": _get_partial_success_status(state),
        "current_step": current_step,
        # La tentative est terminée pour éviter une boucle sur l'étape finale.
        "completed_steps": append_completed_step(state, current_step),
        "analysis_id": None,
        "errors": list(state.errors),
        "warnings": [*state.warnings, warning],
    }


def _build_configuration_warning_update(
    state: ChessAnalysisState, message: str
) -> StateUpdate:
    """Construit la mise à jour après une configuration invalide."""
    logger.error(message)

    return _build_warning_update(
        state,
        WorkflowWarning(
            step=WorkflowStep.SAVE_ANALYSIS, code=ERROR_CONFIGURATION, message=message
        ),
    )


def _build_missing_service_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour lorsque MongoDB est indisponible."""
    message = (
        "MongoDBService est absent ou invalide dans la configuration "
        "LangGraph. L'analyse n'a pas été sauvegardée."
    )
    return _build_configuration_warning_update(state, message)


def _build_missing_request_id_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour lorsque le request_id est absent."""
    message = (
        "Le request_id est absent des métadonnées du workflow. "
        "L'analyse n'a pas été sauvegardée."
    )
    return _build_configuration_warning_update(state, message)


def _build_database_warning_update(
    state: ChessAnalysisState, error: DatabaseError
) -> StateUpdate:
    """Construit la mise à jour après une erreur MongoDB connue."""
    logger.warning("Sauvegarde MongoDB impossible : %s", error)

    return _build_warning_update(
        state,
        WorkflowWarning(
            step=WorkflowStep.SAVE_ANALYSIS, code=error.code, message=str(error)
        ),
    )


def _build_unexpected_warning_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour après une erreur inattendue."""
    return _build_warning_update(
        state,
        WorkflowWarning(
            step=WorkflowStep.SAVE_ANALYSIS,
            code=ERROR_UNEXPECTED,
            message=UNEXPECTED_SAVE_MESSAGE,
        ),
    )


# API publique


async def save_analysis(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Sauvegarde l'analyse finale dans MongoDB."""
    current_step = WorkflowStep.SAVE_ANALYSIS
    request_id = _get_request_id(state)

    if request_id is None:
        return _build_missing_request_id_update(state)

    logger.info("Sauvegarde de l'analyse du workflow %s.", request_id)

    mongodb_service = _get_mongodb_service(config)

    if mongodb_service is None:
        emit_progress(
            step=current_step,
            service=ServiceType.MONGODB,
            status=WorkflowStepStatus.WARNING,
            message=("MongoDBService indisponible. L'analyse n'a pas été sauvegardée."),
        )

        return _build_missing_service_update(state)

    try:
        analysis = _build_analysis_record(state, request_id)

        emit_progress(
            step=current_step,
            service=ServiceType.MONGODB,
            status=WorkflowStepStatus.RUNNING,
            message="Sauvegarde de l'analyse en cours.",
        )

        result = await mongodb_service.save_analysis(analysis)

        emit_progress(
            step=current_step,
            service=ServiceType.MONGODB,
            status=WorkflowStepStatus.COMPLETED,
            message="Analyse sauvegardée.",
        )

    except DatabaseError as error:
        emit_progress(
            step=current_step,
            service=ServiceType.MONGODB,
            status=WorkflowStepStatus.WARNING,
            message="Sauvegarde MongoDB impossible.",
        )

        return _build_database_warning_update(state, error)

    except Exception:
        emit_progress(
            step=current_step,
            service=ServiceType.MONGODB,
            status=WorkflowStepStatus.WARNING,
            message=("Une erreur inattendue a empêché la sauvegarde de l'analyse."),
        )

        # La persistance reste secondaire : une panne imprévue
        # ne doit pas supprimer l'analyse déjà produite.
        logger.exception("Erreur inattendue durant la sauvegarde de l'analyse.")

        return _build_unexpected_warning_update(state)

    logger.info(
        "Analyse %s sauvegardée pour le workflow %s.", result.analysis_id, request_id
    )

    return _build_success_update(state, result)
