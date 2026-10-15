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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agent.watch import DEFAULT_PRICE, PRICES_USD_PER_MILLION
from app.query_understanding import (
    QUESTION_CATEGORIES,
    QueryPlan,
    Understanding,
    category_label,
    combine,
    is_follow_up,
    merge_with_filters,
    plain,
    understand,
    wanted_categories,  # noqa: F401 (réexporté pour les appelants existants)
)

PARIS = ZoneInfo("Europe/Paris")
PROMPT_VERSION = "2026-10-15.4"   # toutes les dates comptées sur les cartes : renouvelle le cache
DEFAULT_SEARCH_MODEL = "mistral-small-latest"
CANDIDATES = 80          # séances comparées à la question (regroupées ensuite par événement)
MAX_PROPOSALS = 8        # événements montrés et donnés à Mistral (2 rangées de 4 cartes, 15/10)
MAX_OTHER_DATES = 12     # autres dates gardées pour un même événement
RATING_WINDOW_DAYS = 7   # avis possible du début de l'événement à 7 jours après sa fin
SUMMARY_CHARS = 240

PUBLICS = ("famille", "soiree", "ados", "seniors")
ZONES = {"montpellier": "montpellier", "thau": "bassin_de_thau"}
PERIODS = ("aujourdhui", "weekend", "7jours", "3mois")

# Étapes affichées par l'interface (même ordre que frontend/src/app/search.model.ts).
STEP_UNDERSTAND = "comprendre"
STEP_SEARCH = "rechercher"
STEP_FILTER = "filtrer"
STEP_WRITE = "rediger"

DAYS = ("Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim.")
MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
PUBLIC_LABELS = {"famille": "famille", "soiree": "soirée", "ados": "ados", "seniors": "seniors"}
ZONE_LABELS = {"montpellier": "Montpellier et sa métropole", "thau": "bassin de Thau"}
PERIOD_LABELS = {"aujourdhui": "aujourd'hui", "weekend": "ce week-end", "7jours": "les 7 prochains jours",
                 "3mois": "les 3 prochains mois"}

