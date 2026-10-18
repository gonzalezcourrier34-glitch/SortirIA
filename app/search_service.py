"""Recherche d'événements pour l'interface : question, filtres, réponse sourcée.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), pour
l'interface Angular (`frontend/`). Il lit la base PostgreSQL remplie chaque
nuit par l'agent de collecte, jamais les sources elles-mêmes.

Une recherche se fait en quatre étapes, affichées en direct dans la fenêtre
« SortirIA cherche pour vous » :

1. **comprendre** : la question et les filtres (public, zone, période, gratuit)
   deviennent des contraintes ;
2. **rechercher** : la question est transformée en vecteur (mistral-embed) et
   comparée aux événements de `v_upcoming_events` (pgvector), en ne gardant
   que ceux qui respectent les contraintes ;
3. **filtrer** : on garde les plus proches (`MAX_PROPOSALS`) ;
4. **rédiger** : Mistral écrit une réponse courte qui cite ses sources [1],
   [2]... et propose des questions de relance. Il ne voit que les événements
   retenus : il ne peut pas en inventer.

Si Mistral ne répond pas, la recherche continue sans lui : tri par date au
lieu du vecteur, réponse rédigée par un modèle de phrase. L'interface reste
utilisable, seule la qualité baisse.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agent.watch import DEFAULT_PRICE, PRICES_USD_PER_MILLION
from app.query_understanding import (
    CATEGORY_LABELS,
    MOMENT_HOURS,
    MOMENT_LABELS,
    PRECISE_SUBJECTS,
    QUESTION_CATEGORIES,
    QueryPlan,
    Understanding,
    category_label,
    combine,
    is_follow_up,
    main_categories,
    merge_with_filters,
    plain,
    understand,
    wanted_categories,  # noqa: F401 (réexporté pour les appelants existants)
    zone_of_city,
)

PARIS = ZoneInfo("Europe/Paris")
PROMPT_VERSION = "2026-10-17.5"  # « regarder » : un bal n'est pas un spectacle de danse
DEFAULT_SEARCH_MODEL = "mistral-small-latest"
CANDIDATES = 80          # séances comparées à la question (regroupées ensuite par événement)
MAX_PROPOSALS = 8        # événements montrés et donnés à Mistral (2 rangées de 4 cartes, 15/10)
MAX_OTHER_DATES = 12     # autres dates gardées pour un même événement
RATING_WINDOW_DAYS = 7   # avis possible du début de l'événement à 7 jours après sa fin
SUMMARY_CHARS = 240

# « enfant » (18/10/2026) : pas un public de la base, mais une partie de
# « famille » : ce qui est pour l'enfant (âge, « jeune public », stage ou atelier
# enfants), quand « Famille » est ce qui se fait ensemble. « soiree » reste
# compris dans la phrase (« un bar », « une fête »), sans bouton.
PUBLICS = ("famille", "enfant", "soiree", "ados", "seniors")
ZONES = {"montpellier": "montpellier", "thau": "bassin_de_thau"}
PERIODS = ("aujourdhui", "weekend", "7jours", "3mois")

# Étapes affichées par l'interface (même ordre que frontend/src/app/search.model.ts).
STEP_UNDERSTAND = "comprendre"
STEP_SEARCH = "rechercher"
STEP_FILTER = "filtrer"
STEP_WRITE = "rediger"

DAYS = ("Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim.")
MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
PUBLIC_LABELS = {"famille": "famille", "enfant": "enfant", "soiree": "soirée", "ados": "ados", "seniors": "seniors"}
ZONE_LABELS = {"montpellier": "Montpellier et sa métropole", "thau": "bassin de Thau"}
PERIOD_LABELS = {"aujourdhui": "aujourd'hui", "weekend": "ce week-end", "7jours": "les 7 prochains jours",
                 "3mois": "les 3 prochains mois"}

WRITE_PROMPT = """Tu es SortirIA, un assistant qui conseille des sorties autour de Montpellier
et du bassin de Thau. On te donne la demande d'un utilisateur et une liste
numérotée d'événements réels tirés de notre base.

Règles :
- réponds en français, en 2 à 4 phrases, sur un ton simple et chaleureux ;
  vouvoie toujours l'utilisateur ;
