"""Génération de la réponse finale du workflow LangGraph.

Ce nœud construit un contexte minimal depuis l'état partagé, délègue la
rédaction à ``LLMService`` et enregistre la réponse obtenue.

Il transmet uniquement les données nécessaires au modèle, n'effectue aucune
analyse échiquéenne supplémentaire et produit une réponse factuelle de secours
si le service LLM est indisponible.
"""

from __future__ import annotations

from collections.abc import Sequence
from textwrap import dedent

from langchain_core.runnables import RunnableConfig

from app.adapters.llm_service import LLMService
from app.agent.progress import emit_progress
from app.agent.state import ChessAnalysisState, StateUpdate, WorkflowContext
from app.agent.utils.workflow_utils import append_completed_step, get_configured_service
from app.core.constants import (
    DEFAULT_RESPONSE_LANGUAGE,
    ERROR_CONFIGURATION,
    ERROR_UNEXPECTED,
)
from app.core.logging import get_logger
from app.schemas.analysis.evaluation import EngineAnalysis
from app.schemas.common.enums import (
    AnalysisStatus,
    ServiceType,
    WorkflowStep,
    WorkflowStepStatus,
)
from app.schemas.common.error import WorkflowWarning
from app.schemas.rag.document import RetrievedDocument

logger = get_logger(__name__)


# Configuration

LLM_SERVICE_KEY = "llm"

VECTOR_CONTENT_PREFIXES = (
    "Type :",
    "Ouverture :",
    "Titre :",
    "Code ECO :",
    "Coups :",
    "Position après :",
    "Position after :",
)

LLM_CONFIGURATION_MESSAGE = (
    "LLMService est absent ou invalide dans la configuration LangGraph. "
    "Une réponse factuelle minimale a été utilisée."
)
LLM_GENERATION_ERROR_MESSAGE = (
    "La génération de la réponse pédagogique a échoué. "
    "Une réponse factuelle minimale a été utilisée."
)


# Instructions

GENERAL_RULES = """
Tu es Chess Agent.

Tu reformules et traduis les informations fournies.
Tu n'effectues aucune analyse échiquéenne.

Les informations fournies constituent l'unique source de vérité.

# Règles générales

- Utilise uniquement les informations fournies.
- N'ajoute aucune information, explication ou qualification.
- Ne déduis rien des métadonnées, statistiques, scores ou coups.
- Ne transforme jamais une notation échiquéenne.
- Traduis la présentation pédagogique sans modifier son sens.
- Ne remplace jamais une action décrite par une autre action.
- Ne transforme jamais « échanger » en « sacrifier ».
- Ne qualifie jamais un coup ou une alternative.
- En français, utilise « les Blancs » et « les Noirs ».

Une explication échiquéenne est autorisée uniquement si elle est
explicitement présente dans la présentation pédagogique fournie.
""".strip()

WIKICHESS_RULES = """
La présentation pédagogique Wikichess peut être reformulée et traduite.

Les autres informations Wikichess sont uniquement descriptives.

Les correspondances Wikichess éventuellement fournies sont factuelles.

N'en déduis aucune qualité, recommandation ou confirmation.
""".strip()

LICHESS_RULES = """
Les statistiques Lichess décrivent uniquement les parties observées.

Tu peux comparer les fréquences fournies.

Si tu indiques le résultat le plus fréquent, utilise la valeur numérique
la plus élevée parmi toutes les valeurs disponibles.

N'en déduis aucun avantage, aucune qualité de position ou de coup,
aucun résultat futur ni aucune relation de cause à effet.
""".strip()

STOCKFISH_RULES = """
Le meilleur coup est uniquement le meilleur coup calculé par Stockfish.

Les alternatives sont uniquement des alternatives calculées par Stockfish.

Tu peux restituer les scores et la profondeur fournis.

Tu peux comparer numériquement les scores.

N'ajoute aucune qualification aux coups ou aux alternatives.

Le signe d'un score n'autorise aucune déduction sur l'avantage d'un camp
ou la qualité de la position.
""".strip()

UNKNOWN_POSITION_RULES = """
Utilise uniquement le contexte fourni pour cette position.

N'essaie pas d'identifier une ouverture.

Ne complète aucune information depuis tes connaissances.
""".strip()

