"""Corrections issues des 200 questions difficiles posées au site en ligne (P5 et P6).

**Provenance** : nouveau fichier écrit pour SortirIA (02/11/2026). Chaque test
reprend une question réelle du compte rendu (perso/tests_production) qui
était mal comprise ou mal rédigée. Lundi 2 novembre 2026, 10 h : samedi = le 7.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import search_answer, search_cards, search_query
from app.query_understanding import PARIS, find_exclusions, is_follow_up, plain, understand
from app.search_filters import SearchFilters
from app.search_service import plan_search

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=PARIS)        # lundi, lendemain de la Toussaint


def period(question: str) -> tuple[str, str, str] | None:
    """(début, fin exclue, libellé) de la période lue dans la question."""
    found = understand(question, NOW).period
    return (found[0].strftime("%d/%m/%Y"), found[1].strftime("%d/%m/%Y"), found[2]) if found else None


# ----------------------------------------------------------------------------
# Dates relatives (11 dates mal lues sur 200)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(("question", "expected"), [
    ("le week-end du 14 novembre autour de Thau", ("14/11/2026", "16/11/2026", "le week-end du sam. 14 nov.")),
    ("le premier week-end de décembre", ("05/12/2026", "07/12/2026", "le week-end du sam. 5 déc.")),
    ("le dernier week-end de novembre", ("28/11/2026", "30/11/2026", "le week-end du sam. 28 nov.")),
    ("la semaine du 23 novembre, un atelier", ("23/11/2026", "30/11/2026", "la semaine du lun. 23 nov.")),
    ("après le 20 novembre, un spectacle", ("21/11/2026", "19/02/2027", "à partir du sam. 21 nov.")),
    ("avant le 10 décembre", ("02/11/2026", "10/12/2026", "jusqu'au mer. 9 déc.")),
    ("du 5 au 8 décembre", ("05/12/2026", "09/12/2026", "du sam. 5 déc. au mar. 8 déc.")),
    ("début décembre un spectacle", ("01/12/2026", "16/12/2026", "début décembre")),
    ("fin novembre une expo", ("16/11/2026", "01/12/2026", "fin novembre")),
    ("ce matin à Sète", ("02/11/2026", "03/11/2026", "aujourd'hui")),
    ("cette nuit à Montpellier", ("02/11/2026", "03/11/2026", "aujourd'hui")),
    ("dans 2 semaines", ("16/11/2026", "23/11/2026", "la semaine du lun. 16 nov.")),
    ("dans 3 jours", ("05/11/2026", "06/11/2026", "jeu. 5 nov.")),
    ("d'ici samedi", ("02/11/2026", "08/11/2026", "d'ici sam. 7 nov.")),
    ("des trucs à faire pdt les vacances de noël", ("19/12/2026", "04/01/2027", "les vacances de Noël")),
    ("pendant les vacances scolaires", ("19/12/2026", "04/01/2027", "les vacances de Noël")),
    ("une soirée pour le nouvel an", ("31/12/2026", "01/01/2027", "le réveillon du nouvel an")),
    ("l'an prochain", ("01/01/2027", "01/04/2027", "début 2027")),
    ("kconcert mtp ce we", ("07/11/2026", "09/11/2026", "ce week-end")),
])
def test_relative_dates(question: str, expected: tuple[str, str, str]) -> None:
    assert period(question) == expected


@pytest.mark.parametrize("question", [
    "jusqu'à 20 euros", "à partir de 6 ans", "un spectacle de 2h", "un marché de Noël", "enfants de 3 à 6 ans",
])
def test_numbers_that_are_not_dates(question: str) -> None:
    """Un prix, un âge, une durée ou un sujet (« marché de Noël ») ne sont pas des dates."""
    assert period(question) is None


def test_christmas_market_keeps_its_subject() -> None:
    assert "noel" in understand("un marché de Noël à Sète", NOW).keywords
    assert "noel" not in understand("des idées pendant les vacances de Noël", NOW).keywords


@pytest.mark.parametrize(("question", "evening", "moment"), [
    ("aujourd'hui à 15h", False, "apres-midi"),
    ("entre 18h et 20h jeudi à Montpellier", True, None),
    ("vers 10h30 samedi", False, "matin"),
    ("1h30 de spectacle", False, None),                    # une durée, pas une heure
])
def test_written_hours_give_the_moment(question: str, evening: bool, moment: str | None) -> None:
    understood = understand(question, NOW)
    assert (understood.evening, understood.moment) == (evening, moment)
    assert not any(word.endswith("h") or word.endswith("h30") for word in understood.keywords)


# ----------------------------------------------------------------------------
# Relances tapées (5 sur 10 perdaient la demande précédente)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "[t] et pour les ados ?", "[t] seulement gratuit ?", "[t] à moins de 10 euros ?", "[t] et s'il pleut ?",
    "[t] pour les petits ?", "[t] et des cours ?", "juste le gratuit", "sans théâtre ?",
])
def test_typed_follow_ups(question: str) -> None:
    """La marque de test « [t] » est ignorée ; « seulement », « à moins de… » précisent."""
    assert is_follow_up(question)


@pytest.mark.parametrize("question", ["du jazz", "photo", "un concert ce soir à Sète",
                                      "que faire ce week-end à Sète en famille"])
def test_new_requests_are_not_follow_ups(question: str) -> None:
    assert not is_follow_up(question)


def test_follow_up_keeps_place_subject_and_date() -> None:
    plan = plan_search("[t] seulement gratuit ?", SearchFilters(), NOW, "[t] exposition à Montpellier")[0]
    assert plan.describe() == "les 3 prochains mois · Montpellier · gratuit · exposition"
    plan = plan_search("[t] et s'il pleut ?", SearchFilters(), NOW, "[t] sortie en famille ce week-end")[0]
    assert plan.describe() == "ce week-end · famille · en intérieur"
    # La date héritée est aussi la date cochée (la case montrait « 3 prochains mois »)
    from app.search_service import checked_by_question
    assert checked_by_question(plan)["date"] == "ce week-end"


# ----------------------------------------------------------------------------
# Négations (4 ignorées ou inversées)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(("question", "described"), [
    ("tout sauf du théâtre pour samedi soir à Montpellier",
     "sam. 7 nov. (le soir) · Montpellier · sauf théâtre"),
    ("un concert mais pas de rock ni d'électro", "les 3 prochains mois · concert · sauf rock, electro"),
    ("pas trop loin de Sète mais pas à Montpellier, un concert ce week-end",
     "ce week-end · à moins de 10 km de Sète · concert · sauf Montpellier"),
    ("une sortie qui ne soit pas payante et pas trop loin de Poussan",
     "les 3 prochains mois · à moins de 10 km de Poussan · gratuit"),
    ("une soirée, pas dans le bassin de Thau", "les 3 prochains mois · soirée · sauf bassin de Thau"),
    ("sauf à Sète et Mèze", "les 3 prochains mois · sauf Sète, Mèze"),
])
def test_negations_become_exclusions(question: str, described: str) -> None:
    assert plan_search(question, SearchFilters(), NOW)[0].describe() == described


@pytest.mark.parametrize("question", ["pas cher près de Frontignan", "sans payer", "pas loin de Mèze",
                                      "sortie en famille sans voiture", "pas mal de concerts"])
def test_negations_that_exclude_nothing(question: str) -> None:
    excluded = find_exclusions(plain(question))[1]
    assert not any(excluded.values())


def test_exclusions_reach_the_sql_query() -> None:
    plan = plan_search("tout sauf du théâtre, pas de rock, pas à Sète, pas dans le bassin de Thau",
                       SearchFilters(), NOW)[0]
    sql, params = search_query.build_search_query(plan, None)
    assert params["excluded_categories"] == ["theatre"] and params["excluded_words"] == "rock"
    assert params["excluded_cities"] == ["sete"] and params["excluded_zones"] == ["bassin_de_thau"]
    # Le titre ne compte que dans les types voisins : un concert « au Théâtre de la Mer » reste
    assert "excluded_related" in sql and params["excluded_related"] == ["theatre", "spectacle"]
    partner_sql, partner_params = search_query.build_partner_query(plan, None, [])
    assert partner_params["excluded_cities"] == ["sete"] and "excluded_cities" in partner_sql


def test_partners_do_not_lead_with_an_excluded_type() -> None:
    from app.query_understanding import main_categories
    assert main_categories(find_exclusions(plain("tout sauf du théâtre"))[0]) == ()


# ----------------------------------------------------------------------------
# Argot, publics et communes (16)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(("question", "public"), [
    ("un truc sympa pr les gosses dimanche", "famille"),
    ("fête foraine ou manège pour les petits", "famille"),
    ("visites pr les vieux à Sète", "seniors"),
    ("des petits prix ce soir", None),
])
def test_slang_publics(question: str, public: str | None) -> None:
    assert understand(question, NOW).public == public


@pytest.mark.parametrize(("question", "cities"), [
    ("que faire à Palavas", ("palavas les flots",)),
    ("que faire à La Grande-Motte", ("la grande motte",)),
    ("sortie à Carnon", ("mauguio", "carnon")),
])
def test_nearby_towns(question: str, cities: tuple[str, ...]) -> None:
    understood = understand(question, NOW)
    assert understood.cities == cities and understood.keywords == ()


def test_places_outside_the_area_are_said() -> None:
    understood = understand("que faire à Agde demain", NOW)
    assert understood.outside == ("Agde",) and understood.keywords == ()
    plan = plan_search("concert à Paris ce week-end", SearchFilters(), NOW)[0]
    assert plan.describe() == "ce week-end · concert · Paris (hors zone)"
    note = search_answer.writer_question("concert à Paris", ("concert",), 3, outside=plan.outside)
    assert "Paris est hors de la zone couverte" in note


# ----------------------------------------------------------------------------
# Faits inventés par Mistral (10)
# ----------------------------------------------------------------------------

CARDS = [
    {"ref": 1, "title": "Marché des halles de Sète", "city": "Sète", "place": "Halles, Sète", "category": "marche",
     "summary": "Le marché couvert du centre-ville.", "audiences": [], "price": "Gratuit"},
    {"ref": 2, "title": "Ateliers du miam", "city": "Sète", "place": "Sète", "category": "atelier",
     "summary": "Ateliers d'arts plastiques pendant les vacances.", "audiences": [], "price": "5 €"},
    {"ref": 3, "title": "La grande braderie", "city": "Frontignan", "place": "Frontignan", "category": "marche",
     "summary": "Vêtements et objets.", "audiences": [], "price": "Gratuit"},
]


def test_access_claims_without_support_are_removed() -> None:
    """« Le marché est accessible en transports en commun » : la liste n'en dit rien."""
    answer = ("Voici une idée : Marché des halles de Sète [1]. Il est accessible en transports en commun. "
              "Ateliers du miam [2] propose des arts plastiques.")
    assert search_answer.check_facts(answer, CARDS) == ("Voici une idée : Marché des halles de Sète [1]. "
                                                        "Ateliers du miam [2] propose des arts plastiques.")
    with pytest.raises(search_answer.RefusedAnswer):           # la seule phrase citée : réponse simple
        search_answer.check_facts("Le [1] Marché des halles est accessible en tram.", CARDS)


