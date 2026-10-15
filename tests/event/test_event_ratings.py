"""Tests des notes des clients (sans base ni appel réel à Mistral)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.event import event_ratings as ratings


def test_user_key_is_anonymous_stable_and_salted() -> None:
    key = ratings.user_key("session-123", salt="sel")
    assert key == ratings.user_key("session-123", salt="sel") and len(key) == 64
    assert "session-123" not in key
    assert key != ratings.user_key("session-123", salt="autre-sel")
    with pytest.raises(ValueError):
        ratings.user_key("  ", salt="sel")


def test_rating_input_validation() -> None:
    assert ratings.RatingInput(stars=4, comment="  Super   soirée ").comment == "Super soirée"
    for bad in ({"stars": 0}, {"stars": 6}, {"stars": 3, "comment": "x" * 301}):
        with pytest.raises(ValidationError):
            ratings.RatingInput(**bad)


def test_moderation(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    request = httpx.Request("POST", ratings.MODERATION_URL)
    monkeypatch.setenv("MISTRAL_API_KEY", "test")

    def answer(categories):
        return lambda *args, **kwargs: httpx.Response(200, json={"results": [{"categories": categories}]},
                                                      request=request)

    monkeypatch.setattr(httpx, "post", answer({"hate_and_discrimination": False, "pii": False}))
    assert ratings.moderate_with_mistral("Très belle soirée") == ("publie", {"flagged": []})
    monkeypatch.setattr(httpx, "post", answer({"hate_and_discrimination": True, "pii": True}))
    assert ratings.moderate_with_mistral("...") == ("refuse", {"flagged": ["hate_and_discrimination", "pii"]})

    def down(*args, **kwargs):
        raise httpx.ConnectError("injoignable")

    monkeypatch.setattr(httpx, "post", down)
    assert ratings.moderate_with_mistral("Bien") == ("en_attente", {})        # repris la nuit suivante
    assert ratings.moderate_with_mistral("") == ("aucun", {})


def test_summary_shows_only_published_comments() -> None:
    rows = [{"stars": 5, "comment": "Génial", "comment_status": "publie", "updated_at": "2026-10-20"},
            {"stars": 2, "comment": "insulte", "comment_status": "refuse", "updated_at": "2026-10-20"},
            {"stars": 4, "comment": "", "comment_status": "aucun", "updated_at": "2026-10-21"}]
    summary = ratings.summarize_ratings(rows)
    assert (summary["ratings"], summary["average_stars"]) == (3, 3.7)
    assert summary["comments"] == [{"stars": 5, "comment": "Génial", "date": "2026-10-20"}]
    assert ratings.summarize_ratings([]) == {"ratings": 0, "average_stars": None, "comments": []}