- ne parle QUE des événements de la liste ; n'invente ni lieu, ni date, ni prix ;
- cite chaque événement mentionné par son numéro entre crochets : [1], [2] ;
- si aucun événement ne correspond vraiment, dis-le franchement ;
- si la demande précise un type de sortie absent de la liste, commence par le
  dire (« Je n'ai pas trouvé de théâtre gratuit en famille… »), puis présente
  les autres événements comme des idées voisines ;
- respecte les contraintes comprises (date, lieu, public, gratuit) : ne
  présente pas un événement qui ne les respecte pas comme s'il y répondait ;
- privilégie les événements qui répondent le mieux à la demande, pas forcément
  les premiers de la liste ; cite-en 2 à 4, pas plus ;
- les événements marqués « offre partenaire » répondent à la demande : cite-les
  en premier, simplement, sans ton publicitaire ;
- ne parle du prix que si la demande parle de budget ou de gratuité ;
- propose 3 questions de relance courtes (moins de 6 mots chacune, finies par
  un point d'interrogation) que l'utilisateur pourrait te poser ensuite
  (« Et s'il pleut ? », « Plutôt le soir ? ») : jamais une phrase sur les
  événements de la liste. Chaque relance est un seul choix, cliquable tel
  quel : « En intérieur ? », jamais « En intérieur ou en extérieur ? » ;

Réponds uniquement en JSON : {"reponse": "...", "suggestions": ["...", "...", "..."]}"""


# ----------------------------------------------------------------------------
# Contraintes
# ----------------------------------------------------------------------------

@dataclass(slots=True)
class SearchFilters:
    """Contraintes choisies dans l'interface (toutes facultatives)."""

    public: str | None = None
    zone: str | None = None
    period: str = "3mois"
    free_only: bool = False

    def __post_init__(self) -> None:
        if self.public not in (None, *PUBLICS):
            raise ValueError(f"public inconnu : {self.public}")
        if self.zone not in (None, *ZONES):
            raise ValueError(f"zone inconnue : {self.zone}")
        if self.period not in PERIODS:
            raise ValueError(f"période inconnue : {self.period}")

    def describe(self) -> str:
        """Résumé lisible, affiché à l'étape « comprendre »."""

        parts = [PERIOD_LABELS[self.period]]
        if self.public:
            parts.append(PUBLIC_LABELS[self.public])
        if self.zone:
            parts.append(ZONE_LABELS[self.zone])
        if self.free_only:
            parts.append("gratuit")
        return " · ".join(parts)


def period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
    """Début et fin (exclue) de la période, heure de Paris.

    « weekend » : du samedi 0 h au lundi 0 h ; le samedi ou le dimanche, ce
    week-end-ci (à partir de maintenant).
    """

    now = now.astimezone(PARIS)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "aujourdhui":
        return now, midnight + timedelta(days=1)
    if period == "weekend":
        saturday = midnight + timedelta(days=(5 - now.weekday()) % 7)
        if now.weekday() == 6:                      # dimanche : le week-end en cours
            saturday = midnight - timedelta(days=1)
        return max(now, saturday), saturday + timedelta(days=2)
    if period == "7jours":
        return now, now + timedelta(days=7)
    return now, now + timedelta(days=90)


# ----------------------------------------------------------------------------
# Requêtes SQL
# ----------------------------------------------------------------------------

EVENT_COLUMNS = """
    e.event_id, e.title, e.summary, e.description, e.category_norm, e.category,
    e.start_at, e.end_at, e.time_known, e.venue_name, e.city,
    e.price_text, e.price_min, e.is_free, e.audiences, e.age_min, e.age_max,
    e.url, e.source_name, e.source_attribution, e.partner_id, e.is_sponsored,
    e.venue_rating, e.venue_rating_count, p.name AS partner_name,
    -- Rang du partenaire (17/10/2026) : 2 premium, 1 mis en avant, 0 sinon
    -- (une formule payante échue ne compte plus : is_sponsored est faux).
    CASE WHEN NOT e.is_sponsored THEN 0 WHEN p.formula = 'premium' THEN 2 ELSE 1 END AS partner_rank,
    sortiria.event_zone(e.latitude, e.longitude, e.city) AS zone"""


# Texte comparé aux mots-clés : titre (deux fois, il compte plus), résumé,
# catégorie, lieu et début de la description ; sans accents, comme la question.
ACCENTS_FROM = "àâäáãåçéèêëíìîïñóòôöõúùûüýÿœ"
ACCENTS_TO = "aaaaaaceeeeiiiinooooouuuuyyo"
LEXICAL_DOCUMENT = (
    "to_tsvector('french', translate(lower(concat_ws(' ', e.title, e.title, e.summary, e.category_norm, "
    f"e.category, e.venue_name, e.city, left(e.description, 1500))), '{ACCENTS_FROM}', '{ACCENTS_TO}'))"
)
# Cadre (15/10/2026) : « s'il pleut » écarte les sorties de plein air ;
# « en plein air » ne garde qu'elles. D'après la catégorie et les mots du texte.
OUTDOOR_CATEGORIES = "ARRAY['nature', 'marche', 'sport', 'fete', 'festival']"
OUTDOOR_TEXT = "'plein air|balade|randonn|plage|jardin|en exterieur|a l exterieur|sentier|kayak|velo'"
EVENT_TEXT = (f"translate(lower(concat_ws(' ', e.title, e.summary, left(e.description, 600))), "
              f"'{ACCENTS_FROM}', '{ACCENTS_TO}')")
# « Enfant » (18/10/2026) : un événement familial pour l'enfant lui-même : âge
# maximal de 14 ans, ou le texte le dit (« jeune public », « dès 6 ans »,
# « 4-11 ans », « stage enfants »). Texte sans accents, en minuscules.
CHILD_TEXT = (r"\m(enfants|jeune public|jeunesse|tout petits?|bebes?|kids|maternelle"
              r"|(des|a partir de) ([2-9]|1[0-2]) ans|([0-9]|1[0-4]) ?(-|a|/) ?([0-9]|1[0-4]) ans)\M")
# Titre et résumé seulement : la description dit souvent « gratuit pour les enfants ».
CHILD_TITLE_SUMMARY = f"translate(lower(concat_ws(' ', e.title, e.summary)), '{ACCENTS_FROM}', '{ACCENTS_TO}')"
# « Gratuit pour les enfants », « tarif enfants » parlent du prix, pas du public.
CHILD_PRICE_TEXT = r"\m(gratuit|gratuite|tarif|reduit|entree)\M[^.]{0,25}\menfants\M"
CHILD_CONDITION = (f"('famille' = ANY (e.audiences) AND ((e.age_max IS NOT NULL AND e.age_max <= 14) "
                   f"OR ({CHILD_TITLE_SUMMARY} ~ '{CHILD_TEXT}' "
                   f"AND {CHILD_TITLE_SUMMARY} !~ '{CHILD_PRICE_TEXT}')))")
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
    "peindre": (f"{LEARN} & (peintur:* | aquarell:* | acryliqu:*)", ("exposition",),
                "atelier de peinture, stage d'aquarelle"),
    "dessiner": (f"{LEARN} & (dessin:* | croquis | manga:* | illustration:* | carnet:*)", ("exposition",),
                 "atelier de dessin, croquis, illustration"),
    "sculpter": (f"{LEARN} & (sculptur:* | modelag:* | poterie:* | ceramiqu:* | argile)", ("exposition",),
                 "atelier de sculpture, modelage, poterie"),
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
    """Mots-clés au format to_tsquery : « jazz | echecs » (n'importe lequel suffit,
    plus il y en a, mieux l'événement est classé)."""

    words = [word for word in (plain(keyword) for keyword in keywords) if re.fullmatch(r"[a-z0-9]+", word)]
    # Préfixe (15/10/2026) : « photo » trouve aussi « photographies », « ciné »
    # trouve « cinéma » (le mot est d'abord ramené à sa racine, puis complété).
    return " | ".join(f"{word}:*" if len(word) >= 4 else word for word in dict.fromkeys(words))


def type_query(words: Sequence[str]) -> str:
    """Types de sortie au format to_tsquery, racines exactes : « jeux | concert »."""

    kept = [word for word in (plain(word) for word in words) if re.fullmatch(r"[a-z]+", word)]
    return " | ".join(dict.fromkeys(kept))


def type_keywords(keywords: Sequence[str]) -> list[str]:
    """Mots de la question qui désignent un type de sortie (« jeux », « concert »)."""

    return [word for word in keywords if word in QUESTION_CATEGORIES or word.rstrip("sx") in QUESTION_CATEGORIES]


def distinctive_keywords(keywords: Sequence[str]) -> list[str]:
    """Mots précis de la question (« échecs », « Fabre », « Halloween ») : ni un
    type de sortie (« concert », « expo »), déjà traité par la catégorie."""

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
    """

    # Plusieurs mots précis : seulement ceux qui en ont le plus (« blind test » :
    # les deux mots, pas une dictée qui propose de « tester » son orthographe).
    # Parmi eux, d'abord ceux où le visiteur voit les mots (titre, résumé, lieu).
    def counts(row: Mapping[str, Any]) -> tuple[int, int]:
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


def fetch_candidates(connection: Any, plan: QueryPlan, vector: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Séances candidates : les mieux classées, plus les meilleures séances des
    partenaires qui respectent les mêmes filtres (15/10/2026).

    Sans question (cases cochées seules), le classement se fait par date : un
    karaoké partenaire dans 15 jours tombait au-delà des 80 premières séances
    et n'était jamais examiné. Les séances partenaires sont ajoutées à la fin ;
    `partners_first` décide ensuite si elles répondent (et les met en tête).
    """

    sql, params = build_search_query(plan, vector)
    rows = fetch_rows(connection, sql, params)
    seen = {str(row["event_id"]) for row in rows}
    partner_sql, partner_params = build_search_query(plan, vector, limit=PARTNER_CANDIDATES, partners_only=True)
    rows += [row for row in fetch_rows(connection, partner_sql, partner_params) if str(row["event_id"]) not in seen]
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
        WHERE CASE WHEN length(w.word) >= 4 THEN v.lexeme LIKE s.lexeme || '%%' ELSE v.lexeme = s.lexeme END)
   OR (NOT EXISTS (SELECT 1 FROM sortiria.title_vocabulary)
       AND EXISTS (SELECT 1 FROM sortiria.events AS e
                   WHERE e.is_outing AND sortiria.title_lexemes(e.title) @@ (
                       quote_literal(s.lexeme) || CASE WHEN length(w.word) >= 4 THEN ':*' ELSE '' END)::tsquery))
"""


def known_subjects(connection: Any, words: Sequence[str]) -> set[str]:
    """Mots de la question écrits dans au moins un titre d'événement de la base.

    Aucune liste à tenir à la main : le vocabulaire suit la base. Une base
    sans la table (schéma pas encore mis à jour) : aucun mot reconnu, la
    recherche fonctionne comme avant.
    """

    if not words:
        return set()
    try:
        return {str(row["word"]) for row in fetch_rows(connection, KNOWN_SUBJECTS_SQL, {"words": list(words)})}
    except Exception:  # noqa: BLE001 (la reconnaissance des sujets est un plus)
        connection.rollback()
        return set()


def on_subject(rows: Sequence[Mapping[str, Any]], plan: QueryPlan) -> list[Mapping[str, Any]]:
    """Lignes qui parlent du sujet de la question : les mots précis (« photo »,
    « cirque ») s'il y en a, sinon le type demandé (« concert »)."""

    if distinctive_keywords(plan.keywords):
        return [row for row in rows if row.get("keyword_match")]
    return [row for row in rows if row.get("category_match")]


def lexical_words(words: Sequence[str]) -> set[str]:
    """Mots distincts de la recherche par mots (sans les synonymes ajoutés)."""

    return {word for word in lexical_query(words).split(" | ") if word}


def core_words(words: Sequence[str]) -> list[str]:
    """Mots de la question, sans les synonymes appris (une alternative, pas un mot de plus)."""

    from app.query_understanding import learned_synonyms

    synonyms = learned_synonyms()
    return [word for word in words if word not in synonyms and word not in synonyms.values()]


def best_match(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Meilleur couple (mots visibles, mots trouvés) parmi les lignes sur le sujet."""

    return max(((int(row.get("visible_hits") or 0), int(row.get("keyword_hits") or 0))
                for row in rows if row.get("keyword_match")), default=(0, 0))


def plan_can_widen(plan: QueryPlan) -> bool:
    """Le plan a des contraintes que `widen_for_subject` peut relâcher."""

    return bool(plan.public or plan.free_only or plan.max_price is not None or plan.evening or plan.moment or plan.setting
                or plan.zone or plan.cities or plan.near or plan.end < plan.start + WIDER_PERIOD)


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
    """

    dropped: list[str] = []
    if plan.public:
        dropped.append(f"pour le public « {PUBLIC_LABELS.get(plan.public, plan.public)} »")
    if plan.free_only or plan.max_price is not None:
        dropped.append("gratuit" if plan.free_only else f"à moins de {plan.max_price:g} €")
    if plan.evening or plan.moment:
        dropped.append(MOMENT_LABELS["soir" if plan.evening else str(plan.moment)])
    if plan.setting:
        dropped.append({"interieur": "en intérieur", "exterieur": "en plein air"}[plan.setting])
    steps: list[tuple[QueryPlan, list[str]]] = []
    if plan.cities and plan.zone and not plan.near:
        # « à Montpellier » : d'abord la métropole, avec toutes les autres
        # contraintes (un spectacle gratuit à Castelnau plutôt qu'un payant).
        steps.append((replace(plan, cities=(), from_question=set()), ["dans la commune même"]))
    steps.append((replace(plan, public=None, free_only=False, max_price=None, evening=False, moment=None,
                          setting=None,
                          from_question=set()), list(dropped)))
    longer = replace(steps[-1][0], end=max(plan.end, plan.start + WIDER_PERIOD), period_label="3 prochains mois")
    steps.append((longer, [*dropped, f"pour « {plan.period_label} »"]))
    if plan.cities and plan.zone and not plan.near:
        # « à Montpellier » : d'abord toute la métropole, avant de lâcher le lieu.
        steps.append((replace(longer, cities=()), [*steps[-1][1], "dans la commune même"]))
    if plan.zone or plan.cities or plan.near:
        steps.append((replace(longer, zone=None, cities=(), near=None), [*steps[-1][1], "à cet endroit"]))
    for wider, relaxed in steps:
        rows = on_subject(fetch_candidates(connection, wider, vector)[0], wider)
        if rows:
            return list(rows), wider, relaxed
    return [], plan, []


def plan_from_filters(filters: SearchFilters, now: datetime, categories: Sequence[str] = ()) -> QueryPlan:
    """Plan de recherche fait des seuls filtres cochés (sans question)."""

    return merge_with_filters(Understanding(categories=tuple(categories)), public=filters.public,
                              zone=filters.zone, period_bounds=period_bounds(filters.period, now),
                              period_label=PERIOD_LABELS[filters.period], free_only=filters.free_only)


def build_search_query(plan: QueryPlan | SearchFilters, vector: str | None, now: datetime | None = None,
                       limit: int = CANDIDATES,
                       categories: Sequence[str] = (), partners_only: bool = False) -> tuple[str, dict[str, Any]]:
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
    """

    if isinstance(plan, SearchFilters):
        plan = plan_from_filters(plan, now or datetime.now(PARIS), categories)
    conditions = ["e.is_outing", "COALESCE(e.end_at, e.start_at) >= %(start)s", "e.start_at < %(end)s"]
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
        conditions.append("(NOT e.time_known OR EXTRACT(HOUR FROM e.start_at AT TIME ZONE 'Europe/Paris') >= 17)")
    elif plan.moment in MOMENT_HOURS:
        first, last = MOMENT_HOURS[plan.moment]
        conditions.append("(NOT e.time_known OR EXTRACT(HOUR FROM e.start_at AT TIME ZONE 'Europe/Paris') "
                          f"BETWEEN {first} AND {last - 1})")

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
    score_sql = " + ".join(score) or "0"
    category_sql = f"{EVENT_CATEGORY} = ANY (%(categories)s)"
    # Le type demandé écrit dans le TITRE ou la catégorie de la source compte
    # aussi (15/10/2026) : « Atelier jeux en famille », rangé en « atelier »,
    # répond à « jeux » ; « Soirée DJ » (catégorie d'un bar) à « soirée ». Pas
    # la description : « après le concert » ne fait pas un concert. Racines
    # exactes, sans préfixe : « jeux » ne doit pas trouver « jeudi ».
    type_words = type_query([*type_keywords(plan.keywords), *plan.categories])
    title_and_category = (f"to_tsvector('french', translate(lower(concat_ws(' ', e.title, e.category)), "
                          f"'{ACCENTS_FROM}', '{ACCENTS_TO}'))")
    if type_words:
        params["type_words"] = type_words
        category_sql = f"({category_sql} OR {title_and_category} @@ to_tsquery('french', %(type_words)s))"
    if plan.watch and plan.categories:
        # Regarder (17/10/2026) : un bal, un cours ou un stage n'est pas du type demandé.
        params["practised"] = PRACTISED
        category_sql = f"({category_sql} AND NOT {title_and_category} @@ to_tsquery('french', %(practised)s))"
    # Mots précis trouvés dans l'événement, comptés un par un (15/10/2026) :
    # « blind test » ne doit pas garder tout ce qui contient « tester » (une
    # dictée, une dégustation). `keep_exact_matches` garde ceux qui en ont le plus.
    # Mots trouvés là où le visiteur les voit (titre, résumé, lieu) : une carte
    # « Concert acoustique » pour « karaoké », parce que la longue description
    # cite un karaoké, ne se comprend pas. Ceux-là passent devant, et les
    # autres ne servent que s'il n'y en a aucun (`keep_exact_matches`).
    visible_text = (f"to_tsvector('french', translate(lower(concat_ws(' ', e.title, e.summary, e.venue_name)), "
                    f"'{ACCENTS_FROM}', '{ACCENTS_TO}'))")
    hits, visible = [], []
    for index, word in enumerate(dict.fromkeys(w for w in lexical_query(distinctive_keywords(plan.keywords))
                                               .split(" | ") if w)):
        params[f"word{index}"] = word
        hits.append(f"(b.document @@ to_tsquery('french', %(word{index})s))::int")
        visible.append(f"({visible_text} @@ to_tsquery('french', %(word{index})s))::int")
    if practice:
        # Pratique : une seule condition (le verbe), au lieu des mots un par un.
        title = (f"to_tsvector('french', translate(lower(e.title), '{ACCENTS_FROM}', '{ACCENTS_TO}'))")
        params.update(practice=practice[0], practice_excluded=list(practice[1]), participation=PARTICIPATION)
        kept = (f"({EVENT_CATEGORY} <> ALL (%(practice_excluded)s) "
                f"OR {title} @@ to_tsquery('french', %(participation)s))")
        hits = [f"(b.document @@ to_tsquery('french', %(practice)s) AND {kept})::int"]
        visible = [f"({visible_text} @@ to_tsquery('french', %(practice)s) AND {kept})::int"]
    hits_sql = " + ".join(hits) or "0"
    visible_sql = " + ".join(visible) or "0"
    sql = (
        f"WITH {', '.join(ctes)}\n"
        f"SELECT {EVENT_COLUMNS}, ({category_sql}) AS category_match, ({hits_sql}) > 0 AS keyword_match,\n"
        f"       ({hits_sql}) AS keyword_hits, ({visible_sql}) AS visible_hits,\n"
        f"       ({score_sql}) AS score\n"
        "FROM base AS b\n"
        "JOIN sortiria.v_upcoming_events AS e ON e.event_id = b.event_id\n"
        "LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id\n"
        + "".join(f"{join}\n" for join in joins)
        + "ORDER BY visible_hits DESC, keyword_hits DESC, category_match DESC, score DESC, e.start_at\n"
        + "LIMIT %(limit)s"
    )
    return sql, params


DETAIL_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.event_id = %(event_id)s"""

# Événement passé depuis moins de 7 jours : plus proposé, mais encore notable
# (bouton « Donner mon avis », 15/10/2026). Mêmes colonnes que la vue.
RECENT_DETAIL_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM (
    SELECT e.*, s.name AS source_name, s.attribution AS source_attribution,
           vr.average_stars AS venue_rating, vr.ratings AS venue_rating_count
    FROM sortiria.events AS e
    JOIN sortiria.sources AS s ON s.source_id = e.source
    LEFT JOIN sortiria.v_venue_ratings AS vr
           ON vr.city_key = sortiria.normalize_city(e.city)
          AND vr.venue_key = lower(btrim(e.venue_name))
) AS e
LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.event_id = %(event_id)s
  AND e.start_at <= now()
  AND COALESCE(e.end_at, e.start_at) >= now() - interval '{RATING_WINDOW_DAYS} days'"""

# Parmi les événements consultés par un visiteur, ceux qu'il peut noter
# maintenant et qu'il n'a pas encore notés (« Vos sorties à noter »).
TO_RATE_SQL = f"""
SELECT e.event_id, e.title, e.start_at, e.end_at, e.time_known, e.venue_name, e.city
FROM sortiria.events AS e
WHERE e.event_id = ANY (%(event_ids)s)
  AND e.start_at <= now()
  AND COALESCE(e.end_at, e.start_at) >= now() - interval '{RATING_WINDOW_DAYS} days'
  AND NOT EXISTS (SELECT 1 FROM sortiria.event_ratings AS r
                  WHERE r.event_id = e.event_id AND r.user_key = %(user_key)s)
ORDER BY e.start_at DESC
LIMIT %(limit)s"""

# Autres dates du même événement (même titre, même lieu, même commune), pour
# le détail. Même normalisation que `occurrence_key` côté Python.
OTHER_DATES_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.is_outing AND e.event_id <> %(event_id)s
  AND lower(regexp_replace(btrim(e.title), '\\s+', ' ', 'g')) = %(title)s
  AND lower(regexp_replace(btrim(COALESCE(e.venue_name, '')), '\\s+', ' ', 'g')) = %(venue)s
  AND lower(regexp_replace(btrim(COALESCE(e.city, '')), '\\s+', ' ', 'g')) = %(city)s
ORDER BY e.start_at
LIMIT %(limit)s"""

# Commentaires publiés sur le lieu de l'événement (tous événements confondus) :
# les notes portent sur le lieu, elles survivent aux événements.
VENUE_COMMENTS_SQL = """
SELECT stars, comment, updated_at, title, event_start
FROM sortiria.event_ratings
WHERE comment_status = 'publie' AND comment <> ''
  AND sortiria.normalize_city(city) = sortiria.normalize_city(%(city)s)
  AND lower(btrim(venue_name)) = lower(btrim(%(venue)s))
  AND %(venue)s <> ''
ORDER BY updated_at DESC
LIMIT %(limit)s"""

# Nombre de notes sur le lieu, même sous le seuil de 3 où la moyenne s'affiche
# (15/10/2026) : « 1 avis » plutôt que « pas encore d'avis » après avoir noté.
VENUE_RATING_COUNT_SQL = """
SELECT count(*) AS ratings
FROM sortiria.event_ratings
WHERE sortiria.normalize_city(city) = sortiria.normalize_city(%(city)s)
  AND lower(btrim(venue_name)) = lower(btrim(%(venue)s))
  AND %(venue)s <> ''"""

# L'avis du visiteur sur cet événement (empreinte anonyme de son navigateur) :
# il le revoit, avec l'état de son commentaire (publié, en relecture, refusé).
OWN_RATING_SQL = """
SELECT stars, comment, comment_status, updated_at
FROM sortiria.event_ratings
WHERE event_id = %(event_id)s AND user_key = %(user_key)s"""

# « À ne pas manquer » : toutes les offres partenaires proposables (3 mois),
# mises en avant d'abord, puis celles des 30 prochains jours, puis les autres.
HIGHLIGHTS_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.is_outing
ORDER BY partner_rank DESC, (e.start_at < now() + interval '30 days') DESC, e.start_at
LIMIT %(pool)s"""

SOURCES_SQL = """
SELECT source_name, count(*) AS events
FROM sortiria.v_upcoming_events
GROUP BY source_name
ORDER BY count(*) DESC"""

# Cache des réponses (bloc 3, 15/10/2026) : une même demande, le même jour,
# sur la même base, reçoit la même réponse sans nouvel appel à Mistral.
# La clé contient la date (« ce soir », « demain » changent de sens chaque
# jour) et le numéro de la dernière collecte (index_version) : une nouvelle
# nuit rend les anciennes réponses inutilisables. Les entrées expirent à
# minuit au plus tard ; la purge de la nuit les supprime (06_retention.sql).
CACHE_HOURS = 6
INDEX_VERSION_SQL = "SELECT COALESCE(max(run_id)::text, '') AS version FROM sortiria.collect_runs"
CACHE_GET_SQL = """
UPDATE sortiria.query_cache SET hits = hits + 1
WHERE cache_key = %(key)s AND expires_at > now()
RETURNING response"""
CACHE_PUT_SQL = """
INSERT INTO sortiria.query_cache
    (cache_key, question_normalized, constraints, response, event_ids, index_version, expires_at)
VALUES (%(key)s, %(question)s, %(constraints)s, %(response)s, %(event_ids)s, %(version)s, %(expires_at)s)
ON CONFLICT (cache_key) DO UPDATE SET
    response = EXCLUDED.response, event_ids = EXCLUDED.event_ids,
    expires_at = EXCLUDED.expires_at, created_at = now()"""


LOG_SQL = """
INSERT INTO sortiria.rag_requests
    (question, constraints, event_ids, status, error, latency_ms,
     prompt_tokens, completion_tokens, cost_usd, llm_model, prompt_version, answer, degraded, from_cache)
VALUES (%(question)s, %(constraints)s, %(event_ids)s, %(status)s, %(error)s, %(latency_ms)s,
        %(prompt_tokens)s, %(completion_tokens)s, %(cost_usd)s, %(llm_model)s, %(prompt_version)s,
        %(answer)s, %(degraded)s, %(from_cache)s)
RETURNING request_id"""


def fetch_rows(connection: Any, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Exécute une requête et renvoie des dictionnaires."""

    with connection.cursor() as cursor:
        cursor.execute(sql, dict(params))
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


# ----------------------------------------------------------------------------
# Mise en forme pour l'interface
# ----------------------------------------------------------------------------

def date_label(start: datetime, time_known: bool = True) -> str:
    """« Sam. 17 oct. · 10:00 » (sans l'heure si elle est inconnue)."""

    local = start.astimezone(PARIS)
    label = f"{DAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]}"
    return f"{label} · {local:%H:%M}" if time_known else label


def event_date_label(row: Mapping[str, Any], now: datetime | None = None) -> str:
    """Date de la carte. Un événement déjà commencé et pas terminé (exposition,
    programme sur plusieurs semaines) affiche « En cours · jusqu'au 19 déc. »
    au lieu de sa date de début passée, qui laissait croire à une erreur."""

    start, end = row["start_at"], row.get("end_at")
    now = now or datetime.now(PARIS)
    if end is not None and start < now <= end and (end - start) > timedelta(days=1):
        local_end = end.astimezone(PARIS)
        return f"En cours · jusqu'au {local_end.day} {MONTHS[local_end.month - 1]}"
    if end is not None and (end - start) > timedelta(days=1):
        # Festival, exposition à venir : « Du 30 oct. au 1 nov. » (un événement
        # du 31 n'apparaît plus comme une erreur de date).
        local_start, local_end = start.astimezone(PARIS), end.astimezone(PARIS)
        return (f"Du {local_start.day} {MONTHS[local_start.month - 1]} "
                f"au {local_end.day} {MONTHS[local_end.month - 1]}")
    return date_label(start, bool(row.get("time_known", True)))


# Un montant décimal dans un texte de prix : « 25.5 », « 7,5 » ; pas une durée
# ni une distance (« 1.5 h », « 2.5 km »), pas un nombre à séparateurs (« 1.500 »).
DECIMAL_AMOUNT = re.compile(r"(?<![\d.,])(\d+)[.,](\d{1,2})(?![\d.,])(?!\s*(?:h\b|min\b|km\b|kg\b|m\b|ans?\b|%))")
MONEY = re.compile(r"€|\beur(?:os?)?\b", re.IGNORECASE)


def french_amount(value: float) -> str:
    """12.5 -> « 12,50 » ; 12.0 -> « 12 » (16/10/2026 : « 25.5 € » s'affichait tel quel)."""

    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.2f}".replace(".", ",")


def french_prices(text: str) -> str:
    """Montants d'un texte de prix au format français : « De 5.5 à 8.5 € » ->
    « De 5,50 à 8,50 € ». Seulement dans un texte qui parle d'euros."""

    if not MONEY.search(text):
        return text
    return DECIMAL_AMOUNT.sub(lambda m: f"{m.group(1)},{m.group(2):0<2}", text)


def price_label(row: Mapping[str, Any]) -> str:
    """Prix affiché sur la carte."""

    if row.get("is_free"):
        return "Gratuit"
    text = french_prices(str(row.get("price_text") or "").strip())
    if text:
        return text if len(text) <= 40 else text[:39].rstrip() + "…"
    if row.get("price_min") is not None:
        return f"dès {french_amount(float(row['price_min']))} €"
    return "Prix non communiqué"


def place_label(row: Mapping[str, Any]) -> str:
    """Lieu affiché : « Salle · Ville », ou la ville seule quand le lieu porte
    son nom (16/10/2026 : « Montpellier · Montpellier »)."""

    venue = str(row.get("venue_name") or "").strip()
    city = str(row.get("city") or "").strip()
    if venue and city and venue.casefold() == city.casefold():
        return city
    return " · ".join(part for part in (venue, city) if part)


def short_summary(row: Mapping[str, Any]) -> str:
    """Le résumé d'enrichissement, sinon le début de la description."""

    text = str(row.get("summary") or "").strip() or " ".join(str(row.get("description") or "").split())
    if len(text) <= SUMMARY_CHARS:
        return text
    return text[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"


def squash(value: Any) -> str:
    """Minuscules, espaces réduits (même règle que OTHER_DATES_SQL)."""

    return " ".join(str(value or "").split()).lower()


def occurrence_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Un même événement à plusieurs dates : même titre, même lieu, même commune."""

    return squash(row.get("title")), squash(row.get("venue_name")), squash(row.get("city"))


MAX_PER_VENUE = 2          # diversité : pas plus de 2 propositions au même lieu
PARTNER_TOP_RANK = 40      # sans type ni mot précis : une offre partenaire doit être parmi les 40 plus proches


# Un titre qui nomme un public en exclut d'autres : « Initiation escalade
# enfants » ne passe pas en tête pour « un truc à faire avec des ados »
# (grille de pertinence du 16/10/2026).
YOUNG_WORDS = ("enfant", "enfants", "tout petit", "tout petits", "bebe", "bebes", "maternelle", "3 6 ans")
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
    return not secondary or any(f" {plain(CATEGORY_LABELS.get(item, item))} " in title for item in main)


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
    """

    main = main_categories(question) if question else ()
    if not exact:
        # Idées voisines : une offre partenaire qui parle du sujet (mot ou type
        # demandé) reste dans les cartes, en tête ; les autres en sortent.
        on_topic = [{**row, "partner_ahead": True} for row in rows if row.get("partner_id") is not None
                    and (row.get("keyword_match") or row.get("category_match")) and partner_fits(row, plan, main)]
        return on_topic + [row for row in rows if row.get("partner_id") is None]
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


def group_occurrences(rows: Sequence[Mapping[str, Any]], now: datetime,
                      limit: int = MAX_PROPOSALS) -> list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]:
    """Regroupe les séances d'un même événement, puis choisit les propositions.

    L'ordre des groupes est celui de leur meilleure séance (la plus proche de
    la question). Dans un groupe, la séance montrée est la prochaine à venir
    (une séance déjà commencée ne passe qu'ensuite) ; les autres sont
    renvoyées triées par date.

    Diversité (15/10/2026) : pas plus de 2 propositions au même lieu tant
    qu'il y a d'autres lieux à proposer ; six ateliers de la même
    médiathèque laissaient peu de choix.
    """

    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(occurrence_key(row), []).append(row)
    chosen: list[tuple[str, str, str]] = []
    waiting: list[tuple[str, str, str]] = []
    per_venue: dict[tuple[str, str], int] = {}
    for key in groups:
        venue = (key[1], key[2])
        if key[1] and per_venue.get(venue, 0) >= MAX_PER_VENUE:
            waiting.append(key)
            continue
        per_venue[venue] = per_venue.get(venue, 0) + 1
        chosen.append(key)
        if len(chosen) == limit:
            break
    chosen += waiting[:limit - len(chosen)]            # pas assez d'autres lieux : on complète
    result = []
    for key in chosen:
        ordered = sorted(groups[key], key=lambda r: (r["start_at"] < now, r["start_at"]))
        main, others = ordered[0], sorted(ordered[1:], key=lambda r: r["start_at"])
        result.append((main, others[:MAX_OTHER_DATES]))
    return result


def sibling_rows(connection: Any, row: Mapping[str, Any], limit: int = 60) -> list[dict[str, Any]]:
    """Toutes les autres séances à venir du même événement (même titre, lieu, commune)."""

    return fetch_rows(connection, OTHER_DATES_SQL, {
        "event_id": str(row["event_id"]), "title": squash(row.get("title")), "venue": squash(row.get("venue_name")),
        "city": squash(row.get("city")), "limit": limit})


def complete_occurrences(connection: Any, row: Mapping[str, Any], plan: QueryPlan,
                         now: datetime, found: Sequence[Mapping[str, Any]] = ()
                         ) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Séance montrée et autres dates d'une carte, d'après TOUTES les séances à venir.

    Correction du 15/10/2026 : la carte ne comptait que les séances trouvées par
    la recherche (« + 1 date » quand le détail en listait 3), et pouvait montrer
    le 12 décembre alors qu'une séance avait lieu le 17 octobre. Maintenant :
    la séance montrée est la prochaine dans la période demandée, et toutes les
    autres sont comptées, comme dans le détail.

    Correction du soir (évaluation des 100 recherches) : la séance montrée est
    choisie parmi celles que la recherche a **trouvées** (`row` et `found`),
    qui respectent toutes les contraintes. Chaque séance a son propre
    enrichissement : pour « Halloween », la carte montrait la séance du 24/10
    (sans Halloween) au lieu de celle du 31/10 ; pour « ados », une séance
    sans le public ados.
    """

    everyone = [row, *sibling_rows(connection, row)]
    matching = [row, *found]
    in_period = [r for r in matching if r["start_at"] < plan.end and (r.get("end_at") or r["start_at"]) >= plan.start]
    main = min(in_period or [row], key=lambda r: (r["start_at"] < now, r["start_at"]))
    others = sorted((r for r in everyone if r["event_id"] != main["event_id"]), key=lambda r: r["start_at"])
    return main, others[:MAX_OTHER_DATES]


def other_dates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Autres dates d'un événement, telles que l'interface les affiche."""

    return [{"event_id": str(row["event_id"]), "date": event_date_label(row)} for row in rows]


def category_display(row: Mapping[str, Any]) -> str:
    """Type de sortie tel qu'on l'écrit : « soirée », « théâtre », « marché »."""

    category = str(row.get("category_norm") or row.get("category") or "").strip()
    return CATEGORY_LABELS.get(category.lower(), category)


DEMO_HOSTS = ("example.org", "example.com", "example.net")


def is_demo_link(url: str) -> bool:
    """Lien d'une offre de démonstration (partenaires fictifs : example.org)."""

    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return any(host == demo or host.endswith(f".{demo}") for demo in DEMO_HOSTS)


def to_card(row: Mapping[str, Any], ref: int | None = None) -> dict[str, Any]:
    """Un événement tel que l'interface l'affiche (carte et détail)."""

    place = place_label(row)
    rating = row.get("venue_rating")
    return {
        "ref": ref,
        "event_id": str(row["event_id"]),
        "title": str(row["title"]),
        "date": event_date_label(row),
        "start_at": row["start_at"].isoformat(),
        "end_at": row["end_at"].isoformat() if row.get("end_at") else None,
        "place": place,
        "city": str(row.get("city") or "").strip(),
        "zone": str(row.get("zone") or ""),
        "category": str(row.get("category_norm") or row.get("category") or "").strip(),
        # Libellé affiché (« soirée », « théâtre ») ; la catégorie brute sert aux calculs.
        "category_label": category_display(row),
        "price": price_label(row),
        "price_min": float(row["price_min"]) if row.get("price_min") is not None else None,
        # Tous les publics (filtres, évaluation) ; l'interface ne répète pas
        # celui qui dit la même chose que le type (« soirée »).
        "audiences": [PUBLIC_LABELS.get(item, item) for item in row.get("audiences") or []],
        "summary": short_summary(row),
        "url": str(row.get("url") or ""),
        "source": str(row.get("source_name") or ""),
        "source_attribution": str(row.get("source_attribution") or ""),
        "partner": row.get("partner_id") is not None,
        # Passée en tête parce qu'elle répond au type et au public demandés.
        "partner_ahead": bool(row.get("partner_ahead")),
        "partner_name": str(row.get("partner_name") or ""),
        # Offre de démonstration (18/10/2026) : partenaire fictif, lien example.org.
        # L'interface l'annonce comme telle et n'envoie pas vers le faux lien.
        "demo": is_demo_link(str(row.get("url") or "")),
        "venue_rating": float(rating) if rating is not None else None,
        "venue_rating_count": int(row.get("venue_rating_count") or 0),
        "other_dates": [],
    }


def event_detail(connection: Any, event_id: str, comments: int = 3,
                 visitor_key: str | None = None) -> dict[str, Any] | None:
    """Détail d'un événement proposable, avec les derniers avis publiés sur son
    lieu, leur nombre, et l'avis du visiteur (`visitor_key` : empreinte de son
    navigateur) s'il en a donné un."""

    rows = fetch_rows(connection, DETAIL_SQL, {"event_id": event_id}) or \
        fetch_rows(connection, RECENT_DETAIL_SQL, {"event_id": event_id})
    if not rows:
        return None
    row = rows[0]
    card = to_card(row)
    card.update(rating_window(row))
    card["other_dates"] = other_dates(sibling_rows(connection, row, MAX_OTHER_DATES))
    card["description"] = " ".join(str(row.get("description") or "").split())[:1200]
    card["comments"] = [
        {"stars": int(item["stars"]), "text": str(item["comment"]),
         "date": item["updated_at"].astimezone(PARIS).strftime("%d/%m/%Y"),
         # L'événement noté : l'avis garde son contexte après la suppression de l'événement.
         "event_title": str(item.get("title") or ""),
         "event_date": date_label(item["event_start"], True) if item.get("event_start") else ""}
        for item in fetch_rows(connection, VENUE_COMMENTS_SQL, {
            "city": row.get("city") or "", "venue": row.get("venue_name") or "", "limit": comments})
    ]
    venue = {"city": row.get("city") or "", "venue": row.get("venue_name") or ""}
    counted = fetch_rows(connection, VENUE_RATING_COUNT_SQL, venue)
    card["venue_ratings"] = int(counted[0]["ratings"]) if counted else 0
    card["my_rating"] = None
    if visitor_key:
        own = fetch_rows(connection, OWN_RATING_SQL, {"event_id": str(row["event_id"]), "user_key": visitor_key})
        if own:
            card["my_rating"] = {"stars": int(own[0]["stars"]), "comment": str(own[0]["comment"] or ""),
                                 "comment_status": str(own[0]["comment_status"]),
                                 "date": own[0]["updated_at"].astimezone(PARIS).strftime("%d/%m/%Y")}
    return card


def rating_window(row: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Peut-on noter cet événement maintenant ? Sinon, à partir de quand.

    Même règle que la base (UPSERT_RATING_SQL) : du début de l'événement à
    7 jours après sa fin.
    """

    now = now or datetime.now(PARIS)
    start = row["start_at"]
    last = (row.get("end_at") or start) + timedelta(days=RATING_WINDOW_DAYS)
    return {"can_rate": start <= now <= last,
            "rate_from": date_label(start, False) if start > now else ""}


def events_to_rate(connection: Any, event_ids: Sequence[str], session_id: str,
                   limit: int = 10) -> list[dict[str, Any]]:
    """Événements consultés par ce visiteur qu'il peut noter maintenant."""

    from src.event.event_ratings import user_key

    ids = [str(event_id) for event_id in event_ids][:50]
    if not ids:
        return []
    rows = fetch_rows(connection, TO_RATE_SQL, {"event_ids": ids, "user_key": user_key(session_id),
                                                "limit": limit})
    return [{"event_id": str(row["event_id"]), "title": str(row["title"]), "date": event_date_label(row),
             "place": place_label(row)}
            for row in rows]


MAX_PARTNER_OFFERS = 3


def build_partner_query(plan: QueryPlan, vector: str | None, exclude: Sequence[str], *,
                        keep_place: bool = True, limit: int = MAX_PARTNER_OFFERS) -> tuple[str, dict[str, Any]]:
    """Offres des partenaires les plus proches de la demande.

    Même période que la demande (et même lieu si `keep_place`), seulement les
    événements d'un partenaire ; classées par proximité de sens avec la
    question (mises en avant d'abord à égalité), sinon par date.
    """

    conditions = ["e.is_outing", "e.partner_id IS NOT NULL", "COALESCE(e.end_at, e.start_at) >= %(start)s",
                  "e.start_at < %(end)s", "NOT (e.event_id = ANY (%(exclude)s))"]
    params: dict[str, Any] = {"start": plan.start, "end": plan.end, "exclude": list(exclude), "limit": limit}
    if keep_place and plan.near:
        conditions.append(NEAR_CONDITION)
        params["near_city"], params["radius"] = plan.near
    elif keep_place and plan.cities:
        conditions.append("sortiria.normalize_city(e.city) = ANY (%(cities)s)")
        params["cities"] = list(plan.cities)
    elif keep_place and plan.zone:
        conditions.append("sortiria.event_zone(e.latitude, e.longitude, e.city) = %(zone)s")
        params["zone"] = ZONES[plan.zone]
    order = "partner_rank DESC, e.start_at"
    if vector:
        params["vector"] = vector
        order = "e.embedding <=> %(vector)s::vector NULLS LAST, partner_rank DESC, e.start_at"
    sql = (f"SELECT {EVENT_COLUMNS}\nFROM sortiria.v_upcoming_events AS e\n"
           "JOIN sortiria.partners AS p ON p.partner_id = e.partner_id\n"
           f"WHERE {' AND '.join(conditions)}\nORDER BY {order}\nLIMIT %(limit)s")
    return sql, params


def partner_offers(connection: Any, plan: QueryPlan, vector: str | None,
                   exclude: Sequence[str]) -> list[dict[str, Any]]:
    """Quand rien ne répond exactement : les offres partenaires les plus proches,
    au même endroit d'abord, puis n'importe où dans la zone couverte."""

    rows = fetch_rows(connection, *build_partner_query(plan, vector, exclude))
    if not rows and (plan.cities or plan.zone or plan.near):
        rows = fetch_rows(connection, *build_partner_query(plan, vector, exclude, keep_place=False))
    offers, seen = [], set()
    for row in rows:
        key = occurrence_key(row)                       # une offre répétée ne compte qu'une fois
        if key not in seen:
            seen.add(key)
            offers.append(to_card(row))
    return offers


def partner_highlights(connection: Any, limit: int = 3) -> list[dict[str, Any]]:
    """« À ne pas manquer » : les offres des partenaires à mettre en avant.

    Mises en avant d'abord, puis les plus proches dans le temps ; une offre par
    partenaire tant qu'il y a d'autres partenaires (chacun a sa place), et une
    offre répétée sur plusieurs dates ne compte qu'une fois.
    """

    rows = fetch_rows(connection, HIGHLIGHTS_SQL, {"pool": max(limit * 10, 30)})
    first, rest, seen = [], [], set()
    partners_shown: set[Any] = set()
    for row in rows:
        key = occurrence_key(row)
        if key in seen:
            continue
        seen.add(key)
        if row.get("partner_id") in partners_shown:
            rest.append(row)
        else:
            partners_shown.add(row.get("partner_id"))
            first.append(row)
    return [to_card(row) for row in (first + rest)[:limit]]


def active_sources(connection: Any) -> list[dict[str, Any]]:
    """Sources qui ont au moins un événement proposable (en-tête de l'interface)."""

    return [{"name": str(row["source_name"]), "events": int(row["events"])}
            for row in fetch_rows(connection, SOURCES_SQL, {})]


# Santé du service (16/10/2026) : lue par la surveillance GitHub
# (.github/workflows/surveillance.yml) via /api/v1/sante. Aucune donnée
# sensible : un statut et un âge en heures.
COLLECT_HEALTH_SQL = """
SELECT
    (SELECT status FROM sortiria.collect_runs ORDER BY started_at DESC LIMIT 1) AS statut,
    (SELECT round((EXTRACT(EPOCH FROM now() - max(started_at)) / 3600)::NUMERIC, 1)
       FROM sortiria.collect_runs WHERE status NOT IN ('failed', 'running')) AS heures
"""


def collect_health(connection: Any) -> dict[str, Any]:
    """Statut de la dernière collecte et heures depuis la dernière réussie."""

    row = fetch_rows(connection, COLLECT_HEALTH_SQL, {})[0]
    hours = row["heures"]
    return {"statut": row["statut"], "heures_depuis_reussite": None if hours is None else float(hours)}


# ----------------------------------------------------------------------------
# Rédaction
# ----------------------------------------------------------------------------

CITATION = re.compile(r"\[(\d+)\]")


def events_for_prompt(cards: Sequence[Mapping[str, Any]]) -> str:
    """Liste numérotée donnée à Mistral (seules informations qu'il peut utiliser)."""

    lines = []
    for card in cards:
        details = [card["date"], card["place"], card["category"], card["price"]]
        if card.get("partner_ahead", card.get("partner")):
            details.append("offre partenaire")
        if card["audiences"]:
            details.append("public : " + ", ".join(card["audiences"]))
        if card.get("other_dates"):
            details.append("autres dates : " + ", ".join(d["date"] for d in card["other_dates"][:4]))
        lines.append(f"[{card['ref']}] {card['title']} — {' ; '.join(d for d in details if d)}\n"
                     f"    {card['summary']}")
    return "\n".join(lines)


def parse_answer(raw: str, count: int) -> tuple[str, list[str]]:
    """Réponse et suggestions lues dans le JSON de Mistral.

    Une citation qui ne renvoie à aucun événement fourni ([7] quand il y en a
    4) est retirée : on n'affiche jamais une source qui n'existe pas.
    """

    data = json.loads(raw)
    answer = str(data.get("reponse") or "").strip()
    if not answer:
        raise ValueError("réponse vide")
    answer = CITATION.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= count else "", answer)
    answer = french_spacing(re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r" {2,}", " ", answer)).strip())
    suggestions = clean_suggestions(data.get("suggestions") or [])
    return answer, suggestions


