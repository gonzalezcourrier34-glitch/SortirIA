-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/05_views.sql
-- Version : 2.1
-- Objet   : Création des vues de consultation, qualité, monitoring,
--           features IA et prédictions de modèles.
--
-- Prérequis :
--   - exécuter sql/00_database.sql ;
--   - exécuter sql/01_articles.sql ;
--   - exécuter sql/02_images_labels.sql ;
--   - exécuter sql/03_features_predictions.sql ;
--   - exécuter sql/04_indexes.sql.
--
-- Les vues sont réexécutables et remplacées si elles existent déjà.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- 1. Vue complète des articles
CREATE OR REPLACE VIEW checkit.vw_articles_complete AS
SELECT
    a.id AS article_id,
    a.external_id,

    s.id AS source_id,
    s.source_key,
    s.display_name AS source_name,
    s.source_type,
    s.default_language AS source_default_language,

    -- Vocabulaire aligné avec le schéma Python CheckIt.AI.
    a.title,
    a.content AS text,
    a.original_url AS url,
    a.canonical_url,
    a.author,
    a.language,
    a.category,
    a.published_at,
    a.extracted_at,
    a.transformed_at,
    a.dataset_role,

    a.data_quality_status,
    a.rejection_reason,
    a.transformation_version,

    a.title_hash,
    a.content_hash,
    a.canonical_url_hash,

    img.image_id,
    img.image_url,
    img.image_path,
    img.image_file_name,
    img.image_format,
    img.image_mime_type,
    img.image_width,
    img.image_height,
    img.image_aspect_ratio,
    img.image_size_bytes,
    img.image_is_valid,
    img.image_validation_status,
    img.text_image_association_status,
    img.text_image_association_score,

    gt.label AS ground_truth_label,
    gt.label_type AS ground_truth_type,
    gt.label_source AS ground_truth_source,
    gt.annotator AS ground_truth_annotator,
    gt.confidence AS ground_truth_confidence,

    pred.prediction_id,
    pred.model_name,
    pred.model_version,
    pred.task_type,
    pred.predicted_label,
    pred.prediction_confidence,
    pred.predicted_at,

    a.pipeline_run_id,
    pr.pipeline_name,
    pr.pipeline_version,
    pr.airflow_run_id,
    pr.status AS pipeline_status,

    a.created_at,
    a.updated_at

FROM checkit.articles AS a

INNER JOIN checkit.sources AS s
    ON s.id = a.source_id

LEFT JOIN checkit.pipeline_runs AS pr
    ON pr.id = a.pipeline_run_id

LEFT JOIN LATERAL (
    SELECT
        i.id AS image_id,
        i.remote_url AS image_url,
        i.local_path AS image_path,
        i.file_name AS image_file_name,
        i.file_format AS image_format,
        i.mime_type AS image_mime_type,
        i.width AS image_width,
        i.height AS image_height,
        i.aspect_ratio AS image_aspect_ratio,
        i.size_bytes AS image_size_bytes,
        i.is_valid AS image_is_valid,
        i.validation_status AS image_validation_status,
        i.association_status AS text_image_association_status,
        i.association_score AS text_image_association_score
    FROM checkit.images AS i
    WHERE i.article_id = a.id
    ORDER BY
        i.is_primary DESC,
        i.image_position ASC,
        i.id ASC
    LIMIT 1
) AS img
    ON TRUE

LEFT JOIN LATERAL (
    SELECT
        al.label,
        al.label_type,
        al.label_source,
        al.annotator,
        al.confidence
    FROM checkit.article_labels AS al
    WHERE al.article_id = a.id
      AND al.is_ground_truth = TRUE
      AND al.is_active = TRUE
    ORDER BY
        al.confidence DESC NULLS LAST,
        al.labeled_at DESC,
        al.id DESC
    LIMIT 1
) AS gt
    ON TRUE

LEFT JOIN LATERAL (
    SELECT
        mp.id AS prediction_id,
        mp.model_name,
        mp.model_version,
        mp.task_type,
        mp.predicted_label,
        mp.confidence AS prediction_confidence,
        mp.predicted_at
    FROM checkit.model_predictions AS mp
    WHERE mp.article_id = a.id
      AND mp.prediction_status = 'success'
    ORDER BY
        mp.predicted_at DESC,
        mp.id DESC
    LIMIT 1
) AS pred
    ON TRUE;

COMMENT ON VIEW checkit.vw_articles_complete IS
    'Vue consolidée alignée avec le schéma article Python : source, texte, URL, image principale, vérité terrain, prédiction et exécution du pipeline.';


