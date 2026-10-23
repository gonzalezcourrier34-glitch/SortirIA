"""Requête SQL de la recherche : candidats, filtres, classement, élargissement.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

Construit et exécute la recherche hybride sur `sortiria.v_upcoming_events`
(filtres stricts, classement par le sens et par les mots, fusion des rangs),
choisit les séances qui répondent exactement, élargit les contraintes quand le
sujet manque, place en tête les offres partenaires qui répondent, et vectorise
la question (mistral-embed).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from app.query_understanding import (
    CATEGORY_LABELS,
    MOMENT_HOURS,
    MOMENT_LABELS,
    PRECISE_SUBJECTS,
    QUESTION_CATEGORIES,
    QueryPlan,
    Understanding,
    main_categories,
    merge_with_filters,
    plain,
)
from app.search_filters import (
    PARIS,
    PERIOD_LABELS,
    PUBLIC_LABELS,
    ZONES,
    SearchFilters,
    period_bounds,
)

CANDIDATES = 80          # séances comparées à la question (regroupées ensuite par événement)

EVENT_COLUMNS = """
    e.event_id, e.title, e.summary, e.description, e.category_norm, e.category,
    e.start_at, e.end_at, e.time_known, e.venue_name, e.city,
    e.price_text, e.price_min, e.is_free, e.audiences, e.age_min, e.age_max,
    e.url, e.source_name, e.source_attribution, e.partner_id, e.is_sponsored,
    e.venue_rating, e.venue_rating_count, p.name AS partner_name,
    -- Rang du partenaire (17/10/2026) : 2 premium, 1 mis en avant, 0 sinon
    -- (une formule payante échue ne compte plus : is_sponsored est faux).
    CASE WHEN NOT e.is_sponsored THEN 0 WHEN p.formula = 'premium' THEN 2 ELSE 1 END
        AS partner_rank,
    sortiria.event_zone(e.latitude, e.longitude, e.city) AS zone"""


# Texte comparé aux mots-clés : titre (deux fois, il compte plus), résumé,
# catégorie, lieu et début de la description ; sans accents, comme la question.
ACCENTS_FROM = "àâäáãåçéèêëíìîïñóòôöõúùûüýÿœ"
ACCENTS_TO = "aaaaaaceeeeiiiinooooouuuuyyo"
LEXICAL_DOCUMENT = (
    "to_tsvector('french', translate(lower(concat_ws(' ', e.title, e.title, e.summary, "
    "e.category_norm, e.category, e.venue_name, e.city, left(e.description, 1500))), "
    f"'{ACCENTS_FROM}', '{ACCENTS_TO}'))"
)
# Cadre (15/10/2026) : « s'il pleut » écarte les sorties de plein air ;
# « en plein air » ne garde qu'elles. D'après la catégorie et les mots du texte.
OUTDOOR_CATEGORIES = "ARRAY['nature', 'marche', 'sport', 'fete', 'festival']"
OUTDOOR_TEXT = ("'plein air|balade|randonn|plage|jardin|en exterieur|a l exterieur|sentier"
                "|kayak|velo'")
EVENT_TEXT = (f"translate(lower(concat_ws(' ', e.title, e.summary, left(e.description, 600))), "
              f"'{ACCENTS_FROM}', '{ACCENTS_TO}')")
# « Enfant » (18/10/2026) : un événement familial pour l'enfant lui-même :
# âge maximal de 14 ans, ou un âge ou « jeune public » dans le titre ou le
# résumé (« dès 6 ans », « 4-11 ans »), ou le mot « enfants » (pas dans une
# exposition : « Joyeux enfants de la Bourgogne » est une exposition de
# peintres, ni pour un prix : « gratuit pour les enfants »). Titre et résumé
# seulement, sans accents, en minuscules : la description dit souvent
# « gratuit pour les enfants ».
CHILD_AGES = (r"\m(jeune public|tout petits?|bebes?|(des|a partir de) ([2-9]|1[0-2]) ans"
              r"|([0-9]|1[0-4]) ?(-|a|/) ?([0-9]|1[0-4]) ans)\M")
CHILD_WORDS = r"\m(enfants|jeunesse|kids|maternelle)\M"
CHILD_PRICE_TEXT = r"\m(gratuit|gratuite|tarif|reduit|entree)\M[^.]{0,25}\menfants\M"
CHILD_TITLE_SUMMARY = (f"translate(lower(concat_ws(' ', e.title, e.summary)), "
                       f"'{ACCENTS_FROM}', '{ACCENTS_TO}')")
CHILD_CONDITION = (
    f"('famille' = ANY (e.audiences) AND ((e.age_max IS NOT NULL AND e.age_max <= 14) "
    f"OR {CHILD_TITLE_SUMMARY} ~ '{CHILD_AGES}' "
    f"OR ({CHILD_TITLE_SUMMARY} ~ '{CHILD_WORDS}' "
    f"AND {CHILD_TITLE_SUMMARY} !~ '{CHILD_PRICE_TEXT}' "
    f"AND lower(COALESCE(NULLIF(e.category_norm, ''), e.category, '')) <> 'exposition')))"
)
# Catégorie d'un événement : l'unifiée (Mistral), sinon celle de la source.
# category_norm vaut '' (et non NULL) tant que l'événement n'est pas classé :
# sans NULLIF, « Lecture » ou « Sport » d'un partenaire ne comptaient pas.
EVENT_CATEGORY = "lower(COALESCE(NULLIF(e.category_norm, ''), e.category, ''))"
INDOOR_CONDITION = (f"({EVENT_CATEGORY} <> ALL ({OUTDOOR_CATEGORIES}) "
                    f"AND {EVENT_TEXT} !~ {OUTDOOR_TEXT})")
OUTDOOR_CONDITION = (f"({EVENT_CATEGORY} = ANY ({OUTDOOR_CATEGORIES}) "
                     f"OR {EVENT_TEXT} ~ {OUTDOOR_TEXT})")
NEAR_CONDITION = """(sortiria.normalize_city(e.city) = %(near_city)s OR (
        e.latitude IS NOT NULL AND sortiria.distance_km(
            (SELECT avg(c.latitude) FROM sortiria.events AS c
             WHERE sortiria.normalize_city(c.city) = %(near_city)s AND c.latitude IS NOT NULL),
            (SELECT avg(c.longitude) FROM sortiria.events AS c
             WHERE sortiria.normalize_city(c.city) = %(near_city)s AND c.longitude IS NOT NULL),
            e.latitude, e.longitude) <= %(radius)s))"""
RRF_K = 60               # fusion des rangs (Reciprocal Rank Fusion) : 1 / (60 + rang)
POOL = 200               # rangs gardés par chaque recherche avant fusion


# Verbes de pratique (17/10/2026) : « je veux chanter » cherche où chanter
# soi-même. Pour chaque verbe (app/query_understanding.py, PRACTICE_VERBS) :
# - ce qui y répond, au format to_tsquery (texte sans accents, racines) ;
# - les types qui ne comptent pas comme réponse, sauf si le titre dit qu'on y
#   participe (« Stage de chant », rangé en concert, reste une réponse) ;
# - des mots ajoutés au texte vectorisé, pour que le sens aille vers la pratique.
LEARN = "(cours | stage:* | atelier:* | initiation:* | apprendr:*)"
# Arts plastiques (18/10/2026) : sans « cours », qui a la même racine que « cour »
# (« les cours d'hôtels particuliers » répondait à « apprendre à dessiner »).
LEARN_ART = "(stage:* | atelier:* | initiation:* | apprendr:*)"
PRACTICES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "chanter": (f"karaoke:* | chorale:* | (chant:* & {LEARN}) | (scene & ouverte) | (open & mic)",
                ("concert", "spectacle", "theatre"),
                "karaoké, cours de chant, stage de chant, chorale, scène ouverte"),
    "danser": ((f"bal | guinguette:* | (soiree & dansante) | milonga:* | "
                f"({LEARN} & (danse:* | salsa | tango | bachata | swing | country | flamenco))"),
               ("concert", "spectacle", "theatre"),
               "bal, guinguette, soirée dansante, cours de danse, stage de danse"),
    # « jeu » sans préfixe : « jeu:* » trouverait « jeudi ».
    "jouer": ("jeu | jeux | tournoi:* | ludotheque:* | (escape & game) | quiz:* | (blind & test)",
              ("theatre", "spectacle", "concert", "cinema"),
              "jeux de société, tournoi, ludothèque, escape game"),
    "peindre": (f"{LEARN_ART} & (peintur:* | aquarell:* | acryliqu:*)", ("exposition",),
                "atelier de peinture, stage d'aquarelle"),
    "dessiner": (f"{LEARN_ART} & (dessin:* | croquis | manga:* | illustration:* | carnet:*)",
                 ("exposition",),
                 "atelier de dessin, croquis, illustration"),
    "sculpter": (f"{LEARN_ART} & (sculptur:* | modelag:* | poterie:* | ceramiqu:* | argile)",
                 ("exposition",),
                 "atelier de sculpture, modelage, poterie"),
    # « Faire du théâtre », « jouer au théâtre », « faire de l'impro » (18/10/2026) :
    # cours, stages, ateliers d'impro ; pas les pièces à voir.
    # « impro » seul, ou « improvisation » avec « théâtre » : pas « Atelier de poterie :
    # improvisations ».
    "theatre": (f"{LEARN} & (theatr:* | impro | comedi:* | clown:*)",
                ("spectacle", "theatre", "concert"),
                "cours de théâtre, atelier d'improvisation, stage de théâtre"),
    "cuisiner": (f"{LEARN} & (cuisin:* | culinair:* | patisseri:* | recette:*)",
                 ("marche", "degustation", "fete"),
                 "atelier de cuisine, cours de cuisine, pâtisserie"),
}
PARTICIPATION = f"{LEARN} | karaoke:* | (scene & ouverte) | bal | guinguette:* | tournoi:*"
# Ce qui se pratique, pour « regarder » (titre et catégorie de la source) :
# « Bal folk », « Soirée dansante », « Cours de salsa » ne sont pas des spectacles.
# « dansant » a la même racine que « danse » (« dans ») : seulement après soirée, repas, thé.
PRACTISED = f"{PARTICIPATION} | ((soiree | repas | the) & dansant) | baleti:* | milonga:*"


def lexical_query(keywords: Sequence[str]) -> str:
    """Met les mots-clés au format to_tsquery : « jazz | echecs ».

    N'importe lequel suffit, plus il y en a, mieux l'événement est classé.

    Parameters
    ----------
    keywords : Sequence[str]
        Les mots-clés de la question.

    Returns
    -------
    str
        Les mots simplifiés joints par « | », complétés par « :* » à partir de
        4 lettres ; chaîne vide s'il n'en reste aucun.
    """

    words = [word for word in (plain(keyword) for keyword in keywords)
             if re.fullmatch(r"[a-z0-9]+", word)]
    # Préfixe (15/10/2026) : « photo » trouve aussi « photographies », « ciné »
    # trouve « cinéma » (le mot est d'abord ramené à sa racine, puis complété).
    return " | ".join(f"{word}:*" if len(word) >= 4 else word for word in dict.fromkeys(words))


def type_query(words: Sequence[str]) -> str:
    """Types de sortie au format to_tsquery, racines exactes : « jeux | concert ».

    Parameters
    ----------
    words : Sequence[str]
        Les mots de type (catégories et mots de la question).

    Returns
    -------
    str
        Les mots simplifiés (lettres seules) joints par « | », sans préfixe.
    """

    kept = [word for word in (plain(word) for word in words) if re.fullmatch(r"[a-z]+", word)]
    return " | ".join(dict.fromkeys(kept))


def type_keywords(keywords: Sequence[str]) -> list[str]:
    """Mots de la question qui désignent un type de sortie (« jeux », « concert »).

    Parameters
    ----------
    keywords : Sequence[str]
        Les mots-clés de la question.

    Returns
    -------
    list[str]
        Ceux qui figurent dans `QUESTION_CATEGORIES` (au pluriel ou non).
    """

    return [word for word in keywords
            if word in QUESTION_CATEGORIES or word.rstrip("sx") in QUESTION_CATEGORIES]


def distinctive_keywords(keywords: Sequence[str]) -> list[str]:
    """Mots précis de la question (« échecs », « Fabre », « Halloween »).

    Ni un type de sortie (« concert », « expo »), déjà traité par la catégorie ;
    les genres de `PRECISE_SUBJECTS` (« cirque », « jazz ») restent des mots précis.

    Parameters
    ----------
    keywords : Sequence[str]
        Les mots-clés de la question.

    Returns
    -------
    list[str]
        Les mots précis, dans l'ordre.
    """

    return [word for word in keywords
            if word in PRECISE_SUBJECTS or word.rstrip("sx") in PRECISE_SUBJECTS
            or (word not in QUESTION_CATEGORIES and word.rstrip("sx") not in QUESTION_CATEGORIES)]


def keep_exact_matches(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, bool]:
    """Ne garde que ce qui répond exactement, quand il y en a.

    1. Des événements citent les mots précis de la question (« échecs ») :
       seulement eux ;
    2. parmi ceux-là, des événements sont du type demandé (« concert ») :
       seulement eux.
    Sinon, rien n'est retiré : ce sont des idées voisines, et Mistral le dira.
    Renvoie (lignes, mots trouvés ?, type trouvé ?).

    Parameters
    ----------
    rows : list[dict[str, Any]]
        Les séances candidates, avec `keyword_match`, `keyword_hits`,
        `visible_hits` et `category_match` (`build_search_query`).

    Returns
    -------
    tuple[list[dict[str, Any]], bool, bool]
        Les lignes gardées, vrai si des mots précis ont été trouvés, vrai si le
        type demandé a été trouvé.
    """

    # Plusieurs mots précis : seulement ceux qui en ont le plus (« blind test » :
    # les deux mots, pas une dictée qui propose de « tester » son orthographe).
    # Parmi eux, d'abord ceux où le visiteur voit les mots (titre, résumé, lieu).
    def counts(row: Mapping[str, Any]) -> tuple[int, int]:
        """Couple (mots visibles, mots trouvés) d'une ligne."""
        hits = int(row.get("keyword_hits") or (1 if row.get("keyword_match") else 0))
        return int(row.get("visible_hits") or 0), hits

    best = max((counts(row) for row in rows if row.get("keyword_match")), default=(0, 0))
    by_words = [row for row in rows if row.get("keyword_match") and counts(row) >= best]
    if by_words:
        rows = by_words
    by_type = [row for row in rows if row.get("category_match")]
    if by_type:
        rows = by_type
    return rows, bool(by_words), bool(by_type)



