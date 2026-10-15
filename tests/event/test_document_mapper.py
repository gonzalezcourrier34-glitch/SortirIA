"""Tests de la conversion « réponse de Mistral -> Event » et des garde-fous."""

from __future__ import annotations

from datetime import date, time

import pytest

from src.event.document_mapper import (
    REASON_INVALID_FIELDS,
    DocumentEvent,
    in_period,
    map_document_event,
    parse_llm_response,
    title_found_in_text,
    validate_llm_event,
)
from src.event.event_validator import validate_event

SOURCE = {"source_id": "agglopole", "name": "Agenda Culture", "credit": "Sète agglopôle, Agenda Culture",
          "published_on": "2026-08-01", "city": "Sète"}
PAGE_TEXT = """20 000 RÔLES SOUS LES EAUX
Musée Étang de Thau Louis Higounet, Bouzigues
Samedi 7 novembre, séances à 18h30 et 21h30. À partir de 12 ans.
2 € jusqu'à 15 ans, 4 € à partir de 16 ans.
O n les appelait G alantes"""


def test_parse_llm_response_accepts_code_fences() -> None:
    answer = '```json\n{"evenements": [{"titre": "A"}, "pas un objet"]}\n```'
    assert parse_llm_response(answer) == [{"titre": "A"}]
    with pytest.raises(ValueError):
        parse_llm_response("Désolé, je ne peux pas.")


def test_validation_rejects_bad_fields() -> None:
    assert validate_llm_event({"titre": "Concert", "date_debut": "7 novembre"}) == (None, REASON_INVALID_FIELDS)
    assert validate_llm_event({"titre": "", "date_debut": "2026-11-07"}) == (None, REASON_INVALID_FIELDS)
    event, reason = validate_llm_event({"titre": "Concert", "date_debut": "2026-11-07", "heure_debut": "18:30", "age_min": 500})
    assert reason == "" and event.heure_debut == time(18, 30) and event.age_min is None


@pytest.mark.parametrize(("title", "expected"), [
    ("20 000 rôles sous les eaux", True),
    ("On les appelait galantes", True),          # lettres espacées dans le PDF
    ("Grand concert de Noël", False),            # inventé
])
def test_title_must_be_in_text(title, expected) -> None:
    assert title_found_in_text(title, PAGE_TEXT) is expected


def test_period() -> None:
    period = (date(2026, 9, 1), date(2026, 12, 31))
    assert in_period(DocumentEvent(titre="Expo", date_debut="2026-08-15", date_fin="2026-09-30"), period)
    assert not in_period(DocumentEvent(titre="Concert", date_debut="2025-11-07"), period)  # mauvaise année


def test_mapping() -> None:
    raw = {"titre": "20 000 RÔLES SOUS LES EAUX", "date_debut": "2026-11-07", "heure_debut": "18:30",
           "heure_fin": "21:00", "lieu": "Musée Étang de Thau", "commune": "BOUZIGUES",
           "prix": "2 € jusqu'à 15 ans, 4 € à partir de 16 ans", "public": "À partir de 12 ans",
           "age_min": 12, "categorie": "Jeu de rôle"}
    event = map_document_event(DocumentEvent.model_validate(raw), SOURCE, page=21,
                               document_url="https://agglopole.fr/agenda.pdf")
    assert (event.start_at, event.end_at) == ("2026-11-07T18:30:00+01:00", "2026-11-07T21:00:00+01:00")
    assert (event.city, event.venue_name, event.price_min, event.price_max) == ("Bouzigues", "Musée Étang de Thau", 2.0, 4.0)
    assert event.url == "https://agglopole.fr/agenda.pdf#page=21"
    assert (event.source, event.source_credit, event.age_min) == ("document", "Sète agglopôle, Agenda Culture", 12)
    assert "famille" not in event.audiences   # à partir de 12 ans, pas « jeunes enfants »
    assert validate_event(event, now=__import__("datetime").datetime(2026, 10, 14, tzinfo=__import__("src.event.event_normalizer", fromlist=["PARIS"]).PARIS)) == (True, "")


