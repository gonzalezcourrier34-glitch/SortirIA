-- ============================================================================
-- SortirIA · 01_sources.sql
-- D'où viennent les événements : sources publiques, partenaires, imports.
--
-- Provenance : nouveau fichier écrit pour SortirIA (14/10/2026).
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Sources
-- ----------------------------------------------------------------------------
-- Une ligne par source déclarée. `attribution` est le texte affiché sous
-- chaque proposition : la Licence Ouverte 2.0 oblige à citer la source.
-- Un événement ne peut pas être chargé si sa source n'est pas déclarée ici
-- (clé étrangère dans events) : aucune donnée sans licence connue.

CREATE TABLE IF NOT EXISTS sortiria.sources (
    source_id    TEXT PRIMARY KEY,
    name         TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    licence      TEXT    NOT NULL,
    attribution  TEXT    NOT NULL,
    homepage     TEXT    NOT NULL DEFAULT '',
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT sources_kind_check
        CHECK (kind IN ('api', 'open_data', 'partenaire', 'page_web'))
);


-- ----------------------------------------------------------------------------
-- 2. Partenaires
-- ----------------------------------------------------------------------------
-- Les professionnels (animation, soirées, activités pour enfants...) qui
-- transmettent leur programme. On ne stocke que l'organisation : les
-- coordonnées des personnes restent hors de la base (RGPD).

CREATE TABLE IF NOT EXISTS sortiria.partners (
    partner_id       BIGSERIAL PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    sector           TEXT NOT NULL DEFAULT 'autre',
    website          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'prospect',
    -- Date jusqu'à laquelle ses événements peuvent être mis en avant.
    sponsored_until  DATE,
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT partners_sector_check
        CHECK (sector IN ('animation', 'soiree', 'enfants', 'culture', 'sport', 'autre')),
    CONSTRAINT partners_status_check
        CHECK (status IN ('prospect', 'actif', 'termine'))
);


-- ----------------------------------------------------------------------------
-- 3. Imports de jeux de données partenaires
-- ----------------------------------------------------------------------------
-- Une ligne par fichier reçu (tableur, CSV, programme). L'empreinte SHA-256
-- empêche d'importer deux fois le même fichier.

CREATE TABLE IF NOT EXISTS sortiria.partner_imports (
    import_id          BIGSERIAL PRIMARY KEY,
    partner_id         BIGINT NOT NULL REFERENCES sortiria.partners (partner_id),
    file_name          TEXT   NOT NULL,
    file_sha256        TEXT   NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    status             TEXT   NOT NULL DEFAULT 'recu',
    rows_total         INTEGER NOT NULL DEFAULT 0,
    rows_loaded        INTEGER NOT NULL DEFAULT 0,
    rows_rejected      INTEGER NOT NULL DEFAULT 0,
    rejection_reasons  JSONB   NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT partner_imports_once UNIQUE (partner_id, file_sha256),
    CONSTRAINT partner_imports_status_check
        CHECK (status IN ('recu', 'importe', 'partiel', 'refuse'))
);


-- ----------------------------------------------------------------------------
-- 4. Sources déclarées au départ
-- ----------------------------------------------------------------------------
-- ON CONFLICT DO NOTHING : relancer le script ne modifie pas une ligne
-- qu'on aurait ajustée à la main.

INSERT INTO sortiria.sources (source_id, name, kind, licence, attribution, homepage)
VALUES
    ('openagenda', 'OpenAgenda', 'api', 'Licence Ouverte 2.0',
     'Source : OpenAgenda', 'https://openagenda.com'),
    ('datatourisme', 'DATAtourisme', 'open_data', 'Licence Ouverte 2.0',
     'Source : DATAtourisme', 'https://www.datatourisme.fr'),
    ('partenaire', 'Partenaires SortirIA', 'partenaire', 'Accord de partenariat',
     'Proposé par un partenaire SortirIA', ''),
    -- Calendriers iCal des lieux (moteur src/extractors/ical/) : le nom du
    -- lieu est cité dans events.source_credit.
    ('ical', 'Calendriers des lieux partenaires', 'partenaire', 'Accord du lieu',
     'Source : agenda du lieu', ''),
    -- Programmes publiés en PDF par des collectivités, lus par Mistral
    -- (src/extractors/documents/). Réutilisation des informations publiques :
    -- citer la source et la date, ne pas dénaturer. Le document est cité
    -- dans events.source_credit.
    ('document', 'Programmes publiés par les collectivités', 'open_data',
     'Réutilisation des informations publiques (CRPA, art. L321-1 et suivants)',
     'Source : programme publié', ''),
    -- Flux RSS d'agenda (moteur RSS de CheckIt.AI, adaptateur
    -- src/extractors/rss/source/) : seulement des flux prévus pour la reprise.
    ('rss', 'Flux RSS d''agenda', 'page_web', 'Flux publié pour la syndication',
     'Source : flux de l''agenda', ''),
    -- Pages d'agenda lues par le moteur scraper de CheckIt.AI : seulement avec
    -- l'autorisation écrite de l'éditeur (robots.txt respecté).
    ('scraper', 'Agendas en ligne (avec autorisation)', 'page_web', 'Autorisation de l''éditeur',
     'Source : site de l''éditeur', '')
ON CONFLICT (source_id) DO NOTHING;

COMMIT;