RESPONSE_RULES = """
# Réponse

Produis 1 à 3 paragraphes courts en {language}.

Utilise uniquement les opérations applicables aux données présentes :

1. traduire ou reformuler fidèlement une présentation pédagogique ;
2. indiquer le meilleur coup Stockfish avec son score et sa profondeur ;
3. indiquer une correspondance Wikichess explicitement fournie ;
4. citer les alternatives Stockfish sans les qualifier ;
5. citer ou comparer numériquement les statistiques Lichess.

Toute autre opération est interdite.

Ne transforme pas une métadonnée en explication.
Ne complète pas une explication pédagogique.
Ne donne aucune interprétation personnelle.
""".strip()


# Services


def _get_llm_service(config: RunnableConfig) -> LLMService | None:
    """Retourne le service LLM configuré avec un type vérifié."""
    service = get_configured_service(config, LLM_SERVICE_KEY, expected_type=LLMService)

    if service is None:
        return None

    if not isinstance(service, LLMService):
        logger.error(
            "Service %s invalide : %s reçu au lieu de LLMService.",
            LLM_SERVICE_KEY,
            type(service).__name__,
        )
        return None

    return service


# Statuts


def _get_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut applicable après une génération réussie."""
    # Une réussite locale ne doit pas masquer une dégradation antérieure.
    if state.status is AnalysisStatus.PARTIAL_SUCCESS:
        return AnalysisStatus.PARTIAL_SUCCESS

    if state.status is AnalysisStatus.FAILED:
        return AnalysisStatus.FAILED

    return AnalysisStatus.SUCCESS


def _get_partial_success_status(state: ChessAnalysisState) -> AnalysisStatus:
    """Retourne le statut applicable après une génération dégradée."""
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


def _normalize_language(value: str | None) -> str:
    """Retourne la langue de réponse normalisée."""
    normalized_value = _normalize_text(value)

    if normalized_value is None:
        return DEFAULT_RESPONSE_LANGUAGE

    return normalized_value.lower()


def _format_value(value: object, default: str = "non disponible") -> str:
    """Formate une valeur destinée au prompt."""
    if value is None:
        return default

    if isinstance(value, bool):
        return "oui" if value else "non"

    return str(value)


def _format_percentage(value: float | int | None) -> str:
    """Formate un pourcentage."""
    if value is None:
        return "non disponible"

    return f"{value:.1f} %"


# Documents RAG


def _is_vector_metadata_line(line: str) -> bool:
    """Indique si une ligne appartient au préambule vectoriel."""
    return line.strip().startswith(VECTOR_CONTENT_PREFIXES)


def _strip_vector_content_header(content: str) -> str | None:
    """Retire le préambule technique ajouté pour l'embedding."""
    lines = content.splitlines()

    if not lines:
        return None

    index = 0
    found_vector_header = False

    while index < len(lines):
        line = lines[index].strip()

        if not line:
            if found_vector_header:
                index += 1
                break

            index += 1
            continue

        if not _is_vector_metadata_line(line):
            break

        found_vector_header = True
        index += 1

    if not found_vector_header:
        return _normalize_text(content)

    remaining_content = "\n".join(lines[index:]).strip()
    return remaining_content or None


def _extract_document_content(retrieved_document: RetrievedDocument) -> str | None:
    """Retourne uniquement la présentation pédagogique disponible."""
    content = _normalize_text(retrieved_document.document.content)

    if content is None:
        return None

    return _strip_vector_content_header(content)


def _get_document_eco(retrieved_document: RetrievedDocument) -> str | None:
    """Retourne le code ECO d'un document lorsqu'il est disponible."""
    metadata = retrieved_document.document.metadata
    return _normalize_text(getattr(metadata, "eco", None))


def _build_document_section(retrieved_document: RetrievedDocument) -> str:
    """Construit le contexte minimal d'un document Wikichess."""
    document = retrieved_document.document
    lines = [f"Titre : {document.title}"]
    eco = _get_document_eco(retrieved_document)

    if eco is not None:
        lines.append(f"Code ECO : {eco}")

    content = _extract_document_content(retrieved_document)
    lines.extend(["", "Présentation pédagogique :"])
    lines.append(content or "Aucune présentation pédagogique disponible.")

    return "\n".join(lines)


