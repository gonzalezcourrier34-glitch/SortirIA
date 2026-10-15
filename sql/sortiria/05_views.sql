-- ============================================================================
-- SortirIA · 05_views.sql
-- Vues de lecture : ce que le RAG peut proposer, et les indicateurs suivis
-- dans Grafana.
--
-- Provenance : nouveau fichier écrit pour SortirIA (14/10/2026).
-- ============================================================================

BEGIN;

-- Les vues sont recréées à chaque exécution : `CREATE VIEW`
-- refuserait une colonne ajoutée à events (elle décalerait celles de la vue).
DROP VIEW IF EXISTS sortiria.v_coverage, sortiria.v_upcoming_events,
                    sortiria.v_collect_health, sortiria.v_daily_quality,
                    sortiria.v_venue_ratings, sortiria.v_partner_ratings;


-- ----------------------------------------------------------------------------
-- 0. Notes des clients, par lieu et par partenaire (14/10/2026)
-- ----------------------------------------------------------------------------
-- Une moyenne n'est affichée qu'à partir de 3 notes : une note isolée ne dit
-- rien d'un lieu. Les notes survivent aux événements (table event_ratings).

CREATE VIEW sortiria.v_venue_ratings AS
SELECT
    sortiria.normalize_city(city)          AS city_key,
    lower(btrim(venue_name))               AS venue_key,
    min(venue_name)                        AS venue_name,
    min(city)                              AS city,
    count(*)                               AS ratings,
    round(avg(stars)::NUMERIC, 1)          AS average_stars,
    count(*) FILTER (WHERE comment_status = 'publie') AS comments,
    max(updated_at)                        AS last_rating_at
FROM sortiria.event_ratings
WHERE venue_name <> ''
GROUP BY 1, 2
HAVING count(*) >= 3;

CREATE VIEW sortiria.v_partner_ratings AS
SELECT
    p.partner_id,
    p.name                                 AS partner_name,
    count(r.*)                             AS ratings,
    round(avg(r.stars)::NUMERIC, 1)        AS average_stars,
    count(*) FILTER (WHERE r.stars <= 2)   AS low_ratings,
    max(r.updated_at)                      AS last_rating_at
FROM sortiria.partners AS p
JOIN sortiria.event_ratings AS r ON r.partner_id = p.partner_id
GROUP BY p.partner_id, p.name;

-- ----------------------------------------------------------------------------
-- 1. Les événements proposables
-- ----------------------------------------------------------------------------
-- Le RAG lit cette vue, pas la table : un événement annulé, reporté, passé
-- ou uniquement en ligne ne peut donc jamais être proposé. La source et son
-- texte d'attribution sont joints pour être affichés avec la proposition.

CREATE VIEW sortiria.v_upcoming_events AS
SELECT
    e.*,
    s.name        AS source_name,
    -- Texte affiché sous la proposition, ex. « Source : DATAtourisme (ADT34),
    -- mise à jour le 06/10/2026 ».
    s.attribution
        || CASE WHEN e.source_credit <> '' THEN ' (' || e.source_credit || ')' ELSE '' END
        || CASE WHEN e.source_updated_at IS NOT NULL
                THEN ', mise à jour le ' || to_char(e.source_updated_at, 'DD/MM/YYYY') ELSE '' END
        AS source_attribution,
    -- Note des clients du lieu (3 notes au moins), pour le classement du RAG.
    vr.average_stars AS venue_rating,
    vr.ratings       AS venue_rating_count
FROM sortiria.events AS e
JOIN sortiria.sources AS s ON s.source_id = e.source
LEFT JOIN sortiria.v_venue_ratings AS vr
       ON vr.city_key = sortiria.normalize_city(e.city)
      AND vr.venue_key = lower(btrim(e.venue_name))
WHERE s.active
  AND e.status NOT IN ('annule', 'reporte', 'deplace_en_ligne')
  AND e.attendance <> 'en_ligne'
  -- Même règle que DELETE_PAST_EVENTS_SQL (event_repository.py) : un
  -- événement « jour seulement » reste visible jusqu'à la fin de son jour.
  AND (
        (e.time_known AND COALESCE(e.end_at, e.start_at) >= now())
     OR (NOT e.time_known
         AND COALESCE(e.end_at, e.start_at) >= date_trunc('day', now() AT TIME ZONE 'Europe/Paris') AT TIME ZONE 'Europe/Paris')
  )
  -- On ne propose que ce qui commence dans les 3 prochains mois (14/10/2026) ;
  -- une exposition déjà commencée reste proposable jusqu'à sa fin.
  AND e.start_at < now() + interval '3 months';


