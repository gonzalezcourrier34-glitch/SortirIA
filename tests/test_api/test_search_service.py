"""Tests du service de recherche de l'interface (sans base ni Mistral)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Self

import pytest

from app import search_service as service
from app.search_service import PARIS, SearchFilters, SearchServices

# Mercredi 14 octobre 2026, 17 h.
NOW = datetime(2026, 10, 14, 17, 0, tzinfo=PARIS)


def row(event_id: str = "e1", **values: Any) -> dict[str, Any]:
    base = {
        "event_id": event_id, "title": "Balade contée", "summary": "Des histoires au bord de l'étang.",
        "description": "", "category_norm": "nature", "category": "", "time_known": True,
        "start_at": datetime(2026, 10, 17, 14, 30, tzinfo=PARIS), "end_at": None,
        "venue_name": "Port", "city": "Bouzigues", "price_text": "", "price_min": None, "is_free": True,
        "audiences": ["famille"], "age_min": None, "age_max": None, "url": "https://agenda.fr/e1",
        "source_name": "Agglopôle", "source_attribution": "Source : Agglopôle", "partner_id": None,
        "is_sponsored": False, "venue_rating": None, "venue_rating_count": None, "partner_name": None,
    }
    return {**base, **values}


class FakeConnection:
    """Renvoie des lignes préparées et garde les requêtes reçues."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    def cursor(self) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.queries.append((sql, params))
        self._result = [{"request_id": 7}] if "rag_requests" in sql else self.rows
        self.description = [(key,) for key in (self._result[0] if self._result else {"x": 0})]

    def fetchall(self) -> list[tuple]:
        return [tuple(item.values()) for item in self._result]

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


# ----------------------------------------------------------------------------
# Contraintes et requête
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(("period", "start", "end"), [
    ("aujourdhui", datetime(2026, 10, 14, 17, tzinfo=PARIS), datetime(2026, 10, 15, tzinfo=PARIS)),
    ("weekend", datetime(2026, 10, 17, tzinfo=PARIS), datetime(2026, 10, 19, tzinfo=PARIS)),
    ("7jours", datetime(2026, 10, 14, 17, tzinfo=PARIS), datetime(2026, 10, 21, 17, tzinfo=PARIS)),
])
def test_period_bounds(period: str, start: datetime, end: datetime) -> None:
    assert service.period_bounds(period, NOW) == (start, end)


def test_weekend_seen_from_saturday_and_sunday() -> None:
    saturday = datetime(2026, 10, 17, 9, tzinfo=PARIS)
    sunday = datetime(2026, 10, 18, 9, tzinfo=PARIS)
    assert service.period_bounds("weekend", saturday) == (saturday, datetime(2026, 10, 19, tzinfo=PARIS))
    assert service.period_bounds("weekend", sunday) == (sunday, datetime(2026, 10, 19, tzinfo=PARIS))


def test_unknown_filters_are_refused() -> None:
    with pytest.raises(ValueError):
        SearchFilters(public="adultes")
    with pytest.raises(ValueError):
        SearchFilters(zone="paris")


def test_query_applies_every_filter() -> None:
    filters = SearchFilters(public="famille", zone="thau", period="weekend", free_only=True)
    sql, params = service.build_search_query(filters, "[0.1,0.2]", NOW)
    assert "%(public)s = ANY (e.audiences)" in sql and params["public"] == "famille"
    assert "event_zone" in sql and params["zone"] == "bassin_de_thau"
    assert "e.is_free" in sql and "embedding <=> %(vector)s::vector" in sql
    sql, params = service.build_search_query(SearchFilters(), None, NOW)
    assert "by_meaning" not in sql and "by_words" not in sql and "public" not in params
    assert "ORDER BY keyword_match DESC, category_match DESC, score DESC, e.start_at" in sql


# ----------------------------------------------------------------------------
# Mise en forme
# ----------------------------------------------------------------------------

def test_card_labels() -> None:
    card = service.to_card(row(), ref=2)
    assert card["date"] == "Sam. 17 oct. · 14:30" and card["place"] == "Port · Bouzigues"
    assert card["price"] == "Gratuit" and card["audiences"] == ["famille"] and card["ref"] == 2
    assert card["partner"] is False and card["venue_rating"] is None
    assert service.to_card(row(time_known=False))["date"] == "Sam. 17 oct."
    assert service.price_label({"is_free": False, "price_text": "", "price_min": 6.5}) == "dès 6,5 €"
    assert service.price_label({"is_free": None}) == "Prix non communiqué"