def _build_documents_context(state: ChessAnalysisState) -> str | None:
    """Construit le contexte Wikichess destiné au LLM."""
    retrieval_context = state.retrieval_context

    if retrieval_context is None:
        return None

    if not retrieval_context.documents:
        return "Aucune présentation pédagogique Wikichess n'a été trouvée."

    sections = [
        _build_document_section(retrieved_document)
        for retrieved_document in retrieval_context.documents
    ]
    return "\n\n".join(sections)


def _get_wikichess_continuations(state: ChessAnalysisState) -> frozenset[str]:
    """Retourne les continuations Wikichess disponibles."""
    retrieval_context = state.retrieval_context

    if retrieval_context is None:
        return frozenset()

    continuations: set[str] = set()

    for retrieved_document in retrieval_context.documents:
        metadata = retrieved_document.document.metadata
        next_moves = getattr(metadata, "next_moves", ())

        if isinstance(next_moves, (str, bytes)):
            continue

        if not isinstance(next_moves, Sequence):
            continue

        for next_move in next_moves:
            move = _normalize_text(getattr(next_move, "move", None))

            if move is not None:
                continuations.add(move)

    return frozenset(continuations)


# Ouverture Lichess


def _build_opening_context(state: ChessAnalysisState) -> str | None:
    """Construit le contexte statistique Lichess."""
    if state.opening is None or state.opening.statistics is None:
        return None

    statistics = state.opening.statistics
    lines = [
        f"Parties : {statistics.games}",
        (f"Victoires blanches : {_format_percentage(statistics.white_win_rate)}"),
        f"Parties nulles : {_format_percentage(statistics.draw_rate)}",
        (f"Victoires noires : {_format_percentage(statistics.black_win_rate)}"),
    ]
    return "\n".join(lines)


# Stockfish


def _get_engine(state: ChessAnalysisState) -> EngineAnalysis | None:
    """Retourne l'analyse moteur disponible."""
    if state.evaluation is not None:
        engine: object = state.evaluation.engine
    else:
        engine = state.engine_analysis

    if engine is None:
        return None

    if not isinstance(engine, EngineAnalysis):
        logger.error(
            "Analyse Stockfish invalide : %s reçu au lieu de EngineAnalysis.",
            type(engine).__name__,
        )
        return None

    return engine


def _append_best_move(lines: list[str], engine: EngineAnalysis) -> None:
    """Ajoute le meilleur coup et l'évaluation Stockfish."""
    best_move = engine.best_move
    evaluation = engine.evaluation

    if best_move is not None:
        lines.append(f"Meilleur coup : {_format_value(best_move.san)}")

    lines.extend(
        [
            f"Score : {_format_value(evaluation.score)}",
            f"Profondeur : {_format_value(evaluation.depth)}",
        ]
    )


def _append_alternatives(lines: list[str], engine: EngineAnalysis) -> None:
    """Ajoute les alternatives Stockfish utiles au LLM."""
    if not engine.alternatives:
        return

    lines.extend(["", "Alternatives :"])
    lines.extend(
        f"- {alternative.san} : {alternative.score}"
        for alternative in engine.alternatives
    )


def _append_wikichess_matches(
    lines: list[str], state: ChessAnalysisState, engine: EngineAnalysis
) -> None:
    """Ajoute les correspondances Stockfish et Wikichess."""
    continuations = _get_wikichess_continuations(state)

    if not continuations:
        return

    best_move = engine.best_move
    compared_moves = [] if best_move is None else [best_move.san]
    compared_moves.extend(alternative.san for alternative in engine.alternatives)
    compared_moves = [move for move in compared_moves if move]

    if not compared_moves:
        return

    lines.extend(["", "Correspondances Wikichess :"])
    lines.extend(
        f"- {move} : {'oui' if move in continuations else 'non'}"
        for move in compared_moves
    )