# Typographie française (16/10/2026) : espace insécable avant « : ; ! ? »
# (« idées proches: » s'affichait collé). Pas dans une heure (« 20:30 »).
# Le signe doit être suivi d'une espace, d'une fin ou d'un guillemet : « 20:30 »
# et « https:// » ne bougent pas, mais « le 31: » devient « le 31 : ».
HIGH_PUNCTUATION = re.compile(r"(?:(?<=[^\s\u00a0])| +)([:;!?])(?=\s|$|[»)\]])")


def french_spacing(text: str) -> str:
    return HIGH_PUNCTUATION.sub(lambda m: "\u00a0" + m.group(1), text)


MAX_SUGGESTION_WORDS = 8


def clean_suggestions(items: Sequence[Any]) -> list[str]:
    """Relances gardées : des questions courtes que le visiteur pourrait poser.

    16/10/2026 : Mistral y mettait parfois des phrases sur les cartes
    (« Ce concert familial a lieu ce soir… [2] »), qui se contredisaient.
    Une relance trop longue, sans point d'interrogation ou citant une carte
    est écartée ; s'il n'en reste aucune, les relances par défaut servent.

    17/10/2026 : une relance est un bouton, donc un seul choix. « En intérieur
    ou en extérieur ? » est séparée en « En intérieur ? » et « En extérieur ? ».
    """

    kept: list[str] = []
    for item in (part for raw in items for part in single_choices(str(raw))):
        text = french_spacing(" ".join(item.split()))
        if (text.endswith("?") and len(text.split()) <= MAX_SUGGESTION_WORDS
                and not CITATION.search(text) and text not in kept):
            kept.append(text)
    return kept[:3]


