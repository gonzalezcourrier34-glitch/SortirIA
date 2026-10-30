"""Tests du service de recherche de l'interface (sans base ni Mistral)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Self

import pytest

from app import search_answer, search_cards, search_filters, search_query, search_service
from app.query_understanding import QueryPlan, merge_with_filters, understand
from app.search_answer import fallback_answer
from app.search_filters import PARIS, SearchFilters
from app.search_service import SearchServices

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
    assert search_filters.period_bounds(period, NOW) == (start, end)


def test_weekend_seen_from_saturday_and_sunday() -> None:
    saturday = datetime(2026, 10, 17, 9, tzinfo=PARIS)
    sunday = datetime(2026, 10, 18, 9, tzinfo=PARIS)
    assert search_filters.period_bounds("weekend", saturday) == (saturday, datetime(2026, 10, 19, tzinfo=PARIS))
    assert search_filters.period_bounds("weekend", sunday) == (sunday, datetime(2026, 10, 19, tzinfo=PARIS))


def test_unknown_filters_are_refused() -> None:
    with pytest.raises(ValueError):
        SearchFilters(public="adultes")
    with pytest.raises(ValueError):
        SearchFilters(zone="paris")


def test_query_applies_every_filter() -> None:
    filters = SearchFilters(public="famille", zone="thau", period="weekend", free_only=True)
    sql, params = search_query.build_search_query(filters, "[0.1,0.2]", NOW)
    assert "%(public)s = ANY (e.audiences)" in sql and params["public"] == "famille"
    assert "event_zone" in sql and params["zone"] == "bassin_de_thau"
    assert "e.is_free" in sql and "embedding <=> %(vector)s::vector" in sql
    sql, params = search_query.build_search_query(SearchFilters(), None, NOW)
    assert "by_meaning" not in sql and "by_words" not in sql and "public" not in params
    assert "ORDER BY visible_hits DESC, keyword_hits DESC, category_match DESC, score DESC, e.start_at" in sql


# ----------------------------------------------------------------------------
# Mise en forme
# ----------------------------------------------------------------------------

def test_card_labels() -> None:
    card = search_cards.to_card(row(), ref=2)
    assert card["date"] == "Sam. 17 oct. · 14:30" and card["place"] == "Port · Bouzigues"
    assert card["price"] == "Gratuit" and card["audiences"] == ["famille"] and card["ref"] == 2
    assert card["partner"] is False and card["venue_rating"] is None
    assert search_cards.to_card(row(time_known=False))["date"] == "Sam. 17 oct."
    assert search_cards.price_label({"is_free": False, "price_text": "", "price_min": 6.5}) == "dès 6,50 €"
    assert search_cards.price_label({"is_free": False, "price_text": "", "price_min": 12.0}) == "dès 12 €"
    assert search_cards.price_label({"is_free": None}) == "Prix non communiqué"


def test_prices_and_places_read_the_french_way() -> None:
    """16/10/2026 : « À partir de 25.5 € » et « Montpellier · Montpellier » sur le site."""

    assert search_cards.french_prices("À partir de 25.5 €") == "À partir de 25,50 €"
    assert search_cards.french_prices("De 12.33 € à 20.55 €") == "De 12,33 € à 20,55 €"
    assert search_cards.french_prices("10 euros, 2 adultes, 1.5 h") == "10 euros, 2 adultes, 1.5 h"
    assert search_cards.french_prices("Tarif 7,5 €") == "Tarif 7,50 €"
    assert search_cards.french_prices("De 5.5 à 8.5 €") == "De 5,50 à 8,50 €"
    assert search_cards.french_prices("Durée 1.5 h") == "Durée 1.5 h"          # pas un prix
    assert search_cards.french_amount(1_000_000.0) == "1000000"
    assert search_cards.place_label({"venue_name": "Montpellier", "city": "Montpellier"}) == "Montpellier"
    assert search_cards.place_label({"venue_name": "Jam", "city": "Montpellier"}) == "Jam · Montpellier"
    assert search_cards.place_label({"venue_name": "", "city": "Sète"}) == "Sète"


def test_suggestions_must_be_short_questions() -> None:
    """16/10/2026 : Mistral mettait des phrases sur les cartes dans les relances."""

    raw = json.dumps({"reponse": "Voici des idées proches: [1].", "suggestions": [
        "Ce concert familial a lieu ce soir à 20h30 à Montpellier, mais il est payant [2].",
        "Et s'il pleut ?", "Plutôt en soirée?", "Veux-tu un concert en famille ?"]})
    answer, suggestions = search_answer.parse_answer(raw, count=2)
    assert answer == "Voici des idées proches\u00a0: [1]."
    assert suggestions == ["Et s'il pleut\u00a0?", "Plutôt en soirée\u00a0?", "Veux-tu un concert en famille\u00a0?"]
    none = json.dumps({"reponse": "Ok.", "suggestions": ["Une phrase sans question.", "Voir la carte [1] ?"]})
    assert search_answer.parse_answer(none, 1)[1] == []          # aucune : relances par défaut
    assert search_answer.french_spacing("Rendez-vous à 20:30 : entrée libre !") == "Rendez-vous à 20:30\u00a0: entrée libre\u00a0!"
    # Après un nombre aussi (« le 31: »), une fois les espaces retirées par parse_answer.
    raw = json.dumps({"reponse": "Rendez-vous le 31 : concert [1]. Libre le 25 ? Oui à 20:30 !",
                      "suggestions": ["Plutôt en soirée ?", "Plutôt en soirée?"]})
    answer, suggestions = search_answer.parse_answer(raw, 1)
    assert answer == "Rendez-vous le 31\u00a0: concert [1]. Libre le 25\u00a0? Oui à 20:30\u00a0!"
    assert suggestions == ["Plutôt en soirée\u00a0?"]                  # doublon retiré


def test_prompt_example_adds_no_constraint() -> None:
    """30/10/2026 : l'exemple « théâtre gratuit en famille » était recopié tel quel."""

    assert "gratuit en famille" not in search_answer.WRITE_PROMPT
    assert "n'ajoute jamais une contrainte" in search_answer.WRITE_PROMPT
    assert "nomme chaque événement" in search_answer.WRITE_PROMPT
    # 30/10/2026, version .2 : un titre d'exemple ou un nom de ligne est recopié tel quel.
    assert "Fête de la soupe" not in search_answer.WRITE_PROMPT
    assert "Contraintes comprises" not in search_answer.WRITE_PROMPT
    raw = json.dumps({"reponse": "Voici une idée : la soirée jeux [1]. Contraintes comprises.",
                      "suggestions": []})
    assert search_answer.parse_answer(raw, 1)[0] == "Voici une idée\u00a0: la soirée jeux [1]."


