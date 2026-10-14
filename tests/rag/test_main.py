"""Tests unitaires de `app/main.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_main.py` (commit 18f7ce1).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import _initialize_rag_service, app, lifespan
from app.security import require_api_key


app.dependency_overrides[require_api_key] = lambda: "test-key"


class FakeMemoryService:
    def __init__(self) -> None:
        self.reset_called = False

    def reset_memory(self) -> None:
        self.reset_called = True


def test_initialize_rag_service_resets_memory_before_returning_when_index_loaded(monkeypatch):
    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.index_dir = Path("unused")

        def is_index_loaded(self):
            return True

    fake_rag = FakeRAG()
    monkeypatch.setattr("app.main.rag_service", fake_rag)

    _initialize_rag_service()

    assert fake_rag.memory_service.reset_called is True


def test_initialize_rag_service_resets_memory_before_loading_existing_index(monkeypatch, tmp_path):
    index_dir = tmp_path / "faiss_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "stub.faiss").write_text("ok", encoding="utf-8")

    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.index_dir = index_dir
            self.documents = None
            self.zone = None
            self.scope = None
            self.loaded = False

        def is_index_loaded(self):
            return False

        def load_index(self):
            self.loaded = True

        def set_documents(self, documents):
            self.documents = documents

    fake_rag = FakeRAG()

    monkeypatch.setattr("app.main.rag_service", fake_rag)
    monkeypatch.setattr("app.main.load_documents", lambda zone, scope: ["doc1"])

    _initialize_rag_service()

    assert fake_rag.memory_service.reset_called is True
    assert fake_rag.loaded is True
    assert fake_rag.documents == ["doc1"]


def test_initialize_rag_service_sets_default_zone_and_scope_when_loading_existing_index(
    monkeypatch, tmp_path
):
    index_dir = tmp_path / "faiss_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "stub.faiss").write_text("ok", encoding="utf-8")

    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.index_dir = index_dir
            self.zone = None
            self.scope = None

        def is_index_loaded(self):
            return False

        def load_index(self):
            pass

        def set_documents(self, documents):
            self.documents = documents

    fake_rag = FakeRAG()

    monkeypatch.setattr("app.main.rag_service", fake_rag)
    monkeypatch.setattr("app.main.load_documents", lambda zone, scope: ["doc1"])

    _initialize_rag_service()

    assert isinstance(fake_rag.zone, str)
    assert isinstance(fake_rag.scope, str)


def test_initialize_rag_service_resets_memory_before_building_index(monkeypatch, tmp_path):
    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.index_dir = tmp_path / "missing_index"
            self.zone = None
            self.scope = None
            self.documents = None

        def is_index_loaded(self):
            return False

        def rebuild_index(self, documents):
            self.documents = documents
            return len(documents)

    fake_rag = FakeRAG()

    monkeypatch.setattr("app.main.rag_service", fake_rag)
    monkeypatch.setattr("app.main.load_documents", lambda zone, scope: ["doc1", "doc2"])

    _initialize_rag_service()

    assert fake_rag.memory_service.reset_called is True
    assert fake_rag.documents == ["doc1", "doc2"]


def test_initialize_rag_service_sets_default_zone_and_scope_when_building_index(monkeypatch, tmp_path):
    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.index_dir = tmp_path / "missing_index"
            self.zone = None
            self.scope = None

        def is_index_loaded(self):
            return False

        def rebuild_index(self, documents):
            return len(documents)

    fake_rag = FakeRAG()

    monkeypatch.setattr("app.main.rag_service", fake_rag)
    monkeypatch.setattr("app.main.load_documents", lambda zone, scope: ["doc1", "doc2"])

    _initialize_rag_service()

    assert isinstance(fake_rag.zone, str)
    assert isinstance(fake_rag.scope, str)


def test_initialize_rag_service_returns_without_building_when_no_documents(monkeypatch, tmp_path):
    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.index_dir = tmp_path / "missing_index"
            self.rebuild_called = False
            self.zone = None
            self.scope = None

        def is_index_loaded(self):
            return False

        def rebuild_index(self, documents):
            self.rebuild_called = True
            return len(documents)

    fake_rag = FakeRAG()

    monkeypatch.setattr("app.main.rag_service", fake_rag)
    monkeypatch.setattr("app.main.load_documents", lambda zone, scope: [])

    _initialize_rag_service()

    assert fake_rag.memory_service.reset_called is True
    assert fake_rag.rebuild_called is False


@pytest.mark.anyio
async def test_lifespan_swallow_init_exception_and_continue(monkeypatch):
    called = {"init": 0}

    def fake_init():
        called["init"] += 1
        raise RuntimeError("boom init")

    monkeypatch.setattr("app.main._initialize_rag_service", fake_init)

    async with lifespan(app):
        assert called["init"] == 1


def test_rebuild_uses_defaults_when_payload_fields_are_null(monkeypatch):
    captured = {}

    class FakeRAG:
        def __init__(self):
            self.memory_service = FakeMemoryService()
            self.zone = None
            self.scope = None

        def rebuild_index(self, documents):
            captured["documents"] = documents
            return len(documents)

    def fake_load_documents(*, zone, scope):
        captured["zone"] = zone
        captured["scope"] = scope
        return ["doc1"]

    monkeypatch.setattr("app.main.rag_service", FakeRAG())
    monkeypatch.setattr("app.main.load_documents", fake_load_documents)

    client = TestClient(app)

    response = client.post(
        "/rebuild",
        json={"zone": None, "scope": None},
    )

    assert response.status_code == 200
    assert isinstance(captured["zone"], str)
    assert isinstance(captured["scope"], str)
    assert captured["documents"] == ["doc1"]