"""Flux RSS d'agenda : adaptateur « événements » pour le moteur RSS de CheckIt.AI.

**Provenance** : nouvel adaptateur écrit pour SortirIA (14/10/2026). Le moteur
(`src/extractors/rss/rss_engine.py`) et le téléchargement / la lecture des
flux (`iter_rss_items` de `rss_extractor.py`) sont ceux de CheckIt.AI ; seul
change ce que l'on fait d'une entrée : un `Event` (date, lieu, commune) au
lieu d'un article de presse (voir `src/event/rss_event_mapper.py`).

Une entrée par flux dans `config/sources.yaml` (`rss_sources`) :

    - name: "Nom du lieu"
      source_id: "nom_du_lieu"
      type: "rss"
      url: "https://exemple.fr/agenda/feed/"
      event_date: "published"   # la date de publication EST la date de l'événement
      venue_name: "Nom du lieu"
      city: "Sète"
      credit: "Nom du lieu"

Seuls les flux dont l'éditeur autorise la reprise sont branchés (flux prévus
pour la syndication, partenaires) ; le robots.txt est respecté.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from config.paths import SOURCES_FILE
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import occurrence_article_url
from src.event.event_schema import Event
from src.event.rss_event_mapper import map_rss_event
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.rss.rss_adapter import RssAdapter
from src.extractors.rss.rss_engine import RssExtractor
from src.utils.extractor_utils import build_standard_article
from src.utils.parsing_utils import parse_non_negative_integer

DEFAULT_DAYS_FORWARD = 60

# Entrée du flux, événement tiré de l'entrée (ou None) et motif du rejet.
MappedEntry = tuple[Any, Event | None, str]


def iter_event_entries(source: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Entrées du flux, téléchargées et lues par le code RSS de CheckIt.AI.

    Import différé : `rss_extractor` charge feedparser, inutile tant qu'aucun
    flux n'est configuré (et remplaçable dans les tests).

    Parameters
    ----------
    source : Mapping[str, Any]
        Entrée de `rss_sources` (nom, url...).

    Returns
    -------
    Sequence[Mapping[str, Any]]
        Entrées du flux analysées par Feedparser.
    """

    from src.extractors.rss.rss_extractor import iter_rss_items

    return iter_rss_items(source)


def map_entry(
    entry: Mapping[str, Any],
    source: Mapping[str, Any],
    now: datetime | None = None
) -> tuple[Event | None, str]:
    """Entrée -> (Event | None, motif), dans la fenêtre de collecte de la source.

    Parameters
    ----------
    entry : Mapping[str, Any]
        Entrée du flux.
    source : Mapping[str, Any]
        Entrée de `rss_sources` ; `days_forward` fixe la fenêtre (60 jours par
        défaut).
    now : datetime | None
        Instant de référence de la fenêtre (heure de Paris). Par défaut
        `None`, qui prend l'instant présent.

    Returns
    -------
    tuple[Event | None, str]
        L'événement et un motif vide, ou None et le motif du rejet (voir
        `map_rss_event`).
    """

    now = now or datetime.now(PARIS)
    horizon = now.date() + timedelta(days=parse_non_negative_integer(source.get("days_forward"), DEFAULT_DAYS_FORWARD))
    return map_rss_event(entry, source, today=now.date(), horizon=horizon, extracted_at=now.isoformat())


def iter_mapped_entries(source: Mapping[str, Any]) -> list[MappedEntry]:
    """Lit le flux et tire une seule fois l'événement de chaque entrée.

    La validation puis la construction de l'article (appelées l'une après
    l'autre par le moteur RSS) réutilisent ce résultat au lieu de refaire
    la conversion.

    Parameters
    ----------
    source : Mapping[str, Any]
        Entrée de `rss_sources` (nom, url, fenêtre...).

    Returns
    -------
    list[MappedEntry]
        Pour chaque entrée : l'entrée, l'événement (ou None) et le motif du
        rejet (« entree_invalide » si l'entrée n'est pas un dictionnaire).
    """

    # Même instant de référence pour toutes les entrées du flux
    now = datetime.now(PARIS)
    mapped_entries: list[MappedEntry] = []

    for entry in iter_event_entries(source):
        if isinstance(entry, Mapping):
            event, reason = map_entry(entry, source, now)
        else:
            event, reason = None, "entree_invalide"
        mapped_entries.append((entry, event, reason))

    return mapped_entries


def validate_event_entry(item: Any, source: Mapping[str, Any]) -> tuple[bool, str]:
    """Rejette les entrées sans date d'événement, sans titre... (motif compté).

    Une entrée hors fenêtre (passée, trop lointaine) est écartée sous le motif
    « hors_fenetre » : ce n'est pas une erreur du flux.

    Parameters
    ----------
    item : Any
        Entrée convertie par `iter_mapped_entries` (entrée, événement, motif).
    source : Mapping[str, Any]
        Entrée de `rss_sources`.

    Returns
    -------
    tuple[bool, str]
        Vrai et motif vide si l'entrée donne un événement, sinon faux et le motif.
    """

    if not isinstance(item, tuple) or len(item) != 3:
        return False, "entree_invalide"
    _, event, reason = item
    if event is None:
        return False, reason or "hors_fenetre"
    return True, ""


def build_event_rss_article(item: MappedEntry, source: Mapping[str, Any]) -> dict[str, Any]:
    """Transforme une entrée validée en « article » du moteur, événement inclus.

    Parameters
    ----------
    item : MappedEntry
        Entrée convertie par `iter_mapped_entries`, déjà validée.
    source : Mapping[str, Any]
        Entrée de `rss_sources` (nom, identifiant, rôle...).

    Returns
    -------
    dict[str, Any]
        Article standard, l'événement dans `metadata["event"]` ; dictionnaire
        vide si l'entrée ne donne pas d'événement.
    """

    _, event, _ = item
    if event is None:
        return {}
    return build_standard_article(
        identifier=event.source_event_id,
        source=source.get("name", "RSS"),
        title=event.title,
        text=event.description,
        image_url="",
        published_at=event.start_at,
        url=occurrence_article_url(event),
        language="fr",
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "feed": source.get("source_id", "")},
    )


EVENT_RSS_ADAPTER = RssAdapter(
    iter_items=iter_mapped_entries,
    build_article=build_event_rss_article,
    validate_item=validate_event_entry,
    default_name="Flux RSS d'agenda",
)

EVENT_RSS_EXTRACTOR = RssExtractor(adapter=EVENT_RSS_ADAPTER, sources_file=SOURCES_FILE)


def extract_all_articles() -> ExtractorResult:
    """Lit tous les flux de `rss_sources` (point d'entrée du registre).

    Returns
    -------
    ExtractorResult
        Événements extraits, statut et bilan des rejets.
    """

    return EVENT_RSS_EXTRACTOR.run()


if __name__ == "__main__":
    result = extract_all_articles()
    print(f"{len(result.articles)} événement(s). Statut : {result.status}. Rejets : {result.rejection_reasons}")