-- 2. Vue des statistiques par source
CREATE OR REPLACE VIEW checkit.vw_source_statistics AS
SELECT
    s.id AS source_id,
    s.source_key,
    s.display_name AS source_name,
    s.source_type,
    s.is_active,

    COUNT(a.id) AS article_count,

    COUNT(a.id) FILTER (
        WHERE a.data_quality_status = 'valid'
    ) AS valid_article_count,

    COUNT(a.id) FILTER (
        WHERE a.data_quality_status = 'warning'
    ) AS warning_article_count,

    COUNT(a.id) FILTER (
        WHERE a.data_quality_status = 'rejected'
    ) AS rejected_article_count,

    COUNT(a.id) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM checkit.images AS i
            WHERE i.article_id = a.id
              AND i.is_valid = TRUE
        )
    ) AS article_with_valid_image_count,

    COUNT(a.id) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM checkit.article_labels AS al
            WHERE al.article_id = a.id
              AND al.is_ground_truth = TRUE
              AND al.is_active = TRUE
        )
    ) AS labeled_article_count,

    COUNT(a.id) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM checkit.model_predictions AS mp
            WHERE mp.article_id = a.id
              AND mp.prediction_status = 'success'
        )
    ) AS predicted_article_count,

    ROUND(
        100.0
        * COUNT(a.id) FILTER (
            WHERE a.data_quality_status = 'valid'
        )
        / NULLIF(COUNT(a.id), 0),
        2
    ) AS valid_rate_percent,

    ROUND(
        100.0
        * COUNT(a.id) FILTER (
            WHERE EXISTS (
                SELECT 1
                FROM checkit.images AS i
                WHERE i.article_id = a.id
                  AND i.is_valid = TRUE
            )
        )
        / NULLIF(COUNT(a.id), 0),
        2
    ) AS valid_image_rate_percent,

    MIN(a.published_at) AS oldest_publication,
    MAX(a.published_at) AS newest_publication,
    MAX(a.extracted_at) AS latest_extraction

FROM checkit.sources AS s

LEFT JOIN checkit.articles AS a
    ON a.source_id = s.id

GROUP BY
    s.id,
    s.source_key,
    s.display_name,
    s.source_type,
    s.is_active;

COMMENT ON VIEW checkit.vw_source_statistics IS
    'Statistiques de volume, qualité, images, labels et prédictions par source.';


-- 3. Vue de qualité globale des articles
CREATE OR REPLACE VIEW checkit.vw_quality_statistics AS
SELECT
    COUNT(*) AS total_articles,

    COUNT(*) FILTER (
        WHERE data_quality_status = 'valid'
    ) AS valid_articles,

    COUNT(*) FILTER (
        WHERE data_quality_status = 'warning'
    ) AS warning_articles,

    COUNT(*) FILTER (
        WHERE data_quality_status = 'rejected'
    ) AS rejected_articles,

    COUNT(*) FILTER (
        WHERE title IS NULL
           OR BTRIM(title) = ''
    ) AS missing_title_count,

    COUNT(*) FILTER (
        WHERE content IS NULL
           OR BTRIM(content) = ''
    ) AS missing_content_count,

    COUNT(*) FILTER (
        WHERE canonical_url IS NULL
           OR BTRIM(canonical_url) = ''
    ) AS missing_url_count,

    COUNT(*) FILTER (
        WHERE published_at IS NULL
    ) AS missing_publication_date_count,

    COUNT(*) FILTER (
        WHERE author IS NULL
           OR BTRIM(author) = ''
    ) AS missing_author_count,

    COUNT(*) FILTER (
        WHERE language IS NULL
           OR BTRIM(language) = ''
    ) AS missing_language_count,

    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM checkit.images AS i
            WHERE i.article_id = articles.id
              AND i.is_valid = TRUE
        )
    ) AS articles_with_valid_image_count,

    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM checkit.article_labels AS al
            WHERE al.article_id = articles.id
              AND al.is_ground_truth = TRUE
              AND al.is_active = TRUE
        )
    ) AS articles_with_ground_truth_count,

    ROUND(
        100.0
        * COUNT(*) FILTER (
            WHERE data_quality_status = 'valid'
        )
        / NULLIF(COUNT(*), 0),
        2
    ) AS valid_rate_percent,

    ROUND(
        100.0
        * COUNT(*) FILTER (
            WHERE EXISTS (
                SELECT 1
                FROM checkit.images AS i
                WHERE i.article_id = articles.id
                  AND i.is_valid = TRUE
            )
        )
        / NULLIF(COUNT(*), 0),
        2
    ) AS valid_image_rate_percent,

    ROUND(
        100.0
        * COUNT(*) FILTER (
            WHERE EXISTS (
                SELECT 1
                FROM checkit.article_labels AS al
                WHERE al.article_id = articles.id
                  AND al.is_ground_truth = TRUE
                  AND al.is_active = TRUE
            )
        )
        / NULLIF(COUNT(*), 0),
        2
    ) AS ground_truth_coverage_percent