def test_bare_citations_receive_their_title() -> None:
    """30/10/2026 : « Voici quelques concerts à Frontignan : [1], [2], [3] et [4]. »"""

    cards = [{"ref": n, "title": f"Concert {n}"} for n in range(1, 5)]
    answer = search_answer.name_citations("Voici quelques concerts à Frontignan\u00a0: [1], [2], [3] et [4].", cards)
    assert answer == ("Voici quelques concerts à Frontignan\u00a0: Concert 1 [1], Concert 2 [2], "
                      "Concert 3 [3] et Concert 4 [4].")


def test_named_citations_are_left_alone() -> None:
    cards = [{"ref": 1, "title": "Fête de la soupe"}, {"ref": 2, "title": "Bal folk"}]
    text = "Allez à la Fête de la soupe [1] ; sinon [2] propose un bal folk."
    assert search_answer.name_citations(text, cards) == text


def test_answer_announcing_ideas_gives_them() -> None:
    """30/10/2026 : 11 réponses sur 80 finissaient par « Voici des idées voisines : »."""

    cards = [{"ref": n, "title": t} for n, t in enumerate(["Bal folk", "Loto", "Expo photo", "Marché"], 1)]
    answer = search_answer.name_citations("Je n'ai pas trouvé de brocante. Voici des idées voisines\u00a0:", cards)
    assert answer == "Je n'ai pas trouvé de brocante. Idées voisines\u00a0: Bal folk [1], Loto [2], Expo photo [3]."
    answer = search_answer.name_citations("Je n'ai pas trouvé de musée gratuit dimanche.", cards[:1])
    assert answer == "Je n'ai pas trouvé de musée gratuit dimanche. Idées voisines\u00a0: Bal folk [1]."
    # Sans citation, les noms donnés ne viennent pas de la liste : seul le constat reste.
    answer = search_answer.name_citations(
        "Je n'ai pas trouvé d'atelier de poterie. Voici des idées voisines\u00a0: la Fête de la soupe "
        "et le Marché de Noël de Sète.", [{"ref": 1, "title": "Atelier terre"}])
    assert answer == "Je n'ai pas trouvé d'atelier de poterie. Idées voisines\u00a0: Atelier terre [1]."
    # Lieu et date ajoutés : c'est souvent ce qui diffère de la demande.
    answer = search_answer.name_citations(
        "Je n'ai pas trouvé d'atelier de poterie près de Thau.",
        [{"ref": 1, "title": "Atelier de poterie", "city": "Saint-Paul-et-Valmalle", "date": "Sam. 14 nov."}])
    assert answer == ("Je n'ai pas trouvé d'atelier de poterie près de Thau. Idées voisines\u00a0: "
                      "Atelier de poterie [1] (Saint-Paul-et-Valmalle, sam. 14 nov.).")
    long_title = "SAISON DE CONCERTS D'ORGUE 2026-2027 À LA CATHÉDRALE SAINT-PIERRE DE MONTPELLIER"
    answer = search_answer.name_citations("Voici une idée : [1].", [{"ref": 1, "title": long_title}])
    assert answer.endswith("… [1].") and len(answer) < len(long_title) + 20


