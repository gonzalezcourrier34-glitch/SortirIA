"""Écriture des événements dans PostgreSQL et retrait des événements passés.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Il suit la
logique de `src/storage/postgres/postgres_storage.py` de CheckIt.AI (fonctions
qui reçoivent une connexion ouverte), mais pour la table `sortiria.events`
(voir `sql/sortiria/02_events.sql` et `03_collect.sql`).

Les fonctions reçoivent une connexion « DB-API » (psycopg2 en pratique) : elles
n'ouvrent rien elles-mêmes, ce qui permet de les tester avec une fausse
connexion et de garder la transaction sous le contrôle de l'appelant.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time
from typing import Any

from src.event.event_embedding import to_pgvector
from src.event.event_normalizer import PARIS
from src.event.event_schema import Event

EVENT_COLUMNS: tuple[str, ...] = (
    "event_id", "source", "source_event_id", "title", "description", "category",
    "keywords", "language", "start_at", "end_at", "time_known", "venue_name",
    "address", "city", "postal_code", "latitude", "longitude", "price_text",
    "price_min", "price_max", "is_free", "status", "attendance", "age_min",
    "age_max", "audiences", "accessibility", "partner_id", "is_sponsored",
    "url", "image_url", "extracted_at", "source_credit", "source_updated_at",
)

# Colonnes mises à jour quand l'événement existe déjà (toutes sauf la clé).
_UPDATED_COLUMNS = [column for column in EVENT_COLUMNS if column != "event_id"]

UPSERT_EVENT_SQL = f"""
INSERT INTO sortiria.events ({", ".join(EVENT_COLUMNS)})
VALUES ({", ".join(f"%({column})s" for column in EVENT_COLUMNS)})
ON CONFLICT (event_id) DO UPDATE SET
    {", ".join(f"{column} = EXCLUDED.{column}" for column in _UPDATED_COLUMNS)},
    last_seen_at = now(),
    -- Si le texte change, l'ancien embedding ne correspond plus : on l'efface
    -- pour qu'il soit recalculé à la prochaine étape d'indexation.
    embedding = CASE
        WHEN sortiria.events.title IS DISTINCT FROM EXCLUDED.title
          OR sortiria.events.description IS DISTINCT FROM EXCLUDED.description
        THEN NULL
        ELSE sortiria.events.embedding
    END
"""

# Un événement à heure connue disparaît une fois terminé ; un événement
# « jour seulement » reste visible jusqu'à la fin de son dernier jour.
DELETE_PAST_EVENTS_SQL = """
DELETE FROM sortiria.events
WHERE (time_known AND COALESCE(end_at, start_at) < %(now)s)
   OR (NOT time_known AND COALESCE(end_at, start_at) < %(today_start)s)
"""

SELECT_MISSING_EMBEDDINGS_SQL = """
SELECT event_id, title, description, category, venue_name, city
FROM sortiria.events
WHERE embedding IS NULL
ORDER BY start_at
LIMIT %(limit)s
"""

UPDATE_EMBEDDING_SQL = """
UPDATE sortiria.events
SET embedding = %(embedding)s::vector, embedding_model = %(model)s
WHERE event_id = %(event_id)s
"""

INSERT_COLLECT_RUN_SQL = """
INSERT INTO sortiria.collect_runs (
    started_at, finished_at, status, events_extracted, events_rejected,
    duplicates_merged, events_upserted, events_expired, events_removed,
    rejection_reasons, failed_sources, message
)
VALUES (
    COALESCE(%(started_at)s::timestamptz, now()), clock_timestamp(), %(status)s,
    %(events_extracted)s, %(events_rejected)s, %(duplicates_merged)s,
    %(events_upserted)s, %(events_expired)s, %(events_removed)s,
    %(rejection_reasons)s, %(failed_sources)s, %(message)s
)
RETURNING run_id
"""

# Un événement revu cette nuit a last_seen_at = now() (début de la
# transaction, voir UPSERT_EVENT_SQL) : ceux qui ont une date plus ancienne
# ont disparu de leur source (retirés, ou désormais refusés par la validation).
DELETE_UNSEEN_EVENTS_SQL = """
DELETE FROM sortiria.events
WHERE source = ANY(%(sources)s) AND last_seen_at < now()
"""


INSERT_RUN_SOURCE_SQL = """
INSERT INTO sortiria.collect_run_sources (
    run_id, source_id, status, attempts, events_kept, error
)
VALUES (%(run_id)s, %(source_id)s, %(status)s, %(attempts)s, %(events_kept)s, %(error)s)
ON CONFLICT (run_id, source_id) DO UPDATE SET
    status = EXCLUDED.status,
    attempts = EXCLUDED.attempts,
    events_kept = EXCLUDED.events_kept,
    error = EXCLUDED.error
