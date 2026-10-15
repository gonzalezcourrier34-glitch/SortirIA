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

-- Commune comparable : minuscules, sans accents ni ponctuation (même règle que
-- normalize_for_comparison dans src/event/event_normalizer.py).
CREATE OR REPLACE FUNCTION sortiria.normalize_city(city TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN btrim(regexp_replace(
    translate(lower(COALESCE(city, '')),
              'àâäáãåçéèêëíìîïñóòôöõúùûüýÿ', 'aaaaaaceeeeiiiinooooouuuuyy'),
    '[^a-z0-9]+', ' ', 'g'));

-- Zone d'un événement (14/10/2026) : d'après sa commune (31 communes de la
-- Métropole, 14 de Sète agglopôle), sinon d'après ses coordonnées. Même
-- règle que `zone_of` dans agent/watch.py : un événement sans GPS (PDF,
-- partenaires) est rangé par sa commune.
CREATE OR REPLACE FUNCTION sortiria.event_zone(
    latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, city TEXT
) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE
    WHEN sortiria.normalize_city(city) = ANY (ARRAY['baillargues', 'beaulieu', 'castelnau le lez', 'castries', 'clapiers', 'cournonsec', 'cournonterral', 'fabregues', 'grabels', 'jacou', 'juvignac', 'lattes', 'laverune', 'le cres', 'montaud', 'montferrier sur lez', 'montpellier', 'murviel les montpellier', 'perols', 'pignan', 'prades le lez', 'restinclieres', 'saint bres', 'saint drezery', 'saint genies des mourgues', 'saint georges d orques', 'saint jean de vedas', 'saussan', 'sussargues', 'vendargues', 'villeneuve les maguelone']) THEN 'montpellier'
    WHEN sortiria.normalize_city(city) = ANY (ARRAY['balaruc le vieux', 'balaruc les bains', 'bouzigues', 'frontignan', 'gigean', 'loupian', 'marseillan', 'meze', 'mireval', 'montbazin', 'poussan', 'sete', 'vic la gardiole', 'villeveyrac']) THEN 'bassin_de_thau'
    WHEN latitude IS NULL OR longitude IS NULL THEN 'sans_gps'
    WHEN sortiria.distance_km(43.6108, 3.8767, latitude, longitude) <= 20 THEN 'montpellier'
    WHEN sortiria.distance_km(43.42, 3.62, latitude, longitude) <= 15 THEN 'bassin_de_thau'
    ELSE 'hors_zone'
END;

COMMIT;