CHOICE = re.compile(r"\s+ou\s+", re.IGNORECASE)   # « ou » (choix), pas « où » (lieu)
LEAD_WORDS = {"plutot", "et", "seulement", "juste", "uniquement", "aussi"}


def single_choices(text: str) -> list[str]:
    """« En intérieur ou en extérieur ? » → « En intérieur ? », « En extérieur ? ».

    Seule une relance faite de deux choix courts est séparée ; si un morceau
    est trop long pour être un choix, la relance est gardée telle quelle et
    `clean_suggestions` la jugera.
    """

    text = " ".join(text.split())
    if not text.endswith("?"):
        return [text]
    parts = CHOICE.split(text.rstrip(" ?\u00a0"))
    if len(parts) != 2 or not all(0 < len(part.split()) <= 4 for part in parts):
        return [text]
    first, second = (part.strip(" ,") for part in parts)
    # « Plutôt samedi ou dimanche ? » → « Plutôt samedi ? », « Plutôt dimanche ? » :
    # le mot d'appel passe aussi sur le second choix (17/10/2026).
    lead = first.split()[0]
    if len(first.split()) > 1 and plain(lead) in LEAD_WORDS and plain(second.split()[0]) not in LEAD_WORDS:
        second = f"{lead} {second[:1].lower()}{second[1:]}"
    return [f"{first} ?", f"{second[:1].upper()}{second[1:]} ?"]