@pytest.mark.parametrize(("title", "city", "expected"), [
    ("LES PAS PERDUS — THÉÂTRE AMATEUR PAR LA TROUPE DE L'UTL34", "Frontignan",
     "Les pas perdus — Théâtre amateur par la troupe de l'UTL34"),
    ("EXPOSITION GUIMET+ MONTPELLIER / CHINE", "Montpellier", "Exposition guimet+ Montpellier / Chine"),
    ("MÉDITERRANÉE BURLESQUE FESTIVAL", "Sète", "Méditerranée burlesque festival"),
    ("SOIRÉE DJ AU PORT DE BOUZIGUES", "Bouzigues", "Soirée DJ au port de Bouzigues"),
    ("MARCHÉ DE NOËL DE PIGNAN", "Pignan", "Marché de Noël de Pignan"),
    ("L'HEURE DU CONTE : NOËL À SÈTE", "Sète", "L'heure du conte : Noël à Sète"),
    ("Fête de la soupe", "Frontignan", "Fête de la soupe"),           # casse mixte : inchangé
    ("Concert  NES - BASED MTP", "", "Concert NES - BASED MTP"),      # pas assez de capitales
])
def test_titles_in_capitals_become_readable(title: str, city: str, expected: str) -> None:
    """30/10/2026 : « LES PAS PERDUS — THÉÂTRE AMATEUR… » dans les cartes et les réponses."""

    assert search_cards.readable_title(title, city) == expected


def test_summary_falls_back_to_description() -> None:
    long_text = "mot " * 200
    summary = search_cards.short_summary({"summary": "", "description": long_text})
    assert summary.endswith("…") and len(summary) <= search_cards.SUMMARY_CHARS + 1


def test_answer_drops_citations_that_do_not_exist() -> None:
    raw = json.dumps({"reponse": "Allez à la balade [1] ou au concert [7] .", "suggestions": ["Et s'il pleut ?"]})
    answer, suggestions = search_answer.parse_answer(raw, count=2)
    assert answer == "Allez à la balade [1] ou au concert." and suggestions == ["Et s'il pleut\u00a0?"]
    with pytest.raises(ValueError):
        search_answer.parse_answer('{"reponse": ""}', 2)


# ----------------------------------------------------------------------------
# Recherche complète
# ----------------------------------------------------------------------------

def run(connection: FakeConnection, services: SearchServices) -> list[dict[str, Any]]:
    return list(search_service.run_search(connection, "  sortie   en famille ", SearchFilters(public="famille"), services))


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
    assert result["answer"] == "Essayez la balade [1]." and result["suggestions"] == ["Le matin\u00a0?"]
    # L'offre partenaire répond à la demande : elle passe en tête (15/10/2026).
    assert [card["ref"] for card in result["events"]] == [1, 2] and result["events"][0]["partner"] is True
    assert "offre partenaire" in search_answer.events_for_prompt(seen["cards"]).split("\n")[0]
    assert result["request_id"] == 7 and result["degraded"] is False
    assert seen["question"] == "sortie en famille"
    search = next(params for sql, params in connection.queries if "vector" in params)
    assert search["vector"] == "[0.1]"
    assert any("query_cache" in sql for sql, _ in connection.queries)      # cherchée puis gardée
    logged = connection.queries[-1][1]
    assert logged["event_ids"] == ["e2", "e1"] and logged["prompt_tokens"] == 100


