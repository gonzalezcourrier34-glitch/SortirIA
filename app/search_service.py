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

Ce module enchaîne les étapes (`plan_search`, `run_search`) ; le travail de
chacune est dans des modules voisins (découpe du 23/10/2026) :

- `app/search_filters.py` : cases cochées (`SearchFilters`), périodes ;
- `app/search_query.py` : requête SQL, classement, élargissement ;
- `app/search_cards.py` : cartes affichées, offres partenaires ;
- `app/search_answer.py` : rédaction par Mistral et réponse de repli ;
- `app/search_cache.py` : cache des réponses ;
- `app/search_log.py` : journal `rag_requests`, pouces, santé ;
- `app/search_detail.py` : détail d'un événement et avis sur son lieu.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.query_understanding import (
    PRECISE_SUBJECTS,
    QueryPlan,
    category_label,
    combine,
    is_follow_up,
    merge_with_filters,
    plain,
    understand,
    zone_of_city,
)
from app.search_answer import (
    default_suggestions,
    fallback_answer,
    order_cards,
    parse_answer,
    write_with_mistral,
    writer_question,
)
from app.search_cache import cache_key, index_version, read_cache, write_cache
from app.search_cards import (
    complete_occurrences,
    group_occurrences,
    other_dates,
    partner_offers,
    to_card,
)
from app.search_filters import PARIS, PERIOD_LABELS, SearchFilters, period_bounds
from app.search_log import log_request
from app.search_query import (
    PRACTICES,
    best_match,
    core_words,
    distinctive_keywords,
    embed_question,
    fetch_candidates,
    keep_exact_matches,
    known_subjects,
    lexical_words,
    partners_first,
    plan_can_widen,
    type_keywords,
    widen_for_subject,
)
from src.logger import get_logger

logger = get_logger(__name__)

# Étapes affichées par l'interface (même ordre que frontend/src/app/search.model.ts).
STEP_UNDERSTAND = "comprendre"
STEP_SEARCH = "rechercher"
STEP_FILTER = "filtrer"
STEP_WRITE = "rediger"


# ----------------------------------------------------------------------------
# Recherche complète, étape par étape
# ----------------------------------------------------------------------------

@dataclass(slots=True)
class SearchServices:
    """Ce dont la recherche a besoin (remplacé par des faux dans les tests).

    Attributes
    ----------
    embed : Callable[[str], str] | None
        Vectorise la question ; None pour chercher sans le sens.
    write : Callable[..., tuple[str, dict[str, int], str]] | None
        Rédige la réponse ; None pour la réponse simple sans Mistral.
    now : Callable[[], datetime]
        Donne l'heure actuelle (à Paris).
    log : bool
        Faux pour ne pas tracer les demandes dans `rag_requests` (évaluation).
    cache : bool
        Faux pour ne pas lire ni écrire le cache des réponses (évaluation).
    """

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
    """Résultat final envoyé à l'interface.

    Attributes
    ----------
    answer : str
        La réponse rédigée, avec ses citations [n].
    events : list[dict[str, Any]]
        Les cartes, dans l'ordre d'affichage.
    suggestions : list[str]
        Les questions de relance.
    filters : str
        Résumé des contraintes appliquées (`QueryPlan.describe`).
    degraded : bool
        Vrai si Mistral n'a pas répondu (réponse simplifiée).
    request_id : int | None
        Numéro de la demande dans `rag_requests`, pour le pouce haut ou bas.
    tokens : dict[str, int]
        Tokens consommés par la rédaction.
    context : str
        Demande complète comprise, renvoyée par l'interface avec la question suivante.
    exact : bool
        Vrai si les propositions répondent exactement ; faux : idées voisines.
    partner_offers : list[dict[str, Any]]
        Offres partenaires proches, affichées à part quand rien n'est exact.
    checked : dict[str, Any]
        Cases que l'interface doit cocher d'après la question.
    gaps : dict[str, Any]
        Ce qui a manqué, gardé dans le journal pour le bilan hebdomadaire.
    """

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
    """Un événement de progression (« event: progress » du flux).

    Parameters
    ----------
    step : str
        L'étape (`STEP_UNDERSTAND`, `STEP_SEARCH`, `STEP_FILTER`, `STEP_WRITE`).
    status : str
        « running » ou « completed ».
    message : str
        Le détail affiché sous l'étape ; vide par défaut.

    Returns
    -------
    dict[str, Any]
        L'événement à diffuser.
    """

    return {"event": "progress", "step": step, "status": status, "message": message}



