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
    assert "ORDER BY visible_hits DESC, keyword_hits DESC, category_match DESC, score DESC, e.start_at" in sql


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
    # L'offre partenaire répond à la demande : elle passe en tête (15/10/2026).
    assert [card["ref"] for card in result["events"]] == [1, 2] and result["events"][0]["partner"] is True
    assert "offre partenaire" in service.events_for_prompt(seen["cards"]).split("\n")[0]
    assert result["request_id"] == 7 and result["degraded"] is False
    assert seen["question"] == "sortie en famille"
    search = next(params for sql, params in connection.queries if "vector" in params)
    assert search["vector"] == "[0.1]"
    assert any("query_cache" in sql for sql, _ in connection.queries)      # cherchée puis gardée
    logged = connection.queries[-1][1]
    assert logged["event_ids"] == ["e2", "e1"] and logged["prompt_tokens"] == 100


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
    assert sql.rstrip().split("\n")[-2] == "ORDER BY visible_hits DESC, keyword_hits DESC, category_match DESC, score DESC, e.start_at"
    assert "= ANY (%(categories)s) OR to_tsvector" in sql and "AS category_match" in sql
    assert params["type_words"] == "spectacle | theatre"
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


# ----------------------------------------------------------------------------
# Offres partenaires en tête des cartes (15/10/2026)
# ----------------------------------------------------------------------------

def plan_for(question: str) -> service.QueryPlan:
    return service.plan_search(question, SearchFilters(), NOW)[0]


def test_matching_partner_offers_come_first_in_relevance_order() -> None:
    rows = [row("e1", score=0.03), row("p1", partner_id=1, score=0.02), row("e2", score=0.01),
            row("p2", partner_id=2, score=0.005)]
    ordered = service.partners_first(rows, plan_for("un concert ce week-end"), exact=True)
    assert [r["event_id"] for r in ordered] == ["p1", "p2", "e1", "e2"]


def test_partner_offer_far_from_a_vague_question_stays_in_its_place() -> None:
    rows = [row(f"e{i}", score=1 / (60 + i)) for i in range(service.PARTNER_TOP_RANK)]
    rows.append(row("p1", partner_id=1, score=0.001))
    ordered = service.partners_first(rows, plan_for("quelque chose de sympa"), exact=True)
    assert ordered[-1]["event_id"] == "p1"
    near = service.partners_first([row("e1", score=0.03), row("p1", partner_id=1, score=0.02)],
                                  plan_for("quelque chose de sympa"), exact=True)
    assert near[0]["event_id"] == "p1"


def test_partner_offers_leave_the_cards_when_nothing_answers_exactly() -> None:
    rows = [row("e1"), row("p1", partner_id=1)]
    ordered = service.partners_first(rows, plan_for("du théâtre"), exact=False)
    assert [r["event_id"] for r in ordered] == ["e1"]


def test_cards_open_with_the_partner_offer_that_answers() -> None:
    connection = FakeConnection([row("e1", title="Concert de jazz", category_norm="concert", category_match=True),
                                 row("p1", title="Soirée jazz au bar", category_norm="concert", category_match=True,
                                     partner_id=4, partner_name="Le Bar", venue_name="Bar", city="Sète")])
    events = list(service.run_search(connection, "un concert", SearchFilters(),
                                     SearchServices(embed=None, write=None, now=lambda: NOW)))
    cards = events[-1]["result"]["events"]
    assert [card["event_id"] for card in cards] == ["p1", "e1"] and cards[0]["partner"] is True


def test_partner_offer_must_aim_at_the_precise_type_and_the_public() -> None:
    """Grille de pertinence du 16/10/2026 : les 3 cartes non pertinentes étaient
    des offres partenaires mises en tête à tort (marionnettes pour « théâtre »
    et pour « danse », escalade pour enfants pour des ados)."""

    puppets = row("p1", title="Spectacle de marionnettes", category_norm="spectacle", category_match=True,
                  partner_id=1, score=0.03)
    play = row("e1", title="Une comédie", category_norm="theatre", category_match=True, score=0.02)
    ordered = service.partners_first([play, puppets], plan_for("j'aimerais aller au théâtre"), exact=True,
                                     question="j'aimerais aller au théâtre")
    assert [r["event_id"] for r in ordered] == ["e1", "p1"] and not ordered[1].get("partner_ahead")
    ordered = service.partners_first([puppets], plan_for("un spectacle de danse"), exact=False,
                                     question="un spectacle de danse")
    assert ordered == []                                   # idées voisines : l'offre sort des cartes
    staged = row("p2", title="Pièce de théâtre au jardin", category_norm="spectacle", category_match=True,
                 partner_id=2, score=0.01)
    ordered = service.partners_first([play, staged], plan_for("j'aimerais aller au théâtre"), exact=True,
                                     question="j'aimerais aller au théâtre")
    assert ordered[0]["event_id"] == "p2" and ordered[0]["partner_ahead"] is True
    climbing = row("p3", title="Initiation escalade enfants", category_norm="atelier", partner_id=3,
                   audiences=["ados", "famille"], score=0.02)
    ados = plan_for("un truc à faire avec des ados")
    ordered = service.partners_first([row("e2", score=0.03), climbing], ados, exact=True,
                                      question="un truc à faire avec des ados")
    assert [r["event_id"] for r in ordered] == ["e2", "p3"]


