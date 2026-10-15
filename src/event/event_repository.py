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
from datetime import datetime, time, timedelta
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
# « audiences » est traitée à part : publics de la source + publics vus par Mistral.
_UPDATED_COLUMNS = [column for column in EVENT_COLUMNS if column not in ("event_id", "audiences")]

UPSERT_EVENT_SQL = f"""
INSERT INTO sortiria.events ({", ".join(EVENT_COLUMNS)})
VALUES ({", ".join(f"%({column})s" for column in EVENT_COLUMNS)})
ON CONFLICT (event_id) DO UPDATE SET
    {", ".join(f"{column} = EXCLUDED.{column}" for column in _UPDATED_COLUMNS)},
    -- Les publics détectés chaque nuit (mots-clés) s'ajoutent à ceux de
    -- l'enrichissement Mistral au lieu de les effacer.
    audiences = ARRAY(SELECT DISTINCT unnest(EXCLUDED.audiences || sortiria.events.audiences_llm) ORDER BY 1),
    last_seen_at = now(),
    -- Texte modifié : l'enrichissement est à refaire.
    enriched_at = CASE
        WHEN sortiria.events.title IS DISTINCT FROM EXCLUDED.title
          OR sortiria.events.description IS DISTINCT FROM EXCLUDED.description
        THEN NULL
        ELSE sortiria.events.enriched_at
    END,
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
# Suppressions avec statistiques (14/10/2026) : chaque suppression ajoute ses
# comptes à sortiria.deleted_event_stats DANS LA MÊME REQUÊTE (CTE). On ne
# garde pas les fiches supprimées, seulement combien, d'où, quoi, pour qui.
# {condition} est remplacé par la clause de chaque motif (texte fixe du code).
DELETE_WITH_STATS_SQL = """
WITH deleted AS (
    DELETE FROM sortiria.events
    WHERE {condition}
    RETURNING source, category_norm, latitude, longitude, city, audiences
), counted AS (
    INSERT INTO sortiria.deleted_event_stats AS stats
        (day, source_id, reason, category, zone, events, family, night)
    SELECT (now() AT TIME ZONE 'Europe/Paris')::DATE, source, %(reason)s,
           COALESCE(NULLIF(category_norm, ''), 'non_classe'),
           sortiria.event_zone(latitude, longitude, city),
           count(*),
           count(*) FILTER (WHERE 'famille' = ANY (audiences)),
           count(*) FILTER (WHERE 'soiree' = ANY (audiences))
    FROM deleted
    GROUP BY 1, 2, 3, 4, 5
    ON CONFLICT (day, source_id, reason, category, zone) DO UPDATE SET
        events = stats.events + EXCLUDED.events,
        family = stats.family + EXCLUDED.family,
        night = stats.night + EXCLUDED.night
)
SELECT count(*) FROM deleted
"""

# Un événement passé reste 7 jours en base (sans être proposé : voir la vue
# v_upcoming_events) pour que les clients puissent le noter (event_ratings).
RATING_WINDOW_DAYS = 7
PAST_CONDITION = """(time_known AND COALESCE(end_at, start_at) < %(now)s)
       OR (NOT time_known AND COALESCE(end_at, start_at) < %(today_start)s)"""
# « Disparu » ne concerne que les événements à venir : un événement passé n'est
# plus renvoyé par sa source, c'est normal ; il est gardé 7 jours pour la notation
# puis retiré par PAST_CONDITION (correction du 15/10/2026 : avant, un événement
# de la veille était supprimé dès la nuit suivante, motif « disparu »).
UNSEEN_CONDITION = """source = ANY(%(sources)s) AND last_seen_at < now()
       AND COALESCE(end_at, start_at) >= now()"""
# Plafond : au-delà de %(maximum)s événements, les plus lointains partent d'abord.
CAP_CONDITION = """event_id IN (
        SELECT event_id FROM sortiria.events
        ORDER BY start_at DESC
        LIMIT GREATEST((SELECT count(*) FROM sortiria.events) - %(maximum)s, 0))"""

DELETE_PAST_EVENTS_SQL = DELETE_WITH_STATS_SQL.format(condition=PAST_CONDITION)
DELETE_UNSEEN_EVENTS_SQL = DELETE_WITH_STATS_SQL.format(condition=UNSEEN_CONDITION)
DELETE_OVER_CAP_SQL = DELETE_WITH_STATS_SQL.format(condition=CAP_CONDITION)

# Plafond de sécurité de la base (14/10/2026).
DEFAULT_MAX_EVENTS = 10_000

SELECT_MISSING_EMBEDDINGS_SQL = """
SELECT event_id, title, description, category, venue_name, city, category_norm, summary
FROM sortiria.events
WHERE embedding IS NULL
  AND COALESCE(end_at, start_at) >= now()   -- un événement passé n'est plus proposé : on ne paie pas pour lui
