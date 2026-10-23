"""Détail d'un événement et avis des visiteurs sur son lieu.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

La fiche détaillée d'un événement (description, autres dates, derniers
commentaires publiés sur le lieu, avis du visiteur), la fenêtre de notation
(du début de l'événement à 7 jours après sa fin) et « Vos sorties à noter ».
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.search_cards import (
    MAX_OTHER_DATES,
    date_label,
    event_date_label,
    other_dates,
    place_label,
    sibling_rows,
    to_card,
)
from app.search_filters import PARIS
from app.search_query import EVENT_COLUMNS, fetch_rows

RATING_WINDOW_DAYS = 7   # avis possible du début de l'événement à 7 jours après sa fin

DETAIL_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.event_id = %(event_id)s"""

# Événement passé depuis moins de 7 jours : plus proposé, mais encore notable
# (bouton « Donner mon avis », 15/10/2026). Mêmes colonnes que la vue.
RECENT_DETAIL_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM (
    SELECT e.*, s.name AS source_name, s.attribution AS source_attribution,
           vr.average_stars AS venue_rating, vr.ratings AS venue_rating_count
    FROM sortiria.events AS e
    JOIN sortiria.sources AS s ON s.source_id = e.source
    LEFT JOIN sortiria.v_venue_ratings AS vr
           ON vr.city_key = sortiria.normalize_city(e.city)
          AND vr.venue_key = lower(btrim(e.venue_name))
) AS e
LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.event_id = %(event_id)s
  AND e.start_at <= now()
  AND COALESCE(e.end_at, e.start_at) >= now() - interval '{RATING_WINDOW_DAYS} days'"""

# Parmi les événements consultés par un visiteur, ceux qu'il peut noter
# maintenant et qu'il n'a pas encore notés (« Vos sorties à noter »).
TO_RATE_SQL = f"""
SELECT e.event_id, e.title, e.start_at, e.end_at, e.time_known, e.venue_name, e.city
FROM sortiria.events AS e
WHERE e.event_id = ANY (%(event_ids)s)
  AND e.start_at <= now()
  AND COALESCE(e.end_at, e.start_at) >= now() - interval '{RATING_WINDOW_DAYS} days'
  AND NOT EXISTS (SELECT 1 FROM sortiria.event_ratings AS r
                  WHERE r.event_id = e.event_id AND r.user_key = %(user_key)s)
ORDER BY e.start_at DESC
LIMIT %(limit)s"""

# Commentaires publiés sur le lieu de l'événement (tous événements confondus) :
# les notes portent sur le lieu, elles survivent aux événements.
VENUE_COMMENTS_SQL = """
SELECT stars, comment, updated_at, title, event_start
FROM sortiria.event_ratings
WHERE comment_status = 'publie' AND comment <> ''
  AND sortiria.normalize_city(city) = sortiria.normalize_city(%(city)s)
  AND lower(btrim(venue_name)) = lower(btrim(%(venue)s))
  AND %(venue)s <> ''