WRITE_PROMPT = """Tu es SortirIA, un assistant qui conseille des sorties autour de Montpellier
et du bassin de Thau. On te donne la demande d'un utilisateur et une liste
numérotée d'événements réels tirés de notre base.

Règles :
- réponds en français, en 2 à 4 phrases, sur un ton simple et chaleureux ;
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
- propose 3 questions de relance courtes (moins de 6 mots chacune) que
  l'utilisateur pourrait poser ensuite.

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
INDOOR_CONDITION = (f"(lower(COALESCE(e.category_norm, e.category, '')) <> ALL ({OUTDOOR_CATEGORIES}) "
                    f"AND {EVENT_TEXT} !~ {OUTDOOR_TEXT})")
OUTDOOR_CONDITION = (f"(lower(COALESCE(e.category_norm, e.category, '')) = ANY ({OUTDOOR_CATEGORIES}) "
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


def lexical_query(keywords: Sequence[str]) -> str:
    """Mots-clés au format to_tsquery : « jazz | echecs » (n'importe lequel suffit,
    plus il y en a, mieux l'événement est classé)."""

    words = [word for word in (plain(keyword) for keyword in keywords) if re.fullmatch(r"[a-z0-9]+", word)]
    return " | ".join(dict.fromkeys(words))


def distinctive_keywords(keywords: Sequence[str]) -> list[str]:
    """Mots précis de la question (« échecs », « Fabre », « Halloween ») : ni un
    type de sortie (« concert », « expo »), déjà traité par la catégorie."""

    return [word for word in keywords if word not in QUESTION_CATEGORIES and word.rstrip("sx") not in QUESTION_CATEGORIES]


def keep_exact_matches(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, bool]:
    """Ne garde que ce qui répond exactement, quand il y en a.

    1. Des événements citent les mots précis de la question (« échecs ») :
       seulement eux ;
    2. parmi ceux-là, des événements sont du type demandé (« concert ») :
       seulement eux.
    Sinon, rien n'est retiré : ce sont des idées voisines, et Mistral le dira.
    Renvoie (lignes, mots trouvés ?, type trouvé ?).
    """

    by_words = [row for row in rows if row.get("keyword_match")]
    if by_words:
        rows = by_words
    by_type = [row for row in rows if row.get("category_match")]
    if by_type:
        rows = by_type
    return rows, bool(by_words), bool(by_type)


def plan_from_filters(filters: SearchFilters, now: datetime, categories: Sequence[str] = ()) -> QueryPlan:
    """Plan de recherche fait des seuls filtres cochés (sans question)."""

    return merge_with_filters(Understanding(categories=tuple(categories)), public=filters.public,
                              zone=filters.zone, period_bounds=period_bounds(filters.period, now),
                              period_label=PERIOD_LABELS[filters.period], free_only=filters.free_only)


def build_search_query(plan: QueryPlan | SearchFilters, vector: str | None, now: datetime | None = None,
                       limit: int = CANDIDATES,
                       categories: Sequence[str] = ()) -> tuple[str, dict[str, Any]]:
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
    params: dict[str, Any] = {"start": plan.start, "end": plan.end, "limit": limit, "pool": POOL,
                              "categories": list(plan.categories)}
    if plan.public:
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

    lexical = lexical_query(plan.keywords)
    distinctive = lexical_query(distinctive_keywords(plan.keywords))
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
    category_sql = "lower(COALESCE(e.category_norm, e.category, '')) = ANY (%(categories)s)"
    keyword_sql = "false"
    if distinctive:
        params["distinctive"] = distinctive
        keyword_sql = "b.document @@ to_tsquery('french', %(distinctive)s)"
    sql = (
        f"WITH {', '.join(ctes)}\n"
        f"SELECT {EVENT_COLUMNS}, ({category_sql}) AS category_match, ({keyword_sql}) AS keyword_match,\n"
        f"       ({score_sql}) AS score\n"
        "FROM base AS b\n"
        "JOIN sortiria.v_upcoming_events AS e ON e.event_id = b.event_id\n"
        "LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id\n"
        + "".join(f"{join}\n" for join in joins)
        + "ORDER BY keyword_match DESC, category_match DESC, score DESC, e.start_at\nLIMIT %(limit)s"
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

# « À ne pas manquer » : toutes les offres partenaires proposables (3 mois),
# mises en avant d'abord, puis celles des 30 prochains jours, puis les autres.
HIGHLIGHTS_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.is_outing
ORDER BY e.is_sponsored DESC, (e.start_at < now() + interval '30 days') DESC, e.start_at
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


def price_label(row: Mapping[str, Any]) -> str:
    """Prix affiché sur la carte."""

    if row.get("is_free"):
        return "Gratuit"
    text = str(row.get("price_text") or "").strip()
    if text:
        return text if len(text) <= 40 else text[:39].rstrip() + "…"
    if row.get("price_min") is not None:
        return f"dès {float(row['price_min']):g} €".replace(".", ",")
    return "Prix non communiqué"


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
                         now: datetime) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Séance montrée et autres dates d'une carte, d'après TOUTES les séances à venir.

    Correction du 15/10/2026 : la carte ne comptait que les séances trouvées par
    la recherche (« + 1 date » quand le détail en listait 3), et pouvait montrer
    le 12 décembre alors qu'une séance avait lieu le 17 octobre. Maintenant :
    la séance montrée est la prochaine dans la période demandée, et toutes les
    autres sont comptées, comme dans le détail.
    """

    everyone = [row, *sibling_rows(connection, row)]
    in_period = [r for r in everyone if r["start_at"] < plan.end and (r.get("end_at") or r["start_at"]) >= plan.start]
    main = min(in_period or [row], key=lambda r: (r["start_at"] < now, r["start_at"]))
    others = sorted((r for r in everyone if r["event_id"] != main["event_id"]), key=lambda r: r["start_at"])
    return main, others[:MAX_OTHER_DATES]


def other_dates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Autres dates d'un événement, telles que l'interface les affiche."""

    return [{"event_id": str(row["event_id"]), "date": event_date_label(row)} for row in rows]


def to_card(row: Mapping[str, Any], ref: int | None = None) -> dict[str, Any]:
    """Un événement tel que l'interface l'affiche (carte et détail)."""

    place = " · ".join(part for part in (str(row.get("venue_name") or "").strip(),
                                         str(row.get("city") or "").strip()) if part)
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
        "price": price_label(row),
        "price_min": float(row["price_min"]) if row.get("price_min") is not None else None,
        "audiences": [PUBLIC_LABELS.get(item, item) for item in row.get("audiences") or []],
        "summary": short_summary(row),
        "url": str(row.get("url") or ""),
        "source": str(row.get("source_name") or ""),
        "source_attribution": str(row.get("source_attribution") or ""),
        "partner": row.get("partner_id") is not None,
        "partner_name": str(row.get("partner_name") or ""),
        "venue_rating": float(rating) if rating is not None else None,
        "venue_rating_count": int(row.get("venue_rating_count") or 0),
        "other_dates": [],
    }