def test_public_not_asked_nor_in_the_cards_is_removed() -> None:
    """« pour occuper vos ados » : ni demandé, ni dans les cartes."""
    answer = "Voici quelques idées pour occuper vos ados pendant les vacances : Ateliers du miam [2]."
    assert search_answer.check_facts(answer, CARDS, request="des trucs pdt les vacances") == \
        "Voici quelques idées pendant les vacances : Ateliers du miam [2]."
    # Demandé : gardé
    assert search_answer.check_facts(answer, CARDS, request="pour les ados") == answer
    assert search_answer.check_facts("Des ateliers en famille et gratuits : Ateliers du miam [2].", CARDS) == \
        "Des ateliers gratuits : Ateliers du miam [2]."


def test_a_place_absent_from_the_cards_is_not_claimed() -> None:
    """« À La Grande-Motte, … [3] » pour une braderie à Frontignan."""
    answer = ("À La Grande-Motte, voici une idée : La grande braderie [3]. "
              "Sinon, Marché des halles de Sète [1].")
    assert search_answer.check_facts(answer, CARDS) == "Sinon, Marché des halles de Sète [1]."
    honest = "Je n'ai rien trouvé à La Grande-Motte. Idées voisines : La grande braderie [3] à Frontignan."
    assert search_answer.check_facts(honest, CARDS) == honest
    near = "Autour de Montpellier et près de Sète : La grande braderie [3]."
    assert search_answer.check_facts(near, CARDS) == near


