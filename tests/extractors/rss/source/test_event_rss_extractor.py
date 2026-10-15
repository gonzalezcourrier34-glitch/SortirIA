"""Tests de l'adaptateur « RSS d'agenda » à travers le moteur RSS de CheckIt.AI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

import src.extractors.rss.source.event_rss_extractor as module
from config.constants import EXTRACTOR_STATUS_SUCCESS
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import event_from_article
from src.extractors.rss.rss_engine import extract_rss_source

SOURCE: dict[str, Any] = {
    "name": "Salle Exemple", "source_id": "salle_exemple", "type": "rss", "enabled": True, "role": "acquisition",
    "url": "https://salle.example/agenda/feed/", "event_date": "published", "venue_name": "Salle Exemple",
    "city": "Sète", "credit": "Salle Exemple", "days_forward": 60, "max_articles": 50,
    "filters": {"require_title": True, "require_url": True, "min_title_length": 3, "min_text_length": 0,
                "min_total_text_length": 3, "remove_duplicates": True},
}
ENTRIES = [
    {"title": "Concert rock", "link": "https://salle.example/rock", "id": "1",
     "published": "Sat, 07 Nov 2026 20:30:00 +0100", "summary": "Trois groupes locaux."},
    {"title": "Concert rock", "link": "https://salle.example/rock", "id": "1",
     "published": "Sat, 07 Nov 2026 20:30:00 +0100", "summary": "Trois groupes locaux."},   # doublon
    {"title": "Soirée passée", "link": "https://salle.example/old", "id": "2",
     "published": "Thu, 01 Oct 2026 20:00:00 +0200"},
    {"title": "Sans date", "link": "https://salle.example/x", "id": "3"},
    {"title": "Bal folk", "link": "https://salle.example/bal", "id": "4",
     "published": "Sun, 15 Nov 2026 21:00:00 +0100"},
]


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 14, 12, 0, tzinfo=tz or PARIS)


@pytest.fixture(autouse=True)
def fake_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    monkeypatch.setattr(module, "iter_event_entries", lambda source: ENTRIES)
    adapter = module.RssAdapter(
        iter_items=module.iter_event_entries, build_article=module.build_event_rss_article,
        validate_item=module.validate_event_entry, default_name="Flux RSS d'agenda",
    )
    monkeypatch.setattr(module, "EVENT_RSS_ADAPTER", adapter)


def test_feed_through_the_checkit_engine() -> None:
    result = extract_rss_source(SOURCE, module.EVENT_RSS_ADAPTER)
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    events = [event_from_article(article) for article in result.articles]
    assert [(e.title, e.start_at, e.city, e.source) for e in events] == [
        ("Concert rock", "2026-11-07T20:30:00+01:00", "Sète", "rss"),
        ("Bal folk", "2026-11-15T21:00:00+01:00", "Sète", "rss"),
    ]
    assert result.rejection_reasons == {"doublon": 1, "hors_fenetre": 1, "date_evenement_absente": 1}


def test_no_feed_configured_by_default() -> None:
    assert module.EVENT_RSS_EXTRACTOR.get_sources() == []