ORDER BY start_at
LIMIT %(limit)s
"""

# Événements à enrichir : jamais enrichis, texte modifié, ou consigne changée.
# Les plus proches d'abord (ce sont ceux que le RAG proposera en premier).
SELECT_TO_ENRICH_SQL = """
SELECT event_id, title, description, category, venue_name, city, age_min, age_max,
       is_free, price_min, price_max
FROM sortiria.events
WHERE (enriched_at IS NULL OR enrichment_version <> %(version)s)
  AND COALESCE(end_at, start_at) >= now()   -- idem : les 7 jours de notation ne justifient pas un enrichissement
ORDER BY start_at
LIMIT %(limit)s
"""

# L'embedding est effacé : il sera recalculé avec la catégorie et le résumé.
UPDATE_ENRICHMENT_SQL = """
UPDATE sortiria.events
SET category_norm = %(category_norm)s,
    summary = %(summary)s,
    price_band = %(price_band)s,
    audiences_llm = %(audiences_llm)s,
    audiences = ARRAY(SELECT DISTINCT unnest(audiences || %(audiences_llm)s::TEXT[]) ORDER BY 1),
    is_outing = %(is_outing)s,
    enriched_at = now(),
    enrichment_version = %(version)s,
    embedding = NULL
WHERE event_id = %(event_id)s
"""

# Échantillon pour le juge de qualité : des événements proposables, tirés au hasard.
SELECT_QUALITY_SAMPLE_SQL = """
SELECT event_id, title, description, category, category_norm, summary, audiences, venue_name, city
FROM sortiria.v_upcoming_events
ORDER BY random()
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
    rejection_reasons, failed_sources, message, events_embedded, cost_usd,
    llm_usage, coverage, node_durations, alerts, prompt_version, mlflow_run_id,
    quality_score, quality, events_capped, events_total
)
VALUES (
    COALESCE(%(started_at)s::timestamptz, now()), clock_timestamp(), %(status)s,
    %(events_extracted)s, %(events_rejected)s, %(duplicates_merged)s,
    %(events_upserted)s, %(events_expired)s, %(events_removed)s,
    %(rejection_reasons)s, %(failed_sources)s, %(message)s, %(events_embedded)s,
    %(cost_usd)s, %(llm_usage)s, %(coverage)s, %(node_durations)s, %(alerts)s,
    %(prompt_version)s, %(mlflow_run_id)s, %(quality_score)s, %(quality)s,
    %(events_capped)s, %(events_total)s
)
RETURNING run_id
"""

# Un événement revu cette nuit a last_seen_at = now() (début de la
# transaction, voir UPSERT_EVENT_SQL) : ceux qui ont une date plus ancienne
# ont disparu de leur source (retirés, ou désormais refusés par la validation).


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


# Événements retenus par source lors de la dernière collecte où elle a réussi.
# Référence pour détecter une baisse anormale : le MEILLEUR des 7 derniers
# bilans « ok » de la source, pas seulement le dernier. Sinon, une source tombée
# à 20 % une nuit (bilan « ok » avec 184 événements) servait de référence la
# nuit suivante, et ses événements étaient balayés à la 3e nuit (15/10/2026).
SELECT_PREVIOUS_SOURCE_COUNTS_SQL = """
SELECT source_id, max(events_kept)
FROM (
    SELECT source_id, events_kept,
           row_number() OVER (PARTITION BY source_id ORDER BY run_id DESC) AS recency
    FROM sortiria.collect_run_sources
    WHERE status = 'ok'
) AS recent
WHERE recency <= 7
GROUP BY source_id
"""

# Couverture de la semaine à venir, par zone et par public, d'après la base
# (publics de Mistral compris) : alimente la surveillance (agent/watch.py).
SELECT_WEEKLY_COVERAGE_SQL = """
SELECT sortiria.event_zone(latitude, longitude, city) AS zone, audience, count(*)
FROM sortiria.v_upcoming_events, unnest(audiences) AS audience
WHERE start_at < now() + make_interval(days => %(days)s)
GROUP BY 1, 2
"""

PURGE_OLD_DATA_SQL = "SELECT requests_deleted, cache_deleted, runs_deleted FROM sortiria.purge_old_data()"

# Vocabulaire des titres (15/10/2026, sql/sortiria/07_vocabulary.sql) : les
# sujets que la recherche reconnaît, recalculés après chaque collecte.
REFRESH_TITLE_VOCABULARY_SQL = "SELECT sortiria.refresh_title_vocabulary()"

# Bilan de la recherche (15/10/2026) : ce que les utilisateurs ont cherché sans
# trouver, d'après le journal `rag_requests` (champ constraints->'recherche').
RECENT_SEARCHES_CTE = """
WITH recent AS (
    SELECT lower(btrim(question)) AS question, status, constraints -> 'recherche' AS r
    FROM sortiria.rag_requests
    WHERE asked_at >= now() - make_interval(days => %(days)s) AND question <> '(filtres seuls)'
)
"""
SEARCH_GAPS_TOTALS_SQL = RECENT_SEARCHES_CTE + """
SELECT count(*) AS questions,
       count(*) FILTER (WHERE status = 'aucun_resultat' OR (r ->> 'exact') = 'false') AS not_exact,
       count(*) FILTER (WHERE status = 'aucun_resultat' OR (r ->> 'sujet_introuvable') = 'true') AS no_answer