def test_search_logs_the_visitor_fingerprint() -> None:
    # 27/10/2026 : l'empreinte du navigateur va dans le journal (visiteurs distincts).
    connection = FakeConnection([row("e1")])
    services = SearchServices(embed=None, write=None, now=lambda: NOW)
    list(search_service.run_search(connection, "sortie en famille", SearchFilters(), services,
                                   visitor_key="empreinte-abc"))
    sql, logged = connection.queries[-1]
    assert "visitor_key" in sql and logged["visitor_key"] == "empreinte-abc"


def test_search_without_visitor_logs_none() -> None:
    connection = FakeConnection([row("e1")])
    list(search_service.run_search(connection, "sortie en famille", SearchFilters(),
                                   SearchServices(embed=None, write=None, now=lambda: NOW)))
    assert connection.queries[-1][1]["visitor_key"] is None


def test_search_survives_mistral_outage() -> None:
    connection = FakeConnection([row("e1")])

    def broken(*args):
        raise RuntimeError("503")

    events = run(connection, SearchServices(embed=broken, write=broken, now=lambda: NOW))
    result = events[-1]["result"]
    assert result["degraded"] is True and "Balade contée [1]" in result["answer"]
    assert result["suggestions"] and "vector" not in connection.queries[0][1]


def test_mistral_outage_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """23/10/2026 : les réponses simplifiées ne laissaient aucune trace de leur cause."""

    def broken(*args):
        raise RuntimeError("503 Service Unavailable")

    with caplog.at_level("WARNING"):
        run(FakeConnection([row("e1")]), SearchServices(embed=broken, write=broken, now=lambda: NOW))
    messages = [record.getMessage() for record in caplog.records]
    assert any("Rédaction par Mistral impossible" in message for message in messages)
    assert any("Vecteur de la question impossible" in message for message in messages)
    assert any("503" in str(record.exc_info[1]) for record in caplog.records if record.exc_info)


def test_fallback_says_when_nothing_is_exact() -> None:
    """Sans Mistral, des idées voisines ne sont pas présentées comme la réponse exacte."""

    plan = merge_with_filters(understand("une pièce de théâtre", NOW), public=None, zone=None,
                              period_bounds=search_filters.period_bounds("3mois", NOW),
                              period_label="les 3 prochains mois", free_only=False)
    cards = [{"title": "Festival des sciences", "ref": 1}]
    neighbours, _ = fallback_answer(cards, plan, exact=False)
    assert neighbours.startswith("Je n'ai rien trouvé d'exact") and "Festival des sciences [1]" in neighbours
    assert fallback_answer(cards, plan)[0].startswith("Voici ce que j'ai trouvé")


def test_search_without_result() -> None:
    events = run(FakeConnection([]), SearchServices(embed=None, write=None, now=lambda: NOW))
    result = events[-1]["result"]
    assert result["events"] == [] and "aucun événement" in result["answer"]


def test_sse_format() -> None:
    text = search_service.sse({"event": "progress", "step": "comprendre", "status": "running"})
    assert text.startswith("event: progress\ndata: {") and text.endswith("\n\n")


# ----------------------------------------------------------------------------
# Type de sortie nommé dans la question
# ----------------------------------------------------------------------------

def test_wanted_categories_understands_theatre_without_accent():
    from app.query_understanding import wanted_categories

    assert wanted_categories("j'aimerais aller au theatre") == ("theatre", "spectacle")
    assert wanted_categories("Des pièces de Théâtre ?") == ("theatre", "spectacle")
    assert wanted_categories("des concerts ce soir") == ("concert",)
    assert wanted_categories("que faire samedi ?") == ()


def test_search_query_puts_wanted_categories_first():
    from datetime import datetime

    from app.search_filters import PARIS, SearchFilters
    from app.search_query import build_search_query

    sql, params = build_search_query(SearchFilters(), "[0.1]", datetime(2026, 10, 15, tzinfo=PARIS),
                                     categories=("spectacle", "theatre"))
    assert sql.rstrip().split("\n")[-2] == "ORDER BY visible_hits DESC, keyword_hits DESC, category_match DESC, score DESC, e.start_at"
    assert "= ANY (%(categories)s) OR to_tsvector" in sql and "AS category_match" in sql
    assert params["type_words"] == "spectacle | theatre"
    assert params["categories"] == ["spectacle", "theatre"]


