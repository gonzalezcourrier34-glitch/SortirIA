-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/00_database.sql
-- Version : 2.0
-- Objet   : Initialisation du schéma PostgreSQL et des tables transversales.
--
-- Cette version ajoute les éléments nécessaires à :
--   - l'orchestration Airflow ;
--   - la supervision des exécutions ;
--   - le stockage des KPI ;
--   - la journalisation en base ;
--   - le versionnage des jeux de données ;
--   - la configuration dynamique du pipeline.
--
-- Prérequis :
--   - la base "checkit" existe ;
--   - le rôle "checkit_et1" existe ;
--   - l'utilisateur exécutant possède les droits nécessaires.
--
-- Le script est non destructif et peut être rejoué.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS checkit
    AUTHORIZATION checkit_et1;

COMMENT ON SCHEMA checkit IS
    'Schéma applicatif du projet CheckIt.AI.';

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Fonction commune de mise à jour de updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION checkit.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION checkit.set_updated_at() IS
    'Met automatiquement à jour updated_at avant chaque modification.';


-- ============================================================================
-- 2. Table des sources
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.sources (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    source_key VARCHAR(100) NOT NULL,
    display_name VARCHAR(150) NOT NULL,
    source_type VARCHAR(30) NOT NULL,

    base_url TEXT,
    default_language VARCHAR(10),

    reliability_score NUMERIC(4, 3),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    requires_api_key BOOLEAN NOT NULL DEFAULT FALSE,

    refresh_interval_minutes INTEGER,
    max_articles_per_run INTEGER,

    last_successful_run TIMESTAMPTZ,
    last_failed_run TIMESTAMPTZ,
    last_error TEXT,

    configuration JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_sources_source_key
        UNIQUE (source_key),

    CONSTRAINT ck_sources_source_key_not_blank
        CHECK (BTRIM(source_key) <> ''),

    CONSTRAINT ck_sources_display_name_not_blank
        CHECK (BTRIM(display_name) <> ''),

    CONSTRAINT ck_sources_source_type
        CHECK (
            source_type IN (
                'rss',
                'api',
                'scraper',
                'social',
                'dataset'
            )
        ),

    CONSTRAINT ck_sources_reliability_score
        CHECK (
            reliability_score IS NULL
            OR reliability_score BETWEEN 0 AND 1
        ),

    CONSTRAINT ck_sources_refresh_interval
        CHECK (
            refresh_interval_minutes IS NULL
            OR refresh_interval_minutes > 0
        ),

    CONSTRAINT ck_sources_max_articles
        CHECK (
            max_articles_per_run IS NULL
            OR max_articles_per_run >= 0
        ),

    CONSTRAINT ck_sources_configuration_object
        CHECK (
            jsonb_typeof(configuration) = 'object'
        )
);

COMMENT ON TABLE checkit.sources IS
    'Référentiel des sources configurées pour CheckIt.AI.';

COMMENT ON COLUMN checkit.sources.source_key IS
    'Identifiant technique stable utilisé par le pipeline.';

COMMENT ON COLUMN checkit.sources.configuration IS
    'Configuration complémentaire de la source au format JSON.';

COMMENT ON COLUMN checkit.sources.reliability_score IS
    'Indicateur contextuel facultatif, sans valeur de vérité terrain.';

COMMENT ON COLUMN checkit.sources.refresh_interval_minutes IS
    'Fréquence théorique de collecte de la source.';

COMMENT ON COLUMN checkit.sources.max_articles_per_run IS
    'Nombre maximal d’articles demandé lors d’une exécution.';


DROP TRIGGER IF EXISTS trg_sources_set_updated_at
ON checkit.sources;

