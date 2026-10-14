"""Données de test partagées par les tests du modèle événement.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.event.event_normalizer import PARIS


def make_openagenda_item(**overrides: Any) -> dict[str, Any]:
    """Événement OpenAgenda réaliste (format `events.json`), modifiable par test."""

    item: dict[str, Any] = {
        "uid": 12345,
        "title": {"fr": "Concert Jazz au Jam", "en": "Jazz concert"},
        "description": {"fr": "Soirée jazz avec le Blue Mood Quartet."},
        "longDescription": {"fr": "<p>Soirée <b>jazz</b> &amp; cocktails.</p>"},
        "keywords": {"fr": ["jazz", "concert"]},
        "conditions": {"fr": "Tarif plein 15 €, réduit 10 €"},
        "canonicalUrl": "https://openagenda.com/montpellier/events/concert-jazz",
        "image": {"base": "https://cdn.openagenda.com/main/", "filename": "jazz.jpg"},
        "location": {
            "name": "Le Jam",
            "address": "100 rue Ferdinand de Lesseps",
            "city": "MONTPELLIER",
            "postalCode": "34070",
            "latitude": 43.6,
            "longitude": 3.86,
        },
        "timings": [
            {"start": "2026-10-24T18:30:00.000Z", "end": "2026-10-24T21:00:00.000Z"},
        ],
    }
    item.update(overrides)
    return item


@pytest.fixture
def openagenda_item() -> dict[str, Any]:
    return make_openagenda_item()


@pytest.fixture
def now() -> datetime:
    """« Maintenant » figé au 20/10/2026 à midi, pour des tests reproductibles."""

    return datetime(2026, 10, 20, 12, 0, tzinfo=PARIS)