def test_writer_question_flags_a_missing_category():
    from app.search_answer import writer_question

    assert "Aucun événement" in writer_question("du théâtre", ("theatre",), 0)
    assert writer_question("du théâtre", ("theatre",), 2) == "du théâtre"
    assert writer_question("que faire ?", (), 0) == "que faire ?"


def test_event_date_label_shows_ongoing_events():
    from datetime import datetime

    from app.search_cards import event_date_label
    from app.search_filters import PARIS

    now = datetime(2026, 10, 15, 11, 0, tzinfo=PARIS)
    ongoing = {"start_at": datetime(2026, 9, 3, 10, 0, tzinfo=PARIS),
               "end_at": datetime(2026, 12, 19, 23, 59, tzinfo=PARIS), "time_known": True}
    assert event_date_label(ongoing, now) == "En cours · jusqu'au 19 déc."
    coming = {"start_at": datetime(2026, 10, 17, 10, 0, tzinfo=PARIS),
              "end_at": datetime(2026, 10, 17, 12, 0, tzinfo=PARIS), "time_known": True}
    assert event_date_label(coming, now) == "Sam. 17 oct. · 10:00"


def test_group_occurrences_keeps_one_card_per_event():
    from datetime import datetime

    from app.search_cards import group_occurrences, other_dates
    from app.search_filters import PARIS

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
    from app.search_query import distinctive_keywords, keep_exact_matches

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

    from app.search_cards import event_date_label
    from app.search_filters import PARIS

    now = datetime(2026, 10, 15, 11, 0, tzinfo=PARIS)
    festival = {"start_at": datetime(2026, 10, 30, 10, 0, tzinfo=PARIS),
                "end_at": datetime(2026, 11, 1, 18, 0, tzinfo=PARIS), "time_known": True}
    assert event_date_label(festival, now) == "Du 30 oct. au 1 nov."


# ----------------------------------------------------------------------------
# Offres partenaires en tête des cartes (15/10/2026)
# ----------------------------------------------------------------------------

def plan_for(question: str) -> QueryPlan:
    return search_service.plan_search(question, SearchFilters(), NOW)[0]


def test_matching_partner_offers_come_first_in_relevance_order() -> None:
    rows = [row("e1", score=0.03), row("p1", partner_id=1, score=0.02), row("e2", score=0.01),
            row("p2", partner_id=2, score=0.005)]
    ordered = search_query.partners_first(rows, plan_for("un concert ce week-end"), exact=True)
    assert [r["event_id"] for r in ordered] == ["p1", "p2", "e1", "e2"]


def test_premium_partner_offers_lead_among_those_that_answer() -> None:
    """17/10/2026 : la formule départage les offres qui répondent, pas les autres événements."""
    rows = [row("e1", score=0.04), row("p1", partner_id=1, score=0.03), row("p2", partner_id=2, score=0.02,
                                                                           partner_rank=2)]
    ordered = search_query.partners_first(rows, plan_for("un concert ce week-end"), exact=True)
    assert [r["event_id"] for r in ordered] == ["p2", "p1", "e1"]


def test_partner_offer_far_from_a_vague_question_stays_in_its_place() -> None:
    rows = [row(f"e{i}", score=1 / (60 + i)) for i in range(search_query.PARTNER_TOP_RANK)]
    rows.append(row("p1", partner_id=1, score=0.001))
    ordered = search_query.partners_first(rows, plan_for("quelque chose de sympa"), exact=True)
    assert ordered[-1]["event_id"] == "p1"
    near = search_query.partners_first([row("e1", score=0.03), row("p1", partner_id=1, score=0.02)],
                                  plan_for("quelque chose de sympa"), exact=True)
    assert near[0]["event_id"] == "p1"


def test_partner_offers_leave_the_cards_when_nothing_answers_exactly() -> None:
    rows = [row("e1"), row("p1", partner_id=1)]
    ordered = search_query.partners_first(rows, plan_for("du théâtre"), exact=False)
    assert [r["event_id"] for r in ordered] == ["e1"]


