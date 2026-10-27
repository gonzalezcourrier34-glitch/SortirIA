"""Tests des routes de l'interface (/api/v1) : application minimale, faux service."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import interface_api, search_cards, search_detail, search_log, search_service


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from app import rate_limit

    for limiter in rate_limit.ALL_LIMITERS:        # chaque test part de zéro
        limiter.reset()
    def fake_search(connection, question, filters, services=None, context="", follow_up=False,
                    visitor_key=None):
        yield search_service.progress("comprendre", "completed", filters.describe())
        if visitor_key:
            yield search_service.progress("comprendre", "completed", f"visiteur : {visitor_key}")
        if context:
            relance = " (relance cliquée)" if follow_up else ""
            yield search_service.progress("comprendre", "completed", f"suite de : {context}{relance}")
        if question == "panne":
            raise RuntimeError("base indisponible")
        yield {"event": "completed", "result": {"answer": f"Pour : {question}", "events": []}}

    monkeypatch.setattr(search_service, "run_search", fake_search)
    monkeypatch.setattr(search_detail, "event_detail",
                        lambda connection, event_id, visitor_key=None:
                        ({"event_id": event_id, "visitor": visitor_key} if event_id == "e1" else None))
    monkeypatch.setattr(search_cards, "partner_highlights", lambda connection, limit: [{"limit": limit}])
    monkeypatch.setattr(search_cards, "active_sources", lambda connection: [{"name": "OpenAgenda", "events": 3}])
    monkeypatch.setattr(search_log, "database_status", lambda connection: "ok")
    monkeypatch.setattr(search_log, "collect_health",
                        lambda connection: {"statut": "success", "heures_depuis_reussite": 14.2})
    monkeypatch.setattr(search_detail, "events_to_rate",
                        lambda connection, ids, session: [{"event_id": i} for i in ids if i == "hier"])

    def fake_rating(connection, event_id, session, stars, comment, **kwargs):
        if event_id == "futur":
            return {"status": "hors_delai", "event_id": event_id}
        return {"status": "enregistree", "event_id": event_id, "rating_id": 1,
                "comment_status": "publie" if comment else "aucun"}

    monkeypatch.setattr(search_log, "record_feedback",
                        lambda connection, request_id, rating, comment="": request_id == 7)

    import src.event.event_ratings as ratings
    monkeypatch.setattr(ratings, "submit_rating", fake_rating)

    @contextmanager
    def fake_connect():
        yield None

    api = FastAPI()
    api.include_router(interface_api.router)
    class FakeConnection:
        def commit(self) -> None:
            pass

    api.dependency_overrides[interface_api.get_connection] = lambda: FakeConnection()
    api.dependency_overrides[interface_api.get_connection_factory] = lambda: fake_connect
    api.dependency_overrides[interface_api.get_search_services] = lambda: None
    return TestClient(api)


def events_of(text: str) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for chunk in text.strip().split("\n\n"):
        name, data = chunk.split("\n")
        found.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return found


def test_search_stream(client: TestClient) -> None:
    response = client.post("/api/v1/recherche/stream",
                           json={"question": "concert", "public": "soiree", "zone": "thau", "period": "weekend"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = events_of(response.text)
    assert [name for name, _ in events] == ["progress", "completed"]
    assert events[0][1]["message"] == "ce week-end · soirée · bassin de Thau"
    assert events[1][1]["result"]["answer"] == "Pour : concert"


def test_search_stream_passes_the_conversation_context(client: TestClient) -> None:
    events = events_of(client.post("/api/v1/recherche/stream",
                                   json={"question": "et pour les enfants ?", "context": "concert samedi"}).text)
    assert events[1][1]["message"] == "suite de : concert samedi"


def test_search_stream_passes_a_clicked_follow_up(client: TestClient) -> None:
    events = events_of(client.post("/api/v1/recherche/stream",
                                   json={"question": "Des spectacles en intérieur ?", "context": "un spectacle à Sète",
                                         "follow_up": True}).text)
    assert events[1][1]["message"] == "suite de : un spectacle à Sète (relance cliquée)"


def test_search_stream_keeps_only_the_visitor_fingerprint(client: TestClient) -> None:
    # 27/10/2026 : le journal ne recevait pas l'identifiant du navigateur, les
    # visiteurs distincts ne se comptaient pas. Seule l'empreinte salée passe.
    from src.event.event_ratings import user_key

    session = "session-de-test-0123456789"
    events = events_of(client.post("/api/v1/recherche/stream",
                                   json={"question": "concert", "session_id": session}).text)
    assert events[1][1]["message"] == f"visiteur : {user_key(session)}"
    assert session not in events[1][1]["message"]


def test_search_stream_without_session_has_no_visitor(client: TestClient) -> None:
    events = events_of(client.post("/api/v1/recherche/stream", json={"question": "concert"}).text)
    assert [name for name, _ in events] == ["progress", "completed"]
    assert client.post("/api/v1/recherche/stream",
                       json={"question": "concert", "session_id": "court"}).status_code == 422


def test_search_error_is_sent_in_the_stream(client: TestClient) -> None:
    events = events_of(client.post("/api/v1/recherche/stream", json={"question": "panne"}).text)
    assert events[-1][0] == "error" and "Réessayez" in events[-1][1]["message"]


def test_search_refuses_unknown_filters(client: TestClient) -> None:
    assert client.post("/api/v1/recherche/stream", json={"zone": "paris"}).status_code == 422
    assert client.post("/api/v1/recherche/stream", json={"question": "x" * 501}).status_code == 422


def test_detail_highlights_and_sources(client: TestClient) -> None:
    assert client.get("/api/v1/evenements/e1").json() == {"event_id": "e1", "visitor": None}
    # L'identifiant du navigateur n'arrive jamais en clair au service : son empreinte salée.
    own = client.get("/api/v1/evenements/e1?visiteur=session-de-test-0123456789").json()
    assert own["visitor"] and own["visitor"] != "session-de-test-0123456789"
    assert client.get("/api/v1/evenements/e1?visiteur=court").status_code == 422
    assert client.get("/api/v1/evenements/inconnu").status_code == 404
    assert client.get("/api/v1/partenaires/a-ne-pas-manquer?limit=2").json() == [{"limit": 2}]
    assert client.get("/api/v1/partenaires/a-ne-pas-manquer?limit=50").status_code == 422
    assert client.get("/api/v1/sources").json() == [{"name": "OpenAgenda", "events": 3}]


def test_rating_routes(client: TestClient) -> None:
    session = "session-de-test-0123456789"
    sent = client.post("/api/v1/evenements/hier/avis", json={"session_id": session, "stars": 4, "comment": "Top"})
    assert sent.status_code == 201 and sent.json() == {"status": "enregistree", "comment_status": "publie"}
    early = client.post("/api/v1/evenements/futur/avis", json={"session_id": session, "stars": 4})
    assert early.status_code == 409
    assert client.post("/api/v1/evenements/hier/avis", json={"session_id": session, "stars": 6}).status_code == 422
    assert client.post("/api/v1/evenements/hier/avis", json={"session_id": "court", "stars": 3}).status_code == 422
    to_rate = client.post("/api/v1/avis/a-donner", json={"session_id": session, "event_ids": ["futur", "hier"]})
    assert to_rate.json() == [{"event_id": "hier"}]


def test_answer_feedback_route(client: TestClient) -> None:
    assert client.post("/api/v1/reponses/7/avis", json={"rating": 1}).status_code == 201
    assert client.post("/api/v1/reponses/7/avis", json={"rating": -1, "comment": "Rien à Sète"}).json() == {
        "status": "enregistre"}
    assert client.post("/api/v1/reponses/8/avis", json={"rating": 1}).status_code == 404
    assert client.post("/api/v1/reponses/7/avis", json={"rating": 0}).status_code == 422
    assert client.post("/api/v1/reponses/7/avis", json={"rating": 1, "comment": "x" * 301}).status_code == 422


def test_health_route_reports_the_last_collect(client: TestClient) -> None:
    assert client.get("/api/v1/sante").json() == {
        "api": "ok", "base": "ok", "collecte": {"statut": "success", "heures_depuis_reussite": 14.2}}


def test_health_route_asks_the_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """23/10/2026 : « base » était écrit en dur ; il vient maintenant d'un vrai SELECT 1."""

    asked: list[Any] = []
    monkeypatch.setattr(search_log, "database_status", lambda connection: asked.append(connection) or "erreur")
    assert client.get("/api/v1/sante").json()["base"] == "erreur"
    assert len(asked) == 1
