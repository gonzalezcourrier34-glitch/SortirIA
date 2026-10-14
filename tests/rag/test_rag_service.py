"""Tests unitaires de `app/rag_service.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_rag_service.py` (commit 18f7ce1).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from app.rag_service import RAGService


# -------------------------------------------------------------------------
# Fakes
# -------------------------------------------------------------------------


class FakeEmbeddings:
    def __init__(self, *args, **kwargs):
        pass


def fake_llm_factory(*args, **kwargs):
    return RunnableLambda(lambda x: "Réponse simulée")


class CapturingChain:
    def __init__(self, answer="Réponse simulée", side_effect=None):
        self.answer = answer
        self.side_effect = side_effect
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        if self.side_effect is not None:
            raise self.side_effect
        return self.answer


class FakeVectorStore:
    def __init__(self, documents=None, results_with_scores=None):
        self.documents = documents or []
        self.results_with_scores = results_with_scores or []
        self.saved_path = None

    def save_local(self, path):
        self.saved_path = path
        return None

    def add_documents(self, docs):
        self.documents.extend(docs)

    def similarity_search_with_score(self, query, k=3):
        return self.results_with_scores[:k]


class FakeFilterService:
    def __init__(self, docs=None):
        self.docs = docs or []

    def filter_documents_with_debug(self, question, docs, default_city=None):
        return {
            "filters": {"question": question, "default_city": default_city},
            "n_input_docs": len(docs),
            "n_after_city": len(self.docs),
            "n_after_type": len(self.docs),
            "n_after_music": len(self.docs),
            "n_after_cultural": len(self.docs),
            "n_after_audience": len(self.docs),
            "n_after_duration": len(self.docs),
            "n_after_date": len(self.docs),
            "n_after_price": len(self.docs),
            "docs": self.docs,
        }


class FakeRetrievalService:
    def __init__(self, ranked_docs=None, retrieval_debug=None):
        self.ranked_docs = ranked_docs
        self.retrieval_debug = retrieval_debug or []

    def rank_documents(self, question, raw_docs, top_k):
        if self.ranked_docs is not None:
            return self.ranked_docs[:top_k]
        return raw_docs[:top_k]

    def rank_documents_with_scores(self, question, raw_docs, top_k):
        if self.retrieval_debug:
            return self.retrieval_debug
        return [
            {
                "title": (doc.metadata or {}).get("title", ""),
                "final_score": (doc.metadata or {}).get("final_score"),
                "vector_score": (doc.metadata or {}).get("vector_score"),
            }
            for doc in raw_docs[:top_k]
        ]


class FakeTraceService:
    def __init__(self, *args, **kwargs):
        self.payloads = []

    def write_trace(self, payload):
        self.payloads.append(payload)


class FakeFAISSFactory:
    def __init__(self):
        self.created_from_documents = None
        self.loaded_store = None

    def from_documents(self, documents, embedding=None, embeddings=None, **kwargs):
        self.created_from_documents = documents
        return FakeVectorStore(documents=documents)

    def load_local(
        self,
        folder_path=None,
        embeddings=None,
        allow_dangerous_deserialization=True,
        **kwargs,
    ):
        return self.loaded_store


class FakeMemoryService:
    def __init__(self):
        self.messages = []
        self.history_text = ""
        self.history_messages = []

    def format_history_for_prompt(
        self,
        session_id: str,
        max_messages: int | None = None,
    ) -> str:
        return self.history_text

    def get_recent_messages(
        self,
        session_id: str,
        max_messages: int | None = None,
    ):
        return self.history_messages

    def append_message(self, session_id: str, role: str, content: str) -> None:
        self.messages.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
            }
        )

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self.append_message(session_id, "user", user_message)
        self.append_message(session_id, "assistant", assistant_message)

    def get_history(self, session_id: str):
        return self.history_messages

    def reset_memory(self) -> None:
        pass


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def sample_documents():
    return [
        Document(
            page_content="Contenu exposition architecture Montpellier",
            metadata={
                "title": "Expo Archi",
                "description": "Une exposition d'architecture très détaillée avec beaucoup de texte. "
                "Cette description doit pouvoir être tronquée dans le contexte LLM sans casser le rendu.",
                "location_name": "Musée X",
                "city": "Montpellier",
                "region": "Occitanie",
                "first_date": "2026-03-01",
                "last_date": "2026-03-10",
                "event_type": "Exposition",
                "canonical_event_type": "exposition",
                "music_genre": "",
                "price_info": "gratuit",
                "is_free": True,
                "keywords_title": ["expo", "archi"],
                "url": "http://test.com",
                "source_url": "http://test.com",
                "content_quality": 8,
                "search_text": "exposition architecture montpellier musee x",
                "final_score": 0.9,
                "diversified_score": 0.8,
            },
        ),
        Document(
            page_content="Contenu concert jazz Sète",
            metadata={
                "title": "Jazz Night",
                "description": "Concert de jazz",
                "location_name": "Salle Y",
                "city": "Sète",
                "region": "Occitanie",
                "first_date": "2026-03-15",
                "last_date": "2026-03-15",
                "event_type": "Concert",
                "canonical_event_type": "concert",
                "music_genre": "jazz",
                "price_info": "payant",
                "is_free": False,
                "keywords_title": ["jazz"],
                "url": "http://concert.com",
                "source_url": "http://concert.com",
                "content_quality": 8,
                "search_text": "concert jazz sete salle y",
            },
        ),
    ]


@pytest.fixture
def patched_rag(monkeypatch):
    fake_faiss = FakeFAISSFactory()

    monkeypatch.setattr("app.rag_service.MistralAIEmbeddings", FakeEmbeddings)
    monkeypatch.setattr("app.rag_service.ChatMistralAI", fake_llm_factory)
    monkeypatch.setattr("app.rag_service.FAISS.from_documents", fake_faiss.from_documents)
    monkeypatch.setattr("app.rag_service.FAISS.load_local", fake_faiss.load_local)
    monkeypatch.setattr("app.rag_service.FilterService", FakeFilterService)
    monkeypatch.setattr("app.rag_service.RetrievalService", FakeRetrievalService)
    monkeypatch.setattr("app.rag_service.TraceService", FakeTraceService)
    monkeypatch.setattr("app.rag_service.MemoryService", FakeMemoryService)
    monkeypatch.setattr("app.rag_service.time.sleep", lambda _: None)

    return fake_faiss


# -------------------------------------------------------------------------
# Base
# -------------------------------------------------------------------------


def test_set_documents(patched_rag, sample_documents):
    rag = RAGService()
    rag.set_documents(sample_documents)

    assert rag.documents == sample_documents


def test_is_index_loaded_false_by_default(patched_rag):
    rag = RAGService()

    assert rag.is_index_loaded() is False


def test_constants_exist(patched_rag):
    rag = RAGService()

    assert "aucun événement correspondant" in rag.EMPTY_ANSWER.lower()
    assert "momentanément indisponible" in rag.TEMPORARY_LLM_UNAVAILABLE_ANSWER.lower()


# -------------------------------------------------------------------------
# Construction / chargement index
# -------------------------------------------------------------------------


def test_build_index(patched_rag, sample_documents):
    rag = RAGService(documents=sample_documents)

    n_docs = rag.build_index()

    assert n_docs == 2
    assert rag.is_index_loaded() is True
    assert patched_rag.created_from_documents is not None
    assert len(patched_rag.created_from_documents) == 2
    assert patched_rag.created_from_documents[0].page_content == sample_documents[0].metadata["search_text"]


def test_build_index_without_documents_raises(patched_rag):
    rag = RAGService(documents=[])

    with pytest.raises(ValueError, match="aucun document n'est disponible"):
        rag.build_index()


def test_load_index_missing_dir_raises(patched_rag, tmp_path):
    rag = RAGService(index_dir=tmp_path / "missing_index")

    with pytest.raises(FileNotFoundError, match="Index FAISS introuvable"):
        rag.load_index()


def test_load_index_success(patched_rag, sample_documents, tmp_path):
    rag = RAGService(index_dir=tmp_path / "faiss_index")
    rag.index_dir.mkdir(parents=True, exist_ok=True)
    (rag.index_dir / "stub.faiss").write_text("ok", encoding="utf-8")

    fake_store = FakeVectorStore(documents=sample_documents)
    patched_rag.loaded_store = fake_store

    rag.load_index()

    assert rag.vectorstore is fake_store
    assert rag.is_index_loaded() is True


def test_ensure_index_ready_loads_existing_index_and_documents(
    patched_rag, sample_documents, monkeypatch, tmp_path
):
    rag = RAGService(index_dir=tmp_path / "faiss_index", documents=[])
    rag.index_dir.mkdir(parents=True, exist_ok=True)
    (rag.index_dir / "stub.faiss").write_text("ok", encoding="utf-8")

    fake_store = FakeVectorStore(documents=sample_documents)
    patched_rag.loaded_store = fake_store

    monkeypatch.setattr("app.rag_service.load_documents", lambda zone, scope: sample_documents)

    rag.ensure_index_ready()

    assert rag.vectorstore is fake_store
    assert rag.documents == sample_documents


def test_ensure_index_ready_builds_index_when_missing(
    patched_rag, sample_documents, monkeypatch, tmp_path
):
    rag = RAGService(index_dir=tmp_path / "faiss_index", documents=[])

    monkeypatch.setattr("app.rag_service.load_documents", lambda zone, scope: sample_documents)

    rag.ensure_index_ready()

    assert rag.is_index_loaded() is True
    assert rag.documents == sample_documents


def test_ensure_index_ready_raises_when_no_documents_loaded(patched_rag, monkeypatch, tmp_path):
    rag = RAGService(index_dir=tmp_path / "faiss_index", documents=[])

    monkeypatch.setattr("app.rag_service.load_documents", lambda zone, scope: [])

    with pytest.raises(ValueError, match="Aucun index FAISS disponible"):
        rag.ensure_index_ready()


# -------------------------------------------------------------------------
# Retry
# -------------------------------------------------------------------------


def test_execute_with_retry_retries_after_429_then_succeeds(patched_rag):
    rag = RAGService()
    state = {"count": 0}

    def flaky():
        state["count"] += 1
        if state["count"] == 1:
            raise Exception("429 Too Many Requests")
        return "ok"

    result = rag._execute_with_retry(flaky, max_retries=3)

    assert result == "ok"
    assert state["count"] == 2


def test_execute_with_retry_raises_after_exhausting_retries(patched_rag):
    rag = RAGService()
    state = {"count": 0}

    def always_fail():
        state["count"] += 1
        raise Exception("429 Too Many Requests")

    with pytest.raises(Exception, match="429 Too Many Requests"):
        rag._execute_with_retry(always_fail, max_retries=2)

    assert state["count"] == 2


def test_execute_with_retry_raises_immediately_for_non_429_error(patched_rag):
    rag = RAGService()
    state = {"count": 0}

    def fail():
        state["count"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        rag._execute_with_retry(fail, max_retries=5)

    assert state["count"] == 1


# -------------------------------------------------------------------------
# Helpers index / retrieval
# -------------------------------------------------------------------------


def test_build_docs_for_vector_index_uses_search_text(patched_rag, sample_documents):
    rag = RAGService()

    docs_for_index = rag._build_docs_for_vector_index(sample_documents)

    assert len(docs_for_index) == 2
    assert docs_for_index[0].page_content == sample_documents[0].metadata["search_text"]
    assert docs_for_index[1].page_content == sample_documents[1].metadata["search_text"]


def test_attach_vector_scores_sets_metadata(patched_rag, sample_documents):
    rag = RAGService()

    docs = rag._attach_vector_scores(
        [
            (sample_documents[0], 0.12),
            (sample_documents[1], 0.56),
        ]
    )

    assert docs[0].metadata["vector_score"] == 0.12
    assert docs[1].metadata["vector_score"] == 0.56


def test_is_reliable_document_true(patched_rag, sample_documents):
    rag = RAGService()

    assert rag._is_reliable_document(sample_documents[0]) is True


def test_is_reliable_document_false_when_too_poor(patched_rag):
    rag = RAGService()
    poor_doc = Document(
        page_content="x",
        metadata={
            "title": "",
            "first_date": "",
            "location_name": "",
            "city": "",
            "source_url": "",
            "content_quality": 1,
        },
    )

    assert rag._is_reliable_document(poor_doc) is False


def test_post_filter_ranked_docs_prefers_reliable_docs(patched_rag, sample_documents):
    rag = RAGService(top_k_final=1)

    poor_doc = Document(
        page_content="x",
        metadata={
            "title": "",
            "first_date": "",
            "location_name": "",
            "city": "",
            "source_url": "",
            "content_quality": 1,
        },
    )

    docs = rag._post_filter_ranked_docs([poor_doc, sample_documents[0]])

    assert len(docs) == 1
    assert docs[0].metadata["title"] == "Expo Archi"


def test_post_filter_ranked_docs_returns_empty_when_input_empty(patched_rag):
    rag = RAGService()

    assert rag._post_filter_ranked_docs([]) == []


def test_post_filter_ranked_docs_falls_back_to_original_docs_when_none_are_reliable(patched_rag):
    rag = RAGService(top_k_final=1)
    poor_doc = Document(
        page_content="x",
        metadata={
            "title": "",
            "first_date": "",
            "location_name": "",
            "city": "",
            "source_url": "",
            "content_quality": 1,
        },
    )

    result = rag._post_filter_ranked_docs([poor_doc])

    assert result == [poor_doc]


# -------------------------------------------------------------------------
# Mémoire conversationnelle
# -------------------------------------------------------------------------


def test_get_recent_history_text_strips_memory_text(patched_rag):
    rag = RAGService()
    rag.memory_service.history_text = "  Utilisateur : bonjour  "

    result = rag._get_recent_history_text("session-1", max_messages=4)

    assert result == "Utilisateur : bonjour"


def test_get_recent_history_messages_returns_memory_messages(patched_rag):
    rag = RAGService()
    rag.memory_service.history_messages = [
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Salut"},
    ]

    result = rag._get_recent_history_messages("session-1", max_messages=4)

    assert result == [
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Salut"},
    ]


def test_needs_rewrite_false_for_autonomous_question(patched_rag):
    rag = RAGService()

    assert rag._needs_rewrite("Je cherche une exposition à Montpellier") is False


def test_needs_rewrite_true_for_short_followup_exact_match(patched_rag):
    rag = RAGService()

    assert rag._needs_rewrite("Et les gratuites ?") is True


def test_needs_rewrite_true_for_marker_based_followup(patched_rag):
    rag = RAGService()

    assert rag._needs_rewrite("Et demain ?") is True


def test_needs_rewrite_true_for_anaphoric_reference(patched_rag):
    rag = RAGService()

    assert rag._needs_rewrite("Je veux celles pour les enfants") is True


def test_rewrite_question_with_history_returns_original_question_when_history_is_empty(patched_rag):
    rag = RAGService()
    rag.memory_service.history_text = ""

    result = rag._rewrite_question_with_history("Et les gratuites ?", "session-1")

    assert result == "Et les gratuites ?"


def test_rewrite_question_with_history_uses_rewrite_chain_when_history_exists(patched_rag):
    rag = RAGService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition à Montpellier"
    rag.rewrite_chain = CapturingChain("Je cherche des expositions gratuites à Montpellier")

    result = rag._rewrite_question_with_history("Et les gratuites ?", "session-1")

    assert result == "Je cherche des expositions gratuites à Montpellier"
    assert rag.rewrite_chain.calls[0]["question"] == "Et les gratuites ?"
    assert "Montpellier" in rag.rewrite_chain.calls[0]["history"]


def test_rewrite_question_with_history_returns_original_when_rewrite_chain_returns_blank(patched_rag):
    rag = RAGService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition"
    rag.rewrite_chain = CapturingChain("   ")

    result = rag._rewrite_question_with_history("Et les gratuites ?", "session-1")

    assert result == "Et les gratuites ?"


def test_rewrite_question_with_history_falls_back_to_original_on_429(patched_rag):
    rag = RAGService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition"
    rag.rewrite_chain = CapturingChain(side_effect=Exception("429 Too Many Requests"))

    result = rag._rewrite_question_with_history("Et les gratuites ?", "session-1")

    assert result == "Et les gratuites ?"


def test_rewrite_question_with_history_raises_non_429_error(patched_rag):
    rag = RAGService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition"
    rag.rewrite_chain = CapturingChain(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        rag._rewrite_question_with_history("Et les gratuites ?", "session-1")


# -------------------------------------------------------------------------
# Sérialisation / debug
# -------------------------------------------------------------------------


def test_serialize_for_json_handles_nested_dates_and_sets(patched_rag):
    rag = RAGService()

    payload = {
        "date": date(2026, 3, 26),
        "datetime": datetime(2026, 3, 26, 10, 30, 0),
        "nested": {"values": [1, 2, {3, 4}]},
    }

    result = rag._serialize_for_json(payload)

    assert result["date"] == "2026-03-26"
    assert result["datetime"].startswith("2026-03-26T10:30:00")
    assert sorted(result["nested"]["values"][2]) == [3, 4]


def test_sanitize_filter_debug_returns_none_when_input_is_none(patched_rag):
    rag = RAGService()

    assert rag._sanitize_filter_debug(None) is None


def test_sanitize_filter_debug_returns_expected_shape(patched_rag):
    rag = RAGService()

    debug_data = {
        "filters": {"date": date(2026, 3, 26)},
        "n_input_docs": 10,
        "n_after_city": 8,
        "n_after_type": 7,
        "n_after_music": 6,
        "n_after_cultural": 5,
        "n_after_audience": 4,
        "n_after_duration": 3,
        "n_after_date": 2,
        "n_after_price": 1,
    }

    result = rag._sanitize_filter_debug(debug_data)

    assert result["filters"]["date"] == "2026-03-26"
    assert result["n_input_docs"] == 10
    assert result["n_after_price"] == 1


# -------------------------------------------------------------------------
# Trace helpers
# -------------------------------------------------------------------------


def test_doc_to_trace_row_returns_expected_fields(patched_rag, sample_documents):
    rag = RAGService()

    row = rag._doc_to_trace_row(sample_documents[0])

    assert row["title"] == "Expo Archi"
    assert row["location_name"] == "Musée X"
    assert row["city"] == "Montpellier"
    assert row["event_type"] == "exposition"
    assert row["url"] == "http://test.com"
    assert row["final_score"] == 0.9
    assert row["diversified_score"] == 0.8


def test_trace_pipeline_writes_serialized_payload(patched_rag, sample_documents):
    rag = RAGService()
    rag.trace_service = FakeTraceService()

    rag._trace_pipeline(
        question="Question originale",
        effective_question="Question reformulée",
        session_id="session-1",
        history=[{"role": "user", "content": "Bonjour"}],
        filter_debug={"filters": {}, "n_input_docs": 2},
        fallback_filter_debug=None,
        prefiltered_docs=sample_documents,
        raw_docs=sample_documents[:1],
        ranked_docs=sample_documents[:1],
        final_docs=sample_documents[:1],
        context="Contexte test",
        answer="Réponse test",
        fallback_used=False,
        generation_prompt="Prompt génération test",
        rewrite_prompt_rendered="Prompt rewrite test",
    )

    assert len(rag.trace_service.payloads) == 1
    payload = rag.trace_service.payloads[0]
    assert payload["question"] == "Question originale"
    assert payload["effective_question"] == "Question reformulée"
    assert payload["session_id"] == "session-1"
    assert payload["history"] == [{"role": "user", "content": "Bonjour"}]
    assert payload["answer"] == "Réponse test"
    assert payload["generation_prompt"] == "Prompt génération test"
    assert payload["rewrite_prompt"] == "Prompt rewrite test"


# -------------------------------------------------------------------------
# Render prompts
# -------------------------------------------------------------------------


def test_build_generation_prompt_text_returns_string(patched_rag, sample_documents):
    rag = RAGService()

    result = rag._build_generation_prompt_text(
        question="Je cherche une exposition",
        context=rag.format_docs(sample_documents[:1]),
        history="Utilisateur : Bonjour",
    )

    assert isinstance(result, str)
    assert "Je cherche une exposition" in result
    assert "Utilisateur : Bonjour" in result
    assert "Expo Archi" in result


def test_build_rewrite_prompt_text_returns_string(patched_rag):
    rag = RAGService()

    result = rag._build_rewrite_prompt_text(
        question="Et les gratuites ?",
        history="Utilisateur : Je cherche une exposition à Montpellier",
    )

    assert isinstance(result, str)
    assert "Et les gratuites ?" in result
    assert "Montpellier" in result


# -------------------------------------------------------------------------
# Retrieval
# -------------------------------------------------------------------------


def test_retrieve_empty_question_raises(patched_rag):
    rag = RAGService()

    with pytest.raises(ValueError, match="La question utilisateur ne peut pas être vide"):
        rag.retrieve("   ")


def test_run_vector_retrieval_returns_scored_docs(patched_rag, sample_documents, monkeypatch):
    rag = RAGService(top_k_retrieval=2)

    fake_local_vs = FakeVectorStore(
        results_with_scores=[
            (sample_documents[0], 0.11),
            (sample_documents[1], 0.22),
        ]
    )
    monkeypatch.setattr(rag, "_build_local_vectorstore", lambda docs: fake_local_vs)

    docs = rag._run_vector_retrieval("architecture", sample_documents)

    assert len(docs) == 2
    assert docs[0].metadata["vector_score"] == 0.11
    assert docs[1].metadata["vector_score"] == 0.22


def test_run_vector_retrieval_returns_empty_when_candidate_docs_empty(patched_rag):
    rag = RAGService()

    result = rag._run_vector_retrieval("architecture", [])

    assert result == []


def test_run_vector_retrieval_returns_empty_when_local_search_returns_no_results(
    patched_rag, monkeypatch, sample_documents
):
    rag = RAGService()

    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[]),
    )

    result = rag._run_vector_retrieval("architecture", sample_documents)

    assert result == []


def test_retrieve_returns_docs_from_pipeline(patched_rag, sample_documents, monkeypatch):
    rag = RAGService()
    monkeypatch.setattr(
        rag,
        "_run_pipeline",
        lambda question, session_id="default": {"docs": sample_documents[:1]},
    )

    docs = rag.retrieve("architecture")

    assert len(docs) == 1
    assert docs[0].metadata["title"] == "Expo Archi"


def test_build_relaxed_question_strips_input(patched_rag):
    rag = RAGService()

    assert rag._build_relaxed_question("  question test  ") == "question test"


def test_prefilter_with_fallback_uses_fallback_when_main_empty(patched_rag, sample_documents):
    rag = RAGService(documents=sample_documents)

    main_debug = {
        "filters": {},
        "n_input_docs": 2,
        "n_after_city": 0,
        "n_after_type": 0,
        "n_after_music": 0,
        "n_after_cultural": 0,
        "n_after_audience": 0,
        "n_after_duration": 0,
        "n_after_date": 0,
        "n_after_price": 0,
        "docs": [],
    }
    fallback_debug = {
        "filters": {},
        "n_input_docs": 2,
        "n_after_city": 1,
        "n_after_type": 1,
        "n_after_music": 1,
        "n_after_cultural": 1,
        "n_after_audience": 1,
        "n_after_duration": 1,
        "n_after_date": 1,
        "n_after_price": 1,
        "docs": [sample_documents[0]],
    }

    class LocalFilterService:
        def __init__(self):
            self.calls = 0

        def filter_documents_with_debug(self, question, docs, default_city=None):
            self.calls += 1
            return main_debug if self.calls == 1 else fallback_debug

    rag.filter_service = LocalFilterService()

    filter_debug, fallback_filter_debug, prefiltered_docs, fallback_used = rag._prefilter_with_fallback(
        "question test"
    )

    assert fallback_used is True
    assert filter_debug["docs"] == []
    assert fallback_filter_debug is not None
    assert len(prefiltered_docs) == 1
    assert prefiltered_docs[0].metadata["title"] == "Expo Archi"


# -------------------------------------------------------------------------
# Formatage
# -------------------------------------------------------------------------


def test_format_doc_returns_structured_text_without_url(patched_rag, sample_documents):
    rag = RAGService()

    result = rag.format_doc(sample_documents[0], index=1)

    assert "Événement 1" in result
    assert "Titre : Expo Archi" in result
    assert "Ville : Montpellier" in result
    assert "Description :" in result
    assert "URL :" not in result


def test_format_doc_truncates_description_to_180_chars(patched_rag):
    rag = RAGService()
    long_text = "x" * 400
    doc = Document(
        page_content="test",
        metadata={
            "title": "Doc",
            "description": long_text,
            "location_name": "Lieu",
            "city": "Ville",
            "region": "Region",
            "first_date": "2026-01-01",
            "last_date": "2026-01-02",
            "canonical_event_type": "exposition",
            "music_genre": "",
            "price_info": "gratuit",
        },
    )

    result = rag.format_doc(doc, index=1)

    assert f"Description : {'x' * 180}" in result
    assert f"Description : {'x' * 181}" not in result


def test_format_docs_empty_returns_empty_string(patched_rag):
    rag = RAGService()

    result = rag.format_docs([])

    assert result == ""


def test_format_docs_returns_structured_text(patched_rag, sample_documents):
    rag = RAGService()

    result = rag.format_docs(sample_documents)

    assert "Événement 1" in result
    assert "Titre : Expo Archi" in result
    assert "Événement 2" in result
    assert "Titre : Jazz Night" in result


def test_format_docs_list_returns_list(patched_rag, sample_documents):
    rag = RAGService()

    result = rag.format_docs_list(sample_documents)

    assert isinstance(result, list)
    assert len(result) == 2
    assert "Événement 1" in result[0]


# -------------------------------------------------------------------------
# Conversion API
# -------------------------------------------------------------------------


def test_build_retrieved_document(patched_rag, sample_documents):
    rag = RAGService()

    retrieved = rag._build_retrieved_document(sample_documents[0])

    assert retrieved.title == "Expo Archi"
    assert retrieved.city == "Montpellier"
    assert retrieved.url == "http://test.com"
    assert retrieved.price_info == "gratuit"
    assert retrieved.is_free is True
    assert retrieved.keywords_title == ["expo", "archi"]
    assert retrieved.score == 0.9


def test_build_retrieved_document_handles_invalid_score(patched_rag):
    rag = RAGService()
    doc = Document(
        page_content="x",
        metadata={
            "title": "Doc",
            "location_name": "Lieu",
            "city": "Ville",
            "region": "Region",
            "first_date": "2026-01-01",
            "last_date": "2026-01-02",
            "event_type": "expo",
            "url": "http://x",
            "price_info": "gratuit",
            "is_free": True,
            "keywords_title": [],
            "final_score": "oops",
        },
    )

    retrieved = rag._build_retrieved_document(doc)

    assert retrieved.score is None


def test_build_retrieved_documents_returns_list(patched_rag, sample_documents):
    rag = RAGService()

    retrieved_docs = rag.build_retrieved_documents(sample_documents)

    assert len(retrieved_docs) == 2
    assert retrieved_docs[0].title == "Expo Archi"


# -------------------------------------------------------------------------
# Génération
# -------------------------------------------------------------------------


def test_generate_answer_returns_empty_answer_when_no_docs(patched_rag):
    rag = RAGService()

    result = rag.generate_answer("question test", [], session_id="test-session")

    assert result == rag.EMPTY_ANSWER


def test_generate_answer_returns_empty_answer_when_formatted_context_is_blank(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService()

    monkeypatch.setattr(rag, "format_docs", lambda docs: "   ")

    result = rag.generate_answer("question test", sample_documents, session_id="session-1")

    assert result == rag.EMPTY_ANSWER


def test_generate_answer_passes_history_to_chain(patched_rag, sample_documents):
    rag = RAGService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition"
    rag.chain = CapturingChain("Réponse générée")

    result = rag.generate_answer("Et les gratuites ?", sample_documents, session_id="session-1")

    assert result == "Réponse générée"
    assert len(rag.chain.calls) == 1
    payload = rag.chain.calls[0]
    assert payload["question"] == "Et les gratuites ?"
    assert "Expo Archi" in payload["context"]
    assert payload["history"] == "Utilisateur : Je cherche une exposition"


def test_generate_answer_returns_empty_answer_when_chain_returns_blank(
    patched_rag, sample_documents
):
    rag = RAGService()
    rag.chain = CapturingChain("   ")

    result = rag.generate_answer("question test", sample_documents, session_id="test-session")

    assert result == rag.EMPTY_ANSWER


def test_generate_answer_returns_temporary_message_on_429(patched_rag, sample_documents):
    rag = RAGService()
    rag.chain = CapturingChain(side_effect=Exception("429 Too Many Requests"))

    result = rag.generate_answer("question test", sample_documents, session_id="test-session")

    assert result == rag.TEMPORARY_LLM_UNAVAILABLE_ANSWER


def test_generate_answer_raises_non_429_error(patched_rag, sample_documents):
    rag = RAGService()
    rag.chain = CapturingChain(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        rag.generate_answer("question test", sample_documents, session_id="test-session")


# -------------------------------------------------------------------------
# Pipeline complet
# -------------------------------------------------------------------------


def test_ask_empty_question_raises(patched_rag):
    rag = RAGService()

    with pytest.raises(ValueError, match="La question utilisateur ne peut pas être vide"):
        rag.ask("   ")


def test_run_pipeline_returns_empty_answer_when_prefilter_returns_no_docs(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents)
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_prefilter_with_fallback",
        lambda question: (
            {"filters": {}, "n_input_docs": 2, "docs": []},
            None,
            [],
            False,
        ),
    )

    result = rag._run_pipeline("Je cherche une exposition", session_id="session-1")

    assert result["answer"] == rag.EMPTY_ANSWER
    assert result["docs"] == []
    assert result["retrieved_documents"] == []
    assert result["retrieved_contexts"] == []
    assert result["fallback_used"] is False
    assert len(rag.trace_service.payloads) == 1


def test_run_pipeline_skips_rewrite_when_no_history(patched_rag, sample_documents, monkeypatch):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.memory_service.history_text = ""
    rag.vectorstore = FakeVectorStore()

    rewrite_state = {"called": False}

    def fake_rewrite(question, session_id):
        rewrite_state["called"] = True
        return "QUESTION REWRITEE"

    monkeypatch.setattr(rag, "_rewrite_question_with_history", fake_rewrite)
    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    result = rag._run_pipeline("Je cherche une exposition", session_id="test-session")

    assert rewrite_state["called"] is False
    assert result["effective_question"] == "Je cherche une exposition"


def test_run_pipeline_skips_rewrite_when_question_does_not_need_it(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.memory_service.history_text = "Utilisateur : ancien message"
    rag.vectorstore = FakeVectorStore()

    rewrite_state = {"called": False}

    def fake_rewrite(question, session_id):
        rewrite_state["called"] = True
        return "QUESTION REWRITEE"

    monkeypatch.setattr(rag, "_rewrite_question_with_history", fake_rewrite)
    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    result = rag._run_pipeline("Je cherche une exposition à Montpellier", session_id="test-session")

    assert rewrite_state["called"] is False
    assert result["effective_question"] == "Je cherche une exposition à Montpellier"


def test_run_pipeline_uses_rewrite_when_history_exists_and_question_needs_it(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition à Montpellier"
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_rewrite_question_with_history",
        lambda question, session_id="default": "Je cherche des expositions gratuites à Montpellier",
    )
    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    result = rag._run_pipeline("Et les gratuites ?", session_id="test-session")

    assert result["effective_question"] == "Je cherche des expositions gratuites à Montpellier"


def test_run_pipeline_success(patched_rag, sample_documents, monkeypatch):
    rag = RAGService(documents=sample_documents, top_k_retrieval=2, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    result = rag._run_pipeline("Je cherche une exposition", session_id="test-session")

    assert result["question"] == "Je cherche une exposition"
    assert result["effective_question"] == "Je cherche une exposition"
    assert result["session_id"] == "test-session"
    assert result["answer"] == "Réponse pipeline"
    assert len(result["docs"]) == 1
    assert result["docs"][0].metadata["title"] == "Expo Archi"
    assert len(result["retrieved_documents"]) == 1
    assert len(result["retrieved_contexts"]) == 1
    assert result["fallback_used"] is False
    assert len(rag.trace_service.payloads) == 1

    trace_payload = rag.trace_service.payloads[0]
    assert "generation_prompt" in trace_payload
    assert "rewrite_prompt" in trace_payload


def test_run_pipeline_appends_turn_to_memory(patched_rag, sample_documents, monkeypatch):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    rag._run_pipeline("Je cherche une exposition", session_id="session-42")

    assert rag.memory_service.messages[0]["session_id"] == "session-42"
    assert rag.memory_service.messages[0]["role"] == "user"
    assert rag.memory_service.messages[0]["content"] == "Je cherche une exposition"
    assert rag.memory_service.messages[1]["role"] == "assistant"
    assert rag.memory_service.messages[1]["content"] == "Réponse pipeline"


def test_run_pipeline_traces_prompt_with_history_before_append_turn(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.memory_service.history_text = "Utilisateur : ancien message"
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_needs_rewrite",
        lambda question: False,
    )
    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    rag._run_pipeline("Je cherche une exposition", session_id="session-99")

    assert len(rag.trace_service.payloads) == 1
    payload = rag.trace_service.payloads[0]

    assert payload["generation_prompt"] is not None
    assert "Utilisateur : ancien message" in payload["generation_prompt"]
    assert "Réponse pipeline" not in payload["generation_prompt"]


def test_run_pipeline_trace_has_no_generation_prompt_when_no_final_docs(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents)
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_prefilter_with_fallback",
        lambda question: (
            {"filters": {}, "n_input_docs": 2, "docs": []},
            None,
            [],
            False,
        ),
    )

    rag._run_pipeline("Je cherche une exposition", session_id="session-1")

    payload = rag.trace_service.payloads[0]
    assert payload["generation_prompt"] is None


def test_run_pipeline_trace_has_no_rewrite_prompt_when_no_history(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.memory_service.history_text = ""
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    rag._run_pipeline("Je cherche une exposition", session_id="session-1")

    payload = rag.trace_service.payloads[0]
    assert payload["rewrite_prompt"] is None


def test_run_pipeline_trace_has_rewrite_prompt_when_history_and_rewrite_needed(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService(documents=sample_documents, top_k_retrieval=1, top_k_final=1)
    rag.chain = CapturingChain("Réponse pipeline")
    rag.filter_service = FakeFilterService(docs=sample_documents)
    rag.retrieval_service = FakeRetrievalService(ranked_docs=[sample_documents[0]])
    rag.trace_service = FakeTraceService()
    rag.memory_service = FakeMemoryService()
    rag.memory_service.history_text = "Utilisateur : Je cherche une exposition à Montpellier"
    rag.vectorstore = FakeVectorStore()

    monkeypatch.setattr(rag, "_needs_rewrite", lambda question: True)
    monkeypatch.setattr(
        rag,
        "_rewrite_question_with_history",
        lambda question, session_id="default": "Je cherche des expositions gratuites à Montpellier",
    )
    monkeypatch.setattr(
        rag,
        "_build_local_vectorstore",
        lambda docs: FakeVectorStore(results_with_scores=[(sample_documents[0], 0.15)]),
    )

    rag._run_pipeline("Et les gratuites ?", session_id="session-1")

    payload = rag.trace_service.payloads[0]
    assert payload["rewrite_prompt"] is not None
    assert "Et les gratuites ?" in payload["rewrite_prompt"]


def test_ask_returns_ask_response(patched_rag, sample_documents, monkeypatch):
    rag = RAGService()

    monkeypatch.setattr(
        rag,
        "_run_pipeline",
        lambda question, session_id="default": {
            "question": question,
            "answer": "Réponse finale",
            "docs": sample_documents[:1],
            "retrieved_documents": rag.build_retrieved_documents(sample_documents[:1]),
            "session_id": session_id,
        },
    )

    response = rag.ask("architecture", session_id="test-session")

    assert response.question == "architecture"
    assert response.answer == "Réponse finale"
    assert response.n_docs == 1
    assert response.session_id == "test-session"
    assert response.documents[0].title == "Expo Archi"


def test_ask_debug_returns_debug_payload(patched_rag, sample_documents, monkeypatch):
    rag = RAGService()

    monkeypatch.setattr(
        rag,
        "_run_pipeline",
        lambda question, session_id="default": {
            "question": question,
            "effective_question": question,
            "session_id": session_id,
            "history": [],
            "answer": "Réponse debug",
            "docs": sample_documents[:1],
            "retrieved_documents": rag.build_retrieved_documents(sample_documents[:1]),
            "retrieved_contexts": ["Événement 1\nTitre : Expo Archi"],
            "fallback_used": False,
            "filter_debug": {"filters": {}, "n_input_docs": 2},
            "fallback_filter_debug": None,
            "retrieval_debug": [{"title": "Expo Archi", "final_score": 0.8}],
        },
    )

    response = rag.ask_debug("architecture", session_id="test-session")

    assert response["question"] == "architecture"
    assert response["effective_question"] == "architecture"
    assert response["session_id"] == "test-session"
    assert response["history"] == []
    assert response["answer"] == "Réponse debug"
    assert response["n_docs"] == 1
    assert response["documents"][0]["title"] == "Expo Archi"
    assert response["retrieved_contexts"] == ["Événement 1\nTitre : Expo Archi"]
    assert response["fallback_used"] is False
    assert "filter_debug" in response
    assert "retrieval_debug" in response
    assert response["retrieval_debug"][0]["title"] == "Expo Archi"


def test_ask_debug_includes_fallback_filter_debug_when_present(
    patched_rag, sample_documents, monkeypatch
):
    rag = RAGService()

    monkeypatch.setattr(
        rag,
        "_run_pipeline",
        lambda question, session_id="default": {
            "question": question,
            "effective_question": question,
            "session_id": session_id,
            "history": [{"role": "user", "content": "Bonjour"}],
            "answer": "Réponse debug",
            "docs": sample_documents[:1],
            "retrieved_documents": rag.build_retrieved_documents(sample_documents[:1]),
            "retrieved_contexts": ["Événement 1\nTitre : Expo Archi"],
            "fallback_used": True,
            "filter_debug": {"filters": {}, "n_input_docs": 2},
            "fallback_filter_debug": {"filters": {"fallback": True}, "n_input_docs": 2},
            "retrieval_debug": [{"title": "Expo Archi"}],
        },
    )

    response = rag.ask_debug("architecture", session_id="session-1")

    assert response["fallback_used"] is True
    assert "fallback_filter_debug" in response
    assert response["fallback_filter_debug"]["filters"]["fallback"] is True
    assert response["retrieval_debug"][0]["title"] == "Expo Archi"