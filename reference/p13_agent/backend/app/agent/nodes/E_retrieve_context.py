"""Récupération du contexte documentaire dans le workflow LangGraph.

Ce nœud recherche le contenu pédagogique Wikichess associé à la position
analysée. Il prépare les critères structurels, délègue la recherche à
``VectorSearchService`` et transforme les résultats en contexte RAG.

Il ne génère aucun embedding, n'accède pas directement à Milvus et ne
reconstruit jamais un historique de coups depuis une ouverture ou une FEN.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite

from langchain_core.runnables import RunnableConfig

from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate, WorkflowContext
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.constants import (
    DEFAULT_DOCUMENT_LANGUAGE,
    DEFAULT_DOCUMENT_TITLE,
    ERROR_CONFIGURATION,
    ERROR_MILVUS_UNAVAILABLE,
    ERROR_UNEXPECTED,
)
from app.core.exceptions import RetrievalError
from app.core.logging import get_logger
from app.schemas.analysis.search import VectorSearchResult
from app.schemas.common.enums import (
    AnalysisStatus,
    DocumentType,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowError, WorkflowWarning
from app.schemas.rag.document import (
    Document,
    DocumentChunk,
    DocumentMetadata,
    DocumentNextMove,
    RetrievalContext,
    RetrievedDocument,
)
from app.services.chess_service import ChessService
from app.services.vector_search_service import VectorSearchService

logger = get_logger(__name__)


# Configuration

CHESS_SERVICE_KEY = "chess_service"
VECTOR_SEARCH_SERVICE_KEY = "vector_search_service"

DEFAULT_DOCUMENT_TYPE = DocumentType.ARTICLE
DEFAULT_DOCUMENT_SOURCE = "milvus"
SELECTED_DOCUMENT_LIMIT = 1
EXCERPT_MAX_LENGTH = 500


# Métadonnées

METADATA_ARTICLE_SLUG_KEY = "article_slug"
METADATA_ARTICLE_TITLE_KEY = "article_title"
METADATA_DATASET_KEY = "dataset"
METADATA_ECO_KEY = "eco"
METADATA_LANGUAGE_KEY = "language"
METADATA_MOVES_KEY = "moves"
METADATA_MOVES_PATH_KEY = "moves_path"
METADATA_NEXT_MOVES_KEY = "next_moves"
METADATA_POSITION_AFTER_KEY = "position_after"
METADATA_SOURCE_KEY = "source"
METADATA_SOURCE_URL_KEY = "source_url"
METADATA_TYPE_KEY = "type"
METADATA_WIKICHESS_TITLE_KEY = "wikichess_title"

NEXT_MOVE_KEY = "move"
NEXT_MOVE_SOURCE_URL_KEY = "source_url"


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


def _get_vector_search_service(config: RunnableConfig) -> VectorSearchService | None:
    """Retourne le service de recherche configuré avec un type vérifié."""
    service = get_configured_service(
        config, VECTOR_SEARCH_SERVICE_KEY, expected_type=VectorSearchService
    )

    if service is None:
        return None

    if not isinstance(service, VectorSearchService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de VectorSearchService.",
            VECTOR_SEARCH_SERVICE_KEY,
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


def _normalize_moves(value: object) -> tuple[str, ...]:
    """Retourne une séquence de coups non vides."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()

    return tuple(
        normalized_move
        for move in value
        if (normalized_move := _normalize_text(move)) is not None
    )


# Coups


def _get_state_moves(state: ChessAnalysisState) -> tuple[str, ...]:
    """Retourne les coups réellement transmis au workflow."""
    return _normalize_moves(getattr(state, "moves", ()))


def _format_moves_path(moves: Sequence[str]) -> str:
    """Formate une séquence de coups pour la recherche et les journaux."""
    return " ".join(moves)


def _convert_moves_to_san(
    service: ChessService, moves: Sequence[str]
) -> tuple[str, ...]:
    """Convertit un historique UCI en une séquence SAN normalisée."""
    converted_moves = service.convert_uci_history_to_san(list(moves))
    san_moves = _normalize_moves(converted_moves)

    if moves and not san_moves:
        raise ValueError("La conversion UCI vers SAN n'a retourné aucun coup.")

    return san_moves


# Ouverture


def _get_opening_identity(state: ChessAnalysisState) -> tuple[str | None, str | None]:
    """Retourne le nom et le code ECO de l'ouverture détectée."""
    if state.opening is None:
        return None, None

    opening = state.opening.opening
    return _normalize_text(opening.name), _normalize_text(opening.eco)


# Requête


