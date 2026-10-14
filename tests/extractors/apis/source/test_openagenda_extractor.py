"""Tests unitaires de `src/extractors/apis/source/openagenda_extractor.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026). Le réseau
est simulé : `fetch_json_object` est remplacé par une fausse API.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import src.extractors.apis.source.openagenda_extractor as module
from src.event.event_pipeline import event_from_article
from src.extractors.apis.api_extractor import ApiAuthenticationError

SOURCE: dict[str, Any] = {
    "name": "OpenAgenda - Montpellier",
    "source_id": "openagenda",
    "type": "api",
    "role": "acquisition",
    "endpoint": "https://openagenda.com/agendas",
    "agendas_search_url": "https://api.openagenda.com/v2/agendas",
    "zone": "Montpellier",
    "page_size": 2,
    "max_pages": 5,
    "max_articles": 50,
    "days_forward": 30,
    "filters": {"require_title": True, "require_url": True},
}


def make_event(uid: int) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": {"fr": f"Événement {uid}"},
        "canonicalUrl": f"https://openagenda.com/e/{uid}",
        "location": {"name": "Salle", "city": "Montpellier"},
        "timings": [{"start": "2030-01-10T19:00:00Z", "end": "2030-01-10T21:00:00Z"}],
    }


class FakeOpenAgenda:
    """Deux agendas ; l'événement 3 est partagé par les deux."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.pages = {"A": [[make_event(1), make_event(2)], [make_event(3)]], "B": [[make_event(3), make_event(4)], []]}

    def __call__(self, url: str, params: dict | None = None, headers: dict | None = None, **_: Any) -> dict:
        self.calls.append((url, dict(params or {})))
        if url.endswith("/v2/agendas"):
            return {"agendas": [{"uid": "A"}, {"uid": "B"}]}
        agenda = url.split("/")[-2]
        page = params["offset"] // params["limit"]
        events = self.pages[agenda][page] if page < len(self.pages[agenda]) else []
        return {"events": events}


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeOpenAgenda:
    api = FakeOpenAgenda()
    monkeypatch.setattr(module, "fetch_json_object", api)
    monkeypatch.setattr(module, "get_api_key", lambda: "cle-test")
    return api


def test_iter_items_walks_agendas_and_pages_without_duplicates(fake_api) -> None:
    items = list(module.iter_openagenda_items(SOURCE, 50))

    assert [item["uid"] for _, _, item in items] == [1, 2, 3, 4]
    assert items[2][2]["_agenda_uid"] == "A"
    # Agenda A : page 0 pleine, page 1 incomplète -> arrêt. Agenda B : page 0 pleine, page 1 vide.
    event_calls = [params for url, params in fake_api.calls if "events.json" in url]
    assert [p["offset"] for p in event_calls] == [0, 2, 0, 2]
    assert event_calls[0]["key"] == "cle-test"


def test_iter_items_respects_maximum(fake_api) -> None:
    assert len(list(module.iter_openagenda_items(SOURCE, 3))) == 3


def test_configured_agendas_skip_the_search(fake_api) -> None:
    list(module.iter_openagenda_items({**SOURCE, "agenda_uids": ["B"]}, 50))
    assert not any(url.endswith("/v2/agendas") for url, _ in fake_api.calls)


def test_full_extraction_produces_events(fake_api) -> None:
    result = module.extract_articles_from_source(SOURCE)

    events = [event_from_article(article) for article in result.articles]
    assert result.status in {"success", "partial_success"}
    assert sorted(event.source_event_id for event in events) == ["1", "2", "3", "4"]
    assert events[0].city == "Montpellier"


def test_build_article_skips_events_without_title() -> None:
    assert module.build_openagenda_article({"uid": 1}, 0, "x", SOURCE) == {}


def test_validate_item() -> None:
    assert module.validate_openagenda_item({"title": "t", "uid": 1}, {}, SOURCE) == (True, "")
    assert module.validate_openagenda_item({"uid": 1}, {}, SOURCE)[0] is False
    assert module.validate_openagenda_item("x", {}, SOURCE)[0] is False