def test_summary_falls_back_to_description() -> None:
    long_text = "mot " * 200
    summary = service.short_summary({"summary": "", "description": long_text})
    assert summary.endswith("…") and len(summary) <= service.SUMMARY_CHARS + 1


def test_answer_drops_citations_that_do_not_exist() -> None:
    raw = json.dumps({"reponse": "Allez à la balade [1] ou au concert [7] .", "suggestions": ["Et s'il pleut ?"]})
    answer, suggestions = service.parse_answer(raw, count=2)
    assert answer == "Allez à la balade [1] ou au concert." and suggestions == ["Et s'il pleut ?"]
    with pytest.raises(ValueError):
        service.parse_answer('{"reponse": ""}', 2)


# ----------------------------------------------------------------------------
# Recherche complète
# ----------------------------------------------------------------------------

def run(connection: FakeConnection, services: SearchServices) -> list[dict[str, Any]]:
    return list(service.run_search(connection, "  sortie   en famille ", SearchFilters(public="famille"), services))


def test_search_streams_the_four_steps_then_the_result() -> None:
    connection = FakeConnection([row("e1"), row("e2", title="Atelier", partner_id=3, partner_name="Ludo")])
    seen: dict[str, Any] = {}

    def write(question, filters, cards):
        seen["question"], seen["cards"] = question, cards
        return json.dumps({"reponse": "Essayez la balade [1].", "suggestions": ["Le matin ?"]}), \
            {"input": 100, "output": 20}, "mistral-small-latest"

    events = run(connection, SearchServices(embed=lambda q: "[0.1]", write=write, now=lambda: NOW))
    steps = [(e["step"], e["status"]) for e in events if e["event"] == "progress"]
    assert steps == [(step, status) for step in ("comprendre", "rechercher", "filtrer", "rediger")
                     for status in ("running", "completed")]
    result = events[-1]["result"]
    assert result["answer"] == "Essayez la balade [1]." and result["suggestions"] == ["Le matin ?"]
    assert [card["ref"] for card in result["events"]] == [1, 2] and result["events"][1]["partner"] is True
    assert result["request_id"] == 7 and result["degraded"] is False
    assert seen["question"] == "sortie en famille"
    search = next(params for sql, params in connection.queries if "vector" in params)
    assert search["vector"] == "[0.1]"
    assert any("query_cache" in sql for sql, _ in connection.queries)      # cherchée puis gardée
    logged = connection.queries[-1][1]
    assert logged["event_ids"] == ["e1", "e2"] and logged["prompt_tokens"] == 100


def test_search_survives_mistral_outage() -> None:
    connection = FakeConnection([row("e1")])

    def broken(*args):
        raise RuntimeError("503")

    events = run(connection, SearchServices(embed=broken, write=broken, now=lambda: NOW))
    result = events[-1]["result"]
    assert result["degraded"] is True and "Balade contée [1]" in result["answer"]
    assert result["suggestions"] and "vector" not in connection.queries[0][1]


def test_search_without_result() -> None:
    events = run(FakeConnection([]), SearchServices(embed=None, write=None, now=lambda: NOW))
    result = events[-1]["result"]
    assert result["events"] == [] and "aucun événement" in result["answer"]


def test_sse_format() -> None:
    text = service.sse({"event": "progress", "step": "comprendre", "status": "running"})
    assert text.startswith("event: progress\ndata: {") and text.endswith("\n\n")


# ----------------------------------------------------------------------------
# Type de sortie nommé dans la question
# ----------------------------------------------------------------------------

def test_wanted_categories_understands_theatre_without_accent():
    from app.search_service import wanted_categories

    assert wanted_categories("j'aimerais aller au theatre") == ("theatre", "spectacle")
    assert wanted_categories("Des pièces de Théâtre ?") == ("theatre", "spectacle")
    assert wanted_categories("des concerts ce soir") == ("concert",)
    assert wanted_categories("que faire samedi ?") == ()