CREATE TRIGGER trg_sources_set_updated_at
BEFORE UPDATE ON checkit.sources
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 3. Table des exécutions du pipeline
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.pipeline_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    dag_id VARCHAR(150),
    airflow_run_id VARCHAR(250),

    pipeline_name VARCHAR(100) NOT NULL,
    pipeline_version VARCHAR(30) NOT NULL,

    code_version VARCHAR(100),
    airflow_version VARCHAR(30),

    status VARCHAR(20) NOT NULL DEFAULT 'running',

    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,

    extraction_duration_seconds NUMERIC(14, 3),
    transformation_duration_seconds NUMERIC(14, 3),
    load_duration_seconds NUMERIC(14, 3),
    duration_seconds NUMERIC(14, 3),

    extracted_count INTEGER NOT NULL DEFAULT 0,
    transformed_count INTEGER NOT NULL DEFAULT 0,
    valid_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    loaded_count INTEGER NOT NULL DEFAULT 0,

    images_downloaded_count INTEGER NOT NULL DEFAULT 0,
    images_valid_count INTEGER NOT NULL DEFAULT 0,
    images_invalid_count INTEGER NOT NULL DEFAULT 0,
    images_pending_count INTEGER NOT NULL DEFAULT 0,

    peak_memory_mb NUMERIC(12, 3),
    average_cpu_percent NUMERIC(6, 2),

    error_message TEXT,

    run_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_pipeline_runs_name_not_blank
        CHECK (BTRIM(pipeline_name) <> ''),

    CONSTRAINT ck_pipeline_runs_version_not_blank
        CHECK (BTRIM(pipeline_version) <> ''),

    CONSTRAINT ck_pipeline_runs_status
        CHECK (
            status IN (
                'running',
                'success',
                'partial',
                'failed'
            )
        ),

    CONSTRAINT ck_pipeline_runs_counts_non_negative
        CHECK (
            extracted_count >= 0
            AND transformed_count >= 0
            AND valid_count >= 0
            AND rejected_count >= 0
            AND loaded_count >= 0
            AND images_downloaded_count >= 0
            AND images_valid_count >= 0
            AND images_invalid_count >= 0
            AND images_pending_count >= 0
        ),

    CONSTRAINT ck_pipeline_runs_durations_non_negative
        CHECK (
            (extraction_duration_seconds IS NULL OR extraction_duration_seconds >= 0)
            AND (transformation_duration_seconds IS NULL OR transformation_duration_seconds >= 0)
            AND (load_duration_seconds IS NULL OR load_duration_seconds >= 0)
            AND (duration_seconds IS NULL OR duration_seconds >= 0)
        ),

    CONSTRAINT ck_pipeline_runs_resources
        CHECK (
            (peak_memory_mb IS NULL OR peak_memory_mb >= 0)
            AND (
                average_cpu_percent IS NULL
                OR average_cpu_percent BETWEEN 0 AND 100
            )
        ),

    CONSTRAINT ck_pipeline_runs_dates
        CHECK (
            finished_at IS NULL
            OR finished_at >= started_at
        ),

    CONSTRAINT ck_pipeline_runs_metadata_object
        CHECK (
            jsonb_typeof(run_metadata) = 'object'
        )
);

COMMENT ON TABLE checkit.pipeline_runs IS
    'Historique détaillé des exécutions locales et Airflow.';

COMMENT ON COLUMN checkit.pipeline_runs.run_metadata IS
    'Métadonnées complémentaires d’exécution au format JSON.';


DROP TRIGGER IF EXISTS trg_pipeline_runs_set_updated_at
ON checkit.pipeline_runs;

