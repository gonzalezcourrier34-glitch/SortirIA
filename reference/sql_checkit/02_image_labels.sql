-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/02_images_labels.sql
-- Version : 3.0
-- Objet   : Création des tables images et article_labels.
--
-- Architecture retenue :
--
--   images
--   ├── caractéristiques techniques
--   ├── caractéristiques visuelles
--   └── validation
--
--   article_labels
--   ├── vérité terrain
--   ├── labels humains
--   └── labels datasets
--
-- Les prédictions automatiques sont volontairement exclues de
-- article_labels et seront stockées dans checkit.model_predictions.
--
-- Prérequis :
--   - exécuter sql/00_database.sql ;
--   - exécuter sql/01_articles.sql ;
--   - disposer de la table checkit.articles.
--
-- Le script peut être rejoué.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Table des images
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.images (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Article auquel l'image est rattachée.
    article_id VARCHAR(64) NOT NULL,

    -- Références distante et locale.
    remote_url TEXT,
    local_path TEXT,

    -- ------------------------------------------------------------------------
    -- Caractéristiques techniques
    -- ------------------------------------------------------------------------

    file_name TEXT,
    file_extension VARCHAR(20),
    file_format VARCHAR(20),
    mime_type VARCHAR(100),

    width INTEGER,
    height INTEGER,
    aspect_ratio NUMERIC(10, 6),
    size_bytes BIGINT,

    file_hash VARCHAR(64),
    perceptual_hash VARCHAR(128),

    download_duration_ms INTEGER,

    image_position INTEGER NOT NULL DEFAULT 0,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,

    -- ------------------------------------------------------------------------
    -- Caractéristiques visuelles
    -- ------------------------------------------------------------------------

    blur_score NUMERIC(14, 6),
    brightness_score NUMERIC(14, 6),
    entropy_score NUMERIC(14, 6),

    -- ------------------------------------------------------------------------
    -- Validation
    -- ------------------------------------------------------------------------

    is_valid BOOLEAN NOT NULL DEFAULT FALSE,
    validation_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    validation_error TEXT,

    association_status VARCHAR(30) NOT NULL DEFAULT 'unchecked',
    association_score NUMERIC(5, 4),
    association_method VARCHAR(50),

    downloaded_at TIMESTAMPTZ,
    validated_at TIMESTAMPTZ,
    associated_at TIMESTAMPTZ,

    image_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- ------------------------------------------------------------------------
    -- Relations
    -- ------------------------------------------------------------------------

    CONSTRAINT fk_images_article
        FOREIGN KEY (article_id)
        REFERENCES checkit.articles(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    -- ------------------------------------------------------------------------
    -- Contraintes de présence
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_images_reference_present
        CHECK (
            NULLIF(BTRIM(COALESCE(remote_url, '')), '') IS NOT NULL
            OR NULLIF(BTRIM(COALESCE(local_path, '')), '') IS NOT NULL
        ),

    CONSTRAINT ck_images_file_name_not_blank
        CHECK (
            file_name IS NULL
            OR BTRIM(file_name) <> ''
        ),

    CONSTRAINT ck_images_file_extension_not_blank
        CHECK (
            file_extension IS NULL
            OR BTRIM(file_extension) <> ''
        ),

    CONSTRAINT ck_images_file_format_not_blank
        CHECK (
            file_format IS NULL
            OR BTRIM(file_format) <> ''
        ),

    CONSTRAINT ck_images_mime_type_not_blank
        CHECK (
            mime_type IS NULL
            OR BTRIM(mime_type) <> ''
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes numériques
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_images_width
        CHECK (
            width IS NULL
            OR width > 0
        ),

    CONSTRAINT ck_images_height
        CHECK (
            height IS NULL
            OR height > 0
        ),

    CONSTRAINT ck_images_aspect_ratio
        CHECK (
            aspect_ratio IS NULL
            OR aspect_ratio > 0
        ),

    CONSTRAINT ck_images_size
        CHECK (
            size_bytes IS NULL
            OR size_bytes >= 0
        ),

    CONSTRAINT ck_images_blur_score
        CHECK (
            blur_score IS NULL
            OR blur_score >= 0
        ),

    CONSTRAINT ck_images_brightness_score
        CHECK (
            brightness_score IS NULL
            OR brightness_score >= 0
        ),

    CONSTRAINT ck_images_entropy_score
        CHECK (
            entropy_score IS NULL
            OR entropy_score >= 0
        ),

    CONSTRAINT ck_images_download_duration
        CHECK (
            download_duration_ms IS NULL
            OR download_duration_ms >= 0
        ),

    CONSTRAINT ck_images_position
        CHECK (
            image_position >= 0
        ),

    CONSTRAINT ck_images_association_score
        CHECK (
            association_score IS NULL
            OR association_score BETWEEN 0 AND 1
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes de validation
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_images_validation_status
        CHECK (
            validation_status IN (
                'pending',
                'valid',
                'invalid',
                'warning'
            )
        ),

    CONSTRAINT ck_images_validation_consistency
        CHECK (
            (
                validation_status = 'valid'
                AND is_valid = TRUE
            )
            OR
            (
                validation_status <> 'valid'
                AND is_valid = FALSE
            )
        ),

    CONSTRAINT ck_images_validation_error
        CHECK (
            validation_status = 'invalid'
            OR validation_error IS NULL
            OR BTRIM(validation_error) = ''
        ),

    CONSTRAINT ck_images_association_status
        CHECK (
            association_status IN (
                'unchecked',
                'technical_match',
                'semantic_match',
                'mismatch'
            )
        ),

    CONSTRAINT ck_images_association_method
        CHECK (
            association_method IS NULL
            OR association_method IN (
                'filename',
                'metadata',
                'manual',
                'clip',
                'multimodal_model'
            )
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes sur les empreintes et JSON
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_images_file_hash
        CHECK (
            file_hash IS NULL
            OR file_hash ~ '^[0-9a-fA-F]{32,64}$'
        ),

    CONSTRAINT ck_images_perceptual_hash
        CHECK (
            perceptual_hash IS NULL
            OR BTRIM(perceptual_hash) <> ''
        ),

    CONSTRAINT ck_images_metadata_object
        CHECK (
            jsonb_typeof(image_metadata) = 'object'
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes temporelles
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_images_validation_dates
        CHECK (
            validated_at IS NULL
            OR downloaded_at IS NULL
            OR validated_at >= downloaded_at
        ),

    CONSTRAINT ck_images_association_dates
        CHECK (
            associated_at IS NULL
            OR validated_at IS NULL
            OR associated_at >= validated_at
        )
);


-- ============================================================================
-- 2. Migration de la table images
-- ============================================================================

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS file_extension VARCHAR(20);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS aspect_ratio NUMERIC(10, 6);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS perceptual_hash VARCHAR(128);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS blur_score NUMERIC(14, 6);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS brightness_score NUMERIC(14, 6);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS entropy_score NUMERIC(14, 6);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS download_duration_ms INTEGER;

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS association_method VARCHAR(50);

ALTER TABLE checkit.images
    ADD COLUMN IF NOT EXISTS associated_at TIMESTAMPTZ;


-- ============================================================================
-- 3. Documentation SQL de la table images
-- ============================================================================

COMMENT ON TABLE checkit.images IS
    'Images associées aux articles avec leurs caractéristiques techniques, visuelles et leur validation.';

COMMENT ON COLUMN checkit.images.article_id IS
    'Article auquel l’image est rattachée.';

COMMENT ON COLUMN checkit.images.remote_url IS
    'URL distante fournie par la source.';

COMMENT ON COLUMN checkit.images.local_path IS
    'Chemin local du fichier image.';

COMMENT ON COLUMN checkit.images.file_hash IS
    'Empreinte cryptographique utilisée pour détecter les fichiers identiques.';

COMMENT ON COLUMN checkit.images.perceptual_hash IS
    'Empreinte perceptuelle utilisée pour détecter les images visuellement similaires.';

COMMENT ON COLUMN checkit.images.blur_score IS
    'Score de netteté ou de flou calculé sur l’image.';

COMMENT ON COLUMN checkit.images.brightness_score IS
    'Luminosité moyenne calculée sur l’image.';

COMMENT ON COLUMN checkit.images.entropy_score IS
    'Mesure de richesse visuelle calculée sur l’image.';

COMMENT ON COLUMN checkit.images.validation_status IS
    'État de validation technique de l’image.';

COMMENT ON COLUMN checkit.images.association_status IS
    'État de l’association entre l’article et l’image.';

COMMENT ON COLUMN checkit.images.association_score IS
    'Score facultatif de cohérence texte-image compris entre 0 et 1.';

COMMENT ON COLUMN checkit.images.image_metadata IS
    'Métadonnées techniques complémentaires conservées au format JSON.';


-- ============================================================================
-- 4. Déclencheur updated_at pour images
-- ============================================================================

DROP TRIGGER IF EXISTS trg_images_set_updated_at
ON checkit.images;

CREATE TRIGGER trg_images_set_updated_at
BEFORE UPDATE ON checkit.images
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 5. Table des labels d'articles
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.article_labels (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    article_id VARCHAR(64) NOT NULL,

    -- Valeur du label.
    label VARCHAR(50) NOT NULL,

    -- Origine fonctionnelle du label.
    label_type VARCHAR(30) NOT NULL,

    -- Source ayant fourni le label.
    label_source VARCHAR(150),

    -- Annotateur humain ou organisme.
    annotator VARCHAR(150),

    -- Méthode utilisée pour produire le label.
    annotation_method VARCHAR(50),

    -- Niveau de confiance éventuel.
    confidence NUMERIC(5, 4),

    -- Indique si le label constitue une vérité terrain.
    is_ground_truth BOOLEAN NOT NULL DEFAULT FALSE,

    -- Permet de désactiver un label sans le supprimer.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    notes TEXT,

    label_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    labeled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- ------------------------------------------------------------------------
    -- Relations
    -- ------------------------------------------------------------------------

    CONSTRAINT fk_article_labels_article
        FOREIGN KEY (article_id)
        REFERENCES checkit.articles(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    -- ------------------------------------------------------------------------
    -- Contraintes
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_article_labels_label_not_blank
        CHECK (
            BTRIM(label) <> ''
        ),

    -- Les prédictions de modèles sont exclues de cette table.
    CONSTRAINT ck_article_labels_type
        CHECK (
            label_type IN (
                'dataset',
                'manual',
                'fact_check'
            )
        ),

    CONSTRAINT ck_article_labels_source_not_blank
        CHECK (
            label_source IS NULL
            OR BTRIM(label_source) <> ''
        ),

    CONSTRAINT ck_article_labels_annotator_not_blank
        CHECK (
            annotator IS NULL
            OR BTRIM(annotator) <> ''
        ),

    CONSTRAINT ck_article_labels_annotation_method
        CHECK (
            annotation_method IS NULL
            OR annotation_method IN (
                'dataset_import',
                'manual_review',
                'external_fact_check'
            )
        ),

    CONSTRAINT ck_article_labels_confidence
        CHECK (
            confidence IS NULL
            OR confidence BETWEEN 0 AND 1
        ),

    -- Tous les labels de cette table peuvent être des références,
    -- mais seuls ceux explicitement marqués sont considérés ground truth.
    CONSTRAINT ck_article_labels_ground_truth_type
        CHECK (
            is_ground_truth = FALSE
            OR label_type IN (
                'dataset',
                'manual',
                'fact_check'
            )
        ),

    CONSTRAINT ck_article_labels_notes_not_blank
        CHECK (
            notes IS NULL
            OR BTRIM(notes) <> ''
        ),

    CONSTRAINT ck_article_labels_metadata_object
        CHECK (
            jsonb_typeof(label_metadata) = 'object'
        ),

    CONSTRAINT uq_article_labels_origin
        UNIQUE (
            article_id,
            label,
            label_type,
            label_source
        )
);


-- ============================================================================
-- 6. Migration de la table article_labels
-- ============================================================================

ALTER TABLE checkit.article_labels
    DROP CONSTRAINT IF EXISTS ck_article_labels_type;

ALTER TABLE checkit.article_labels
    DROP CONSTRAINT IF EXISTS ck_article_labels_annotation_method;

ALTER TABLE checkit.article_labels
    ADD CONSTRAINT ck_article_labels_type
        CHECK (
            label_type IN (
                'dataset',
                'manual',
                'fact_check'
            )
        );

ALTER TABLE checkit.article_labels
    ADD CONSTRAINT ck_article_labels_annotation_method
        CHECK (
            annotation_method IS NULL
            OR annotation_method IN (
                'dataset_import',
                'manual_review',
                'external_fact_check'
            )
        );

ALTER TABLE checkit.article_labels
    ADD COLUMN IF NOT EXISTS annotator VARCHAR(150);

ALTER TABLE checkit.article_labels
    ADD COLUMN IF NOT EXISTS annotation_method VARCHAR(50);

ALTER TABLE checkit.article_labels
    ADD COLUMN IF NOT EXISTS notes TEXT;


-- ============================================================================
-- 7. Documentation SQL de la table article_labels
-- ============================================================================

COMMENT ON TABLE checkit.article_labels IS
    'Vérités terrain, annotations humaines et labels issus de datasets.';

COMMENT ON COLUMN checkit.article_labels.label IS
    'Valeur du label, par exemple fake, real ou misleading.';

COMMENT ON COLUMN checkit.article_labels.label_type IS
    'Origine du label : dataset, manual ou fact_check.';

COMMENT ON COLUMN checkit.article_labels.label_source IS
    'Dataset, organisme ou source ayant fourni le label.';

COMMENT ON COLUMN checkit.article_labels.annotator IS
    'Annotateur humain ou organisme ayant attribué le label.';

COMMENT ON COLUMN checkit.article_labels.annotation_method IS
    'Méthode de production du label.';

COMMENT ON COLUMN checkit.article_labels.confidence IS
    'Niveau de confiance facultatif compris entre 0 et 1.';

COMMENT ON COLUMN checkit.article_labels.is_ground_truth IS
    'Indique que le label constitue une vérité terrain.';

COMMENT ON COLUMN checkit.article_labels.notes IS
    'Justification ou commentaire associé au label.';

COMMENT ON COLUMN checkit.article_labels.label_metadata IS
    'Métadonnées complémentaires conservées au format JSON.';


-- ============================================================================
-- 8. Déclencheur updated_at pour article_labels
-- ============================================================================

DROP TRIGGER IF EXISTS trg_article_labels_set_updated_at
ON checkit.article_labels;

CREATE TRIGGER trg_article_labels_set_updated_at
BEFORE UPDATE ON checkit.article_labels
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 9. Propriétaires
-- ============================================================================

ALTER TABLE checkit.images
    OWNER TO checkit_et1;

ALTER TABLE checkit.article_labels
    OWNER TO checkit_et1;


COMMIT;


-- ============================================================================
-- 10. Vérification
-- ============================================================================

SELECT
    table_schema,
    table_name
FROM information_schema.tables
WHERE table_schema = 'checkit'
  AND table_name IN (
      'images',
      'article_labels'
  )
ORDER BY table_name;