def _build_engine_context(state: ChessAnalysisState) -> str | None:
    """Construit le contexte Stockfish compact."""
    if _get_unknown_position_context(state) is not None:
        return None

    engine = _get_engine(state)

    if engine is None:
        return None

    lines: list[str] = []
    _append_best_move(lines, engine)
    _append_alternatives(lines, engine)
    _append_wikichess_matches(lines, state, engine)

    return "\n".join(lines) or None


# Position inconnue


def _get_unknown_position_context(state: ChessAnalysisState) -> str | None:
    """Retourne le contexte produit par le nœud de position inconnue."""
    if state.opening is not None:
        return None

    return _normalize_text(state.workflow_context.unknown_position_context)


# Prompt


def _append_prompt_section(
    sections: list[str], title: str, content: str | None, rules: str | None = None
) -> None:
    """Ajoute une section uniquement lorsqu'un contenu existe."""
    if not content:
        return

    section = [f"# {title}", "", content]

    if rules:
        section.extend(["", rules])

    sections.append("\n".join(section))


def _build_system_prompt(state: ChessAnalysisState) -> str:
    """Construit le prompt minimal transmis au modèle."""
    sections = [GENERAL_RULES]
    _append_prompt_section(
        sections, "Wikichess", _build_documents_context(state), WIKICHESS_RULES
    )
    _append_prompt_section(
        sections, "Lichess", _build_opening_context(state), LICHESS_RULES
    )
    _append_prompt_section(
        sections, "Stockfish", _build_engine_context(state), STOCKFISH_RULES
    )
    _append_prompt_section(
        sections,
        "Position inconnue",
        _get_unknown_position_context(state),
        UNKNOWN_POSITION_RULES,
    )
    language = _normalize_language(state.options.response_language)
    sections.append(RESPONSE_RULES.format(language=language))

    return dedent("\n\n".join(sections)).strip()


# Extraction


def _extract_opening_name(state: ChessAnalysisState) -> str | None:
    """Retourne le nom de l'ouverture détectée."""
    if state.opening is None:
        return None

    return _normalize_text(state.opening.opening.name)


def _extract_best_move(state: ChessAnalysisState) -> str | None:
    """Retourne le meilleur coup Stockfish disponible."""
    engine = _get_engine(state)

    if engine is None:
        return None

    best_move = engine.best_move

    if best_move is None:
        return None

    return _normalize_text(best_move.san) or _normalize_text(best_move.uci)


def _get_retrieved_document_count(state: ChessAnalysisState) -> int:
    """Retourne le nombre de documents récupérés."""
    if state.retrieval_context is None:
        return 0

    return state.retrieval_context.total_results


# Réponse de secours


def _append_position_status(sections: list[str], state: ChessAnalysisState) -> None:
    """Ajoute le statut particulier de la position."""
    position = state.position

    if position is None:
        return

    if position.is_checkmate:
        sections.append("La position est indiquée comme un échec et mat.")
        return

    if position.is_stalemate:
        sections.append("La position est indiquée comme un pat.")
        return

    if position.is_check:
        sections.append("Le joueur au trait est indiqué comme étant en échec.")


def _build_fallback_response(state: ChessAnalysisState) -> str:
    """Construit une réponse factuelle sans LLM."""
    sections = [
        (
            "La génération de la réponse pédagogique par le modèle "
            "de langage n'est pas disponible."
        ),
        f"Position FEN analysée : {state.fen.strip()}.",
    ]
    opening_name = _extract_opening_name(state)

    if opening_name is None:
        sections.append("Aucune ouverture connue n'a été détectée.")
    else:
        sections.append(f"Ouverture détectée : {opening_name}.")

    best_move = _extract_best_move(state)

    if best_move is not None:
        sections.append(f"Meilleur coup retourné par Stockfish : {best_move}.")

    _append_position_status(sections, state)
    document_count = _get_retrieved_document_count(state)

    if document_count > 0:
        sections.append(f"{document_count} document(s) Wikichess ont été retrouvés.")

    if state.videos:
        sections.append(
            f"{len(state.videos)} vidéo(s) pédagogique(s) ont été sélectionnée(s)."
        )

    sections.append(
        "Les résultats détaillés restent disponibles dans les autres "
        "champs de la réponse."
    )
    return "\n\n".join(sections)


# Mises à jour


