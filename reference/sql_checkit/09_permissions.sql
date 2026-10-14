-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/09_permissions.sql
-- Version : 1.0
-- Objet   : Création des rôles et attribution des droits PostgreSQL.
--
-- Stratégie de sécurité :
--
--   checkit_et1
--   └── rôle applicatif ETL
--       - lecture et écriture sur les tables métier ;
--       - exécution des fonctions et procédures utiles ;
--       - aucun droit d'administration du serveur.
--
--   checkit_reader
--   └── lecture seule
--       - consultation des tables et vues ;
--       - aucun INSERT, UPDATE, DELETE ou TRUNCATE.
--
--   checkit_dashboard
--   └── lecture limitée au reporting
--       - consultation des vues KPI uniquement ;
--       - aucun accès direct aux tables sensibles.
--
-- Prérequis :
--   - exécuter les scripts 00 à 08 ;
--   - exécuter ce script avec un rôle administrateur PostgreSQL ;
--   - le rôle checkit_et1 existe déjà.
--
-- Important :
--   - aucun mot de passe n'est stocké dans ce fichier ;
--   - les mots de passe doivent être définis hors dépôt Git ;
--   - les secrets doivent être gérés via .env, Airflow Connections
--     ou un backend de secrets.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Création des rôles complémentaires
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'checkit_reader'
    ) THEN
        CREATE ROLE checkit_reader
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END;
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'checkit_dashboard'
    ) THEN
        CREATE ROLE checkit_dashboard
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END;
$$;


-- ============================================================================
-- 2. Sécurisation de la base et du schéma
-- ============================================================================

-- Retire les privilèges génériques hérités par PUBLIC.
REVOKE ALL
ON DATABASE checkit
FROM PUBLIC;

REVOKE ALL
ON SCHEMA checkit
FROM PUBLIC;

-- Le rôle ETL peut se connecter et utiliser le schéma.
GRANT CONNECT
ON DATABASE checkit
TO checkit_et1;

GRANT USAGE
ON SCHEMA checkit
TO checkit_et1;

-- Les rôles de lecture peuvent se connecter et utiliser le schéma.
GRANT CONNECT
ON DATABASE checkit
TO checkit_reader;

GRANT USAGE
ON SCHEMA checkit
TO checkit_reader;

GRANT CONNECT
ON DATABASE checkit
TO checkit_dashboard;

GRANT USAGE
ON SCHEMA checkit
TO checkit_dashboard;


-- ============================================================================
-- 3. Droits du rôle ETL
-- ============================================================================

-- Lecture et écriture sur les tables applicatives.
GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA checkit
TO checkit_et1;

-- Accès aux séquences générées automatiquement.
GRANT USAGE, SELECT
ON ALL SEQUENCES IN SCHEMA checkit
TO checkit_et1;

-- Exécution des fonctions et procédures applicatives.
GRANT EXECUTE
ON ALL FUNCTIONS IN SCHEMA checkit
TO checkit_et1;

GRANT EXECUTE
ON ALL PROCEDURES IN SCHEMA checkit
TO checkit_et1;

-- Le rôle ETL ne reçoit volontairement pas :
--   - CREATE sur la base ;
--   - CREATE sur le schéma ;
--   - TRUNCATE ;
--   - REFERENCES ;
--   - TRIGGER ;
--   - droits d'administration des rôles.


-- ============================================================================
-- 4. Droits du rôle de lecture
-- ============================================================================

GRANT SELECT
ON ALL TABLES IN SCHEMA checkit
TO checkit_reader;

GRANT SELECT
ON ALL SEQUENCES IN SCHEMA checkit
TO checkit_reader;

