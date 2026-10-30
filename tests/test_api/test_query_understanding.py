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


def test_relaxed_plan_keeps_the_date_from_the_question_but_not_the_moment() -> None:
    """23/10/2026 : « sauf la date » : la date lue dans la question reste cochée
    (`period_from_question`) ; le moment de la journée, lui, est relâché."""

    from app.search_filters import SearchFilters
    from app.search_service import checked_by_question, plan_search

    plan = plan_search("un concert gratuit à Sète samedi matin", SearchFilters(), NOW)[0]
    assert plan.period_from_question and plan.moment == "matin"
    relaxed = plan.relaxed()
    assert relaxed.period_from_question and relaxed.moment is None
    assert (relaxed.start, relaxed.end, relaxed.period_label) == (plan.start, plan.end, plan.period_label)
    assert checked_by_question(relaxed) == {"date": plan.period_label}


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
    same_minute = save_run(report, mode="recherche", question_file=questions, note="sans la règle", folder=tmp_path)
    assert same_minute.name == "2026-10-15_12h30_2.json" and first.exists()     # le premier n'est pas écrasé
    lines = (tmp_path / "historique.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 4 and lines[0].startswith("date;mode;jeu;") and ";0.99;" in lines[1]


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
    from app.search_filters import SearchFilters
    from app.search_service import plan_search

    plan = plan_search("des spectacles gratuits à Montpellier", SearchFilters(), NOW)[0]
    assert plan.cities == ("montpellier",) and plan.zone == "montpellier"
    plan = plan_search("un concert à Mèze", SearchFilters(), NOW)[0]
    assert plan.cities == ("meze",) and plan.zone is None


def test_a_clicked_follow_up_keeps_the_previous_request() -> None:
    """17/10/2026 : « Des spectacles en intérieur ? » cliqué après « un spectacle
    à Sète » perdait Sète (relance longue avec un sujet : nouvelle demande)."""
    from app.search_filters import SearchFilters
    from app.search_service import plan_search

    typed = plan_search("Des spectacles en intérieur ?", SearchFilters(), NOW, "un spectacle à Sète")[0]
    assert typed.cities == ()
    clicked, embed = plan_search("Des spectacles en intérieur ?", SearchFilters(), NOW, "un spectacle à Sète",
                                 follow_up=True)
    assert clicked.cities == ("sete",) and clicked.setting == "interieur"
    assert "Sète" in embed
    short = plan_search("Plutôt en matinée ?", SearchFilters(), NOW, "un atelier à Sète samedi", follow_up=True)[0]
    assert short.cities == ("sete",)


def test_the_cache_tells_a_clicked_follow_up_apart() -> None:
    from app.search_cache import cache_key
    from app.search_filters import SearchFilters

    assert cache_key("x ?", "y", SearchFilters(), NOW, "v") != cache_key("x ?", "y", SearchFilters(), NOW, "v", True)


def test_the_question_changes_the_checked_boxes() -> None:
    """17/10/2026 : « avec les enfants » avec « Seniors » coché cherchait pour les seniors."""
    from app.search_filters import SearchFilters
    from app.search_service import checked_by_question, plan_search

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



def test_evening_public_and_type_are_written_once() -> None:
    """30/10/2026 : « soirée électro à Montpellier samedi » affichait « soirée · soirée »."""
    from app.search_filters import SearchFilters
    from app.search_service import plan_search

    plan = plan_search("soirée électro à Montpellier samedi", SearchFilters(), NOW)[0]
    assert plan.public == "soiree" and "soiree" in plan.categories
    assert plan.describe().count("soirée") == 1
    plan = plan_search("soirée jeux de société à Montpellier", SearchFilters(), NOW)[0]
    assert plan.describe().count("soirée") == 1                   # « soirée ou jeux »


def test_in_the_evening_is_a_time_not_the_evening_public() -> None:
    """17/10/2026 : « Plutôt en soirée ? » cochait le public « Soirée » (bars, fêtes)."""
    from app.search_filters import SearchFilters
    from app.search_service import checked_by_question, plan_search

    understood = understand("un spectacle en soirée à Sète", NOW)
    assert understood.evening and understood.public is None
    assert "soiree" not in understood.keywords
    assert understand("une soirée dans un bar", NOW).public == "soiree"          # le public reste
    plan = plan_search("Plutôt en soirée ?", SearchFilters(), NOW, "un spectacle à Sète", follow_up=True)[0]
    assert plan.evening and plan.public is None and "moment" in plan.from_question
    assert checked_by_question(plan) == {"zone": "thau"}                        # aucune case : lu dans la phrase
    assert not plan.relaxed().evening


@pytest.mark.parametrize(("question", "moment"), [
    ("un atelier samedi matin", "matin"),
    ("une balade en matinée", "matin"),
    ("un spectacle l'après-midi", "apres-midi"),
    ("une expo en journée", "journee"),
    ("un concert", None),
])
def test_the_moment_of_the_day_is_read_in_the_sentence(question: str, moment: str | None) -> None:
    understood = understand(question, NOW)
    assert understood.moment == moment and not understood.evening


def test_a_daytime_follow_up_replaces_the_evening() -> None:
    from app.search_filters import SearchFilters
    from app.search_service import plan_search

    plan = plan_search("Plutôt en matinée ?", SearchFilters(), NOW, "un concert le soir", follow_up=True)[0]
    assert not plan.evening and plan.moment == "matin"
    assert "(le matin)" in plan.describe()
    plan = plan_search("Plutôt le soir ?", SearchFilters(), NOW, "une balade le matin", follow_up=True)[0]
    assert plan.evening and plan.moment is None
    plan = plan_search("et à Mèze ?", SearchFilters(), NOW, "une balade le matin")[0]
    assert plan.moment == "matin"                                               # gardé par la relance


@pytest.mark.parametrize(("question", "practice"), [
    ("je veux chanter", "chanter"),
    ("où danser ce soir à Sète", "danser"),
    ("jouer aux jeux de société", "jouer"),
    ("apprendre à dessiner", "dessiner"),
    ("cuisiner avec les enfants", "cuisiner"),
    ("jouer au théâtre", "theatre"),                  # en faire soi-même (18/10/2026)
    ("aller au théâtre", None),                       # en voir : recherche habituelle
    ("un concert", None),
])
def test_practice_verbs(question: str, practice: str | None) -> None:
    """17/10/2026 : « je veux chanter », c'est chanter soi-même, pas écouter un concert."""
    understood = understand(question, NOW)
    assert understood.practice == practice
    if practice:
        assert understood.keywords == ({"theatre": "theatral"}.get(practice, practice),) and understood.categories == ()


def test_a_follow_up_keeps_the_practice_until_a_new_subject() -> None:
    from app.search_filters import SearchFilters
    from app.search_service import plan_search

    plan = plan_search("et à Sète ?", SearchFilters(), NOW, "je veux chanter")[0]
    assert plan.practice == "chanter" and "pour chanter soi-même" in plan.describe()
    plan = plan_search("un concert de jazz", SearchFilters(), NOW, "je veux chanter")[0]
    assert plan.practice is None


@pytest.mark.parametrize(("question", "keywords", "categories"), [
    # Bilan du 19/10/2026 : des mots de la phrase cherchés dans les titres.
    ("quels sont les prochaines expositions de peinture ?", ("expositions", "peinture"), ("exposition",)),
    ("ou je peux voire des tableaux de peinture", ("tableaux", "peinture"), ("exposition",)),
    ("bonjour, je voudrais des propositions de concert", ("concert",), ("concert",)),
])
def test_words_of_the_sentence_are_not_subjects(question: str, keywords: tuple[str, ...],
                                                categories: tuple[str, ...]) -> None:
    understood = understand(question, NOW)
    assert understood.keywords == keywords and understood.categories == categories