FROM checkit.articles AS articles;

COMMENT ON VIEW checkit.vw_quality_statistics IS
    'Indicateurs globaux de qualité, couverture image et disponibilité de la vérité terrain.';


-- 4. Vue des statistiques d'exécution du pipeline
CREATE OR REPLACE VIEW checkit.vw_pipeline_statistics AS
SELECT
    pr.id AS pipeline_run_id,
    pr.dag_id,
    pr.airflow_run_id,
    pr.pipeline_name,
    pr.pipeline_version,
    pr.code_version,
    pr.airflow_version,
    pr.status,
    pr.started_at,
    pr.finished_at,

    COALESCE(
        pr.duration_seconds,
        EXTRACT(
            EPOCH FROM (
                COALESCE(pr.finished_at, CURRENT_TIMESTAMP)
                - pr.started_at
            )
        )
    ) AS effective_duration_seconds,

    pr.extraction_duration_seconds,
    pr.transformation_duration_seconds,
    pr.load_duration_seconds,

    pr.extracted_count,
    pr.transformed_count,
    pr.valid_count,
    pr.rejected_count,
    pr.loaded_count,

    pr.images_downloaded_count,
    pr.images_valid_count,
    pr.images_invalid_count,

    pr.peak_memory_mb,
    pr.average_cpu_percent,

    ROUND(
        100.0
        * pr.valid_count
        / NULLIF(pr.extracted_count, 0),
        2
    ) AS valid_rate_percent,

    ROUND(
        100.0
        * pr.rejected_count
        / NULLIF(pr.extracted_count, 0),
        2
    ) AS rejection_rate_percent,

    ROUND(
        100.0
        * pr.loaded_count
        / NULLIF(pr.valid_count, 0),
        2
    ) AS load_success_rate_percent,

    ROUND(
        100.0
        * pr.images_valid_count
        / NULLIF(pr.images_downloaded_count, 0),
        2
    ) AS valid_image_rate_percent,

    pr.error_message,
    pr.run_metadata,
    pr.created_at,
    pr.updated_at

FROM checkit.pipeline_runs AS pr;

COMMENT ON VIEW checkit.vw_pipeline_statistics IS
    'KPI détaillés pour chaque exécution locale ou Airflow du pipeline.';


-- 5. Vue quotidienne des exécutions
CREATE OR REPLACE VIEW checkit.vw_daily_pipeline_statistics AS
SELECT
    DATE(started_at) AS execution_date,
    pipeline_name,

    COUNT(*) AS run_count,

    COUNT(*) FILTER (
        WHERE status = 'success'
    ) AS successful_run_count,

    COUNT(*) FILTER (
        WHERE status = 'partial'
    ) AS partial_run_count,

    COUNT(*) FILTER (
        WHERE status = 'failed'
    ) AS failed_run_count,

    SUM(extracted_count) AS extracted_count,
    SUM(valid_count) AS valid_count,
    SUM(rejected_count) AS rejected_count,
    SUM(loaded_count) AS loaded_count,

    ROUND(
        AVG(
            COALESCE(
                duration_seconds,
                EXTRACT(
                    EPOCH FROM (
                        COALESCE(finished_at, CURRENT_TIMESTAMP)
                        - started_at
                    )
                )
            )
        )::NUMERIC,
        2
    ) AS average_duration_seconds,

    ROUND(
        100.0
        * COUNT(*) FILTER (
            WHERE status = 'success'
        )
        / NULLIF(COUNT(*), 0),
        2
    ) AS success_rate_percent

FROM checkit.pipeline_runs

GROUP BY
    DATE(started_at),
    pipeline_name;

COMMENT ON VIEW checkit.vw_daily_pipeline_statistics IS
    'Agrégation quotidienne des volumes, durées et taux de succès du pipeline.';


