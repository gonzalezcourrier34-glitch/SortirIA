"""Tests unitaires de `src/event/event_pipeline.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from dataclasses import replace

from src.event.event_pipeline import event_from_article, prepare_events
from src.event.openagenda_mapper import map_openagenda_event


def as_article(event) -> dict:
    return {"title": event.title, "metadata": {"event": event.to_dict()}}


def test_event_from_article_round_trip(openagenda_item) -> None:
    event = map_openagenda_event(openagenda_item)
    assert event_from_article(as_article(event)) == event
    assert event_from_article({"metadata": {}}) is None


def test_prepare_events_counts_everything(openagenda_item, now) -> None:
    good = map_openagenda_event(openagenda_item)
    duplicate = replace(good, source="autre")
    past = replace(good, event_id="x", start_at="2026-01-01T20:00:00+01:00", end_at="")
    articles = [as_article(good), as_article(duplicate), as_article(past), {"title": "inconnu"}]

    events, report = prepare_events(articles, now)

    assert [event.event_id for event in events] == [good.event_id]
    assert report.to_dict() == {
        "events_extracted": 4,
        "events_rejected": 2,
        "duplicates_merged": 1,
        "cross_source_merged": 0,
        "rejection_reasons": {"evenement_passe": 1, "format_inconnu": 1},
    }
