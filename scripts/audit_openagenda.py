"""Audit d'une collecte OpenAgenda : les chiffres de la partie 2 du rapport.

**Provenance** : nouveau script écrit pour SortirIA (13/10/2026).

Il lance une vraie collecte (clé OPENAGENDA_API_KEY dans .env), prépare les
événements comme la collecte nocturne (validation, déduplication), puis
affiche les indicateurs de qualité des données. Rien n'est écrit en base.

Utilisation :
    uv run python -m scripts.audit_openagenda
    uv run python -m scripts.audit_openagenda --json audit.json
    uv run python -m scripts.audit_openagenda --json audit.json --brut audit_brut.json

L'option --brut enregistre la structure réelle de quelques événements
OpenAgenda (liste des champs présents et un exemple complet) : elle sert à
vérifier où la source range la catégorie, les mots-clés, le public, etc.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from src.event.event_normalizer import PARIS, parse_event_datetime
from src.event.event_pipeline import prepare_events
from src.event.event_schema import Event
from src.extractors.apis.source.openagenda_extractor import (
    OPENAGENDA_EXTRACTOR,
    extract_all_articles,
    extract_articles_from_source,
    fetch_agenda_page,
    get_api_key,
    is_in_area,
)


def percent(part: int, total: int) -> str:
    """Formate une part en pourcentage à une décimale.

    Parameters
    ----------
    part : int
        Le nombre d'éléments concernés.
    total : int
        Le nombre total d'éléments.

    Returns
    -------
    str
        Par exemple « 42.5 % », ou « n/a » si le total est nul.
    """
    return f"{100 * part / total:.1f} %" if total else "n/a"


def compute_indicators(events: list[Event], target_city: str) -> dict[str, Any]:
    """Calcule les indicateurs de qualité sur les événements retenus.

    Parameters
    ----------
    events : list[Event]
        Les événements après validation et déduplication.
    target_city : str
        La ville visée, comparée sans tenir compte de la casse.

    Returns
    -------
    dict[str, Any]
        Les compteurs et pourcentages (ville cible, heure, prix, gratuité, GPS,
        image, description), l'horizon des dates et les dix villes et
        catégories les plus fréquentes.
    """

    # Compter villes, catégories et horizon des dates de début
    total = len(events)
    cities = Counter(event.city or "(sans ville)" for event in events)
    categories = Counter(event.category or "(sans catégorie)" for event in events)
    starts = [parse_event_datetime(event.start_at) for event in events]
    days_ahead = Counter(
        "aujourd'hui-7 j" if (d - datetime.now(PARIS)).days < 7 else "8-30 j" if (d - datetime.now(PARIS)).days <= 30 else "> 30 j"
        for d in starts if d
    )
    # Mesurer la part des événements dans la ville cible et le remplissage des champs
    in_target = sum(1 for event in events if event.city.lower() == target_city.lower())
    return {
        "evenements_retenus": total,
        "dans_la_ville_cible": in_target,
        "part_dans_la_ville_cible": percent(in_target, total),
        "avec_heure_precise": percent(sum(e.time_known for e in events), total),
        "avec_prix_exploitable": percent(sum(e.price_min is not None for e in events), total),
        "gratuits": percent(sum(e.is_free is True for e in events), total),
        "avec_coordonnees_gps": percent(sum(e.latitude is not None and e.longitude is not None for e in events), total),
        "avec_image": percent(sum(bool(e.image_url) for e in events), total),
        "avec_description": percent(sum(bool(e.description) for e in events), total),
        "horizon": dict(days_ahead),
        "top_villes": dict(cities.most_common(10)),
        "top_categories": dict(categories.most_common(10)),
    }


def field_paths(value: Any, prefix: str = "") -> list[str]:
    """Liste les chemins des champs d'un objet JSON (« location.city »...).

    Les dictionnaires de traductions (clés « fr », « en »...) ne sont pas
    détaillés ; pour une liste de dictionnaires, seul le premier élément est
    parcouru (suffixe « [] »).

    Parameters
    ----------
    value : Any
        L'objet JSON à parcourir.
    prefix : str
        Le chemin déjà parcouru. Par défaut vide (racine).

    Returns
    -------
    list[str]
        Les chemins de tous les champs rencontrés.
    """

    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            if not (isinstance(child, dict) and set(child) <= {"fr", "en", "es", "de", "it"}):
                paths.extend(field_paths(child, path))
        return paths
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return field_paths(value[0], prefix + "[]")
    return []


def dump_raw_structure(source: dict[str, Any], agenda_uids: list[str], path: Path) -> None:
    """Enregistre la fréquence des champs et un exemple brut d'événement local.

    Lit une page de 50 événements dans au plus trois agendas, garde ceux de la
    zone, puis écrit un JSON avec la fréquence de chaque champ et un exemple.

    Parameters
    ----------
    source : dict[str, Any]
        La configuration de la source OpenAgenda (zones, paramètres d'appel).
    agenda_uids : list[str]
        Les agendas à interroger ; seuls les trois premiers sont lus.
    path : Path
        Le fichier JSON à écrire.
    """

    api_key = get_api_key()
    items: list[dict[str, Any]] = []
    for agenda_uid in agenda_uids[:3]:
        items += [item for item in fetch_agenda_page(source, agenda_uid, api_key, 0, 50) if is_in_area(item, source)]
    counts = Counter(p for item in items for p in set(field_paths(item)))
    payload = {
        "evenements_analyses": len(items),
        "frequence_des_champs": {k: f"{100 * v / len(items):.0f} %" for k, v in counts.most_common()} if items else {},
        "exemple_brut": items[0] if items else None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Structure brute enregistrée dans {path} ({len(items)} événement(s) analysé(s)).")


def main() -> None:
    """Lance la collecte, prépare les événements et affiche le bilan d'audit.

    Options : `--json` pour enregistrer le bilan, `--brut` pour la structure
    brute de quelques événements, `--decouverte` pour chercher les agendas par
    termes au lieu de la liste `agenda_uids`.
    """
    # Lire les options de la ligne de commande
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="Enregistre aussi le résultat dans ce fichier JSON.")
    parser.add_argument("--brut", type=Path, help="Enregistre la structure brute de quelques événements.")
    parser.add_argument(
        "--decouverte", action="store_true",
        help="Ignore agenda_uids et recherche les agendas par termes (lent, ~6 min) "
             "pour repérer de nouveaux agendas utiles.",
    )
    args = parser.parse_args()

    # Collecter (agendas configurés ou découverte), puis préparer comme la nuit
    source = OPENAGENDA_EXTRACTOR.source
    configured = {str(uid) for uid in source.get("agenda_uids") or []}
    if args.decouverte:
        source = {**source, "agenda_uids": []}
        result = extract_articles_from_source(source)
    else:
        result = extract_all_articles()
    events, report = prepare_events(result.articles)
    indicators = compute_indicators(events, str(source.get("zone") or "Montpellier"))
    agendas = Counter(
        str((article.get("metadata") or {}).get("agenda_uid", "")) for article in result.articles
    )

    # Assembler le bilan, sans la description de l'exemple (trop longue)
    summary = {
        "date": datetime.now(PARIS).isoformat(timespec="seconds"),
        "statut_extraction": result.status,
        "plafond_evenements": source.get("max_articles"),
        "plafond_agendas": source.get("max_agendas"),
        "articles_extraits": len(result.articles),
        "plafond_atteint": len(result.articles) >= int(source.get("max_articles") or 0) * 0.95,
        "preparation": report.to_dict(),
        "agendas_utiles": dict(agendas.most_common(20)),
        # En mode découverte : agendas utiles absents de agenda_uids, à ajouter
        # dans config/sources.yaml.
        "nouveaux_agendas": [uid for uid, _ in agendas.most_common() if uid and uid not in configured],
        "indicateurs": indicators,
        "exemple": events[0].to_dict() if events else None,
    }
    if summary["exemple"]:
        summary["exemple"].pop("description", None)

    # Afficher, puis enregistrer ce qui a été demandé
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.brut:
        dump_raw_structure(dict(source), [uid for uid, _ in agendas.most_common(3) if uid], args.brut)
    if args.json:
        args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nRésultat enregistré dans {args.json}")


if __name__ == "__main__":
    main()
