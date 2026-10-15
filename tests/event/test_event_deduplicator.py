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


# --- Même événement venu de deux sources (15/10/2026) -----------------------

def test_same_event_from_two_sources_is_merged(openagenda_item) -> None:
    from src.event.event_deduplicator import merge_cross_source

    base = map_openagenda_event(openagenda_item)
    openagenda = replace(base, event_id="oa", source="openagenda", title="Concert : Jazz au port de Sète",
                         city="Sète", start_at="2030-10-24T20:30:00+02:00", time_known=True, image_url="")
    tourisme = replace(base, event_id="dt", source="datatourisme", title="JAZZ AU PORT DE SETE",
                       city="SETE", start_at="2030-10-24T20:30:00+02:00", time_known=True,
                       image_url="https://x.fr/photo.jpg", description="")
    other_day = replace(tourisme, event_id="dt2", start_at="2030-10-25T20:30:00+02:00")
    other_session = replace(tourisme, event_id="dt3", start_at="2030-10-24T15:00:00+02:00")
    other_title = replace(tourisme, event_id="dt4", title="Marché de Noël")

    merged, count = merge_cross_source([openagenda, tourisme, other_day, other_session, other_title])

    assert count == 1
    assert [event.event_id for event in merged] == ["oa", "dt2", "dt3", "dt4"]   # OpenAgenda gardé (prioritaire)
    assert merged[0].image_url == "https://x.fr/photo.jpg"                        # complété par l'autre fiche


def test_same_source_and_partner_priority(openagenda_item) -> None:
    from src.event.event_deduplicator import merge_cross_source

    base = map_openagenda_event(openagenda_item)
    first = replace(base, event_id="a", source="openagenda", title="Atelier poterie enfants", city="Mèze",
                    start_at="2030-10-24T14:00:00+02:00")
    twin = replace(first, event_id="b")                                         # même source : jamais fusionné
    assert merge_cross_source([first, twin])[1] == 0
    partner = replace(first, event_id="p", source="partenaire", partner_id=3, title="ATELIER POTERIE ENFANTS")
    merged, count = merge_cross_source([first, partner])
    assert count == 1 and merged[0].event_id == "p" and merged[0].partner_id == 3   # la fiche du partenaire gagne
