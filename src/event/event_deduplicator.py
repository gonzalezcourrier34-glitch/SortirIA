"""Fusion d'un même événement annoncé par plusieurs sources.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), inspiré de
`src/article/processing/article_deduplicator.py` de CheckIt.AI.

Deux événements sont considérés comme identiques s'ils ont le même
`event_id`, calculé à partir du titre, du jour et du lieu normalisés
(voir `event_normalizer.build_dedup_key`). On garde alors la version la plus
complète, et on complète ses champs vides avec ceux de l'autre.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.event.event_schema import Event


def completeness(event: Event) -> int:
    """Nombre de champs renseignés : sert à choisir la meilleure version."""

    return sum(
        1
        for value in event.to_dict().values()
        if value not in (None, "", [], {})
    )


def merge_events(main: Event, other: Event) -> Event:
    """Complète les champs vides de `main` avec ceux de `other`."""

    merged = main.to_dict()
    for name, value in other.to_dict().items():
        if merged.get(name) in (None, "", []) and value not in (None, "", []):
            merged[name] = value
    return Event.from_dict(merged)


def deduplicate_events(events: Iterable[Event]) -> tuple[list[Event], int]:
    """Supprime les doublons. Renvoie (événements uniques, nombre de doublons fusionnés).

    L'ordre de première apparition est conservé, pour des résultats stables.
    """

    kept: dict[str, Event] = {}
    duplicates = 0
    for event in events:
        existing = kept.get(event.event_id)
        if existing is None:
            kept[event.event_id] = event
            continue
        duplicates += 1
        best, rest = (event, existing) if completeness(event) > completeness(existing) else (existing, event)
        kept[event.event_id] = merge_events(best, rest)
    return list(kept.values()), duplicates