def test_cards_open_with_the_partner_offer_that_answers() -> None:
    connection = FakeConnection([row("e1", title="Concert de jazz", category_norm="concert", category_match=True),
                                 row("p1", title="Soirée jazz au bar", category_norm="concert", category_match=True,
                                     partner_id=4, partner_name="Le Bar", venue_name="Bar", city="Sète")])
    events = list(search_service.run_search(connection, "un concert", SearchFilters(),
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
    ordered = search_query.partners_first([play, puppets], plan_for("j'aimerais aller au théâtre"), exact=True,
                                     question="j'aimerais aller au théâtre")
    assert [r["event_id"] for r in ordered] == ["e1", "p1"] and not ordered[1].get("partner_ahead")
    ordered = search_query.partners_first([puppets], plan_for("un spectacle de danse"), exact=False,
                                     question="un spectacle de danse")
    assert ordered == []                                   # idées voisines : l'offre sort des cartes
    staged = row("p2", title="Pièce de théâtre au jardin", category_norm="spectacle", category_match=True,
                 partner_id=2, score=0.01)
    ordered = search_query.partners_first([play, staged], plan_for("j'aimerais aller au théâtre"), exact=True,
                                     question="j'aimerais aller au théâtre")
    assert ordered[0]["event_id"] == "p2" and ordered[0]["partner_ahead"] is True
    climbing = row("p3", title="Initiation escalade enfants", category_norm="atelier", partner_id=3,
                   audiences=["ados", "famille"], score=0.02)
    ados = plan_for("un truc à faire avec des ados")
    ordered = search_query.partners_first([row("e2", score=0.03), climbing], ados, exact=True,
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
    ordered, _ = search_answer.order_cards(cards, "Voici [1].")
    assert [card["event_id"] for card in ordered] == ["e1", "p1"]
    assert "offre partenaire" not in search_answer.events_for_prompt([{**cards[1], "ref": 2, "title": "T", "date": "",
                                                                 "place": "", "category": "", "price": "",
                                                                 "audiences": [], "summary": ""}])


def test_cards_order_partners_then_cited_then_the_rest() -> None:
    cards = [{"ref": i, "event_id": f"e{i}", "partner": i == 6} for i in range(1, 9)]
    ordered, answer = search_answer.order_cards(cards, "Idées : [5] FABRIQUE, [7] LUDI ou [8] BRIQUES, et [5].")
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
    events = list(search_service.run_search(connection, question, filters,
                                     SearchServices(embed=None, write=write, now=lambda: NOW)))
    return events[-1]["result"]


def test_precise_subject_words_are_searched_in_the_text() -> None:
    assert search_query.distinctive_keywords(["cirque", "spectacle", "jazz", "concert", "photo"]) == [
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
    assert search_query.lexical_query(["photo", "cine", "art"]) == "photo:* | cine:* | art"


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
    ordered = search_query.partners_first(rows, plan_for("du théâtre"), exact=False)
    assert [r["event_id"] for r in ordered] == ["p1", "e1"]


def test_type_written_in_the_title_counts_as_the_type() -> None:
    """« jeux » : « Atelier jeux en famille », rangé en atelier, répond aussi (15/10/2026)."""

    sql, params = search_query.build_search_query(plan_for("des jeux"), None)
    assert params["type_words"] == "jeux" and "to_tsquery('french', %(type_words)s)" in sql
    assert "NULLIF(e.category_norm, '')" in sql
    sql, params = search_query.build_search_query(plan_for("une sortie demain"), None)
    assert "type_words" not in params


def test_default_suggestions_offer_the_evening_as_a_time() -> None:
    assert "Plutôt le soir ?" in search_answer.default_suggestions(SearchFilters())
    plan = search_service.plan_search("un concert ce soir", SearchFilters(), NOW)[0]
    assert "Plutôt en journée ?" in search_answer.default_suggestions(plan)


def test_a_suggestion_is_a_single_choice() -> None:
    """17/10/2026 : une relance est un bouton, donc un seul choix."""

    assert search_answer.single_choices("En intérieur ou en extérieur ?") == ["En intérieur ?", "En extérieur ?"]
    assert search_answer.single_choices("Plutôt samedi ou dimanche ?") == ["Plutôt samedi ?", "Plutôt dimanche ?"]
    assert search_answer.single_choices("Et s'il pleut ou s'il vente ?") == ["Et s'il pleut ?", "Et s'il vente ?"]
    assert search_answer.single_choices("Plutôt samedi ou plutôt dimanche ?") == ["Plutôt samedi ?", "Plutôt dimanche ?"]
    assert search_answer.single_choices("Où manger après ?") == ["Où manger après ?"]          # « où » : un lieu
    assert search_answer.single_choices("Et s'il pleut ?") == ["Et s'il pleut ?"]
    raw = json.dumps({"reponse": "Ok.", "suggestions": ["En intérieur ou en extérieur ?", "Plutôt en soirée ?"]})
    assert search_answer.parse_answer(raw, 1)[1] == ["En intérieur\u00a0?", "En extérieur\u00a0?", "Plutôt en soirée\u00a0?"]



def test_the_moment_of_the_day_filters_the_start_hour() -> None:
    """17/10/2026 : « le matin », « l'après-midi » écrits filtrent l'heure de début."""

    def sql_for(question: str) -> str:
        return search_query.build_search_query(search_service.plan_search(question, SearchFilters(), NOW)[0], None)[0]

    assert "BETWEEN 0 AND 11" in sql_for("un atelier samedi matin")
    assert "BETWEEN 12 AND 17" in sql_for("un spectacle l'après-midi")
    assert ">= 17" in sql_for("un concert en soirée")
    assert "BETWEEN" not in sql_for("un concert")


def test_demo_offers_are_marked() -> None:
    """18/10/2026 : partenaires fictifs annoncés comme démonstration, sans faux lien."""
    assert search_cards.to_card(row(url="https://example.org/bar"))["demo"] is True
    assert search_cards.to_card(row(url="https://www.sete.fr/agenda"))["demo"] is False
    assert search_cards.is_demo_link("https://sub.example.com/x") and not search_cards.is_demo_link("https://notexample.org")


def test_child_public_is_part_of_family() -> None:
    """18/10/2026 : « Enfant » = familial et pour l'enfant (âge ou texte), sans nouveau public en base."""
    sql, params = search_query.build_search_query(SearchFilters(public="enfant"), None, NOW)
    assert "'famille' = ANY (e.audiences)" in sql and "e.age_max <= 14" in sql and "public" not in params
    sql, params = search_query.build_search_query(SearchFilters(public="famille"), None, NOW)
    assert params["public"] == "famille"


# --- Modèle de secours de la rédaction (23/10/2026) ---------------------------

def test_writer_falls_back_to_a_second_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le 23/10/2026, `mistral-small-latest` répondait 503 : le second modèle répond."""

    from src.event import event_enrichment

    asked: list[str] = []

    def call(system: str, text: str, model: str) -> tuple[str, dict[str, int]]:
        asked.append(model)
        if model == "mistral-small-latest":
            raise RuntimeError("503 Service unavailable")
        return '{"reponse": "ok"}', {"input": 10, "output": 2}

    monkeypatch.setattr(event_enrichment, "call_mistral_json", call)
    monkeypatch.delenv("SEARCH_MODEL", raising=False)
    monkeypatch.delenv("SEARCH_FALLBACK_MODEL", raising=False)
    plan = merge_with_filters(understand("un concert", NOW), public=None, zone=None,
                              period_bounds=search_filters.period_bounds("3mois", NOW),
                              period_label="les 3 prochains mois", free_only=False)
    raw, usage, model = search_answer.write_with_mistral("un concert", plan, [])
    assert asked == ["mistral-small-latest", "mistral-medium-latest"]
    assert model == "mistral-medium-latest" and raw == '{"reponse": "ok"}' and usage["input"] == 10


def test_writer_raises_when_every_model_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.event import event_enrichment

    def call(system: str, text: str, model: str) -> tuple[str, dict[str, int]]:
        raise RuntimeError(f"503 {model}")

    monkeypatch.setattr(event_enrichment, "call_mistral_json", call)
    monkeypatch.setenv("SEARCH_MODEL", "modele-a")
    monkeypatch.setenv("SEARCH_FALLBACK_MODEL", "modele-b")
    with pytest.raises(RuntimeError, match="modele-b"):
        search_answer.write_with_mistral("un concert", SearchFilters(), [])


@pytest.mark.parametrize(("main", "fallback", "expected"), [
    (None, None, ["mistral-small-latest", "mistral-medium-latest"]),
    ("mistral-medium-latest", None, ["mistral-medium-latest"]),       # secours identique : un seul essai
    ("ministral-8b-latest", "", ["ministral-8b-latest"]),              # secours désactivé
])
def test_writer_models(monkeypatch: pytest.MonkeyPatch, main: str | None, fallback: str | None,
                       expected: list[str]) -> None:
    for name, value in (("SEARCH_MODEL", main), ("SEARCH_FALLBACK_MODEL", fallback)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert search_answer.writer_models() == expected
