"""Tests de l'API principale (app/main.py), après le retrait des routes héritées (15/10/2026)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_docs_redirect() -> None:
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"] == "/docs"


def test_only_sortiria_routes_remain() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/v1/recherche/stream" in paths and "/events/{event_id}/notes" in paths
    assert not {"/ask", "/ask/debug", "/rebuild"} & paths          # routes FAISS retirées