def test_english_answers_are_refused() -> None:
    with pytest.raises(search_answer.RefusedAnswer):
        search_answer.check_facts("Here are some great options for your weekend in Sète: the market [1].", CARDS)


def test_exact_answers_are_not_called_neighbouring_ideas() -> None:
    """« et pour demain ? » répondait « pas d'événement demain » devant des cartes de demain."""
    assert "ne dis pas qu'il n'y a rien" in search_answer.writer_question("et pour demain ?", (), 0,
                                                                             filters_met=True)
    answer = search_answer.name_citations("Le musée de Loupian est gratuit le dimanche.", CARDS[:1], exact=True)
    assert "À découvrir" in answer and "Idées voisines" not in answer


def test_bracketed_titles_and_copied_notes() -> None:
    import json
    raw = json.dumps({"reponse": "Le [Festival dernier cri] [1] ; le livre [complet] [2].", "suggestions": []})
    assert search_answer.parse_answer(raw, 2)[0].startswith("Le Festival dernier cri [1]")
    note = search_answer.writer_question("danse le 14", ("danse",), 1, widened=["pour « sam. 14 nov. »"],
                                         asked="sam. 14 nov. · Montpellier · danse")
    assert "(sam. 14 nov., Montpellier, danse)" in note and "·" not in note


