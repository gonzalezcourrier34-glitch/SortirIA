-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/04_indexes.sql
-- Version : 2.0
-- Objet   : Création des index utiles aux recherches, jointures, KPI,
--           déduplication, features IA et prédictions de modèles.
--
-- Prérequis :
--   - exécuter sql/00_database.sql ;
--   - exécuter sql/01_articles.sql ;
--   - exécuter sql/02_images_labels.sql ;
--   - exécuter sql/03_features_predictions.sql.
--
-- Le script est réexécutable sans supprimer les données existantes.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Index sur les sources
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_sources_type_active
ON checkit.sources (
    source_type,
    is_active
);

CREATE INDEX IF NOT EXISTS idx_sources_display_name
ON checkit.sources (
    LOWER(display_name)
);

CREATE INDEX IF NOT EXISTS idx_sources_last_successful_run
ON checkit.sources (
    last_successful_run DESC
)
WHERE last_successful_run IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sources_configuration
ON checkit.sources
USING GIN (
    configuration
);


-- ============================================================================
-- 2. Index sur les exécutions du pipeline
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at
ON checkit.pipeline_runs (
    started_at DESC
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
ON checkit.pipeline_runs (
    status
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline_name_started_at
ON checkit.pipeline_runs (
    pipeline_name,
    started_at DESC
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_airflow_run_id
ON checkit.pipeline_runs (
    airflow_run_id
)
WHERE airflow_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_dag_id
ON checkit.pipeline_runs (
    dag_id
)
WHERE dag_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_metadata
ON checkit.pipeline_runs
USING GIN (
    run_metadata
);


-- ============================================================================
-- 3. Index sur les métriques du pipeline
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_pipeline_metrics_run_id
ON checkit.pipeline_metrics (
    pipeline_run_id
);

CREATE INDEX IF NOT EXISTS idx_pipeline_metrics_name
ON checkit.pipeline_metrics (
    metric_name
);

CREATE INDEX IF NOT EXISTS idx_pipeline_metrics_component
ON checkit.pipeline_metrics (
    component
)
WHERE component IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pipeline_metrics_measured_at
ON checkit.pipeline_metrics (
    measured_at DESC
);

CREATE INDEX IF NOT EXISTS idx_pipeline_metrics_metadata
ON checkit.pipeline_metrics
USING GIN (
    metric_metadata
);


-- ============================================================================
-- 4. Index sur la configuration
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_pipeline_configuration_active
ON checkit.pipeline_configuration (
    is_active
)
WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_pipeline_configuration_sensitive
ON checkit.pipeline_configuration (
    is_sensitive
)
WHERE is_sensitive = TRUE;


-- ============================================================================
-- 5. Index sur les versions de datasets
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_dataset_versions_created_at
ON checkit.dataset_versions (
    created_at DESC
);

CREATE INDEX IF NOT EXISTS idx_dataset_versions_pipeline_run_id
ON checkit.dataset_versions (
    pipeline_run_id
)
WHERE pipeline_run_id IS NOT NULL;


-- ============================================================================
-- 6. Index sur les logs ETL
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_etl_logs_pipeline_run_id
ON checkit.etl_logs (
    pipeline_run_id
)
WHERE pipeline_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_etl_logs_level
ON checkit.etl_logs (
    log_level
);

CREATE INDEX IF NOT EXISTS idx_etl_logs_component
ON checkit.etl_logs (
    component
);

CREATE INDEX IF NOT EXISTS idx_etl_logs_created_at
ON checkit.etl_logs (
    created_at DESC
);

CREATE INDEX IF NOT EXISTS idx_etl_logs_errors
ON checkit.etl_logs (
    created_at DESC
)
WHERE log_level IN (
    'ERROR',
    'CRITICAL'
);

CREATE INDEX IF NOT EXISTS idx_etl_logs_details
ON checkit.etl_logs
USING GIN (
    error_details
);


-- ============================================================================
-- 7. Index de jointure sur les articles
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_articles_source_id
ON checkit.articles (
    source_id
);

CREATE INDEX IF NOT EXISTS idx_articles_pipeline_run_id
ON checkit.articles (
    pipeline_run_id
)
WHERE pipeline_run_id IS NOT NULL;


-- ============================================================================
-- 8. Index fonctionnels sur les articles
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_articles_published_at
ON checkit.articles (
    published_at DESC
)
WHERE published_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_extracted_at
ON checkit.articles (
    extracted_at DESC
);

CREATE INDEX IF NOT EXISTS idx_articles_language
ON checkit.articles (
    language
)
WHERE language IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_dataset_role
ON checkit.articles (
    dataset_role
)
WHERE dataset_role IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_quality_status
ON checkit.articles (
    data_quality_status
);

CREATE INDEX IF NOT EXISTS idx_articles_category
ON checkit.articles (
    LOWER(category)
)
WHERE category IS NOT NULL
  AND BTRIM(category) <> '';

CREATE INDEX IF NOT EXISTS idx_articles_author
ON checkit.articles (
    LOWER(author)
)
WHERE author IS NOT NULL
  AND BTRIM(author) <> '';

CREATE INDEX IF NOT EXISTS idx_articles_transformed_at
ON checkit.articles (
    transformed_at DESC
)
WHERE transformed_at IS NOT NULL;


-- ============================================================================
-- 9. Index de déduplication sur les articles
-- ============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_articles_canonical_url
ON checkit.articles (
    canonical_url
)
WHERE canonical_url IS NOT NULL
  AND BTRIM(canonical_url) <> '';

CREATE INDEX IF NOT EXISTS idx_articles_content_hash
ON checkit.articles (
    content_hash
)
WHERE content_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_title_hash
ON checkit.articles (
    title_hash
)
WHERE title_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_canonical_url_hash
ON checkit.articles (
    canonical_url_hash
)
WHERE canonical_url_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_source_external_id
ON checkit.articles (
    source_id,
    external_id
)
WHERE external_id IS NOT NULL
  AND BTRIM(external_id) <> '';


-- ============================================================================
-- 10. Index textuels PostgreSQL
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_articles_full_text
ON checkit.articles
USING GIN (
    to_tsvector(
        'simple',
        COALESCE(title, '')
        || ' '
        || COALESCE(content, '')
    )
);

CREATE INDEX IF NOT EXISTS idx_articles_raw_payload
ON checkit.articles
USING GIN (
    raw_payload
);


-- ============================================================================
-- 11. Index sur les images
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_images_article_id
ON checkit.images (
    article_id
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_images_primary_per_article
ON checkit.images (
    article_id
)
WHERE is_primary = TRUE;

CREATE INDEX IF NOT EXISTS idx_images_validation_status
ON checkit.images (
    validation_status
);

CREATE INDEX IF NOT EXISTS idx_images_is_valid
ON checkit.images (
    is_valid
)
WHERE is_valid = TRUE;

CREATE INDEX IF NOT EXISTS idx_images_association_status
ON checkit.images (
    association_status
);

CREATE INDEX IF NOT EXISTS idx_images_file_hash
ON checkit.images (
    file_hash
)
WHERE file_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_images_perceptual_hash
ON checkit.images (
    perceptual_hash
)
WHERE perceptual_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_images_local_path
ON checkit.images (
    local_path
)
WHERE local_path IS NOT NULL
  AND BTRIM(local_path) <> '';

CREATE INDEX IF NOT EXISTS idx_images_file_format
ON checkit.images (
    file_format
)
WHERE file_format IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_images_metadata
ON checkit.images
USING GIN (
    image_metadata
);


-- ============================================================================
-- 12. Index sur les labels
-- ============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_article_labels_identity
ON checkit.article_labels (
    article_id,
    label,
    label_type,
    COALESCE(label_source, '')
);

CREATE INDEX IF NOT EXISTS idx_article_labels_article_id
ON checkit.article_labels (
    article_id
);

CREATE INDEX IF NOT EXISTS idx_article_labels_label
ON checkit.article_labels (
    label
);

CREATE INDEX IF NOT EXISTS idx_article_labels_type
ON checkit.article_labels (
    label_type
);

CREATE INDEX IF NOT EXISTS idx_article_labels_source
ON checkit.article_labels (
    label_source
)
WHERE label_source IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_article_labels_ground_truth
ON checkit.article_labels (
    article_id,
    label
)
WHERE is_ground_truth = TRUE
  AND is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_article_labels_active
ON checkit.article_labels (
    is_active
)
WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_article_labels_labeled_at
ON checkit.article_labels (
    labeled_at DESC
);

CREATE INDEX IF NOT EXISTS idx_article_labels_metadata
ON checkit.article_labels
USING GIN (
    label_metadata
);


-- ============================================================================
-- 13. Index sur les features IA
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_article_features_article_id
ON checkit.article_features (
    article_id
);

CREATE INDEX IF NOT EXISTS idx_article_features_image_id
ON checkit.article_features (
    image_id
)
WHERE image_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_article_features_pipeline_run_id
ON checkit.article_features (
    pipeline_run_id
)
WHERE pipeline_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_article_features_group_name
ON checkit.article_features (
    feature_group,
    feature_name
);

CREATE INDEX IF NOT EXISTS idx_article_features_version
ON checkit.article_features (
    feature_name,
    feature_version
);

CREATE INDEX IF NOT EXISTS idx_article_features_producer
ON checkit.article_features (
    producer_name,
    producer_version
)
WHERE producer_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_article_features_computed_at
ON checkit.article_features (
    computed_at DESC
);

CREATE INDEX IF NOT EXISTS idx_article_features_metadata
ON checkit.article_features
USING GIN (
    feature_metadata
);

CREATE INDEX IF NOT EXISTS idx_article_features_json_value
ON checkit.article_features
USING GIN (
    json_value
)
WHERE json_value IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_article_features_embeddings
ON checkit.article_features (
    feature_name,
    producer_name,
    producer_version
)
WHERE feature_group = 'embedding';


-- ============================================================================
-- 14. Index sur les prédictions de modèles
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_model_predictions_article_id
ON checkit.model_predictions (
    article_id
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_image_id
ON checkit.model_predictions (
    image_id
)
WHERE image_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_model_predictions_pipeline_run_id
ON checkit.model_predictions (
    pipeline_run_id
)
WHERE pipeline_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_model_predictions_model
ON checkit.model_predictions (
    model_name,
    model_version
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_task_type
ON checkit.model_predictions (
    task_type
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_label
ON checkit.model_predictions (
    predicted_label
)
WHERE predicted_label IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_model_predictions_confidence
ON checkit.model_predictions (
    confidence DESC
)
WHERE confidence IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_model_predictions_status
ON checkit.model_predictions (
    prediction_status
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_predicted_at
ON checkit.model_predictions (
    predicted_at DESC
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_probabilities
ON checkit.model_predictions
USING GIN (
    probabilities
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_explanation
ON checkit.model_predictions
USING GIN (
    explanation
);


COMMIT;


-- ============================================================================
-- 15. Vérification
-- ============================================================================

SELECT
    schemaname,
    tablename,
    indexname
FROM pg_indexes
WHERE schemaname = 'checkit'
ORDER BY
    tablename,
    indexname;