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


def test_data_routes_ask_robots_not_to_index() -> None:
    """16/10/2026 : en-tête X-Robots-Tag sur /api/, en plus de robots.txt."""

    from pathlib import Path

    client = TestClient(app)
    response = client.get("/api/v1/route-inconnue")
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert "X-Robots-Tag" not in client.get("/health").headers
    robots = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "robots.txt").read_text(encoding="utf-8")
    assert "Disallow: /api/" in robots and "User-agent: GPTBot" in robots
