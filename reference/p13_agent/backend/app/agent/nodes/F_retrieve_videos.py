"""Récupération de vidéos pédagogiques dans le workflow LangGraph.

Ce nœud construit une recherche YouTube depuis l'ouverture détectée ou, à
défaut, depuis le document Wikichess sélectionné par le moteur RAG. Il délègue
la recherche à ``YoutubeService`` et enrichit l'état avec les vidéos retenues.

La vidéo reste un enrichissement facultatif : l'absence de contexte interrompt
uniquement cette étape. Le nœud ne communique pas directement avec YouTube et
ne modifie jamais l'état reçu.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig

from app.adapters.youtube_service import YoutubeService
from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate, WorkflowContext
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.config import settings
from app.core.constants import (
    ERROR_CONFIGURATION,
    ERROR_UNEXPECTED,
    ERROR_YOUTUBE_UNAVAILABLE,
)
from app.core.exceptions import YoutubeError
from app.core.logging import get_logger
from app.schemas.common.enums import (
    AnalysisStatus,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowError, WorkflowWarning
from app.schemas.media.video import Video, VideoCollection, VideoSearchRequest

logger = get_logger(__name__)


# Configuration

YOUTUBE_SERVICE_KEY = "youtube_service"

SEARCH_SOURCE_LICHESS = "lichess"
SEARCH_SOURCE_WIKICHESS = "wikichess"


# Types


@dataclass(frozen=True, slots=True)
class VideoSearchContext:
    """Informations nécessaires à une recherche YouTube."""

    query: str
    title: str
    eco: str | None
    source: str


# Services


def _get_youtube_service(config: RunnableConfig) -> YoutubeService | None:
    """Retourne le service YouTube configuré avec un type vérifié."""
    service = get_configured_service(
        config, YOUTUBE_SERVICE_KEY, expected_type=YoutubeService
    )

    if service is None:
        return None

    if not isinstance(service, YoutubeService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de YoutubeService.",
            YOUTUBE_SERVICE_KEY,
            type(service).__name__,
        )
        return None

    return service


# Statuts


def _get_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut applicable après une recherche réussie."""
    # Une réussite locale ne doit pas masquer une dégradation antérieure.
    if state.status is AnalysisStatus.PARTIAL_SUCCESS:
        return AnalysisStatus.PARTIAL_SUCCESS

    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.SUCCESS


def _get_partial_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut applicable après une recherche dégradée."""
    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.PARTIAL_SUCCESS


# Normalisation


def _normalize_text(value: object) -> str | None:
    """Retourne une chaîne facultative nettoyée."""
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    return normalized_value or None


# Sources


def _get_opening_identity(state: ChessAnalysisState) -> tuple[str | None, str | None]:
    """Retourne le nom et le code ECO de l'ouverture détectée."""
    if state.opening is None:
        return None, None

    opening = state.opening.opening
    return _normalize_text(opening.name), _normalize_text(opening.eco)


def _get_wikichess_identity(state: ChessAnalysisState) -> tuple[str | None, str | None]:
    """Retourne le titre et le code ECO du document Wikichess retenu."""
    retrieval_context = state.retrieval_context

    if retrieval_context is None or not retrieval_context.documents:
        return None, None

    document = retrieval_context.documents[0].document
    metadata = document.metadata

    # Ces champs appartiennent au schéma Wikichess enrichi. ``getattr`` garde
    # le nœud compatible avec les anciens documents qui ne les possèdent pas.
    wikichess_title = _normalize_text(getattr(metadata, "wikichess_title", None))
    title = wikichess_title or _normalize_text(document.title)
    eco = _normalize_text(getattr(metadata, "eco", None))

    return title, eco


# Contexte de recherche


def _build_lichess_search_context(
    state: ChessAnalysisState,
) -> VideoSearchContext | None:
    """Construit le contexte YouTube depuis l'ouverture Lichess."""
    opening_name, opening_eco = _get_opening_identity(state)

    if opening_name is None:
        return None

    query = " ".join(part for part in (opening_name, opening_eco) if part is not None)

    return VideoSearchContext(
        query=query, title=opening_name, eco=opening_eco, source=SEARCH_SOURCE_LICHESS
    )


