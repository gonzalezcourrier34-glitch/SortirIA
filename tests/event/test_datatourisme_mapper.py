"""Tests de la conversion DATAtourisme -> Event.

L'objet de test reprend la structure d'une vraie réponse de l'API
(exploration du 14/10/2026, séance de cinéma à Palavas-les-Flots).
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

from src.event.datatourisme_mapper import (
    get_category,
    get_occurrences,
    get_price,
    map_datatourisme_occurrences,
    parse_opening_times,
    pick_text,
    single_time,
)
from src.event.event_validator import validate_event

DETAILS = (
    "Mercredi 14 octobre 2030 de 19h30 à 21h10.\n\n"
    "Dimanche 18 octobre 2030 de 19h30 à 21h10.\n\n"
    "Vendredi 30 octobre 2030 de 17h30 à 19h10."
)


def make_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "uuid": "012283d9-0a98-30a2-a1d5-a13598a6ad31",
        "uri": "https://data.datatourisme.fr/32/d2bc3ab2",
        "label": {"@fr": "BILLY MELODY"},
        "type": ["Event", "PointOfInterest", "EntertainmentAndEvent"],
        "hasBeenCreatedBy": {"legalName": "34 - ADT34"},
        "lastUpdate": "2026-10-06",
        "isLocatedAt": [{
            "geo": {"latitude": 43.528737, "longitude": 3.933426},
            "address": [{
                "hasAddressCity": {"insee": "34192", "label": {"@fr": "Palavas-les-Flots"}},
                "streetAddress": ["16 Boulevard Maréchal Joffre"],
                "postalCode": "34250",
                "addressLocality": "Palavas-les-Flots",
            }],
        }],
        "hasDescription": [{
            "description": {"@fr": "Suite à la perte de son père, Billie déménage."},
            "shortDescription": {"@fr": "Séance cinéma (Durée : 1h40)"},
        }],
        "hasContact": [{"legalName": "BILLY MELODY", "homepage": ["https://www.cineplan.fr/"]}],
        "offers": [{"priceSpecification": [
            {"priceCurrency": "EUR", "minPrice": [5], "maxPrice": [5]},
            {"priceCurrency": "EUR", "minPrice": [6], "maxPrice": [6]},
        ]}],
        "hasTheme": [{"label": {"@fr": "Cinéma"}, "key": "Cinema"}],
        "takesPlaceAt": [
            {"openingDetails": {"@fr": DETAILS}, "startDate": "2030-10-14", "endDate": "2030-10-14"},
            {"openingDetails": {"@fr": DETAILS}, "startDate": "2030-10-18", "endDate": "2030-10-18"},
            {"openingDetails": {"@fr": DETAILS}, "startDate": "2030-10-30", "endDate": "2030-10-30"},
        ],
    }
    item.update(overrides)
    return item


def test_pick_text_handles_at_language_keys_and_lists() -> None:
    assert pick_text({"@fr": "Bonjour", "@en": "Hello"}) == "Bonjour"
    assert pick_text({"@en": "Hello"}) == "Hello"
    assert pick_text([{"@fr": ["Texte"]}]) == "Texte"


def test_parse_opening_times_by_day() -> None:
    times = parse_opening_times(DETAILS)
    assert times[date(2030, 10, 14)] == (time(19, 30), time(21, 10))
    assert times[date(2030, 10, 30)] == (time(17, 30), time(19, 10))
    assert parse_opening_times("Le 1er novembre 2030 à 20h.") == {date(2030, 11, 1): (time(20, 0), None)}


def test_single_time_only_when_unambiguous() -> None:
    assert single_time("Tous les jours de 9h à 12h30") == (time(9, 0), time(12, 30))
    assert single_time("De 9h à 12h puis de 14h à 18h") is None
    assert single_time("Horaires sur le site") is None


def test_one_event_per_session_with_its_time() -> None:
    events = map_datatourisme_occurrences(make_item())
    assert [event.start_at for event in events] == [
        "2030-10-14T19:30:00+02:00",
        "2030-10-18T19:30:00+02:00",
        "2030-10-30T17:30:00+01:00",   # après le passage à l'heure d'hiver
    ]
    assert all(event.time_known for event in events)
    assert len({event.event_id for event in events}) == 3


def test_mapped_fields() -> None:
    event = map_datatourisme_occurrences(make_item())[0]
    assert (event.title, event.category, event.city, event.postal_code) == ("BILLY MELODY", "Cinéma", "Palavas-les-Flots", "34250")
    assert (event.price_min, event.price_max, event.is_free, event.price_text) == (5.0, 6.0, False, "de 5 à 6 €")
    assert event.url == "https://www.cineplan.fr/"
    assert event.venue_name == ""            # le contact porte le nom du film, pas du lieu
    assert (event.source_credit, event.source_updated_at) == ("34 - ADT34", "2026-10-06")
    assert event.source == "datatourisme" and event.source_event_id.endswith("@2030-10-14")
    assert validate_event(event) == (True, "")


def test_free_event_and_missing_price() -> None:
    free = make_item(offers=[{"priceSpecification": [{"minPrice": [0], "maxPrice": [0]}]}])
    assert get_price(free) == ("Gratuit", 0.0, 0.0, True)
    assert get_price(make_item(offers=[])) == ("", None, None, None)


def test_long_exhibition_without_time() -> None:
    item = make_item(takesPlaceAt=[{"startDate": "2030-09-01", "endDate": "2031-01-15"}], hasTheme=[],
                     type=["Event", "ExhibitionEvent"])
    (occurrence,) = get_occurrences(item)
    assert occurrence == {"start_at": "2030-09-01T00:00:00+02:00", "end_at": "2031-01-15T00:00:00+01:00", "time_known": False}
    assert get_category(item) == "Exposition"


def test_family_word_in_description_does_not_make_a_family_event() -> None:
    item = make_item(hasDescription=[{"description": {"@fr": "Un drame familial."}, "shortDescription": {"@fr": "drame familial"}}])
    assert map_datatourisme_occurrences(item)[0].audiences == []


def test_url_falls_back_to_datatourisme_record() -> None:
    event = map_datatourisme_occurrences(make_item(hasContact=[]))[0]
    assert event.url == "https://data.datatourisme.fr/32/d2bc3ab2"