def test_free_flag_and_booking_link() -> None:
    raw = {"titre": "Chaussons !", "date_debut": "2026-12-05", "gratuit": True, "age_max": 6,
           "lien_reservation": "https://billetterie.example/chaussons"}
    event = map_document_event(DocumentEvent.model_validate(raw), SOURCE, page=40, document_url="https://x.fr/a.pdf")
    assert (event.is_free, event.price_min, event.url) == (True, 0.0, "https://billetterie.example/chaussons")
    assert event.audiences == ["famille"] and event.time_known is False and event.city == "Sète"


def test_registration_and_city_from_venue() -> None:
    raw = {"titre": "Bricoludo", "date_debut": "2026-10-20", "lieu": "MÉDIATHÈQUE MONTAIGNE", "sur_inscription": True,
           "description": "Fabrique ton mémory."}
    source = {**SOURCE, "venues": {"Médiathèque Montaigne": "Frontignan"}}
    event = map_document_event(DocumentEvent.model_validate(raw), source, page=29, document_url="https://x.fr/a.pdf")
    assert (event.city, event.description) == ("Frontignan", "Fabrique ton mémory. Sur inscription.")


def test_two_events_on_the_same_page_and_day_keep_distinct_identifiers() -> None:
    """Avant le 15/10/2026 : même identifiant (source@page@jour), le second passait pour un doublon."""

    base = {"date_debut": "2026-11-07", "heure_debut": "18:30", "commune": "SÈTE"}
    jazz = DocumentEvent.model_validate({**base, "titre": "Concert de jazz", "lieu": "Théâtre Molière"})
    visite = DocumentEvent.model_validate({**base, "titre": "Visite guidée", "lieu": "Musée Paul-Valéry"})
    first = map_document_event(jazz, SOURCE, page=12, document_url="https://agglopole.fr/agenda.pdf")
    second = map_document_event(visite, SOURCE, page=12, document_url="https://agglopole.fr/agenda.pdf")
    assert first.source_event_id != second.source_event_id
    assert first.source_event_id.startswith("agglopole@p12@2026-11-07@")
    # Le même événement relu dans le paquet suivant (recouvrement) garde le même identifiant.
    again = map_document_event(jazz, SOURCE, page=12, document_url="https://agglopole.fr/agenda.pdf")
    assert again.source_event_id == first.source_event_id


def test_several_days_with_start_time_stay_visible_until_the_end_of_the_last_day() -> None:
    raw = {"titre": "Stage de cirque", "date_debut": "2026-10-20", "date_fin": "2026-10-22", "heure_debut": "10:00",
           "lieu": "Chapiteau", "commune": "Frontignan"}
    event = map_document_event(DocumentEvent.model_validate(raw), SOURCE, page=3, document_url="https://x.fr/a.pdf")
    assert (event.time_known, event.end_at) == (True, "2026-10-22T23:59:59+02:00")


# --- Séances listées avec la fin de la période (Agenda de Sète, 15/10/2026) ---

def test_listed_sessions_lose_the_period_end_date():
    from datetime import date

    from src.event.document_mapper import DocumentEvent, fix_listed_sessions

    def session(day, end=date(2026, 12, 31), title="PAUSE JEUX", place="Médiathèque La Fabrique"):
        return DocumentEvent(titre=title, date_debut=day, date_fin=end, lieu=place)

    events = [session(date(2026, 9, 3)), session(date(2026, 9, 5)), session(date(2026, 10, 14)),
              session(date(2026, 12, 1), title="Exposition", end=date(2026, 12, 31)),
              session(date(2026, 10, 20), title="Stage", end=date(2026, 10, 22)),
              session(date(2026, 10, 27), title="Stage", end=date(2026, 10, 29))]
    fixed = fix_listed_sessions(events)

    assert [e.date_fin for e in fixed[:3]] == [None, None, None]       # séances : un jour chacune
    assert fixed[3].date_fin == date(2026, 12, 31)                       # exposition seule : inchangée
    assert [e.date_fin for e in fixed[4:]] == [date(2026, 10, 22), date(2026, 10, 29)]  # stages sans chevauchement
