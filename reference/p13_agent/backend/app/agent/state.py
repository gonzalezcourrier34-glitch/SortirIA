"""État partagé du workflow LangGraph.

Ce module définit :

- les types et fonctions injectables utilisés par le workflow ;
- les options pilotant son exécution ;
- les structures construites pendant l'analyse ;
- l'état partagé entre les différents nœuds LangGraph.

Le state constitue le contrat commun du workflow. Chaque nœud lit
uniquement les données dont il a besoin puis retourne une mise à jour
partielle destinée à être fusionnée par LangGraph.

Les services applicatifs sont transmis par le RunnableConfig et ne sont
jamais stockés directement dans l'état.

Ce module ne contient aucune logique métier et ne modifie jamais
directement le déroulement du workflow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis.evaluation import EngineAnalysis, PositionEvaluation
from app.schemas.chess.opening import OpeningDetails
from app.schemas.chess.position import BoardPosition
from app.schemas.common.enums import AnalysisStatus, WorkflowStep
from app.schemas.common.error import WorkflowError, WorkflowWarning
from app.schemas.media.video import Video
from app.schemas.rag.document import RetrievalContext

# Types

EmbeddingVector = list[float]

StateUpdate = dict[str, Any]

# Cet alias distingue explicitement le résultat d'un nœud des autres
# dictionnaires métier manipulés dans le workflow.
NodeResult = StateUpdate


# Fonctions injectables

# Certains composants reçoivent uniquement une fonction d'embedding afin
# de rester indépendants de l'implémentation complète du service.
EmbeddingFunction = Callable[[str], EmbeddingVector | Awaitable[EmbeddingVector]]


# Options


class AnalysisOptions(BaseModel):
    """Options pilotant l'exécution du workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Analyse

    include_stockfish: bool = Field(
        default=True, description="Active l'analyse Stockfish."
    )

    include_opening: bool = Field(
        default=True,
        description=("Active la détection d'une ouverture connue avec Lichess."),
    )

    # Recherche documentaire

    include_context: bool = Field(
        default=True,
        description=("Active la récupération du contexte documentaire dans Milvus."),
    )

    # Ressources pédagogiques

    include_videos: bool = Field(
        default=True, description="Active la recherche de vidéos pédagogiques."
    )

    # Génération

    generate_response: bool = Field(
        default=True,
        description=(
            "Active la génération de la réponse finale avec le modèle de langage."
        ),
    )

    response_language: str = Field(
        default="fr",
        min_length=2,
        max_length=10,
        description="Langue attendue pour la réponse finale.",
    )

    # Persistance

    save_analysis: bool = Field(
        default=True, description="Active la sauvegarde finale dans MongoDB."
    )


# Métadonnées


class WorkflowMetadata(BaseModel):
    """Métadonnées techniques du workflow."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Traçabilité

    request_id: str | None = Field(
        default=None, description="Identifiant de la requête."
    )

    # Exécution

    started_at: datetime | None = Field(
        default=None, description="Date de démarrage du workflow."
    )

    finished_at: datetime | None = Field(
        default=None, description="Date de fin du workflow."
    )

    duration_ms: float | None = Field(
        default=None, ge=0, description="Durée totale du workflow en millisecondes."
    )

    # Embeddings

    embedding_model: str | None = Field(
        default=None, description="Modèle d'embedding utilisé."
    )

    embedding_provider: str | None = Field(
        default=None, description="Fournisseur des embeddings."
    )

    embedding_dimension: int | None = Field(
        default=None, ge=1, description="Dimension des vecteurs d'embedding."
    )

    # Modèle de langage

    llm_model: str | None = Field(
        default=None, description="Modèle de langage utilisé."
    )

    llm_provider: str | None = Field(
        default=None, description="Fournisseur du modèle de langage."
    )

    # Stockfish

    stockfish_depth: int | None = Field(
        default=None, ge=1, description="Profondeur Stockfish utilisée."
    )

    # RAG

    rag_top_k: int | None = Field(
        default=None,
        ge=1,
        description=("Nombre maximal de résultats demandés au moteur RAG."),
    )

    retrieved_document_count: int | None = Field(
        default=None, ge=0, description="Nombre de documents récupérés dans Milvus."
    )


# Contexte


class WorkflowContext(BaseModel):
    """Éléments progressivement préparés pour la génération finale."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Position

    position_summary: str | None = Field(
        default=None,
        description=(
            "Résumé factuel de la position validée construit par le workflow."
        ),
    )

    # Ouverture

    opening_summary: str | None = Field(
        default=None,
        description=("Résumé factuel de l'ouverture détectée par Lichess."),
    )

    opening_context: str | None = Field(
        default=None,
        description=("Contexte pédagogique préparé pour une ouverture connue."),
    )

    # Moteur

    engine_context: str | None = Field(
        default=None, description="Synthèse moteur préparée pour le modèle final."
    )

    # Position inconnue

    unknown_position_context: str | None = Field(
        default=None,
        description=(
            "Contexte pédagogique préparé lorsqu'aucune "
            "ouverture connue n'a été détectée."
        ),
    )

    # Documents

    documents_summary: str | None = Field(
        default=None,
        description=("Synthèse des documents retrouvés par le moteur RAG."),
    )

    # Vidéos

    videos_summary: str | None = Field(
        default=None, description=("Synthèse des ressources vidéo sélectionnées.")
    )

    # Réponse finale

    final_summary: str | None = Field(
        default=None, description="Réponse finale produite par le modèle."
    )


