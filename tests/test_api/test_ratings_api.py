"""Tests des routes de notes (API) : application minimale, faux dépôt."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ratings_api
from app.security import require_api_key


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    saved: list[dict[str, Any]] = []

    def fake_submit(connection, event_id, session_id, stars, comment=""):
        saved.append({"event_id": event_id, "stars": stars, "comment": comment})
        if event_id == "trop-tard":
            return {"status": "hors_delai", "event_id": event_id}
        return {"status": "enregistree", "event_id": event_id, "rating_id": 1, "comment_status": "publie"}

    import src.event.event_ratings as service
    from src.event import event_repository

    monkeypatch.setattr(service, "submit_rating", fake_submit)
    monkeypatch.setattr(event_repository, "fetch_event_ratings", lambda connection, event_id: [
        {"stars": 5, "comment": "Génial", "comment_status": "publie", "updated_at": "2026-10-20"}])
    api = FastAPI()
    api.include_router(ratings_api.router)
    api.dependency_overrides[ratings_api.get_connection] = lambda: None
    api.dependency_overrides[require_api_key] = lambda: "test"
    test_client = TestClient(api)
    test_client.saved = saved
    return test_client


def test_rate_and_read(client: TestClient) -> None:
    response = client.post("/events/e1/notes", json={"session_id": "session-1234", "stars": 4, "comment": "Bien"})
    assert response.status_code == 201 and response.json()["status"] == "enregistree"
    assert client.get("/events/e1/notes").json() == {
        "event_id": "e1", "ratings": 1, "average_stars": 5.0,
        "comments": [{"stars": 5, "comment": "Génial", "date": "2026-10-20"}]}


def test_refusals(client: TestClient) -> None:
    assert client.post("/events/trop-tard/notes", json={"session_id": "session-1234", "stars": 4}).status_code == 409
    assert client.post("/events/e1/notes", json={"session_id": "session-1234", "stars": 6}).status_code == 422
    assert client.post("/events/e1/notes", json={"session_id": "court", "stars": 3}).status_code == 422


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import security

    monkeypatch.setattr(security, "API_KEY", "la-vraie-cle")
    api = FastAPI()
    api.include_router(ratings_api.router)
    api.dependency_overrides[ratings_api.get_connection] = lambda: None
    assert TestClient(api).get("/events/e1/notes").status_code in (401, 403)
    assert TestClient(api).get("/events/e1/notes", headers={"x-api-key": "mauvaise"}).status_code in (401, 403)