def event_detail(connection: Any, event_id: str, comments: int = 3) -> dict[str, Any] | None:
    """Détail d'un événement proposable, avec les derniers avis publiés sur son lieu."""

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
             "place": " · ".join(part for part in (str(row.get("venue_name") or "").strip(),
                                                    str(row.get("city") or "").strip()) if part)}
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
    order = "e.is_sponsored DESC, e.start_at"
    if vector:
        params["vector"] = vector
        order = "e.embedding <=> %(vector)s::vector NULLS LAST, e.is_sponsored DESC, e.start_at"
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


# ----------------------------------------------------------------------------
# Rédaction
# ----------------------------------------------------------------------------

CITATION = re.compile(r"\[(\d+)\]")


def events_for_prompt(cards: Sequence[Mapping[str, Any]]) -> str:
    """Liste numérotée donnée à Mistral (seules informations qu'il peut utiliser)."""

    lines = []
    for card in cards:
        details = [card["date"], card["place"], card["category"], card["price"]]
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
    answer = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r" {2,}", " ", answer)).strip()
    suggestions = [str(item).strip() for item in data.get("suggestions") or [] if str(item).strip()][:3]
    return answer, suggestions


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

    suggestions = ["Et s'il pleut ?", "Plutôt en soirée ?" if filters.public != "soiree" else "Plutôt en journée ?"]
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


def progress(step: str, status: str, message: str = "") -> dict[str, Any]:
    """Un événement de progression (« event: progress » du flux)."""

    return {"event": "progress", "step": step, "status": status, "message": message}


def cache_key(question: str, context: str, filters: SearchFilters, now: datetime, version: str) -> str:
    """Empreinte de la demande : question et contexte normalisés, cases cochées,
    jour, version du prompt et de la base."""

    import hashlib

    parts = [PROMPT_VERSION, version, now.astimezone(PARIS).date().isoformat(), plain(question), plain(context),
             json.dumps(asdict(filters), sort_keys=True)]
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


def plan_search(question: str, filters: SearchFilters, now: datetime,
                context: str = "") -> tuple[QueryPlan, str]:
    """Plan de recherche et texte à vectoriser.

    `context` est la question précédente de la conversation : pour une relance
    (« et pour les enfants ? »), ses contraintes sont gardées et complétées, et
    le sens cherché est celui des deux questions réunies.
    """

    current = understand(question, now)
    embed_text = question
    if context and is_follow_up(question):
        current = combine(understand(context, now), current)
        embed_text = f"{context} {question}"
    plan = merge_with_filters(current, public=filters.public, zone=filters.zone,
                              period_bounds=period_bounds(filters.period, now),
                              period_label=PERIOD_LABELS[filters.period], free_only=filters.free_only)
    return plan, embed_text


