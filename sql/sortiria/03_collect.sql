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
    message           TEXT        NOT NULL DEFAULT '',
    -- Suivi de l'agent (14/10/2026) : ce qu'il faut pour suivre sa santé,
    -- son coût et la qualité de ses extractions nuit après nuit.
    events_embedded   INTEGER     NOT NULL DEFAULT 0,
    cost_usd          NUMERIC(10, 4) NOT NULL DEFAULT 0,  -- coût Mistral estimé
    llm_usage         JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- {modèle: {input, output}}
    coverage          JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- {zone: {famille, soiree}} sur 7 jours
    node_durations    JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- {nœud du graphe: secondes}
    alerts            TEXT[]      NOT NULL DEFAULT '{}',
    prompt_version    TEXT        NOT NULL DEFAULT '',  -- consigne de lecture des PDF
    mlflow_run_id     TEXT        NOT NULL DEFAULT '',  -- lien vers la trace MLflow
    quality_score     NUMERIC(4, 3),                     -- juge Mistral (0 à 1), NULL sans contrôle
    quality           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    events_capped     INTEGER     NOT NULL DEFAULT 0,  -- supprimés par le plafond (les plus lointains)
    events_total      INTEGER                          -- taille de la base après la collecte
);

-- Mise à niveau d'une base créée avant le 14/10/2026 à 11 h.
ALTER TABLE sortiria.collect_runs
    ADD COLUMN IF NOT EXISTS events_removed INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS events_embedded INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS node_durations JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS alerts TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS prompt_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS mlflow_run_id TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS quality_score NUMERIC(4, 3),
    ADD COLUMN IF NOT EXISTS quality JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS events_capped INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS events_total INTEGER;

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

    PRIMARY KEY (run_id, source_id)
);

-- « partiel » (15/10/2026) : l'extracteur a rendu des événements mais une partie
-- de sa lecture a échoué (un PDF sur deux en 404, un agenda OpenAgenda en quota).
-- L'agent ne balaie pas les événements d'une telle source.
ALTER TABLE sortiria.collect_run_sources DROP CONSTRAINT IF EXISTS collect_run_sources_status_check;
ALTER TABLE sortiria.collect_run_sources ADD CONSTRAINT collect_run_sources_status_check
    CHECK (status IN ('ok', 'partiel', 'echec'));

-- Lien entre un import partenaire et la collecte qui l'a traité.
ALTER TABLE sortiria.partner_imports
    ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES sortiria.collect_runs (run_id);


-- ----------------------------------------------------------------------------
-- 3. Les propositions de l'agent (14/10/2026)
-- ----------------------------------------------------------------------------
-- Quand l'agent sait réparer une panne (document déplacé : nouvelle adresse
-- trouvée et vérifiée), il PROPOSE la réparation ici, sans l'appliquer. Un
-- humain l'accepte ou la refuse : uv run python -m agent.proposals

CREATE TABLE IF NOT EXISTS sortiria.agent_proposals (
    proposal_id  BIGSERIAL   PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id       BIGINT      REFERENCES sortiria.collect_runs (run_id) ON DELETE SET NULL,
    source_id    TEXT        NOT NULL DEFAULT '',
    kind         TEXT        NOT NULL,      -- famille de panne (agent/diagnosis.py)
    diagnosis    TEXT        NOT NULL,      -- explication lisible
    action       JSONB       NOT NULL,      -- ex. {"type": "remplacer_url", "old": ..., "new": ...}
    status       TEXT        NOT NULL DEFAULT 'en_attente',
    decided_at   TIMESTAMPTZ,

    CONSTRAINT agent_proposals_status_check
        CHECK (status IN ('en_attente', 'acceptee', 'refusee', 'appliquee'))
);

-- Une même réparation n'est proposée qu'une fois tant qu'elle attend une réponse.
CREATE UNIQUE INDEX IF NOT EXISTS agent_proposals_pending_once
    ON sortiria.agent_proposals (source_id, (action ->> 'new'))
    WHERE status = 'en_attente';


-- ----------------------------------------------------------------------------
-- 4. Statistiques des événements supprimés (14/10/2026)
-- ----------------------------------------------------------------------------
-- La base ne garde que les événements utiles (à venir, 10 000 au plus) ; un
-- événement supprimé ne laisse qu'une trace comptée : par jour, source,
-- motif, catégorie et zone. Assez pour les tendances (ce qui a été proposé,
-- d'où, pour qui), sans conserver les fiches.

CREATE TABLE IF NOT EXISTS sortiria.deleted_event_stats (
    day        DATE    NOT NULL,     -- jour de la suppression (heure de Paris)
    source_id  TEXT    NOT NULL,
    reason     TEXT    NOT NULL,     -- passe, disparu (de sa source), plafond
    category   TEXT    NOT NULL,     -- catégorie unifiée, « non_classe » sinon
    zone       TEXT    NOT NULL,     -- sortiria.event_zone
    events     INTEGER NOT NULL DEFAULT 0,
    family     INTEGER NOT NULL DEFAULT 0,   -- dont publics « famille »
    night      INTEGER NOT NULL DEFAULT 0,   -- dont publics « soirée »

    PRIMARY KEY (day, source_id, reason, category, zone),
    CONSTRAINT deleted_event_stats_reason_check
        CHECK (reason IN ('passe', 'disparu', 'plafond'))
);


COMMIT;
