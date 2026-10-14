-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/03_features_predictions.sql
-- Version : 1.0
-- Objet   : Création des tables article_features et model_predictions.
--
-- Architecture retenue :
--
--   article_features
--   ├── NLP
--   ├── Vision
--   ├── Multimodal
--   ├── Embeddings
--   └── Variables calculées
--
--   model_predictions
--   ├── modèle
--   ├── version
--   ├── probabilités
--   ├── décision
--   └── explication
--
-- Prérequis :
--   - exécuter sql/00_database.sql ;
--   - exécuter sql/01_articles.sql ;
--   - exécuter sql/02_images_labels.sql ;
--   - disposer des tables checkit.articles, checkit.images
--     et checkit.pipeline_runs.
--
-- Le script peut être rejoué.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Table des caractéristiques calculées
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.article_features (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Article auquel la caractéristique est associée.
    article_id VARCHAR(64) NOT NULL,

    -- Image facultative lorsque la caractéristique est visuelle.
    image_id BIGINT,

    -- Exécution ayant produit la caractéristique.
    pipeline_run_id UUID,

    -- ------------------------------------------------------------------------
    -- Identification de la caractéristique
    -- ------------------------------------------------------------------------

    feature_group VARCHAR(30) NOT NULL,
    feature_name VARCHAR(150) NOT NULL,
    feature_type VARCHAR(30) NOT NULL,

    -- Version fonctionnelle de la caractéristique.
    feature_version VARCHAR(50) NOT NULL DEFAULT '1.0',

    -- Modèle ou méthode ayant produit la caractéristique.
    producer_name VARCHAR(150),
    producer_version VARCHAR(50),

    -- ------------------------------------------------------------------------
    -- Valeurs possibles
    -- ------------------------------------------------------------------------

    numeric_value NUMERIC(24, 10),
    text_value TEXT,
    boolean_value BOOLEAN,
    json_value JSONB,
    vector_path TEXT,

    -- Dimension d'un embedding lorsque la valeur est stockée ailleurs.
    vector_dimension INTEGER,

    -- Niveau de confiance ou qualité éventuel.
    confidence NUMERIC(5, 4),

    -- Métadonnées complémentaires.
    feature_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- ------------------------------------------------------------------------
    -- Relations
    -- ------------------------------------------------------------------------

    CONSTRAINT fk_article_features_article
        FOREIGN KEY (article_id)
        REFERENCES checkit.articles(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT fk_article_features_image
        FOREIGN KEY (image_id)
        REFERENCES checkit.images(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT fk_article_features_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES checkit.pipeline_runs(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL,

    -- ------------------------------------------------------------------------
    -- Contraintes d'identification
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_article_features_group
        CHECK (
            feature_group IN (
                'nlp',
                'vision',
                'multimodal',
                'embedding',
                'metadata',
                'quality'
            )
        ),

    CONSTRAINT ck_article_features_name_not_blank
        CHECK (
            BTRIM(feature_name) <> ''
        ),

    CONSTRAINT ck_article_features_type
        CHECK (
            feature_type IN (
                'numeric',
                'text',
                'boolean',
                'json',
                'vector_path'
            )
        ),

    CONSTRAINT ck_article_features_version_not_blank
        CHECK (
            BTRIM(feature_version) <> ''
        ),

    CONSTRAINT ck_article_features_producer_name
        CHECK (
            producer_name IS NULL
            OR BTRIM(producer_name) <> ''
        ),

    CONSTRAINT ck_article_features_producer_version
        CHECK (
            producer_version IS NULL
            OR BTRIM(producer_version) <> ''
        ),

    -- ------------------------------------------------------------------------
    -- Cohérence entre le type et la valeur stockée
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_article_features_value_consistency
        CHECK (
            (
                feature_type = 'numeric'
                AND numeric_value IS NOT NULL
                AND text_value IS NULL
                AND boolean_value IS NULL
                AND json_value IS NULL
                AND vector_path IS NULL
            )
            OR
            (
                feature_type = 'text'
                AND numeric_value IS NULL
                AND text_value IS NOT NULL
                AND boolean_value IS NULL
                AND json_value IS NULL
                AND vector_path IS NULL
            )
            OR
            (
                feature_type = 'boolean'
                AND numeric_value IS NULL
                AND text_value IS NULL
                AND boolean_value IS NOT NULL
                AND json_value IS NULL
                AND vector_path IS NULL
            )
            OR
            (
                feature_type = 'json'
                AND numeric_value IS NULL
                AND text_value IS NULL
                AND boolean_value IS NULL
                AND json_value IS NOT NULL
                AND vector_path IS NULL
            )
            OR
            (
                feature_type = 'vector_path'
                AND numeric_value IS NULL
                AND text_value IS NULL
                AND boolean_value IS NULL
                AND json_value IS NULL
                AND vector_path IS NOT NULL
            )
        ),

    CONSTRAINT ck_article_features_text_value_not_blank
        CHECK (
            text_value IS NULL
            OR BTRIM(text_value) <> ''
        ),

    CONSTRAINT ck_article_features_vector_path_not_blank
        CHECK (
            vector_path IS NULL
            OR BTRIM(vector_path) <> ''
        ),

    CONSTRAINT ck_article_features_vector_dimension
        CHECK (
            vector_dimension IS NULL
            OR vector_dimension > 0
        ),

    CONSTRAINT ck_article_features_vector_dimension_consistency
        CHECK (
            feature_type = 'vector_path'
            OR vector_dimension IS NULL
        ),

    CONSTRAINT ck_article_features_confidence
        CHECK (
            confidence IS NULL
            OR confidence BETWEEN 0 AND 1
        ),

    CONSTRAINT ck_article_features_json_value
        CHECK (
            json_value IS NULL
            OR jsonb_typeof(json_value) IN (
                'object',
                'array',
                'string',
                'number',
                'boolean'
            )
        ),

    CONSTRAINT ck_article_features_metadata_object
        CHECK (
            jsonb_typeof(feature_metadata) = 'object'
        ),

    -- Une feature visuelle liée à une image doit référencer cette image.
    CONSTRAINT ck_article_features_image_consistency
        CHECK (
            feature_group <> 'vision'
            OR image_id IS NOT NULL
        ),

    -- Un embedding doit préciser son emplacement et sa dimension.
    CONSTRAINT ck_article_features_embedding_consistency
        CHECK (
            feature_group <> 'embedding'
            OR (
                feature_type = 'vector_path'
                AND vector_path IS NOT NULL
                AND vector_dimension IS NOT NULL
            )
        )
);


-- ============================================================================
-- 2. Documentation SQL de article_features
-- ============================================================================

COMMENT ON TABLE checkit.article_features IS
    'Caractéristiques calculées pour les articles, images et traitements multimodaux.';

COMMENT ON COLUMN checkit.article_features.feature_group IS
    'Famille de la caractéristique : nlp, vision, multimodal, embedding, metadata ou quality.';

COMMENT ON COLUMN checkit.article_features.feature_name IS
    'Nom fonctionnel de la caractéristique.';

COMMENT ON COLUMN checkit.article_features.feature_type IS
    'Type de valeur stockée : numeric, text, boolean, json ou vector_path.';

COMMENT ON COLUMN checkit.article_features.feature_version IS
    'Version de la définition fonctionnelle de la caractéristique.';

COMMENT ON COLUMN checkit.article_features.producer_name IS
    'Nom du modèle, de l’algorithme ou de la méthode ayant produit la caractéristique.';

COMMENT ON COLUMN checkit.article_features.vector_path IS
    'Chemin d’un embedding stocké hors de PostgreSQL.';

COMMENT ON COLUMN checkit.article_features.vector_dimension IS
    'Nombre de dimensions de l’embedding.';

COMMENT ON COLUMN checkit.article_features.confidence IS
    'Niveau de confiance éventuel compris entre 0 et 1.';


-- ============================================================================
-- 3. Déclencheur updated_at pour article_features
-- ============================================================================

DROP TRIGGER IF EXISTS trg_article_features_set_updated_at
ON checkit.article_features;

CREATE TRIGGER trg_article_features_set_updated_at
BEFORE UPDATE ON checkit.article_features
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 4. Table des prédictions de modèles
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.model_predictions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Article analysé.
    article_id VARCHAR(64) NOT NULL,

    -- Image facultative dans le cas d'une prédiction visuelle.
    image_id BIGINT,

    -- Exécution ayant produit la prédiction.
    pipeline_run_id UUID,

    -- ------------------------------------------------------------------------
    -- Identification du modèle
    -- ------------------------------------------------------------------------

    model_name VARCHAR(150) NOT NULL,
    model_version VARCHAR(50) NOT NULL,

    task_type VARCHAR(50) NOT NULL,

    -- Version du jeu de features utilisé.
    feature_set_version VARCHAR(50),

    -- ------------------------------------------------------------------------
    -- Résultat de la prédiction
    -- ------------------------------------------------------------------------

    predicted_label VARCHAR(100),
    confidence NUMERIC(5, 4),

    decision_threshold NUMERIC(5, 4),

    probabilities JSONB NOT NULL DEFAULT '{}'::JSONB,
    explanation JSONB NOT NULL DEFAULT '{}'::JSONB,

    prediction_status VARCHAR(20) NOT NULL DEFAULT 'success',
    error_message TEXT,

    predicted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- ------------------------------------------------------------------------
    -- Relations
    -- ------------------------------------------------------------------------

    CONSTRAINT fk_model_predictions_article
        FOREIGN KEY (article_id)
        REFERENCES checkit.articles(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT fk_model_predictions_image
        FOREIGN KEY (image_id)
        REFERENCES checkit.images(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT fk_model_predictions_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES checkit.pipeline_runs(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL,

    -- ------------------------------------------------------------------------
    -- Contraintes d'identification
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_model_predictions_name_not_blank
        CHECK (
            BTRIM(model_name) <> ''
        ),

    CONSTRAINT ck_model_predictions_version_not_blank
        CHECK (
            BTRIM(model_version) <> ''
        ),

    CONSTRAINT ck_model_predictions_task_type
        CHECK (
            task_type IN (
                'fake_news_classification',
                'claim_detection',
                'stance_detection',
                'source_credibility',
                'image_authenticity',
                'text_image_consistency',
                'multimodal_classification'
            )
        ),

    CONSTRAINT ck_model_predictions_feature_set_version
        CHECK (
            feature_set_version IS NULL
            OR BTRIM(feature_set_version) <> ''
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes sur les résultats
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_model_predictions_label_not_blank
        CHECK (
            predicted_label IS NULL
            OR BTRIM(predicted_label) <> ''
        ),

    CONSTRAINT ck_model_predictions_confidence
        CHECK (
            confidence IS NULL
            OR confidence BETWEEN 0 AND 1
        ),

    CONSTRAINT ck_model_predictions_threshold
        CHECK (
            decision_threshold IS NULL
            OR decision_threshold BETWEEN 0 AND 1
        ),

    CONSTRAINT ck_model_predictions_probabilities_object
        CHECK (
            jsonb_typeof(probabilities) = 'object'
        ),

    CONSTRAINT ck_model_predictions_explanation_object
        CHECK (
            jsonb_typeof(explanation) = 'object'
        ),

    CONSTRAINT ck_model_predictions_status
        CHECK (
            prediction_status IN (
                'success',
                'failed',
                'skipped'
            )
        ),

    CONSTRAINT ck_model_predictions_success_consistency
        CHECK (
            prediction_status <> 'success'
            OR predicted_label IS NOT NULL
        ),

    CONSTRAINT ck_model_predictions_error_consistency
        CHECK (
            prediction_status = 'failed'
            OR error_message IS NULL
            OR BTRIM(error_message) = ''
        ),

    -- Une tâche visuelle doit référencer une image.
    CONSTRAINT ck_model_predictions_image_consistency
        CHECK (
            task_type NOT IN (
                'image_authenticity',
                'text_image_consistency'
            )
            OR image_id IS NOT NULL
        )
);


-- ============================================================================
-- 5. Documentation SQL de model_predictions
-- ============================================================================

COMMENT ON TABLE checkit.model_predictions IS
    'Prédictions historisées produites par les modèles CheckIt.AI.';

COMMENT ON COLUMN checkit.model_predictions.model_name IS
    'Nom du modèle ayant produit la prédiction.';

COMMENT ON COLUMN checkit.model_predictions.model_version IS
    'Version du modèle utilisée.';

COMMENT ON COLUMN checkit.model_predictions.task_type IS
    'Type de tâche IA exécutée.';

COMMENT ON COLUMN checkit.model_predictions.predicted_label IS
    'Décision finale produite par le modèle.';

COMMENT ON COLUMN checkit.model_predictions.confidence IS
    'Confiance du modèle comprise entre 0 et 1.';

COMMENT ON COLUMN checkit.model_predictions.probabilities IS
    'Distribution des probabilités par classe au format JSON.';

COMMENT ON COLUMN checkit.model_predictions.explanation IS
    'Éléments explicatifs structurés associés à la décision.';

COMMENT ON COLUMN checkit.model_predictions.prediction_status IS
    'État de la prédiction : success, failed ou skipped.';


-- ============================================================================
-- 6. Déclencheur updated_at pour model_predictions
-- ============================================================================

DROP TRIGGER IF EXISTS trg_model_predictions_set_updated_at
ON checkit.model_predictions;

CREATE TRIGGER trg_model_predictions_set_updated_at
BEFORE UPDATE ON checkit.model_predictions
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 7. Propriétaires
-- ============================================================================

ALTER TABLE checkit.article_features
    OWNER TO checkit_et1;

ALTER TABLE checkit.model_predictions
    OWNER TO checkit_et1;


COMMIT;


-- ============================================================================
-- 8. Vérification
-- ============================================================================

SELECT
    table_schema,
    table_name
FROM information_schema.tables
WHERE table_schema = 'checkit'
  AND table_name IN (
      'article_features',
      'model_predictions'
  )
ORDER BY table_name;

SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'checkit'
  AND table_name = 'article_features'
ORDER BY ordinal_position;

SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'checkit'
  AND table_name = 'model_predictions'
ORDER BY ordinal_position;