-- 6. Vue de qualité des images
CREATE OR REPLACE VIEW checkit.vw_image_quality_statistics AS
SELECT
    COUNT(*) AS total_images,

    COUNT(*) FILTER (
        WHERE is_valid = TRUE
    ) AS valid_images,

    COUNT(*) FILTER (
        WHERE validation_status = 'invalid'
    ) AS invalid_images,

    COUNT(*) FILTER (
        WHERE validation_status = 'pending'
    ) AS pending_images,

    COUNT(*) FILTER (
        WHERE association_status = 'technical_match'
    ) AS technical_match_count,

    COUNT(*) FILTER (
        WHERE association_status = 'semantic_match'
    ) AS semantic_match_count,

    COUNT(*) FILTER (
        WHERE association_status = 'mismatch'
    ) AS mismatch_count,

    COUNT(*) FILTER (
        WHERE association_status = 'unchecked'
    ) AS unchecked_count,

    ROUND(
        100.0
        * COUNT(*) FILTER (
            WHERE is_valid = TRUE
        )
        / NULLIF(COUNT(*), 0),
        2
    ) AS valid_image_rate_percent,

    ROUND(
        100.0
        * COUNT(*) FILTER (
            WHERE association_status IN (
                'technical_match',
                'semantic_match'
            )
        )
        / NULLIF(COUNT(*), 0),
        2
    ) AS association_match_rate_percent,

    ROUND(AVG(width)::NUMERIC, 2) AS average_width,
    ROUND(AVG(height)::NUMERIC, 2) AS average_height,
    ROUND(AVG(aspect_ratio)::NUMERIC, 4) AS average_aspect_ratio,
    ROUND(AVG(size_bytes)::NUMERIC, 2) AS average_size_bytes,
    ROUND(AVG(blur_score)::NUMERIC, 4) AS average_blur_score,
    ROUND(AVG(brightness_score)::NUMERIC, 4) AS average_brightness_score,
    ROUND(AVG(entropy_score)::NUMERIC, 4) AS average_entropy_score,
    ROUND(AVG(download_duration_ms)::NUMERIC, 2) AS average_download_duration_ms

FROM checkit.images;

COMMENT ON VIEW checkit.vw_image_quality_statistics IS
    'Indicateurs globaux sur la qualité technique, visuelle et sémantique des images.';


-- 7. Vue de distribution des labels
CREATE OR REPLACE VIEW checkit.vw_label_distribution AS
SELECT
    al.label,
    al.label_type,
    al.label_source,
    al.is_ground_truth,
    al.is_active,

    COUNT(*) AS label_count,

    ROUND(
        AVG(al.confidence)::NUMERIC,
        4
    ) AS average_confidence,

    MIN(al.labeled_at) AS first_label_date,
    MAX(al.labeled_at) AS latest_label_date

FROM checkit.article_labels AS al

GROUP BY
    al.label,
    al.label_type,
    al.label_source,
    al.is_ground_truth,
    al.is_active;

COMMENT ON VIEW checkit.vw_label_distribution IS
    'Distribution des vérités terrain et annotations par origine.';


-- 8. Vue de distribution des features
CREATE OR REPLACE VIEW checkit.vw_feature_statistics AS
SELECT
    af.feature_group,
    af.feature_name,
    af.feature_type,
    af.feature_version,
    af.producer_name,
    af.producer_version,

    COUNT(*) AS feature_count,
    COUNT(DISTINCT af.article_id) AS article_count,
    COUNT(DISTINCT af.image_id) FILTER (
        WHERE af.image_id IS NOT NULL
    ) AS image_count,

    ROUND(
        AVG(af.numeric_value)::NUMERIC,
        6
    ) AS average_numeric_value,

    MIN(af.computed_at) AS first_computation,
    MAX(af.computed_at) AS latest_computation

FROM checkit.article_features AS af

GROUP BY
    af.feature_group,
    af.feature_name,
    af.feature_type,
    af.feature_version,
    af.producer_name,
    af.producer_version;

COMMENT ON VIEW checkit.vw_feature_statistics IS
    'Statistiques de couverture et versionnement des features calculées.';


-- 9. Vue des prédictions de modèles
CREATE OR REPLACE VIEW checkit.vw_model_prediction_statistics AS
SELECT
    mp.model_name,
    mp.model_version,
    mp.task_type,
    mp.prediction_status,
    mp.predicted_label,

    COUNT(*) AS prediction_count,
    COUNT(DISTINCT mp.article_id) AS article_count,

    ROUND(
        AVG(mp.confidence)::NUMERIC,
        4
    ) AS average_confidence,

    ROUND(
        MIN(mp.confidence)::NUMERIC,
        4
    ) AS minimum_confidence,

    ROUND(
        MAX(mp.confidence)::NUMERIC,
        4
    ) AS maximum_confidence,

    MIN(mp.predicted_at) AS first_prediction,
    MAX(mp.predicted_at) AS latest_prediction

