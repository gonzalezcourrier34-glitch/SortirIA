"""Surveillance de la collecte : ce que l'agent vérifie après le chargement.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), décisions
ajoutées à l'agent après le branchement des programmes PDF et des fichiers des
partenaires. Ce sont des fonctions pures (pas de réseau, pas de base) : elles
reçoivent ce que l'agent sait et rendent des alertes en français, reprises
dans le mail de bilan.

Quatre questions, posées chaque nuit :

1. **Un programme PDF arrive-t-il à son terme ?** Le programme de l'Agglopôle
   couvre septembre-décembre : sans nouveau document, la source s'éteint en
   silence au 1er janvier. Alerte trois semaines avant.
2. **Un partenaire est-il devenu inactif ?** Fichier sans séance à venir, ou
   pas mis à jour depuis 30 jours : il faut le relancer (suivi commercial).
3. **La promesse du service est-elle tenue ?** Des sorties « famille » et
   « soirée », à Montpellier et sur le bassin de Thau : on compte celles des
   7 prochains jours pour chacune des quatre cases.
4. **Combien a coûté la nuit ?** Tokens Mistral (lecture des PDF, embeddings)
   et estimation en dollars, avec une alerte au-delà du budget.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from src.event.event_normalizer import normalize_for_comparison, parse_event_datetime

# ---------------------------------------------------------------------------
# Réglages (calibrage du 14/10/2026)
# ---------------------------------------------------------------------------

DOCUMENT_EXPIRY_WARNING_DAYS = 21      # alerte 3 semaines avant la fin d'un programme
PARTNER_STALE_DAYS = 30                # fichier partenaire non mis à jour depuis 30 jours
COVERAGE_DAYS = 7                      # on regarde la semaine à venir
COVERAGE_MIN_EVENTS = 10               # en dessous : la case est jugée trop pauvre
AUDIENCES = ("famille", "soiree")
ZONES = ("montpellier", "bassin_de_thau")
ZONE_LABELS = {"montpellier": "Montpellier", "bassin_de_thau": "bassin de Thau"}
AUDIENCE_LABELS = {"famille": "famille", "soiree": "soirée"}

# Communes de Montpellier Méditerranée Métropole (31) et de Sète agglopôle
# méditerranée (14). Un événement sans coordonnées GPS (PDF, partenaires) est
# rangé d'après sa commune.
MONTPELLIER_COMMUNES = frozenset(normalize_for_comparison(name) for name in (
    "Montpellier", "Baillargues", "Beaulieu", "Castelnau-le-Lez", "Castries", "Clapiers", "Cournonsec",
    "Cournonterral", "Fabrègues", "Grabels", "Jacou", "Juvignac", "Lattes", "Lavérune", "Le Crès", "Montaud",
    "Montferrier-sur-Lez", "Murviel-lès-Montpellier", "Pérols", "Pignan", "Prades-le-Lez", "Restinclières",
    "Saint-Brès", "Saint-Drézéry", "Saint-Geniès-des-Mourgues", "Saint-Georges-d'Orques", "Saint-Jean-de-Védas",
    "Saussan", "Sussargues", "Vendargues", "Villeneuve-lès-Maguelone",
))
THAU_COMMUNES = frozenset(normalize_for_comparison(name) for name in (
    "Sète", "Frontignan", "Balaruc-les-Bains", "Balaruc-le-Vieux", "Bouzigues", "Gigean", "Loupian",
    "Marseillan", "Mèze", "Mireval", "Montbazin", "Poussan", "Vic-la-Gardiole", "Villeveyrac",
))
# Centres et rayons, comme la vue sortiria.v_coverage (05_views.sql).
ZONE_CENTERS = {"montpellier": (43.6108, 3.8767, 20.0), "bassin_de_thau": (43.42, 3.62, 15.0)}

# Tarifs Mistral en dollars par million de tokens (entrée, sortie), relevés
# en 2026 et à vérifier sur la page des tarifs : l'estimation sert d'ordre de
# grandeur, pas de facture.
PRICES_USD_PER_MILLION = {
    "mistral-embed": (0.10, 0.0),
    "mistral-small-latest": (0.10, 0.30),
    "ministral-8b-latest": (0.10, 0.10),
    "mistral-medium-latest": (0.40, 2.00),   # juge de qualité
    "mistral-large-latest": (0.50, 1.50),    # juge de pertinence (scripts/eval_pertinence.py, docs/couts.md)
}
DEFAULT_PRICE = (0.40, 2.00)          # modèle inconnu : prudence (prix d'un grand modèle)
DEFAULT_NIGHTLY_BUDGET_USD = 0.50


# ---------------------------------------------------------------------------
# 1. Programmes PDF
# ---------------------------------------------------------------------------

def check_documents(documents: Iterable[Mapping[str, Any]], today: date) -> list[str]:
    """Alertes pour les programmes terminés ou proches de leur fin.

    `documents` : nom et fin de période de chaque programme configuré
    (`{"name": ..., "end": date}`).

    Un programme sans date de fin lisible est ignoré ; l'alerte part
    `DOCUMENT_EXPIRY_WARNING_DAYS` jours (21) avant la fin.

    Parameters
    ----------
    documents : Iterable[Mapping[str, Any]]
        Les programmes PDF configurés, avec leur nom et leur fin de période.
    today : date
        Date du jour.

    Returns
    -------
    list[str]
        Une alerte par programme terminé ou proche de sa fin.
    """

    alerts = []
    for document in documents:
        end = document.get("end")
        if not isinstance(end, date):
            continue
        days_left = (end - today).days
        if days_left < 0:
            alerts.append(f"Programme « {document['name']} » terminé depuis le {end:%d/%m/%Y} : "
                          "ajouter le suivant dans config/sources.yaml (document_sources).")
        elif days_left <= DOCUMENT_EXPIRY_WARNING_DAYS:
            alerts.append(f"Programme « {document['name']} » : se termine le {end:%d/%m/%Y} "
                          f"(dans {days_left} jour(s)), chercher le prochain.")
    return alerts


# ---------------------------------------------------------------------------
# 2. Partenaires
# ---------------------------------------------------------------------------

def check_partners(
    files: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    today: date,
) -> list[str]:
    """Alertes pour les partenaires à relancer.

    `files` : un élément par fichier déposé (`name`, `file_name`, `updated`
    = date de dernière modification). Les séances à venir sont comptées parmi
    les événements de la nuit, d'après `partner_info.file_name`.

    Parameters
    ----------
    files : Iterable[Mapping[str, Any]]
        Les fichiers déposés par les partenaires.
    events : Iterable[Mapping[str, Any]]
        Les événements préparés cette nuit.
    today : date
        Date du jour.

    Returns
    -------
    list[str]
        Une alerte par fichier sans séance à venir ou non mis à jour depuis
        plus de `PARTNER_STALE_DAYS` jours (30).
    """

    # Compter les séances à venir de chaque fichier partenaire
    upcoming: dict[str, int] = {}
    for event in events:
        info = event.get("partner_info") or {}
        if info.get("file_name"):
            upcoming[info["file_name"]] = upcoming.get(info["file_name"], 0) + 1
    # Alerter : plus de séance d'abord, sinon fichier trop ancien
    alerts = []
    for item in files:
        name, file_name = item.get("name") or item.get("file_name"), item.get("file_name")
        updated = item.get("updated")
        if not upcoming.get(file_name):
            alerts.append(f"Partenaire « {name} » : plus aucune séance à venir dans {file_name}, le relancer.")
        elif isinstance(updated, date) and (today - updated).days > PARTNER_STALE_DAYS:
            alerts.append(f"Partenaire « {name} » : {file_name} non mis à jour depuis "
                          f"{(today - updated).days} jours, demander son nouveau programme.")
    return alerts


# ---------------------------------------------------------------------------
# 3. Couverture : publics x zones, semaine à venir
# ---------------------------------------------------------------------------

def zone_of(event: Mapping[str, Any]) -> str:
    """Zone d'un événement : d'après sa commune, sinon d'après ses coordonnées.

    Parameters
    ----------
    event : Mapping[str, Any]
        L'événement (`city`, `latitude`, `longitude`).

    Returns
    -------
    str
        `montpellier`, `bassin_de_thau` ou `hors_zone`.
    """

    city = normalize_for_comparison(event.get("city"))
    if city in MONTPELLIER_COMMUNES:
        return "montpellier"
    if city in THAU_COMMUNES:
        return "bassin_de_thau"
    latitude, longitude = event.get("latitude"), event.get("longitude")
    if latitude is not None and longitude is not None:
        from src.utils.geo_utils import distance_km

        for zone, (lat, lon, radius) in ZONE_CENTERS.items():
            if distance_km(lat, lon, float(latitude), float(longitude)) <= radius:
                return zone
    return "hors_zone"


def is_in_week(event: Mapping[str, Any], today: date, days: int = COVERAGE_DAYS) -> bool:
    """L'événement a-t-il lieu (au moins un jour) dans les `days` prochains jours ?

    Parameters
    ----------
    event : Mapping[str, Any]
        L'événement (`start_at`, `end_at` facultatif).
    today : date
        Date du jour.
    days : int
        Largeur de la fenêtre. Par défaut 7 jours.

    Returns
    -------
    bool
        True si la période de l'événement croise la fenêtre ; False sans date de début.
    """

    start = parse_event_datetime(event.get("start_at"))
    if start is None:
        return False
    end = parse_event_datetime(event.get("end_at")) or start
    return start.date() <= today + timedelta(days=days) and end.date() >= today


def coverage(events: Iterable[Mapping[str, Any]], today: date) -> dict[str, dict[str, int]]:
    """Nombre de sorties de la semaine par zone et par public : {zone: {public: n}}.

    Parameters
    ----------
    events : Iterable[Mapping[str, Any]]
        Les événements à compter.
    today : date
        Date du jour (début de la semaine regardée).

    Returns
    -------
    dict[str, dict[str, int]]
        Les quatre cases (Montpellier / Thau × famille / soirée), à 0 si vides.
    """

    counts = {zone: dict.fromkeys(AUDIENCES, 0) for zone in ZONES}
    for event in events:
        zone = zone_of(event)
        if zone not in counts or not is_in_week(event, today):
            continue
        for audience in event.get("audiences") or []:
            if audience in counts[zone]:
                counts[zone][audience] += 1
    return counts


def coverage_minimum() -> int:
    """Seuil d'alerte de couverture (`COUVERTURE_MIN_SEMAINE` dans .env).

    10 par défaut ; 0 : désactivé.

    Returns
    -------
    int
        Le seuil ; la valeur par défaut si la variable n'est pas un entier.
    """

    try:
        return int(os.getenv("COUVERTURE_MIN_SEMAINE", COVERAGE_MIN_EVENTS))
    except ValueError:
        return COVERAGE_MIN_EVENTS


def check_coverage(counts: Mapping[str, Mapping[str, int]], minimum: int | None = None) -> list[str]:
    """Alertes pour les cases trop pauvres (ex. moins de 10 sorties « soirée » sur Thau).

    Parameters
    ----------
    counts : Mapping[str, Mapping[str, int]]
        La couverture calculée par `coverage`.
    minimum : int | None
        Seuil d'alerte ; None : celui de `coverage_minimum`.

    Returns
    -------
    list[str]
        Une alerte par case sous le seuil.
    """

    minimum = coverage_minimum() if minimum is None else minimum
    alerts = []
    for zone in ZONES:
        for audience in AUDIENCES:
            count = counts.get(zone, {}).get(audience, 0)
            if count < minimum:
                alerts.append(f"Couverture faible : {count} sortie(s) « {AUDIENCE_LABELS[audience]} » "
                              f"à {ZONE_LABELS[zone]} dans les {COVERAGE_DAYS} prochains jours "
                              f"(minimum souhaité : {minimum}).")
    return alerts


# ---------------------------------------------------------------------------
# 4. Coût Mistral
# ---------------------------------------------------------------------------

def estimate_tokens(texts: Iterable[str]) -> int:
    """Tokens estimés d'une liste de textes (environ 4 caractères par token en français).

    L'API d'embeddings utilisée ne renvoie pas le nombre exact : c'est une
    estimation, indiquée comme telle dans le bilan.

    Parameters
    ----------
    texts : Iterable[str]
        Les textes envoyés à l'API.

    Returns
    -------
    int
        Nombre de tokens estimé.
    """

    return sum(len(text) for text in texts) // 4


def nightly_cost(usage: Mapping[str, Mapping[str, int]]) -> float:
    """Coût estimé en dollars : `usage` = {modèle: {"input": n, "output": n}}.

    Un modèle absent de `PRICES_USD_PER_MILLION` est compté au prix d'un grand modèle.

    Parameters
    ----------
    usage : Mapping[str, Mapping[str, int]]
        Tokens consommés par modèle.

    Returns
    -------
    float
        Coût estimé, arrondi à 4 décimales.
    """

    total = 0.0
    for model, tokens in usage.items():
        price_in, price_out = PRICES_USD_PER_MILLION.get(model, DEFAULT_PRICE)
        total += tokens.get("input", 0) * price_in / 1e6 + tokens.get("output", 0) * price_out / 1e6
    return round(total, 4)


def nightly_budget() -> float:
    """Budget d'une nuit en dollars (`MISTRAL_BUDGET_NUIT_USD` dans .env, 0,50 par défaut).

    Returns
    -------
    float
        Le budget ; la valeur par défaut si la variable n'est pas un nombre.
    """

    try:
        return float(os.getenv("MISTRAL_BUDGET_NUIT_USD", DEFAULT_NIGHTLY_BUDGET_USD))
    except ValueError:
        return DEFAULT_NIGHTLY_BUDGET_USD


def check_cost(cost: float, budget: float | None = None) -> list[str]:
    """Alerte si la nuit a coûté plus que le budget.

    Parameters
    ----------
    cost : float
        Coût estimé de la nuit, en dollars.
    budget : float | None
        Budget en dollars ; None : celui de `nightly_budget`.

    Returns
    -------
    list[str]
        Une alerte si le budget est dépassé, sinon une liste vide.
    """

    budget = nightly_budget() if budget is None else budget
    if cost > budget:
        return [f"Coût Mistral de la nuit : {cost:.2f} $ estimés, au-delà du budget de {budget:.2f} $."]
    return []


# ---------------------------------------------------------------------------
# 5. Qualité (juge Mistral, src/event/quality_judge.py)
# ---------------------------------------------------------------------------

DEFAULT_QUALITY_MIN = 0.80


def quality_minimum() -> float:
    """Score de qualité minimal (`QUALITE_MIN` dans .env, 0,80 par défaut).

    Returns
    -------
    float
        Le score minimal ; la valeur par défaut si la variable n'est pas un nombre.
    """

    try:
        return float(os.getenv("QUALITE_MIN", DEFAULT_QUALITY_MIN))
    except ValueError:
        return DEFAULT_QUALITY_MIN


def check_quality(quality: Mapping[str, Any], minimum: float | None = None) -> list[str]:
    """Alerte si le score de l'échantillon jugé est sous le minimum.

    L'alerte cite au plus trois problèmes relevés par le juge.

    Parameters
    ----------
    quality : Mapping[str, Any]
        Bilan du juge (`score`, `checked`, `problems`).
    minimum : float | None
        Score minimal ; None : celui de `quality_minimum`.

    Returns
    -------
    list[str]
        Une alerte si le score est sous le minimum, sinon une liste vide.
    """

    minimum = quality_minimum() if minimum is None else minimum
    score = quality.get("score")
    if score is None or score >= minimum:
        return []
    problems = "; ".join(quality.get("problems", [])[:3])
    alert = (f"Qualité en baisse : score {score:.0%} sur {quality.get('checked', 0)} événements jugés "
             f"(minimum {minimum:.0%}). Exemples : {problems or 'non précisés'}.")
    return [alert]


# ---------------------------------------------------------------------------
# 6. Taille de la base (plafond de sécurité, 14/10/2026)
# ---------------------------------------------------------------------------

DEFAULT_MAX_EVENTS = 10_000
CAPACITY_WARNING_RATIO = 0.90


def max_events() -> int:
    """Plafond de la base (`SORTIRIA_MAX_EVENEMENTS` dans .env, 10 000 par défaut).

    Returns
    -------
    int
        Le plafond, au moins 1 ; la valeur par défaut si la variable n'est pas un entier.
    """

    try:
        return max(int(os.getenv("SORTIRIA_MAX_EVENEMENTS", DEFAULT_MAX_EVENTS)), 1)
    except ValueError:
        return DEFAULT_MAX_EVENTS


def check_capacity(total: int | None, capped: int, maximum: int | None = None) -> list[str]:
    """Alertes : plafond atteint (événements lointains retirés), ou base presque pleine.

    « Presque pleine » : au moins 90 % du plafond (`CAPACITY_WARNING_RATIO`).

    Parameters
    ----------
    total : int | None
        Taille de la base après la collecte ; None si inconnue.
    capped : int
        Événements retirés cette nuit par le plafond.
    maximum : int | None
        Plafond ; None : celui de `max_events`.

    Returns
    -------
    list[str]
        Au plus une alerte.
    """

    maximum = max_events() if maximum is None else maximum
    if capped:
        alert = (f"Plafond de {maximum} événements atteint : {capped} événement(s) parmi les plus lointains "
                 "retiré(s) (statistiques gardées). Réduire la fenêtre de collecte ou relever le plafond.")
        return [alert]
    if total is not None and total >= maximum * CAPACITY_WARNING_RATIO:
        return [f"Base presque pleine : {total} événements pour un plafond de {maximum} ({total / maximum:.0%})."]
    return []


# ---------------------------------------------------------------------------
# 7. Notes des clients (14/10/2026)
# ---------------------------------------------------------------------------

def check_ratings(ratings: Mapping[str, Any]) -> list[str]:
    """Alertes sur les notes : partenaires mal notés (2,5/5 ou moins sur 5 notes
    au moins) ; sorties mal notées (2/5 ou moins sur 3 notes, 16/10/2026),
    signalées le jour d'une nouvelle note : fiche fausse ou source peu fiable ?

    Parameters
    ----------
    ratings : Mapping[str, Any]
        Bilan des notes (`low_rated_partners`, `low_rated_events`).

    Returns
    -------
    list[str]
        Une alerte par partenaire puis par sortie mal notés.
    """

    alerts = [
        f"Partenaire « {item['partner']} » mal noté par les clients : {float(item['average']):.1f}/5 "
        f"sur {item['ratings']} notes, à contacter."
        for item in ratings.get("low_rated_partners") or []
    ]
    for item in ratings.get("low_rated_events") or []:
        place = ", ".join(str(part) for part in (item.get("venue"), item.get("city")) if part)
        alerts.append(
            f"Sortie mal notée : « {item.get('title')} »" + (f" ({place})" if place else "")
            + f", source {item.get('source') or '?'} : {float(item['average']):.1f}/5 sur {item['ratings']} notes. "
            "Vérifier la fiche (date, lieu, description) et la fiabilité de la source.")
    return alerts


# Bilan de la recherche (15/10/2026) : le lundi, ce que les utilisateurs ont
# cherché sans trouver pendant les 7 derniers jours.
SEARCH_REPORT_WEEKDAY = 0      # lundi
SEARCH_REPORT_DAYS = 7


def search_report_due(today: date) -> bool:
    """Le bilan de la recherche part une fois par semaine, le lundi.

    Parameters
    ----------
    today : date
        Date du jour.

    Returns
    -------
    bool
        True le lundi.
    """

    return today.weekday() == SEARCH_REPORT_WEEKDAY


def merge_usage(*usages: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, int]]:
    """Additionne plusieurs relevés {modèle: {"input": n, "output": n}}.

    Parameters
    ----------
    *usages : Mapping[str, Mapping[str, int]]
        Les relevés à additionner (un relevé vide ou None est ignoré).

    Returns
    -------
    dict[str, dict[str, int]]
        Le relevé total, par modèle.
    """

    merged: dict[str, dict[str, int]] = {}
    for usage in usages:
        for model, tokens in (usage or {}).items():
            slot = merged.setdefault(model, {"input": 0, "output": 0})
            slot["input"] += int(tokens.get("input", 0))
            slot["output"] += int(tokens.get("output", 0))
    return merged