def _build_search_query(state: ChessAnalysisState, moves: Sequence[str]) -> str:
    """Construit la description factuelle de la recherche."""
    opening_name, opening_eco = _get_opening_identity(state)
    sections = ["Type : présentation Wikichess"]

    if moves:
        sections.append(f"Coups : {_format_moves_path(moves)}")

    if opening_name:
        sections.append(f"Ouverture Lichess : {opening_name}")

    if opening_eco:
        sections.append(f"Code ECO Lichess : {opening_eco}")

    return "\n".join(sections)


# Lecture des métadonnées


def _get_result_metadata(result: VectorSearchResult) -> dict[str, object]:
    """Retourne les métadonnées normalisées d'un résultat."""
    metadata = result.metadata

    if not isinstance(metadata, Mapping):
        return {}

    return {str(key): value for key, value in metadata.items()}


def _get_metadata_string(metadata: Mapping[str, object], key: str, default: str) -> str:
    """Retourne une chaîne obligatoire issue des métadonnées."""
    return _normalize_text(metadata.get(key)) or default


def _get_optional_metadata_string(
    metadata: Mapping[str, object], key: str
) -> str | None:
    """Retourne une chaîne facultative issue des métadonnées."""
    return _normalize_text(metadata.get(key))


def _get_metadata_moves(metadata: Mapping[str, object]) -> tuple[str, ...]:
    """Retourne les coups Wikichess présents dans les métadonnées."""
    return _normalize_moves(metadata.get(METADATA_MOVES_KEY))


def _build_next_move(value: object) -> DocumentNextMove | None:
    """Construit une continuation Wikichess valide."""
    if not isinstance(value, Mapping):
        return None

    move = _normalize_text(value.get(NEXT_MOVE_KEY))
    source_url = _normalize_text(value.get(NEXT_MOVE_SOURCE_URL_KEY))

    if move is None or source_url is None:
        return None

    return DocumentNextMove(move=move, source_url=source_url)


def _get_metadata_next_moves(
    metadata: Mapping[str, object],
) -> tuple[DocumentNextMove, ...]:
    """Retourne les continuations Wikichess valides."""
    value = metadata.get(METADATA_NEXT_MOVES_KEY)

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()

    return tuple(
        next_move for item in value if (next_move := _build_next_move(item)) is not None
    )


# Résultats vectoriels


def _get_result_identifier(result: VectorSearchResult) -> str:
    """Retourne l'identifiant stable d'un résultat."""
    return _normalize_text(str(result.id)) or "document"


def _get_result_content(result: VectorSearchResult) -> str:
    """Retourne le contenu complet d'un résultat."""
    return _normalize_text(result.content) or ""


def _get_result_similarity(result: VectorSearchResult) -> float:
    """Retourne une similarité numérique exploitable."""
    value: object = result.similarity

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0.0

    try:
        similarity = float(value)
    except ValueError:
        return 0.0

    return similarity if isfinite(similarity) else 0.0


def _normalize_similarity(value: float) -> float:
    """Limite une similarité à l'intervalle de zéro à un."""
    if not isfinite(value):
        return 0.0

    return min(max(value, 0.0), 1.0)


def _get_result_source(result: VectorSearchResult) -> str:
    """Retourne la source du document."""
    metadata = _get_result_metadata(result)
    source = _normalize_text(metadata.get(METADATA_SOURCE_KEY))

    if source:
        return source

    dataset = _normalize_text(metadata.get(METADATA_DATASET_KEY))
    return dataset or DEFAULT_DOCUMENT_SOURCE


def _get_document_type(metadata: Mapping[str, object]) -> DocumentType:
    """Retourne le type métier du document."""
    value = metadata.get(METADATA_TYPE_KEY)

    if isinstance(value, DocumentType):
        return value

    normalized_value = _normalize_text(value)

    if normalized_value is None:
        return DEFAULT_DOCUMENT_TYPE

    try:
        return DocumentType(normalized_value.lower())
    except ValueError:
        logger.debug("Type de document inconnu : %s", normalized_value)
        return DEFAULT_DOCUMENT_TYPE


# Conversion documentaire


def _build_excerpt(content: str, *, max_length: int = EXCERPT_MAX_LENGTH) -> str | None:
    """Construit un aperçu court du contenu pédagogique."""
    normalized_content = " ".join(content.split())

    if not normalized_content:
        return None

    if len(normalized_content) <= max_length:
        return normalized_content

    return normalized_content[:max_length].rstrip() + "..."