PARTNER_CANDIDATES = 20     # séances partenaires toujours examinées, en plus des 80 meilleures


def fetch_candidates(connection: Any, plan: QueryPlan, vector: str | None,
                     ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Séances candidates : les mieux classées, plus les meilleures séances des
    partenaires qui respectent les mêmes filtres (15/10/2026).

    Sans question (cases cochées seules), le classement se fait par date : un
    karaoké partenaire dans 15 jours tombait au-delà des 80 premières séances
    et n'était jamais examiné. Les séances partenaires sont ajoutées à la fin ;
    `partners_first` décide ensuite si elles répondent (et les met en tête).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    plan : QueryPlan
        Le plan de recherche (contraintes, types, mots-clés).
    vector : str | None
        Vecteur de la question au format pgvector, ou None (classement par date et mots).

    Returns
    -------
    tuple[list[dict[str, Any]], dict[str, Any]]
        Les séances candidates, et les paramètres de la requête principale
        (`lexical` y dit si les mots-clés ont servi).
    """

    sql, params = build_search_query(plan, vector)
    rows = fetch_rows(connection, sql, params)
    seen = {str(row["event_id"]) for row in rows}
    partner_sql, partner_params = build_search_query(plan, vector, limit=PARTNER_CANDIDATES,
                                                     partners_only=True)
    rows += [row for row in fetch_rows(connection, partner_sql, partner_params)
             if str(row["event_id"]) not in seen]
    return rows, params


WIDER_PERIOD = timedelta(days=90)   # sujet introuvable : jusqu'aux 3 prochains mois


# Mots de la question qui figurent dans au moins un titre de la base
# (sql/sortiria/07_vocabulary.sql). Tant que le vocabulaire n'a pas été
# calculé (base neuve), les titres sont lus directement.
KNOWN_SUBJECTS_SQL = """
SELECT DISTINCT w.word
FROM unnest(%(words)s::text[]) AS w(word),
     LATERAL unnest(sortiria.title_lexemes(w.word)) AS s
WHERE EXISTS (
        SELECT 1 FROM sortiria.title_vocabulary AS v
        WHERE CASE WHEN length(w.word) >= 4 THEN v.lexeme LIKE s.lexeme || '%%'
                   ELSE v.lexeme = s.lexeme END)
   OR (NOT EXISTS (SELECT 1 FROM sortiria.title_vocabulary)
       AND EXISTS (SELECT 1 FROM sortiria.events AS e
                   WHERE e.is_outing AND sortiria.title_lexemes(e.title) @@ (
                       quote_literal(s.lexeme)
                       || CASE WHEN length(w.word) >= 4 THEN ':*' ELSE '' END)::tsquery))
"""


def known_subjects(connection: Any, words: Sequence[str]) -> set[str]:
    """Mots de la question écrits dans au moins un titre d'événement de la base.

    Aucune liste à tenir à la main : le vocabulaire suit la base. Une base
    sans la table (schéma pas encore mis à jour) : aucun mot reconnu, la
    recherche fonctionne comme avant.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (annulée si la requête échoue).
    words : Sequence[str]
        Les mots précis de la question.

    Returns
    -------
    set[str]
        Les mots trouvés dans au moins un titre ; vide en cas d'erreur.
    """

    if not words:
        return set()
    try:
        rows = fetch_rows(connection, KNOWN_SUBJECTS_SQL, {"words": list(words)})
        return {str(row["word"]) for row in rows}
    except Exception:  # noqa: BLE001 (la reconnaissance des sujets est un plus)
        connection.rollback()
        return set()


def on_subject(rows: Sequence[Mapping[str, Any]], plan: QueryPlan) -> list[Mapping[str, Any]]:
    """Lignes qui parlent du sujet de la question.

    Les mots précis (« photo », « cirque ») s'il y en a, sinon le type demandé
    (« concert »).

    Parameters
    ----------
    rows : Sequence[Mapping[str, Any]]
        Les séances candidates.
    plan : QueryPlan
        Le plan de recherche (mots-clés).

    Returns
    -------
    list[Mapping[str, Any]]
        Les lignes avec `keyword_match` (mots précis) ou `category_match` (type).
    """

    if distinctive_keywords(plan.keywords):
        return [row for row in rows if row.get("keyword_match")]
    return [row for row in rows if row.get("category_match")]


def lexical_words(words: Sequence[str]) -> set[str]:
    """Mots distincts de la recherche par mots (sans les synonymes ajoutés).

    Parameters
    ----------
    words : Sequence[str]
        Les mots de la question.

    Returns
    -------
    set[str]
        Les termes de `lexical_query`, un par mot distinct.
    """

    return {word for word in lexical_query(words).split(" | ") if word}


def core_words(words: Sequence[str]) -> list[str]:
    """Mots de la question, sans les synonymes appris (une alternative, pas un mot de plus).

    Parameters
    ----------
    words : Sequence[str]
        Les mots de la question.

    Returns
    -------
    list[str]
        Les mots qui ne sont ni un synonyme appris ni sa cible.
    """

    from app.query_understanding import learned_synonyms

    synonyms = learned_synonyms()
    return [word for word in words if word not in synonyms and word not in synonyms.values()]


def best_match(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Meilleur couple (mots visibles, mots trouvés) parmi les lignes sur le sujet.

    Parameters
    ----------
    rows : Sequence[Mapping[str, Any]]
        Les séances candidates.

    Returns
    -------
    tuple[int, int]
        Le plus grand couple (`visible_hits`, `keyword_hits`), ou (0, 0).
    """

    return max(((int(row.get("visible_hits") or 0), int(row.get("keyword_hits") or 0))
                for row in rows if row.get("keyword_match")), default=(0, 0))


def plan_can_widen(plan: QueryPlan) -> bool:
    """Le plan a des contraintes que `widen_for_subject` peut relâcher.

    Parameters
    ----------
    plan : QueryPlan
        Le plan de recherche.

    Returns
    -------
    bool
        Vrai s'il y a un public, un prix, un moment, un cadre, un lieu, ou une
        période plus courte que `WIDER_PERIOD`.
    """

    return bool(plan.public or plan.free_only or plan.max_price is not None or plan.evening
                or plan.moment or plan.setting or plan.zone or plan.cities or plan.near
                or plan.end < plan.start + WIDER_PERIOD)


def widen_for_subject(connection: Any, plan: QueryPlan, vector: str | None,
                      ) -> tuple[list[dict[str, Any]], QueryPlan, list[str]]:
    """Le sujet demandé est absent avec les contraintes choisies (15/10/2026).

    Plutôt que 8 idées sans rapport (« cirque » → ateliers et jeux), on cherche
    le sujet lui-même en relâchant les contraintes, une étape à la fois :
    0. « à Montpellier » : la métropole, toutes contraintes gardées (16/10/2026) ;
    1. public, gratuit, budget, soirée, intérieur/plein air ;
    2. puis la période, étendue aux 3 prochains mois ;
    3. puis le lieu (la métropole, puis partout).
    Renvoie (lignes sur le sujet, plan élargi, contraintes relâchées) ; aucune
    ligne si le sujet n'existe nulle part : mieux vaut le dire que proposer
    n'importe quoi.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    plan : QueryPlan
        Le plan de recherche dont le sujet est absent.
    vector : str | None
        Vecteur de la question au format pgvector, ou None.

    Returns
    -------
    tuple[list[dict[str, Any]], QueryPlan, list[str]]
        Les lignes sur le sujet, le plan élargi qui les a trouvées, et les
        contraintes relâchées (libellés pour la réponse) ; ([], plan, []) si
        rien n'est trouvé.
    """

    # Libellés des contraintes relâchées à la première étape
    dropped: list[str] = []
    if plan.public:
        dropped.append(f"pour le public « {PUBLIC_LABELS.get(plan.public, plan.public)} »")
    if plan.free_only or plan.max_price is not None:
        dropped.append("gratuit" if plan.free_only else f"à moins de {plan.max_price:g} €")
    if plan.evening or plan.moment:
        dropped.append(MOMENT_LABELS["soir" if plan.evening else str(plan.moment)])
    if plan.setting:
        dropped.append({"interieur": "en intérieur", "exterieur": "en plein air"}[plan.setting])
    # Construire les plans élargis, du plus proche au plus large
    steps: list[tuple[QueryPlan, list[str]]] = []
    if plan.cities and plan.zone and not plan.near:
        # « à Montpellier » : d'abord la métropole, avec toutes les autres
        # contraintes (un spectacle gratuit à Castelnau plutôt qu'un payant).
        steps.append((replace(plan, cities=(), from_question=set()), ["dans la commune même"]))
    steps.append((replace(plan, public=None, free_only=False, max_price=None, evening=False,
                          moment=None, setting=None, from_question=set()), list(dropped)))
    longer = replace(steps[-1][0], end=max(plan.end, plan.start + WIDER_PERIOD),
                     period_label="3 prochains mois")
    steps.append((longer, [*dropped, f"pour « {plan.period_label} »"]))
    if plan.cities and plan.zone and not plan.near:
        # « à Montpellier » : d'abord toute la métropole, avant de lâcher le lieu.
        steps.append((replace(longer, cities=()), [*steps[-1][1], "dans la commune même"]))
    if plan.zone or plan.cities or plan.near:
        steps.append((replace(longer, zone=None, cities=(), near=None),
                      [*steps[-1][1], "à cet endroit"]))
    # Essayer chaque plan : le premier qui trouve le sujet l'emporte
    for wider, relaxed in steps:
        rows = on_subject(fetch_candidates(connection, wider, vector)[0], wider)
        if rows:
            return list(rows), wider, relaxed
    return [], plan, []


def plan_from_filters(filters: SearchFilters, now: datetime,
                      categories: Sequence[str] = ()) -> QueryPlan:
    """Plan de recherche fait des seuls filtres cochés (sans question).

    Parameters
    ----------
    filters : SearchFilters
        Les cases cochées.
    now : datetime
        Heure de référence pour la période.
    categories : Sequence[str]
        Types de sortie à préférer ; aucun par défaut.

    Returns
    -------
    QueryPlan
        Le plan correspondant.
    """

    return merge_with_filters(Understanding(categories=tuple(categories)), public=filters.public,
                              zone=filters.zone, period_bounds=period_bounds(filters.period, now),
                              period_label=PERIOD_LABELS[filters.period],
                              free_only=filters.free_only)


def build_search_query(plan: QueryPlan | SearchFilters, vector: str | None,
                       now: datetime | None = None, limit: int = CANDIDATES,
                       categories: Sequence[str] = (),
                       partners_only: bool = False) -> tuple[str, dict[str, Any]]:
    """Requête de recherche hybride sur `v_upcoming_events` (paramètres psycopg2).

    1. **Filtres stricts** : période, public, zone ou communes, gratuit, soirée.
    2. **Deux classements** des événements qui restent :
       - par le sens : distance entre le vecteur de la question et celui de
         l'événement (pgvector) ;
       - par les mots : recherche plein texte en français (« jazz », « échecs »,
         « Fabre »), qui retrouve les noms propres que le sens rate.
    3. **Fusion des rangs** (RRF) : score = 1/(60 + rang sens) + 1/(60 + rang mots).
       Un événement bien classé par les deux passe devant.
    4. Les événements du type demandé (théâtre…) passent devant les autres,
       sans les exclure.

    Sans vecteur ni mots-clés : les plus proches dans le temps d'abord.

    Parameters
    ----------
    plan : QueryPlan | SearchFilters
        Le plan de recherche ; de simples filtres sont d'abord changés en plan.
    vector : str | None
        Vecteur de la question au format pgvector, ou None.
    now : datetime | None
        Heure de référence, utilisée seulement si `plan` est un `SearchFilters`
        (par défaut maintenant).
    limit : int
        Nombre maximum de séances. Par défaut `CANDIDATES` (80).
    categories : Sequence[str]
        Types de sortie, utilisés seulement si `plan` est un `SearchFilters`.
    partners_only : bool
        Vrai pour ne chercher que les séances des partenaires.

    Returns
    -------
    tuple[str, dict[str, Any]]
        La requête SQL et ses paramètres nommés.
    """

    # Filtres stricts : période, public, lieu, prix, cadre, moment
    if isinstance(plan, SearchFilters):
        plan = plan_from_filters(plan, now or datetime.now(PARIS), categories)
    conditions = ["e.is_outing", "COALESCE(e.end_at, e.start_at) >= %(start)s",
                  "e.start_at < %(end)s"]
    if partners_only:
        conditions.append("e.partner_id IS NOT NULL")
    params: dict[str, Any] = {"start": plan.start, "end": plan.end, "limit": limit, "pool": POOL,
                              "categories": list(plan.categories)}
    if plan.public == "enfant":
        conditions.append(CHILD_CONDITION)
    elif plan.public:
        conditions.append("%(public)s = ANY (e.audiences)")
        params["public"] = plan.public
    if plan.cities:
        conditions.append("sortiria.normalize_city(e.city) = ANY (%(cities)s)")
        params["cities"] = list(plan.cities)
    elif plan.zone:
        conditions.append("sortiria.event_zone(e.latitude, e.longitude, e.city) = %(zone)s")
        params["zone"] = ZONES[plan.zone]
    if plan.near:
        # « Près de Sète » : la commune elle-même, ou un événement à moins de N km
        # du centre de ses événements géolocalisés (les fiches sans GPS, PDF et
        # partenaires, ne comptent que par leur commune).
        conditions.append(NEAR_CONDITION)
        params["near_city"], params["radius"] = plan.near
    if plan.free_only:
        conditions.append("e.is_free")
    elif plan.max_price is not None:
        # Budget : gratuit, ou prix d'entrée connu sous le seuil.
        conditions.append("(e.is_free OR e.price_min <= %(max_price)s)")
        params["max_price"] = plan.max_price
    if plan.setting == "interieur":
        conditions.append(INDOOR_CONDITION)
    elif plan.setting == "exterieur":
        conditions.append(OUTDOOR_CONDITION)
    if plan.evening:
        conditions.append("(NOT e.time_known "
                          "OR EXTRACT(HOUR FROM e.start_at AT TIME ZONE 'Europe/Paris') >= 17)")
    elif plan.moment in MOMENT_HOURS:
        first, last = MOMENT_HOURS[plan.moment]
        conditions.append("(NOT e.time_known "
                          "OR EXTRACT(HOUR FROM e.start_at AT TIME ZONE 'Europe/Paris') "
                          f"BETWEEN {first} AND {last - 1})")

    # Deux classements des séances filtrées : par le sens, par les mots
    practice = PRACTICES.get(plan.practice or "")
    lexical = practice[0] if practice else lexical_query(plan.keywords)
    ctes = [f"""base AS (
    SELECT e.event_id, e.embedding, {LEXICAL_DOCUMENT} AS document
    FROM sortiria.v_upcoming_events AS e
    WHERE {' AND '.join(conditions)})"""]
    joins, score = [], []
    if vector:
        params["vector"] = vector
        ctes.append("""by_meaning AS (
    SELECT event_id, row_number() OVER (ORDER BY embedding <=> %(vector)s::vector) AS rank
    FROM base WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %(vector)s::vector LIMIT %(pool)s)""")
        joins.append("LEFT JOIN by_meaning ON by_meaning.event_id = b.event_id")
        score.append(f"COALESCE(1.0 / ({RRF_K} + by_meaning.rank), 0)")
    if lexical:
        params["lexical"] = lexical
        ctes.append("""by_words AS (
    SELECT event_id, row_number() OVER (ORDER BY ts_rank_cd(document, query) DESC) AS rank
    FROM base, to_tsquery('french', %(lexical)s) AS query
    WHERE document @@ query
    ORDER BY ts_rank_cd(document, query) DESC LIMIT %(pool)s)""")
        joins.append("LEFT JOIN by_words ON by_words.event_id = b.event_id")
        score.append(f"COALESCE(1.0 / ({RRF_K} + by_words.rank), 0)")
    # Fusion des rangs, puis l'expression « du type demandé »
    score_sql = " + ".join(score) or "0"
    category_sql = f"{EVENT_CATEGORY} = ANY (%(categories)s)"
    # Le type demandé écrit dans le TITRE ou la catégorie de la source compte
    # aussi (15/10/2026) : « Atelier jeux en famille », rangé en « atelier »,
    # répond à « jeux » ; « Soirée DJ » (catégorie d'un bar) à « soirée ». Pas
    # la description : « après le concert » ne fait pas un concert. Racines
    # exactes, sans préfixe : « jeux » ne doit pas trouver « jeudi ».
    type_words = type_query([*type_keywords(plan.keywords), *plan.categories])
    title_and_category = ("to_tsvector('french', translate(lower(concat_ws(' ', e.title, "
                          f"e.category)), '{ACCENTS_FROM}', '{ACCENTS_TO}'))")
    if type_words:
        params["type_words"] = type_words
        category_sql = (f"({category_sql} OR {title_and_category} "
                        "@@ to_tsquery('french', %(type_words)s))")
    if plan.watch and plan.categories:
        # Regarder (17/10/2026) : un bal, un cours ou un stage n'est pas du type demandé.
        params["practised"] = PRACTISED
        category_sql = (f"({category_sql} AND NOT {title_and_category} "
                        "@@ to_tsquery('french', %(practised)s))")
    # Mots précis trouvés dans l'événement, comptés un par un (15/10/2026) :
    # « blind test » ne doit pas garder tout ce qui contient « tester » (une
    # dictée, une dégustation). `keep_exact_matches` garde ceux qui en ont le plus.
    # Mots trouvés là où le visiteur les voit (titre, résumé, lieu) : une carte
    # « Concert acoustique » pour « karaoké », parce que la longue description
    # cite un karaoké, ne se comprend pas. Ceux-là passent devant, et les
    # autres ne servent que s'il n'y en a aucun (`keep_exact_matches`).
    visible_text = ("to_tsvector('french', translate(lower(concat_ws(' ', e.title, e.summary, "
                    f"e.venue_name)), '{ACCENTS_FROM}', '{ACCENTS_TO}'))")
    hits, visible = [], []
    precise_terms = lexical_query(distinctive_keywords(plan.keywords)).split(" | ")
    for index, word in enumerate(dict.fromkeys(w for w in precise_terms if w)):
        params[f"word{index}"] = word
        hits.append(f"(b.document @@ to_tsquery('french', %(word{index})s))::int")
        visible.append(f"({visible_text} @@ to_tsquery('french', %(word{index})s))::int")
    if practice:
        # Pratique : une seule condition (le verbe), au lieu des mots un par un.
        title = ("to_tsvector('french', translate(lower(e.title), "
                 f"'{ACCENTS_FROM}', '{ACCENTS_TO}'))")
        params.update(practice=practice[0], practice_excluded=list(practice[1]),
                      participation=PARTICIPATION)
        kept = (f"({EVENT_CATEGORY} <> ALL (%(practice_excluded)s) "
                f"OR {title} @@ to_tsquery('french', %(participation)s))")
        hits = [f"(b.document @@ to_tsquery('french', %(practice)s) AND {kept})::int"]
        visible = [f"({visible_text} @@ to_tsquery('french', %(practice)s) AND {kept})::int"]
    # Assembler la requête : mots visibles, mots trouvés, type, score, date
    hits_sql = " + ".join(hits) or "0"
    visible_sql = " + ".join(visible) or "0"
    sql = (
        f"WITH {', '.join(ctes)}\n"
        f"SELECT {EVENT_COLUMNS}, ({category_sql}) AS category_match, "
        f"({hits_sql}) > 0 AS keyword_match,\n"
        f"       ({hits_sql}) AS keyword_hits, ({visible_sql}) AS visible_hits,\n"
        f"       ({score_sql}) AS score\n"
        "FROM base AS b\n"
        "JOIN sortiria.v_upcoming_events AS e ON e.event_id = b.event_id\n"
        "LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id\n"
        + "".join(f"{join}\n" for join in joins)
        + "ORDER BY visible_hits DESC, keyword_hits DESC, category_match DESC, score DESC, "
        + "e.start_at\n"
        + "LIMIT %(limit)s"
    )
    return sql, params


def fetch_rows(connection: Any, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Exécute une requête et renvoie des dictionnaires.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (psycopg2).
    sql : str
        La requête, avec des paramètres nommés `%(nom)s`.
    params : Mapping[str, Any]
        Les valeurs des paramètres.

    Returns
    -------
    list[dict[str, Any]]
        Une ligne par dictionnaire, clés = noms des colonnes.
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, dict(params))
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


# Offres partenaires : les placer en tête quand elles répondent (classement).
# Sans type ni mot précis : une offre partenaire doit être parmi les 40 plus proches.
PARTNER_TOP_RANK = 40


# Un titre qui nomme un public en exclut d'autres : « Initiation escalade
# enfants » ne passe pas en tête pour « un truc à faire avec des ados »
# (grille de pertinence du 16/10/2026).
YOUNG_WORDS = ("enfant", "enfants", "tout petit", "tout petits", "bebe", "bebes", "maternelle",
               "3 6 ans")
TITLE_PUBLIC_CLASH = {
    "ados": YOUNG_WORDS,
    "adultes": YOUNG_WORDS,
    "seniors": (*YOUNG_WORDS, "ado", "ados", "adolescent", "adolescents"),
    "soiree": YOUNG_WORDS,
}


def partner_fits(row: Mapping[str, Any], plan: QueryPlan, main: Sequence[str]) -> bool:
    """Une offre partenaire vise-t-elle le type précis et le public demandés ?

    Le type : celui que la question vise (`main_categories`), pas un type
    voisin accepté en plus (« spectacle » pour « théâtre »), sauf si le mot
    demandé est dans l'offre. Le public : le titre ne doit pas en nommer un
    autre. Sans ces deux conditions, l'offre reste à sa place parmi les autres.

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance du partenaire.
    plan : QueryPlan
        Le plan de recherche (public demandé).
    main : Sequence[str]
        Les types que la question vise (`main_categories`).

    Returns
    -------
    bool
        Vrai si l'offre peut passer en tête.
    """

    title = f" {plain(str(row.get('title') or ''))} "
    clash = TITLE_PUBLIC_CLASH.get(plan.public or "", ())
    if any(f" {word} " in title for word in clash):
        return False
    if row.get("keyword_match") or not main:
        return True
    # Seul cas refusé : l'offre n'est pas du type visé (« spectacle » pour
    # « théâtre », pour « un spectacle de danse ») et ne le nomme pas.
    category = str(row.get("category_norm") or row.get("category") or "").strip().lower()
    secondary = category not in main
    return not secondary or any(f" {plain(CATEGORY_LABELS.get(item, item))} " in title
                                for item in main)


def partners_first(rows: list[dict[str, Any]], plan: QueryPlan, exact: bool,
                   question: str = "") -> list[dict[str, Any]]:
    """Place en tête les offres partenaires qui répondent à la demande (15/10/2026).

    - Réponse exacte : une séance de partenaire déjà retenue respecte toutes
      les contraintes (période, lieu, public, gratuit) et, si la question
      précise un type ou des mots (« concert », « jazz »), elle les a aussi
      (`keep_exact_matches`). Sans type ni mot précis, elle doit en plus être
      parmi les plus proches de la question par le sens. Ces offres passent
      en tête des cartes, dans leur ordre de pertinence.
    - Pas de réponse exacte : une offre partenaire qui parle du sujet (mot ou
      type demandé) reste en tête des cartes ; les autres quittent les cartes
      d'idées voisines, les plus proches sont proposées à part, dans
      l'encadré « offres partenaires » (`partner_offers`), et restent dans la
      colonne de droite.
    - Dans les deux cas (16/10/2026), l'offre doit viser le type précis et le
      public demandés (`partner_fits`). Les offres passées en tête sont
      marquées `partner_ahead` : l'ordre des cartes et la rédaction s'en
      servent ; une offre partenaire qui n'y est pas garde sa place.

    Parameters
    ----------
    rows : list[dict[str, Any]]
        Les séances retenues, dans l'ordre de pertinence.
    plan : QueryPlan
        Le plan de recherche.
    exact : bool
        Vrai si les séances répondent exactement (ou au sujet élargi).
    question : str
        La question, pour trouver le type visé ; vide pour des filtres seuls.

    Returns
    -------
    list[dict[str, Any]]
        Les offres passées en tête, puis les autres séances.
    """

    main = main_categories(question) if question else ()
    if not exact:
        # Idées voisines : une offre partenaire qui parle du sujet (mot ou type
        # demandé) reste dans les cartes, en tête ; les autres en sortent.
        on_topic = [{**row, "partner_ahead": True} for row in rows
                    if row.get("partner_id") is not None
                    and (row.get("keyword_match") or row.get("category_match"))
                    and partner_fits(row, plan, main)]
        return on_topic + [row for row in rows if row.get("partner_id") is None]
    # Réponse exacte : une offre qui répond passe en tête
    precise = bool(plan.categories) or bool(distinctive_keywords(plan.keywords))
    ranked_by_meaning = any(row.get("score") for row in rows)
    ahead, rest = [], []
    for rank, row in enumerate(rows):
        answers = precise or not ranked_by_meaning or rank < PARTNER_TOP_RANK
        if row.get("partner_id") is not None and answers and partner_fits(row, plan, main):
            ahead.append({**row, "partner_ahead": True})
        else:
            rest.append(row)
    # Parmi les offres qui répondent, la formule départage (premium d'abord),
    # sans changer l'ordre de pertinence à formule égale (tri stable).
    ahead.sort(key=lambda row: -int(row.get("partner_rank") or 0))
    return ahead + rest


MAX_PARTNER_OFFERS = 3


def build_partner_query(plan: QueryPlan, vector: str | None, exclude: Sequence[str], *,
                        keep_place: bool = True,
                        limit: int = MAX_PARTNER_OFFERS) -> tuple[str, dict[str, Any]]:
    """Offres des partenaires les plus proches de la demande.

    Même période que la demande (et même lieu si `keep_place`), seulement les
    événements d'un partenaire ; classées par proximité de sens avec la
    question (mises en avant d'abord à égalité), sinon par date.

    Parameters
    ----------
    plan : QueryPlan
        Le plan de recherche (période, lieu).
    vector : str | None
        Vecteur de la question au format pgvector, ou None (tri par date).
    exclude : Sequence[str]
        Les événements déjà sur les cartes.
    keep_place : bool
        Vrai pour garder le lieu demandé. Par défaut vrai.
    limit : int
        Nombre maximum d'offres. Par défaut `MAX_PARTNER_OFFERS` (3).

    Returns
    -------
    tuple[str, dict[str, Any]]
        La requête SQL et ses paramètres nommés.
    """

    # Filtres : période, partenaires seulement, et le lieu si demandé
    conditions = ["e.is_outing", "e.partner_id IS NOT NULL",
                  "COALESCE(e.end_at, e.start_at) >= %(start)s",
                  "e.start_at < %(end)s", "NOT (e.event_id = ANY (%(exclude)s))"]
    params: dict[str, Any] = {"start": plan.start, "end": plan.end, "exclude": list(exclude),
                              "limit": limit}
    if keep_place and plan.near:
        conditions.append(NEAR_CONDITION)
        params["near_city"], params["radius"] = plan.near
    elif keep_place and plan.cities:
        conditions.append("sortiria.normalize_city(e.city) = ANY (%(cities)s)")
        params["cities"] = list(plan.cities)
    elif keep_place and plan.zone:
        conditions.append("sortiria.event_zone(e.latitude, e.longitude, e.city) = %(zone)s")
        params["zone"] = ZONES[plan.zone]
    # Ordre : par le sens si possible, sinon par formule puis par date
    order = "partner_rank DESC, e.start_at"
    if vector:
        params["vector"] = vector
        order = "e.embedding <=> %(vector)s::vector NULLS LAST, partner_rank DESC, e.start_at"
    sql = (f"SELECT {EVENT_COLUMNS}\nFROM sortiria.v_upcoming_events AS e\n"
           "JOIN sortiria.partners AS p ON p.partner_id = e.partner_id\n"
           f"WHERE {' AND '.join(conditions)}\nORDER BY {order}\nLIMIT %(limit)s")
    return sql, params


def embed_question(question: str) -> str:
    """Vecteur de la question au format pgvector.

    Parameters
    ----------
    question : str
        Le texte à vectoriser (mistral-embed).

    Returns
    -------
    str
        Le vecteur au format texte de pgvector.
    """

    from agent.services import embed_with_mistral
    from src.event.event_embedding import to_pgvector

    return to_pgvector(embed_with_mistral([question])[0])