def _build_wikichess_search_context(
    state: ChessAnalysisState,
) -> VideoSearchContext | None:
    """Construit le contexte YouTube depuis le document Wikichess."""
    wikichess_title, wikichess_eco = _get_wikichess_identity(state)

    if wikichess_title is None:
        return None

    _, opening_eco = _get_opening_identity(state)
    eco = wikichess_eco or opening_eco
    query = " ".join(part for part in (wikichess_title, eco) if part is not None)

    return VideoSearchContext(
        query=query, title=wikichess_title, eco=eco, source=SEARCH_SOURCE_WIKICHESS
    )


def _build_search_context(state: ChessAnalysisState) -> VideoSearchContext | None:
    """Construit le meilleur contexte disponible pour YouTube."""
    lichess_context = _build_lichess_search_context(state)

    if lichess_context is not None:
        return lichess_context

    return _build_wikichess_search_context(state)


# Recherche


def _build_search_request(context: VideoSearchContext) -> VideoSearchRequest:
    """Construit la requête attendue par YoutubeService."""
    return VideoSearchRequest(
        query=context.query,
        max_results=settings.youtube_search_max_results,
        language=settings.youtube_default_language,
    )


def _select_videos(collection: VideoCollection) -> list[Video]:
    """Sélectionne les vidéos conservées dans l'état."""
    recommendations = collection.videos[: settings.max_selected_videos]
    return [recommendation.video for recommendation in recommendations]


# Résumés


def _build_video_summary(video: Video, index: int) -> str:
    """Construit le résumé factuel d'une vidéo."""
    lines = [
        f"Vidéo {index} :",
        f"- Titre : {video.title}",
        f"- Chaîne : {video.channel.name}",
        f"- URL : {video.url}",
    ]

    optional_values = (
        ("Miniature", video.thumbnail_url),
        ("Publication", video.published_at),
        ("Langue", video.language),
    )

    for label, value in optional_values:
        if value:
            lines.append(f"- {label} : {value}")

    if video.duration_seconds is not None:
        lines.append(f"- Durée : {video.duration_seconds} secondes")

    return "\n".join(lines)


def _build_videos_summary(videos: Sequence[Video]) -> str | None:
    """Construit le contexte vidéo destiné à la réponse finale."""
    if not videos:
        return None

    return "\n\n".join(
        _build_video_summary(video, index)
        for index, video in enumerate(videos, start=1)
    )


def _build_workflow_context(
    state: ChessAnalysisState, videos: Sequence[Video]
) -> WorkflowContext:
    """Ajoute le résumé vidéo au contexte du workflow."""
    return state.workflow_context.model_copy(
        update={"videos_summary": _build_videos_summary(videos)}
    )


# Mises à jour


def _build_completed_update(
    state: ChessAnalysisState,
    *,
    status: AnalysisStatus,
    videos: Sequence[Video],
    warnings: Sequence[WorkflowWarning],
) -> StateUpdate:
    """Construit la mise à jour d'une étape YouTube terminée."""
    current_step = WorkflowStep.RETRIEVE_VIDEOS
    selected_videos = list(videos)

    return {
        "status": status,
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "videos": selected_videos,
        "workflow_context": _build_workflow_context(state, selected_videos),
        "errors": list(state.errors),
        "warnings": list(warnings),
    }


def _build_success_update(
    state: ChessAnalysisState, videos: Sequence[Video]
) -> StateUpdate:
    """Construit la mise à jour après une recherche réussie."""
    return _build_completed_update(
        state, status=_get_success_status(state), videos=videos, warnings=state.warnings
    )


def _build_warning_update(
    state: ChessAnalysisState, warning: WorkflowWarning
) -> StateUpdate:
    """Construit la mise à jour après une indisponibilité de YouTube."""
    return _build_completed_update(
        state,
        status=_get_partial_success_status(state),
        videos=(),
        warnings=(*state.warnings, warning),
    )