@pytest.mark.parametrize(("question", "described"), [
    ("¿qué hacer en Montpellier este fin de semana?", "ce week-end · Montpellier et sa métropole"),
    ("what to do in Sete tonight", "aujourd'hui (le soir) · Sète"),
    ("un spectacle qui dure moins d'une heure pour les petits",
     "les 3 prochains mois · famille · spectacle ou théâtre"),
])
def test_foreign_and_duration_words_are_not_required(question: str, described: str) -> None:
    """« dure » trouvait « durable » (une conférence) ; « hacer », « semana » étaient exigés."""
    plan = plan_search(question, SearchFilters(), NOW)[0]
    assert plan.describe() == described and not [word for word in plan.keywords if word != "spectacle"]


def test_a_title_with_an_exclamation_mark_is_not_cut() -> None:
    """« … ou l'exposition » restait seul quand la suite, après le « ! » d'un titre, était retirée."""
    cards = [{"ref": 1, "title": "Les Arceaux : toute une histoire !", "city": "Montpellier", "summary": ""}]
    with pytest.raises(search_answer.RefusedAnswer):
        search_answer.check_facts("Voici l'exposition Les Arceaux : toute une histoire ! [1] accessible en tram.",
                                  cards)
    assert search_answer.sentences("Voir Pop ! [1] puis rentrer. Fin.") == ["Voir Pop ! [1] puis rentrer.", "Fin."]
    # Titre déjà écrit avant sa citation, espace insécable compris : pas répété
    answer = "Idées : Les Arceaux : toute une histoire\u00a0! [1]"
    assert search_answer.name_citations(answer, cards) == answer


def test_a_title_ending_with_punctuation_is_not_named_twice() -> None:
    cards = [{"ref": 1, "title": "Folle de rage X rockstore - Le legendaire aftershow !"}]
    answer = "Voici un concert : Folle de rage X rockstore - Le legendaire aftershow ! [1]."
    assert search_answer.name_citations(answer, cards) == answer


# ----------------------------------------------------------------------------
# Titres : noms propres et parties en capitales
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(("title", "city", "context", "expected"), [
    ("MICHEL KELEMENIS", "Montpellier", "Le chorégraphe Michel Kelemenis présente sa création.",
     "Michel Kelemenis"),
    ("OL LYONNES - MHSC", "Montpellier", "Le match oppose l'OL Lyonnes au Montpellier HSC.", "OL Lyonnes - MHSC"),
    ("RACONTE-MOI UNE HISTOIRE : 25 novembre", "Frontignan", "", "Raconte-moi une histoire : 25 novembre"),
    ("PAUSE JEUX : Les échecs", "Balaruc-les-Bains", "", "Pause jeux : Les échecs"),
    ("SAINT-JEAN - FEU DE JOIE", "Lattes", "", "Saint-Jean - Feu de joie"),
    ("Balade — Ève laroche-Joubert", "Vic-la-Gardiole", "Une balade avec Ève Laroche-Joubert.",
     "Balade — Ève Laroche-Joubert"),
])
def test_titles_keep_proper_nouns(title: str, city: str, context: str, expected: str) -> None:
    assert search_cards.readable_title(title, city, context) == expected
