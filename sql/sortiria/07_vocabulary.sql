-- ============================================================================
-- SortirIA · 07_vocabulary.sql
-- Vocabulaire des titres d'événements : les sujets que la base connaît.
--
-- Provenance : nouveau fichier écrit pour SortirIA (15/10/2026).
--
-- Un mot de la question est un SUJET s'il apparaît dans au moins un titre
-- d'événement (« cirque », « photo », « poterie ») : la recherche l'exige
-- alors dans le texte des événements proposés. Un mot absent de tous les
-- titres (« romantique », « insolite ») est une AMBIANCE : la recherche par
-- le sens s'en charge. Aucune liste écrite à la main : le vocabulaire suit la
-- base, recalculé à chaque collecte nocturne par l'agent :
--   SELECT sortiria.refresh_title_vocabulary();
-- ============================================================================

BEGIN;

-- Une ligne par racine de mot (lexème français, sans accents) des titres.
CREATE TABLE IF NOT EXISTS sortiria.title_vocabulary (
    lexeme       TEXT        PRIMARY KEY,
    events       INTEGER     NOT NULL,              -- titres où la racine apparaît
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Recherche par préfixe (« photo » trouve « photograph », racine de « photographies »).
CREATE INDEX IF NOT EXISTS title_vocabulary_prefix_idx
    ON sortiria.title_vocabulary (lexeme text_pattern_ops);

-- Même normalisation que la recherche plein texte (app/search_service.py,
-- LEXICAL_DOCUMENT) : minuscules, sans accents, dictionnaire français.
CREATE OR REPLACE FUNCTION sortiria.title_lexemes(title TEXT) RETURNS tsvector
LANGUAGE sql IMMUTABLE AS $$
    SELECT to_tsvector('french', translate(lower(COALESCE(title, '')),
                                           'àâäáãåçéèêëíìîïñóòôöõúùûüýÿœ', 'aaaaaaceeeeiiiinooooouuuuyyo'))
$$;

-- Recalcule le vocabulaire d'après les titres des sorties en base. Renvoie le
-- nombre de racines connues.
CREATE OR REPLACE FUNCTION sortiria.refresh_title_vocabulary() RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    known INTEGER;
BEGIN
    DELETE FROM sortiria.title_vocabulary;
    INSERT INTO sortiria.title_vocabulary (lexeme, events)
    SELECT word, ndoc
    FROM ts_stat('SELECT sortiria.title_lexemes(title) FROM sortiria.events WHERE is_outing')
    WHERE length(word) >= 3;
    GET DIAGNOSTICS known = ROW_COUNT;
    RETURN known;
END $$;

COMMIT;