def order_cards(cards: list[dict[str, Any]], answer: str) -> tuple[list[dict[str, Any]], str]:
    """Ordre des cartes affichées (15/10/2026) : d'abord les offres partenaires,
    puis les événements que la réponse cite (dans l'ordre où elle les cite),
    puis les autres. Les numéros sont refaits de 1 à n, et les citations de
    la réponse ([5] → [2]) suivent, pour que [n] désigne toujours la n-ième carte.
    """

    cited: list[int] = []
    for match in CITATION.finditer(answer):
        ref = int(match.group(1))
        if 1 <= ref <= len(cards) and ref not in cited:
            cited.append(ref)
    partners = [ref for ref in range(1, len(cards) + 1)
                if cards[ref - 1].get("partner_ahead", cards[ref - 1].get("partner"))]
    order = partners + [ref for ref in cited if ref not in partners]
    order += [ref for ref in range(1, len(cards) + 1) if ref not in order]
    new_ref = {old: new for new, old in enumerate(order, start=1)}
    ordered = []
    for old in order:
        card = dict(cards[old - 1])
        card["ref"] = new_ref[old]
        ordered.append(card)
    answer = CITATION.sub(lambda m: f"[{new_ref.get(int(m.group(1)), int(m.group(1)))}]", answer)
    return ordered, answer