def test_main_type_is_the_precise_one() -> None:
    from app.query_understanding import main_categories

    assert main_categories("j'aimerais aller au théâtre") == ("theatre",)
    assert main_categories("un spectacle de danse") == ("danse",)
    assert main_categories("un spectacle") == ("spectacle",)
    assert main_categories("une sortie demain") == ()


def test_partner_kept_in_place_is_not_moved_first_by_card_order() -> None:
    cards = [{"ref": 1, "event_id": "e1", "partner": False}, {"ref": 2, "event_id": "p1", "partner": True,
                                                              "partner_ahead": False}]
    ordered, _ = service.order_cards(cards, "Voici [1].")
    assert [card["event_id"] for card in ordered] == ["e1", "p1"]
    assert "offre partenaire" not in service.events_for_prompt([{**cards[1], "ref": 2, "title": "T", "date": "",
                                                                 "place": "", "category": "", "price": "",
                                                                 "audiences": [], "summary": ""}])


def test_cards_order_partners_then_cited_then_the_rest() -> None:
    cards = [{"ref": i, "event_id": f"e{i}", "partner": i == 6} for i in range(1, 9)]
    ordered, answer = service.order_cards(cards, "Idées : [5] FABRIQUE, [7] LUDI ou [8] BRIQUES, et [5].")
    assert [card["event_id"] for card in ordered] == ["e6", "e5", "e7", "e8", "e1", "e2", "e3", "e4"]
    assert [card["ref"] for card in ordered] == list(range(1, 9))
    assert answer == "Idées : [2] FABRIQUE, [3] LUDI ou [4] BRIQUES, et [2]."
    assert cards[0]["ref"] == 1                      # les cartes reçues ne sont pas modifiées


# ----------------------------------------------------------------------------
# Sujet introuvable : recherche élargie au sujet (15/10/2026)
# ----------------------------------------------------------------------------

class SubjectConnection(FakeConnection):
    """Rien sur le sujet avec « gratuit » coché ; le sujet existe sans cette case."""

    def __init__(self, strict: list[dict[str, Any]], wider: list[dict[str, Any]]) -> None:
        super().__init__([])
        self.strict, self.wider = strict, wider

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.queries.append((sql, params))
        if "rag_requests" in sql:
            self._result = [{"request_id": 7}]
        elif "WITH base" in sql:
            self._result = self.strict if "AND e.is_free" in sql else self.wider
        else:
            self._result = []
        self.description = [(key,) for key in (self._result[0] if self._result else {"x": 0})]


def search(connection: FakeConnection, question: str, write=None) -> dict[str, Any]:
    filters = SearchFilters(public="famille", period="7jours", free_only=True)
    events = list(service.run_search(connection, question, filters,
                                     SearchServices(embed=None, write=write, now=lambda: NOW)))
    return events[-1]["result"]


def test_precise_subject_words_are_searched_in_the_text() -> None:
    assert service.distinctive_keywords(["cirque", "spectacle", "jazz", "concert", "photo"]) == [
        "cirque", "jazz", "photo"]


def test_missing_subject_is_searched_without_the_constraints_instead_of_unrelated_ideas() -> None:
    unrelated = [row("a1", title="Histoires à jouer", category_norm="atelier"),
                 row("a2", title="Pause jeux", category_norm="jeux")]
    circus = [row("c1", title="Stage de cirque vacances", category_norm="atelier", keyword_match=True,
                  is_free=False, price_text="60 €", partner_id=2, partner_name="Ludothèque"),
              row("a1", title="Histoires à jouer", category_norm="atelier", keyword_match=False,
                  is_free=True, price_text="", partner_id=None, partner_name=None)]
    seen: dict[str, Any] = {}

    def write(question, plan, cards):
        seen["question"] = question
        return json.dumps({"reponse": "Pas gratuit, mais il y a [1].", "suggestions": []}), {}, "m"

    result = search(SubjectConnection(unrelated, circus), "cirque", write)
    assert [card["event_id"] for card in result["events"]] == ["c1"]
    assert result["events"][0]["partner"] is True and result["exact"] is False
    assert "gratuit" in seen["question"] and "pour le public « famille »" in seen["question"]
    assert result["partner_offers"] == []


def test_clear_subject_found_nowhere_gives_no_unrelated_card() -> None:
    unrelated = [row("a1", title="Histoires à jouer", category_norm="atelier")]
    result = search(SubjectConnection(unrelated, unrelated), "cirque")
    assert result["events"] == [] and "« cirque »" in result["answer"] and result["partner_offers"] == []


