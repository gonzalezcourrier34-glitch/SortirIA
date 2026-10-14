"""Tests unitaires de `src/event/openagenda_mapper.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from src.event.openagenda_mapper import get_path, get_start_and_end, map_openagenda_event

from .conftest import make_openagenda_item


def test_map_openagenda_event_fills_all_fields(openagenda_item) -> None:
    event = map_openagenda_event(openagenda_item, extracted_at="2026-10-13T02:00:00+02:00")

    assert event is not None
    assert event.title == "Concert Jazz au Jam"
    assert event.description == "Soirée jazz & cocktails."  # longDescription prioritaire, HTML retiré
    assert event.start_at == "2026-10-24T20:30:00+02:00"
    assert event.end_at == "2026-10-24T23:00:00+02:00"
    assert event.time_known is True
    assert (event.venue_name, event.city, event.postal_code) == ("Le Jam", "Montpellier", "34070")
    assert (event.price_min, event.price_max, event.is_free) == (10.0, 15.0, False)
    assert event.keywords == ["jazz", "concert"]
    assert event.source_event_id == "12345"
    assert event.url.startswith("https://openagenda.com/")
    assert event.image_url == "https://cdn.openagenda.com/main/jazz.jpg"
    assert len(event.event_id) == 16


def test_map_returns_none_without_title() -> None:
    assert map_openagenda_event(make_openagenda_item(title={})) is None


def test_flat_fields_are_supported() -> None:
    item = make_openagenda_item(location={}, city="Lattes", location_name="Arena", timings=[])
    item.update(firstDate="2026-11-02", lastDate="2026-11-03")
    event = map_openagenda_event(item)

    assert event is not None
    assert (event.city, event.venue_name) == ("Lattes", "Arena")
    assert event.start_at.startswith("2026-11-02")
    assert event.time_known is False


def test_get_start_and_end_uses_first_and_last_timings() -> None:
    item = {"timings": [
        {"start": "2026-10-25T18:00:00Z", "end": "2026-10-25T19:00:00Z"},
        {"start": "2026-10-24T18:00:00Z", "end": "2026-10-24T19:00:00Z"},
    ]}
    start, end, known = get_start_and_end(item)
    assert start.startswith("2026-10-24T20:00")
    # Le 25/10/2026, la France passe à l'heure d'hiver (UTC+1) : 19 h UTC = 20 h.
    assert end == "2026-10-25T20:00:00+01:00"
    assert known is True


def test_get_start_and_end_combines_date_and_time() -> None:
    start, _, known = get_start_and_end({"firstDate": "2026-10-24", "firstTimeStart": "20:30"})
    assert start == "2026-10-24T20:30:00+02:00"
    assert known is True


def test_get_path_returns_first_non_empty_value() -> None:
    data = {"a": {"b": ""}, "c": 3}
    assert get_path(data, ("a", "b"), ("c",)) == 3
    assert get_path(data, ("x", "y")) is None


def test_invalid_image_is_dropped_but_event_is_kept() -> None:
    event = map_openagenda_event(make_openagenda_item(image={"filename": "sans-base.jpg"}))
    assert event is not None
    assert event.image_url == ""


def test_category_comes_from_tags_when_category_is_empty() -> None:
    item = make_openagenda_item(
        category=None,
        tags=[{"label": "Exposition", "slug": "exposition"}, {"label": "Ouverture exceptionnelle"}],
        tagGroups=[{"slug": "public", "tags": [{"label": "Tout public"}]}],
    )
    event = map_openagenda_event(item)
    assert event.category == "Exposition"
    assert {"Exposition", "Ouverture exceptionnelle", "Tout public"} <= set(event.keywords)
