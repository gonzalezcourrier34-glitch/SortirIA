"""Rédaction de la réponse par Mistral : consigne, appel, lecture, repli.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

La consigne (`WRITE_PROMPT`, versionnée par `PROMPT_VERSION`), la liste
numérotée des événements donnée à Mistral, la lecture de sa réponse JSON
(citations vérifiées, typographie, relances), l'ordre des cartes selon les
citations, et la réponse simple quand Mistral ne répond pas.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.query_understanding import QueryPlan, category_label, plain
from app.search_filters import SearchFilters
from src.logger import get_logger

logger = get_logger(__name__)

# Version de la consigne : mots de la phrase ignorés (bilan du 19/10), « tableaux » = exposition.
PROMPT_VERSION = "2026-10-19.1"
DEFAULT_SEARCH_MODEL = "mistral-small-latest"
# Modèle de secours (23/10/2026) : ce matin-là, `mistral-small-latest` répondait
# « 503 Service unavailable » à chaque appel ; le modèle de la collecte de nuit,
# lui, répondait. Si le modèle principal échoue, on essaie une fois celui-ci
# avant de passer en réponse simplifiée. Vide ou égal au principal : pas de secours.
DEFAULT_SEARCH_FALLBACK_MODEL = "mistral-medium-latest"

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


CITATION = re.compile(r"\[(\d+)\]")


def events_for_prompt(cards: Sequence[Mapping[str, Any]]) -> str:
    """Liste numérotée donnée à Mistral (seules informations qu'il peut utiliser).

    Parameters
    ----------
    cards : Sequence[Mapping[str, Any]]
        Les cartes retenues (`to_card`), numérotées par `ref`.

    Returns
    -------
    str
        Une entrée par carte : numéro, titre, date, lieu, type, prix, mention
        partenaire, public, autres dates, puis le résumé.
    """

    lines = []
    for card in cards:
        details = [card["date"], card["place"], card["category"], card["price"]]
        if card.get("partner_ahead", card.get("partner")):
            details.append("offre partenaire")
        if card["audiences"]:
            details.append("public : " + ", ".join(card["audiences"]))
        if card.get("other_dates"):
            dates = ", ".join(d["date"] for d in card["other_dates"][:4])
            details.append("autres dates : " + dates)
        lines.append(f"[{card['ref']}] {card['title']} — {' ; '.join(d for d in details if d)}\n"
                     f"    {card['summary']}")
    return "\n".join(lines)


def parse_answer(raw: str, count: int) -> tuple[str, list[str]]:
    """Réponse et suggestions lues dans le JSON de Mistral.

    Une citation qui ne renvoie à aucun événement fourni ([7] quand il y en a
    4) est retirée : on n'affiche jamais une source qui n'existe pas.

    Parameters
    ----------
    raw : str
        Le JSON renvoyé par Mistral (`reponse`, `suggestions`).
    count : int
        Nombre d'événements fournis : les citations au-delà sont retirées.

    Returns
    -------
    tuple[str, list[str]]
        La réponse nettoyée (espaces, typographie) et les relances gardées.

    Raises
    ------
    ValueError
        Si la réponse est vide, ou si le JSON est illisible
        (`json.JSONDecodeError`, laissée remonter).
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
    """Met une espace insécable avant « : ; ! ? » (typographie française).

    Parameters
    ----------
    text : str
        Le texte à corriger.

    Returns
    -------
    str
        Le texte corrigé ; heures et liens (« 20:30 », « https:// ») inchangés.
    """
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

    Parameters
    ----------
    items : Sequence[Any]
        Les relances proposées par Mistral.

    Returns
    -------
    list[str]
        Au plus 3 relances, sans doublon ; vide s'il n'en reste aucune (les
        relances par défaut sont ajoutées par l'appelant).
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

    Parameters
    ----------
    text : str
        Une relance proposée.

    Returns
    -------
    list[str]
        Les deux choix séparés, ou la relance seule (espaces réduits).
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
    if (len(first.split()) > 1 and plain(lead) in LEAD_WORDS
            and plain(second.split()[0]) not in LEAD_WORDS):
        second = f"{lead} {second[:1].lower()}{second[1:]}"
    return [f"{first} ?", f"{second[:1].upper()}{second[1:]} ?"]


def order_cards(cards: list[dict[str, Any]], answer: str) -> tuple[list[dict[str, Any]], str]:
    """Range les cartes affichées et renumérote les citations de la réponse.

    Ordre des cartes affichées (15/10/2026) : d'abord les offres partenaires,
    puis les événements que la réponse cite (dans l'ordre où elle les cite),
    puis les autres. Les numéros sont refaits de 1 à n, et les citations de
    la réponse ([5] → [2]) suivent, pour que [n] désigne toujours la n-ième carte.

    Parameters
    ----------
    cards : list[dict[str, Any]]
        Les cartes numérotées de 1 à n (`ref`).
    answer : str
        La réponse rédigée, avec ses citations [n].

    Returns
    -------
    tuple[list[dict[str, Any]], str]
        Les cartes dans le nouvel ordre (copies renumérotées), et la réponse aux
        citations mises à jour.
    """

    # Relever les cartes citées, dans l'ordre de la réponse
    cited: list[int] = []
    for match in CITATION.finditer(answer):
        ref = int(match.group(1))
        if 1 <= ref <= len(cards) and ref not in cited:
            cited.append(ref)
    # Nouvel ordre : partenaires, puis cartes citées, puis les autres
    partners = [ref for ref in range(1, len(cards) + 1)
                if cards[ref - 1].get("partner_ahead", cards[ref - 1].get("partner"))]
    order = partners + [ref for ref in cited if ref not in partners]
    order += [ref for ref in range(1, len(cards) + 1) if ref not in order]
    # Renuméroter les cartes et les citations de la réponse
    new_ref = {old: new for new, old in enumerate(order, start=1)}
    ordered = []
    for old in order:
        card = dict(cards[old - 1])
        card["ref"] = new_ref[old]
        ordered.append(card)
    answer = CITATION.sub(lambda m: f"[{new_ref.get(int(m.group(1)), int(m.group(1)))}]", answer)
    return ordered, answer


def fallback_answer(cards: Sequence[Mapping[str, Any]],
                    filters: QueryPlan | SearchFilters, *, exact: bool = True) -> tuple[str, list[str]]:
    """Réponse sans Mistral (service indisponible) : simple et toujours exacte.

    Quand rien ne répond exactement, la phrase le dit (23/10/2026) : « Voici ce
    que j'ai trouvé » devant un festival pour « une pièce de théâtre » laissait
    croire à une réponse exacte.

    Parameters
    ----------
    cards : Sequence[Mapping[str, Any]]
        Les cartes retenues (les 3 premières sont citées).
    filters : QueryPlan | SearchFilters
        Les contraintes, résumées dans la phrase.
    exact : bool
        Vrai si les cartes répondent exactement à la demande ; faux pour des
        idées voisines. Par défaut vrai.

    Returns
    -------
    tuple[str, list[str]]
        La réponse et ses relances (vides quand des cartes sont trouvées).
    """

    if not cards:
        message = ("Je n'ai trouvé aucun événement qui corresponde pour "
                   f"{filters.describe()}. Essayez d'élargir la période ou la zone.")
        return message, ["Sur 3 mois ?", "Partout ?"]
    names = ", ".join(f"{card['title']} [{card['ref']}]" for card in cards[:3])
    if not exact:
        return (f"Je n'ai rien trouvé d'exact pour {filters.describe()}. "
                f"Quelques idées voisines : {names}."), []
    return f"Voici ce que j'ai trouvé pour {filters.describe()} : {names}.", []


def default_suggestions(filters: QueryPlan | SearchFilters) -> list[str]:
    """Relances proposées si Mistral n'en donne pas.

    Parameters
    ----------
    filters : QueryPlan | SearchFilters
        Les contraintes : le soir et le gratuit changent les relances proposées.

    Returns
    -------
    list[str]
        Trois relances.
    """

    evening = isinstance(filters, QueryPlan) and filters.evening
    suggestions = ["Et s'il pleut ?", "Plutôt en journée ?" if evening else "Plutôt le soir ?"]
    suggestions.append("Seulement gratuit ?" if not filters.free_only else "Sur 3 mois ?")
    return suggestions


def write_with_mistral(question: str, filters: QueryPlan | SearchFilters,
                       cards: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, int], str]:
    """Appel de rédaction. Renvoie (JSON brut, tokens, modèle).

    Le modèle est lu dans `SEARCH_MODEL` (par défaut `DEFAULT_SEARCH_MODEL`). S'il
    échoue (panne, quota, délai dépassé), un second essai est fait avec
    `SEARCH_FALLBACK_MODEL` (par défaut `DEFAULT_SEARCH_FALLBACK_MODEL`) ; le modèle
    renvoyé est celui qui a répondu, et il est écrit dans le journal des demandes.

    Parameters
    ----------
    question : str
        La demande transmise, avec ses précisions (`writer_question`).
    filters : QueryPlan | SearchFilters
        Les contraintes comprises, résumées pour Mistral.
    cards : Sequence[Mapping[str, Any]]
        Les cartes retenues, seules informations données à Mistral.

    Returns
    -------
    tuple[str, dict[str, int], str]
        Le JSON brut de la réponse, les tokens consommés et le nom du modèle qui
        a répondu.

    Raises
    ------
    Exception
        L'erreur du dernier modèle essayé, si aucun n'a répondu (`run_search`
        passe alors en réponse simplifiée).
    """

    from src.event.event_enrichment import call_mistral_json

    # Préparer le texte envoyé : la demande, les contraintes, les cartes numérotées
    text = (f"Demande : {question}\nContraintes comprises : {filters.describe()}\n\n"
            f"Événements trouvés :\n{events_for_prompt(cards)}")
    # Essayer le modèle principal, puis le modèle de secours s'il est différent
    models = writer_models()
    for number, model in enumerate(models, start=1):
        try:
            raw, usage = call_mistral_json(WRITE_PROMPT, text, model)
        except Exception:
            if number == len(models):
                raise
            logger.warning("Rédaction : %s indisponible, essai avec %s.", model, models[number],
                           exc_info=True)
            continue
        return raw, usage, model
    raise RuntimeError("aucun modèle de rédaction configuré")  # pragma: no cover


def writer_models() -> list[str]:
    """Modèles de rédaction à essayer, dans l'ordre.

    Returns
    -------
    list[str]
        `SEARCH_MODEL`, puis `SEARCH_FALLBACK_MODEL` s'il est défini (non vide) et
        différent du premier.
    """

    main = os.getenv("SEARCH_MODEL", "").strip() or DEFAULT_SEARCH_MODEL
    fallback = os.getenv("SEARCH_FALLBACK_MODEL", DEFAULT_SEARCH_FALLBACK_MODEL).strip()
    return [main] if not fallback or fallback == main else [main, fallback]


def writer_question(question: str, categories: Sequence[str], matching: int, *,
                    relaxed: str = "", context: str = "", missing_words: Sequence[str] = (),
                    widened: Sequence[str] = (), asked: str = "") -> str:
    """Demande transmise à Mistral, avec les précisions utiles.

    Relance d'une question précédente, type de sortie absent, mots absents,
    contraintes relâchées : chaque précision est ajoutée entre parenthèses.

    Parameters
    ----------
    question : str
        La question du visiteur.
    categories : Sequence[str]
        Les types de sortie demandés.
    matching : int
        Nombre de cartes du type demandé.
    relaxed : str
        Résumé des contraintes relâchées faute de résultat ; vide sinon.
    context : str
        La demande complète (question précédente comprise) ; vide sinon.
    missing_words : Sequence[str]
        Mots précis qu'aucune carte ne mentionne.
    widened : Sequence[str]
        Contraintes relâchées pour trouver le sujet (`widen_for_subject`).
    asked : str
        Résumé des contraintes demandées au départ.

    Returns
    -------
    str
        La question suivie des précisions, une par ligne.
    """

    notes = []
    if context and context != question:
        notes.append(f"Relance de la question précédente ; demande complète : « {context} ».")
    if categories and not matching:
        notes.append(f"Type demandé : {category_label(categories)}. Aucun événement de la liste "
                     "n'est de ce type avec ces filtres : dis-le d'abord.")
    if missing_words:
        notes.append(f"Aucun événement de la liste ne mentionne « {' '.join(missing_words)} » "
                     ": dis-le d'abord, puis présente les autres comme des idées voisines.")
    if relaxed:
        notes.append(f"Rien ne correspond exactement à « {relaxed} » : la recherche a été "
                     "élargie. Dis-le d'abord, puis présente ces idées voisines.")
    if widened:
        notes.append(f"Rien sur ce sujet pour « {asked} ». Les événements de la liste parlent "
                     f"bien du sujet, mais sans ces contraintes : {', '.join(widened)}. Dis-le "
                     "d'abord en une phrase, puis présente-les en précisant ce qui diffère (prix, "
                     "date, lieu, public).")
    return question + "".join(f"\n({note})" for note in notes)
