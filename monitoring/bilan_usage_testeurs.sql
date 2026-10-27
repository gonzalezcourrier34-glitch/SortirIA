-- Bilan d'usage SortirIA : période des testeurs (17/10 au 26/10/2026 inclus)
-- À lancer sur la base de production (Grafana > Explore, source « SortirIA production (Azure) »,
-- ou psql avec le compte grafana_lecture). Lecture seule, aucune modification.
-- Penser à changer les dates si besoin. Dans Explore, lancer une requête à la fois (mode Code).
-- Les questions qui commencent par « [t » (mes tests) sont exclues, comme dans agent/drift.py.

-- 1. Volume, visiteurs, réponses exactes, latence, coût
SELECT
    count(*)                                                          AS questions,
    count(DISTINCT session_id)                                        AS visiteurs,
    round(100.0 * avg(CASE WHEN (constraints->'recherche'->>'exact')::boolean
                           THEN 1 ELSE 0 END), 1)                     AS pct_reponse_exacte,
    round(100.0 * avg(CASE WHEN cardinality(event_ids) = 0
                           THEN 1 ELSE 0 END), 1)                     AS pct_sans_resultat,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)          AS latence_p95_ms,
    round(sum(cost_usd), 4)                                           AS cout_mistral_usd
FROM sortiria.rag_requests
WHERE asked_at >= '2026-10-17' AND asked_at < '2026-10-27'
  AND question NOT LIKE '[t%';   -- exclut mes questions de test

-- 2. Pouces haut / bas (réponse entière = event_id vide ; sinon avis sur une carte)
SELECT
    CASE WHEN f.event_id = '' THEN 'réponse' ELSE 'carte' END         AS sur,
    count(*)                                                          AS avis,
    sum(CASE WHEN f.rating = 1  THEN 1 ELSE 0 END)                    AS pouces_haut,
    sum(CASE WHEN f.rating = -1 THEN 1 ELSE 0 END)                    AS pouces_bas,
    round(100.0 * avg(CASE WHEN f.rating = 1 THEN 1 ELSE 0 END), 1)   AS pct_pouces_haut
FROM sortiria.feedback f
JOIN sortiria.rag_requests r USING (request_id)
WHERE r.asked_at >= '2026-10-17' AND r.asked_at < '2026-10-27'
  AND r.question NOT LIKE '[t%'
GROUP BY 1;

-- 3. Les questions sans réponse exacte et les pouces bas, à relire à la main
SELECT r.asked_at, r.question, r.constraints->'recherche' AS ce_qui_a_manque,
       f.rating, f.comment
FROM sortiria.rag_requests r
LEFT JOIN sortiria.feedback f USING (request_id)
WHERE r.asked_at >= '2026-10-17' AND r.asked_at < '2026-10-27'
  AND r.question NOT LIKE '[t%'
  AND (NOT coalesce((r.constraints->'recherche'->>'exact')::boolean, true)
       OR f.rating = -1)
ORDER BY r.asked_at;

-- 4. Le pic du 23/10 : cache, réponses dégradées ou erreurs ?
SELECT asked_at::date AS jour, count(*) AS questions,
       sum(CASE WHEN from_cache THEN 1 ELSE 0 END) AS depuis_cache,
       sum(CASE WHEN degraded   THEN 1 ELSE 0 END) AS sans_mistral,
       sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS erreurs,
       string_agg(DISTINCT nullif(error, ''), ' | ') AS messages_erreur
FROM sortiria.rag_requests
WHERE asked_at >= '2026-10-15'
GROUP BY 1 ORDER BY 1;
