-- ============================================================================
-- SortirIA · 06_retention.sql
-- Durées de conservation (RGPD : on ne garde pas les questions indéfiniment).
--
-- Provenance : nouveau fichier écrit pour SortirIA (14/10/2026).
--
-- Appel prévu à la fin de chaque collecte nocturne :
--   SELECT * FROM sortiria.purge_old_data();
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION sortiria.purge_old_data(
    requests_days INTEGER DEFAULT 90,
    runs_days     INTEGER DEFAULT 365
) RETURNS TABLE (requests_deleted BIGINT, cache_deleted BIGINT, runs_deleted BIGINT)
LANGUAGE plpgsql AS $$
BEGIN
    -- Les avis liés partent avec leur question (ON DELETE CASCADE).
    DELETE FROM sortiria.rag_requests
    WHERE asked_at < now() - make_interval(days => requests_days);
    GET DIAGNOSTICS requests_deleted = ROW_COUNT;

    DELETE FROM sortiria.query_cache WHERE expires_at < now();
    GET DIAGNOSTICS cache_deleted = ROW_COUNT;

    -- Un import partenaire garde sa trace même si la collecte est purgée.
    UPDATE sortiria.partner_imports SET run_id = NULL
    WHERE run_id IN (SELECT run_id FROM sortiria.collect_runs
                     WHERE started_at < now() - make_interval(days => runs_days));
    DELETE FROM sortiria.collect_runs
    WHERE started_at < now() - make_interval(days => runs_days);
    GET DIAGNOSTICS runs_deleted = ROW_COUNT;

    RETURN NEXT;
END;
$$;

COMMIT;