def _build_updated_workflow_context(
    state: ChessAnalysisState, response: str
) -> WorkflowContext:
    """Ajoute la réponse finale au contexte du workflow."""
    return state.workflow_context.model_copy(update={"final_summary": response})


def _build_success_update(
    state: ChessAnalysisState, response: str, workflow_context: WorkflowContext
) -> StateUpdate:
    """Construit la mise à jour après une génération réussie."""
    current_step = WorkflowStep.GENERATE_RESPONSE

    return {
        "status": _get_success_status(state),
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "response": response,
        "workflow_context": workflow_context,
        "errors": list(state.errors),
        "warnings": list(state.warnings),
    }


def _build_warning_update(
    state: ChessAnalysisState,
    warning: WorkflowWarning,
    response: str,
    workflow_context: WorkflowContext,
) -> StateUpdate:
    """Construit la mise à jour avec une réponse de secours."""
    current_step = WorkflowStep.GENERATE_RESPONSE

    return {
        "status": _get_partial_success_status(state),
        "current_step": current_step,
        "completed_steps": append_completed_step(state, current_step),
        "response": response,
        "workflow_context": workflow_context,
        "errors": list(state.errors),
        "warnings": [*state.warnings, warning],
    }


def _build_fallback_update(
    state: ChessAnalysisState, code: str, message: str
) -> StateUpdate:
    """Construit une mise à jour dégradée avec la réponse de secours."""
    response = _build_fallback_response(state)
    workflow_context = _build_updated_workflow_context(state, response)
    warning = WorkflowWarning(
        step=WorkflowStep.GENERATE_RESPONSE, code=code, message=message
    )
    return _build_warning_update(state, warning, response, workflow_context)


# Génération


async def _generate_llm_response(service: LLMService, state: ChessAnalysisState) -> str:
    """Construit le prompt et retourne la réponse non vide du LLM."""
    prompt = _build_system_prompt(state)
    logger.debug("Prompt final préparé : %s caractères.", len(prompt))
    logger.info("Appel de LLMService.generate().")

    generated_response = await service.generate(prompt=prompt)
    response = _normalize_text(generated_response)

    if response is None:
        raise ValueError("LLMService a retourné une réponse vide.")

    logger.info("Retour de LLMService.generate().")
    return response


async def generate_response(
    state: ChessAnalysisState, config: RunnableConfig
) -> StateUpdate:
    """Génère la réponse finale du workflow."""
    current_step = WorkflowStep.GENERATE_RESPONSE
    request_id = state.metadata.request_id or "unknown"

    logger.info("Génération de la réponse finale du workflow %s.", request_id)

    llm_service = _get_llm_service(config)

    if llm_service is None:
        emit_progress(
            step=current_step,
            service=ServiceType.LLM,
            status=WorkflowStepStatus.WARNING,
            message=("LLMService indisponible. Utilisation de la réponse de secours."),
        )

        logger.error("LLMService absent ou invalide dans la configuration LangGraph.")

        return _build_fallback_update(
            state, ERROR_CONFIGURATION, LLM_CONFIGURATION_MESSAGE
        )

    try:
        emit_progress(
            step=current_step,
            service=ServiceType.LLM,
            status=WorkflowStepStatus.RUNNING,
            message="Génération de la réponse pédagogique en cours.",
        )

        response = await _generate_llm_response(llm_service, state)

        emit_progress(
            step=current_step,
            service=ServiceType.LLM,
            status=WorkflowStepStatus.COMPLETED,
            message="Réponse pédagogique générée.",
        )

    except Exception:
        emit_progress(
            step=current_step,
            service=ServiceType.LLM,
            status=WorkflowStepStatus.WARNING,
            message=(
                "Génération LLM impossible. Utilisation de la réponse de secours."
            ),
        )

        logger.exception("Échec de la génération du workflow %s.", request_id)

        return _build_fallback_update(
            state, ERROR_UNEXPECTED, LLM_GENERATION_ERROR_MESSAGE
        )

    workflow_context = _build_updated_workflow_context(state, response)

    logger.info("Réponse finale générée pour le workflow %s.", request_id)

    return _build_success_update(state, response, workflow_context)
