"""Fonctions géographiques communes : distance GPS et zones de collecte.

**Provenance** : déplacé le 14/10/2026 depuis
`src/extractors/apis/source/openagenda_extractor.py`, où ces fonctions avaient
été écrites le 13/10/2026. DATAtourisme et les scripts d'audit les utilisaient
aussi : elles sont maintenant dans un module commun, que chaque adaptateur
importe.

Les zones sont déclarées une seule fois dans `config/sources.yaml` (liste
`areas`, partagée par les sources grâce à une ancre YAML) : Montpellier et sa
métropole (20 km) et le bassin de Thau (15 km). Plusieurs petites zones
évitent un cercle géant qui engloberait des communes sans intérêt.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import asin, cos, radians, sin, sqrt
from typing import Any

EARTH_RADIUS_KM = 6371.0
DEFAULT_RADIUS_KM = 20.0


def to_float(value: Any) -> float | None:
    """Convertit en nombre, ou renvoie None (valeur absente ou illisible)."""

    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau entre deux points GPS (formule de haversine).

    La même formule existe en SQL (`sortiria.distance_km`, fichier
    `sql/sortiria/00_extensions.sql`) pour filtrer directement en base.
    """

    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def get_areas(source: Mapping[str, Any]) -> list[dict[str, float]]:
    """Zones d'une source : liste `areas`, ou ancien format à une seule zone.

    Chaque zone a un centre (`latitude`, `longitude`) et un rayon `radius_km`.
    """

    areas: list[dict[str, float]] = []
    for area in source.get("areas") or []:
        lat, lon = to_float(area.get("latitude")), to_float(area.get("longitude"))
        if lat is not None and lon is not None:
            areas.append({"latitude": lat, "longitude": lon, "radius_km": to_float(area.get("radius_km")) or DEFAULT_RADIUS_KM})
    center_lat, center_lon = to_float(source.get("center_latitude")), to_float(source.get("center_longitude"))
    if not areas and center_lat is not None and center_lon is not None:
        areas.append({"latitude": center_lat, "longitude": center_lon,
                      "radius_km": to_float(source.get("radius_km")) or DEFAULT_RADIUS_KM})
    return areas


def is_point_in_areas(latitude: float, longitude: float, areas: list[dict[str, float]]) -> bool:
    """Vrai si le point est dans au moins une des zones."""

    return any(
        distance_km(area["latitude"], area["longitude"], latitude, longitude) <= area["radius_km"]
        for area in areas
    )