-- ----------------------------------------------------------------------------
-- 2. Couverture par zone
-- ----------------------------------------------------------------------------
-- Nombre d'événements proposables par zone. Les centres et rayons recopient
-- `areas` de config/sources.yaml : à modifier aux deux endroits.

CREATE VIEW sortiria.v_coverage AS
SELECT
    -- Commune d'abord, puis GPS (fonction sortiria.event_zone, 00_extensions.sql).
    sortiria.event_zone(latitude, longitude, city)   AS zone,
    count(*)                                         AS events,
    count(*) FILTER (WHERE is_free)                  AS free_events,
    count(*) FILTER (WHERE 'famille' = ANY (audiences)) AS family_events,
    count(*) FILTER (WHERE 'soiree' = ANY (audiences))  AS night_events,
    count(*) FILTER (WHERE embedding IS NULL)        AS missing_embedding
FROM sortiria.v_upcoming_events
GROUP BY 1;


-- ----------------------------------------------------------------------------
-- 3. Santé de la collecte
-- ----------------------------------------------------------------------------

CREATE VIEW sortiria.v_collect_health AS
SELECT
    run_id,
    started_at,
    status,
    EXTRACT(EPOCH FROM (finished_at - started_at))::INTEGER AS duration_s,
    events_extracted,
    events_rejected,
    events_upserted,
    events_expired,
    events_removed,
    events_capped,
    events_total,
    failed_sources,
    events_embedded,
    cost_usd,
    cardinality(alerts)                                    AS alerts_count,
    -- Couverture de la semaine (famille / soirée x Montpellier / Thau).
    (coverage -> 'montpellier' ->> 'famille')::INTEGER     AS montpellier_famille,
    (coverage -> 'montpellier' ->> 'soiree')::INTEGER      AS montpellier_soiree,
    (coverage -> 'bassin_de_thau' ->> 'famille')::INTEGER  AS thau_famille,
    (coverage -> 'bassin_de_thau' ->> 'soiree')::INTEGER   AS thau_soiree,
    prompt_version,
    mlflow_run_id,
    quality_score
FROM sortiria.collect_runs
ORDER BY started_at DESC;


-- ----------------------------------------------------------------------------
-- 4. Qualité du service, par jour
-- ----------------------------------------------------------------------------
-- Une ligne par jour (heure de Paris) : les indicateurs de la grille
-- go / no-go suivis dans Grafana.

CREATE VIEW sortiria.v_daily_quality AS
WITH requests AS (
    SELECT
        (asked_at AT TIME ZONE 'Europe/Paris')::DATE AS day,
        count(*)                                         AS requests,
        avg(from_cache::INT)                             AS cache_rate,
        avg((status = 'erreur')::INT)                    AS error_rate,
        avg((status = 'aucun_resultat')::INT)            AS empty_rate,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS latency_p95_ms,
        avg(degraded::INT)                               AS degraded_rate,
        sum(cost_usd)                                    AS cost_usd
    FROM sortiria.rag_requests
    GROUP BY 1
),
ratings AS (
    SELECT
        (r.asked_at AT TIME ZONE 'Europe/Paris')::DATE AS day,
        count(*)                                         AS ratings,
        avg((f.rating = 1)::INT)                         AS thumbs_up_rate
    FROM sortiria.feedback AS f
    JOIN sortiria.rag_requests AS r USING (request_id)
    GROUP BY 1
)
SELECT
    q.day,
    q.requests,
    round(q.cache_rate, 3)      AS cache_rate,
    round(q.error_rate, 3)      AS error_rate,
    round(q.empty_rate, 3)      AS empty_rate,
    round(q.latency_p95_ms::NUMERIC) AS latency_p95_ms,
    round(q.degraded_rate, 3)   AS degraded_rate,
    q.cost_usd,
    COALESCE(r.ratings, 0)      AS ratings,
    round(r.thumbs_up_rate, 3)  AS thumbs_up_rate
FROM requests AS q
LEFT JOIN ratings AS r USING (day)
ORDER BY q.day DESC;

COMMIT;
