-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/07_functions.sql
-- Version : 1.0
-- Objet   : Fonctions PostgreSQL communes utilisées par les pipelines,
--           les vues, les procédures et le monitoring.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;

CREATE OR REPLACE FUNCTION checkit.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION checkit.is_null_or_blank(input_value TEXT)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT input_value IS NULL OR BTRIM(input_value) = '';
$$;

CREATE OR REPLACE FUNCTION checkit.normalize_whitespace(input_value TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN input_value IS NULL THEN NULL
        ELSE NULLIF(BTRIM(REGEXP_REPLACE(input_value, '\s+', ' ', 'g')), '')
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.normalize_text(input_value TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT checkit.normalize_whitespace(
        REPLACE(
            REPLACE(
                REPLACE(COALESCE(input_value, ''), CHR(160), ' '),
                CHR(8203),
                ''
            ),
            CHR(65279),
            ''
        )
    );
$$;

CREATE OR REPLACE FUNCTION checkit.extract_domain(input_url TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.is_null_or_blank(input_url) THEN NULL
        ELSE LOWER(
            REGEXP_REPLACE(
                REGEXP_REPLACE(
                    input_url,
                    '^[a-zA-Z][a-zA-Z0-9+.-]*://',
                    ''
                ),
                '[/?:#].*$',
                ''
            )
        )
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.normalize_url(input_url TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.is_null_or_blank(input_url) THEN NULL
        ELSE REGEXP_REPLACE(LOWER(BTRIM(input_url)), '[#].*$', '')
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.is_valid_url(input_url TEXT)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT
        NOT checkit.is_null_or_blank(input_url)
        AND input_url ~* '^https?://[a-z0-9.-]+(?:\:[0-9]+)?(?:/.*)?$';
$$;

CREATE OR REPLACE FUNCTION checkit.is_valid_language(input_language TEXT)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT
        input_language IS NULL
        OR input_language ~ '^[a-z]{2,3}(?:-[A-Z]{2})?$';
$$;

CREATE OR REPLACE FUNCTION checkit.is_valid_image_extension(input_extension TEXT)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.is_null_or_blank(input_extension) THEN FALSE
        ELSE LOWER(LTRIM(input_extension, '.')) IN (
            'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tif', 'tiff'
        )
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.is_valid_mime_type(input_mime_type TEXT)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.is_null_or_blank(input_mime_type) THEN FALSE
        ELSE LOWER(input_mime_type) IN (
            'image/jpeg',
            'image/png',
            'image/webp',
            'image/gif',
            'image/bmp',
            'image/tiff'
        )
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.is_json_object(input_value JSONB)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT input_value IS NOT NULL AND JSONB_TYPEOF(input_value) = 'object';
$$;

CREATE OR REPLACE FUNCTION checkit.generate_md5_hash(input_value TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.is_null_or_blank(input_value) THEN NULL
        ELSE MD5(checkit.normalize_text(input_value))
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.generate_title_hash(input_title TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT checkit.generate_md5_hash(LOWER(COALESCE(input_title, '')));
$$;

CREATE OR REPLACE FUNCTION checkit.generate_content_hash(input_content TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT checkit.generate_md5_hash(LOWER(COALESCE(input_content, '')));
$$;

CREATE OR REPLACE FUNCTION checkit.generate_url_hash(input_url TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.normalize_url(input_url) IS NULL THEN NULL
        ELSE MD5(checkit.normalize_url(input_url))
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.word_count(input_text TEXT)
RETURNS INTEGER
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN checkit.is_null_or_blank(input_text) THEN 0
        ELSE CARDINALITY(
            REGEXP_SPLIT_TO_ARRAY(
                checkit.normalize_text(input_text),
                '\s+'
            )
        )
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.reading_time_minutes(
    input_text TEXT,
    words_per_minute NUMERIC DEFAULT 200
)
RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN words_per_minute IS NULL OR words_per_minute <= 0 THEN NULL
        ELSE ROUND(
            checkit.word_count(input_text)::NUMERIC / words_per_minute,
            2
        )
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.text_length(input_text TEXT)
RETURNS INTEGER
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CHARACTER_LENGTH(
        COALESCE(checkit.normalize_text(input_text), '')
    );
$$;

CREATE OR REPLACE FUNCTION checkit.compute_aspect_ratio(
    input_width INTEGER,
    input_height INTEGER
)
RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN input_width IS NULL
          OR input_height IS NULL
          OR input_width <= 0
          OR input_height <= 0
        THEN NULL
        ELSE ROUND(input_width::NUMERIC / input_height::NUMERIC, 6)
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.image_orientation(
    input_width INTEGER,
    input_height INTEGER
)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN input_width IS NULL
          OR input_height IS NULL
          OR input_width <= 0
          OR input_height <= 0
        THEN NULL
        WHEN input_width = input_height THEN 'square'
        WHEN input_width > input_height THEN 'landscape'
        ELSE 'portrait'
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.safe_percentage(
    numerator NUMERIC,
    denominator NUMERIC
)
RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN denominator IS NULL OR denominator = 0 THEN NULL
        ELSE ROUND(100.0 * numerator / denominator, 2)
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.success_rate(
    success_count NUMERIC,
    total_count NUMERIC
)
RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT checkit.safe_percentage(success_count, total_count);
$$;

CREATE OR REPLACE FUNCTION checkit.rejection_rate(
    rejected_count NUMERIC,
    total_count NUMERIC
)
RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT checkit.safe_percentage(rejected_count, total_count);
$$;

CREATE OR REPLACE FUNCTION checkit.execution_duration_seconds(
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
)
RETURNS NUMERIC
LANGUAGE SQL
STABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN started_at IS NULL THEN NULL
        ELSE ROUND(
            EXTRACT(
                EPOCH FROM (
                    COALESCE(finished_at, CURRENT_TIMESTAMP) - started_at
                )
            )::NUMERIC,
            3
        )
    END;
$$;

CREATE OR REPLACE FUNCTION checkit.is_multimodal_article(
    input_article_id VARCHAR
)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
PARALLEL SAFE
AS $$
    SELECT
        EXISTS (
            SELECT 1
            FROM checkit.articles AS a
            WHERE a.id = input_article_id
              AND NOT checkit.is_null_or_blank(a.title)
              AND NOT checkit.is_null_or_blank(a.content)
        )
        AND EXISTS (
            SELECT 1
            FROM checkit.images AS i
            WHERE i.article_id = input_article_id
              AND i.is_valid = TRUE
        );
$$;

CREATE OR REPLACE FUNCTION checkit.has_ground_truth(
    input_article_id VARCHAR
)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
PARALLEL SAFE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM checkit.article_labels AS al
        WHERE al.article_id = input_article_id
          AND al.is_ground_truth = TRUE
          AND al.is_active = TRUE
    );
$$;

CREATE OR REPLACE FUNCTION checkit.latest_prediction_label(
    input_article_id VARCHAR,
    input_task_type VARCHAR DEFAULT NULL
)
RETURNS TEXT
LANGUAGE SQL
STABLE
PARALLEL SAFE
AS $$
    SELECT mp.predicted_label
    FROM checkit.model_predictions AS mp
    WHERE mp.article_id = input_article_id
      AND mp.prediction_status = 'success'
      AND (
          input_task_type IS NULL
          OR mp.task_type = input_task_type
      )
    ORDER BY mp.predicted_at DESC, mp.id DESC
    LIMIT 1;
$$;

COMMENT ON FUNCTION checkit.set_updated_at() IS
    'Met automatiquement à jour updated_at.';
COMMENT ON FUNCTION checkit.is_null_or_blank(TEXT) IS
    'Teste si un texte est nul ou vide.';
COMMENT ON FUNCTION checkit.normalize_whitespace(TEXT) IS
    'Normalise les espaces.';
COMMENT ON FUNCTION checkit.normalize_text(TEXT) IS
    'Nettoie certains caractères invisibles et normalise les espaces.';
COMMENT ON FUNCTION checkit.extract_domain(TEXT) IS
    'Extrait le domaine d’une URL.';
COMMENT ON FUNCTION checkit.normalize_url(TEXT) IS
    'Normalise une URL.';
COMMENT ON FUNCTION checkit.is_valid_url(TEXT) IS
    'Valide une URL HTTP ou HTTPS.';
COMMENT ON FUNCTION checkit.is_valid_language(TEXT) IS
    'Valide un code langue court.';
COMMENT ON FUNCTION checkit.is_valid_image_extension(TEXT) IS
    'Valide une extension d’image.';
COMMENT ON FUNCTION checkit.is_valid_mime_type(TEXT) IS
    'Valide un type MIME image.';
COMMENT ON FUNCTION checkit.is_json_object(JSONB) IS
    'Teste si une valeur JSONB est un objet.';
COMMENT ON FUNCTION checkit.generate_md5_hash(TEXT) IS
    'Génère un hash MD5.';
COMMENT ON FUNCTION checkit.generate_title_hash(TEXT) IS
    'Génère le hash d’un titre.';
COMMENT ON FUNCTION checkit.generate_content_hash(TEXT) IS
    'Génère le hash d’un contenu.';
COMMENT ON FUNCTION checkit.generate_url_hash(TEXT) IS
    'Génère le hash d’une URL.';
COMMENT ON FUNCTION checkit.word_count(TEXT) IS
    'Compte les mots.';
COMMENT ON FUNCTION checkit.reading_time_minutes(TEXT, NUMERIC) IS
    'Estime le temps de lecture.';
COMMENT ON FUNCTION checkit.text_length(TEXT) IS
    'Mesure la longueur d’un texte normalisé.';
COMMENT ON FUNCTION checkit.compute_aspect_ratio(INTEGER, INTEGER) IS
    'Calcule le ratio largeur/hauteur.';
COMMENT ON FUNCTION checkit.image_orientation(INTEGER, INTEGER) IS
    'Retourne square, landscape ou portrait.';
COMMENT ON FUNCTION checkit.safe_percentage(NUMERIC, NUMERIC) IS
    'Calcule un pourcentage sans division par zéro.';
COMMENT ON FUNCTION checkit.success_rate(NUMERIC, NUMERIC) IS
    'Calcule un taux de succès.';
COMMENT ON FUNCTION checkit.rejection_rate(NUMERIC, NUMERIC) IS
    'Calcule un taux de rejet.';
COMMENT ON FUNCTION checkit.execution_duration_seconds(TIMESTAMPTZ, TIMESTAMPTZ) IS
    'Calcule une durée en secondes.';
COMMENT ON FUNCTION checkit.is_multimodal_article(VARCHAR) IS
    'Teste si un article possède un texte et une image valide.';
COMMENT ON FUNCTION checkit.has_ground_truth(VARCHAR) IS
    'Teste si un article possède une vérité terrain active.';
COMMENT ON FUNCTION checkit.latest_prediction_label(VARCHAR, VARCHAR) IS
    'Retourne le dernier label prédit.';

ALTER FUNCTION checkit.set_updated_at() OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_null_or_blank(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.normalize_whitespace(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.normalize_text(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.extract_domain(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.normalize_url(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_valid_url(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_valid_language(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_valid_image_extension(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_valid_mime_type(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_json_object(JSONB) OWNER TO checkit_et1;
ALTER FUNCTION checkit.generate_md5_hash(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.generate_title_hash(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.generate_content_hash(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.generate_url_hash(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.word_count(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.reading_time_minutes(TEXT, NUMERIC) OWNER TO checkit_et1;
ALTER FUNCTION checkit.text_length(TEXT) OWNER TO checkit_et1;
ALTER FUNCTION checkit.compute_aspect_ratio(INTEGER, INTEGER) OWNER TO checkit_et1;
ALTER FUNCTION checkit.image_orientation(INTEGER, INTEGER) OWNER TO checkit_et1;
ALTER FUNCTION checkit.safe_percentage(NUMERIC, NUMERIC) OWNER TO checkit_et1;
ALTER FUNCTION checkit.success_rate(NUMERIC, NUMERIC) OWNER TO checkit_et1;
ALTER FUNCTION checkit.rejection_rate(NUMERIC, NUMERIC) OWNER TO checkit_et1;
ALTER FUNCTION checkit.execution_duration_seconds(TIMESTAMPTZ, TIMESTAMPTZ) OWNER TO checkit_et1;
ALTER FUNCTION checkit.is_multimodal_article(VARCHAR) OWNER TO checkit_et1;
ALTER FUNCTION checkit.has_ground_truth(VARCHAR) OWNER TO checkit_et1;
ALTER FUNCTION checkit.latest_prediction_label(VARCHAR, VARCHAR) OWNER TO checkit_et1;

COMMIT;

SELECT
    routine_schema,
    routine_name,
    data_type
FROM information_schema.routines
WHERE routine_schema = 'checkit'
ORDER BY routine_name;