FROM recent
"""
SEARCH_GAPS_WORDS_SQL = RECENT_SEARCHES_CTE + """
SELECT word, count(*) AS times
FROM recent, jsonb_array_elements_text(COALESCE(r -> 'mots_inconnus', '[]'::jsonb)) AS word
GROUP BY word ORDER BY times DESC, word LIMIT %(limit)s
"""
SEARCH_GAPS_QUESTIONS_SQL = RECENT_SEARCHES_CTE + """
SELECT question, count(*) AS times
FROM recent
WHERE status = 'aucun_resultat' OR (r ->> 'exact') = 'false'
GROUP BY question ORDER BY times DESC, question LIMIT %(limit)s
"""

# Partenaires (ajout du 14/10/2026) : la configuration fait foi pour le nom, le
# secteur, le site et la fin du partenariat ; les notes saisies en base restent.
UPSERT_PARTNER_SQL = """
INSERT INTO sortiria.partners (name, sector, website, status, sponsored_until)
VALUES (%(name)s, %(sector)s, %(website)s, 'actif', %(sponsored_until)s)
ON CONFLICT (name) DO UPDATE SET
    sector = EXCLUDED.sector,
    website = EXCLUDED.website,
    status = 'actif',
    sponsored_until = EXCLUDED.sponsored_until
RETURNING partner_id
"""

# Un même fichier (même empreinte) n'est compté qu'une fois par partenaire ;
# relu chaque nuit, son bilan est simplement mis à jour.
UPSERT_PARTNER_IMPORT_SQL = """
INSERT INTO sortiria.partner_imports
    (partner_id, file_name, file_sha256, status, rows_total, rows_loaded, rows_rejected, rejection_reasons)
VALUES
    (%(partner_id)s, %(file_name)s, %(file_sha256)s, %(status)s, %(rows_total)s,
     %(rows_loaded)s, %(rows_rejected)s, %(rejection_reasons)s)
ON CONFLICT (partner_id, file_sha256) DO UPDATE SET
    file_name = EXCLUDED.file_name,
    status = EXCLUDED.status,
    rows_total = EXCLUDED.rows_total,
    rows_loaded = EXCLUDED.rows_loaded,
    rows_rejected = EXCLUDED.rows_rejected,
    rejection_reasons = EXCLUDED.rejection_reasons