def _build_document_metadata(
    result: VectorSearchResult, metadata: Mapping[str, object]
) -> DocumentMetadata:
    """Construit les métadonnées métier d'un document RAG."""
    return DocumentMetadata(
        source=_get_result_source(result),
        language=_get_metadata_string(
            metadata, METADATA_LANGUAGE_KEY, DEFAULT_DOCUMENT_LANGUAGE
        ),
        author=None,
        url=_get_optional_metadata_string(metadata, METADATA_SOURCE_URL_KEY),
        publication_date=None,
        eco=_get_optional_metadata_string(metadata, METADATA_ECO_KEY),
        moves=_get_metadata_moves(metadata),
        moves_path=_get_optional_metadata_string(metadata, METADATA_MOVES_PATH_KEY),
        position_after=_get_optional_metadata_string(
            metadata, METADATA_POSITION_AFTER_KEY
        ),
        wikichess_title=_get_optional_metadata_string(
            metadata, METADATA_WIKICHESS_TITLE_KEY
        ),
        next_moves=_get_metadata_next_moves(metadata),
    )


def _build_retrieved_document(result: VectorSearchResult) -> RetrievedDocument:
    """Construit un document RAG depuis un résultat vectoriel."""
    metadata = _get_result_metadata(result)
    identifier = _get_result_identifier(result)
    article_slug = _get_metadata_string(metadata, METADATA_ARTICLE_SLUG_KEY, identifier)
    title = _get_metadata_string(
        metadata,
        METADATA_ARTICLE_TITLE_KEY,
        _get_metadata_string(
            metadata, METADATA_WIKICHESS_TITLE_KEY, DEFAULT_DOCUMENT_TITLE
        ),
    )
    content = _get_result_content(result)
    document = Document(
        id=article_slug,
        type=_get_document_type(metadata),
        title=title,
        content=content,
        metadata=_build_document_metadata(result, metadata),
    )
    chunk = DocumentChunk(
        id=identifier, document_id=article_slug, content=content, chunk_index=0
    )

    return RetrievedDocument(
        document=document,
        similarity=_normalize_similarity(_get_result_similarity(result)),
        chunk=chunk,
        excerpt=_build_excerpt(content),
    )


# Contexte RAG


def _select_results(results: Sequence[VectorSearchResult]) -> list[VectorSearchResult]:
    """Sélectionne les résultats les plus similaires."""
    ordered_results = sorted(results, key=_get_result_similarity, reverse=True)
    return ordered_results[:SELECTED_DOCUMENT_LIMIT]


def _build_retrieval_context(
    query: str, results: Sequence[VectorSearchResult]
) -> RetrievalContext:
    """Construit le contexte documentaire du workflow."""
    selected_results = _select_results(results)
    documents = [_build_retrieved_document(result) for result in selected_results]

    return RetrievalContext(
        query=query, documents=documents, total_results=len(documents)
    )


def _build_empty_retrieval_context(query: str) -> RetrievalContext:
    """Construit un contexte documentaire vide."""
    return RetrievalContext(query=query, documents=[], total_results=0)


# Contexte du workflow


def _build_documents_summary(retrieval_context: RetrievalContext) -> str | None:
    """Construit le résumé documentaire destiné à la réponse finale."""
    if not retrieval_context.documents:
        return None

    sections: list[str] = []

    for retrieved_document in retrieval_context.documents:
        document = retrieved_document.document
        metadata = document.metadata
        lines = [f"Titre : {document.title}"]

        if metadata.wikichess_title:
            lines.append(f"Titre Wikichess : {metadata.wikichess_title}")

        if metadata.eco:
            lines.append(f"Code ECO : {metadata.eco}")

        if metadata.moves:
            lines.append(f"Coups : {_format_moves_path(metadata.moves)}")

        if metadata.position_after:
            lines.append(f"Position après : {metadata.position_after}")

        if document.content:
            lines.append(f"Présentation Wikichess :\n{document.content}")

        if metadata.next_moves:
            next_moves = ", ".join(next_move.move for next_move in metadata.next_moves)
            lines.append(f"Continuations Wikichess : {next_moves}")

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _build_workflow_context(
    state: ChessAnalysisState, retrieval_context: RetrievalContext
) -> WorkflowContext:
    """Ajoute le résumé documentaire au contexte du workflow."""
    return state.workflow_context.model_copy(
        update={"documents_summary": _build_documents_summary(retrieval_context)}
    )


# Recherche