def test_date_window() -> None:
    assert module.get_date_window({"days_forward": 7}, today=date(2026, 10, 13)) == ("2026-10-13", "2026-10-20")


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAGENDA_API_KEY", raising=False)
    monkeypatch.setattr(type(module.OPENAGENDA_EXTRACTOR), "api_key", property(lambda self: ""))
    with pytest.raises(ApiAuthenticationError):
        module.get_api_key()


# --- Filtre géographique (ajouté après la première collecte réelle) ---

AREA = {**SOURCE, "center_latitude": 43.6108, "center_longitude": 3.8767, "radius_km": 20, "cities": ["Montpellier"]}


def test_distance_montpellier_paris_is_about_600_km() -> None:
    assert 590 < module.distance_km(43.6108, 3.8767, 48.8566, 2.3522) < 610


def test_is_in_area_uses_gps_when_available() -> None:
    lattes = {"location": {"city": "Lattes", "latitude": 43.567, "longitude": 3.909}}
    paris = {"location": {"city": "Paris", "latitude": 48.8566, "longitude": 2.3522}}
    assert module.is_in_area(lattes, AREA)
    assert not module.is_in_area(paris, AREA)


def test_is_in_area_falls_back_to_city_name() -> None:
    assert module.is_in_area({"location": {"city": "MONTPELLIER"}}, AREA)
    assert not module.is_in_area({"location": {"city": "Lyon"}}, AREA)


def test_far_events_do_not_consume_the_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    far = [{**make_event(i), "location": {"city": "Paris", "latitude": 48.85, "longitude": 2.35}} for i in range(1, 4)]
    near = [{**make_event(i), "location": {"city": "Montpellier", "latitude": 43.61, "longitude": 3.88}} for i in range(10, 12)]
    monkeypatch.setattr(module, "get_api_key", lambda: "cle")
    monkeypatch.setattr(module, "search_agenda_uids", lambda source, key: ["A"])
    pages = [far + near[:1], near[1:]]
    monkeypatch.setattr(module, "fetch_agenda_page", lambda source, uid, key, offset, size: pages[offset // size] if offset // size < len(pages) else [])

    items = list(module.iter_openagenda_items({**AREA, "page_size": 4}, 2))

    assert [item["uid"] for _, _, item in items] == [10, 11]


def test_several_search_terms_are_merged_without_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"Montpellier": [{"uid": "A"}, {"uid": "B"}], "Hérault": [{"uid": "B"}, {"uid": "C"}]}
    monkeypatch.setattr(module, "fetch_json_object", lambda url, params=None, headers=None, **_: {"agendas": answers[params["search"]]})
    uids = module.search_agenda_uids({**SOURCE, "search_terms": ["Montpellier", "Hérault"]}, "cle")
    assert uids == ["A", "B", "C"]


# --- Plusieurs zones : Montpellier + bassin de Thau ---

ZONES = {**SOURCE, "areas": [
    {"name": "Montpellier", "latitude": 43.6108, "longitude": 3.8767, "radius_km": 20},
    {"name": "Bassin de Thau", "latitude": 43.42, "longitude": 3.62, "radius_km": 15},
]}


@pytest.mark.parametrize(
    ("city", "lat", "lon", "expected"),
    [
        ("Montpellier", 43.611, 3.877, True),
        ("Sète", 43.403, 3.697, True),
        ("Marseillan", 43.356, 3.531, True),
        ("Frontignan", 43.448, 3.756, True),
        ("Béziers", 43.344, 3.215, False),
        ("Nîmes", 43.837, 4.360, False),
    ],
)
def test_montpellier_and_thau_areas(city, lat, lon, expected) -> None:
    item = {"location": {"city": city, "latitude": lat, "longitude": lon}}
    assert module.is_in_area(item, ZONES) is expected


def test_old_single_center_format_still_works() -> None:
    assert module.get_areas({"center_latitude": 43.6, "center_longitude": 3.87, "radius_km": 5}) == [
        {"latitude": 43.6, "longitude": 3.87, "radius_km": 5.0}
    ]