"""


def event_to_row(event: Event) -> dict[str, Any]:
    """Prépare les paramètres SQL d'un événement (les dates vides deviennent NULL)."""

    row = {column: getattr(event, column) for column in EVENT_COLUMNS}
    for column in ("end_at", "extracted_at", "source_updated_at"):
        row[column] = row[column] or None
    return row


def import_status(rows_loaded: int, rows_rejected: int) -> str:
    """Statut d'un fichier partenaire : importé, partiel ou refusé."""

    if rows_loaded and not rows_rejected:
        return "importe"
    return "partiel" if rows_loaded else "refuse"


def attach_partners(connection: Any, events: Sequence[Event]) -> int:
    """Enregistre les partenaires et leurs fichiers, et relie leurs événements.

    Pour chaque événement venu d'un fichier partenaire (`partner_info`) :
    le partenaire est créé ou mis à jour dans `partners`, l'événement reçoit
    son `partner_id`, et le bilan du fichier va dans `partner_imports`.
    À appeler avant `upsert_events`, dans la même transaction (un événement
    « mis en avant » doit avoir un partenaire : contrainte de la table).
    Renvoie le nombre d'événements reliés.
    """

    partner_events = [event for event in events if event.partner_info.get("name")]
    if not partner_events:
        return 0
    partner_ids: dict[str, int] = {}
    imports: dict[tuple[int, str], dict[str, Any]] = {}
    with connection.cursor() as cursor:
        for event in partner_events:
            info = event.partner_info
            name = str(info["name"])
            if name not in partner_ids:
                cursor.execute(UPSERT_PARTNER_SQL, {
                    "name": name,
                    "sector": str(info.get("sector") or "autre"),
                    "website": str(info.get("website") or ""),
                    "sponsored_until": str(info.get("sponsored_until") or "")[:10] or None,
                })
                partner_ids[name] = int(cursor.fetchone()[0])
            event.partner_id = partner_ids[name]
            if info.get("file_sha256"):
                loaded, rejected = int(info.get("rows_loaded", 0)), int(info.get("rows_rejected", 0))
                imports[(event.partner_id, str(info["file_sha256"]))] = {
                    "partner_id": event.partner_id,
                    "file_name": str(info.get("file_name", "")),
                    "file_sha256": str(info["file_sha256"]),
                    "status": import_status(loaded, rejected),
                    "rows_total": int(info.get("rows_total", 0)),
                    "rows_loaded": loaded,
                    "rows_rejected": rejected,
                    "rejection_reasons": json.dumps(dict(info.get("rejection_reasons", {})), ensure_ascii=False),
                }
        if imports:
            cursor.executemany(UPSERT_PARTNER_IMPORT_SQL, list(imports.values()))
    return len(partner_events)


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
        cursor.execute(DELETE_UNSEEN_EVENTS_SQL, {"sources": list(sources), "reason": "disparu"})
        return _deleted_count(cursor)


def _deleted_count(cursor: Any) -> int:
    """Nombre de lignes supprimées, rendu par la requête DELETE_WITH_STATS_SQL."""

    row = cursor.fetchone() if getattr(cursor, "description", None) else None
    return int(row[0]) if row else max(cursor.rowcount or 0, 0)


def enforce_event_cap(connection: Any, maximum: int = DEFAULT_MAX_EVENTS) -> int:
    """Ramène la base sous le plafond en supprimant les événements les plus lointains.

    Les plus proches sont ceux que le RAG propose (3 mois au plus) : ce sont
    les derniers à partir. Renvoie le nombre d'événements supprimés.
    """

    with connection.cursor() as cursor:
        cursor.execute(DELETE_OVER_CAP_SQL, {"maximum": int(maximum), "reason": "plafond"})
        return _deleted_count(cursor)