def run_search(connection: Any, question: str, filters: SearchFilters,
               services: SearchServices | None = None, context: str = "") -> Iterator[dict[str, Any]]:
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
    plan, embed_text = plan_search(question, filters, now, context)
    yield progress(STEP_UNDERSTAND, "completed", plan.describe())

    # Même demande, même jour, même base : la réponse déjà rédigée, sans Mistral.
    key = version = ""
    if services.cache and question:
        version = index_version(connection)
        key = cache_key(question, context, filters, now, version)
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
            vector = services.embed(embed_text)
        except Exception:  # noqa: BLE001 (sans vecteur, mots-clés et date : la recherche continue)
            degraded = True
    sql, params = build_search_query(plan, vector)
    candidates = fetch_rows(connection, sql, params)
    relaxed = ""
    if not candidates and plan.from_question:
        # Les contraintes lues dans la question ne laissent rien : on les
        # relâche (sauf la date) et Mistral le dira.
        relaxed = plan.describe()
        plan = plan.relaxed()
        sql, params = build_search_query(plan, vector)
        candidates = fetch_rows(connection, sql, params)
    how = " + ".join(part for part, used in (("sens", vector), ("mots-clés", params.get("lexical"))) if used)
    found = f"{len(candidates)} séances ({how or 'triées par date'})"
    if relaxed:
        found += f", rien pour « {relaxed} » : recherche élargie"
    yield progress(STEP_SEARCH, "completed", found)

    yield progress(STEP_FILTER, "running")
    # Seulement ce qui répond exactement (mots précis, type demandé), quand il y
    # en a : 2 concerts à Mèze valent mieux que 2 concerts et 4 autres choses.
    candidates, words_found, type_found = keep_exact_matches(candidates)
    missing_words = distinctive_keywords(plan.keywords) if not words_found else []
    exact = bool(candidates) and not relaxed and (type_found or not plan.categories) and not missing_words
    # Un événement à plusieurs dates ne prend qu'une carte (sa prochaine date),
    # les autres dates y sont rattachées.
    cards = []
    for index, (row, _found) in enumerate(group_occurrences(candidates, now), start=1):
        main, others = complete_occurrences(connection, row, plan, now)
        card = to_card(main, index)
        card["other_dates"] = other_dates(others)
        cards.append(card)
    matching = sum(1 for card in cards if card["category"].lower() in plan.categories)
    kept = f"{len(cards)} retenus"
    if plan.categories:
        kept += f", dont {matching} de type {category_label(plan.categories)}"
    # Rien d'exact : on propose aussi les offres partenaires les plus proches.
    offers: list[dict[str, Any]] = []
    if not exact:
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
                                    missing_words=missing_words)
            raw, tokens, model = services.write(asked, plan, cards)
            answer, suggestions = parse_answer(raw, len(cards))
        except Exception:  # noqa: BLE001 (Mistral absent ou réponse illisible : réponse simple)
            degraded = True
            answer, suggestions = fallback_answer(cards, plan)
    else:
        answer, suggestions = fallback_answer(cards, plan)
    suggestions = suggestions or default_suggestions(plan)
    yield progress(STEP_WRITE, "completed", "réponse sourcée" if not degraded else "réponse simplifiée")

    result = SearchResult(answer=answer, events=cards, suggestions=suggestions, filters=plan.describe(),
                          degraded=degraded, tokens=tokens, context=embed_text[-1000:], exact=exact,
                          partner_offers=offers)
    if key and cards and not degraded:
        write_cache(connection, key, question, plan, result, version, now)
    if services.log:
        result.request_id = log_request(connection, question, filters, result, model,
                                        int((time.monotonic() - started) * 1000), plan=plan)
    yield {"event": "completed", "result": asdict(result)}


def writer_question(question: str, categories: Sequence[str], matching: int, *,
                    relaxed: str = "", context: str = "", missing_words: Sequence[str] = ()) -> str:
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
    return question + "".join(f"\n({note})" for note in notes)


def request_cost(model: str, tokens: Mapping[str, int]) -> float:
    """Coût d'une réponse en dollars (grille de agent/watch.py), gardé au millionième :
    une seule réponse coûte de l'ordre de 0,00001 $, l'arrondi de la nuit (4 décimales) le perdrait."""

    price_in, price_out = PRICES_USD_PER_MILLION.get(model, DEFAULT_PRICE)
    return round(tokens.get("input", 0) * price_in / 1e6 + tokens.get("output", 0) * price_out / 1e6, 6)


def plan_summary(plan: QueryPlan) -> dict[str, Any]:
    """Le plan en JSON pour `rag_requests.constraints`."""

    return {"periode": [plan.start.isoformat(), plan.end.isoformat()], "public": plan.public,
            "zone": plan.zone, "communes": list(plan.cities), "gratuit": plan.free_only, "soir": plan.evening,
            "categories": list(plan.categories), "mots_cles": list(plan.keywords),
            "lus_dans_la_question": sorted(plan.from_question)}


def log_request(connection: Any, question: str, filters: SearchFilters, result: SearchResult,
                model: str, latency_ms: int, plan: QueryPlan | None = None, from_cache: bool = False) -> int | None:
    """Trace la demande dans `rag_requests` (suivi Grafana). Jamais bloquant."""

    try:
        rows = fetch_rows(connection, LOG_SQL, {
            "question": question or "(filtres seuls)",
            # Filtres cochés et contraintes comprises : la base du suivi de précision.
            "constraints": json.dumps({**asdict(filters), **({"compris": plan_summary(plan)} if plan else {})},
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


def sse(event: Mapping[str, Any]) -> str:
    """Un message Server-Sent Events (même format que Chess Agent)."""

    name = str(event.get("event", "progress"))
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
