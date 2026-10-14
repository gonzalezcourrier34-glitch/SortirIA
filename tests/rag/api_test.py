"""Test manuel de l'API FastAPI du RAG avec des services simulés (non lancé par pytest : le nom ne commence pas par test_).

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/api_test.py` (commit 18f7ce1).
"""

from fastapi.testclient import TestClient

from app.main import app
from app.security import require_api_key


class FakeMemoryService:
    def reset_memory(self) -> None:
        pass


class FakeRAGService:
    def __init__(self):
        self.memory_service = FakeMemoryService()

    def ask(self, question: str, session_id: str = "default"):
        from app.schemas import AskResponse, RetrievedDocument

        return AskResponse(
            question=question,
            answer="Voici une exposition d'architecture à Montpellier.",
            n_docs=1,
            documents=[
                RetrievedDocument(
                    title="Expo Archi",
                    city="Montpellier",
                    location_name="Musée X",
                    region="Occitanie",
                    first_date="2026-03-01",
                    last_date="2026-03-10",
                    event_type="Exposition",
                    url="http://test.com",
                    price_info="gratuit",
                    is_free=True,
                    keywords_title=[],
                    score=None,
                )
            ],
            session_id=session_id,
        )

    def ask_debug(self, question: str, session_id: str = "default"):
        doc = {
            "title": "Expo Archi",
            "location_name": "Musée X",
            "city": "Montpellier",
            "region": "Occitanie",
            "first_date": "2026-03-01",
            "last_date": "2026-03-10",
            "event_type": "Exposition",
            "music_genre": "",
            "price_info": "gratuit",
            "is_free": True,
            "vector_score": 0.42,
            "final_score": 12.5,
            "diversified_score": 12.5,
            "url": "http://test.com",
        }

        return {
            "question": question,
            "effective_question": question,
            "session_id": session_id,
            "history": [],
            "zone": "Montpellier",
            "scope": "city",
            "top_k_retrieval": 10,
            "top_k_final": 3,
            "fallback_used": False,
            "n_input_docs": 1,
            "n_prefiltered_docs": 1,
            "n_raw_docs": 1,
            "n_ranked_docs": 1,
            "n_final_docs": 1,
            "n_docs": 1,
            "documents": [doc],
            "filter_debug": {
                "filters": {},
                "n_input_docs": 1,
                "n_after_city": 1,
                "n_after_date": 1,
                "n_after_type": 1,
                "n_after_music": 1,
                "n_after_cultural": 1,
                "n_after_audience": 1,
                "n_after_duration": 1,
                "n_after_price": 1,
            },
            "fallback_filter_debug": None,
            "prefiltered_docs": [doc],
            "raw_docs": [doc],
            "ranked_docs": [doc],
            "final_docs": [doc],
            "retrieved_contexts": ["Contenu test"],
            "context": (
                "Événement 1\n"
                "Titre : Expo Archi\n"
                "Lieu : Musée X\n"
                "Ville : Montpellier\n"
                "Région : Occitanie\n"
                "Date de début : 2026-03-01\n"
                "Date de fin : 2026-03-10\n"
                "Type : Exposition\n"
                "Genre musical : \n"
                "Tarification : gratuit\n"
                "Description : Exposition test\n"
                "URL : http://test.com"
            ),
            "answer": "Réponse debug",
        }

    def rebuild_index(self, documents):
        self.documents = documents
        return 3

    def is_index_loaded(self):
        return True


class FakeRAGServiceNoIndex:
    def __init__(self):
        self.memory_service = FakeMemoryService()

    def is_index_loaded(self):
        return False


app.dependency_overrides[require_api_key] = lambda: "test-key"