def checked_by_question(plan: QueryPlan) -> dict[str, Any]:
    """Cases que l'interface doit cocher pour refléter la question.

    Parameters
    ----------
    plan : QueryPlan
        Le plan de recherche, avec ce qui vient de la question.

    Returns
    -------
    dict[str, Any]
        Les cases fixées par la question : `public`, `date` (libellé) et `zone`,
        seulement celles qui s'appliquent.
    """

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

    Parameters
    ----------
    question : str
        La question du visiteur.
    filters : SearchFilters
        Les cases cochées.
    now : datetime
        Heure de référence pour lire les dates.
    context : str
        La demande précédente de la conversation ; vide par défaut.
    follow_up : bool
        Vrai si la question est une relance cliquée. Par défaut faux.

    Returns
    -------
    tuple[QueryPlan, str]
        Le plan de recherche, et le texte à vectoriser.
    """

    # Lire la question : un public ou un lieu écrit l'emporte sur la case cochée
    current = understand(question, now)
    public, zone = filters.public, filters.zone
    if current.public:
        public = None
    if current.zone or current.cities or current.near:
        zone = None
    # Relance : compléter la demande précédente
    embed_text = question
    if context and (follow_up or is_follow_up(question)):
        current = combine(understand(context, now), current)
        embed_text = f"{context} {question}"
    # Fusionner avec les cases cochées
    plan = merge_with_filters(current, public=public, zone=zone,
                              period_bounds=period_bounds(filters.period, now),
                              period_label=PERIOD_LABELS[filters.period],
                              free_only=filters.free_only)
    return plan, embed_text


def run_search(connection: Any, question: str, filters: SearchFilters,
               services: SearchServices | None = None, context: str = "",
               follow_up: bool = False, visitor_key: str | None = None) -> Iterator[dict[str, Any]]:
    """Recherche complète. Produit les étapes une à une, puis le résultat.

    Dernier élément : {"event": "completed", "result": {...}}. Une panne de
    la base lève l'exception (l'appelant envoie « event: error »).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    question : str
        La question du visiteur (espaces réduits, 500 caractères gardés).
    filters : SearchFilters
        Les cases cochées.
    services : SearchServices | None
        Les services Mistral ; par défaut les vrais.
    context : str
        La demande précédente de la conversation ; vide par défaut.
    follow_up : bool
        Vrai si la question est une relance cliquée. Par défaut faux.
    visitor_key : str | None
        Empreinte salée du navigateur, gardée dans le journal (27/10/2026) ;
        None par défaut.

    Yields
    ------
    dict[str, Any]
        Les événements `progress` de chaque étape, puis l'événement `completed`
        avec le résultat (`SearchResult` en dictionnaire).
    """

    services = services or SearchServices()
    started = time.monotonic()
    question = " ".join(question.split())[:500]
    context = " ".join(context.split())[:500]
    now = services.now()
    degraded = False

    # Étape « comprendre » : la question et les cases deviennent un plan
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
                result = SearchResult(**{name: cached[name]
                                         for name in SearchResult.__dataclass_fields__
                                         if name in cached})
                cached["request_id"] = log_request(connection, question, filters, result, "", int(
                    (time.monotonic() - started) * 1000), plan=plan, from_cache=True,
                    visitor_key=visitor_key)
            yield {"event": "completed", "result": cached}
            return

    # Étape « rechercher » : vecteur de la question, puis séances candidates
    yield progress(STEP_SEARCH, "running")
    vector = None
    if embed_text and services.embed is not None:
        try:
            # Pratique : le sens va vers ce qui permet de la faire (pas vers les concerts).
            hint = f" ({PRACTICES[plan.practice][2]})" if plan.practice in PRACTICES else ""
            vector = services.embed(embed_text + hint)
        except Exception:  # sans vecteur, mots-clés et date : la recherche continue
            # Tracer la cause dans les journaux : la réponse, elle, continue sans le sens
            logger.warning("Vecteur de la question impossible : recherche par mots-clés seulement.",
                           exc_info=True)
            degraded = True
    candidates, params = fetch_candidates(connection, plan, vector)
    relaxed = ""
    if not candidates and plan.from_question:
        # Les contraintes lues dans la question ne laissent rien : on les
        # relâche (sauf la date) et Mistral le dira.
        relaxed = plan.describe()
        plan = plan.relaxed()
        candidates, params = fetch_candidates(connection, plan, vector)
    how = " + ".join(part for part, used in (("sens", vector), ("mots-clés", params.get("lexical")))
                     if used)
    found = f"{len(candidates)} séances ({how or 'triées par date'})"
    if relaxed:
        found += f", rien pour « {relaxed} » : recherche élargie"
    yield progress(STEP_SEARCH, "completed", found)

    # Étape « filtrer » : garder ce qui répond, élargir si le sujet manque
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
    # Décider si la réponse est exacte ou faite d'idées voisines
    missing_words = distinctive_keywords(plan.keywords) if not words_found else []
    # Exact seulement si la carte montre le sujet (15/10/2026, évaluation des
    # 100 recherches) : des mots trouvés dans la seule description (« bière »
    # dans le programme d'un bar) donnent des idées voisines, annoncées comme
    # telles ; et tous les mots d'une expression doivent être là (« feu
    # d'artifice » : pas seulement « feu »), sauf une alternative (« vins ou huîtres »).
    shown_words = (not words or any(int(row.get("visible_hits") or 0) for row in candidates)
                   or not any("visible_hits" in row for row in candidates))
    # Un synonyme appris est une alternative (« zumba » ou « fitness »), pas un mot de plus
    # à exiger (`core_words`).
    asked = len(lexical_words(core_words(words)))
    best_hits = max((int(row.get("keyword_hits") or 0) for row in candidates), default=0)
    partial = words_found and asked > 1 and best_hits < asked \
        and " ou " not in f" {plain(question)} " \
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

    # Étape « rédiger » : Mistral, sinon une réponse simple
    yield progress(STEP_WRITE, "running")
    tokens: dict[str, int] = {}
    model = ""
    if cards and services.write is not None:
        try:
            asked = writer_question(question, plan.categories, matching, relaxed=relaxed,
                                    context=embed_text, missing_words=missing_words,
                                    widened=widened, asked=asked_plan.describe())
            raw, tokens, model = services.write(asked, plan, cards)
            answer, suggestions = parse_answer(raw, len(cards))
        except Exception:  # Mistral absent ou réponse illisible : réponse simple
            # Tracer la cause (23/10/2026 : toutes les réponses étaient simplifiées, sans
            # aucune trace dans les journaux), puis répondre sans Mistral
            logger.warning("Rédaction par Mistral impossible : réponse simplifiée.", exc_info=True)
            degraded = True
            answer, suggestions = fallback_answer(cards, plan, exact=exact)
    elif nothing_on_subject:
        subject = " ".join(precise_words) or category_label(plan.categories)
        answer = (f"Je n'ai trouvé aucun événement sur « {subject} » dans notre base, ni pour "
                  f"{asked_plan.describe()}, ni sur les 3 prochains mois dans toute la zone.")
        suggestions = []
    else:
        answer, suggestions = fallback_answer(cards, plan, exact=exact)
    suggestions = suggestions or default_suggestions(plan)
    cards, answer = order_cards(cards, answer)
    yield progress(STEP_WRITE, "completed",
                   "réponse sourcée" if not degraded else "réponse simplifiée")

    # Résultat : cache, journal, puis envoi
    result = SearchResult(answer=answer, events=cards, suggestions=suggestions,
                          filters=plan.describe(), degraded=degraded, tokens=tokens,
                          context=embed_text[-1000:], exact=exact, partner_offers=offers,
                          checked=checked_by_question(plan),
                          gaps={"mots_inconnus": unknown_words, "elargi": widened,
                                "mots_absents": missing_words,
                                "sujet_introuvable": nothing_on_subject})
    if key and cards and not degraded:
        write_cache(connection, key, question, plan, result, version, now)
    if services.log:
        result.request_id = log_request(connection, question, filters, result, model,
                                        int((time.monotonic() - started) * 1000), plan=plan,
                                        visitor_key=visitor_key)
    yield {"event": "completed", "result": asdict(result)}


def sse(event: Mapping[str, Any]) -> str:
    """Un message Server-Sent Events (même format que Chess Agent).

    Parameters
    ----------
    event : Mapping[str, Any]
        L'événement ; sa clé `event` donne le nom du message (« progress » par défaut).

    Returns
    -------
    str
        Les lignes `event:` et `data:` (JSON), suivies d'une ligne vide.
    """

    name = str(event.get("event", "progress"))
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
