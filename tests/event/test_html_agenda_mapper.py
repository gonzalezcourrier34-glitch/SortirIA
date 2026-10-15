"""Tests de la lecture d'une page d'agenda HTML (structure type WordPress + schema.org)."""

from __future__ import annotations

from datetime import date

from src.event import html_agenda_mapper as mapper

SELECTORS = {"article": "article", "title": "h2.entry-title", "link": "h2.entry-title a",
             "text": ".entry-summary p", "start_date": "[itemprop=startDate]",
             "end_date": "[itemprop=endDate]", "category": ".entry-meta a"}
SOURCE = {"source_id": "ville", "name": "Ville Exemple", "city": "frontignan", "credit": "Ville Exemple"}
TODAY, HORIZON = date(2026, 10, 14), date(2026, 12, 13)


def card(title: str, start: str, end: str = "", summary: str = "Texte.", theme: str = "Atelier") -> str:
    end_html = f' au <span itemprop="endDate" content="{end}">fin</span>' if end else ""
    return f"""<article class="l3"><div class="vignette"><img src="photo.jpg">
      <div class="entry-summary"><p>{summary} <a class="more-link" href="/evenement/x/">Lire la suite</a></p></div></div>
      <header class="entry-header"><div class="dates">Du <span itemprop="startDate" content="{start}">jour</span>{end_html}</div>
      <h2 class="entry-title"><a href="/evenement/{title.lower().replace(' ', '-')}/">{title}</a></h2>
      <div class="entry-meta"><a href="/agenda/theme/x/">{theme}</a></div></header></article>"""


PAGE = "<html><body>" + "".join([
    card("🎗Octobre rose : marche", "2026-10-03T06:00:00+02:00", "2026-10-31T06:00:00+01:00", theme="Octobre Rose"),
    card("Ateliers bien-être", "2026-10-07T00:00:00+02:00", "2026-11-14T00:00:00+01:00",
         summary="Ateliers de yoga 🗓️ Du 7 octobre 📍 O’studio, 37, route de …"),
    card("Concert du soir", "2026-11-20T21:00:00+01:00"),
    card("Fête passée", "2026-09-01T00:00:00+02:00"),
]) + "</body></html>"


def test_cards_are_read_from_the_list_page() -> None:
    cards = mapper.parse_agenda_page(PAGE, SELECTORS, "https://ville.example/agenda/")
    assert len(cards) == 4
    assert cards[1]["url"] == "https://ville.example/evenement/ateliers-bien-être/"
    assert cards[1]["summary"].endswith("O’studio, 37, route de")   # « Lire la suite » retiré
    assert cards[0]["category"] == "Octobre Rose"


def test_mapping_and_cleaning() -> None:
    cards = mapper.parse_agenda_page(PAGE, SELECTORS, "https://ville.example/agenda/")
    rose, yoga, concert, past = (mapper.map_agenda_card(c, SOURCE, today=TODAY, horizon=HORIZON) for c in cards)
    assert rose[0].title == "Octobre rose : marche"                  # symbole de tête retiré
    assert yoga[0].venue_name == "O’studio" and yoga[0].city == "Frontignan"
    assert (yoga[0].start_at, yoga[0].end_at, yoga[0].time_known) == (
        "2026-10-07T00:00:00+02:00", "2026-11-14T00:00:00+01:00", False)
    assert concert[0].time_known is True and "soiree" in concert[0].audiences
    assert past == (None, "")                                        # hors fenêtre : ignoré
    assert rose[0].source == "scraper" and rose[0].source_credit == "Ville Exemple"


def test_day_only_option_ignores_default_hours() -> None:
    cards = mapper.parse_agenda_page(PAGE, SELECTORS, "https://ville.example/agenda/")
    with_hours, _ = mapper.map_agenda_card(cards[0], SOURCE, today=TODAY, horizon=HORIZON)
    day_only, _ = mapper.map_agenda_card(cards[0], {**SOURCE, "day_only": True}, today=TODAY, horizon=HORIZON)
    assert with_hours.time_known is True                             # « 06:00 » pris au mot
    assert (day_only.time_known, day_only.start_at) == (False, "2026-10-03T00:00:00+02:00")


def test_rejections() -> None:
    base = {"title": "Bal", "url": "https://ville.example/bal", "start": "2026-11-01"}
    assert mapper.map_agenda_card({**base, "title": ""}, SOURCE, today=TODAY, horizon=HORIZON) == (None, mapper.REASON_NO_TITLE)
    assert mapper.map_agenda_card({**base, "url": ""}, SOURCE, today=TODAY, horizon=HORIZON) == (None, mapper.REASON_NO_LINK)
    assert mapper.map_agenda_card({**base, "start": "bientôt"}, SOURCE, today=TODAY, horizon=HORIZON) == (None, mapper.REASON_NO_DATE)
