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

from src.event.event_deduplicator import deduplicate_events, merge_cross_source
from src.event.event_schema import Event
from src.event.event_validator import conform_to_schema, validate_event


@dataclass(slots=True)
class PreparationReport:
    """Bilan de la préparation, enregistré dans `sortiria.collect_runs`.

    Attributes
    ----------
    events_extracted : int
        Nombre d'articles reçus des extracteurs.
    events_rejected : int
        Nombre d'événements écartés (format inconnu ou validation refusée).
    duplicates_merged : int
        Nombre total de doublons fusionnés (même source et entre sources).
    cross_source_merged : int
        Dont : même événement venu de deux sources.
    rejection_reasons : dict[str, int]
        Nombre de rejets par motif.
    """

    events_extracted: int = 0
    events_rejected: int = 0
    duplicates_merged: int = 0
    cross_source_merged: int = 0        # dont : même événement venu de deux sources
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convertir le bilan en dictionnaire prêt à enregistrer.

        Returns
        -------
        dict[str, Any]
            Les compteurs et une copie des motifs de rejet.
        """
        return {
            "events_extracted": self.events_extracted,
            "events_rejected": self.events_rejected,
            "duplicates_merged": self.duplicates_merged,
            "cross_source_merged": self.cross_source_merged,
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

    Parameters
    ----------
    event : Event
        L'événement dont on construit l'URL technique.

    Returns
    -------
    str
        `event.url` avec le paramètre `occurrence=<event_id>`, placé avant
        un éventuel fragment.
    """

    # Le paramètre doit passer AVANT un éventuel fragment (`#page=3` des PDF) :
    # tout ce qui suit `#` est ignoré par la normalisation des URL, et deux
    # événements d'un même PDF devenaient des doublons (bug du 14/10/2026).
    address, hash_sign, fragment = event.url.partition("#")
    separator = "&" if "?" in address else "?"
    return f"{address}{separator}occurrence={event.event_id}{hash_sign}{fragment}"


def event_from_article(article: Mapping[str, Any]) -> Event | None:
    """Récupère l'`Event` rangé par un extracteur dans `article["metadata"]["event"]`.

    Parameters
    ----------
    article : Mapping[str, Any]
        Article produit par le moteur d'extraction.

    Returns
    -------
    Event or None
        L'événement reconstruit, ou None si l'article n'en contient pas.
    """

    metadata = article.get("metadata")
    data = metadata.get("event") if isinstance(metadata, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    return Event.from_dict(dict(data))


def prepare_events(
    articles: Iterable[Mapping[str, Any]],
    now: datetime | None = None,
) -> tuple[list[Event], PreparationReport]:
    """Convertit, valide et déduplique les événements d'une collecte.

    Parameters
    ----------
    articles : Iterable[Mapping[str, Any]]
        Articles produits par les extracteurs, chacun portant un événement.
    now : datetime or None
        Heure de référence pour écarter les événements passés. Par défaut
        None (l'heure courante, voir `validate_event`).

    Returns
    -------
    tuple[list[Event], PreparationReport]
        Les événements valides et uniques, et le bilan chiffré de la préparation.
    """

    report = PreparationReport()
    reasons: Counter[str] = Counter()
    valid: list[Event] = []

    # Convertir chaque article en événement, le mettre au schéma et le valider
    for article in articles:
        report.events_extracted += 1
        event = event_from_article(article)
        if event is None:
            reasons["format_inconnu"] += 1
            continue
        conform_to_schema(event)
        is_valid, reason = validate_event(event, now)
        if not is_valid:
            reasons[reason] += 1
            continue
        valid.append(event)

    # Fusionner les doublons d'une même source (même clé)
    unique, duplicates = deduplicate_events(valid)
    # Puis le même événement venu de deux sources, titres écrits autrement.
    unique, cross_source = merge_cross_source(unique)
    # Remplir le bilan de la nuit
    report.events_rejected = sum(reasons.values())
    report.duplicates_merged = duplicates + cross_source
    report.cross_source_merged = cross_source
    report.rejection_reasons = dict(reasons)
    return unique, report