def fallback_answer(cards: Sequence[Mapping[str, Any]], filters: QueryPlan | SearchFilters) -> tuple[str, list[str]]:
    """Réponse sans Mistral (service indisponible) : simple et toujours exacte."""

    if not cards:
        message = ("Je n'ai trouvé aucun événement qui corresponde pour "
                   f"{filters.describe()}. Essayez d'élargir la période ou la zone.")
        return message, ["Sur 3 mois ?", "Partout ?"]
    names = ", ".join(f"{card['title']} [{card['ref']}]" for card in cards[:3])
    return f"Voici ce que j'ai trouvé pour {filters.describe()} : {names}.", []


def default_suggestions(filters: QueryPlan | SearchFilters) -> list[str]:
    """Relances proposées si Mistral n'en donne pas."""

    evening = isinstance(filters, QueryPlan) and filters.evening
    suggestions = ["Et s'il pleut ?", "Plutôt en journée ?" if evening else "Plutôt le soir ?"]
    suggestions.append("Seulement gratuit ?" if not filters.free_only else "Sur 3 mois ?")
    return suggestions


def write_with_mistral(question: str, filters: QueryPlan | SearchFilters,
                       cards: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, int], str]:
    """Appel de rédaction. Renvoie (JSON brut, tokens, modèle)."""

    from src.event.event_enrichment import call_mistral_json

    model = os.getenv("SEARCH_MODEL", DEFAULT_SEARCH_MODEL)
    text = (f"Demande : {question}\nContraintes comprises : {filters.describe()}\n\n"
            f"Événements trouvés :\n{events_for_prompt(cards)}")
    raw, usage = call_mistral_json(WRITE_PROMPT, text, model)
    return raw, usage, model


def embed_question(question: str) -> str:
    """Vecteur de la question au format pgvector."""

    from agent.services import embed_with_mistral
    from src.event.event_embedding import to_pgvector

    return to_pgvector(embed_with_mistral([question])[0])


# ----------------------------------------------------------------------------
# Recherche complète, étape par étape
# ----------------------------------------------------------------------------

@dataclass(slots=True)
class SearchServices:
    """Ce dont la recherche a besoin (remplacé par des faux dans les tests)."""

    embed: Callable[[str], str] | None = embed_question
    write: Callable[..., tuple[str, dict[str, int], str]] | None = write_with_mistral
    now: Callable[[], datetime] = lambda: datetime.now(PARIS)
    # False pour l'évaluation (scripts/eval_recherche.py) : ses questions ne
    # doivent pas compter dans le suivi des vraies demandes (rag_requests).
    log: bool = True
    # False pour l'évaluation : elle doit mesurer la recherche, pas le cache.
    cache: bool = True


@dataclass(slots=True)
class SearchResult:
    """Résultat final envoyé à l'interface."""

    answer: str
    events: list[dict[str, Any]]
    suggestions: list[str]
    filters: str
    degraded: bool = False
    request_id: int | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    # Demande complète comprise (question + relances) : l'interface la renvoie
    # avec la question suivante pour garder le fil de la conversation.
    context: str = ""
    # True quand les propositions répondent exactement (type, mots, contraintes) ;
    # False : idées voisines, ce que la réponse dit d'abord.
    exact: bool = True
    # Pas de réponse exacte : les offres de nos partenaires les plus proches
    # de la demande (affichées à part, dans un encadré « partenaire »).
    partner_offers: list[dict[str, Any]] = field(default_factory=list)
    # Cases à cocher selon la question (17/10/2026) : « avec les enfants »
    # coche « Famille », « à Sète » coche « Thau ». Seules les cases que la
    # question a fixées sont présentes.
    checked: dict[str, Any] = field(default_factory=dict)
    # Ce qui a manqué (15/10/2026), gardé dans le journal pour le bilan
    # hebdomadaire de l'agent : mots écrits dans aucun titre de la base,
    # contraintes relâchées, mots absents des propositions, sujet introuvable.
    gaps: dict[str, Any] = field(default_factory=dict)


def progress(step: str, status: str, message: str = "") -> dict[str, Any]:
    """Un événement de progression (« event: progress » du flux)."""

    return {"event": "progress", "step": step, "status": status, "message": message}


def cache_key(question: str, context: str, filters: SearchFilters, now: datetime, version: str,
              follow_up: bool = False) -> str:
    """Empreinte de la demande : question et contexte normalisés, cases cochées,
    jour, version du prompt et de la base, synonymes appris (un synonyme
    validé sert dès la question suivante, sans attendre la fin du cache)."""

    import hashlib

    from app.query_understanding import learned_synonyms

    parts = [PROMPT_VERSION, version, now.astimezone(PARIS).date().isoformat(), plain(question), plain(context),
             json.dumps(asdict(filters), sort_keys=True), json.dumps(learned_synonyms(), sort_keys=True)]
    if follow_up:
        parts.append("relance")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def cache_expiry(now: datetime) -> datetime:
    """Fin de validité : 6 h plus tard, et jamais après minuit (heure de Paris)."""

    local = now.astimezone(PARIS)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return min(local + timedelta(hours=CACHE_HOURS), midnight)


def read_cache(connection: Any, key: str) -> dict[str, Any] | None:
    """Réponse gardée pour cette demande, ou None. Jamais bloquant."""

    try:
        rows = fetch_rows(connection, CACHE_GET_SQL, {"key": key})
        connection.commit()
    except Exception:  # noqa: BLE001 (sans cache, la recherche se fait normalement)
        connection.rollback()
        return None
    response = rows[0].get("response") if rows else None
    if not response:
        return None
    try:
        cached = json.loads(response) if isinstance(response, str) else dict(response)
    except (TypeError, ValueError):
        return None
    return cached if "answer" in cached and "events" in cached else None