FROM checkit.model_predictions AS mp

GROUP BY
    mp.model_name,
    mp.model_version,
    mp.task_type,
    mp.prediction_status,
    mp.predicted_label;

COMMENT ON VIEW checkit.vw_model_prediction_statistics IS
    'Distribution des décisions et niveaux de confiance par modèle et version.';


-- 10. Vue de comparaison vérité terrain / prédiction
CREATE OR REPLACE VIEW checkit.vw_prediction_ground_truth_comparison AS
SELECT
    a.id AS article_id,
    s.source_key,
    s.display_name AS source_name,

    gt.label AS ground_truth_label,
    gt.label_source AS ground_truth_source,

    mp.model_name,
    mp.model_version,
    mp.task_type,
    mp.predicted_label,
    mp.confidence,
    mp.predicted_at,

    CASE
        WHEN gt.label IS NULL THEN NULL
        WHEN LOWER(mp.predicted_label) = LOWER(gt.label) THEN TRUE
        ELSE FALSE
    END AS prediction_is_correct

FROM checkit.articles AS a

INNER JOIN checkit.sources AS s
    ON s.id = a.source_id

LEFT JOIN LATERAL (
    SELECT
        al.label,
        al.label_source
    FROM checkit.article_labels AS al
    WHERE al.article_id = a.id
      AND al.is_ground_truth = TRUE
      AND al.is_active = TRUE
    ORDER BY
        al.confidence DESC NULLS LAST,
        al.labeled_at DESC,
        al.id DESC
    LIMIT 1
) AS gt
    ON TRUE

INNER JOIN checkit.model_predictions AS mp
    ON mp.article_id = a.id
   AND mp.prediction_status = 'success';

COMMENT ON VIEW checkit.vw_prediction_ground_truth_comparison IS
    'Comparaison entre vérité terrain et prédictions normalisées pour l’évaluation.';


-- 11. Vue des métriques personnalisées
CREATE OR REPLACE VIEW checkit.vw_pipeline_metrics AS
SELECT
    pm.id AS metric_id,
    pm.pipeline_run_id,

    pr.pipeline_name,
    pr.pipeline_version,
    pr.status AS pipeline_status,
    pr.started_at,

    pm.metric_name,
    pm.metric_value,
    pm.metric_unit,
    pm.component,
    pm.metric_metadata,
    pm.measured_at

FROM checkit.pipeline_metrics AS pm

INNER JOIN checkit.pipeline_runs AS pr
    ON pr.id = pm.pipeline_run_id;

COMMENT ON VIEW checkit.vw_pipeline_metrics IS
    'Vue consolidée des métriques personnalisées et de leur exécution associée.';


-- 12. Vue des erreurs ETL récentes
CREATE OR REPLACE VIEW checkit.vw_recent_etl_errors AS
SELECT
    el.id AS log_id,
    el.pipeline_run_id,

    pr.pipeline_name,
    pr.airflow_run_id,
    pr.status AS pipeline_status,

    el.log_level,
    el.component,
    el.message,
    el.error_type,
    el.error_details,
    el.created_at

FROM checkit.etl_logs AS el

LEFT JOIN checkit.pipeline_runs AS pr
    ON pr.id = el.pipeline_run_id

WHERE el.log_level IN (
    'ERROR',
    'CRITICAL'
)

ORDER BY el.created_at DESC;

COMMENT ON VIEW checkit.vw_recent_etl_errors IS
    'Dernières erreurs et erreurs critiques du pipeline ETL.';


-- 13. Propriétaires
ALTER VIEW checkit.vw_articles_complete
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_source_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_quality_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_pipeline_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_daily_pipeline_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_image_quality_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_label_distribution
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_feature_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_model_prediction_statistics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_prediction_ground_truth_comparison
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_pipeline_metrics
    OWNER TO checkit_et1;

ALTER VIEW checkit.vw_recent_etl_errors
    OWNER TO checkit_et1;


COMMIT;


-- 14. Vérification
SELECT
    table_schema,
    table_name
FROM information_schema.views
WHERE table_schema = 'checkit'
ORDER BY table_name;