def count_events(connection: Any) -> int:
    """Nombre d'événements en base."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM sortiria.events")
        row = cursor.fetchone()
        return int(row[0]) if row else 0


def delete_past_events(connection: Any, now: datetime | None = None) -> int:
    """Supprime les événements terminés depuis plus de 7 jours (délai de notation).

    Renvoie le nombre de lignes supprimées.
    """

    now = (now or datetime.now(PARIS)).astimezone(PARIS) - timedelta(days=RATING_WINDOW_DAYS)
    today_start = datetime.combine(now.date(), time.min, tzinfo=PARIS)
    with connection.cursor() as cursor:
        cursor.execute(DELETE_PAST_EVENTS_SQL, {"now": now, "today_start": today_start, "reason": "passe"})
        return _deleted_count(cursor)


def fetch_events_missing_embedding(connection: Any, limit: int = 500) -> list[dict[str, Any]]:
    """Liste les événements dont l'embedding reste à calculer."""

    with connection.cursor() as cursor:
        cursor.execute(SELECT_MISSING_EMBEDDINGS_SQL, {"limit": limit})
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def fetch_events_to_enrich(connection: Any, version: str, limit: int = 1500) -> list[dict[str, Any]]:
    """Événements à enrichir par Mistral (nouveaux, modifiés, ou d'une ancienne consigne)."""

    with connection.cursor() as cursor:
        cursor.execute(SELECT_TO_ENRICH_SQL, {"version": version, "limit": limit})
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def update_enrichments(connection: Any, rows: Iterable[Mapping[str, Any]], version: str) -> int:
    """Enregistre les enrichissements. Chaque ligne : event_id, category_norm, summary,
    price_band, audiences_llm, is_outing. Renvoie le nombre de lignes."""

    params = [{**dict(row), "version": version} for row in rows]
    if not params:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(UPDATE_ENRICHMENT_SQL, params)
    return len(params)


def fetch_quality_sample(connection: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Événements proposables tirés au hasard, pour le juge de qualité."""

    with connection.cursor() as cursor:
        cursor.execute(SELECT_QUALITY_SAMPLE_SQL, {"limit": limit})
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


# Une proposition déjà refusée (même source, même valeur proposée) n'est pas
# reproposée : un agenda écarté ne revient pas chaque dimanche.
INSERT_PROPOSAL_SQL = """
INSERT INTO sortiria.agent_proposals (run_id, source_id, kind, diagnosis, action)
SELECT %(run_id)s, %(source_id)s, %(kind)s, %(diagnosis)s, %(action)s::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM sortiria.agent_proposals AS done
    WHERE done.source_id = %(source_id)s AND done.status = 'refusee'
      AND done.action ->> 'new' = %(action)s::jsonb ->> 'new')
ON CONFLICT (source_id, (action ->> 'new')) WHERE status = 'en_attente' DO NOTHING
"""


def record_proposals(connection: Any, run_id: int | None, proposals: Iterable[Mapping[str, Any]]) -> int:
    """Enregistre les réparations proposées par l'agent (une seule fois chacune)."""

    params = [
        {"run_id": run_id, "source_id": str(item.get("source_id", "")), "kind": str(item["kind"]),
         "diagnosis": str(item["diagnosis"]), "action": json.dumps(dict(item["action"]), ensure_ascii=False)}
        for item in proposals
    ]
    if not params:
        return 0
    with connection.cursor() as cursor:
        cursor.executemany(INSERT_PROPOSAL_SQL, params)
    return len(params)


def fetch_proposals(connection: Any, status: str = "en_attente") -> list[dict[str, Any]]:
    """Propositions de l'agent dans un état donné (les plus anciennes d'abord)."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT proposal_id, created_at, source_id, kind, diagnosis, action, status "
            "FROM sortiria.agent_proposals WHERE status = %(status)s ORDER BY proposal_id",
            {"status": status},
        )
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def set_proposal_status(connection: Any, proposal_id: int, status: str) -> int:
    """Change l'état d'une proposition (acceptee, refusee, appliquee). Renvoie 1 si trouvée."""

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE sortiria.agent_proposals SET status = %(status)s, decided_at = now() "
            "WHERE proposal_id = %(id)s",
            {"status": status, "id": proposal_id},
        )
        return max(cursor.rowcount or 0, 0)


# Notes des clients (14/10/2026). L'événement doit exister et être notable :
# commencé, et fini depuis 7 jours au plus. La note garde de quoi noter le
# lieu et le partenaire après la suppression de l'événement.
UPSERT_RATING_SQL = """
INSERT INTO sortiria.event_ratings (
    event_id, user_key, stars, comment, comment_status, moderation,
    source, title, venue_name, city, zone, category, partner_id, event_start
)
SELECT e.event_id, %(user_key)s, %(stars)s, %(comment)s, %(comment_status)s, %(moderation)s,
       e.source, e.title, e.venue_name, e.city, sortiria.event_zone(e.latitude, e.longitude, e.city),
       COALESCE(NULLIF(e.category_norm, ''), e.category), e.partner_id, e.start_at
