"""Tests de la conversion d'une entrée de flux RSS d'agenda en `Event`."""

from __future__ import annotations

from datetime import date

from src.event import rss_event_mapper as mapper

TODAY, HORIZON = date(2026, 10, 14), date(2026, 12, 13)
SOURCE = {"source_id": "salle", "name": "Salle Exemple", "venue_name": "Salle Exemple", "city": "sète",
          "credit": "Salle Exemple", "event_date": "published"}


def test_event_module_dates_and_location() -> None:
    entry = {"title": "Concert <b>jazz</b>", "link": "https://salle.example/jazz", "id": "jazz-1",
             "ev_startdate": "2026-11-14T20:30:00+01:00", "ev_enddate": "2026-11-14T23:00:00+01:00",
             "ev_location": "Scène du port", "summary": "<p>Quartet de jazz.</p>",
             "tags": [{"term": "Concert"}]}
    event, reason = mapper.map_rss_event(entry, {**SOURCE, "event_date": ""}, today=TODAY, horizon=HORIZON)
    assert reason == ""
    assert (event.source, event.title, event.venue_name, event.city) == ("rss", "Concert jazz", "Scène du port", "Sète")
    assert (event.start_at, event.end_at, event.category) == (
        "2026-11-14T20:30:00+01:00", "2026-11-14T23:00:00+01:00", "Concert")
    assert event.description == "Quartet de jazz." and event.source_credit == "Salle Exemple"


def test_publication_date_is_the_event_date_when_configured() -> None:
    entry = {"title": "Soirée salsa", "link": "https://salle.example/salsa", "published": "Fri, 13 Nov 2026 21:00:00 +0100"}
    event, _ = mapper.map_rss_event(entry, SOURCE, today=TODAY, horizon=HORIZON)
    assert event.start_at == "2026-11-13T21:00:00+01:00" and event.venue_name == "Salle Exemple"
    assert "soiree" in event.audiences


def test_publication_date_is_ignored_otherwise() -> None:
    entry = {"title": "Actualité", "published": "Fri, 13 Nov 2026 21:00:00 +0100"}
    assert mapper.map_rss_event(entry, {**SOURCE, "event_date": ""}, today=TODAY, horizon=HORIZON) == (
        None, mapper.REASON_NO_EVENT_DATE)


def test_window_title_and_city() -> None:
    past = {"title": "Passé", "published": "Thu, 01 Oct 2026 20:00:00 +0200"}
    far = {"title": "Lointain", "published": "Fri, 01 Jan 2027 20:00:00 +0100"}
    assert mapper.map_rss_event(past, SOURCE, today=TODAY, horizon=HORIZON) == (None, "")
    assert mapper.map_rss_event(far, SOURCE, today=TODAY, horizon=HORIZON) == (None, "")
    assert mapper.map_rss_event({"title": " ", "published": "x"}, SOURCE, today=TODAY, horizon=HORIZON) == (
        None, mapper.REASON_NO_TITLE)
    entry = {"title": "Bal", "published": "Fri, 13 Nov 2026 21:00:00 +0100"}
    assert mapper.map_rss_event(entry, {**SOURCE, "city": ""}, today=TODAY, horizon=HORIZON) == (
        None, mapper.REASON_NO_CITY)


def test_day_only_date() -> None:
    entry = {"title": "Vide-grenier", "link": "https://salle.example/vg", "ev_startdate": "2026-11-15"}
    event, _ = mapper.map_rss_event(entry, SOURCE, today=TODAY, horizon=HORIZON)
    assert event.time_known is False and event.start_at == "2026-11-15T00:00:00+01:00"