# État partagé


class ChessAnalysisState(BaseModel):
    """État partagé entre tous les nœuds LangGraph."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Entrée

    fen: str = Field(
        ..., min_length=1, description="Position à analyser au format FEN."
    )

    moves: list[str] = Field(
        default_factory=list,
        description=("Historique réel des coups ayant conduit à la position analysée."),
    )

    question: str = Field(
        default="", description="Question facultative associée à l'analyse."
    )

    # Configuration

    options: AnalysisOptions = Field(
        default_factory=AnalysisOptions,
        description="Options déterminant les branches du workflow.",
    )

    # Workflow

    status: AnalysisStatus = Field(
        default=AnalysisStatus.PENDING, description="Statut global de l'analyse."
    )

    current_step: WorkflowStep = Field(
        default=WorkflowStep.VALIDATE_POSITION,
        description="Dernière étape commencée ou exécutée.",
    )

    completed_steps: list[WorkflowStep] = Field(
        default_factory=list, description="Historique des étapes terminées."
    )

    warnings: list[WorkflowWarning] = Field(
        default_factory=list,
        description=("Avertissements non bloquants rencontrés pendant le workflow."),
    )

    errors: list[WorkflowError] = Field(
        default_factory=list, description="Erreurs rencontrées pendant l'exécution."
    )

    # Position

    position: BoardPosition | None = Field(
        default=None, description="Position validée et structurée."
    )

    # Ouverture

    opening: OpeningDetails | None = Field(
        default=None,
        description=(
            "Ouverture détectée et informations associées retournées par Lichess."
        ),
    )

    # Moteur

    engine_analysis: EngineAnalysis | None = Field(
        default=None, description="Analyse brute produite par Stockfish."
    )

    evaluation: PositionEvaluation | None = Field(
        default=None, description="Évaluation enrichie de la position."
    )

    # Recherche documentaire

    retrieval_context: RetrievalContext | None = Field(
        default=None,
        description=("Contexte documentaire construit depuis les résultats Milvus."),
    )

    # Vidéos

    videos: list[Video] = Field(
        default_factory=list, description="Vidéos pédagogiques sélectionnées."
    )

    # Contexte de génération

    workflow_context: WorkflowContext = Field(
        default_factory=WorkflowContext,
        description="Résumés intermédiaires construits par les nœuds.",
    )

    # Réponse

    response: str | None = Field(
        default=None, description="Réponse finale destinée à l'utilisateur."
    )

    # Persistance

    analysis_id: str | None = Field(
        default=None, description=("Identifiant de l'analyse enregistrée dans MongoDB.")
    )

    # Métadonnées

    metadata: WorkflowMetadata = Field(
        default_factory=WorkflowMetadata,
        description=(
            "Informations techniques utilisées pour la supervision et le diagnostic."
        ),
    )


# Compatibilité

# Cet alias maintient la compatibilité avec les modules qui utilisent
# encore l'ancien nom pendant la migration progressive des imports.
AnalysisState = ChessAnalysisState