FROM sortiria.events AS e
WHERE e.event_id = %(event_id)s
  AND e.start_at <= now()
  AND COALESCE(e.end_at, e.start_at) >= now() - interval '7 days'
ON CONFLICT (event_id, user_key) DO UPDATE SET
    stars = EXCLUDED.stars,
    comment = EXCLUDED.comment,
    comment_status = EXCLUDED.comment_status,
    moderation = EXCLUDED.moderation,
    updated_at = now()
RETURNING rating_id
"""


def upsert_rating(connection: Any, rating: Mapping[str, Any]) -> int | None:
    """Enregistre la note d'un client ; None si l'événement n'est pas notable."""

    with connection.cursor() as cursor:
        cursor.execute(UPSERT_RATING_SQL, dict(rating))
        row = cursor.fetchone()
        return int(row[0]) if row else None


def fetch_event_ratings(connection: Any, event_id: str) -> list[dict[str, Any]]:
    """Notes d'un événement (étoiles, commentaire et son statut, date)."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stars, comment, comment_status, updated_at, title, event_start FROM sortiria.event_ratings "
            "WHERE event_id = %(event_id)s ORDER BY updated_at DESC",
            {"event_id": event_id},
        )
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def fetch_pending_comments(connection: Any, limit: int = 200) -> list[dict[str, Any]]:
    """Commentaires en attente de modération (service indisponible au moment de la note)."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rating_id, comment FROM sortiria.event_ratings "
            "WHERE comment_status = 'en_attente' ORDER BY rating_id LIMIT %(limit)s",
            {"limit": limit},
        )
        return [{"rating_id": int(row[0]), "comment": str(row[1])} for row in cursor.fetchall()]


def update_comment_status(connection: Any, rating_id: int, status: str, details: Mapping[str, Any]) -> None:
    """Résultat de la modération d'un commentaire."""

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE sortiria.event_ratings SET comment_status = %(status)s, moderation = %(details)s "
            "WHERE rating_id = %(rating_id)s",
            {"status": status, "details": json.dumps(dict(details), ensure_ascii=False), "rating_id": rating_id},
        )


# Bilan des notes pour l'agent : dernières 24 h, et partenaires mal notés
# (moyenne de 2,5 ou moins sur 5 notes au moins) à contacter.
RATINGS_SUMMARY_SQL = """
SELECT
    (SELECT count(*) FROM sortiria.event_ratings WHERE updated_at > now() - interval '1 day'),
    (SELECT round(avg(stars)::NUMERIC, 2) FROM sortiria.event_ratings WHERE updated_at > now() - interval '1 day'),
    (SELECT count(*) FROM sortiria.event_ratings),
    (SELECT COALESCE(json_agg(json_build_object('partner', partner_name, 'average', average_stars,
                                                'ratings', ratings)), '[]'::json)
     FROM sortiria.v_partner_ratings WHERE ratings >= 5 AND average_stars <= 2.5)
"""


def ratings_summary(connection: Any) -> dict[str, Any]:
    """Notes des dernières 24 h, total, partenaires mal notés."""

    with connection.cursor() as cursor:
        cursor.execute(RATINGS_SUMMARY_SQL)
        row = cursor.fetchone() or (0, None, 0, [])
    low = row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]")
    return {"new_24h": int(row[0] or 0), "average_24h": float(row[1]) if row[1] is not None else None,
            "total": int(row[2] or 0), "low_rated_partners": low}


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
        # Suivi de l'agent (14/10/2026)
        "events_embedded": int(report.get("events_embedded", 0)),
        "cost_usd": float(report.get("cost_usd", 0.0)),
        "llm_usage": json.dumps(dict(report.get("llm_usage", {})), ensure_ascii=False),
        "coverage": json.dumps(dict(report.get("coverage", {})), ensure_ascii=False),
        "node_durations": json.dumps(dict(report.get("node_durations", {})), ensure_ascii=False),
        "alerts": [str(alert) for alert in report.get("alerts", [])],
        "prompt_version": str(report.get("prompt_version", "")),
        "mlflow_run_id": str(report.get("mlflow_run_id", "")),
        "quality_score": (report.get("quality") or {}).get("score"),
        "quality": json.dumps(dict(report.get("quality") or {}), ensure_ascii=False),
        "events_capped": int(report.get("events_capped") or 0),
        "events_total": report.get("events_total"),
    }
    with connection.cursor() as cursor:
        cursor.execute(INSERT_COLLECT_RUN_SQL, params)
        return int(cursor.fetchone()[0])


