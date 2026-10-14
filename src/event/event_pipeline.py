"""Préparation des événements collectés : conversion, validation, déduplication.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Il joue pour
les événements le rôle de `src/pipelines/transformation_pipeline.py` de
CheckIt.AI pour les articles.

C'est l'étape entre l'extraction (moteur CheckIt.AI) et le chargement en base
(`event_repository`). Elle produit aussi le bilan chiffré de la nuit, utilisé
par l'agent de collecte et le tableau de bord.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.event.event_deduplicator import deduplicate_events
from src.event.event_schema import Event
from src.event.event_validator import validate_event


@dataclass(slots=True)
class PreparationReport:
    """Bilan de la préparation, enregistré dans `sortiria.collect_runs`."""

    events_extracted: int = 0
    events_rejected: int = 0
    duplicates_merged: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_extracted": self.events_extracted,
            "events_rejected": self.events_rejected,
            "duplicates_merged": self.duplicates_merged,
            "rejection_reasons": dict(self.rejection_reasons),
        }


def occurrence_article_url(event: Event) -> str:
    """URL technique, unique par séance, pour l'« article » du moteur CheckIt.

    Le moteur considère deux articles de même URL comme des doublons. Or
    plusieurs séances d'un film, ou tous les événements d'un calendrier de
    lieu, partagent la même URL (le site du cinéma, du lieu...). On ajoute donc
    l'identifiant de l'événement (titre + jour + lieu) : le dédoublonnage du
    moteur suit alors la même règle que celui des événements. Le lien montré à
    l'utilisateur reste `event.url`. (Bug trouvé le 14/10/2026 en testant le
    moteur iCal.)
    """

    separator = "&" if "?" in event.url else "?"
    return f"{event.url}{separator}occurrence={event.event_id}"


def event_from_article(article: Mapping[str, Any]) -> Event | None:
    """Récupère l'`Event` rangé par un extracteur dans `article["metadata"]["event"]`."""

    metadata = article.get("metadata")
    data = metadata.get("event") if isinstance(metadata, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    return Event.from_dict(dict(data))


def prepare_events(
    articles: Iterable[Mapping[str, Any]],
    now: datetime | None = None,
) -> tuple[list[Event], PreparationReport]:
    """Convertit, valide et déduplique les événements d'une collecte."""

    report = PreparationReport()
    reasons: Counter[str] = Counter()
    valid: list[Event] = []

    for article in articles:
        report.events_extracted += 1
        event = event_from_article(article)
        if event is None:
            reasons["format_inconnu"] += 1
            continue
        is_valid, reason = validate_event(event, now)
        if not is_valid:
            reasons[reason] += 1
            continue
        valid.append(event)

    unique, duplicates = deduplicate_events(valid)
    report.events_rejected = sum(reasons.values())
    report.duplicates_merged = duplicates
    report.rejection_reasons = dict(reasons)
    return unique, report
