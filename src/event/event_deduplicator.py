"""Fusion d'un même événement annoncé par plusieurs sources.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), inspiré de
`src/article/processing/article_deduplicator.py` de CheckIt.AI.

Deux événements sont considérés comme identiques s'ils ont le même
`event_id`, calculé à partir du titre, du jour et du lieu normalisés
(voir `event_normalizer.build_dedup_key`). On garde alors la version la plus
complète, et on complète ses champs vides avec ceux de l'autre.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from src.event.event_normalizer import normalize_for_comparison, parse_event_datetime
from src.event.event_schema import Event

# Même événement, deux sources (15/10/2026) : l'office de tourisme et
# OpenAgenda annoncent souvent la même sortie, avec un titre et un lieu
# écrits autrement (« Concert : Jazz au port » / « JAZZ AU PORT »). La clé
# exacte ne les rapproche pas ; ce second passage le fait, prudemment.
# Source gardée en priorité : le partenaire (sa fiche porte son offre), puis
# la plus riche en informations.
SOURCE_PRIORITY = {"partenaire": 0, "openagenda": 1, "datatourisme": 2, "document": 3}
TITLE_SIMILARITY = 0.75      # part des mots communs (Jaccard) pour deux titres du même jour et de la même commune
MAX_TIME_GAP_MINUTES = 30    # deux heures connues plus éloignées : deux séances différentes
TITLE_STOPWORD_TEXT = """
le la les l un une des de du d et a au aux en pour par sur avec dans the concert spectacle atelier
exposition visite soiree conference festival
"""
TITLE_STOPWORDS = frozenset(TITLE_STOPWORD_TEXT.split())


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


def title_words(title: str) -> frozenset[str]:
    """Mots porteurs d'un titre (sans accents, sans mots outils ni types de sortie)."""

    words = re.findall(r"[a-z0-9]+", normalize_for_comparison(title))
    return frozenset(word for word in words if len(word) >= 3 and word not in TITLE_STOPWORDS)


def same_event(first: Event, second: Event) -> bool:
    """Deux fiches de sources différentes décrivent-elles la même sortie ?

    Même jour et même commune (déjà vérifiés par l'appelant), heures compatibles,
    et titres presque identiques : au moins 75 % de mots communs, ou l'un
    contenu dans l'autre avec au moins deux mots.
    """

    if first.source == second.source:
        return False
    start_a, start_b = parse_event_datetime(first.start_at), parse_event_datetime(second.start_at)
    both_timed = first.time_known and second.time_known
    if (both_timed and isinstance(start_a, datetime) and isinstance(start_b, datetime)
            and abs((start_a - start_b).total_seconds()) > MAX_TIME_GAP_MINUTES * 60):
        return False
    words_a, words_b = title_words(first.title), title_words(second.title)
    if not words_a or not words_b:
        return False
    common = len(words_a & words_b)
    if common / len(words_a | words_b) >= TITLE_SIMILARITY:
        return True
    return common >= 2 and (words_a <= words_b or words_b <= words_a)


def merge_cross_source(events: Iterable[Event]) -> tuple[list[Event], int]:
    """Fusionne le même événement publié par deux sources. Renvoie (événements, fusions).

    Comparaison seulement entre événements du même jour et de la même commune
    (rapide, et sans risque de rapprocher deux villes). La fiche gardée est
    celle de la source prioritaire (identifiant stable d'une nuit à l'autre),
    complétée par l'autre.
    """

    events = list(events)
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, event in enumerate(events):
        start = parse_event_datetime(event.start_at)
        if start is not None and event.city:
            buckets[(start.date().isoformat(), normalize_for_comparison(event.city))].append(index)
    absorbed: set[int] = set()
    replaced: dict[int, Event] = {}
    for indexes in buckets.values():
        for position, i in enumerate(indexes):
            if i in absorbed:
                continue
            for j in indexes[position + 1:]:
                if j in absorbed:
                    continue
                main_event = replaced.get(i, events[i])
                if not same_event(main_event, events[j]):
                    continue
                keep_first = (SOURCE_PRIORITY.get(main_event.source, 9), -completeness(main_event)) <= (
                    SOURCE_PRIORITY.get(events[j].source, 9), -completeness(events[j]))
                best, other = (main_event, events[j]) if keep_first else (events[j], main_event)
                replaced[i] = merge_events(best, other)
                absorbed.add(j)
    merged = [replaced.get(index, event) for index, event in enumerate(events) if index not in absorbed]
    return merged, len(absorbed)
