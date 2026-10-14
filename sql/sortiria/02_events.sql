-- ============================================================================
-- SortirIA · 02_events.sql
-- Les événements à venir, prêts pour le filtre SQL et la recherche sémantique.
--
-- Provenance : reprend sql/events/01_events.sql (13/10/2026), complété le
-- 14/10/2026 par : la clé étrangère vers les sources, le statut OpenAgenda
-- (annulé, reporté, complet), le mode de participation, l'âge, les publics
-- (famille, soirée), l'accessibilité et les champs partenaires.
-- ============================================================================

BEGIN;

-- Une ligne par événement unique (après déduplication entre sources).
-- event_id est calculé à partir du titre, du jour et du lieu normalisés
-- (src/event/event_normalizer.py, build_event_id).

CREATE TABLE IF NOT EXISTS sortiria.events (
    event_id         TEXT PRIMARY KEY,
    source           TEXT        NOT NULL REFERENCES sortiria.sources (source_id),
    source_event_id  TEXT        NOT NULL DEFAULT '',

    -- Contenu
    title            TEXT        NOT NULL,
    description      TEXT        NOT NULL DEFAULT '',
    category         TEXT        NOT NULL DEFAULT '',
    keywords         TEXT[]      NOT NULL DEFAULT '{}',
    language         TEXT        NOT NULL DEFAULT 'fr',

    -- Quand
    start_at         TIMESTAMPTZ NOT NULL,
    end_at           TIMESTAMPTZ,
    time_known       BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Où
    venue_name       TEXT        NOT NULL DEFAULT '',
    address          TEXT        NOT NULL DEFAULT '',
    city             TEXT        NOT NULL DEFAULT '',
    postal_code      TEXT        NOT NULL DEFAULT '',
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,

    -- Combien
    price_text       TEXT        NOT NULL DEFAULT '',
    price_min        NUMERIC(8, 2),
    price_max        NUMERIC(8, 2),
    is_free          BOOLEAN,

    -- Pour qui, et dans quelles conditions
    status           TEXT        NOT NULL DEFAULT 'programme',
    attendance       TEXT        NOT NULL DEFAULT 'sur_place',
    age_min          SMALLINT,
    age_max          SMALLINT,
    audiences        TEXT[]      NOT NULL DEFAULT '{}',
    accessibility    TEXT[]      NOT NULL DEFAULT '{}',

    -- Partenaires : un événement mis en avant est toujours signalé comme tel.
    partner_id       BIGINT      REFERENCES sortiria.partners (partner_id),
    is_sponsored     BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Liens
    url              TEXT        NOT NULL,
    image_url        TEXT        NOT NULL DEFAULT '',

    -- Embedding Mistral (mistral-embed : 1 024 dimensions). NULL tant qu'il
    -- n'est pas calculé, ou après une modification du titre / de la description.
    embedding        vector(1024),
    embedding_model  TEXT,

    -- Traçabilité. source_credit et source_updated_at : producteur de la
    -- fiche et date de sa mise à jour, à citer (Licence Ouverte 2.0).
    extracted_at     TIMESTAMPTZ,
    source_credit    TEXT        NOT NULL DEFAULT '',
    source_updated_at DATE,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ----------------------------------------------------------------------------
-- Mise à niveau d'une base créée avec la version du 13/10/2026
-- ----------------------------------------------------------------------------
-- Sans effet sur une base neuve : les colonnes existent déjà.

ALTER TABLE sortiria.events
    ADD COLUMN IF NOT EXISTS status        TEXT     NOT NULL DEFAULT 'programme',
    ADD COLUMN IF NOT EXISTS attendance    TEXT     NOT NULL DEFAULT 'sur_place',
    ADD COLUMN IF NOT EXISTS age_min       SMALLINT,
    ADD COLUMN IF NOT EXISTS age_max       SMALLINT,
    ADD COLUMN IF NOT EXISTS audiences     TEXT[]   NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS accessibility TEXT[]   NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS partner_id    BIGINT   REFERENCES sortiria.partners (partner_id),
    ADD COLUMN IF NOT EXISTS is_sponsored  BOOLEAN  NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS source_credit TEXT     NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_updated_at DATE;


-- ----------------------------------------------------------------------------
-- Règles de cohérence
-- ----------------------------------------------------------------------------
-- Déclarées à part (DROP puis ADD) pour que le script reste réexécutable.

ALTER TABLE sortiria.events
    DROP CONSTRAINT IF EXISTS events_end_after_start,
    DROP CONSTRAINT IF EXISTS events_price_order,
    DROP CONSTRAINT IF EXISTS events_status_check,
    DROP CONSTRAINT IF EXISTS events_attendance_check,
    DROP CONSTRAINT IF EXISTS events_age_check,
    DROP CONSTRAINT IF EXISTS events_audiences_check,
    DROP CONSTRAINT IF EXISTS events_sponsored_needs_partner,
    DROP CONSTRAINT IF EXISTS events_source_fkey;

ALTER TABLE sortiria.events
    ADD CONSTRAINT events_source_fkey
        FOREIGN KEY (source) REFERENCES sortiria.sources (source_id),
    ADD CONSTRAINT events_end_after_start
        CHECK (end_at IS NULL OR end_at >= start_at),
    ADD CONSTRAINT events_price_order
        CHECK (price_min IS NULL OR price_max IS NULL OR price_min <= price_max),
    -- Statuts OpenAgenda (1 à 6), traduits par openagenda_mapper.py.
    ADD CONSTRAINT events_status_check
        CHECK (status IN ('programme', 'reprogramme', 'deplace_en_ligne',
                          'reporte', 'complet', 'annule')),
    ADD CONSTRAINT events_attendance_check
        CHECK (attendance IN ('sur_place', 'en_ligne', 'mixte')),
    ADD CONSTRAINT events_age_check
        CHECK ((age_min IS NULL OR age_min BETWEEN 0 AND 120)
           AND (age_max IS NULL OR age_max BETWEEN 0 AND 120)
           AND (age_min IS NULL OR age_max IS NULL OR age_min <= age_max)),
    ADD CONSTRAINT events_audiences_check
        CHECK (audiences <@ ARRAY['famille', 'soiree']::TEXT[]),
    ADD CONSTRAINT events_sponsored_needs_partner
        CHECK (NOT is_sponsored OR partner_id IS NOT NULL);


-- ----------------------------------------------------------------------------
-- Index
-- ----------------------------------------------------------------------------
-- Les filtres les plus fréquents : date, ville, catégorie, gratuité, public.
CREATE INDEX IF NOT EXISTS events_start_at_idx  ON sortiria.events (start_at);
CREATE INDEX IF NOT EXISTS events_city_idx      ON sortiria.events (lower(city));
CREATE INDEX IF NOT EXISTS events_category_idx  ON sortiria.events (lower(category));
CREATE INDEX IF NOT EXISTS events_is_free_idx   ON sortiria.events (is_free);
CREATE INDEX IF NOT EXISTS events_audiences_idx ON sortiria.events USING gin (audiences);
CREATE INDEX IF NOT EXISTS events_partner_idx   ON sortiria.events (partner_id)
    WHERE partner_id IS NOT NULL;

-- Recherche sémantique (similarité cosinus).
CREATE INDEX IF NOT EXISTS events_embedding_idx
    ON sortiria.events USING hnsw (embedding vector_cosine_ops);

COMMIT;
