"""Tests du lecteur iCal et de la conversion en Event."""

from __future__ import annotations

from datetime import date, datetime

from src.event.event_normalizer import PARIS
from src.event.event_validator import REASON_CANCELLED, validate_event
from src.event.ical_mapper import (
    expand_occurrences,
    get_occurrences,
    map_ical_event,
    parse_calendar,
    parse_ical_datetime,
    unescape,
    unfold_lines,
)

TODAY = date(2030, 10, 14)  # un lundi

CALENDAR = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:concert-1@le-lieu\r\n"
    "SUMMARY:Concert jazz\\, quartet\r\n"
    "DTSTART;TZID=Europe/Paris:20301018T203000\r\n"
    "DTEND;TZID=Europe/Paris:20301018T230000\r\n"
    "LOCATION:Le Lieu\\, 3 rue des Arts\r\n"
    "DESCRIPTION:Une soirée avec le quartet.\\nEntrée 12 €.\r\n"
    "  Bar sur place.\r\n"
    "CATEGORIES:Concert,Jazz\r\n"
    "URL:https://le-lieu.fr/concert-1\r\n"
    "BEGIN:VALARM\r\n"
    "SUMMARY:Rappel qui ne doit pas écraser le titre\r\n"
    "END:VALARM\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:atelier@le-lieu\r\n"
    "SUMMARY:Atelier enfants\r\n"
    "DTSTART;TZID=Europe/Paris:20301015T100000\r\n"
    "DTEND;TZID=Europe/Paris:20301015T113000\r\n"
    "RRULE:FREQ=WEEKLY;BYDAY=TU,SA;COUNT=6\r\n"
    "EXDATE;TZID=Europe/Paris:20301019T100000\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:annule@le-lieu\r\n"
    "SUMMARY:Spectacle annulé\r\n"
    "DTSTART:20301020T190000Z\r\n"
    "STATUS:CANCELLED\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY:Marché de nuit\r\n"
    "DTSTART;VALUE=DATE:20301025\r\n"
    "DTEND;VALUE=DATE:20301026\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

SOURCE = {
    "name": "Le Lieu", "source_id": "le_lieu", "venue_name": "Le Lieu", "city": "SÈTE",
    "postal_code": "34200", "latitude": 43.40, "longitude": 3.69, "website": "https://le-lieu.fr",
}


def test_unfold_and_unescape() -> None:
    assert unfold_lines("A:un\r\n  deux\r\nB:trois") == ["A:un deux", "B:trois"]
    assert unescape(r"a\, b\; c\nd\\e") == "a, b; c\nd\\e"


def test_parse_calendar_ignores_nested_blocks() -> None:
    events = parse_calendar(CALENDAR)
    assert len(events) == 4
    assert events[0]["SUMMARY"][1] == "Concert jazz\\, quartet"
    assert "Bar sur place" in events[0]["DESCRIPTION"][1]


def test_dates_in_all_forms() -> None:
    assert parse_ical_datetime({"TZID": "Europe/Paris"}, "20301018T203000")[0] == datetime(2030, 10, 18, 20, 30, tzinfo=PARIS)
    utc, known = parse_ical_datetime({}, "20301020T190000Z")
    assert (utc.hour, known) == (21, True)        # 19 h UTC = 21 h à Paris (heure d'été)
    day, known = parse_ical_datetime({"VALUE": "DATE"}, "20301025")
    assert (day.date(), known) == (date(2030, 10, 25), False)
    assert parse_ical_datetime({}, "pas une date") == (None, False)


def test_weekly_rule_with_days_count_and_exdate() -> None:
    atelier = parse_calendar(CALENDAR)[1]
    starts = [occ["start_at"][:16] for occ in get_occurrences(atelier, TODAY, 30)]
    # COUNT=6 : mar 15, sam 19 (retiré par EXDATE), mar 22, sam 26, mar 29, sam 2 nov.
    assert starts == ["2030-10-15T10:00", "2030-10-22T10:00", "2030-10-26T10:00", "2030-10-29T10:00", "2030-11-02T10:00"]


def test_daily_rule_stops_at_window_end() -> None:
    start = datetime(2030, 10, 14, 9, 0, tzinfo=PARIS)
    days = expand_occurrences(start, "FREQ=DAILY", [], datetime(2030, 10, 17, tzinfo=PARIS))
    assert len(days) == 3


def test_unsupported_rule_gives_only_first_date() -> None:
    start = datetime(2030, 10, 14, 9, 0, tzinfo=PARIS)
    assert expand_occurrences(start, "FREQ=MONTHLY;BYDAY=1MO", [], datetime(2031, 1, 1, tzinfo=PARIS)) == [start]


def test_concert_is_mapped() -> None:
    concert = parse_calendar(CALENDAR)[0]
    (occurrence,) = get_occurrences(concert, TODAY, 30)
    event = map_ical_event(concert, occurrence, SOURCE)
    assert event.title == "Concert jazz, quartet"
    assert (event.venue_name, event.address, event.city) == ("Le Lieu", "3 rue des Arts", "Sète")
    assert (event.start_at, event.end_at) == ("2030-10-18T20:30:00+02:00", "2030-10-18T23:00:00+02:00")
    assert event.category == "Concert" and event.keywords == ["Concert", "Jazz"]
    assert event.url == "https://le-lieu.fr/concert-1"
    assert event.price_min == 12.0
    assert event.source == "ical" and event.source_credit == "Le Lieu"
    assert validate_event(event, now=datetime(2030, 10, 14, tzinfo=PARIS)) == (True, "")


def test_cancelled_event_is_rejected_by_validation() -> None:
    cancelled = parse_calendar(CALENDAR)[2]
    (occurrence,) = get_occurrences(cancelled, TODAY, 30)
    event = map_ical_event(cancelled, occurrence, SOURCE)
    assert validate_event(event, now=datetime(2030, 10, 14, tzinfo=PARIS)) == (False, REASON_CANCELLED)


def test_all_day_event_and_source_defaults() -> None:
    market = parse_calendar(CALENDAR)[3]
    (occurrence,) = get_occurrences(market, TODAY, 30)
    assert occurrence == {"start_at": "2030-10-25T00:00:00+02:00", "end_at": "", "time_known": False}
    event = map_ical_event(market, occurrence, SOURCE)
    assert (event.venue_name, event.url, event.latitude) == ("Le Lieu", "https://le-lieu.fr", 43.40)
    # « nuit » n'est pas un mot « soirée » (« Nuit des musées »), et l'heure est inconnue.
    assert event.audiences == []


def test_events_outside_window_are_dropped() -> None:
    concert = parse_calendar(CALENDAR)[0]
    assert get_occurrences(concert, date(2030, 10, 19), 30) == []   # déjà passé
    assert get_occurrences(concert, date(2030, 9, 1), 30) == []     # trop loin


def test_multi_day_all_day_event_ends_on_its_last_day() -> None:
    """DTEND est exclusif en iCal : un salon du 25 au 27 a DTEND = 28 ; il ne doit pas être visible le 28."""

    calendar = CALENDAR.replace("DTEND;VALUE=DATE:20301026", "DTEND;VALUE=DATE:20301028")
    salon = parse_calendar(calendar)[3]
    (occurrence,) = get_occurrences(salon, TODAY, 30)
    assert occurrence == {"start_at": "2030-10-25T00:00:00+02:00", "end_at": "2030-10-27T00:00:00+02:00",
                          "time_known": False}