def record_run_sources(connection: Any, run_id: int, rows: Iterable[Mapping[str, Any]]) -> int:
    """Enregistre le détail par source d'une collecte. Renvoie le nombre de lignes.

    Chaque ligne : `source_id`, `status` (« ok », « partiel » ou « echec »), `attempts`,
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


def fetch_weekly_coverage(connection: Any, days: int = 7) -> dict[str, dict[str, int]]:
    """Sorties des `days` prochains jours par zone et par public : {zone: {public: n}}.

    Les zones et publics absents sont à 0, pour que la surveillance voie les
    cases vides.
    """

    counts = {zone: {audience: 0 for audience in ("famille", "soiree", "ados", "seniors")}
              for zone in ("montpellier", "bassin_de_thau")}
    with connection.cursor() as cursor:
        cursor.execute(SELECT_WEEKLY_COVERAGE_SQL, {"days": int(days)})
        for zone, audience, count in cursor.fetchall():
            if zone in counts and audience in counts[zone]:
                counts[zone][audience] = int(count)
    return counts


def fetch_previous_source_counts(connection: Any) -> dict[str, int]:
    """Événements retenus par source : le meilleur de ses 7 dernières collectes réussies."""

    with connection.cursor() as cursor:
        cursor.execute(SELECT_PREVIOUS_SOURCE_COUNTS_SQL)
        return {str(source_id): int(count) for source_id, count in cursor.fetchall()}


def purge_old_data(connection: Any) -> dict[str, int]:
    """Applique les durées de conservation (fonction `sortiria.purge_old_data`).

    Questions de plus de 90 jours (et leurs avis), cache expiré, collectes de
    plus d'un an. Renvoie le nombre de lignes supprimées par catégorie.
    """

    with connection.cursor() as cursor:
        cursor.execute(PURGE_OLD_DATA_SQL)
        row = cursor.fetchone() or (0, 0, 0)
    return {"requests": int(row[0]), "cache": int(row[1]), "runs": int(row[2])}


def refresh_title_vocabulary(connection: Any) -> int:
    """Recalcule le vocabulaire des titres (sujets reconnus par la recherche).
    Renvoie le nombre de racines de mots connues."""

    with connection.cursor() as cursor:
        cursor.execute(REFRESH_TITLE_VOCABULARY_SQL)
        row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_search_gaps(connection: Any, days: int = 7, limit: int = 10) -> dict[str, Any]:
    """Ce que les utilisateurs ont cherché sans trouver ces `days` derniers jours.

    - questions posées, sans réponse exacte, sans aucune réponse sur le sujet ;
    - mots écrits dans aucun titre de la base (« zumba ») : une source, un
      partenaire ou un synonyme à envisager ;
    - questions sans réponse exacte les plus fréquentes.
    L'agent le met dans son bilan du lundi ; il ne change rien lui-même.
    """

    params = {"days": int(days), "limit": int(limit)}
    with connection.cursor() as cursor:
        cursor.execute(SEARCH_GAPS_TOTALS_SQL, params)
        questions, not_exact, no_answer = cursor.fetchone() or (0, 0, 0)
        cursor.execute(SEARCH_GAPS_WORDS_SQL, params)
        words = [(str(word), int(times)) for word, times in cursor.fetchall()]
        cursor.execute(SEARCH_GAPS_QUESTIONS_SQL, params)
        unanswered = [(str(question), int(times)) for question, times in cursor.fetchall()]
    return {"days": int(days), "questions": int(questions or 0), "not_exact": int(not_exact or 0),
            "no_answer": int(no_answer or 0), "unknown_words": words, "unanswered": unanswered}
