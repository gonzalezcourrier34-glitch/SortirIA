"""
Schémas Pydantic utilisés par l'API RAG.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/schemas.py` (commit 18f7ce1).

Ce module définit les structures de données échangées
entre le client et l'API.

Les modèles permettent :
- la validation des requêtes
- la structuration des réponses
- la documentation automatique de l'API
- une cohérence claire entre les services internes et les endpoints

Évolution mémoire courte
------------------------
Les requêtes utilisateur peuvent désormais transporter un `session_id`
afin de rattacher plusieurs tours d'échange à une même conversation
courte gérée par `MemoryService`.

Cette mémoire sert uniquement à :
- maintenir la cohérence conversationnelle
- reformuler les questions dépendantes du contexte récent
- améliorer l'interprétation de la demande utilisateur

Elle ne constitue pas une source factuelle.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RetrievedDocument(BaseModel):
    """
    Représente un document source retourné par le système RAG.

    Dans ce projet, un document correspond à un événement culturel
    provenant d'OpenAgenda.

    Le schéma expose les métadonnées utiles à :
    - l'affichage côté client
    - l'interprétation de la réponse générée
    - l'inspection du retrieval lors du debug
    """

    title: str = Field(
        default="",
        description="Titre de l'événement.",
    )
    location_name: str = Field(
        default="",
        description="Nom du lieu où se déroule l'événement.",
    )
    city: str = Field(
        default="",
        description="Ville de l'événement.",
    )
    region: str = Field(
        default="",
        description="Région associée à l'événement.",
    )
    first_date: str = Field(
        default="",
        description="Date de début de l'événement.",
    )
    last_date: str = Field(
        default="",
        description="Date de fin de l'événement.",
    )
    event_type: str = Field(
        default="",
        description="Type d'événement.",
    )
    url: str = Field(
        default="",
        description="Lien vers l'événement source.",
    )
    price_info: str = Field(
        default="",
        description="Information de tarification simplifiée de l'événement.",
    )
    is_free: bool | None = Field(
        default=None,
        description="Indique si l'événement est identifié comme gratuit.",
    )
    keywords_title: list[str] = Field(
        default_factory=list,
        description="Mots-clés extraits du titre de l'événement.",
    )
    score: float | None = Field(
        default=None,
        description=(
            "Score éventuel du document si le système décide "
            "de l'exposer dans le futur."
        ),
    )


class ConversationMessage(BaseModel):
    """
    Représente un message court de l'historique conversationnel.
    """

    role: str = Field(
        description="Rôle du message dans la conversation : user ou assistant.",
        examples=["user", "assistant"],
    )
    content: str = Field(
        description="Contenu textuel du message.",
    )

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        """
        Vérifie que le rôle est autorisé.
        """
        value = value.strip().lower()
        if value not in {"user", "assistant"}:
            raise ValueError("Le rôle doit être 'user' ou 'assistant'.")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        """
        Vérifie que le contenu n'est pas vide.
        """
        value = value.strip()
        if not value:
            raise ValueError("Le contenu du message ne peut pas être vide.")
        return value


class AskRequest(BaseModel):
    """
    Requête envoyée à l'endpoint `/ask`.

    Ce schéma contient :
    - la question utilisateur
    - un identifiant de session permettant de rattacher
      la requête à une conversation courte en mémoire
    """

    question: str = Field(
        ...,
        min_length=1,
        description="Question utilisateur.",
    )
    session_id: str = Field(
        default="default",
        min_length=1,
        description=(
            "Identifiant de session conversationnelle utilisé pour "
            "la mémoire courte."
        ),
        examples=["default", "session_001", "user_tab_abc123"],
    )

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        """
        Vérifie que la question n'est pas vide après nettoyage.
        """
        value = value.strip()
        if not value:
            raise ValueError("La question ne peut pas être vide.")
        return value

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        """
        Vérifie que l'identifiant de session n'est pas vide.
        """
        value = value.strip()
        if not value:
            raise ValueError("Le session_id ne peut pas être vide.")
        return value


class AskResponse(BaseModel):
    """
    Réponse retournée par l'endpoint `/ask`.

    Cette réponse contient :
    - la question utilisateur
    - la réponse générée par le modèle
    - le nombre de documents retenus
    - la liste structurée des documents utilisés
    - l'identifiant de session associé à la conversation
    """

    question: str = Field(
        description="Question utilisateur.",
    )
    answer: str = Field(
        description="Réponse générée par le système RAG.",
    )
    n_docs: int = Field(
        description="Nombre de documents retenus pour construire la réponse.",
    )
    documents: list[RetrievedDocument] = Field(
        default_factory=list,
        description="Documents utilisés pour construire la réponse.",
    )
    session_id: str = Field(
        default="default",
        description="Identifiant de session conversationnelle.",
    )


class DebugResponse(BaseModel):
    """
    Réponse retournée par l'endpoint `/ask/debug`.

    Ce schéma permet d'inspecter plus finement le comportement
    du pipeline RAG.

    Il expose notamment :
    - la question utilisateur
    - la question reformulée utilisée pour le retrieval
    - l'historique conversationnel court
    - les documents récupérés
    - les contextes textuels transmis au modèle
    - certaines informations de debug sur le filtrage et le ranking
    """

    question: str = Field(
        description="Question utilisateur originale.",
    )
    effective_question: str = Field(
        default="",
        description="Question effectivement utilisée pour le retrieval après reformulation contextuelle éventuelle.",
    )
    session_id: str = Field(
        default="default",
        description="Identifiant de session conversationnelle.",
    )
    history: list[ConversationMessage] = Field(
        default_factory=list,
        description="Historique conversationnel court de la session.",
    )
    answer: str = Field(
        description="Réponse générée par le système RAG.",
    )
    n_docs: int = Field(
        description="Nombre de documents récupérés et retenus.",
    )
    documents: list[RetrievedDocument] = Field(
        default_factory=list,
        description="Documents récupérés avec leurs métadonnées.",
    )
    retrieved_contexts: list[str] = Field(
        default_factory=list,
        description="Liste des contextes textuels utilisés pour la génération.",
    )
    fallback_used: bool = Field(
        default=False,
        description="Indique si un fallback de préfiltrage a été utilisé.",
    )
    filter_debug: dict = Field(
        default_factory=dict,
        description="Informations de debug sur le préfiltrage documentaire.",
    )
    fallback_filter_debug: dict | None = Field(
        default=None,
        description="Informations de debug du fallback de préfiltrage s'il a été déclenché.",
    )
    retrieval_debug: list[dict] = Field(
        default_factory=list,
        description="Informations de debug sur le ranking métier et le retrieval.",
    )

class RebuildRequest(BaseModel):
    """
    Requête envoyée à l'endpoint `/rebuild`.

    Elle permet de relancer le chargement des documents et la
    reconstruction de l'index vectoriel à partir d'une zone donnée.
    """

    zone: str | None = Field(
        default=None,
        description="Zone géographique à charger.",
        examples=["Montpellier"],
    )
    scope: str | None = Field(
        default=None,
        description="Portée géographique utilisée pour filtrer les événements.",
        examples=["city"],
    )


class RebuildResponse(BaseModel):
    """
    Réponse retournée par l'endpoint `/rebuild`.

    Elle résume le résultat de l'opération de reconstruction
    de l'index vectoriel.
    """

    status: str = Field(
        description="Statut global de l'opération.",
    )
    message: str = Field(
        description="Message décrivant le résultat de l'opération.",
    )
    n_docs_indexed: int = Field(
        description="Nombre de documents indexés dans FAISS.",
    )


class HealthResponse(BaseModel):
    """
    Réponse retournée par l'endpoint `/health`.

    Ce schéma permet de vérifier rapidement l'état du service
    ainsi que la disponibilité de l'index vectoriel.
    """

    status: str = Field(
        description="Statut global du service.",
    )
    index_loaded: bool = Field(
        description="Indique si l'index FAISS est chargé en mémoire.",
    )