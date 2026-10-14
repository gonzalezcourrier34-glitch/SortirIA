"""
Service principal du pipeline RAG OpenAgenda.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/rag_service.py` (commit 18f7ce1).
Modifié pour SortirIA : uniquement des modèles Mistral (branche OpenAI retirée, modèles « ministral-* » acceptés).

Ce module orchestre le pipeline complet de recherche et de génération
de réponses à partir d'événements OpenAgenda indexés dans FAISS.

## Responsabilités principales

Cette classe pilote les grandes étapes du système :

- chargement ou reconstruction de l'index vectoriel global
- chargement du corpus documentaire source
- préfiltrage structuré des documents via `FilterService`
- fallback de préfiltrage lorsque les contraintes sont trop strictes
- recherche vectorielle sur le sous-corpus retenu
- injection du score vectoriel dans les métadonnées
- ranking métier via `RetrievalService`
- contrôle final de cohérence documentaire
- formatage du contexte pour le LLM
- génération de la réponse finale
- gestion d'une mémoire conversationnelle courte via `MemoryService`
- reformulation contextuelle des questions selon l'historique récent
- construction des objets API
- écriture des traces JSONL

## Philosophie du pipeline

Le service ne contient pas la logique métier fine du filtrage lexical
ou du scoring métier. Ces responsabilités restent déléguées à :

- `FilterService`
- `RetrievalService`

`RAGService` agit comme chef d'orchestre du pipeline. Il décide :

- quand lancer les différentes briques
- quand déclencher un fallback
- quels documents transmettre au ranking
- quels documents conserver pour la réponse finale

## Notes

Deux représentations textuelles coexistent volontairement :

- `search_text` :
  texte riche destiné à l'embedding et à la recherche vectorielle
- `format_doc()` :
  fiche compacte destinée au LLM
- `format_doc_debug()` :
  fiche détaillée destinée au debug / affichage

Cette séparation permet :

- un retrieval plus riche sémantiquement
- un contexte LLM plus compact
- un affichage debug plus lisible et plus complet

## Mémoire conversationnelle

La mémoire courte sert uniquement à :

- maintenir la cohérence conversationnelle entre plusieurs tours
- reformuler les questions elliptiques ou dépendantes du contexte
- améliorer la qualité du retrieval en produisant une question autonome

Les faits utilisés dans la réponse finale doivent toujours provenir
des documents récupérés dans le contexte, jamais de l'historique seul.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings

from app.config import (
    DEFAULT_SCOPE,
    DEFAULT_ZONE,
    EMBEDDING_MODEL,
    FAISS_INDEX_DIR,
    LLM_MODEL,
)
from app.document_service import load_documents
from app.filter_service import FilterService
from app.memory_service import MemoryService
from app.retrieval_service import RetrievalService
from app.schemas import AskResponse, RetrievedDocument
from app.trace_service import TraceService


# Préfixes des modèles Mistral acceptés (ajout SortirIA).
MISTRAL_MODEL_PREFIXES: tuple[str, ...] = ("mistral", "ministral", "magistral", "open-mistral")


def is_mistral_model(model_name: str) -> bool:
    """Vrai si le nom désigne un modèle Mistral (« mistral-embed », « ministral-8b-latest »...)."""

    return model_name.strip().lower().startswith(MISTRAL_MODEL_PREFIXES)


class RAGService:
    """
    Service principal du pipeline RAG.

    Cette classe orchestre l'ensemble du flux de traitement :

    1. disponibilité des documents et de l'index
    2. préfiltrage structuré
    3. fallback éventuel si le filtrage est trop dur
    4. reformulation contextuelle via mémoire courte
    5. recherche vectorielle locale
    6. ranking métier
    7. contrôle final des documents
    8. génération de la réponse
    9. mise à jour de la mémoire conversationnelle
    10. traçage complet

    Le service ne porte pas la logique métier détaillée du filtrage
    ou du scoring. Il pilote uniquement le flux global.

    Les modèles d'embeddings et de génération sont configurables via
    des variables d'environnement afin de permettre un changement
    de provider sans modifier le code métier.
    """

    EMPTY_ANSWER = "Je n'ai trouvé aucun événement correspondant dans les données disponibles."
    TEMPORARY_LLM_UNAVAILABLE_ANSWER = (
        "Le modèle est momentanément indisponible. Merci de réessayer dans quelques instants."
    )

    def __init__(
        self,
        documents: list[Document] | None = None,
        index_dir: str | Path | None = None,
        embedding_model: str = EMBEDDING_MODEL,
        llm_model: str = LLM_MODEL,
        top_k_retrieval: int = 10,
        top_k_final: int = 3,
        zone: str = DEFAULT_ZONE,
        scope: str = DEFAULT_SCOPE,
        trace_enabled: bool = True,
        trace_file: str | Path = "artifacts/rag_trace.jsonl",
    ) -> None:
        """
        Initialise le service RAG et ses dépendances principales.

        Parameters
        ----------
        documents : list[Document] | None
            Corpus documentaire déjà chargé en mémoire, si disponible.
        index_dir : str | Path | None
            Répertoire contenant l'index FAISS global.
        embedding_model : str
            Nom du modèle d'embeddings à utiliser.
            Peut provenir des variables d'environnement.
        llm_model : str
            Nom du modèle de génération / reformulation à utiliser.
            Peut provenir des variables d'environnement.
        top_k_retrieval : int
            Nombre maximal de documents candidats retenus après retrieval vectoriel.
        top_k_final : int
            Nombre maximal de documents conservés pour la réponse finale.
        zone : str
            Zone par défaut utilisée dans le préfiltrage.
        scope : str
            Niveau de portée géographique par défaut.
        trace_enabled : bool
            Active ou non l'écriture des traces JSONL.
        trace_file : str | Path
            Fichier de sortie des traces.
        """
        self.documents = documents or []
        self.index_dir = Path(index_dir) if index_dir else FAISS_INDEX_DIR
        self.top_k_retrieval = top_k_retrieval
        self.top_k_final = top_k_final
        self.zone = zone
        self.scope = scope

        # Noms de modèles conservés pour le debug et les traces.
        self.embedding_model_name = embedding_model
        self.llm_model_name = llm_model

        # Construction dynamique des composants.
        self.embeddings = self._build_embeddings(embedding_model)
        self.llm = self._build_llm(llm_model)

        # Vectorstore FAISS global chargé en mémoire.
        self.vectorstore: FAISS | None = None

        # Briques spécialisées du pipeline.
        self.filter_service = FilterService()
        self.retrieval_service = RetrievalService()
        self.memory_service = MemoryService()
        self.trace_service = TraceService(
            trace_file=trace_file,
            enabled=trace_enabled,
        )

        # Prompt principal de génération.
        # Cette version reste compacte mais réintroduit les contraintes
        # de cardinalité afin de forcer le LLM à lister tous les événements
        # présents dans le contexte final.
        self.prompt = ChatPromptTemplate.from_template(
            """