async def _search_documents(
    *, state: ChessAnalysisState, moves: Sequence[str], service: VectorSearchService
) -> RetrievalContext:
    """Recherche le document Wikichess correspondant au contexte."""
    _, opening_eco = _get_opening_identity(state)
    query = _build_search_query(state, moves)

    if opening_eco is None and not moves:
        return _build_empty_retrieval_context(query)

    logger.info(
        "Recherche Wikichess : eco=%r, moves=%r.",
        opening_eco,
        _format_moves_path(moves) if moves else None,
    )
    results = await service.search_wikichess(
        query=query, eco=opening_eco, moves=moves, limit=SELECTED_DOCUMENT_LIMIT
    )

    _log_search_results(results)

    if not results:
        logger.info("Aucun document Wikichess correspondant au contexte disponible.")
        return _build_empty_retrieval_context(query)

    logger.info("%s document(s) Wikichess identifié(s).", len(results))
    return _build_retrieval_context(query, results)


# Erreurs


def _get_retrieval_error_code(error: RetrievalError) -> str:
    """Retourne le code public d'une erreur de recherche connue."""
    code = _normalize_text(getattr(error, "code", None))
    return code or ERROR_MILVUS_UNAVAILABLE


def _build_missing_service_update(
    state: ChessAnalysisState, service_name: str
) -> StateUpdate:
    """Construit la mise à jour lorsqu'un service est indisponible."""
    message = f"{service_name} est absent ou invalide dans la configuration LangGraph."
    logger.error(message)

    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            code=ERROR_CONFIGURATION,
            message=message,
            recoverable=False,
        ),
    )


def _build_unexpected_error_update(
    state: ChessAnalysisState, message: str
) -> StateUpdate:
    """Construit la mise à jour après une erreur inattendue."""
    return _build_error_update(
        state,
        WorkflowError(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            code=ERROR_UNEXPECTED,
            message=message,
            recoverable=False,
        ),
    )


# Mises à jour