def write_cache(connection: Any, key: str, question: str, plan: QueryPlan, result: SearchResult,
                version: str, now: datetime) -> None:
    """Garde une réponse complète et sourcée. Jamais bloquant."""

    try:
        fetch_rows(connection, CACHE_PUT_SQL + " RETURNING cache_key", {
            "key": key, "question": plain(question),
            "constraints": json.dumps(plan_summary(plan), ensure_ascii=False),
            "response": json.dumps(asdict(result), ensure_ascii=False, default=str),
            "event_ids": [card["event_id"] for card in result.events],
            "version": version, "expires_at": cache_expiry(now),
        })
        connection.commit()
    except Exception:  # noqa: BLE001
        connection.rollback()


def index_version(connection: Any) -> str:
    """Numéro de la dernière collecte : change chaque nuit."""

    try:
        rows = fetch_rows(connection, INDEX_VERSION_SQL, {})
        return str(rows[0]["version"]) if rows else ""
    except Exception:  # noqa: BLE001
        connection.rollback()
        return ""


def checked_by_question(plan: QueryPlan) -> dict[str, Any]:
    """Cases que l'interface doit cocher pour refléter la question."""

    checked: dict[str, Any] = {}
    # « soiree » n'a plus de bouton (18/10/2026) : compris, mais rien à cocher.
    if "public" in plan.from_question and plan.public and plan.public != "soiree":
        checked["public"] = plan.public
    if plan.period_from_question:
        checked["date"] = plan.period_label
    places = plan.from_question & {"cities", "zone", "near"}
    if places:
        if plan.near:
            checked["zone"] = zone_of_city(plan.near[0])
        elif plan.cities and len({zone_of_city(city) for city in plan.cities}) == 1:
            checked["zone"] = zone_of_city(plan.cities[0])
        elif plan.zone:
            checked["zone"] = plan.zone
    return checked


def plan_search(question: str, filters: SearchFilters, now: datetime,
                context: str = "", follow_up: bool = False) -> tuple[QueryPlan, str]:
    """Plan de recherche et texte à vectoriser.

    `context` est la question précédente de la conversation : pour une relance
    (« et pour les enfants ? »), ses contraintes sont gardées et complétées, et
    le sens cherché est celui des deux questions réunies.

    `follow_up` : le visiteur a cliqué une relance proposée sous la réponse
    (17/10/2026). C'est toujours une suite, même longue ou avec un sujet
    (« Des spectacles en intérieur ? ») : la demande précédente est gardée.

    Cases cochées (17/10/2026) : un public ou un lieu écrit dans la question
    qu'on vient de poser l'emporte sur la case cochée (« avec les enfants »
    avec « Seniors » coché cherche pour les familles, et l'interface coche
    « Famille ») ; ce qui vient seulement de la question précédente cède à la
    case : cocher, c'est vouloir changer.
    """

    current = understand(question, now)
    public, zone = filters.public, filters.zone
    if current.public:
        public = None
    if current.zone or current.cities or current.near:
        zone = None
    embed_text = question
    if context and (follow_up or is_follow_up(question)):
        current = combine(understand(context, now), current)
        embed_text = f"{context} {question}"
    plan = merge_with_filters(current, public=public, zone=zone,
                              period_bounds=period_bounds(filters.period, now),
                              period_label=PERIOD_LABELS[filters.period], free_only=filters.free_only)
    return plan, embed_text


def run_search(connection: Any, question: str, filters: SearchFilters,
               services: SearchServices | None = None, context: str = "",
               follow_up: bool = False) -> Iterator[dict[str, Any]]:
    """Recherche complète. Produit les étapes une à une, puis le résultat.

    Dernier élément : {"event": "completed", "result": {...}}. Une panne de
    la base lève l'exception (l'appelant envoie « event: error »).
    """

    services = services or SearchServices()
    started = time.monotonic()
    question = " ".join(question.split())[:500]
    context = " ".join(context.split())[:500]
    now = services.now()
    degraded = False

    yield progress(STEP_UNDERSTAND, "running")
    plan, embed_text = plan_search(question, filters, now, context, follow_up)
    yield progress(STEP_UNDERSTAND, "completed", plan.describe())

    # Même demande, même jour, même base : la réponse déjà rédigée, sans Mistral.
    key = version = ""
    if services.cache and question:
        version = index_version(connection)
        key = cache_key(question, context, filters, now, version, follow_up)
        cached = read_cache(connection, key)
        if cached is not None:
            for step in (STEP_SEARCH, STEP_FILTER, STEP_WRITE):
                yield progress(step, "completed", "réponse déjà préparée aujourd'hui (cache)")
            cached["request_id"] = None
            if services.log:
                result = SearchResult(**{name: cached[name] for name in SearchResult.__dataclass_fields__
                                         if name in cached})
                cached["request_id"] = log_request(connection, question, filters, result, "", int(
                    (time.monotonic() - started) * 1000), plan=plan, from_cache=True)
            yield {"event": "completed", "result": cached}
            return

    yield progress(STEP_SEARCH, "running")
    vector = None
    if embed_text and services.embed is not None:
        try:
            # Pratique : le sens va vers ce qui permet de la faire (pas vers les concerts).
            hint = f" ({PRACTICES[plan.practice][2]})" if plan.practice in PRACTICES else ""
            vector = services.embed(embed_text + hint)
        except Exception:  # noqa: BLE001 (sans vecteur, mots-clés et date : la recherche continue)
            degraded = True
    candidates, params = fetch_candidates(connection, plan, vector)
    relaxed = ""
    if not candidates and plan.from_question:
        # Les contraintes lues dans la question ne laissent rien : on les
        # relâche (sauf la date) et Mistral le dira.
        relaxed = plan.describe()
        plan = plan.relaxed()
        candidates, params = fetch_candidates(connection, plan, vector)
    how = " + ".join(part for part, used in (("sens", vector), ("mots-clés", params.get("lexical"))) if used)
    found = f"{len(candidates)} séances ({how or 'triées par date'})"
    if relaxed:
        found += f", rien pour « {relaxed} » : recherche élargie"
    yield progress(STEP_SEARCH, "completed", found)

    yield progress(STEP_FILTER, "running")
    # Seulement ce qui répond exactement (mots précis, type demandé), quand il y
    # en a : 2 concerts à Mèze valent mieux que 2 concerts et 4 autres choses.
    candidates, words_found, type_found = keep_exact_matches(candidates)
    words = distinctive_keywords(plan.keywords)
    has_subject = bool(words) or bool(plan.categories)
    # Sujet net : un type de sortie (« concert »), un genre (« cirque »,
    # « jazz ») ou un mot qui figure dans des titres de la base (« photo »,
    # « poterie ») ; un mot écrit dans aucun titre (« romantique ») est une
    # ambiance, que seul le sens retrouve. Le vocabulaire n'est lu que si les
    # mots manquent.
    unknown_words: list[str] = []
    precise_words: list[str] = []
    if words and not words_found:
        known = known_subjects(connection, words)
        precise_words = [word for word in words if word in known or word in PRECISE_SUBJECTS
                         or word.rstrip("sx") in PRECISE_SUBJECTS]
        unknown_words = [word for word in words if word not in precise_words]
    clear_subject = bool(plan.categories) or bool(precise_words)
    widened: list[str] = []
    nothing_on_subject = False
    asked_plan = plan
    # Le sujet manque avec ces contraintes (aucun mot ni type trouvé, ou un
    # genre précis absent : des concerts mais pas de jazz) : on le cherche
    # ailleurs (sans gratuit, sur 3 mois…) plutôt que des idées sans rapport.
    if has_subject and not words_found and (not type_found or precise_words):
        wider_rows, wider_plan, dropped = widen_for_subject(connection, plan, vector)
        if wider_rows:
            candidates, plan, widened = wider_rows, wider_plan, dropped
        elif not type_found and clear_subject:
            candidates, nothing_on_subject = [], True        # le sujet n'existe nulle part
        # Sinon : les concerts trouvés (sans jazz), ou les idées voisines par le
        # sens pour un mot d'ambiance ; Mistral dit ce qui manque.
        words_found = any(row.get("keyword_match") for row in candidates)
        type_found = any(row.get("category_match") for row in candidates)
    elif words_found and not relaxed and plan_can_widen(plan) \
            and best_match(candidates)[0] < len(lexical_words(core_words(words))):
        # Quelques mots seulement trouvés avec ces contraintes, et pas là où le
        # visiteur les voit en entier (16/10/2026) : « karaoké géant… gratuit,
        # pour un public adulte » avec « Famille » coché trouvait une fête qui
        # cite « groupe », pendant que le Karaoké géant d'un partenaire, en
        # soirée, était écarté par le public. On regarde sans les contraintes :
        # si le sujet y est nettement mieux montré, il passe devant, et la
        # réponse dit ce qui a été relâché.
        wider_rows, wider_plan, dropped = widen_for_subject(connection, plan, vector)
        if best_match(wider_rows)[0] > best_match(candidates)[0]:
            candidates = keep_exact_matches(wider_rows)[0]
            plan, widened = wider_plan, dropped
            words_found = any(row.get("keyword_match") for row in candidates)
            type_found = any(row.get("category_match") for row in candidates)
    missing_words = distinctive_keywords(plan.keywords) if not words_found else []
    # Exact seulement si la carte montre le sujet (15/10/2026, évaluation des
    # 100 recherches) : des mots trouvés dans la seule description (« bière »
    # dans le programme d'un bar) donnent des idées voisines, annoncées comme
    # telles ; et tous les mots d'une expression doivent être là (« feu
    # d'artifice » : pas seulement « feu »), sauf une alternative (« vins ou huîtres »).
    shown_words = not words or any(int(row.get("visible_hits") or 0) for row in candidates) or not any(
        "visible_hits" in row for row in candidates)
    # Un synonyme appris est une alternative (« zumba » ou « fitness »), pas un mot de plus à exiger.
    from app.query_understanding import learned_synonyms

    synonyms = learned_synonyms()
    core = [word for word in words if word not in synonyms and word not in synonyms.values()]
    asked = len({word for word in lexical_query(core).split(" | ") if word})
    best_hits = max((int(row.get("keyword_hits") or 0) for row in candidates), default=0)
    partial = words_found and asked > 1 and best_hits < asked and " ou " not in f" {plain(question)} " \
        and any("keyword_hits" in row for row in candidates)
    # Le mot précis trouvé suffit (15/10/2026) : « cirque » est rangé en
    # « spectacle », mais un stage de cirque répond aussi à « cirque ».
    exact = bool(candidates) and not relaxed and not widened and not missing_words and shown_words \
        and not partial and (type_found or words_found or not plan.categories)
    # Offres partenaires qui répondent (au sujet, même élargi) : en tête des
    # cartes ; sinon, hors des cartes.
    candidates = partners_first(candidates, plan, exact or bool(widened), question)
    # Un événement à plusieurs dates ne prend qu'une carte (sa prochaine date),
    # les autres dates y sont rattachées.
    cards = []
    for index, (row, found) in enumerate(group_occurrences(candidates, now), start=1):
        main, others = complete_occurrences(connection, row, plan, now, found)
        card = to_card(main, index)
        card["other_dates"] = other_dates(others)
        cards.append(card)
    # Du type demandé : par la catégorie, ou par le titre (« Atelier jeux »).
    named = [word.rstrip("sx") for word in type_keywords(plan.keywords)]
    matching = sum(1 for card in cards if card["category"].lower() in plan.categories
                   or any(word and word in plain(card["title"]) for word in named))
    kept = f"{len(cards)} retenus"
    if plan.categories:
        kept += f", dont {matching} de type {category_label(plan.categories)}"
    # Rien d'exact : on propose aussi les offres partenaires les plus proches.
    offers: list[dict[str, Any]] = []
    # Idées voisines : les offres partenaires les plus proches par le sens,
    # à part. Sujet élargi : celles qui en parlent sont déjà dans les cartes ;
    # sujet introuvable : pas d'offre sans rapport.
    if not exact and not widened and not nothing_on_subject:
        try:
            offers = partner_offers(connection, plan, vector, [card["event_id"] for card in cards])
        except Exception:  # noqa: BLE001 (un plus : la réponse principale ne dépend pas d'elles)
            connection.rollback()
        if offers:
            kept += f" ; {len(offers)} offre(s) partenaire proche(s)"
    yield progress(STEP_FILTER, "completed", kept)

    yield progress(STEP_WRITE, "running")
    tokens: dict[str, int] = {}
    model = ""
    if cards and services.write is not None:
        try:
            asked = writer_question(question, plan.categories, matching, relaxed=relaxed, context=embed_text,
                                    missing_words=missing_words, widened=widened, asked=asked_plan.describe())
            raw, tokens, model = services.write(asked, plan, cards)
            answer, suggestions = parse_answer(raw, len(cards))
        except Exception:  # noqa: BLE001 (Mistral absent ou réponse illisible : réponse simple)
            degraded = True
            answer, suggestions = fallback_answer(cards, plan)
    elif nothing_on_subject:
        subject = " ".join(precise_words) or category_label(plan.categories)
        answer = (f"Je n'ai trouvé aucun événement sur « {subject} » dans notre base, ni pour "
                  f"{asked_plan.describe()}, ni sur les 3 prochains mois dans toute la zone.")
        suggestions = []
    else:
        answer, suggestions = fallback_answer(cards, plan)
    suggestions = suggestions or default_suggestions(plan)
    cards, answer = order_cards(cards, answer)
    yield progress(STEP_WRITE, "completed", "réponse sourcée" if not degraded else "réponse simplifiée")

    result = SearchResult(answer=answer, events=cards, suggestions=suggestions, filters=plan.describe(),
                          degraded=degraded, tokens=tokens, context=embed_text[-1000:], exact=exact,
                          partner_offers=offers, checked=checked_by_question(plan),
                          gaps={"mots_inconnus": unknown_words, "elargi": widened,
                                "mots_absents": missing_words, "sujet_introuvable": nothing_on_subject})
    if key and cards and not degraded:
        write_cache(connection, key, question, plan, result, version, now)
    if services.log:
        result.request_id = log_request(connection, question, filters, result, model,
                                        int((time.monotonic() - started) * 1000), plan=plan)
    yield {"event": "completed", "result": asdict(result)}