-- Les fonctions purement consultatives peuvent être exécutées.
GRANT EXECUTE
ON FUNCTION checkit.is_null_or_blank(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.normalize_whitespace(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.normalize_text(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.extract_domain(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.normalize_url(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.is_valid_url(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.is_valid_language(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.word_count(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.reading_time_minutes(TEXT, NUMERIC)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.text_length(TEXT)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.compute_aspect_ratio(INTEGER, INTEGER)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.image_orientation(INTEGER, INTEGER)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.safe_percentage(NUMERIC, NUMERIC)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.success_rate(NUMERIC, NUMERIC)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.rejection_rate(NUMERIC, NUMERIC)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.execution_duration_seconds(
    TIMESTAMPTZ,
    TIMESTAMPTZ
)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.is_multimodal_article(VARCHAR)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.has_ground_truth(VARCHAR)
TO checkit_reader;

GRANT EXECUTE
ON FUNCTION checkit.latest_prediction_label(
    VARCHAR,
    VARCHAR
)
TO checkit_reader;


-- ============================================================================
-- 5. Droits du rôle dashboard
-- ============================================================================

-- Retire explicitement tout accès direct aux tables.
REVOKE ALL
ON ALL TABLES IN SCHEMA checkit
FROM checkit_dashboard;

-- Autorise uniquement les vues nécessaires au dashboard.
GRANT SELECT
ON checkit.vw_articles_complete
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_source_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_quality_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_pipeline_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_daily_pipeline_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_image_quality_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_label_distribution
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_feature_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_model_prediction_statistics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_prediction_ground_truth_comparison
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_pipeline_metrics
TO checkit_dashboard;

GRANT SELECT
ON checkit.vw_recent_etl_errors
TO checkit_dashboard;


-- ============================================================================
-- 6. Privilèges par défaut
-- ============================================================================
--
-- Ces règles s'appliquent aux futurs objets créés par checkit_et1.
-- Elles évitent de devoir réexécuter tous les GRANT à chaque nouvelle table.
-- ============================================================================

ALTER DEFAULT PRIVILEGES
FOR ROLE checkit_et1
IN SCHEMA checkit
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLES
TO checkit_et1;

ALTER DEFAULT PRIVILEGES
FOR ROLE checkit_et1
IN SCHEMA checkit
GRANT USAGE, SELECT
ON SEQUENCES
TO checkit_et1;

ALTER DEFAULT PRIVILEGES
FOR ROLE checkit_et1
IN SCHEMA checkit
GRANT EXECUTE
ON FUNCTIONS
TO checkit_et1;

-- Le rôle reader obtient automatiquement SELECT sur les futures tables.
ALTER DEFAULT PRIVILEGES
FOR ROLE checkit_et1
IN SCHEMA checkit
GRANT SELECT
ON TABLES
TO checkit_reader;

-- Le rôle dashboard ne reçoit volontairement aucun droit automatique.
-- Chaque nouvelle vue doit être autorisée explicitement.


-- ============================================================================
-- 7. Révocations explicites
-- ============================================================================

REVOKE CREATE
ON DATABASE checkit
FROM checkit_et1;

REVOKE CREATE
ON SCHEMA checkit
FROM checkit_et1;

REVOKE TRUNCATE
ON ALL TABLES IN SCHEMA checkit
FROM checkit_et1;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
ON ALL TABLES IN SCHEMA checkit
FROM checkit_reader;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
ON ALL TABLES IN SCHEMA checkit
FROM checkit_dashboard;

REVOKE EXECUTE
ON ALL PROCEDURES IN SCHEMA checkit
FROM checkit_reader;

REVOKE EXECUTE
ON ALL PROCEDURES IN SCHEMA checkit
FROM checkit_dashboard;


-- ============================================================================
-- 8. Exemples de comptes de connexion
-- ============================================================================
--
-- Ces commandes sont volontairement commentées afin de ne pas stocker
-- de mot de passe dans le dépôt.
--
-- À exécuter manuellement avec des secrets solides :
--
-- CREATE ROLE checkit_read_user
--     LOGIN
--     PASSWORD 'MOT_DE_PASSE_HORS_GIT'
--     IN ROLE checkit_reader;
--
-- CREATE ROLE checkit_dashboard_user
--     LOGIN
--     PASSWORD 'MOT_DE_PASSE_HORS_GIT'
--     IN ROLE checkit_dashboard;
--
-- Le rôle ETL existant est :
--     checkit_et1
--
-- Dans Airflow, utiliser une connexion nommée :
--     checkit_postgres
--


COMMIT;


-- ============================================================================
-- 9. Vérification des rôles
-- ============================================================================

SELECT
    rolname,
    rolcanlogin,
    rolsuper,
    rolcreatedb,
    rolcreaterole,
    rolinherit
FROM pg_roles
WHERE rolname IN (
    'checkit_et1',
    'checkit_reader',
    'checkit_dashboard'
)
ORDER BY rolname;


-- ============================================================================
-- 10. Vérification des privilèges sur le schéma
-- ============================================================================

SELECT
    grantee,
    privilege_type
FROM information_schema.role_usage_grants
WHERE object_schema = 'checkit'
ORDER BY
    grantee,
    privilege_type;


-- ============================================================================
-- 11. Vérification des privilèges sur les tables et vues
-- ============================================================================

SELECT
    grantee,
    table_schema,
    table_name,
    privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'checkit'
  AND grantee IN (
      'checkit_et1',
      'checkit_reader',
      'checkit_dashboard'
  )
ORDER BY
    grantee,
    table_name,
    privilege_type;