CREATE TRIGGER trg_pipeline_runs_set_updated_at
BEFORE UPDATE ON checkit.pipeline_runs
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 4. Table des métriques du pipeline
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.pipeline_metrics (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    pipeline_run_id UUID NOT NULL,

    metric_name VARCHAR(150) NOT NULL,
    metric_value NUMERIC(20, 6) NOT NULL,
    metric_unit VARCHAR(50),

    component VARCHAR(100),
    metric_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    measured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_pipeline_metrics_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES checkit.pipeline_runs(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT ck_pipeline_metrics_name_not_blank
        CHECK (BTRIM(metric_name) <> ''),

    CONSTRAINT ck_pipeline_metrics_unit_not_blank
        CHECK (
            metric_unit IS NULL
            OR BTRIM(metric_unit) <> ''
        ),

    CONSTRAINT ck_pipeline_metrics_metadata_object
        CHECK (
            jsonb_typeof(metric_metadata) = 'object'
        )
);

COMMENT ON TABLE checkit.pipeline_metrics IS
    'Mesures détaillées produites pendant les exécutions du pipeline.';


-- ============================================================================
-- 5. Table de configuration du pipeline
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.pipeline_configuration (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    configuration_key VARCHAR(150) NOT NULL,
    configuration_value JSONB NOT NULL,

    description TEXT,

    is_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_pipeline_configuration_key
        UNIQUE (configuration_key),

    CONSTRAINT ck_pipeline_configuration_key_not_blank
        CHECK (BTRIM(configuration_key) <> '')
);

COMMENT ON TABLE checkit.pipeline_configuration IS
    'Configuration fonctionnelle du pipeline stockée en base.';

COMMENT ON COLUMN checkit.pipeline_configuration.is_sensitive IS
    'Indique qu’une valeur ne doit pas être affichée dans les interfaces ou logs.';


DROP TRIGGER IF EXISTS trg_pipeline_configuration_set_updated_at
ON checkit.pipeline_configuration;

CREATE TRIGGER trg_pipeline_configuration_set_updated_at
BEFORE UPDATE ON checkit.pipeline_configuration
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 6. Table des versions de dataset
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.dataset_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    version_name VARCHAR(100) NOT NULL,
    pipeline_run_id UUID,

    description TEXT,

    article_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    label_count INTEGER NOT NULL DEFAULT 0,

    dataset_path TEXT,
    checksum VARCHAR(64),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_dataset_versions_name
        UNIQUE (version_name),

    CONSTRAINT fk_dataset_versions_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES checkit.pipeline_runs(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL,

    CONSTRAINT ck_dataset_versions_name_not_blank
        CHECK (BTRIM(version_name) <> ''),

    CONSTRAINT ck_dataset_versions_counts_non_negative
        CHECK (
            article_count >= 0
            AND image_count >= 0
            AND label_count >= 0
        ),

    CONSTRAINT ck_dataset_versions_checksum
        CHECK (
            checksum IS NULL
            OR checksum ~ '^[0-9a-fA-F]{32,64}$'
        )
);

COMMENT ON TABLE checkit.dataset_versions IS
    'Versions reproductibles des jeux de données produits par le pipeline.';


-- ============================================================================
-- 7. Table de journalisation ETL
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.etl_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    pipeline_run_id UUID,

    log_level VARCHAR(20) NOT NULL,
    component VARCHAR(150) NOT NULL,
    message TEXT NOT NULL,

    error_type VARCHAR(150),
    error_details JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_etl_logs_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES checkit.pipeline_runs(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT ck_etl_logs_level
        CHECK (
            log_level IN (
                'DEBUG',
                'INFO',
                'WARNING',
                'ERROR',
                'CRITICAL'
            )
        ),

    CONSTRAINT ck_etl_logs_component_not_blank
        CHECK (BTRIM(component) <> ''),

    CONSTRAINT ck_etl_logs_message_not_blank
        CHECK (BTRIM(message) <> ''),

    CONSTRAINT ck_etl_logs_details_object
        CHECK (
            jsonb_typeof(error_details) = 'object'
        )
);

COMMENT ON TABLE checkit.etl_logs IS
    'Journalisation structurée des événements ETL destinée au monitoring.';


-- ============================================================================
-- 8. Propriétaires
-- ============================================================================

ALTER TABLE checkit.sources
    OWNER TO checkit_et1;

ALTER TABLE checkit.pipeline_runs
    OWNER TO checkit_et1;

ALTER TABLE checkit.pipeline_metrics
    OWNER TO checkit_et1;

ALTER TABLE checkit.pipeline_configuration
    OWNER TO checkit_et1;

ALTER TABLE checkit.dataset_versions
    OWNER TO checkit_et1;

ALTER TABLE checkit.etl_logs
    OWNER TO checkit_et1;

ALTER FUNCTION checkit.set_updated_at()
    OWNER TO checkit_et1;


COMMIT;


-- ============================================================================
-- 9. Vérification
-- ============================================================================

SELECT
    table_schema,
    table_name
FROM information_schema.tables
WHERE table_schema = 'checkit'
ORDER BY table_name;