def writer_question(question: str, categories: Sequence[str], matching: int, *,
                    relaxed: str = "", context: str = "", missing_words: Sequence[str] = (),
                    widened: Sequence[str] = (), asked: str = "") -> str:
    """Demande transmise à Mistral, avec les précisions utiles : relance d'une
    question précédente, type de sortie absent, contraintes relâchées."""

    notes = []
    if context and context != question:
        notes.append(f"Relance de la question précédente ; demande complète : « {context} ».")
    if categories and not matching:
        notes.append(f"Type demandé : {category_label(categories)}. Aucun événement de la liste "
                     "n'est de ce type avec ces filtres : dis-le d'abord.")
    if missing_words:
        notes.append(f"Aucun événement de la liste ne mentionne « {' '.join(missing_words)} » : "
                     "dis-le d'abord, puis présente les autres comme des idées voisines.")
    if relaxed:
        notes.append(f"Rien ne correspond exactement à « {relaxed} » : la recherche a été élargie. "
                     "Dis-le d'abord, puis présente ces idées voisines.")
    if widened:
        notes.append(f"Rien sur ce sujet pour « {asked} ». Les événements de la liste parlent bien du sujet, "
                     f"mais sans ces contraintes : {', '.join(widened)}. Dis-le d'abord en une phrase, "
                     "puis présente-les en précisant ce qui diffère (prix, date, lieu, public).")
    return question + "".join(f"\n({note})" for note in notes)


def request_cost(model: str, tokens: Mapping[str, int]) -> float:
    """Coût d'une réponse en dollars (grille de agent/watch.py), gardé au millionième :
    une seule réponse coûte de l'ordre de 0,00001 $, l'arrondi de la nuit (4 décimales) le perdrait."""

    price_in, price_out = PRICES_USD_PER_MILLION.get(model, DEFAULT_PRICE)
    return round(tokens.get("input", 0) * price_in / 1e6 + tokens.get("output", 0) * price_out / 1e6, 6)


def plan_summary(plan: QueryPlan) -> dict[str, Any]:
    """Le plan en JSON pour `rag_requests.constraints`."""

    return {"periode": [plan.start.isoformat(), plan.end.isoformat()], "public": plan.public,
            "zone": plan.zone, "communes": list(plan.cities), "gratuit": plan.free_only, "soir": plan.evening, "moment": plan.moment,
            "categories": list(plan.categories), "mots_cles": list(plan.keywords),
            "lus_dans_la_question": sorted(plan.from_question)}


def log_request(connection: Any, question: str, filters: SearchFilters, result: SearchResult,
                model: str, latency_ms: int, plan: QueryPlan | None = None, from_cache: bool = False) -> int | None:
    """Trace la demande dans `rag_requests` (suivi Grafana). Jamais bloquant."""

    try:
        rows = fetch_rows(connection, LOG_SQL, {
            "question": question or "(filtres seuls)",
            # Filtres cochés et contraintes comprises : la base du suivi de précision.
            # « recherche » : réponse exacte ou non, et ce qui a manqué (bilan
            # hebdomadaire de l'agent, `fetch_search_gaps`).
            "constraints": json.dumps({**asdict(filters), **({"compris": plan_summary(plan)} if plan else {}),
                                       "recherche": {"exact": result.exact, **result.gaps}},
                                      ensure_ascii=False),
            "event_ids": [card["event_id"] for card in result.events],
            "status": "ok" if result.events else "aucun_resultat",
            "error": "service Mistral indisponible" if result.degraded else "",
            "latency_ms": latency_ms,
            "prompt_tokens": result.tokens.get("input"),
            "completion_tokens": result.tokens.get("output"),
            # Même grille de prix que l'agent (agent/watch.py) : l'embedding de la
            # question n'est pas compté (quelques dizaines de tokens).
            "cost_usd": request_cost(model, result.tokens) if model and result.tokens else None,
            "llm_model": model or None,
            "prompt_version": PROMPT_VERSION,
            "answer": result.answer,
            "degraded": result.degraded,
            "from_cache": from_cache,
        })
        connection.commit()
        return int(rows[0]["request_id"]) if rows else None
    except Exception:  # noqa: BLE001 (le suivi ne doit jamais bloquer une réponse)
        connection.rollback()
        return None


# Pouce haut / pouce bas sur une réponse (16/10/2026). Seulement pour une
# réponse de moins d'un jour : au-delà, le visiteur ne l'a plus sous les yeux.
# Un second clic remplace le premier (contrainte feedback_once). Le
# commentaire n'est jamais publié : il sert au bilan et, plus tard, de donnée
# d'entraînement (docs/evolutions.md).
FEEDBACK_WINDOW = timedelta(days=1)
RECORD_FEEDBACK_SQL = """
INSERT INTO sortiria.feedback (request_id, event_id, rating, comment)
SELECT r.request_id, '', %(rating)s, %(comment)s
FROM sortiria.rag_requests AS r
WHERE r.request_id = %(request_id)s AND r.asked_at >= %(since)s
ON CONFLICT (request_id, event_id) DO UPDATE
SET rating = EXCLUDED.rating, comment = EXCLUDED.comment, created_at = now()
RETURNING feedback_id"""


def record_feedback(connection: Any, request_id: int, rating: int, comment: str = "",
                    now: datetime | None = None) -> bool:
    """Enregistre l'avis sur une réponse (1 : utile, -1 : pas utile).
    Faux si la réponse n'existe pas ou date de plus d'un jour."""

    if rating not in (-1, 1):
        raise ValueError("rating vaut 1 ou -1")
    since = (now or datetime.now(PARIS)) - FEEDBACK_WINDOW
    rows = fetch_rows(connection, RECORD_FEEDBACK_SQL, {
        "request_id": request_id, "rating": rating, "comment": comment.strip()[:300], "since": since})
    connection.commit()
    return bool(rows)


def sse(event: Mapping[str, Any]) -> str:
    """Un message Server-Sent Events (même format que Chess Agent)."""

    name = str(event.get("event", "progress"))
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
