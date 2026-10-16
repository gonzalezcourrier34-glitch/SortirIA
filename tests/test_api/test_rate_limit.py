"""Tests de la limite de débit des routes publiques (16/10/2026)."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import rate_limit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window_refuses_then_frees_a_slot() -> None:
    clock = Clock()
    limiter = rate_limit.SlidingWindowLimiter(3, 60, clock)
    assert [limiter.hit("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.hit("a") == 60.0                     # 4e appel : attendre que le 1er sorte
    assert limiter.hit("b") == 0.0                      # un autre visiteur n'est pas gêné
    clock.now += 30
    assert limiter.hit("a") == 30.0
    clock.now += 31
    assert limiter.hit("a") == 0.0                      # le 1er appel est sorti de la fenêtre


def test_zero_disables_a_limit() -> None:
    limiter = rate_limit.SlidingWindowLimiter(0, 60)
    assert all(limiter.hit("a") == 0.0 for _ in range(100))


def test_tracked_visitors_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "MAX_TRACKED_KEYS", 5)
    limiter = rate_limit.SlidingWindowLimiter(1, 60)
    for index in range(20):
        limiter.hit(f"ip{index}")
    assert len(limiter._calls) == 5


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(rate_limit, "SEARCH_PER_MINUTE", rate_limit.SlidingWindowLimiter(2, 60))
    monkeypatch.setattr(rate_limit, "SEARCH_PER_HOUR", rate_limit.SlidingWindowLimiter(100, 3600))
    monkeypatch.setattr(rate_limit, "SEARCH_GLOBAL_PER_HOUR", rate_limit.SlidingWindowLimiter(3, 3600))
    api = FastAPI()

    @api.post("/recherche", dependencies=[Depends(rate_limit.limit_search)])
    def search() -> dict[str, str]:
        return {"ok": "oui"}

    return TestClient(api)


def test_search_is_limited_per_visitor_then_for_everyone(client: TestClient) -> None:
    first = {"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}
    assert [client.post("/recherche", headers=first).status_code for _ in range(2)] == [200, 200]
    refused = client.post("/recherche", headers=first)
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) >= 1
    assert "réessayez" in refused.json()["detail"]
    # Un autre visiteur passe, jusqu'au plafond du service (3 recherches acceptées en tout).
    assert client.post("/recherche", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    everyone = client.post("/recherche", headers={"X-Forwarded-For": "3.3.3.3"})
    assert everyone.status_code == 429 and "beaucoup de questions" in everyone.json()["detail"]


def test_visitor_address_comes_from_the_proxy_header() -> None:
    from starlette.requests import Request

    def request(headers: dict[str, str]) -> Request:
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                        "client": ("9.9.9.9", 1234)})

    assert rate_limit.client_key(request({"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})) == "1.2.3.4"
    assert rate_limit.client_key(request({})) == "9.9.9.9"