ORDER BY updated_at DESC
LIMIT %(limit)s"""

# Nombre de notes sur le lieu, même sous le seuil de 3 où la moyenne s'affiche
# (15/10/2026) : « 1 avis » plutôt que « pas encore d'avis » après avoir noté.
VENUE_RATING_COUNT_SQL = """
SELECT count(*) AS ratings
FROM sortiria.event_ratings
WHERE sortiria.normalize_city(city) = sortiria.normalize_city(%(city)s)
  AND lower(btrim(venue_name)) = lower(btrim(%(venue)s))
  AND %(venue)s <> ''"""

# L'avis du visiteur sur cet événement (empreinte anonyme de son navigateur) :
# il le revoit, avec l'état de son commentaire (publié, en relecture, refusé).
OWN_RATING_SQL = """
SELECT stars, comment, comment_status, updated_at
FROM sortiria.event_ratings
WHERE event_id = %(event_id)s AND user_key = %(user_key)s"""


def event_detail(connection: Any, event_id: str, comments: int = 3,
                 visitor_key: str | None = None) -> dict[str, Any] | None:
    """Détail d'un événement proposable, avec les avis sur son lieu et celui du visiteur.

    Détail d'un événement proposable, avec les derniers avis publiés sur son
    lieu, leur nombre, et l'avis du visiteur (`visitor_key` : empreinte de son
    navigateur) s'il en a donné un. Un événement passé depuis moins de
    `RATING_WINDOW_DAYS` jours est encore trouvé, pour pouvoir le noter.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    event_id : str
        Identifiant de l'événement.
    comments : int
        Nombre de commentaires publiés à joindre. Par défaut 3.
    visitor_key : str | None
        Empreinte du navigateur du visiteur, ou None.

    Returns
    -------
    dict[str, Any] | None
        La carte complétée (fenêtre de notation, autres dates, description,
        commentaires, nombre d'avis, avis du visiteur), ou None si
        l'événement est introuvable.
    """

    # L'événement : proposable, sinon passé récemment
    rows = fetch_rows(connection, DETAIL_SQL, {"event_id": event_id}) or \
        fetch_rows(connection, RECENT_DETAIL_SQL, {"event_id": event_id})
    if not rows:
        return None
    row = rows[0]
    card = to_card(row)
    card.update(rating_window(row))
    card["other_dates"] = other_dates(sibling_rows(connection, row, MAX_OTHER_DATES))
    card["description"] = " ".join(str(row.get("description") or "").split())[:1200]
    # Les avis sur le lieu : derniers commentaires publiés, puis nombre de notes
    card["comments"] = [
        {"stars": int(item["stars"]), "text": str(item["comment"]),
         "date": item["updated_at"].astimezone(PARIS).strftime("%d/%m/%Y"),
         # L'événement noté : l'avis garde son contexte après la suppression de l'événement.
         "event_title": str(item.get("title") or ""),
         "event_date": date_label(item["event_start"], True) if item.get("event_start") else ""}
        for item in fetch_rows(connection, VENUE_COMMENTS_SQL, {
            "city": row.get("city") or "", "venue": row.get("venue_name") or "", "limit": comments})
    ]
    venue = {"city": row.get("city") or "", "venue": row.get("venue_name") or ""}
    counted = fetch_rows(connection, VENUE_RATING_COUNT_SQL, venue)
    card["venue_ratings"] = int(counted[0]["ratings"]) if counted else 0
    # L'avis du visiteur lui-même, s'il en a donné un
    card["my_rating"] = None
    if visitor_key:
        own = fetch_rows(connection, OWN_RATING_SQL,
                         {"event_id": str(row["event_id"]), "user_key": visitor_key})
        if own:
            card["my_rating"] = {
                "stars": int(own[0]["stars"]), "comment": str(own[0]["comment"] or ""),
                "comment_status": str(own[0]["comment_status"]),
                "date": own[0]["updated_at"].astimezone(PARIS).strftime("%d/%m/%Y")}
    return card


def rating_window(row: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Peut-on noter cet événement maintenant ? Sinon, à partir de quand.

    Même règle que la base (UPSERT_RATING_SQL) : du début de l'événement à
    7 jours après sa fin.

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance (`start_at`, `end_at`).
    now : datetime | None
        Heure de référence ; par défaut maintenant.

    Returns
    -------
    dict[str, Any]
        `can_rate` (bool), et `rate_from` : la date d'ouverture si l'événement
        n'a pas commencé, sinon une chaîne vide.
    """

    now = now or datetime.now(PARIS)
    start = row["start_at"]
    last = (row.get("end_at") or start) + timedelta(days=RATING_WINDOW_DAYS)
    return {"can_rate": start <= now <= last,
            "rate_from": date_label(start, False) if start > now else ""}


def events_to_rate(connection: Any, event_ids: Sequence[str], session_id: str,
                   limit: int = 10) -> list[dict[str, Any]]:
    """Événements consultés par ce visiteur qu'il peut noter maintenant.

    Ceux qu'il a déjà notés sont écartés.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    event_ids : Sequence[str]
        Les événements consultés (50 premiers gardés).
    session_id : str
        Identifiant aléatoire du navigateur (changé en empreinte avant la requête).
    limit : int
        Nombre maximum d'événements. Par défaut 10.

    Returns
    -------
    list[dict[str, Any]]
        Pour chaque événement : identifiant, titre, date et lieu affichés.
    """

    from src.event.event_ratings import user_key

    ids = [str(event_id) for event_id in event_ids][:50]
    if not ids:
        return []
    rows = fetch_rows(connection, TO_RATE_SQL, {"event_ids": ids, "user_key": user_key(session_id),
                                                "limit": limit})
    return [{"event_id": str(row["event_id"]), "title": str(row["title"]),
             "date": event_date_label(row), "place": place_label(row)}
            for row in rows]