def _build_error_update(state: ChessAnalysisState, error: WorkflowError) -> StateUpdate:
    """Construit la mise à jour après un échec bloquant."""
    return {
        "status": AnalysisStatus.FAILED,
        "current_step": WorkflowStep.RETRIEVE_VIDEOS,
        # Une étape échouée n'est jamais ajoutée aux étapes terminées.
        "completed_steps": list(state.completed_steps),
        "videos": [],
        "workflow_context": _build_workflow_context(state, ()),
        "errors": [*state.errors, error],
        "warnings": list(state.warnings),
    }


def _build_missing_service_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour lorsque YoutubeService est indisponible."""
    message = "YoutubeService est absent ou invalide dans la configuration LangGraph."

    logger.error(message)

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.RETRIEVE_VIDEOS,
            code=ERROR_CONFIGURATION,
            message=message,
            recoverable=False,
        ),
    )


def _build_youtube_warning_update(
    state: ChessAnalysisState, error: YoutubeError
) -> StateUpdate:
    """Construit la mise à jour après une erreur YouTube connue."""
    return _build_warning_update(
        state,
        WorkflowWarning(
            step=WorkflowStep.RETRIEVE_VIDEOS,
            code=ERROR_YOUTUBE_UNAVAILABLE,
            message=str(error),
        ),
    )


def _build_unexpected_error_update(state: ChessAnalysisState) -> StateUpdate:
    """Construit la mise à jour après une erreur inattendue."""
    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.RETRIEVE_VIDEOS,
            code=ERROR_UNEXPECTED,
            message=("Une erreur inattendue a empêché la récupération des vidéos."),
            recoverable=False,
        ),
    )


# API publique


async def retrieve_videos(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Recherche les vidéos pédagogiques associées à l'ouverture."""
    current_step = WorkflowStep.RETRIEVE_VIDEOS
    search_context = _build_search_context(state)

    if search_context is None:
        logger.info(
            "Aucun contexte Lichess ou Wikichess exploitable. "
            "Recherche YouTube ignorée."
        )

        return _build_success_update(state, ())

    logger.info(
        "Préparation de la recherche YouTube : titre=%r, eco=%r, source=%s, query=%r.",
        search_context.title,
        search_context.eco,
        search_context.source,
        search_context.query,
    )

    youtube_service = _get_youtube_service(config)

    if youtube_service is None:
        emit_progress(
            step=current_step,
            service=ServiceType.YOUTUBE,
            status=WorkflowStepStatus.FAILED,
            message="YoutubeService indisponible.",
        )

        return _build_missing_service_update(state)

    try:
        request = _build_search_request(search_context)

        emit_progress(
            step=current_step,
            service=ServiceType.YOUTUBE,
            status=WorkflowStepStatus.RUNNING,
            message="Recherche de vidéos pédagogiques en cours.",
        )

        collection = await youtube_service.search_videos(request)

        videos = _select_videos(collection)

        emit_progress(
            step=current_step,
            service=ServiceType.YOUTUBE,
            status=WorkflowStepStatus.COMPLETED,
            message="Recherche de vidéos pédagogiques terminée.",
        )

    except YoutubeError as error:
        emit_progress(
            step=current_step,
            service=ServiceType.YOUTUBE,
            status=WorkflowStepStatus.WARNING,
            message="Recherche YouTube indisponible.",
        )

        logger.warning("Recherche YouTube impossible : %s", error)

        return _build_youtube_warning_update(state, error)

    except Exception:
        emit_progress(
            step=current_step,
            service=ServiceType.YOUTUBE,
            status=WorkflowStepStatus.FAILED,
            message=("Une erreur inattendue a interrompu la recherche YouTube."),
        )

        logger.exception("Erreur inattendue durant la récupération des vidéos.")

        return _build_unexpected_error_update(state)

    logger.info(
        "%s vidéo(s) pédagogique(s) conservée(s) : titre=%r, source=%s.",
        len(videos),
        search_context.title,
        search_context.source,
    )

    return _build_success_update(state, videos)
