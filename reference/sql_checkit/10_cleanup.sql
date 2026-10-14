-- ============================================================================
-- CheckIt.AI
-- Fichier : sql/10_cleanup.sql
-- Version : 1.0
-- Objet   : Nettoyage contrôlé des données de démonstration et maintenance.
--
-- Modes proposés :
--
--   1. Nettoyage sécurisé des données de démonstration
--      - exécuté par défaut ;
--      - ne supprime pas les vraies données du pipeline.
--
--   2. Purge des anciens historiques
--      - commandes fournies mais commentées ;
--      - à activer uniquement selon la politique de rétention.
--
--   3. Réinitialisation complète du schéma
--      - commandes fournies mais commentées ;
--      - destruction totale des objets CheckIt.AI.
--
-- Prérequis :
--   - disposer du schéma checkit ;
--   - exécuter avec un rôle autorisé à supprimer les données concernées.
--
-- ATTENTION :
--   Les sections de réinitialisation complète sont destructives.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO checkit, public;


-- ============================================================================
-- 1. Identification du lot de démonstration
-- ============================================================================

-- L'exécution créée par 06_seed.sql.
DO $$
BEGIN
    RAISE NOTICE
        'Début du nettoyage des données de démonstration CheckIt.AI.';
END;
$$;


-- ============================================================================
-- 2. Suppression des prédictions de démonstration
-- ============================================================================

DELETE FROM checkit.model_predictions
WHERE pipeline_run_id = '00000000-0000-0000-0000-000000000001'
   OR article_id LIKE 'demo\_%' ESCAPE '\';


-- ============================================================================
-- 3. Suppression des features de démonstration
-- ============================================================================

DELETE FROM checkit.article_features
WHERE pipeline_run_id = '00000000-0000-0000-0000-000000000001'
   OR article_id LIKE 'demo\_%' ESCAPE '\';


-- ============================================================================
-- 4. Suppression des labels de démonstration
-- ============================================================================

DELETE FROM checkit.article_labels
WHERE article_id LIKE 'demo\_%' ESCAPE '\'
   OR label_metadata @> '{"demo": true}'::JSONB;


-- ============================================================================
-- 5. Suppression des images de démonstration
-- ============================================================================

DELETE FROM checkit.images
WHERE article_id LIKE 'demo\_%' ESCAPE '\'
   OR image_metadata @> '{"demo": true}'::JSONB;


-- ============================================================================
-- 6. Suppression des articles de démonstration
-- ============================================================================

DELETE FROM checkit.articles
WHERE id LIKE 'demo\_%' ESCAPE '\'
   OR raw_payload @> '{"demo": true}'::JSONB;


-- ============================================================================
-- 7. Suppression des métriques et logs de démonstration
-- ============================================================================

DELETE FROM checkit.pipeline_metrics
WHERE pipeline_run_id = '00000000-0000-0000-0000-000000000001'
   OR metric_metadata @> '{"demo": true}'::JSONB;

DELETE FROM checkit.etl_logs
WHERE pipeline_run_id = '00000000-0000-0000-0000-000000000001';


-- ============================================================================
-- 8. Suppression de la version de dataset de démonstration
-- ============================================================================

DELETE FROM checkit.dataset_versions
WHERE id = '00000000-0000-0000-0000-000000000101'
   OR version_name = 'demo_dataset_v1';


-- ============================================================================
-- 9. Suppression de l'exécution de démonstration
-- ============================================================================

DELETE FROM checkit.pipeline_runs
WHERE id = '00000000-0000-0000-0000-000000000001'
   OR run_metadata @> '{"seed": true}'::JSONB;


-- ============================================================================
-- 10. Nettoyage des configurations de démonstration
-- ============================================================================

DELETE FROM checkit.pipeline_configuration
WHERE configuration_key IN (
    'max_articles_per_source',
    'download_images',
    'accepted_languages',
    'request_timeout_seconds',
    'min_title_length'
);


-- ============================================================================
-- 11. Conservation des sources
-- ============================================================================
--
-- Les sources insérées par 06_seed.sql sont aussi des sources réelles
-- du projet. Elles sont donc conservées par défaut.
--
-- Pour les supprimer également, décommenter :
--
-- DELETE FROM checkit.sources
-- WHERE source_key IN (
--     'bbc_news',
--     'franceinfo',
--     'newsdata',
--     'reddit',
--     'fakeddit',
--     'fakenewsnet'
-- )
-- AND NOT EXISTS (
--     SELECT 1
--     FROM checkit.articles AS a
--     WHERE a.source_id = checkit.sources.id
-- );


COMMIT;


-- ============================================================================
-- 12. Vérification après nettoyage
-- ============================================================================

SELECT 'articles_demo' AS object_name, COUNT(*) AS remaining_count
FROM checkit.articles
WHERE id LIKE 'demo\_%' ESCAPE '\'

UNION ALL

SELECT 'images_demo', COUNT(*)
FROM checkit.images
WHERE article_id LIKE 'demo\_%' ESCAPE '\'

UNION ALL

SELECT 'labels_demo', COUNT(*)
FROM checkit.article_labels
WHERE article_id LIKE 'demo\_%' ESCAPE '\'

UNION ALL

SELECT 'features_demo', COUNT(*)
FROM checkit.article_features
WHERE article_id LIKE 'demo\_%' ESCAPE '\'

UNION ALL

SELECT 'predictions_demo', COUNT(*)
FROM checkit.model_predictions
WHERE article_id LIKE 'demo\_%' ESCAPE '\'

UNION ALL

SELECT 'pipeline_run_demo', COUNT(*)
FROM checkit.pipeline_runs
WHERE id = '00000000-0000-0000-0000-000000000001';


-- ============================================================================
-- 13. Purges de maintenance facultatives
-- ============================================================================
--
-- Ces commandes sont volontairement commentées.
-- Adapter les durées à la politique de rétention du projet.
--
-- CALL checkit.purge_old_etl_logs(90);
--
-- CALL checkit.purge_old_pipeline_metrics(365);
--
-- CALL checkit.purge_empty_pipeline_runs(180);


-- ============================================================================
-- 14. Nettoyage complet des données, sans supprimer le schéma
-- ============================================================================
--
-- ATTENTION : cette section supprime toutes les données métier.
-- Décommenter uniquement pour reconstruire une base vide.
--
-- BEGIN;
--
-- TRUNCATE TABLE
--     checkit.model_predictions,
--     checkit.article_features,
--     checkit.article_labels,
--     checkit.images,
--     checkit.articles,
--     checkit.pipeline_metrics,
--     checkit.etl_logs,
--     checkit.dataset_versions,
--     checkit.pipeline_runs,
--     checkit.pipeline_configuration,
--     checkit.sources
-- RESTART IDENTITY
-- CASCADE;
--
-- COMMIT;


-- ============================================================================
-- 15. Suppression complète du schéma
-- ============================================================================
--
-- DANGER : cette commande supprime toutes les tables, vues, fonctions,
-- procédures, index et données du projet CheckIt.AI.
--
-- Elle doit uniquement être utilisée pour une reconstruction totale.
--
-- DROP SCHEMA IF EXISTS checkit CASCADE;
--
-- Pour reconstruire ensuite :
--
--   00_database.sql
--   01_articles.sql
--   02_images_labels.sql
--   03_features_predictions.sql
--   04_indexes.sql
--   05_views.sql
--   07_functions.sql
--   08_procedures.sql
--   09_permissions.sql
--   06_seed.sql            -- facultatif
--
-- ============================================================================