def test_search_query_puts_wanted_categories_first():
    from datetime import datetime

    from app.search_service import PARIS, SearchFilters, build_search_query

    sql, params = build_search_query(SearchFilters(), "[0.1]", datetime(2026, 10, 15, tzinfo=PARIS),
                                     categories=("spectacle", "theatre"))
    assert sql.rstrip().split("\n")[-2] == "ORDER BY keyword_match DESC, category_match DESC, score DESC, e.start_at"
    assert "ANY (%(categories)s)) AS category_match" in sql
    assert params["categories"] == ["spectacle", "theatre"]


def test_writer_question_flags_a_missing_category():
    from app.search_service import writer_question

    assert "Aucun événement" in writer_question("du théâtre", ("theatre",), 0)
    assert writer_question("du théâtre", ("theatre",), 2) == "du théâtre"
    assert writer_question("que faire ?", (), 0) == "que faire ?"


def test_event_date_label_shows_ongoing_events():
    from datetime import datetime

    from app.search_service import PARIS, event_date_label

    now = datetime(2026, 10, 15, 11, 0, tzinfo=PARIS)
    ongoing = {"start_at": datetime(2026, 9, 3, 10, 0, tzinfo=PARIS),
               "end_at": datetime(2026, 12, 19, 23, 59, tzinfo=PARIS), "time_known": True}
    assert event_date_label(ongoing, now) == "En cours · jusqu'au 19 déc."
    coming = {"start_at": datetime(2026, 10, 17, 10, 0, tzinfo=PARIS),
              "end_at": datetime(2026, 10, 17, 12, 0, tzinfo=PARIS), "time_known": True}
    assert event_date_label(coming, now) == "Sam. 17 oct. · 10:00"


def test_group_occurrences_keeps_one_card_per_event():
    from datetime import datetime

    from app.search_service import PARIS, group_occurrences, other_dates

    now = datetime(2026, 10, 15, 11, 0, tzinfo=PARIS)

    def row(event_id, title, day, venue="Médiathèque La Fabrique", city="Marseillan"):
        return {"event_id": event_id, "title": title, "venue_name": venue, "city": city,
                "start_at": datetime(2026, 10, day, 10, 0, tzinfo=PARIS), "end_at": None, "time_known": True}

    rows = [row("b", "PAUSE JEUX", 24), row("x", "Concert", 18), row("a", "Pause  jeux ", 17),
            row("c", "PAUSE JEUX", 31), row("z", "PAUSE JEUX", 20, venue="Autre lieu")]
    groups = group_occurrences(rows, now)

    # Ordre : celui de la meilleure séance ; séance montrée : la plus proche.
    assert [main["event_id"] for main, _ in groups] == ["a", "x", "z"]
    assert [r["event_id"] for r in groups[0][1]] == ["b", "c"]
    assert other_dates(groups[0][1]) == [{"event_id": "b", "date": "Sam. 24 oct. · 10:00"},
                                         {"event_id": "c", "date": "Sam. 31 oct. · 10:00"}]
    assert len(group_occurrences(rows, now, limit=1)) == 1


def test_keep_exact_matches_prefers_precise_words_then_type():
    from app.search_service import distinctive_keywords, keep_exact_matches

    rows = [{"id": 1, "keyword_match": False, "category_match": True},
            {"id": 2, "keyword_match": True, "category_match": False},
            {"id": 3, "keyword_match": True, "category_match": True}]
    kept, words, kind = keep_exact_matches(rows)
    assert [r["id"] for r in kept] == [3] and words and kind
    kept, words, kind = keep_exact_matches([{"id": 1}, {"id": 2}])     # rien d'exact : tout est gardé
    assert len(kept) == 2 and not words and not kind
    assert distinctive_keywords(["jeux", "echecs", "concerts", "fabre"]) == ["echecs", "fabre"]


def test_multi_day_events_show_their_span():
    from datetime import datetime

    from app.search_service import PARIS, event_date_label

    now = datetime(2026, 10, 15, 11, 0, tzinfo=PARIS)
    festival = {"start_at": datetime(2026, 10, 30, 10, 0, tzinfo=PARIS),
                "end_at": datetime(2026, 11, 1, 18, 0, tzinfo=PARIS), "time_known": True}
    assert event_date_label(festival, now) == "Du 30 oct. au 1 nov."
