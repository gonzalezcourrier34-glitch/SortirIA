-- ============================================================================
-- SortirIA · 03_collect.sql
-- Le suivi des collectes nocturnes de l'agent.
--
-- Provenance : reprend la table collect_runs de sql/events/01_events.sql
-- (13/10/2026) ; le détail par source est ajouté le 14/10/2026.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Une ligne par collecte
-- ----------------------------------------------------------------------------
-- Alimente le tableau de bord « nuits réussies » et les alertes.

CREATE TABLE IF NOT EXISTS sortiria.collect_runs (
    run_id            BIGSERIAL PRIMARY KEY,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    status            TEXT        NOT NULL DEFAULT 'running',
    events_extracted  INTEGER     NOT NULL DEFAULT 0,
    events_rejected   INTEGER     NOT NULL DEFAULT 0,
    duplicates_merged INTEGER     NOT NULL DEFAULT 0,
    events_upserted   INTEGER     NOT NULL DEFAULT 0,
    events_expired    INTEGER     NOT NULL DEFAULT 0,
    -- Événements retirés car absents de leur source cette nuit-là.
    events_removed    INTEGER     NOT NULL DEFAULT 0,
    rejection_reasons JSONB       NOT NULL DEFAULT '{}'::jsonb,
    failed_sources    TEXT[]      NOT NULL DEFAULT '{}',
    message           TEXT        NOT NULL DEFAULT ''
);

-- Mise à niveau d'une base créée avant le 14/10/2026 à 11 h.
ALTER TABLE sortiria.collect_runs
    ADD COLUMN IF NOT EXISTS events_removed INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS collect_runs_started_at_idx
    ON sortiria.collect_runs (started_at);


-- ----------------------------------------------------------------------------
-- 2. Le détail par source
-- ----------------------------------------------------------------------------
-- C'est ce qui montre les décisions de l'agent : combien d'essais pour
-- chaque source, laquelle a échoué, combien d'événements elle a apportés.
-- Rempli par agent/nodes.py (write_report).

CREATE TABLE IF NOT EXISTS sortiria.collect_run_sources (
    run_id         BIGINT  NOT NULL REFERENCES sortiria.collect_runs (run_id) ON DELETE CASCADE,
    source_id      TEXT    NOT NULL REFERENCES sortiria.sources (source_id),
    status         TEXT    NOT NULL,
    attempts       SMALLINT NOT NULL DEFAULT 1,
    -- Événements de cette source retenus après validation et déduplication.
    events_kept    INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER,
    error          TEXT    NOT NULL DEFAULT '',

    PRIMARY KEY (run_id, source_id),
    CONSTRAINT collect_run_sources_status_check
        CHECK (status IN ('ok', 'echec'))
);

-- Lien entre un import partenaire et la collecte qui l'a traité.
ALTER TABLE sortiria.partner_imports
    ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES sortiria.collect_runs (run_id);

COMMIT;