def test_root_redirects_to_docs():
    client = TestClient(app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/docs"


def test_health_ok(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["index_loaded"] is True


def test_health_index_not_loaded(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGServiceNoIndex())
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["index_loaded"] is False


def test_ask(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())
    client = TestClient(app)

    question = "Je cherche une exposition d'architecture à Montpellier"

    response = client.post(
        "/ask",
        json={"question": question, "session_id": "test-session"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["question"] == question
    assert data["answer"] == "Voici une exposition d'architecture à Montpellier."
    assert data["n_docs"] == 1
    assert data["session_id"] == "test-session"
    assert len(data["documents"]) == 1
    assert data["documents"][0]["title"] == "Expo Archi"
    assert data["documents"][0]["city"] == "Montpellier"


def test_ask_value_error(monkeypatch):
    class FakeRAG:
        def ask(self, question: str, session_id: str = "default"):
            raise ValueError("question invalide")

    monkeypatch.setattr("app.main.rag_service", FakeRAG())
    client = TestClient(app)

    response = client.post(
        "/ask",
        json={"question": "bad question", "session_id": "test-session"},
    )

    assert response.status_code == 400
    assert "question invalide" in response.json()["detail"]


def test_ask_file_not_found(monkeypatch):
    class FakeRAG:
        def ask(self, question: str, session_id: str = "default"):
            raise FileNotFoundError("index absent")

    monkeypatch.setattr("app.main.rag_service", FakeRAG())
    client = TestClient(app)

    response = client.post(
        "/ask",
        json={"question": "test", "session_id": "test-session"},
    )

    assert response.status_code == 503
    assert "index absent" in response.json()["detail"]


def test_ask_unexpected_error(monkeypatch):
    class FakeRAG:
        def ask(self, question: str, session_id: str = "default"):
            raise Exception("boom")

    monkeypatch.setattr("app.main.rag_service", FakeRAG())
    client = TestClient(app)

    response = client.post(
        "/ask",
        json={"question": "test", "session_id": "test-session"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Erreur interne du serveur."


def test_ask_debug(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())
    client = TestClient(app)

    response = client.post(
        "/ask/debug",
        json={"question": "debug exposition", "session_id": "debug-session"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["question"] == "debug exposition"
    assert data["effective_question"] == "debug exposition"
    assert data["session_id"] == "debug-session"
    assert data["history"] == []
    assert data["answer"] == "Réponse debug"
    assert data["n_docs"] == 1
    assert data["retrieved_contexts"] == ["Contenu test"]
    assert "filter_debug" in data
    assert "documents" in data
    assert len(data["documents"]) == 1
    assert data["documents"][0]["title"] == "Expo Archi"
    

def test_rebuild(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())
    monkeypatch.setattr(
        "app.main.load_documents",
        lambda *args, **kwargs: ["fake_doc_1", "fake_doc_2", "fake_doc_3"],
    )
    client = TestClient(app)

    response = client.post(
        "/rebuild",
        json={"zone": "Montpellier", "scope": "city"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["n_docs_indexed"] == 3


def test_rebuild_no_documents(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())
    monkeypatch.setattr(
        "app.main.load_documents",
        lambda *args, **kwargs: [],
    )
    client = TestClient(app)

    response = client.post(
        "/rebuild",
        json={"zone": "Montpellier", "scope": "city"},
    )

    assert response.status_code == 400
    assert "Aucun document" in response.json()["detail"]


def test_rebuild_value_error(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())

    def fake_load_documents(*args, **kwargs):
        raise ValueError("zone invalide")

    monkeypatch.setattr("app.main.load_documents", fake_load_documents)
    client = TestClient(app)

    response = client.post(
        "/rebuild",
        json={"zone": "Montpellier", "scope": "city"},
    )

    assert response.status_code == 400
    assert "zone invalide" in response.json()["detail"]


def test_rebuild_unexpected_error(monkeypatch):
    monkeypatch.setattr("app.main.rag_service", FakeRAGService())

    def fake_load_documents(*args, **kwargs):
        raise Exception("boom")

    monkeypatch.setattr("app.main.load_documents", fake_load_documents)
    client = TestClient(app)

    response = client.post(
        "/rebuild",
        json={"zone": "Montpellier", "scope": "city"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Erreur interne du serveur."