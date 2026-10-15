"""Tests des fonctions géographiques communes (src/utils/geo_utils.py)."""

from __future__ import annotations

from src.utils.geo_utils import distance_km, get_areas, is_point_in_areas, to_float

ZONES = {"areas": [
    {"name": "Montpellier", "latitude": 43.6108, "longitude": 3.8767, "radius_km": 20},
    {"name": "Bassin de Thau", "latitude": 43.42, "longitude": 3.62, "radius_km": 15},
]}


def test_distance_montpellier_paris_is_about_600_km() -> None:
    assert 590 < distance_km(43.6108, 3.8767, 48.8566, 2.3522) < 610


def test_points_in_our_two_zones() -> None:
    areas = get_areas(ZONES)
    assert is_point_in_areas(43.403, 3.697, areas)        # Sète
    assert is_point_in_areas(43.611, 3.877, areas)        # Montpellier
    assert not is_point_in_areas(43.344, 3.215, areas)    # Béziers


def test_old_single_center_format_and_defaults() -> None:
    assert get_areas({"center_latitude": "43.6", "center_longitude": 3.87}) == [
        {"latitude": 43.6, "longitude": 3.87, "radius_km": 20.0}
    ]
    assert get_areas({}) == []


def test_to_float() -> None:
    assert to_float("3,5".replace(",", ".")) == 3.5
    assert to_float("") is None and to_float("abc") is None and to_float(None) is None
