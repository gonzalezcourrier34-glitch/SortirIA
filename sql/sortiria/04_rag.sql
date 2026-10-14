-- ============================================================================
-- SortirIA · 04_rag.sql
-- Le journal des questions, les avis des utilisateurs et le cache.
--
-- Provenance : reprend la table rag_requests de sql/events/01_events.sql
-- (13/10/2026). Le 14/10/2026, l'avis passe dans sa propre table (un avis
-- par proposition, pas un par réponse) et le cache est ajouté.
--
-- RGPD : aucune donnée personnelle. session_id est un identifiant tiré au
-- hasard par l'interface, sans compte ni adresse IP.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Une ligne par question posée
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sortiria.rag_requests (
    request_id        BIGSERIAL PRIMARY KEY,
    asked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id        UUID,
    question          TEXT        NOT NULL,
    -- Contraintes extraites par Ministral : date, zone, budget, type, public.
    constraints       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    event_ids         TEXT[]      NOT NULL DEFAULT '{}',
    from_cache        BOOLEAN     NOT NULL DEFAULT FALSE,
    status            TEXT        NOT NULL DEFAULT 'ok',
    error             TEXT        NOT NULL DEFAULT '',
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_eur          NUMERIC(10, 6),
    extraction_model  TEXT,
    llm_model         TEXT,
    prompt_version    TEXT,
    index_version     TEXT
);

-- Mise à niveau d'une base créée le 13/10/2026.
ALTER TABLE sortiria.rag_requests
    ADD COLUMN IF NOT EXISTS session_id       UUID,
    ADD COLUMN IF NOT EXISTS status           TEXT NOT NULL DEFAULT 'ok',
    ADD COLUMN IF NOT EXISTS error            TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS extraction_model TEXT,
    DROP COLUMN IF EXISTS user_feedback;

ALTER TABLE sortiria.rag_requests DROP CONSTRAINT IF EXISTS rag_requests_status_check;
ALTER TABLE sortiria.rag_requests ADD CONSTRAINT rag_requests_status_check
    CHECK (status IN ('ok', 'aucun_resultat', 'refuse', 'erreur'));

CREATE INDEX IF NOT EXISTS rag_requests_asked_at_idx ON sortiria.rag_requests (asked_at);


-- ----------------------------------------------------------------------------
-- 2. Avis des utilisateurs (pouce en haut / pouce en bas)
-- ----------------------------------------------------------------------------
-- Un avis par proposition. event_id n'est pas une clé étrangère : l'événement
-- est supprimé une fois passé, mais l'avis reste utile pour l'évaluation et,
-- plus tard, comme donnée d'entraînement.

CREATE TABLE IF NOT EXISTS sortiria.feedback (
    feedback_id  BIGSERIAL PRIMARY KEY,
    request_id   BIGINT   NOT NULL REFERENCES sortiria.rag_requests (request_id) ON DELETE CASCADE,
    event_id     TEXT     NOT NULL DEFAULT '',
    rating       SMALLINT NOT NULL,
    comment      TEXT     NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT feedback_rating_check  CHECK (rating IN (-1, 1)),
    CONSTRAINT feedback_comment_check CHECK (length(comment) <= 500),
    -- event_id vide = avis sur la réponse entière.
    CONSTRAINT feedback_once UNIQUE (request_id, event_id)
);


-- ----------------------------------------------------------------------------
-- 3. Cache des réponses
-- ----------------------------------------------------------------------------
-- Clé = empreinte de la question normalisée + jour + version de l'index :
-- la même question posée le même jour, sur les mêmes données, est servie
-- sans appeler Mistral. Une nouvelle collecte change index_version et rend
-- donc les anciennes entrées inutilisables.

CREATE TABLE IF NOT EXISTS sortiria.query_cache (
    cache_key            TEXT PRIMARY KEY,
    question_normalized  TEXT    NOT NULL,
    constraints          JSONB   NOT NULL DEFAULT '{}'::jsonb,
    response             JSONB   NOT NULL,
    event_ids            TEXT[]  NOT NULL DEFAULT '{}',
    index_version        TEXT    NOT NULL DEFAULT '',
    hits                 INTEGER NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS query_cache_expires_at_idx ON sortiria.query_cache (expires_at);

COMMIT;
