-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/01_articles.sql
-- Version : 3.0
-- Objet   : Création de la table centrale des articles.
--
-- Architecture retenue :
--
--   articles
--   ├── texte
--   ├── métadonnées
--   ├── URLs
--   └── qualité
--
-- Les éléments suivants sont volontairement stockés ailleurs :
--
--   - images et caractéristiques visuelles      -> checkit.images
--   - vérité terrain et annotations             -> checkit.article_labels
--   - variables NLP, vision et embeddings       -> checkit.article_features
--   - décisions et probabilités des modèles     -> checkit.model_predictions
--
-- Prérequis :
--   - exécuter sql/00_database.sql ;
--   - disposer du schéma checkit ;
--   - disposer des tables checkit.sources et checkit.pipeline_runs.
--
-- Le script peut être rejoué.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Table centrale des articles
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkit.articles (
    -- ------------------------------------------------------------------------
    -- Identité et traçabilité
    -- ------------------------------------------------------------------------

    -- Identifiant stable généré par CheckIt.AI.
    id VARCHAR(64) PRIMARY KEY,

    -- Source ayant fourni l'article.
    source_id BIGINT NOT NULL,

    -- Exécution ayant collecté ou transformé l'article.
    pipeline_run_id UUID,

    -- Identifiant d'origine fourni par la source.
    external_id TEXT,

    -- ------------------------------------------------------------------------
    -- Texte observé
    -- ------------------------------------------------------------------------

    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',

    -- ------------------------------------------------------------------------
    -- URLs
    -- ------------------------------------------------------------------------

    original_url TEXT,
    canonical_url TEXT,

    -- ------------------------------------------------------------------------
    -- Métadonnées éditoriales
    -- ------------------------------------------------------------------------

    author TEXT,
    language VARCHAR(10),
    category TEXT,

    published_at TIMESTAMPTZ,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    transformed_at TIMESTAMPTZ,

    -- Rôle de l'article dans le corpus.
    dataset_role VARCHAR(50),

    -- ------------------------------------------------------------------------
    -- Qualité et contrôle
    -- ------------------------------------------------------------------------

    data_quality_status VARCHAR(30) NOT NULL DEFAULT 'pending',
    rejection_reason TEXT,

    -- Version du pipeline ayant produit la représentation courante.
    transformation_version VARCHAR(30),

    -- Empreintes utilisées pour la traçabilité et la déduplication.
    title_hash VARCHAR(64),
    content_hash VARCHAR(64),
    canonical_url_hash VARCHAR(64),

    -- Métadonnées techniques non encore mappées.
    raw_payload JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- ------------------------------------------------------------------------
    -- Relations
    -- ------------------------------------------------------------------------

    CONSTRAINT fk_articles_source
        FOREIGN KEY (source_id)
        REFERENCES checkit.sources(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_articles_pipeline_run
        FOREIGN KEY (pipeline_run_id)
        REFERENCES checkit.pipeline_runs(id)
        ON UPDATE CASCADE
        ON DELETE SET NULL,

    -- ------------------------------------------------------------------------
    -- Contraintes textuelles
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_articles_id_not_blank
        CHECK (
            BTRIM(id) <> ''
        ),

    CONSTRAINT ck_articles_title_not_blank
        CHECK (
            BTRIM(title) <> ''
        ),

    CONSTRAINT ck_articles_external_id_not_blank
        CHECK (
            external_id IS NULL
            OR BTRIM(external_id) <> ''
        ),

    CONSTRAINT ck_articles_original_url_not_blank
        CHECK (
            original_url IS NULL
            OR BTRIM(original_url) <> ''
        ),

    CONSTRAINT ck_articles_canonical_url_not_blank
        CHECK (
            canonical_url IS NULL
            OR BTRIM(canonical_url) <> ''
        ),

    CONSTRAINT ck_articles_author_not_blank
        CHECK (
            author IS NULL
            OR BTRIM(author) <> ''
        ),

    CONSTRAINT ck_articles_language_not_blank
        CHECK (
            language IS NULL
            OR BTRIM(language) <> ''
        ),

    CONSTRAINT ck_articles_category_not_blank
        CHECK (
            category IS NULL
            OR BTRIM(category) <> ''
        ),

    CONSTRAINT ck_articles_transformation_version_not_blank
        CHECK (
            transformation_version IS NULL
            OR BTRIM(transformation_version) <> ''
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes métier
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_articles_dataset_role
        CHECK (
            dataset_role IS NULL
            OR dataset_role IN (
                'acquisition',
                'labeled_reference',
                'multimodal_reference',
                'fact_check_reference',
                'social_reference'
            )
        ),

    CONSTRAINT ck_articles_quality_status
        CHECK (
            data_quality_status IN (
                'pending',
                'valid',
                'warning',
                'rejected'
            )
        ),

    -- Un motif de rejet n'est attendu que pour une ligne rejetée.
    CONSTRAINT ck_articles_rejection_reason
        CHECK (
            data_quality_status = 'rejected'
            OR rejection_reason IS NULL
            OR BTRIM(rejection_reason) = ''
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes temporelles
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_articles_transformation_dates
        CHECK (
            transformed_at IS NULL
            OR transformed_at >= extracted_at
        ),

    -- ------------------------------------------------------------------------
    -- Contraintes sur les empreintes et JSON
    -- ------------------------------------------------------------------------

    CONSTRAINT ck_articles_title_hash
        CHECK (
            title_hash IS NULL
            OR title_hash ~ '^[0-9a-fA-F]{32,64}$'
        ),

    CONSTRAINT ck_articles_content_hash
        CHECK (
            content_hash IS NULL
            OR content_hash ~ '^[0-9a-fA-F]{32,64}$'
        ),

    CONSTRAINT ck_articles_url_hash
        CHECK (
            canonical_url_hash IS NULL
            OR canonical_url_hash ~ '^[0-9a-fA-F]{32,64}$'
        ),

    CONSTRAINT ck_articles_raw_payload_object
        CHECK (
            jsonb_typeof(raw_payload) = 'object'
        )
);


-- ============================================================================
-- 2. Migration depuis l'ancienne version
-- ============================================================================
--
-- Ces colonnes étaient auparavant stockées dans articles.
-- Elles appartiennent désormais à checkit.article_features.
--
-- Les suppressions concernent uniquement des variables calculées.
-- Elles ne suppriment ni le texte, ni les métadonnées, ni les URLs.
-- ============================================================================

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_lengths_non_negative;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_word_counts_non_negative;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_total_text_length;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_total_word_count;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_reading_time;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_language_confidence;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_duplicate_score;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_publication_year;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_publication_month;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_publication_day;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_publication_hour;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_publication_weekday;

ALTER TABLE checkit.articles
    DROP CONSTRAINT IF EXISTS ck_articles_publication_date_consistency;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS language_confidence;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS title_length;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS content_length;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS total_text_length;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS title_word_count;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS content_word_count;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS total_word_count;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS reading_time_minutes;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS has_publication_date;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS publication_year;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS publication_month;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS publication_day;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS publication_hour;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS publication_weekday;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS has_author;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS has_url;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS has_image;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS is_multimodal;

ALTER TABLE checkit.articles
    DROP COLUMN IF EXISTS duplicate_score;


-- ============================================================================
-- 3. Documentation SQL
-- ============================================================================

COMMENT ON TABLE checkit.articles IS
    'Publications observées collectées et normalisées par CheckIt.AI.';

COMMENT ON COLUMN checkit.articles.id IS
    'Identifiant stable généré par CheckIt.AI.';

COMMENT ON COLUMN checkit.articles.source_id IS
    'Source ayant fourni l’article.';

COMMENT ON COLUMN checkit.articles.pipeline_run_id IS
    'Exécution du pipeline ayant collecté ou transformé l’article.';

COMMENT ON COLUMN checkit.articles.external_id IS
    'Identifiant original fourni par la source.';

COMMENT ON COLUMN checkit.articles.title IS
    'Titre nettoyé de la publication.';

COMMENT ON COLUMN checkit.articles.content IS
    'Contenu textuel principal nettoyé.';

COMMENT ON COLUMN checkit.articles.original_url IS
    'URL fournie initialement par la source.';

COMMENT ON COLUMN checkit.articles.canonical_url IS
    'URL normalisée utilisée pour la traçabilité et la déduplication.';

COMMENT ON COLUMN checkit.articles.author IS
    'Auteur ou compte ayant publié le contenu.';

COMMENT ON COLUMN checkit.articles.language IS
    'Langue normalisée de la publication.';

COMMENT ON COLUMN checkit.articles.category IS
    'Catégorie ou thème associé à la publication.';

COMMENT ON COLUMN checkit.articles.published_at IS
    'Date de publication fournie par la source.';

COMMENT ON COLUMN checkit.articles.extracted_at IS
    'Date de collecte de la publication.';

COMMENT ON COLUMN checkit.articles.transformed_at IS
    'Date de la dernière transformation appliquée.';

COMMENT ON COLUMN checkit.articles.dataset_role IS
    'Rôle de l’article dans le corpus.';

COMMENT ON COLUMN checkit.articles.data_quality_status IS
    'Statut qualité : pending, valid, warning ou rejected.';

COMMENT ON COLUMN checkit.articles.rejection_reason IS
    'Motif expliquant le rejet de l’article.';

COMMENT ON COLUMN checkit.articles.transformation_version IS
    'Version du pipeline ayant produit la représentation courante.';

COMMENT ON COLUMN checkit.articles.title_hash IS
    'Empreinte normalisée du titre.';

COMMENT ON COLUMN checkit.articles.content_hash IS
    'Empreinte du contenu textuel.';

COMMENT ON COLUMN checkit.articles.canonical_url_hash IS
    'Empreinte de l’URL canonique.';

COMMENT ON COLUMN checkit.articles.raw_payload IS
    'Données techniques non mappées conservées au format JSON.';


-- ============================================================================
-- 4. Déclencheur updated_at
-- ============================================================================

DROP TRIGGER IF EXISTS trg_articles_set_updated_at
ON checkit.articles;

CREATE TRIGGER trg_articles_set_updated_at
BEFORE UPDATE ON checkit.articles
FOR EACH ROW
EXECUTE FUNCTION checkit.set_updated_at();


-- ============================================================================
-- 5. Propriétaire
-- ============================================================================

ALTER TABLE checkit.articles
    OWNER TO checkit_et1;


COMMIT;


-- ============================================================================
-- 6. Vérification
-- ============================================================================

SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'checkit'
  AND table_name = 'articles'
ORDER BY ordinal_position;