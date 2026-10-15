"""Tests de l'adaptateur « agenda » à travers le moteur scraper de CheckIt.AI (sans réseau)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

import src.extractors.scrapers.source.agenda_scraper as module
from config.constants import EXTRACTOR_STATUS_SUCCESS
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import event_from_article
from src.extractors.scrapers.scraper_engine import extract_scraper_source
from src.extractors.scrapers.scraper_http_utils import ScraperPageError

SELECTORS = {"article": "article", "title": "h2.entry-title", "link": "h2.entry-title a",
             "text": ".entry-summary p", "start_date": "[itemprop=startDate]"}


def card(title: str, start: str) -> str:
    """Fiche d'agenda inventée, structurée comme une page WordPress (schema.org)."""

    return (f'<article><div class="entry-summary"><p>Texte.</p></div><header>'
            f'<span itemprop="startDate" content="{start}">jour</span>'
            f'<h2 class="entry-title"><a href="/evenement/{title.lower().replace(" ", "-")}/">{title}</a></h2>'
            f'</header></article>')

SOURCE: dict[str, Any] = {
    "name": "Ville Exemple - Agenda", "source_id": "ville_agenda", "type": "scraper", "enabled": True,
    "role": "acquisition", "base_url": "https://ville.example", "start_urls": ["https://ville.example/agenda/"],
    "page_url_template": "https://ville.example/agenda/page/{n}/", "max_pages": 5, "respect_robots_txt": True,
    "request_delay_seconds": 0, "days_forward": 60, "max_articles": 50, "city": "Frontignan",
    "credit": "Ville Exemple", "day_only": True, "selectors": SELECTORS,
    "filters": {"require_title": True, "require_url": True, "min_title_length": 3, "min_text_length": 0,
                "min_total_text_length": 3, "remove_duplicates": True},
}
PAGES = {
    "https://ville.example/agenda/": card("Atelier jeux", "2026-10-20T06:00:00+02:00") + card("Fête passée", "2026-09-01"),
    "https://ville.example/agenda/page/2/": card("Bal folk", "2026-11-15T00:00:00+01:00"),
    "https://ville.example/agenda/page/3/": "<html><body>Aucun résultat</body></html>",
}


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 14, 12, 0, tzinfo=tz or PARIS)


@pytest.fixture
def fake_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    requested: list[str] = []

    def fake_get_html(client, url, respect_robots, delay):
        requested.append(url)
        assert respect_robots is True
        if url not in PAGES:
            raise ScraperPageError("statut_http_invalide", f"404 {url}")
        return PAGES[url], url

    monkeypatch.setattr(module, "get_html", fake_get_html)
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    return requested


def test_pages_until_an_empty_one(fake_site: list[str]) -> None:
    cards = list(module.iter_agenda_cards(SOURCE))
    assert [c["title"] for c in cards] == ["Atelier jeux", "Fête passée", "Bal folk"]
    assert fake_site == ["https://ville.example/agenda/", "https://ville.example/agenda/page/2/",
                         "https://ville.example/agenda/page/3/"]          # s'arrête à la page vide


def test_first_page_failure_is_a_real_failure(fake_site: list[str]) -> None:
    with pytest.raises(ScraperPageError):
        list(module.iter_agenda_cards({**SOURCE, "start_urls": ["https://ville.example/absent/"]}))


def test_through_the_checkit_engine(fake_site: list[str]) -> None:
    result = extract_scraper_source(SOURCE, module.AGENDA_ADAPTER)
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    events = [event_from_article(article) for article in result.articles]
    assert [(e.title, e.start_at, e.time_known, e.source) for e in events] == [
        ("Atelier jeux", "2026-10-20T00:00:00+02:00", False, "scraper"),
        ("Bal folk", "2026-11-15T00:00:00+01:00", False, "scraper"),
    ]
    assert result.rejection_reasons == {"hors_fenetre": 1}


def test_disabled_sources_are_not_loaded() -> None:
    assert module.load_agenda_sources() == []          # Frontignan : en attente d'autorisation


def test_page_urls() -> None:
    assert module.page_urls(SOURCE)[:3] == ["https://ville.example/agenda/", "https://ville.example/agenda/page/2/",
                                            "https://ville.example/agenda/page/3/"]
    assert len(module.page_urls(SOURCE)) == 5