def test_mood_word_found_nowhere_keeps_the_ideas_closest_in_meaning() -> None:
    # « romantique » n'est écrit nulle part : le sens reste le meilleur guide.
    nearby = [row("a1", title="Dîner-concert au bord de l'étang", category_norm="concert")]
    seen: dict[str, Any] = {}

    def write(question, plan, cards):
        seen["question"] = question
        return json.dumps({"reponse": "Idée voisine : [1].", "suggestions": []}), {}, "m"

    result = search(SubjectConnection(nearby, nearby), "un endroit romantique", write)
    assert [card["event_id"] for card in result["events"]] == ["a1"] and result["exact"] is False
    assert "romantique" in seen["question"]


def test_missing_genre_is_searched_before_settling_for_the_type() -> None:
    # Des concerts gratuits cette semaine, mais le jazz est payant : on montre le jazz.
    concerts = [row("c1", title="Concert de chorale", category_norm="concert", category_match=True,
                    keyword_match=False)]
    jazz = [row("j1", title="Soirée jazz", category_norm="concert", category_match=True, keyword_match=True,
                is_free=False)]
    result = search(SubjectConnection(concerts, jazz), "un concert de jazz")
    assert [card["event_id"] for card in result["events"]] == ["j1"] and result["exact"] is False


def test_subject_better_shown_without_the_constraints_goes_first() -> None:
    """Capture du 16/10/2026 : « Karaoké géant… » avec « Famille » coché. Une
    fête qui cite seulement « groupe » passait, le Karaoké géant du partenaire
    (en soirée) restait dehors. Sans les contraintes, il montre tous les mots :
    il passe en tête, et la réponse dit ce qui a été relâché."""

    party = [row("f1", title="Martror", summary="Fête avec musique en groupe.", category_norm="fete",
                 keyword_match=True, keyword_hits=1, visible_hits=1)]
    karaoke = [row("k1", title="Karaoké géant (exemple)", category_norm="jeux", audiences=["soiree"],
                   summary="Soirée karaoké géant avec des milliers de titres pour chanter en groupe.",
                   keyword_match=True, keyword_hits=5, visible_hits=5, partner_id=1, partner_name="Bar Exemple"),
               *party]
    seen: dict[str, Any] = {}

    def write(question, plan, cards):
        seen["question"] = question
        return json.dumps({"reponse": "Pas pour les familles, mais [1].", "suggestions": []}), {}, "m"

    result = search(SubjectConnection(party, karaoke),
                    "karaoké géant en groupe avec des milliers de titres", write)
    assert [card["event_id"] for card in result["events"]] == ["k1"]
    assert result["events"][0]["partner"] is True and result["exact"] is False
    assert "pour le public « famille »" in seen["question"]


def test_constraints_are_kept_when_widening_shows_the_subject_no_better() -> None:
    same = [row("f1", title="Martror", summary="Fête avec musique en groupe.", category_norm="fete",
                keyword_match=True, keyword_hits=1, visible_hits=1)]
    result = search(SubjectConnection(same, same), "karaoké géant en groupe")
    assert [card["event_id"] for card in result["events"]] == ["f1"]


def test_words_are_searched_by_prefix() -> None:
    assert service.lexical_query(["photo", "cine", "art"]) == "photo:* | cine:* | art"


def test_partner_circus_course_answers_circus_even_if_filed_as_a_course() -> None:
    """Capture du 15/10/2026 : le stage de cirque du partenaire était relégué dans
    l'encadré « offres partenaires » parce qu'il est rangé en « stage », pas en
    « spectacle ». Le mot « cirque » trouvé suffit : il passe en tête des cartes."""

    rows = [row("d1", title="Stage de cirque enfants", category_norm="atelier", keyword_match=True,
                category_match=False, partner_id=None, partner_name=None),
            row("p1", title="Stage de cirque vacances", category_norm="atelier", keyword_match=True,
                category_match=False, partner_id=3, partner_name="Ludothèque")]
    result = search(SubjectConnection(rows, rows), "cirque")
    assert [card["event_id"] for card in result["events"]] == ["p1", "d1"]
    assert result["exact"] is True and result["partner_offers"] == []


def test_on_topic_partner_offer_stays_among_neighbour_ideas() -> None:
    rows = [row("e1", keyword_match=False, category_match=False),
            row("p1", partner_id=1, keyword_match=False, category_match=True),
            row("p2", partner_id=2, keyword_match=False, category_match=False)]
    ordered = service.partners_first(rows, plan_for("du théâtre"), exact=False)
    assert [r["event_id"] for r in ordered] == ["p1", "e1"]


def test_type_written_in_the_title_counts_as_the_type() -> None:
    """« jeux » : « Atelier jeux en famille », rangé en atelier, répond aussi (15/10/2026)."""

    sql, params = service.build_search_query(plan_for("des jeux"), None)
    assert params["type_words"] == "jeux" and "to_tsquery('french', %(type_words)s)" in sql
    assert "NULLIF(e.category_norm, '')" in sql
    sql, params = service.build_search_query(plan_for("une sortie demain"), None)
    assert "type_words" not in params