"""


def event_to_row(event: Event) -> dict[str, Any]:
    """Prépare les paramètres SQL d'un événement (les dates vides deviennent NULL)."""

    row = {column: getattr(event, column) for column in EVENT_COLUMNS}
    for column in ("end_at", "extracted_at", "source_updated_at"):
        row[column] = row[column] or None
    return row


def upsert_events(connection: Any, events: Iterable[Event]) -> int:
    """Insère ou met à jour des événements. Renvoie le nombre de lignes envoyées."""

    rows = [event_to_row(event) for event in events]
    if not rows:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(UPSERT_EVENT_SQL, rows)
    return len(rows)


def delete_unseen_events(connection: Any, sources: Sequence[str]) -> int:
    """Supprime les événements de ces sources qui n'ont pas été revus.

    À appeler dans la même transaction que `upsert_events`, et seulement pour
    les sources lues sans échec (voir `sources_safe_to_sweep` dans l'agent).
    """

    if not sources:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(DELETE_UNSEEN_EVENTS_SQL, {"sources": list(sources)})
        return max(cursor.rowcount or 0, 0)


def delete_past_events(connection: Any, now: datetime | None = None) -> int:
    """Supprime les événements terminés. Renvoie le nombre de lignes supprimées."""

    now = (now or datetime.now(PARIS)).astimezone(PARIS)
    today_start = datetime.combine(now.date(), time.min, tzinfo=PARIS)
    with connection.cursor() as cursor:
        cursor.execute(DELETE_PAST_EVENTS_SQL, {"now": now, "today_start": today_start})
        return max(cursor.rowcount or 0, 0)


def fetch_events_missing_embedding(connection: Any, limit: int = 500) -> list[dict[str, Any]]:
    """Liste les événements dont l'embedding reste à calculer."""

    with connection.cursor() as cursor:
        cursor.execute(SELECT_MISSING_EMBEDDINGS_SQL, {"limit": limit})
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def update_embeddings(
    connection: Any,
    embeddings: Iterable[tuple[str, Sequence[float]]],
    model: str,
) -> int:
    """Enregistre les embeddings calculés. Renvoie le nombre d'événements mis à jour."""

    rows = [
        {"event_id": event_id, "embedding": to_pgvector(vector), "model": model}
        for event_id, vector in embeddings
    ]
    if not rows:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(UPDATE_EMBEDDING_SQL, rows)
    return len(rows)


def record_collect_run(connection: Any, report: Mapping[str, Any]) -> int:
    """Enregistre le bilan d'une collecte nocturne. Renvoie l'identifiant créé."""

    params = {
        "status": report.get("status", "success"),
        "events_extracted": int(report.get("events_extracted", 0)),
        "events_rejected": int(report.get("events_rejected", 0)),
        "duplicates_merged": int(report.get("duplicates_merged", 0)),
        "events_upserted": int(report.get("events_upserted", 0)),
        "events_expired": int(report.get("events_expired", 0)),
        "events_removed": int(report.get("events_removed", 0)),
        "started_at": report.get("started_at") or None,
        "rejection_reasons": json.dumps(dict(report.get("rejection_reasons", {})), ensure_ascii=False),
        "failed_sources": list(report.get("failed_sources", [])),
        "message": str(report.get("message", "")),
    }
    with connection.cursor() as cursor:
        cursor.execute(INSERT_COLLECT_RUN_SQL, params)
        return int(cursor.fetchone()[0])


def record_run_sources(connection: Any, run_id: int, rows: Iterable[Mapping[str, Any]]) -> int:
    """Enregistre le détail par source d'une collecte. Renvoie le nombre de lignes.

    Chaque ligne : `source_id`, `status` (« ok » ou « echec »), `attempts`,
    `events_kept` et `error` (facultatif).
    """

    params = [
        {
            "run_id": run_id,
            "source_id": str(row["source_id"]),
            "status": str(row.get("status", "ok")),
            "attempts": int(row.get("attempts", 1)),
            "events_kept": int(row.get("events_kept", 0)),
            "error": str(row.get("error", "")),
        }
        for row in rows
    ]
    if not params:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(INSERT_RUN_SOURCE_SQL, params)
    return len(params)
