"""Lecteur générique de calendriers iCal (.ics).

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), calqué sur
`src/extractors/rss/rss_extractor.py` de CheckIt.AI : un seul lecteur pour
tous les calendriers déclarés dans `ical_sources` (config/sources.yaml).

Brancher le calendrier d'un lieu partenaire ne demande donc **aucun code** :

    ical_sources:
      - name: "Le Jam"
        source_id: "le_jam"
        type: "ical"
        url: "https://exemple.fr/agenda.ics"
        venue_name: "Le Jam"           # lieu par défaut si l'événement n'en a pas
        city: "Montpellier"
        latitude: 43.6
        longitude: 3.86
        website: "https://exemple.fr"  # lien si l'événement n'a pas d'URL

Chaque séance (y compris celles d'un événement répété) dans les
`days_forward` jours devient un `Event` (`src/event/ical_mapper.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import HTTP_HEADERS
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import occurrence_article_url
from src.event.ical_mapper import get_occurrences, map_ical_event, parse_calendar
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.ical.ical_adapter import IcalAdapter
from src.extractors.ical.ical_engine import IcalExtractor, extract_ical_sources
from src.utils.extractor_utils import build_standard_article
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.value_utils import normalize_value

DEFAULT_DAYS_FORWARD = 30
REQUEST_TIMEOUT_S = 30.0


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    reraise=True,
)
def fetch_calendar_text(url: str) -> str:
    """Télécharge un calendrier (trois essais en cas de coupure réseau)."""

    response = httpx.get(url, headers=dict(HTTP_HEADERS), timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def iter_ical_items(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Séances du calendrier dans la fenêtre de dates (une par élément)."""

    text = fetch_calendar_text(normalize_value(source.get("url")))
    if "BEGIN:VCALENDAR" not in text.upper():
        raise ValueError("Le document téléchargé n'est pas un calendrier iCal.")
    today = datetime.now(PARIS).date()
    days_forward = parse_non_negative_integer(source.get("days_forward"), DEFAULT_DAYS_FORWARD)
    return [
        {"vevent": vevent, "occurrence": occurrence}
        for vevent in parse_calendar(text)
        for occurrence in get_occurrences(vevent, today, days_forward)
    ]


def validate_ical_entry(item: Any, source: Mapping[str, Any]) -> tuple[bool, str]:
    """Contrôle minimal : un titre et une séance."""

    del source
    if not isinstance(item, Mapping) or not isinstance(item.get("vevent"), Mapping):
        return False, "evenement_ical_invalide"
    if not item["vevent"].get("SUMMARY"):
        return False, "titre_ical_absent"
    return True, ""


def build_ical_article(item: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    """Transforme une séance en « article » du moteur, événement inclus."""

    event = map_ical_event(item["vevent"], item["occurrence"], source, extracted_at=datetime.now(PARIS).isoformat())
    if event is None:
        return {}
    return build_standard_article(
        # Les UID iCal devraient être uniques au monde, mais on préfixe par
        # le calendrier pour ne pas dépendre de la rigueur de chaque lieu.
        identifier=f"{source.get('source_id', '')}:{event.source_event_id or event.title + '|' + event.start_at}",
        source=source.get("name", "iCal"),
        title=event.title,
        text=event.description,
        image_url=event.image_url,
        published_at=event.start_at,
        url=occurrence_article_url(event),  # unique par séance (voir event_pipeline)
        language=event.language,
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "calendar": source.get("source_id", "")},
    )


ICAL_ADAPTER = IcalAdapter(
    iter_items=iter_ical_items,
    build_article=build_ical_article,
    validate_item=validate_ical_entry,
    default_name="Calendriers iCal",
)

ICAL_EXTRACTOR = IcalExtractor(adapter=ICAL_ADAPTER)


def extract_articles_from_sources(sources: list[Mapping[str, Any]]) -> ExtractorResult:
    """Lit une liste de calendriers déjà chargée."""

    return extract_ical_sources(sources, ICAL_ADAPTER)


def extract_all_articles() -> ExtractorResult:
    """Lit tous les calendriers de `ical_sources`."""

    return ICAL_EXTRACTOR.run()


if __name__ == "__main__":
    result = extract_all_articles()
    print(f"{len(result.articles)} séance(s) iCal. Statut : {result.status}.")
    if result.message:
        print(result.message)
