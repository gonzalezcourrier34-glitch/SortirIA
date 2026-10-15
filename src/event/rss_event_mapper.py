"""Conversion d'une entrée de flux RSS d'agenda en `Event`.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Le moteur RSS
de CheckIt.AI lit des flux de presse : une entrée = un article daté de sa
publication. Un flux d'AGENDA porte en plus la date de l'événement, de deux
façons selon les sites :

- le module RSS « événements » (`ev:startdate`, `ev:enddate`, `ev:location`),
  que feedparser rend sous `ev_startdate`, `ev_enddate`, `ev_location` ;
- la date de publication elle-même : beaucoup d'agendas (extension WordPress
  « The Events Calendar », Démosphère avec l'option prévue) mettent la date de
  l'événement dans `pubDate`. Il faut alors le dire dans la configuration
  (`event_date: "published"`), sinon ce serait la date de mise en ligne.

Le lieu et la commune viennent de l'entrée quand elle les donne, sinon de la
configuration de la source (un flux = souvent un seul lieu).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time
from email.utils import parsedate_to_datetime
from typing import Any

from src.event.event_normalizer import (
    PARIS,
    build_dedup_key,
    build_event_id,
    clean_text,
    normalize_city,
    parse_event_datetime,
    to_iso,
)
from src.event.event_schema import Event
from src.event.openagenda_mapper import infer_audiences

REASON_NO_TITLE = "titre_absent"
REASON_NO_EVENT_DATE = "date_evenement_absente"
REASON_NO_CITY = "commune_absente"

TAG_PATTERN = re.compile(r"<[^>]+>")


def strip_html(value: Any) -> str:
    """Texte brut d'un champ HTML de flux (description, résumé)."""

    return clean_text(TAG_PATTERN.sub(" ", str(value or "")))


def parse_feed_datetime(value: Any) -> datetime | None:
    """Date d'un flux : ISO 8601 (`ev:startdate`) ou RFC 822 (`pubDate`)."""

    text = clean_text(value)
    if not text:
        return None
    parsed = parse_event_datetime(text)
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PARIS)
    return parsed.astimezone(PARIS)


def get_event_dates(entry: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[datetime | None, datetime | None, bool]:
    """(début, fin, heure connue) d'une entrée, selon la règle de la source."""

    if entry.get("ev_startdate"):
        start = parse_feed_datetime(entry.get("ev_startdate"))
        end = parse_feed_datetime(entry.get("ev_enddate"))
        time_known = "T" in str(entry.get("ev_startdate")) or ":" in str(entry.get("ev_startdate"))
        return start, end, time_known
    if source.get("event_date") == "published":
        start = parse_feed_datetime(entry.get("published") or entry.get("updated"))
        return start, None, bool(start and start.time() != time.min)
    return None, None, False


def map_rss_event(
    entry: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    today: date,
    horizon: date,
    extracted_at: str = "",
) -> tuple[Event | None, str]:
    """Transforme une entrée en `Event`. Renvoie (événement, "") ou (None, motif).

    Une entrée hors de la fenêtre (passée, ou trop lointaine) renvoie
    (None, "") : elle est ignorée sans être comptée comme une erreur.
    """

    title = strip_html(entry.get("title"))
    if not title:
        return None, REASON_NO_TITLE
    start, end, time_known = get_event_dates(entry, source)
    if start is None:
        return None, REASON_NO_EVENT_DATE
    if (end or start).date() < today or start.date() > horizon:
        return None, ""
    venue = strip_html(entry.get("ev_location")) or clean_text(source.get("venue_name"))
    city = normalize_city(source.get("city"))
    if not city:
        return None, REASON_NO_CITY
    if not time_known:
        start = datetime.combine(start.date(), time.min, tzinfo=PARIS)

    description = strip_html(entry.get("summary") or entry.get("description"))[:1000]
    tags = [clean_text(tag.get("term")) for tag in entry.get("tags") or [] if isinstance(tag, Mapping)]
    category = next((tag for tag in tags if tag), clean_text(source.get("category")))
    start_at = to_iso(start)
    link = clean_text(entry.get("link")) or clean_text(source.get("website"))
    return Event(
        event_id=build_event_id(build_dedup_key(title, start_at, venue or city)),
        source="rss",
        source_event_id=f"{source.get('source_id', '')}:{clean_text(entry.get('id')) or link or title}",
        title=title,
        description=description,
        category=category,
        keywords=[tag for tag in tags if tag],
        start_at=start_at,
        end_at=to_iso(end) if end and end > start else "",
        time_known=time_known,
        venue_name=venue,
        city=city,
        latitude=source.get("latitude"),
        longitude=source.get("longitude"),
        audiences=infer_audiences([title, category, description], start_at, time_known, None, None),
        url=link,
        extracted_at=extracted_at,
        source_credit=clean_text(source.get("credit") or source.get("name")),
    ), ""
