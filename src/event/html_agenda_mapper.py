"""Lecture d'une page d'agenda HTML (sélecteurs CSS), puis conversion en `Event`.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), pour le
moteur scraper de CheckIt.AI. Les sélecteurs viennent de la configuration
(`selectors` dans `config/sources.yaml`) : brancher un autre site autorisé ne
demande pas de code, seulement ses sélecteurs.

Écrit sur la page d'agenda de frontignan.fr (enregistrée le 14/10/2026) : chaque
événement est un `<article>` dont les dates sont balisées au format
schema.org (`itemprop="startDate"`, `content="2026-10-12T00:00:00+02:00"`).
La liste suffit : pas besoin d'ouvrir la page de chaque événement, ce qui
divise par dix le nombre de requêtes envoyées au site.

Seules les informations pratiques sont reprises (titre, dates, thème, court
résumé, lien) ; jamais les images.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

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
REASON_NO_DATE = "date_absente"
REASON_NO_LINK = "lien_absent"

# « 📍 O'studio, 37 route de … » : lieu annoncé dans le résumé.
VENUE_PATTERN = re.compile(r"📍\s*([^📅🗓️➡️\n]{3,80})")
READ_MORE_PATTERN = re.compile(r"\s*(…|\.\.\.)?\s*Lire la suite.*$", re.IGNORECASE)
# Symboles en tête de titre (« 🎗Octobre rose ») : retirés.
LEADING_SYMBOLS_PATTERN = re.compile(r"^[^\w«\"(]+")
# Date ISO : le jour (10 premiers caractères) et l'heure éventuelle.
ISO_DAY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})(T(\d{2}):(\d{2}))?")


def parse_card_date(value: Any, day_only: bool = False) -> tuple[datetime | None, bool]:
    """(date, heure connue ?) d'une fiche.

    Minuit vaut « jour seulement ». Avec `day_only` (option de la source),
    l'heure est toujours ignorée : sur frontignan.fr, la liste affiche « 06:00 »
    par défaut pour les événements sur plusieurs jours (vérifié le 14/10/2026 :
    une exposition n'ouvre pas à 6 h). Le jour est lu tel qu'écrit, sans
    conversion de fuseau.
    """

    text = clean_text(value)
    match = ISO_DAY_PATTERN.match(text)
    if match and (day_only or not match.group(2) or match.group(3, 4) == ("00", "00")):
        return datetime.combine(date.fromisoformat(match.group(1)), time.min, tzinfo=PARIS), False
    parsed = parse_event_datetime(text)
    return parsed, parsed is not None


def select_text(node: Tag, selector: str | None) -> str:
    """Texte du premier élément correspondant (vide si pas de sélecteur)."""

    if not selector:
        return ""
    found = node.select_one(selector)
    return clean_text(found.get_text(" ", strip=True)) if found else ""


def select_date(node: Tag, selector: str | None) -> str:
    """Date d'un élément : attribut `content` ou `datetime` (schema.org, <time>), sinon son texte."""

    if not selector:
        return ""
    found = node.select_one(selector)
    if not found:
        return ""
    return clean_text(found.get("content") or found.get("datetime") or found.get_text(" ", strip=True))


def parse_agenda_page(html: str, selectors: Mapping[str, Any], page_url: str = "") -> list[dict[str, str]]:
    """Fiches brutes d'une page d'agenda : une par élément `selectors.article`."""

    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for node in soup.select(str(selectors.get("article") or "article")):
        link_node = node.select_one(str(selectors.get("link") or "a[href]"))
        summary = select_text(node, selectors.get("text"))
        cards.append({
            "title": select_text(node, selectors.get("title")),
            "url": urljoin(page_url, link_node.get("href", "")) if link_node else "",
            "start": select_date(node, selectors.get("start_date")),
            "end": select_date(node, selectors.get("end_date")),
            "category": select_text(node, selectors.get("category")),
            "summary": READ_MORE_PATTERN.sub("", summary),
            "venue": select_text(node, selectors.get("venue")),
            "page_url": page_url,
        })
    return cards


def map_agenda_card(
    card: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    today: date,
    horizon: date,
    extracted_at: str = "",
) -> tuple[Event | None, str]:
    """Fiche -> (Event, "") ou (None, motif) ; hors fenêtre -> (None, "")."""

    title = LEADING_SYMBOLS_PATTERN.sub("", clean_text(card.get("title"))).strip()
    if not title:
        return None, REASON_NO_TITLE
    url = clean_text(card.get("url"))
    if not url:
        return None, REASON_NO_LINK
    day_only = bool(source.get("day_only"))
    start, time_known = parse_card_date(card.get("start"), day_only)
    if start is None:
        return None, REASON_NO_DATE
    end, _ = parse_card_date(card.get("end"), day_only)
    if (end or start).date() < today or start.date() > horizon:
        return None, ""

    summary = clean_text(card.get("summary"))
    match = VENUE_PATTERN.search(summary)
    # Le résumé du site est coupé (« … ») : on ne garde que le nom du lieu,
    # avant la première virgule (« O'studio, 37, route de » -> « O'studio »).
    venue = clean_text(card.get("venue")) or (clean_text(match.group(1)).split(",")[0].strip(" .") if match else "")
    city = normalize_city(source.get("city"))
    category = clean_text(card.get("category"))
    start_at = to_iso(start)
    return Event(
        event_id=build_event_id(build_dedup_key(title, start_at, venue or city)),
        source="scraper",
        source_event_id=f"{source.get('source_id', '')}:{url}",
        title=title,
        description=summary[:1000],
        category=category,
        keywords=[category] if category else [],
        start_at=start_at,
        end_at=to_iso(end) if end and end > start else "",
        time_known=time_known,
        venue_name=venue,
        city=city,
        latitude=source.get("latitude"),
        longitude=source.get("longitude"),
        audiences=infer_audiences([title, category, summary], start_at, time_known, None, None),
        url=url,
        extracted_at=extracted_at,
        source_credit=clean_text(source.get("credit") or source.get("name")),
    ), ""
