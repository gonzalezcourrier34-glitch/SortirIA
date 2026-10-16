"""Pages d'agenda HTML : adaptateur « événements » pour le moteur scraper de CheckIt.AI.

**Provenance** : nouvel adaptateur écrit pour SortirIA (14/10/2026). Le moteur
(`src/extractors/scrapers/scraper_engine.py`) et le téléchargement des pages
(`get_html` : robots.txt vérifié, pause entre deux pages, détection des pages
anti-robots) sont ceux de CheckIt.AI ; la lecture des fiches est dans
`src/event/html_agenda_mapper.py`, pilotée par les sélecteurs CSS de la
configuration (`scraper_sources`).

**Règle du projet** : un site n'est lu qu'avec l'autorisation écrite de son
éditeur (ou une licence qui le permet). Tant qu'elle n'est pas obtenue, la
source reste `enabled: false` et l'agent ne la lance pas.

Essai sur une page enregistrée depuis le navigateur (aucune requête au site) :

    uv run python -m src.extractors.scrapers.source.agenda_scraper --fichier <page_enregistree>.html
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config.constants import (
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
    SOURCE_TYPE_SCRAPER,
)
from config.paths import SOURCES_FILE
from config.source_config import load_source_registry
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import occurrence_article_url
from src.event.html_agenda_mapper import map_agenda_card, parse_agenda_page
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.core.extractor_service import execute_configured_sources_extractor
from src.extractors.files.file_engine import FileBatchContext
from src.extractors.scrapers.scraper_adapter import ScraperAdapter
from src.extractors.scrapers.scraper_engine import extract_scraper_source
from src.extractors.scrapers.scraper_http_utils import (
    ScraperPageError,
    create_http_client,
    get_html,
    get_scraper_http_options,
)
from src.logger import get_logger
from src.utils.extractor_utils import build_standard_article
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)

DEFAULT_DAYS_FORWARD = 60
DEFAULT_MAX_PAGES = 10


# ---------------------------------------------------------------------------
# Pages à lire
# ---------------------------------------------------------------------------

def page_urls(source: Mapping[str, Any]) -> list[str]:
    """Pages à lire : `start_urls`, puis `page_url_template` (« .../page/{n}/ ») de 2 à `max_pages`."""

    urls = [str(url) for url in source.get("start_urls") or []]
    template = normalize_value(source.get("page_url_template"))
    max_pages = parse_non_negative_integer(source.get("max_pages"), DEFAULT_MAX_PAGES)
    if template:
        urls.extend(template.format(n=number) for number in range(2, max_pages + 1))
    return urls


def iter_agenda_cards(source: Mapping[str, Any]) -> Iterator[dict[str, str]]:
    """Fiches de toutes les pages, page par page ; s'arrête à la première page vide ou absente.

    Le robots.txt et la pause entre deux pages sont gérés par `get_html`
    (CheckIt.AI) d'après `respect_robots_txt` et `request_delay_seconds`.
    """

    respect_robots, delay = get_scraper_http_options(source)
    selectors = source.get("selectors") or {}
    with create_http_client() as client:
        for number, url in enumerate(page_urls(source), start=1):
            try:
                html, final_url = get_html(client, url, respect_robots, delay)
            except ScraperPageError as error:
                if number == 1:
                    raise   # la première page est indispensable : vraie panne
                logger.info("Fin de la pagination à la page %s (%s).", number, error)
                return
            cards = parse_agenda_page(html, selectors, final_url or url)
            logger.info("%s, page %s : %s fiche(s).", source.get("name"), number, len(cards))
            if not cards:
                return
            yield from cards


# ---------------------------------------------------------------------------
# Adaptateur
# ---------------------------------------------------------------------------

def map_card(card: Mapping[str, Any], source: Mapping[str, Any]):
    """Fiche -> (Event | None, motif), dans la fenêtre de collecte de la source."""

    now = datetime.now(PARIS)
    horizon = now.date() + timedelta(days=parse_non_negative_integer(source.get("days_forward"), DEFAULT_DAYS_FORWARD))
    return map_agenda_card(card, source, today=now.date(), horizon=horizon, extracted_at=now.isoformat())


def validate_agenda_card(card: Any, source: Mapping[str, Any]) -> tuple[bool, str]:
    """Rejette les fiches sans titre, sans date... ; « hors_fenetre » pour le passé ou le lointain."""

    if not isinstance(card, Mapping):
        return False, "fiche_invalide"
    event, reason = map_card(card, source)
    if event is None:
        return False, reason or "hors_fenetre"
    return True, ""


def build_agenda_article(card: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    """Transforme une fiche validée en « article » du moteur, événement inclus."""

    event, _ = map_card(card, source)
    if event is None:
        return {}
    return build_standard_article(
        identifier=event.source_event_id,
        source=source.get("name", "Agenda en ligne"),
        title=event.title,
        text=event.description,
        image_url="",          # jamais d'image : elles appartiennent à leurs auteurs
        published_at=event.start_at,
        url=occurrence_article_url(event),
        language="fr",
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "page": card.get("page_url", "")},
    )


AGENDA_ADAPTER = ScraperAdapter(
    source_id="scraper",
    default_name="Agendas en ligne",
    iter_items=iter_agenda_cards,
    build_article=build_agenda_article,
    validate_item=validate_agenda_card,
)


# ---------------------------------------------------------------------------
# Toutes les sources `scraper_sources`
# ---------------------------------------------------------------------------

def load_agenda_sources() -> list[dict[str, Any]]:
    """Sources actives de `scraper_sources` (celles en attente d'accord sont `enabled: false`)."""

    registry = load_source_registry(SOURCES_FILE, require_secrets=False)
    return [source.to_dict() for source in registry.by_type(SOURCE_TYPE_SCRAPER) if source.enabled]


def extract_agenda_sources(sources: Sequence[Mapping[str, Any]]) -> ExtractorResult:
    """Lit chaque site avec le moteur scraper ; un site en panne n'arrête pas les autres."""

    batch = FileBatchContext()
    for source in sources:
        batch.add_result(extract_scraper_source(source, AGENDA_ADAPTER))
    failed = batch.statuses[EXTRACTOR_STATUS_FAILED]
    if failed:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS if batch.articles else EXTRACTOR_STATUS_FAILED
    else:
        status = EXTRACTOR_STATUS_SUCCESS if batch.articles else EXTRACTOR_STATUS_EMPTY
    return ExtractorResult(
        name=AGENDA_ADAPTER.default_name,
        source_type=SOURCE_TYPE_SCRAPER,
        status=status,
        message=f"{len(batch.articles)} événement(s) depuis {len(sources)} site(s).",
        articles=batch.articles,
        errors=batch.errors,
        analyzed_count=batch.analyzed_count,
        rejected_count=batch.rejected_count,
        requests_count=batch.requests_count,
        rejection_reasons=dict(batch.rejection_stats),
    )


def extract_all_articles() -> ExtractorResult:
    """Point d'entrée du registre : tous les sites autorisés."""

    return execute_configured_sources_extractor(
        extractor_name=AGENDA_ADAPTER.default_name,
        source_type=SOURCE_TYPE_SCRAPER,
        sources_loader=load_agenda_sources,
        extraction_function=extract_agenda_sources,
    )


# ---------------------------------------------------------------------------
# Essai sur une page enregistrée
# ---------------------------------------------------------------------------

def check_saved_page(path: Path, source: Mapping[str, Any]) -> str:
    """Rapport sur une page enregistrée depuis le navigateur (aucune requête au site)."""

    cards = parse_agenda_page(path.read_text(encoding="utf-8", errors="replace"), source.get("selectors") or {},
                              str((source.get("start_urls") or [""])[0]))
    lines = [f"{path.name} : {len(cards)} fiche(s) trouvée(s) avec les sélecteurs de « {source.get('name')} »."]
    for card in cards:
        event, reason = map_card(card, source)
        if event is not None:
            lines.append(f"  ok   {event.start_at[:10]} -> {event.end_at[:10] or '...':10} {event.title}")
        else:
            lines.append(f"  --   {reason or 'hors_fenetre'} : {card.get('title')}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agendas en ligne (scraper).")
    parser.add_argument("--fichier", type=Path, help="Page enregistrée à tester (aucune requête au site).")
    parser.add_argument("--source", default="frontignan_agenda", help="source_id dans scraper_sources.")
    args = parser.parse_args()
    if args.fichier:
        registry = load_source_registry(SOURCES_FILE, require_secrets=False)
        source = next((s.to_dict() for s in registry.by_type(SOURCE_TYPE_SCRAPER) if s.source_id == args.source), None)
        if source is None:
            print(f"Source {args.source} absente de scraper_sources.")
            return
        print(check_saved_page(args.fichier, source))
        return
    result = extract_all_articles()
    print(f"{len(result.articles)} événement(s). Statut : {result.status}. Rejets : {result.rejection_reasons}")


if __name__ == "__main__":
    main()
