"""Tests du moteur iCal : plusieurs calendriers, dont un en panne (sans réseau)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import src.extractors.ical.ical_extractor as extractor
from config.constants import (
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
)
from config.source_config import build_source_config
from src.event.event_pipeline import event_from_article
from src.extractors.files.file_adapter import FileSourceAdapter

FUTURE = "20991018T203000"


def calendar(*titles: str) -> str:
    events = "".join(
        f"BEGIN:VEVENT\r\nUID:{i}@x\r\nSUMMARY:{title}\r\nDTSTART;TZID=Europe/Paris:{FUTURE}\r\nEND:VEVENT\r\n"
        for i, title in enumerate(titles)
    )
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{events}END:VCALENDAR\r\n"


# Mêmes filtres que le rôle « acquisition » de config/sources.yaml.
ACQUISITION_FILTERS = {"require_title": True, "require_url": True, "min_title_length": 3,
                       "min_text_length": 0, "min_total_text_length": 3, "remove_duplicates": True}


def source(name: str, url: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "source_id": name.lower().replace(" ", "_"), "url": url, "max_articles": 50,
            "venue_name": name, "city": "Montpellier", "website": f"https://{name.lower().replace(' ', '')}.fr",
            "days_forward": 365 * 80, "filters": ACQUISITION_FILTERS, **extra}


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = {
        "https://a.fr/agenda.ics": calendar("Concert A", "Théâtre A"),
        "https://b.fr/agenda.ics": calendar("Concert A", "Expo B"),   # « Concert A » en double ? autre lieu
        "https://html.fr/agenda.ics": "<html>pas un calendrier</html>",
    }

    def fake_fetch(url: str) -> str:
        if url == "https://panne.fr/agenda.ics":
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        return documents[url]

    monkeypatch.setattr(extractor, "fetch_calendar_text", fake_fetch)


def test_one_failing_calendar_does_not_stop_the_others(fake_http) -> None:
    result = extractor.extract_articles_from_sources([
        source("Lieu A", "https://a.fr/agenda.ics"),
        source("Lieu en panne", "https://panne.fr/agenda.ics"),
        source("Lieu B", "https://b.fr/agenda.ics"),
    ])
    assert result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
    titles = sorted(event_from_article(a).title for a in result.articles)
    # Même titre dans deux lieux différents : deux événements distincts.
    assert titles == ["Concert A", "Concert A", "Expo B", "Théâtre A"]
    assert any("HTTP 404" in error for error in result.errors)


def test_document_that_is_not_a_calendar_fails_cleanly(fake_http) -> None:
    result = extractor.extract_articles_from_sources([source("Lieu HTML", "https://html.fr/agenda.ics")])
    assert result.status == EXTRACTOR_STATUS_FAILED
    assert any("pas un calendrier" in error for error in result.errors)


def test_disabled_calendar_is_empty(fake_http) -> None:
    result = extractor.extract_articles_from_sources([source("Lieu A", "https://a.fr/agenda.ics", enabled=False)])
    assert result.status == EXTRACTOR_STATUS_EMPTY


def test_article_carries_event_and_credit(fake_http) -> None:
    result = extractor.extract_articles_from_sources([source("Lieu A", "https://a.fr/agenda.ics")])
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    event = event_from_article(result.articles[0])
    assert (event.source, event.source_credit, event.venue_name, event.url) == ("ical", "Lieu A", "Lieu A", "https://lieua.fr")
    assert result.articles[0]["source_type"] == "ical"


def test_adapter_contract() -> None:
    with pytest.raises(TypeError):
        FileSourceAdapter(iter_items="pas une fonction", build_article=lambda item, src: {})


def test_config_requires_a_valid_url() -> None:
    defaults = {"common": {"language": "fr", "country": "France", "category": "evenement", "max_articles": 10}}
    ok = build_source_config({"name": "Lieu", "source_id": "lieu", "type": "ical", "role": "acquisition",
                              "url": "https://lieu.fr/a.ics", "city": "Sète"}, "ical", defaults)
    assert ok.validate("ical") == [] and ok.to_dict()["city"] == "Sète"
    bad = build_source_config({"name": "Lieu", "source_id": "lieu", "type": "ical", "role": "acquisition",
                               "url": "pas-une-url"}, "ical", defaults)
    assert any("url" in error for error in bad.validate("ical"))
