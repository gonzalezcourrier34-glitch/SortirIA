-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/08_procedures.sql
-- Version : 1.0
-- Objet   : Procédures PostgreSQL utilisées par Airflow, le monitoring
--           et la maintenance du pipeline.
--
-- Prérequis :
--   - exécuter sql/00_database.sql ;
--   - exécuter sql/01_articles.sql ;
--   - exécuter sql/02_images_labels.sql ;
--   - exécuter sql/03_features_predictions.sql ;
--   - exécuter sql/07_functions.sql.
--
-- Les procédures ne réalisent aucun COMMIT interne.
-- Elles peuvent donc être appelées depuis Airflow dans une transaction.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Clôturer une exécution de pipeline
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.finalize_pipeline_run(
    input_run_id UUID,
    input_status VARCHAR,
    input_extracted_count INTEGER DEFAULT NULL,
    input_transformed_count INTEGER DEFAULT NULL,
    input_valid_count INTEGER DEFAULT NULL,
    input_rejected_count INTEGER DEFAULT NULL,
    input_loaded_count INTEGER DEFAULT NULL,
    input_images_downloaded_count INTEGER DEFAULT NULL,
    input_images_valid_count INTEGER DEFAULT NULL,
    input_images_invalid_count INTEGER DEFAULT NULL,
    input_error_message TEXT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF input_status NOT IN (
        'success',
        'partial',
        'failed'
    ) THEN
        RAISE EXCEPTION
            'Statut de pipeline invalide : %',
            input_status;
    END IF;

    UPDATE checkit.pipeline_runs
    SET
        status = input_status,
        finished_at = CURRENT_TIMESTAMP,

        extracted_count = COALESCE(
            input_extracted_count,
            extracted_count
        ),

        transformed_count = COALESCE(
            input_transformed_count,
            transformed_count
        ),

        valid_count = COALESCE(
            input_valid_count,
            valid_count
        ),

        rejected_count = COALESCE(
            input_rejected_count,
            rejected_count
        ),

        loaded_count = COALESCE(
            input_loaded_count,
            loaded_count
        ),

        images_downloaded_count = COALESCE(
            input_images_downloaded_count,
            images_downloaded_count
        ),

        images_valid_count = COALESCE(
            input_images_valid_count,
            images_valid_count
        ),

        images_invalid_count = COALESCE(
            input_images_invalid_count,
            images_invalid_count
        ),

        duration_seconds = checkit.execution_duration_seconds(
            started_at,
            CURRENT_TIMESTAMP
        ),

        error_message = input_error_message,

        updated_at = CURRENT_TIMESTAMP

    WHERE id = input_run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Exécution de pipeline introuvable : %',
            input_run_id;
    END IF;
END;
$$;

COMMENT ON PROCEDURE checkit.finalize_pipeline_run(
    UUID,
    VARCHAR,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    TEXT
) IS
    'Clôture une exécution du pipeline et met à jour ses volumes, son statut et sa durée.';


-- ============================================================================
-- 2. Mettre à jour l’état d’une source
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.update_source_run_status(
    input_source_key VARCHAR,
    input_success BOOLEAN,
    input_error_message TEXT DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE checkit.sources
    SET
        last_successful_run = CASE
            WHEN input_success THEN CURRENT_TIMESTAMP
            ELSE last_successful_run
        END,

        last_failed_run = CASE
            WHEN input_success THEN last_failed_run
            ELSE CURRENT_TIMESTAMP
        END,

        last_error = CASE
            WHEN input_success THEN NULL
            ELSE input_error_message
        END,

        updated_at = CURRENT_TIMESTAMP

    WHERE source_key = input_source_key;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Source introuvable : %',
            input_source_key;
    END IF;
END;
$$;

COMMENT ON PROCEDURE checkit.update_source_run_status(
    VARCHAR,
    BOOLEAN,
    TEXT
) IS
    'Met à jour la dernière exécution réussie ou échouée d’une source.';


-- ============================================================================
-- 3. Enregistrer une métrique de pipeline
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.register_pipeline_metric(
    input_pipeline_run_id UUID,
    input_metric_name VARCHAR,
    input_metric_value NUMERIC,
    input_metric_unit VARCHAR DEFAULT NULL,
    input_component VARCHAR DEFAULT NULL,
    input_metadata JSONB DEFAULT '{}'::JSONB
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF checkit.is_null_or_blank(input_metric_name) THEN
        RAISE EXCEPTION
            'Le nom de la métrique est obligatoire.';
    END IF;

    IF input_metadata IS NULL
       OR JSONB_TYPEOF(input_metadata) <> 'object' THEN
        RAISE EXCEPTION
            'Les métadonnées de métrique doivent être un objet JSON.';
    END IF;

    INSERT INTO checkit.pipeline_metrics (
        pipeline_run_id,
        metric_name,
        metric_value,
        metric_unit,
        component,
        metric_metadata
    )
    VALUES (
        input_pipeline_run_id,
        input_metric_name,
        input_metric_value,
        input_metric_unit,
        input_component,
        input_metadata
    );
END;
$$;

COMMENT ON PROCEDURE checkit.register_pipeline_metric(
    UUID,
    VARCHAR,
    NUMERIC,
    VARCHAR,
    VARCHAR,
    JSONB
) IS
    'Enregistre une métrique personnalisée associée à une exécution du pipeline.';


-- ============================================================================
-- 4. Enregistrer un événement ETL
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.register_etl_log(
    input_pipeline_run_id UUID,
    input_log_level VARCHAR,
    input_component VARCHAR,
    input_message TEXT,
    input_error_type VARCHAR DEFAULT NULL,
    input_error_details JSONB DEFAULT '{}'::JSONB
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF input_log_level NOT IN (
        'DEBUG',
        'INFO',
        'WARNING',
        'ERROR',
        'CRITICAL'
    ) THEN
        RAISE EXCEPTION
            'Niveau de log invalide : %',
            input_log_level;
    END IF;

    IF checkit.is_null_or_blank(input_component) THEN
        RAISE EXCEPTION
            'Le composant du log est obligatoire.';
    END IF;

    IF checkit.is_null_or_blank(input_message) THEN
        RAISE EXCEPTION
            'Le message du log est obligatoire.';
    END IF;

    IF input_error_details IS NULL
       OR JSONB_TYPEOF(input_error_details) <> 'object' THEN
        RAISE EXCEPTION
            'Les détails d’erreur doivent être un objet JSON.';
    END IF;

    INSERT INTO checkit.etl_logs (
        pipeline_run_id,
        log_level,
        component,
        message,
        error_type,
        error_details
    )
    VALUES (
        input_pipeline_run_id,
        input_log_level,
        input_component,
        input_message,
        input_error_type,
        input_error_details
    );
END;
$$;

COMMENT ON PROCEDURE checkit.register_etl_log(
    UUID,
    VARCHAR,
    VARCHAR,
    TEXT,
    VARCHAR,
    JSONB
) IS
    'Enregistre un événement structuré du pipeline ETL.';


-- ============================================================================
-- 5. Créer ou mettre à jour une version de dataset
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.create_dataset_version(
    input_version_name VARCHAR,
    input_pipeline_run_id UUID,
    input_description TEXT DEFAULT NULL,
    input_dataset_path TEXT DEFAULT NULL,
    input_checksum VARCHAR DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    computed_article_count INTEGER;
    computed_image_count INTEGER;
    computed_label_count INTEGER;
BEGIN
    IF checkit.is_null_or_blank(input_version_name) THEN
        RAISE EXCEPTION
            'Le nom de version du dataset est obligatoire.';
    END IF;

    SELECT COUNT(*)
    INTO computed_article_count
    FROM checkit.articles
    WHERE pipeline_run_id = input_pipeline_run_id;

    SELECT COUNT(*)
    INTO computed_image_count
    FROM checkit.images AS i
    INNER JOIN checkit.articles AS a
        ON a.id = i.article_id
    WHERE a.pipeline_run_id = input_pipeline_run_id;

    SELECT COUNT(*)
    INTO computed_label_count
    FROM checkit.article_labels AS al
    INNER JOIN checkit.articles AS a
        ON a.id = al.article_id
    WHERE a.pipeline_run_id = input_pipeline_run_id
      AND al.is_active = TRUE;

    INSERT INTO checkit.dataset_versions (
        version_name,
        pipeline_run_id,
        description,
        article_count,
        image_count,
        label_count,
        dataset_path,
        checksum
    )
    VALUES (
        input_version_name,
        input_pipeline_run_id,
        input_description,
        computed_article_count,
        computed_image_count,
        computed_label_count,
        input_dataset_path,
        input_checksum
    )
    ON CONFLICT (version_name)
    DO UPDATE SET
        pipeline_run_id = EXCLUDED.pipeline_run_id,
        description = EXCLUDED.description,
        article_count = EXCLUDED.article_count,
        image_count = EXCLUDED.image_count,
        label_count = EXCLUDED.label_count,
        dataset_path = EXCLUDED.dataset_path,
        checksum = EXCLUDED.checksum;
END;
$$;

COMMENT ON PROCEDURE checkit.create_dataset_version(
    VARCHAR,
    UUID,
    TEXT,
    TEXT,
    VARCHAR
) IS
    'Crée ou met à jour une version de dataset en calculant automatiquement ses volumes.';


-- ============================================================================
-- 6. Purger les anciens logs ETL
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.purge_old_etl_logs(
    retention_days INTEGER DEFAULT 90
)
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    IF retention_days IS NULL
       OR retention_days < 1 THEN
        RAISE EXCEPTION
            'La durée de conservation doit être supérieure ou égale à 1 jour.';
    END IF;

    DELETE FROM checkit.etl_logs
    WHERE created_at
        < CURRENT_TIMESTAMP
          - MAKE_INTERVAL(days => retention_days);

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RAISE NOTICE
        '% log(s) ETL supprimé(s).',
        deleted_count;
END;
$$;

COMMENT ON PROCEDURE checkit.purge_old_etl_logs(INTEGER) IS
    'Supprime les logs ETL plus anciens que la durée de conservation indiquée.';


-- ============================================================================
-- 7. Purger les anciennes métriques
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.purge_old_pipeline_metrics(
    retention_days INTEGER DEFAULT 365
)
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    IF retention_days IS NULL
       OR retention_days < 1 THEN
        RAISE EXCEPTION
            'La durée de conservation doit être supérieure ou égale à 1 jour.';
    END IF;

    DELETE FROM checkit.pipeline_metrics
    WHERE measured_at
        < CURRENT_TIMESTAMP
          - MAKE_INTERVAL(days => retention_days);

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RAISE NOTICE
        '% métrique(s) supprimée(s).',
        deleted_count;
END;
$$;

COMMENT ON PROCEDURE checkit.purge_old_pipeline_metrics(INTEGER) IS
    'Supprime les métriques plus anciennes que la durée de conservation indiquée.';


-- ============================================================================
-- 8. Purger les exécutions anciennes sans données associées
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.purge_empty_pipeline_runs(
    retention_days INTEGER DEFAULT 180
)
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    IF retention_days IS NULL
       OR retention_days < 1 THEN
        RAISE EXCEPTION
            'La durée de conservation doit être supérieure ou égale à 1 jour.';
    END IF;

    DELETE FROM checkit.pipeline_runs AS pr
    WHERE pr.started_at
        < CURRENT_TIMESTAMP
          - MAKE_INTERVAL(days => retention_days)
      AND NOT EXISTS (
          SELECT 1
          FROM checkit.articles AS a
          WHERE a.pipeline_run_id = pr.id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM checkit.dataset_versions AS dv
          WHERE dv.pipeline_run_id = pr.id
      );

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RAISE NOTICE
        '% exécution(s) vide(s) supprimée(s).',
        deleted_count;
END;
$$;

COMMENT ON PROCEDURE checkit.purge_empty_pipeline_runs(INTEGER) IS
    'Supprime les anciennes exécutions sans article ni version de dataset associée.';


-- ============================================================================
-- 9. Recalculer les empreintes manquantes des articles
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.refresh_missing_article_hashes()
LANGUAGE plpgsql
AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE checkit.articles
    SET
        title_hash = COALESCE(
            title_hash,
            checkit.generate_title_hash(title)
        ),

        content_hash = COALESCE(
            content_hash,
            checkit.generate_content_hash(content)
        ),

        canonical_url_hash = COALESCE(
            canonical_url_hash,
            checkit.generate_url_hash(canonical_url)
        ),

        updated_at = CURRENT_TIMESTAMP

    WHERE title_hash IS NULL
       OR content_hash IS NULL
       OR (
           canonical_url IS NOT NULL
           AND canonical_url_hash IS NULL
       );

    GET DIAGNOSTICS updated_count = ROW_COUNT;

    RAISE NOTICE
        '% article(s) mis à jour.',
        updated_count;
END;
$$;

COMMENT ON PROCEDURE checkit.refresh_missing_article_hashes() IS
    'Recalcule les empreintes manquantes des titres, contenus et URLs canoniques.';


-- ============================================================================
-- 10. Recalculer les ratios d’image manquants
-- ============================================================================

CREATE OR REPLACE PROCEDURE checkit.refresh_missing_image_ratios()
LANGUAGE plpgsql
AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE checkit.images
    SET
        aspect_ratio = checkit.compute_aspect_ratio(
            width,
            height
        ),

        updated_at = CURRENT_TIMESTAMP

    WHERE aspect_ratio IS NULL
      AND width IS NOT NULL
      AND height IS NOT NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;

    RAISE NOTICE
        '% image(s) mise(s) à jour.',
        updated_count;
END;
$$;

COMMENT ON PROCEDURE checkit.refresh_missing_image_ratios() IS
    'Calcule les ratios largeur/hauteur manquants des images.';


-- ============================================================================
-- 11. Propriétaires
-- ============================================================================

ALTER PROCEDURE checkit.finalize_pipeline_run(
    UUID,
    VARCHAR,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    INTEGER,
    TEXT
)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.update_source_run_status(
    VARCHAR,
    BOOLEAN,
    TEXT
)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.register_pipeline_metric(
    UUID,
    VARCHAR,
    NUMERIC,
    VARCHAR,
    VARCHAR,
    JSONB
)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.register_etl_log(
    UUID,
    VARCHAR,
    VARCHAR,
    TEXT,
    VARCHAR,
    JSONB
)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.create_dataset_version(
    VARCHAR,
    UUID,
    TEXT,
    TEXT,
    VARCHAR
)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.purge_old_etl_logs(INTEGER)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.purge_old_pipeline_metrics(INTEGER)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.purge_empty_pipeline_runs(INTEGER)
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.refresh_missing_article_hashes()
OWNER TO checkit_et1;

ALTER PROCEDURE checkit.refresh_missing_image_ratios()
OWNER TO checkit_et1;


COMMIT;


-- ============================================================================
-- 12. Vérification
-- ============================================================================

SELECT
    routine_schema,
    routine_name,
    routine_type
FROM information_schema.routines
WHERE routine_schema = 'checkit'
  AND routine_type = 'PROCEDURE'
ORDER BY routine_name;