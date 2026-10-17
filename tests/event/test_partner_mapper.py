"""Tests de la lecture d'une ligne de fichier partenaire."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from src.event import partner_mapper as mapper
from src.event.event_normalizer import PARIS
from src.event.event_validator import validate_event

TODAY = date(2026, 10, 14)
PARTNER = {"file": "ludo", "name": "Ludothèque du Port", "sector": "enfants",
           "website": "https://ludo.example", "sponsored_until": "2026-12-31"}
ROW = {"titre": "Atelier jeux", "date": "21/11/2026", "heure_debut": "14h30", "heure_fin": "17:00",
       "lieu": "Ludothèque du Port", "commune": "sète", "prix": "3 €", "public": "famille",
       "age_min": "4", "age_max": "12 ans", "categorie": "Atelier"}


@pytest.mark.parametrize(("header", "expected"), [
    ("Heure début", "heure_debut"), ("Ville", "commune"), ("  Titre ", "titre"), ("Lien réservation", "lien"),
])
def test_headers_are_normalized(header, expected) -> None:
    assert mapper.normalize_header(header) == expected


@pytest.mark.parametrize("value", ["21/11/2026", "21-11-2026", "2026-11-21", "21/11/26", "2026-11-21 00:00:00",
                                   datetime(2026, 11, 21, 0, 0), date(2026, 11, 21)])  # noqa: DTZ001
def test_dates(value) -> None:
    assert mapper.parse_cell_date(value) == date(2026, 11, 21)


@pytest.mark.parametrize(("value", "expected"), [
    ("20:30", time(20, 30)), ("20h30", time(20, 30)), ("20h", time(20, 0)), ("9.15", time(9, 15)),
    (time(20, 30, 15), time(20, 30)), ("", None), (float("nan"), None),
])
def test_times(value, expected) -> None:
    assert mapper.parse_cell_time(value) == expected


def test_unreadable_time_raises() -> None:
    with pytest.raises(ValueError):
        mapper.parse_cell_time("le soir")


def test_full_row() -> None:
    event, reason = mapper.map_partner_row(ROW, PARTNER, today=TODAY)
    assert reason == ""
    assert (event.source, event.city, event.start_at, event.end_at) == (
        "partenaire", "Sète", "2026-11-21T14:30:00+01:00", "2026-11-21T17:00:00+01:00")
    assert (event.price_min, event.age_min, event.age_max, event.audiences) == (3.0, 4, 12, ["famille"])
    assert (event.url, event.source_credit, event.is_sponsored) == ("https://ludo.example", "Ludothèque du Port", True)
    assert validate_event(event, now=datetime(2026, 10, 14, tzinfo=PARIS)) == (True, "")


@pytest.mark.parametrize(("changes", "reason"), [
    ({"titre": ""}, mapper.REASON_MISSING_TITLE),
    ({"date": "samedi"}, mapper.REASON_BAD_DATE),
    ({"date_fin": "bientôt"}, mapper.REASON_BAD_DATE),
    ({"heure_debut": "le soir"}, mapper.REASON_BAD_TIME),
    ({"lieu": " "}, mapper.REASON_MISSING_VENUE),
    ({"commune": ""}, mapper.REASON_MISSING_CITY),
    ({"age_min": "12", "age_max": "4"}, mapper.REASON_BAD_AGES),
    ({"lien": "voir sur place"}, mapper.REASON_BAD_LINK),
])
def test_rejections_have_a_reason(changes, reason) -> None:
    assert mapper.map_partner_row({**ROW, **changes}, PARTNER, today=TODAY) == (None, reason)


def test_link_needed_when_partner_has_no_website() -> None:
    assert mapper.map_partner_row(ROW, {**PARTNER, "website": ""}, today=TODAY) == (None, mapper.REASON_MISSING_LINK)


def test_past_session_is_ignored_silently() -> None:
    assert mapper.map_partner_row({**ROW, "date": "01/10/2026"}, PARTNER, today=TODAY) == (None, "")


def test_night_free_cancelled_and_partnership_over() -> None:
    row = {**ROW, "titre": "Soirée DJ", "heure_debut": "23:00", "heure_fin": "05:00", "public": "soirée",
           "age_min": "18", "age_max": "", "gratuit": "oui", "statut": "Annulé"}
    event, _ = mapper.map_partner_row(row, {**PARTNER, "sponsored_until": "2026-10-01"}, today=TODAY)
    assert event.audiences == ["soiree"] and event.is_free is True and event.price_max == 0.0
    assert event.status == "annule" and event.is_sponsored is False
    assert event.end_at == ""   # fin après minuit sans date_fin : pas de fin inventée


def test_exhibition_over_several_days() -> None:
    event, _ = mapper.map_partner_row({**ROW, "date": "01/10/2026", "date_fin": "31/12/2026", "heure_debut": "",
                                       "heure_fin": ""}, PARTNER, today=TODAY)
    # Fin du dernier jour (pas minuit au début de ce jour) : l'exposition reste visible le 31.
    assert event.time_known is False and event.end_at == "2026-12-31T23:59:59+01:00"


def test_several_days_with_a_start_time_end_at_the_end_of_the_last_day() -> None:
    event, _ = mapper.map_partner_row({**ROW, "date": "20/10/2026", "date_fin": "22/10/2026", "heure_debut": "10:00",
                                       "heure_fin": ""}, PARTNER, today=TODAY)
    # Avant le 15/10/2026 : fin au 22/10 à 0 h, l'événement disparaissait de la vue le dernier jour.
    assert event.time_known is True and event.end_at == "2026-10-22T23:59:59+02:00"


def test_light_cleaning() -> None:
    row = {**ROW, "titre": "  SOIRÉE   SALSA ", "commune": "montpellier", "lien": "www.salsa.example/soiree"}
    event, _ = mapper.map_partner_row(row, PARTNER, today=TODAY)
    assert (event.title, event.city, event.url) == ("Soirée salsa", "Montpellier", "https://www.salsa.example/soiree")
    assert mapper.tidy_title("DJ") == "DJ"   # sigle court gardé


@pytest.mark.parametrize("public", ["tout public", "Tous publics", "tout âge"])
def test_all_audiences_includes_families(public) -> None:
    row = {**ROW, "titre": "Exposition photo", "public": public, "age_min": "", "age_max": ""}
    event, _ = mapper.map_partner_row(row, PARTNER, today=TODAY)
    assert "famille" in event.audiences


def test_all_audiences_for_adults_only_is_not_for_families() -> None:
    row = {**ROW, "titre": "Dégustation", "public": "tout public", "age_min": "18", "age_max": ""}
    event, _ = mapper.map_partner_row(row, PARTNER, today=TODAY)
    assert "famille" not in event.audiences


def test_partner_formula_and_sponsoring() -> None:
    """17/10/2026 : trois formules ; une date seule vaut « mis en avant » (ancienne configuration)."""

    assert mapper.partner_formula({"formule": "Premium"}) == "premium"
    assert mapper.partner_formula({"formule": "mis en avant"}) == "mis_en_avant"
    assert mapper.partner_formula({"sponsored_until": "2026-12-31"}) == "mis_en_avant"
    assert mapper.partner_formula({}) == "gratuit"
    assert mapper.partner_formula({"formule": "or"}) == "gratuit"
    assert not mapper.is_sponsored({"formule": "gratuit", "sponsored_until": "2026-12-31"}, TODAY)
    assert mapper.is_sponsored({"formule": "premium", "sponsored_until": "2026-12-31"}, TODAY)
    assert not mapper.is_sponsored({"formule": "premium", "sponsored_until": "2026-01-01"}, TODAY)
