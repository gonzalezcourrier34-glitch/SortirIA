"""Tests de la compréhension de la question (app/query_understanding.py).

**Provenance** : nouveau fichier écrit pour SortirIA (15/10/2026). Jeudi
15 octobre 2026, 11 h : samedi = le 17, dimanche = le 18.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.query_understanding import (
    PARIS,
    QueryPlan,
    Understanding,
    combine,
    is_follow_up,
    merge_with_filters,
    understand,
)

NOW = datetime(2026, 10, 15, 11, 0, tzinfo=PARIS)        # jeudi


def day(text: str) -> tuple[str, str, str] | None:
    period = understand(text, NOW).period
    return (period[0].strftime("%d/%m %H:%M"), period[1].strftime("%d/%m"), period[2]) if period else None


@pytest.mark.parametrize(("question", "expected"), [
    ("concert ce soir", ("15/10 11:00", "16/10", "aujourd'hui")),
    ("quoi faire demain ?", ("16/10 00:00", "17/10", "demain")),
    ("une expo ce week-end", ("17/10 00:00", "19/10", "ce week-end")),
    ("du théâtre samedi", ("17/10 00:00", "18/10", "sam. 17 oct.")),
    ("le 24 octobre", ("24/10 00:00", "25/10", "sam. 24 oct.")),
    ("le 24/10 à Sète", ("24/10 00:00", "25/10", "sam. 24 oct.")),
    ("la semaine prochaine", ("19/10 00:00", "26/10", "la semaine prochaine")),
    ("en novembre", ("01/11 00:00", "01/12", "en novembre")),
    ("jeudi prochain", ("22/10 00:00", "23/10", "jeu. 22 oct.")),
    ("un concert de jazz", None),
])
def test_dates(question: str, expected) -> None:
    assert day(question) == expected


def test_places_publics_price_and_type() -> None:
    u = understand("Du théâtre samedi à Sète avec les enfants, gratuit si possible", NOW)
    assert (u.cities, u.public, u.free_only) == (("sete",), "famille", True)
    assert u.categories == ("theatre", "spectacle") and u.keywords == ("theatre",)
    assert understand("balade autour de l'étang", NOW).zone == "thau"
    assert understand("à Balaruc", NOW).cities == ("balaruc le vieux", "balaruc les bains")
    assert understand("jeux d'échecs pour ados à Mèze", NOW).keywords == ("jeux", "echecs")


@pytest.mark.parametrize("question", ["un petit concert", "pour tous âges", "avec des amis"])
def test_ambiguous_words_set_no_public(question: str) -> None:
    assert understand(question, NOW).public is None


def test_evening() -> None:
    assert understand("concert samedi soir", NOW).evening
    assert not understand("concert samedi", NOW).evening


def test_question_narrows_the_ticked_filters() -> None:
    three_months = (NOW, datetime(2027, 1, 13, tzinfo=PARIS))
    plan = merge_with_filters(understand("théâtre samedi à Sète", NOW), public=None, zone="montpellier",
                              period_bounds=three_months, period_label="les 3 prochains mois", free_only=False)
    assert plan.start.day == 17 and plan.end.day == 18 and plan.period_label == "sam. 17 oct."
    assert plan.cities == ("sete",) and plan.zone is None           # la commune écrite l'emporte
    assert "cities" in plan.from_question
    assert plan.describe() == "sam. 17 oct. · Sète · théâtre ou spectacle"
    # Période cochée incompatible : ce qui est écrit l'emporte.
    today = (NOW, datetime(2026, 10, 16, tzinfo=PARIS))
    plan = merge_with_filters(understand("samedi", NOW), public=None, zone=None, period_bounds=today,
                              period_label="aujourd'hui", free_only=False)
    assert plan.start.day == 17


def test_relaxed_plan_keeps_ticked_filters_and_date() -> None:
    plan = merge_with_filters(understand("concert gratuit à Sète pour les enfants samedi", NOW), public=None,
                              zone=None, period_bounds=(NOW, datetime(2027, 1, 1, tzinfo=PARIS)),
                              period_label="3 mois", free_only=False)
    relaxed = plan.relaxed()
    assert (relaxed.cities, relaxed.public, relaxed.free_only) == ((), None, False)
    assert relaxed.start == plan.start and relaxed.categories == ("concert",)
    assert isinstance(relaxed, QueryPlan)


def test_follow_up_keeps_the_previous_request() -> None:
    assert is_follow_up("et pour les enfants ?") and is_follow_up("plutôt samedi")
    assert not is_follow_up("je cherche une exposition de photographie à Montpellier")
    # Un nouveau sujet, même en un mot, est une nouvelle demande (15/10/2026).
    assert not is_follow_up("photo") and not is_follow_up("du jazz") and not is_follow_up("un concert")
    assert is_follow_up("à Sète ?") and is_follow_up("en famille") and is_follow_up("moins cher")
    merged = combine(understand("concert de jazz à Sète samedi", NOW), understand("et dimanche ?", NOW))
    assert merged.period[2] == "dim. 18 oct." and merged.cities == ("sete",)
    assert merged.categories == ("concert",) and "jazz" in merged.keywords
    assert combine(Understanding(cities=("sete",)), understand("plutôt autour de l'étang", NOW)).cities == ()


def test_every_evaluation_run_is_kept(tmp_path) -> None:
    """scripts/eval_recherche.py : rapport JSON + une ligne d'historique par lancement."""

    from scripts.eval_recherche import save_run

    questions = tmp_path / "questions.yaml"
    questions.write_text("questions: []\n", encoding="utf-8")
    report = {"date": "2026-10-15T12:30:00+02:00", "questions": 32, "comprehension": 1.0, "precision": 0.99,
              "idees_voisines": 2, "sans_resultat": 0, "latence_mediane_s": 0.54, "latence_p95_s": 1.17,
              "detail": []}
    first = save_run(report, mode="recherche", question_file=questions, note="hybride", folder=tmp_path)
    save_run({**report, "date": "2026-10-15T13:00:00+02:00"}, mode="recherche", question_file=questions,
             note="", folder=tmp_path)
    assert first.name == "2026-10-15_12h30.json"
    lines = (tmp_path / "historique.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 3 and lines[0].startswith("date;mode;jeu;") and ";0.99;" in lines[1]


# --- Budget, cadre, distance (15/10/2026) -----------------------------------

@pytest.mark.parametrize(("question", "price"), [
    ("un concert à moins de 15 €", 15.0),
    ("un concert à moins de 15€", 15.0),
    ("un spectacle 20 euros max", 20.0),
    ("budget 30", 30.0),
    ("une sortie pas chère", 15.0),
    ("moins de 15 ans", None),                 # un âge, pas un prix
    ("à moins de 20 km de Sète", None),        # une distance, pas un prix
    ("du théâtre gratuit", None),              # gratuit l'emporte
])
def test_budget(question: str, price) -> None:
    assert understand(question, NOW).max_price == price


def test_setting_and_distance() -> None:
    assert understand("que faire s'il pleut ce week-end", NOW).setting == "interieur"
    assert understand("du cinéma en plein air", NOW).setting == "exterieur"
    assert understand("une balade près de Sète", NOW).near == ("sete", 10.0)
    assert understand("un atelier à 5 km de Mèze", NOW).near == ("meze", 5.0)
    assert understand("un concert à Sète", NOW).near is None           # « à Sète » : dans la commune
    plan = merge_with_filters(understand("pas cher près de Frontignan s'il pleut", NOW), public=None, zone="thau",
                              period_bounds=(NOW, datetime(2027, 1, 1, tzinfo=PARIS)), period_label="3 mois",
                              free_only=False)
    assert plan.near == ("frontignan", 10.0) and plan.zone is None and plan.cities == ()
    assert plan.describe() == "3 mois · à moins de 10 km de Frontignan · moins de 15 € · en intérieur"
    relaxed = plan.relaxed()
    assert (relaxed.near, relaxed.max_price, relaxed.setting) == (None, None, None)
    assert understand("plutôt pas cher", NOW).keywords == ()           # « cher » n'est pas un mot-clé


def test_a_montpellier_means_the_city_itself() -> None:
    """Grille de pertinence du 16/10/2026 : « des spectacles gratuits à
    Montpellier » proposait Mauguio et Palavas (zone de 20 km)."""

    from app.query_understanding import find_places, plain

    assert find_places(plain("des spectacles gratuits à Montpellier")) == (("montpellier",), "montpellier")
    assert find_places(plain("un festival près de Montpellier")) == ((), "montpellier")
    assert find_places(plain("que faire dans la métropole de Montpellier")) == ((), "montpellier")


def test_a_montpellier_keeps_the_metropolis_for_widening() -> None:
    from app.search_service import SearchFilters, plan_search

    plan = plan_search("des spectacles gratuits à Montpellier", SearchFilters(), NOW)[0]
    assert plan.cities == ("montpellier",) and plan.zone == "montpellier"
    plan = plan_search("un concert à Mèze", SearchFilters(), NOW)[0]
    assert plan.cities == ("meze",) and plan.zone is None


def test_a_clicked_follow_up_keeps_the_previous_request() -> None:
    """17/10/2026 : « Des spectacles en intérieur ? » cliqué après « un spectacle
    à Sète » perdait Sète (relance longue avec un sujet : nouvelle demande)."""
    from app.search_service import SearchFilters, plan_search

    typed = plan_search("Des spectacles en intérieur ?", SearchFilters(), NOW, "un spectacle à Sète")[0]
    assert typed.cities == ()
    clicked, embed = plan_search("Des spectacles en intérieur ?", SearchFilters(), NOW, "un spectacle à Sète",
                                 follow_up=True)
    assert clicked.cities == ("sete",) and clicked.setting == "interieur"
    assert "Sète" in embed
    short = plan_search("Plutôt en matinée ?", SearchFilters(), NOW, "un atelier à Sète samedi", follow_up=True)[0]
    assert short.cities == ("sete",)


def test_the_cache_tells_a_clicked_follow_up_apart() -> None:
    from app.search_service import SearchFilters, cache_key

    assert cache_key("x ?", "y", SearchFilters(), NOW, "v") != cache_key("x ?", "y", SearchFilters(), NOW, "v", True)


def test_the_question_changes_the_checked_boxes() -> None:
    """17/10/2026 : « avec les enfants » avec « Seniors » coché cherchait pour les seniors."""
    from app.search_service import SearchFilters, checked_by_question, plan_search

    plan = plan_search("un truc avec les enfants samedi près de Sète", SearchFilters(public="seniors",
                                                                                   zone="montpellier"), NOW)[0]
    assert plan.public == "famille"
    assert checked_by_question(plan) == {"public": "famille", "zone": "thau", "date": plan.period_label}
    # Sans public écrit, la case cochée reste.
    plan = plan_search("un concert samedi", SearchFilters(public="seniors"), NOW)[0]
    assert plan.public == "seniors" and checked_by_question(plan) == {"date": plan.period_label}
    assert "sam" in plan.period_label
    # Ce qui vient de la question précédente cède à la case qu'on vient de cocher.
    plan = plan_search("et samedi ?", SearchFilters(public="seniors"), NOW, "un atelier pour les enfants")[0]
    assert plan.public == "seniors" and "public" not in checked_by_question(plan)



def test_in_the_evening_is_a_time_not_the_evening_public() -> None:
    """17/10/2026 : « Plutôt en soirée ? » cochait le public « Soirée » (bars, fêtes)."""
    from app.search_service import SearchFilters, checked_by_question, plan_search

    understood = understand("un spectacle en soirée à Sète", NOW)
    assert understood.evening and understood.public is None
    assert "soiree" not in understood.keywords
    assert understand("une soirée dans un bar", NOW).public == "soiree"          # le public reste
    plan = plan_search("Plutôt en soirée ?", SearchFilters(), NOW, "un spectacle à Sète", follow_up=True)[0]
    assert plan.evening and plan.public is None
    assert checked_by_question(plan) == {"evening": True, "zone": "thau"}
    assert not plan.period_from_question


def test_the_evening_box_and_the_daytime_question() -> None:
    """« Le soir » coché filtre ; « en journée » écrit (ou relancé) l'annule et décoche."""
    from app.search_service import SearchFilters, checked_by_question, plan_search

    plan = plan_search("un concert à Sète", SearchFilters(evening=True), NOW)[0]
    assert plan.evening and checked_by_question(plan) == {"zone": "thau"}
    assert plan.relaxed().evening                                               # coché : gardé
    plan = plan_search("un concert en journée", SearchFilters(evening=True), NOW)[0]
    assert not plan.evening and checked_by_question(plan) == {"evening": False}
    plan = plan_search("Plutôt en matinée ?", SearchFilters(), NOW, "un concert le soir", follow_up=True)[0]
    assert not plan.evening
    plan = plan_search("un concert le soir", SearchFilters(), NOW)[0]
    assert not plan.relaxed().evening                                           # écrit : relâché
