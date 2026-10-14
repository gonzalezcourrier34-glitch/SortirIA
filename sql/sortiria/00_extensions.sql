-- ============================================================================
-- SortirIA · 00_extensions.sql
-- Extensions, schéma et fonctions utilitaires.
--
-- Provenance : nouveau fichier écrit pour SortirIA (14/10/2026).
--
-- Les fichiers de ce dossier s'exécutent dans l'ordre de leur numéro :
--   - automatiquement au premier démarrage de la base (docker compose les
--     monte dans /docker-entrypoint-initdb.d) ;
--   - ou à la main : uv run python -m scripts.init_db
-- Ils sont réexécutables (IF NOT EXISTS, CREATE OR REPLACE).
-- ============================================================================

BEGIN;

-- pgvector : type « vector » et index de recherche par similarité.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS sortiria;

-- Distance en kilomètres entre deux points GPS (formule de haversine).
-- Même calcul que `distance_km` dans openagenda_extractor.py : la base peut
-- ainsi filtrer « à moins de 10 km de Sète » sans passer par Python.
CREATE OR REPLACE FUNCTION sortiria.distance_km(
    lat1 DOUBLE PRECISION, lon1 DOUBLE PRECISION,
    lat2 DOUBLE PRECISION, lon2 DOUBLE PRECISION
) RETURNS DOUBLE PRECISION
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN 2 * 6371.0 * asin(sqrt(
    power(sin(radians(lat2 - lat1) / 2), 2)
    + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)
));

COMMIT;
