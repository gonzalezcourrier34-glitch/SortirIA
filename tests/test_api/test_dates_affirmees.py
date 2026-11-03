"""Jours affirmés par la réponse, contrôlés contre les cartes citées (03/11/2026).

**Provenance** : nouveau fichier écrit pour SortirIA (03/11/2026), après les
240 questions posées en production (perso/tests_production) : à « un concert
gratuit ce soir à Montpellier », la réponse disait « Archipol en concert en duo
à Sète ce soir [3] » pour un concert du sam. 5 déc. Mardi 3 novembre 2026, 19 h 30.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import search_answer
from app.query_understanding import PARIS

NOW = datetime(2026, 11, 3, 19, 30, tzinfo=PARIS)          # mardi soir ; samedi = le 7

CARDS = [
    {"ref": 1, "title": "Concert rock", "city": "Montpellier", "start_at": "2026-11-07T20:30:00+01:00",
     "end_at": None, "price": "12 à 18 €", "category": "concert"},
    {"ref": 2, "title": "Koa jazz festival", "city": "Montpellier", "start_at": "2026-10-20T00:00:00+02:00",
     "end_at": "2026-11-09T23:59:00+01:00", "price": "10 €", "category": "concert"},
    {"ref": 3, "title": "Archipol en concert en duo", "city": "Sète", "start_at": "2026-12-05T19:30:00+01:00",
     "end_at": None, "price": "Gratuit", "category": "concert"},
    {"ref": 4, "title": "Castelnau-le-Lez d'hier et d'aujourd'hui", "city": "Castelnau-le-Lez",
     "start_at": "2026-11-14T10:00:00+01:00", "end_at": None, "category": "visite"},
    {"ref": 5, "title": "Mini-ciné-minot", "city": "Sète", "start_at": "2026-11-04T10:30:00+01:00",
     "end_at": None, "category": "cinema", "other_dates": [{"event_id": "x", "date": "Mer. 9 déc."}]},
]


def test_a_day_on_which_no_cited_event_happens_is_removed() -> None:
    """La phrase de la réponse réelle (#138) est retirée ; il ne reste aucune citation : réponse simple."""
    answer = ("Aucun concert gratuit ce soir à Montpellier n'est disponible dans la liste. Voici des idées "
              "voisines : [3] Archipol en concert en duo à Sète ce soir, gratuit, mais en soirée ; [1] Concert "
              "rock à Montpellier le 7 nov. à 20h30.")
    with pytest.raises(search_answer.RefusedAnswer):
        search_answer.check_facts(answer, CARDS, request="un concert gratuit ce soir à Montpellier", now=NOW)
    # Une autre phrase citée reste : seule la phrase fausse part
    assert search_answer.check_facts("Ce soir, Archipol en concert en duo [3]. Samedi, Concert rock [1].",
                                     CARDS, now=NOW) == "Samedi, Concert rock [1]."


def test_a_day_supported_by_a_cited_event_is_kept() -> None:
    kept = [
        "Samedi, profitez du Concert rock [1].",
        "Samedi 5 décembre, Archipol en concert en duo [3].",
        "Ce soir, le Koa jazz festival [2] continue à Montpellier.",              # en cours jusqu'au 9
        "Ce week-end, Concert rock [1] ou Koa jazz festival [2].",
        "Ce soir, Koa jazz festival [2] ; sinon Archipol en concert en duo [3] le 5 décembre.",
    ]
    for answer in kept:
        assert search_answer.check_facts(answer, CARDS, now=NOW) == answer


def test_what_is_not_checked() -> None:
    """Titre qui contient un jour, absence dite, date seule, autres dates, pas d'heure de référence."""
    unchanged = [
        "Pour une visite historique, Castelnau-le-Lez d'hier et d'aujourd'hui [4] le 14 novembre.",
        "Je n'ai rien trouvé ce soir, mais Concert rock [1] a lieu samedi.",
        "Archipol en concert en duo [3] le 9 décembre.",
        "Demain, Mini-ciné-minot [5] à Sète.",                                   # d'autres dates : non contredit
    ]
    for answer in unchanged:
        assert search_answer.check_facts(answer, CARDS, now=NOW) == answer
    # Sans heure de référence (appels existants), les dates ne sont pas contrôlées
    assert search_answer.check_facts("Ce soir, Archipol en concert en duo [3].", CARDS) == \
        "Ce soir, Archipol en concert en duo [3]."


def test_card_days_reads_paris_days() -> None:
    assert search_answer.card_days(CARDS[1]) == (datetime(2026, 10, 20).date(), datetime(2026, 11, 9).date())
    assert search_answer.card_days(CARDS[4]) is None
    assert search_answer.card_days({"ref": 9, "start_at": "pas une date"}) is None