def _build_success_update(
    state: ChessAnalysisState, retrieval_context: RetrievalContext
) -> StateUpdate:
    """Construit la mise à jour après une recherche réussie."""
    current_step = WorkflowStep.RETRIEVE_CONTEXT

    return {
        "status": _get_success_status(state),
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "retrieval_context": retrieval_context,
        "workflow_context": _build_workflow_context(state, retrieval_context),
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


def _build_warning_update(
    state: ChessAnalysisState, warning: WorkflowWarning, *, query: str
) -> StateUpdate:
    """Construit la mise à jour après une erreur récupérable."""
    current_step = WorkflowStep.RETRIEVE_CONTEXT
    retrieval_context = _build_empty_retrieval_context(query)

    return {
        "status": _get_partial_success_status(state),
        "current_step": current_step,
        # La recherche facultative est terminée malgré l'avertissement.
        "completed_steps": append_completed_step(state, current_step),
        "retrieval_context": retrieval_context,
        "workflow_context": _build_workflow_context(state, retrieval_context),
        "errors": list(state.errors),
        "warnings": [*state.warnings, warning],
    }


def _build_error_update(state: ChessAnalysisState, error: WorkflowError) -> StateUpdate:
    """Construit la mise à jour après un échec bloquant."""
    return {
        "status": AnalysisStatus.FAILED,
        "current_step": WorkflowStep.RETRIEVE_CONTEXT,
        # Une étape échouée n'est jamais ajoutée aux étapes terminées.
        "completed_steps": list(state.completed_steps),
        "errors": [*state.errors, error],
        "warnings": list(state.warnings),
    }


# Journalisation


def _log_search_results(results: Sequence[VectorSearchResult]) -> None:
    """Journalise les résultats documentaires sans exposer leur contenu."""
    logger.info("Recherche Wikichess terminée : %s résultat(s).", len(results))

    for index, result in enumerate(results, start=1):
        metadata = _get_result_metadata(result)
        logger.debug(
            "Résultat %s : id=%r, similarité=%.4f, titre=%r, eco=%r, "
            "moves=%r, moves_path=%r, position_after=%r, next_moves=%s.",
            index,
            result.id,
            _get_result_similarity(result),
            metadata.get(METADATA_ARTICLE_TITLE_KEY),
            metadata.get(METADATA_ECO_KEY),
            _get_metadata_moves(metadata),
            metadata.get(METADATA_MOVES_PATH_KEY),
            metadata.get(METADATA_POSITION_AFTER_KEY),
            len(_get_metadata_next_moves(metadata)),
        )


def _log_retrieval_result(retrieval_context: RetrievalContext) -> None:
    """Journalise le document finalement retenu."""
    if not retrieval_context.documents:
        logger.info("Aucun document pédagogique Wikichess récupéré.")
        return

    retrieved_document = retrieval_context.documents[0]
    document = retrieved_document.document
    metadata = document.metadata
    logger.info(
        "Document Wikichess récupéré : titre=%r, wikichess_title=%r, "
        "similarité=%.4f, eco=%r, moves=%r, position_after=%r, "
        "next_moves=%s.",
        document.title,
        metadata.wikichess_title,
        retrieved_document.similarity,
        metadata.eco,
        metadata.moves,
        metadata.position_after,
        len(metadata.next_moves),
    )


# Nœud


async def retrieve_context(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Recherche le contexte pédagogique associé à la position."""
    opening_name, opening_eco = _get_opening_identity(state)
    uci_moves = _get_state_moves(state)
    logger.info(
        "Préparation de la recherche documentaire : moves=%r, ouverture=%r, eco=%r.",
        _format_moves_path(uci_moves) if uci_moves else None,
        opening_name,
        opening_eco,
    )

    if opening_eco is None and not uci_moves:
        query = _build_search_query(state, ())
        logger.info(
            "Aucun code ECO ni historique de coups disponible. "
            "Recherche Wikichess ignorée."
        )
        return _build_success_update(state, _build_empty_retrieval_context(query))

    vector_search_service = _get_vector_search_service(config)

    if vector_search_service is None:
        emit_progress(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            service=ServiceType.VECTOR_SEARCH,
            status=WorkflowStepStatus.FAILED,
            message="VectorSearchService indisponible.",
        )

        return _build_missing_service_update(state, "VectorSearchService")

    san_moves: tuple[str, ...] = ()

    if uci_moves:
        chess_service = _get_chess_service(config)

        if chess_service is None:
            emit_progress(
                step=WorkflowStep.RETRIEVE_CONTEXT,
                service=ServiceType.CHESS,
                status=WorkflowStepStatus.FAILED,
                message="ChessService indisponible.",
            )

            return _build_missing_service_update(state, "ChessService")

        try:
            emit_progress(
                step=WorkflowStep.RETRIEVE_CONTEXT,
                service=ServiceType.CHESS,
                status=WorkflowStepStatus.RUNNING,
                message="Conversion des coups UCI en SAN en cours.",
            )

            san_moves = _convert_moves_to_san(chess_service, uci_moves)

            emit_progress(
                step=WorkflowStep.RETRIEVE_CONTEXT,
                service=ServiceType.CHESS,
                status=WorkflowStepStatus.COMPLETED,
                message="Conversion des coups UCI en SAN terminée.",
            )

        except Exception:
            emit_progress(
                step=WorkflowStep.RETRIEVE_CONTEXT,
                service=ServiceType.CHESS,
                status=WorkflowStepStatus.FAILED,
                message="Conversion des coups UCI en SAN impossible.",
            )

            logger.exception("Impossible de convertir l'historique UCI en SAN.")

            return _build_unexpected_error_update(
                state, "Une erreur inattendue a empêché la conversion des coups."
            )

        logger.info(
            "Historique converti pour Wikichess : uci=%r, san=%r.",
            _format_moves_path(uci_moves),
            _format_moves_path(san_moves),
        )

    query = _build_search_query(state, san_moves)
    logger.info("Recherche du contexte pédagogique Wikichess.")

    try:
        emit_progress(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            service=ServiceType.VECTOR_SEARCH,
            status=WorkflowStepStatus.RUNNING,
            message="Recherche documentaire Wikichess en cours.",
        )

        retrieval_context = await _search_documents(
            state=state, moves=san_moves, service=vector_search_service
        )

        emit_progress(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            service=ServiceType.VECTOR_SEARCH,
            status=WorkflowStepStatus.COMPLETED,
            message="Recherche documentaire Wikichess terminée.",
        )

    except RetrievalError as error:
        emit_progress(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            service=ServiceType.VECTOR_SEARCH,
            status=WorkflowStepStatus.WARNING,
            message="Recherche documentaire Wikichess indisponible.",
        )

        logger.warning("Recherche documentaire indisponible : %s", error)

        return _build_warning_update(
            state,
            WorkflowWarning(
                step=WorkflowStep.RETRIEVE_CONTEXT,
                code=_get_retrieval_error_code(error),
                message=str(error),
            ),
            query=query,
        )

    except Exception:
        emit_progress(
            step=WorkflowStep.RETRIEVE_CONTEXT,
            service=ServiceType.VECTOR_SEARCH,
            status=WorkflowStepStatus.FAILED,
            message=("Une erreur inattendue a interrompu la recherche documentaire."),
        )

        logger.exception(
            "Erreur inattendue durant la récupération du contexte documentaire."
        )

        return _build_unexpected_error_update(
            state, "Une erreur inattendue a empêché la recherche documentaire."
        )

    _log_retrieval_result(retrieval_context)
    return _build_success_update(state, retrieval_context)
