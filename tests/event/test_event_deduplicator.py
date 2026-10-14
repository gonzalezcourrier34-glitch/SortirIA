"""Tests unitaires de `src/event/event_deduplicator.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from dataclasses import replace

from src.event.event_deduplicator import completeness, deduplicate_events, merge_events
from src.event.openagenda_mapper import map_openagenda_event


def test_duplicates_are_merged_and_completed(openagenda_item) -> None:
    full = map_openagenda_event(openagenda_item)
    poor = replace(full, description="", image_url="", source="autre_agenda")

    unique, merged = deduplicate_events([poor, full])

    assert merged == 1
    assert len(unique) == 1
    assert unique[0].description  # la version la plus complète l'emporte
    assert unique[0].image_url.endswith("jazz.jpg")


def test_distinct_events_are_kept_in_order(openagenda_item) -> None:
    first = map_openagenda_event(openagenda_item)
    second = replace(first, event_id="autre", title="Théâtre d'impro")

    unique, merged = deduplicate_events([first, second])

    assert merged == 0
    assert [event.event_id for event in unique] == [first.event_id, "autre"]


def test_merge_fills_only_empty_fields(openagenda_item) -> None:
    main = replace(map_openagenda_event(openagenda_item), price_text="")
    other = replace(main, price_text="Gratuit", title="Autre titre")

    merged = merge_events(main, other)

    assert merged.price_text == "Gratuit"
    assert merged.title == main.title
    assert completeness(merged) > completeness(main)