Tu es un assistant spécialisé dans les événements culturels.

Règles :
- Utilise uniquement le contexte fourni.
- N'invente aucun événement.
- Si le contexte est vide, réponds exactement :
  "Je n'ai trouvé aucun événement correspondant dans les données disponibles."

Format obligatoire :
Liste d'événements pour {question} :
- Titre de l'événement (date : ..., lieu : ...)
- Titre de l'événement (date : ..., lieu : ...)

Contraintes :
- Dates au format AAAA/MM/JJ
- Intervalles au format AAAA/MM/JJ au AAAA/MM/JJ
- Tu dois lister tous les événements présents dans le contexte final
- Tu ne dois en oublier aucun
- Chaque événement du contexte doit apparaître une seule fois
- Le nombre de lignes de la liste doit correspondre exactement au nombre d'événements présents dans le contexte final
- Aucune introduction ni conclusion

Historique récent :
{history}

Question :
{question}

Contexte :
{context}
""".strip()
        )

        # Prompt de reformulation.
        # Il n'est utilisé que si la question semble réellement dépendre
        # de l'historique récent.
        self.rewrite_prompt = ChatPromptTemplate.from_template(
            """
Tu reçois :
- un historique récent de conversation
- une nouvelle question utilisateur

Ta mission :
- reformuler la nouvelle question pour qu'elle soit autonome
- conserver strictement l'intention de l'utilisateur
- ne rien inventer
- si la question est déjà autonome, retourne-la telle quelle ou presque

Historique :
{history}

Nouvelle question :
{question}

