"""Explore l'API DATAtourisme (événements) avant de la brancher.

**Provenance** : nouveau script écrit pour SortirIA (14/10/2026), sur le
modèle de `scripts/audit_openagenda.py` (fonction `field_paths` réutilisée).

Historique de la décision :

- le CSV simplifié des événements publié sur data.gouv.fr est inutilisable :
  exploration du 14/10/2026, 77 201 lignes, dates, villes, codes postaux et
  coordonnées vides à 100 % ;
- la plateforme Diffuseur ferme (plus de nouveaux flux depuis octobre 2026) ;
- l'accès recommandé est l'API DATAtourisme : https://api.datatourisme.fr/v1/docs
  (clé gratuite, Licence Ouverte, 1 000 requêtes par heure).

Ce script interroge `GET /v1/entertainmentAndEvent` (« Fêtes et
manifestations ») autour du centre de chacune de nos zones (`areas` de
config/sources.yaml), puis enregistre :

- le nombre d'événements par zone ;
- la fréquence de chaque champ (pour savoir où sont les dates, les prix...) ;
- quelques événements bruts ;
- le résultat de deux essais : champs supplémentaires (`fields`) et filtre
  sur la date de fin (`takesPlaceAt.endDate[gte]`), que la documentation ne
  détaille pas.

La clé se met dans le fichier .env : DATATOURISME_API_KEY=...

Utilisation :

    uv run python -m scripts.explore_datatourisme --json perso/datatourisme_exploration.json
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from config.environment import load_environment
from scripts.audit_openagenda import field_paths
from src.event.event_normalizer import PARIS
from src.extractors.apis.source.openagenda_extractor import OPENAGENDA_EXTRACTOR
from src.utils.geo_utils import get_areas

API_URL = "https://api.datatourisme.fr/v1/entertainmentAndEvent"
PAGE_SIZE = 100
MAX_PAGES_PER_AREA = 5  # 500 événements par zone au plus pour l'exploration


def call_api(api_key: str, params: dict[str, Any], url: str | None = None) -> tuple[int, dict[str, Any] | str]:
    """Appelle l'API. Renvoie (code HTTP, JSON) ou (code HTTP, message d'erreur).

    `url` sert à suivre les liens `meta.next` (qui contiennent déjà leurs
    paramètres) ; sinon on appelle API_URL avec `params`.
    """

    full_url = url or f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        full_url,
        headers={"X-API-Key": api_key, "Accept": "application/json", "User-Agent": "SortirIA/0.1 (projet étudiant)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")[:500]


def explore_area(api_key: str, area: dict[str, float]) -> tuple[int, list[dict[str, Any]], int]:
    """Lit les événements d'une zone. Renvoie (total annoncé, objets lus, code HTTP)."""

    params = {
        "geo_distance": f"{area['latitude']},{area['longitude']},{area['radius_km']:g}km",
        "page_size": PAGE_SIZE,
        "lang": "fr",
    }
    status, data = call_api(api_key, params)
    if status != 200 or not isinstance(data, dict):
        print(f"Erreur {status} : {data}")
        return 0, [], status
    objects = list(data.get("objects") or [])
    total = int((data.get("meta") or {}).get("total") or 0)
    next_url = (data.get("meta") or {}).get("next")
    pages = 1
    while next_url and pages < MAX_PAGES_PER_AREA:
        status, data = call_api(api_key, {}, url=next_url)
        if status != 200 or not isinstance(data, dict):
            break
        objects += list(data.get("objects") or [])
        next_url = (data.get("meta") or {}).get("next")
        pages += 1
    return total, objects, 200


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="Enregistre le résultat dans ce fichier JSON.")
    parser.add_argument("--echantillon", type=int, default=5, help="Nombre d'événements bruts à garder.")
    args = parser.parse_args()

    load_environment()
    api_key = os.environ.get("DATATOURISME_API_KEY", "").strip()
    if not api_key:
        print("DATATOURISME_API_KEY absente du fichier .env.")
        return 1

    areas = get_areas(OPENAGENDA_EXTRACTOR.source)  # mêmes zones qu'OpenAgenda
    names = [area.get("name", f"zone {i}") for i, area in enumerate(OPENAGENDA_EXTRACTOR.source.get("areas") or [])]

    by_area: dict[str, Any] = {}
    all_objects: dict[str, dict[str, Any]] = {}
    for index, area in enumerate(areas):
        total, objects, status = explore_area(api_key, area)
        name = names[index] if index < len(names) else f"zone {index}"
        by_area[name] = {"code_http": status, "total_annonce": total, "lus": len(objects)}
        for obj in objects:
            all_objects[str(obj.get("uuid") or obj.get("uri") or len(all_objects))] = obj

    paths: Counter[str] = Counter()
    for obj in all_objects.values():
        paths.update(set(field_paths(obj)))
    frequency = {path: f"{100 * count / len(all_objects):.0f} %" for path, count in sorted(paths.items())} if all_objects else {}

    # Essais sur ce que la documentation ne détaille pas.
    center = areas[0]
    geo = f"{center['latitude']},{center['longitude']},{center['radius_km']:g}km"
    tests = {}
    status, data = call_api(api_key, {"geo_distance": geo, "page_size": 2, "lang": "fr",
                                      "fields": "uuid,label,takesPlaceAt,offers,hasTheme,hasAudience"})
    tests["fields_takesPlaceAt_offers"] = {"code_http": status, "reponse": data}
    status, data = call_api(api_key, {"geo_distance": geo, "page_size": 1, "lang": "fr",
                                      "takesPlaceAt.endDate[gte]": datetime.now(PARIS).date().isoformat()})
    tests["filtre_date_de_fin"] = {
        "code_http": status,
        "total": (data.get("meta") or {}).get("total") if isinstance(data, dict) else data,
    }

    summary = {
        "api": API_URL,
        "zones": by_area,
        "evenements_distincts": len(all_objects),
        "frequence_des_champs": frequency,
        "essais": tests,
        "echantillon": list(all_objects.values())[: args.echantillon],
    }
    print(json.dumps({k: v for k, v in summary.items() if k not in ("echantillon", "frequence_des_champs")},
                     ensure_ascii=False, indent=2)[:4000])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nRésultat complet : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
