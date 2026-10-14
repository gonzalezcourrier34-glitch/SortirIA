"""Tests de l'extracteur DATAtourisme (sans réseau : fetch_json_object est remplacé)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import src.extractors.apis.source.datatourisme_extractor as module
from src.event.event_pipeline import event_from_article
from src.extractors.apis.api_extractor import ApiAuthenticationError

SOURCE: dict[str, Any] = {
    "name": "DATAtourisme - test",
    "source_id": "datatourisme",
    "endpoint": "https://api.datatourisme.fr/v1/entertainmentAndEvent",
    "areas": [
        {"name": "Montpellier", "latitude": 43.6108, "longitude": 3.8767, "radius_km": 20},
        {"name": "Thau", "latitude": 43.42, "longitude": 3.62, "radius_km": 15},
    ],
    "days_forward": 30,
}


def event(uuid: str, start: str, end: str | None = None) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "uri": f"https://data.datatourisme.fr/{uuid}",
        "label": {"@fr": f"Événement {uuid}"},
        "type": ["Event"],
        "isLocatedAt": [{"geo": {"latitude": 43.6, "longitude": 3.87},
                         "address": [{"addressLocality": "Montpellier", "postalCode": "34000"}]}],
        "takesPlaceAt": [{"startDate": start, "endDate": end or start}],
    }


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Deux pages pour Montpellier, une page pour Thau (avec un doublon)."""

    calls: list[dict[str, Any]] = []
    pages = {
        ("Montpellier", 1): {"objects": [event("a", "2026-10-20"), event("b", "2026-12-25")],
                             "meta": {"next": "https://api.datatourisme.fr/v1/entertainmentAndEvent?page=2&crs=x"}},
        ("Montpellier", 2): {"objects": [event("c", "2026-09-01", "2027-01-31")], "meta": {"next": None}},
        ("Thau", 1): {"objects": [event("a", "2026-10-20"), event("d", "2026-10-15")], "meta": {"next": None}},
    }

    def fake_fetch(url: str, params: Any = None, headers: Any = None, **kwargs: Any) -> dict[str, Any]:
        calls.append({"url": url, "params": params, "headers": headers})
        if params is None:  # lien « next »
            return pages[("Montpellier", 2)]
        zone = "Montpellier" if params["geo_distance"].startswith("43.6108") else "Thau"
        return pages[(zone, 1)]

    monkeypatch.setattr(module, "fetch_json_object", fake_fetch)
    monkeypatch.setattr(module, "get_api_key", lambda: "cle-test")
    monkeypatch.setattr(module, "datetime", _FixedDatetime)
    return calls


class _FixedDatetime(module.datetime):
    @classmethod
    def now(cls, tz=None):  # 14/10/2026, jour de l'exploration
        return module.datetime(2026, 10, 14, 12, 0, tzinfo=tz)


def test_reads_all_pages_and_zones_without_duplicates(fake_api) -> None:
    items = list(module.iter_datatourisme_items(SOURCE, 100))
    uuids = [item["uuid"] for _, _, item in items]
    # b (25/12) est hors fenêtre de 30 jours ; a n'est lu qu'une fois.
    assert uuids == ["a", "c", "d"]
    first_call = fake_api[0]
    assert first_call["headers"]["X-API-Key"] == "cle-test"
    assert first_call["params"]["geo_distance"] == "43.6108,3.8767,20km"
    assert "takesPlaceAt" in first_call["params"]["fields"]
    assert fake_api[1]["params"] is None  # page suivante : URL complète, pas de paramètres


def test_maximum_is_respected(fake_api) -> None:
    assert len(list(module.iter_datatourisme_items(SOURCE, 2))) == 2


def test_window_keeps_running_exhibition() -> None:
    occurrence = {"start_at": "2026-09-01T00:00:00+02:00", "end_at": "2027-01-31T00:00:00+01:00"}
    assert module.is_in_window(occurrence, SOURCE, today=date(2026, 10, 14))
    past = {"start_at": "2026-10-01T00:00:00+02:00", "end_at": ""}
    assert not module.is_in_window(past, SOURCE, today=date(2026, 10, 14))


def test_article_carries_the_event(fake_api) -> None:
    identifier, index, item = next(module.iter_datatourisme_items(SOURCE, 1))
    article = module.build_datatourisme_article(item, index, identifier, SOURCE)
    event = event_from_article(article)
    assert event is not None and event.source == "datatourisme" and event.title == "Événement a"


def test_validation_requires_a_session() -> None:
    assert module.validate_datatourisme_item({"label": "x", "uuid": "u"}, {}, SOURCE) == (False, "seance_datatourisme_absente")


def test_missing_key_gives_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoKey:
        api_key = ""
        name = "DATAtourisme"

    monkeypatch.setattr(module, "DATATOURISME_EXTRACTOR", NoKey())
    with pytest.raises(ApiAuthenticationError):
        module.get_api_key()


def test_sessions_sharing_a_homepage_are_not_merged(fake_api) -> None:
    """Trois séances avec le même site web : trois articles distincts pour le moteur."""

    from src.event.datatourisme_mapper import map_datatourisme_occurrences  # noqa: F401
    item = event("film", "2026-10-20")
    item["hasContact"] = [{"homepage": ["https://cinema.fr/"]}]
    item["takesPlaceAt"] = [{"startDate": d, "endDate": d} for d in ("2026-10-20", "2026-10-21", "2026-10-22")]
    urls = set()
    for index, occurrence in enumerate(module.get_occurrences(item)):
        article = module.build_datatourisme_article({**item, "_occurrence": occurrence}, index, "x", SOURCE)
        urls.add(article["url"])
        assert event_from_article(article).url == "https://cinema.fr/"   # lien affiché inchangé
    assert len(urls) == 3


def test_pagination_stops_when_next_link_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def looping_fetch(url, params=None, headers=None, **kwargs):
        calls.append(url)
        return {"objects": [event("a", "2026-10-20")], "meta": {"next": "https://api.datatourisme.fr/v1/x?crs=meme"}}

    monkeypatch.setattr(module, "fetch_json_object", looping_fetch)
    area = SOURCE["areas"][0]
    assert [obj["uuid"] for obj in module.fetch_area_pages(SOURCE, area, "cle")] == ["a"]
    assert len(calls) == 2   # la 2e page n'apporte rien de nouveau : arrêt