Question reformulée :
""".strip()
        )

        # Chaînes LangChain.
        self.chain = self.prompt | self.llm | StrOutputParser()
        self.rewrite_chain = self.rewrite_prompt | self.llm | StrOutputParser()

    # ------------------------------------------------------------------
    # Construction dynamique des modèles
    # ------------------------------------------------------------------

    def _build_embeddings(self, model_name: str) -> Embeddings:
        """
        Construit l'objet d'embeddings Mistral demandé.

        Modifié pour SortirIA : tout le projet utilise Mistral ; la branche
        OpenAI du POC a été retirée.
        """
        if not is_mistral_model(model_name):
            raise ValueError(
                f"Modèle d'embeddings non supporté : {model_name}. "
                "SortirIA utilise uniquement Mistral (ex. 'mistral-embed')."
            )
        return MistralAIEmbeddings(model=model_name)

    def _build_llm(self, model_name: str) -> BaseChatModel:
        """
        Construit le LLM Mistral demandé (Mistral Small, Ministral...).

        Modifié pour SortirIA : branche OpenAI retirée, et les modèles
        « ministral-* » sont acceptés (l'ancien test startswith("mistral")
        les refusait).
        """
        if not is_mistral_model(model_name):
            raise ValueError(
                f"LLM non supporté : {model_name}. "
                "SortirIA utilise uniquement Mistral (ex. 'mistral-small-latest')."
            )
        return ChatMistralAI(
            model=model_name,
            temperature=0.2,
        )

    # ------------------------------------------------------------------
    # Gestion de l'index et des documents
    # ------------------------------------------------------------------

    def is_index_loaded(self) -> bool:
        """
        Indique si le vectorstore FAISS global est déjà chargé en mémoire.
        """
        return self.vectorstore is not None

    def set_documents(self, documents: list[Document]) -> None:
        """
        Injecte explicitement une nouvelle liste de documents dans le service.
        """
        self.documents = documents

    def _ensure_documents_loaded(self) -> None:
        """
        Charge les documents si le corpus mémoire est vide.
        """
        if self.documents:
            return

        self.documents = load_documents(
            zone=self.zone,
            scope=self.scope,
        )

    def ensure_index_ready(self) -> None:
        """
        S'assure que l'index FAISS et le corpus documentaire sont prêts.
        """
        if self.vectorstore is not None:
            self._ensure_documents_loaded()
            return

        if self.index_dir.exists() and any(self.index_dir.iterdir()):
            self.load_index()
            self._ensure_documents_loaded()
            return

        if not self.documents:
            self._ensure_documents_loaded()

        if not self.documents:
            raise ValueError(
                "Aucun index FAISS disponible et aucun document fourni pour le construire."
            )

        self.build_index()

    def _build_docs_for_vector_index(self, docs: list[Document]) -> list[Document]:
        """
        Prépare les documents à injecter dans l'index vectoriel.

        Le `search_text` est privilégié car il est plus riche pour l'embedding
        que le `page_content` destiné au LLM.
        """
        docs_for_index: list[Document] = []

        for doc in docs:
            md = dict(doc.metadata or {})
            search_text = md.get("search_text", "") or doc.page_content

            docs_for_index.append(
                Document(
                    page_content=search_text,
                    metadata=md,
                )
            )

        return docs_for_index

    def _execute_with_retry(
        self,
        func: Callable[[], Any],
        max_retries: int = 5,
    ) -> Any:
        """
        Exécute une fonction avec retry exponentiel sur erreur 429.

        Ce helper est utilisé principalement pour les embeddings et la
        construction d'index. Les appels LLM de rewrite / génération
        sont gérés séparément avec un fallback métier.
        """
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as exc:
                message = str(exc)
                if "429" in message and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                raise

    def build_index(self) -> int:
        """
        Construit l'index FAISS global à partir du corpus documentaire.
        """
        if not self.documents:
            raise ValueError(
                "Impossible de construire l'index : aucun document n'est disponible."
            )

        docs_for_index = self._build_docs_for_vector_index(self.documents)

        batch_size = 32
        pause_seconds = 0.3
        vectorstore: FAISS | None = None

        for i in range(0, len(docs_for_index), batch_size):
            batch = docs_for_index[i : i + batch_size]

            if vectorstore is None:
                vectorstore = self._execute_with_retry(
                    lambda: FAISS.from_documents(
                        documents=batch,
                        embedding=self.embeddings,
                    )
                )
            else:
                self._execute_with_retry(
                    lambda: vectorstore.add_documents(batch)
                )

            time.sleep(pause_seconds)

        self.vectorstore = vectorstore

        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.vectorstore.save_local(str(self.index_dir))

        return len(self.documents)

    def load_index(self) -> None:
        """
        Charge l'index FAISS depuis le disque.
        """
        if not self.index_dir.exists() or not any(self.index_dir.iterdir()):
            raise FileNotFoundError(
                f"Index FAISS introuvable dans le dossier : {self.index_dir}"
            )

        self.vectorstore = FAISS.load_local(
            folder_path=str(self.index_dir),
            embeddings=self.embeddings,
            allow_dangerous_deserialization=True,
        )

    def rebuild_index(self, documents: list[Document]) -> int:
        """
        Remplace le corpus courant puis reconstruit complètement l'index.
        """
        self.set_documents(documents)
        return self.build_index()

    def _build_local_vectorstore(self, docs: list[Document]) -> FAISS:
        """
        Construit un FAISS local temporaire sur un sous-corpus filtré.
        """
        docs_for_index = self._build_docs_for_vector_index(docs)

        return FAISS.from_documents(
            documents=docs_for_index,
            embedding=self.embeddings,
        )

    # ------------------------------------------------------------------
    # Mémoire conversationnelle
    # ------------------------------------------------------------------

    def _get_recent_history_text(
        self,
        session_id: str,
        max_messages: int | None = None,
    ) -> str:
        """
        Retourne l'historique récent formaté en texte pour le prompt.
        """
        return self.memory_service.format_history_for_prompt(
            session_id=session_id,
            max_messages=max_messages,
        ).strip()

    def _get_recent_history_messages(
        self,
        session_id: str,
        max_messages: int | None = None,
    ) -> list[dict[str, str]]:
        """
        Retourne l'historique récent sous forme structurée.
        """
        return self.memory_service.get_recent_messages(
            session_id=session_id,
            max_messages=max_messages,
        )

    def _needs_rewrite(self, question: str) -> bool:
        """
        Détecte si la question semble dépendre de l'historique récent.

        L'objectif est d'éviter un appel LLM de reformulation pour les
        questions déjà autonomes, afin de réduire la latence, le coût
        et le risque de 429 sur le modèle conversationnel.
        """
        q = (question or "").strip().lower()

        if not q:
            return False

        short_followups = {
            "et à sete ?",
            "et à sète ?",
            "et à montpellier ?",
            "et pour les enfants ?",
            "et les gratuites ?",
            "et les payantes ?",
            "et demain ?",
            "et ce week-end ?",
            "et ce weekend ?",
        }

        if q in short_followups:
            return True

        followup_markers = [
            "et ",
            "aussi ",
            "sinon ",
            "plutôt ",
            "plutot ",
            "dans ce cas ",
            "pour les ",
            "pour la ",
            "pour le ",
            "les gratuites",
            "les payantes",
            "demain",
            "ce week-end",
            "ce weekend",
        ]

        if len(q) < 35 and any(q.startswith(marker) for marker in followup_markers):
            return True

        pronouns = ["ceux", "celles", "ce type", "ça", "cela", "les mêmes", "les memes"]
        if any(p in q for p in pronouns):
            return True

        return False

    def _rewrite_question_with_history(self, question: str, session_id: str) -> str:
        """
        Reformule la question à partir de l'historique récent si nécessaire.

        En cas d'échec 429 sur le modèle, la question brute est conservée
        afin de poursuivre le pipeline sans bloquer toute la requête.
        """
        history = self._get_recent_history_text(
            session_id=session_id,
            max_messages=6,
        )

        if not history:
            return question.strip()

        try:
            rewritten = self.rewrite_chain.invoke(
                {
                    "history": history,
                    "question": question,
                }
            ).strip()
            return rewritten or question.strip()
        except Exception as exc:
            message = str(exc)
            if "429" in message:
                return question.strip()
            raise

    # ------------------------------------------------------------------
    # Sérialisation / debug
    # ------------------------------------------------------------------

    def _serialize_for_json(self, value: Any) -> Any:
        """
        Convertit des objets potentiellement non sérialisables en JSON.
        """
        if isinstance(value, (date, datetime)):
            return value.isoformat()

        if isinstance(value, dict):
            return {
                str(key): self._serialize_for_json(val)
                for key, val in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [self._serialize_for_json(item) for item in value]

        return value

    def _sanitize_filter_debug(self, debug_data: dict[str, Any] | None) -> dict[str, Any] | None:
        """
        Réduit les informations de debug filtre à un format compact et sérialisable.
        """
        if debug_data is None:
            return None

        return {
            "filters": self._serialize_for_json(debug_data.get("filters", {})),
            "n_input_docs": debug_data.get("n_input_docs", 0),
            "n_after_city": debug_data.get("n_after_city", 0),
            "n_after_date": debug_data.get("n_after_date", 0),
            "n_after_type": debug_data.get("n_after_type", 0),
            "n_after_music": debug_data.get("n_after_music", 0),
            "n_after_cultural": debug_data.get("n_after_cultural", 0),
            "n_after_audience": debug_data.get("n_after_audience", 0),
            "n_after_duration": debug_data.get("n_after_duration", 0),
            "n_after_price": debug_data.get("n_after_price", 0),
        }

    # ------------------------------------------------------------------
    # Helpers de trace
    # ------------------------------------------------------------------

    def _doc_to_trace_row(self, doc: Document) -> dict[str, Any]:
        """
        Transforme un document en ligne légère de trace.
        """
        md = doc.metadata or {}

        return {
            "title": md.get("title", ""),
            "location_name": md.get("location_name", ""),
            "city": md.get("city", ""),
            "region": md.get("region", ""),
            "first_date": md.get("first_date", ""),
            "last_date": md.get("last_date", ""),
            "event_type": md.get("canonical_event_type", "") or md.get("event_type", ""),
            "music_genre": md.get("music_genre", ""),
            "price_info": md.get("price_info", ""),
            "is_free": md.get("is_free"),
            "vector_score": md.get("vector_score"),
            "final_score": md.get("final_score"),
            "diversified_score": md.get("diversified_score"),
            "url": md.get("source_url", "") or md.get("url", ""),
        }

    def _trace_pipeline(
        self,
        *,
        question: str,
        effective_question: str,
        session_id: str,
        history: list[dict[str, str]],
        filter_debug: dict[str, Any],
        fallback_filter_debug: dict[str, Any] | None,
        prefiltered_docs: list[Document],
        raw_docs: list[Document],
        ranked_docs: list[Document],
        final_docs: list[Document],
        context: str,
        answer: str,
        fallback_used: bool,
        generation_prompt: str | None = None,
        rewrite_prompt_rendered: str | None = None,
    ) -> None:
        """
        Enregistre une trace complète du pipeline pour analyse ultérieure.
        """
        payload = {
            "question": question,
            "effective_question": effective_question,
            "session_id": session_id,
            "history": history,
            "zone": self.zone,
            "scope": self.scope,
            "embedding_model": self.embedding_model_name,
            "llm_model": self.llm_model_name,
            "top_k_retrieval": self.top_k_retrieval,
            "top_k_final": self.top_k_final,
            "fallback_used": fallback_used,
            "n_input_docs": len(self.documents),
            "n_prefiltered_docs": len(prefiltered_docs),
            "n_raw_docs": len(raw_docs),
            "n_ranked_docs": len(ranked_docs),
            "n_final_docs": len(final_docs),
            "filter_debug": self._sanitize_filter_debug(filter_debug),
            "fallback_filter_debug": self._sanitize_filter_debug(fallback_filter_debug),
            "prefiltered_docs": [self._doc_to_trace_row(doc) for doc in prefiltered_docs[:50]],
            "raw_docs": [self._doc_to_trace_row(doc) for doc in raw_docs[:50]],
            "ranked_docs": [self._doc_to_trace_row(doc) for doc in ranked_docs[:50]],
            "final_docs": [self._doc_to_trace_row(doc) for doc in final_docs[:50]],
            "context": context,
            "generation_prompt": generation_prompt,
            "rewrite_prompt": rewrite_prompt_rendered,
            "answer": answer,
        }

        self.trace_service.write_trace(self._serialize_for_json(payload))

    def _build_generation_prompt_text(
        self,
        question: str,
        context: str,
        history: str,
    ) -> str:
        """
        Rend le prompt final de génération tel qu'il est construit.

        Cette méthode ne déclenche aucun appel LLM. Elle sert uniquement
        à produire une version texte du prompt pour la traçabilité.
        """
        prompt_value = self.prompt.invoke(
            {
                "question": question,
                "context": context,
                "history": history,
            }
        )

        try:
            return prompt_value.to_string()
        except Exception:
            return str(prompt_value)

    def _build_rewrite_prompt_text(
        self,
        question: str,
        history: str,
    ) -> str:
        """
        Rend le prompt de reformulation tel qu'il est construit.

        Cette méthode ne déclenche aucun appel LLM. Elle sert uniquement
        à produire une version texte du prompt pour la traçabilité.
        """
        prompt_value = self.rewrite_prompt.invoke(
            {
                "question": question,
                "history": history,
            }
        )

        try:
            return prompt_value.to_string()
        except Exception:
            return str(prompt_value)

    # ------------------------------------------------------------------
    # Retrieval interne
    # ------------------------------------------------------------------

    def _attach_vector_scores(
        self,
        results: list[tuple[Document, float]],
    ) -> list[Document]:
        """
        Injecte les scores vectoriels dans les métadonnées des documents.
        """
        docs: list[Document] = []

        for doc, score in results:
            if doc.metadata is None:
                doc.metadata = {}

            doc.metadata["vector_score"] = float(score)
            docs.append(doc)

        return docs

    def _is_reliable_document(self, doc: Document) -> bool:
        """
        Vérifie qu'un document semble suffisamment fiable pour la réponse finale.
        """
        md = doc.metadata or {}

        has_title = bool(md.get("title"))
        has_date = bool(md.get("first_date"))
        has_location = bool(md.get("location_name") or md.get("city"))
        has_url = bool(md.get("source_url") or md.get("url"))
        content_quality = md.get("content_quality", 0)

        try:
            content_quality = int(content_quality)
        except (TypeError, ValueError):
            content_quality = 0

        return has_title and has_date and has_location and (has_url or content_quality >= 4)

    def _post_filter_ranked_docs(self, docs: list[Document]) -> list[Document]:
        """
        Retient les documents fiables après ranking.
        """
        if not docs:
            return []

        reliable_docs = [doc for doc in docs if self._is_reliable_document(doc)]

        if reliable_docs:
            return reliable_docs[: self.top_k_final]

        return docs[: self.top_k_final]

    def _run_vector_retrieval(self, question: str, candidate_docs: list[Document]) -> list[Document]:
        """
        Lance la recherche vectorielle sur le sous-corpus préfiltré.
        """
        if not candidate_docs:
            return []

        local_vectorstore = self._build_local_vectorstore(candidate_docs)

        raw_results = local_vectorstore.similarity_search_with_score(
            query=question,
            k=min(self.top_k_retrieval, len(candidate_docs)),
        )

        if not raw_results:
            return []

        return self._attach_vector_scores(raw_results)

    def _build_relaxed_question(self, question: str) -> str:
        """
        Construit une version relâchée de la question pour le fallback.
        """
        return question.strip()

    def _prefilter_with_fallback(
        self,
        question: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[Document], bool]:
        """
        Préfiltre les documents avec une stratégie de fallback progressive.

        Retourne `fallback_used=True` uniquement si le fallback a réellement
        permis de récupérer des documents.
        """
        filter_debug = self.filter_service.filter_documents_with_debug(
            question=question,
            docs=self.documents,
            default_city=self.zone,
        )

        prefiltered_docs = filter_debug.get("docs", [])
        if prefiltered_docs:
            return filter_debug, None, prefiltered_docs, False

        fallback_question = self._build_relaxed_question(question)
        fallback_filter_debug = self.filter_service.filter_documents_with_debug(
            question=fallback_question,
            docs=self.documents,
            default_city=None,
        )

        fallback_docs = fallback_filter_debug.get("docs", [])
        if fallback_docs:
            return filter_debug, fallback_filter_debug, fallback_docs, True

        return filter_debug, fallback_filter_debug, [], False

    # ------------------------------------------------------------------
    # API retrieval
    # ------------------------------------------------------------------

    def retrieve(self, question: str, session_id: str = "default") -> list[Document]:
        """
        Retourne directement les documents finaux retenus par le pipeline.
        """
        if not question or not question.strip():
            raise ValueError("La question utilisateur ne peut pas être vide.")

        result = self._run_pipeline(question, session_id=session_id)
        return result["docs"]

    # ------------------------------------------------------------------
    # Formatage du contexte
    # ------------------------------------------------------------------

    def format_doc(self, doc: Document, index: int | None = None) -> str:
        """
        Formate un document en fiche compacte lisible par le LLM.

        Cette représentation est optimisée pour réduire les tokens :
        - description tronquée plus court
        - URL exclue du contexte LLM
        - seules les informations utiles à la génération sont conservées
        """
        md = doc.metadata or {}

        lines: list[str] = []

        if index is not None:
            lines.append(f"Événement {index}")

        description = (md.get("description", "") or "")[:180]

        lines.extend(
            [
                f"Titre : {md.get('title', '')}",
                f"Lieu : {md.get('location_name', '')}",
                f"Ville : {md.get('city', '')}",
                f"Région : {md.get('region', '')}",
                f"Date de début : {md.get('first_date', '')}",
                f"Date de fin : {md.get('last_date', '')}",
                f"Type : {md.get('canonical_event_type', '') or md.get('event_type', '')}",
                f"Genre musical : {md.get('music_genre', '')}",
                f"Tarification : {md.get('price_info', '')}",
                f"Description : {description}",
            ]
        )

        return "\n".join(lines)

    def format_doc_debug(self, doc: Document, index: int | None = None) -> str:
        """
        Formate un document en fiche détaillée pour le debug / l'affichage.

        Cette représentation est plus riche que celle utilisée pour le LLM :
        - description plus longue
        - URL conservée
        - adaptée à l'analyse humaine du pipeline
        """
        md = doc.metadata or {}

        lines: list[str] = []

        if index is not None:
            lines.append(f"Événement {index}")

        description = (md.get("description", "") or "")[:500]

        lines.extend(
            [
                f"Titre : {md.get('title', '')}",
                f"Lieu : {md.get('location_name', '')}",
                f"Ville : {md.get('city', '')}",
                f"Région : {md.get('region', '')}",
                f"Date de début : {md.get('first_date', '')}",
                f"Date de fin : {md.get('last_date', '')}",
                f"Type : {md.get('canonical_event_type', '') or md.get('event_type', '')}",
                f"Genre musical : {md.get('music_genre', '')}",
                f"Tarification : {md.get('price_info', '')}",
                f"Description : {description}",
                f"URL : {md.get('source_url', '') or md.get('url', '')}",
            ]
        )

        return "\n".join(lines)

    def format_docs(self, docs: list[Document]) -> str:
        """
        Concatène plusieurs documents compacts en un seul contexte texte.

        Cette méthode est destinée au contexte envoyé au LLM.
        """
        if not docs:
            return ""

        return "\n\n".join(
            self.format_doc(doc, idx)
            for idx, doc in enumerate(docs, start=1)
        )

    def format_docs_list(self, docs: list[Document]) -> list[str]:
        """
        Retourne la liste des fiches compactes, une par document.

        Cette méthode peut servir lorsque l'on souhaite inspecter la version
        strictement envoyée au LLM.
        """
        if not docs:
            return []

        return [
            self.format_doc(doc, idx)
            for idx, doc in enumerate(docs, start=1)
        ]

    def format_docs_list_debug(self, docs: list[Document]) -> list[str]:
        """
        Retourne la liste des fiches détaillées, une par document.

        Cette méthode est destinée à l'affichage debug / utilisateur
        afin de conserver plus d'informations que dans le contexte LLM.
        """
        if not docs:
            return []

        return [
            self.format_doc_debug(doc, idx)
            for idx, doc in enumerate(docs, start=1)
        ]

    # ------------------------------------------------------------------
    # Conversion API
    # ------------------------------------------------------------------

    def _build_retrieved_document(self, doc: Document) -> RetrievedDocument:
        """
        Convertit un Document LangChain en schéma API RetrievedDocument.
        """
        md = doc.metadata or {}

        raw_score = md.get("final_score", md.get("vector_score"))
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None

        return RetrievedDocument(
            title=md.get("title", ""),
            location_name=md.get("location_name", ""),
            city=md.get("city", ""),
            region=md.get("region", ""),
            first_date=md.get("first_date", ""),
            last_date=md.get("last_date", ""),
            event_type=md.get("canonical_event_type", "") or md.get("event_type", ""),
            url=md.get("source_url", "") or md.get("url", ""),
            price_info=md.get("price_info", ""),
            is_free=md.get("is_free"),
            keywords_title=md.get("keywords_title", []),
            score=score,
        )

    def build_retrieved_documents(
        self,
        docs: list[Document],
    ) -> list[RetrievedDocument]:
        """
        Convertit une liste de Documents en objets API.
        """
        return [self._build_retrieved_document(doc) for doc in docs]

    # ------------------------------------------------------------------
    # Génération
    # ------------------------------------------------------------------

    def generate_answer(
        self,
        question: str,
        docs: list[Document],
        session_id: str = "default",
    ) -> str:
        """
        Génère la réponse finale du LLM à partir du contexte documentaire.

        En cas de 429 sur le modèle, une réponse de dégradation contrôlée
        est renvoyée afin d'éviter un 500 serveur brut.
        """
        if not docs:
            return self.EMPTY_ANSWER

        context = self.format_docs(docs).strip()
        if not context:
            return self.EMPTY_ANSWER

        history = self._get_recent_history_text(
            session_id=session_id,
            max_messages=6,
        )

        try:
            answer = self.chain.invoke(
                {
                    "question": question,
                    "context": context,
                    "history": history,
                }
            )
            answer = answer.strip()
            return answer or self.EMPTY_ANSWER
        except Exception as exc:
            message = str(exc)
            if "429" in message:
                return self.TEMPORARY_LLM_UNAVAILABLE_ANSWER
            raise

    # ------------------------------------------------------------------
    # Pipeline complet
    # ------------------------------------------------------------------

    def _run_pipeline(self, question: str, session_id: str = "default") -> dict[str, Any]:
        """
        Exécute le pipeline complet de bout en bout.
        """
        if not question or not question.strip():
            raise ValueError("La question utilisateur ne peut pas être vide.")

        self.ensure_index_ready()

        # Historique récupéré une seule fois au début du pipeline.
        # Il sert à décider si un rewrite est réellement utile.
        history_text = self._get_recent_history_text(
            session_id=session_id,
            max_messages=6,
        )

        # Reformulation conditionnelle.
        # On évite un second appel LLM si la question est déjà autonome.
        if history_text and self._needs_rewrite(question):
            effective_question = self._rewrite_question_with_history(question, session_id)
        else:
            effective_question = question.strip()

        # Préfiltrage structuré + fallback éventuel.
        filter_debug, fallback_filter_debug, prefiltered_docs, fallback_used = (
            self._prefilter_with_fallback(effective_question)
        )

        raw_docs: list[Document] = []
        ranked_docs: list[Document] = []
        final_docs: list[Document] = []

        if prefiltered_docs:
            raw_docs = self._run_vector_retrieval(
                question=effective_question,
                candidate_docs=prefiltered_docs,
            )

            if raw_docs:
                ranked_docs = self.retrieval_service.rank_documents(
                    question=effective_question,
                    raw_docs=raw_docs,
                    top_k=min(self.top_k_retrieval, len(raw_docs)),
                )

                final_docs = self._post_filter_ranked_docs(ranked_docs)

        # Construction du contexte final pour le LLM.
        context = self.format_docs(final_docs)

        # Prompt de rewrite tracé seulement si un vrai historique existe
        # et si la logique de rewrite est potentiellement activable.
        rewrite_prompt_rendered = None
        if history_text and self._needs_rewrite(question):
            rewrite_prompt_rendered = self._build_rewrite_prompt_text(
                question=question,
                history=history_text,
            )

        # Génération finale.
        answer = self.generate_answer(question, final_docs, session_id)
        retrieved_documents = self.build_retrieved_documents(final_docs)

        # Pour l'affichage debug / utilisateur, on conserve une version détaillée
        # des documents finaux afin de ne pas perdre d'informations utiles.
        retrieved_contexts = self.format_docs_list_debug(final_docs)

        # Prompt de génération tracé seulement si des documents finaux existent.
        generation_prompt = None
        if final_docs:
            generation_prompt = self._build_generation_prompt_text(
                question=question,
                context=context,
                history=history_text,
            )

        retrieval_debug = self.retrieval_service.rank_documents_with_scores(
            question=effective_question,
            raw_docs=raw_docs,
            top_k=len(raw_docs) if raw_docs else self.top_k_final,
        )

        # Mise à jour de la mémoire courte après la réponse.
        self.memory_service.append_turn(
            session_id=session_id,
            user_message=question,
            assistant_message=answer,
        )

        history = self._get_recent_history_messages(
            session_id=session_id,
            max_messages=6,
        )

        # Traçage complet.
        self._trace_pipeline(
            question=question,
            effective_question=effective_question,
            session_id=session_id,
            history=history,
            filter_debug=filter_debug,
            fallback_filter_debug=fallback_filter_debug,
            prefiltered_docs=prefiltered_docs,
            raw_docs=raw_docs,
            ranked_docs=ranked_docs,
            final_docs=final_docs,
            context=context,
            answer=answer,
            fallback_used=fallback_used,
            generation_prompt=generation_prompt,
            rewrite_prompt_rendered=rewrite_prompt_rendered,
        )

        return {
            "question": question,
            "effective_question": effective_question,
            "session_id": session_id,
            "history": history,
            "answer": answer,
            "docs": final_docs,
            "retrieved_documents": retrieved_documents,
            "retrieved_contexts": retrieved_contexts,
            "filter_debug": self._sanitize_filter_debug(filter_debug),
            "fallback_filter_debug": self._sanitize_filter_debug(fallback_filter_debug),
            "fallback_used": fallback_used,
            "retrieval_debug": self._serialize_for_json(retrieval_debug),
        }

    def ask(self, question: str, session_id: str = "default") -> AskResponse:
        """
        API principale orientée production.

        Retourne la réponse finale typée sous forme d'AskResponse.
        """
        result = self._run_pipeline(question, session_id=session_id)

        return AskResponse(
            question=result["question"],
            answer=result["answer"],
            n_docs=len(result["docs"]),
            documents=result["retrieved_documents"],
            session_id=result["session_id"],
        )

    def ask_debug(self, question: str, session_id: str = "default") -> dict[str, Any]:
        """
        Variante debug de l'API ask.

        Retourne la réponse ainsi que les états intermédiaires utiles
        à l'analyse du pipeline.
        """
        result = self._run_pipeline(question, session_id=session_id)

        retrieved_documents = result.get("retrieved_documents", [])
        retrieved_contexts = result.get("retrieved_contexts", [])
        retrieval_debug = result.get("retrieval_debug", [])
        filter_debug = result.get("filter_debug", {})
        fallback_filter_debug = result.get("fallback_filter_debug")
        fallback_used = result.get("fallback_used", False)

        response = {
            "question": result.get("question", question),
            "effective_question": result.get("effective_question", question),
            "session_id": result.get("session_id", session_id),
            "history": result.get("history", []),
            "answer": result.get("answer", ""),
            "n_docs": len(retrieved_documents),
            "documents": [
                doc.model_dump() if hasattr(doc, "model_dump") else doc.dict()
                for doc in retrieved_documents
            ],
            "retrieved_contexts": retrieved_contexts if isinstance(retrieved_contexts, list) else [],
            "fallback_used": fallback_used,
            "filter_debug": filter_debug if isinstance(filter_debug, dict) else {},
            "retrieval_debug": retrieval_debug if isinstance(retrieval_debug, list) else [],
        }

        if fallback_filter_debug is not None:
            response["fallback_filter_debug"] = (
                fallback_filter_debug if isinstance(fallback_filter_debug, dict) else {}
            )

        return self._serialize_for_json(response)