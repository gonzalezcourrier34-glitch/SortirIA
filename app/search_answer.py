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

from app.query_understanding import (
    CITY_ALIASES,
    MONTPELLIER_CITIES,
    OUTSIDE_PLACES,
    THAU_CITIES,
    QueryPlan,
    category_label,
    city_label,
    plain,
)
from app.search_filters import SearchFilters
from src.logger import get_logger

logger = get_logger(__name__)


class RefusedAnswer(ValueError):
    """Réponse de Mistral écartée par un contrôle du code (format, faits sans appui, langue).

    La réponse simple (`fallback_answer`) la remplace, sans la marquer comme
    simplifiée : Mistral a répondu, c'est le code qui a refusé.
    """

# Version de la consigne : mots de la phrase ignorés (bilan du 19/10), « tableaux » = exposition.
# 30/10/2026 : l'exemple « théâtre gratuit en famille » était recopié tel quel
# (« théâtre à Montpellier vendredi soir » → « pas de théâtre gratuit en
# famille ») ; réponses qui annonçaient des idées sans en nommer aucune.
# 30/10/2026, .2 : la version .1 citait en exemple « la Fête de la soupe [2] »,
# recopié comme titre d'autres événements (8 réponses sur 100), et renvoyait à
# « Contraintes comprises », recopié en fin de réponse (6 sur 100). Plus aucun
# exemple de titre ni de nom de ligne dans les règles.
# 02/11/2026, version .1 : réponses en anglais à une question en anglais ;
# accès (« accessible en transports en commun ») et public (« pour les ados »)
# affirmés sans appui dans la liste. Le code vérifie aussi (`check_facts`).
PROMPT_VERSION = "2026-11-02.1"
DEFAULT_SEARCH_MODEL = "mistral-small-latest"
# Modèle de secours (23/10/2026) : ce matin-là, `mistral-small-latest` répondait
# « 503 Service unavailable » à chaque appel ; le modèle de la collecte de nuit,
# lui, répondait. Si le modèle principal échoue, on essaie une fois celui-ci
# avant de passer en réponse simplifiée. Vide ou égal au principal : pas de secours.
DEFAULT_SEARCH_FALLBACK_MODEL = "mistral-medium-latest"
# Délai de rédaction (02/11/2026) : une réponse a mis 126 s (« que faire à Lyon
# demain »), le délai par défaut de la bibliothèque étant de 120 s. Au-delà de
# 15 s, le modèle de secours répond ; une rédaction prend 2 s en médiane.
WRITE_TIMEOUT = 15

WRITE_PROMPT = """Tu es SortirIA, un assistant qui conseille des sorties autour de Montpellier
et du bassin de Thau. On te donne la demande d'un utilisateur et une liste
numérotée d'événements réels tirés de notre base.

Règles :
- réponds toujours en français, quelle que soit la langue de la demande, en
  2 à 4 phrases, sur un ton simple et chaleureux ; vouvoie toujours l'utilisateur ;
- ne parle QUE des événements de la liste ; n'invente ni lieu, ni date, ni prix ;
- cite chaque événement mentionné par son numéro entre crochets : [1], [2] ;
- si aucun événement ne correspond vraiment, dis-le franchement ;
- si la demande précise un type de sortie absent de la liste, commence par le
  dire avec les mots de la demande, et seulement ceux-là, puis présente les
  autres événements comme des idées voisines ;
- n'ajoute jamais une contrainte que la demande ne contient pas (gratuit, en
  famille, le soir…) ;
- ne dis rien du public, de l'accès (transports, stationnement, accessibilité)
  ni du lieu d'un événement que sa ligne ne dit pas ; si la demande en parle et
  que la liste n'en dit rien, dis que tu n'as pas cette information ;
- nomme chaque événement que tu cites par son titre tel qu'il est écrit dans la
  liste, suivi de son numéro ; jamais une suite de numéros seuls, jamais un
  événement absent de la liste ; ne termine jamais la réponse par « : » ;
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
# Une lettre (pas un chiffre ni une ponctuation) ; « et », « ou » ne nomment rien.
LETTER = re.compile(r"[^\W\d_]")
LINK_WORDS = re.compile(r"\b(?:et|ou)\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[:;.!?]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Format des lignes données à Mistral (`events_for_prompt`) recopié dans la réponse
# (01/11/2026, 200 questions difficiles) : « Les Nuits du Sud à Sète — Du 12 au 14
# juillet · … ; concert ; offre partenaire ; Festival… », des événements absents des
# cartes, ou des lignes brutes. Un tiret long suivi de deux « ; » de près (les dates
# abrégées, « 3 déc. », ont un point), ou « offre partenaire ; », n'arrive pas dans
# une réponse rédigée.
RAW_EVENT_LINE = re.compile(r"—[^!?\n]{0,120}?;[^!?\n]{0,60}?;|offre partenaire\s*;", re.IGNORECASE)
# Nom de ligne de la demande recopié en fin de réponse (« … au Réservoir [1]. Contraintes comprises. »).
LEAKED_LABEL = re.compile(r"\s*Contraintes comprises\s*[.:]?\s*$", re.IGNORECASE)
BRACKETED_TITLE = re.compile(r"\[([^\[\]\d][^\[\]]*)\]\s*(?=\[\d+\])")
# Mise en forme Markdown (03/11/2026 : « **Cyrana** [1] » affiché avec ses
# astérisques) : l'interface montre du texte simple, le gras est retiré.
MARKDOWN_EMPHASIS = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
TITLE_CHARS = 60


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
    answer = LEAKED_LABEL.sub("", answer)
    answer = MARKDOWN_EMPHASIS.sub(r"\2", answer)
    # Titre mis entre crochets devant sa citation (02/11/2026 : « le [Festival dernier cri] [2] »)
    answer = BRACKETED_TITLE.sub(r"\1 ", answer)
    if RAW_EVENT_LINE.search(answer):
        # Lignes de la consigne recopiées : rien ne garantit que ces événements soient
        # dans les cartes. La réponse simple (`fallback_answer`) les remplace.
        raise RefusedAnswer("réponse au format des lignes de la consigne")
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


def short_title(title: str) -> str:
    """Titre d'une carte, coupé à un mot entier au-delà de `TITLE_CHARS` signes.

    Parameters
    ----------
    title : str
        Le titre de la carte.

    Returns
    -------
    str
        Le titre, ou son début suivi de « … ».
    """

    title = " ".join(str(title).split())
    if len(title) <= TITLE_CHARS:
        return title
    return title[:TITLE_CHARS].rsplit(" ", 1)[0] + "…"


def where_and_when(card: Mapping[str, Any]) -> str:
    """« (Sète, sam. 14 nov.) » : la commune et la date d'une carte, s'il y en a.

    Parameters
    ----------
    card : Mapping[str, Any]
        La carte (`city`, `date`).

    Returns
    -------
    str
        Le complément entre parenthèses, précédé d'une espace ; vide sinon.
    """

    date = str(card.get("date") or "").strip()
    parts = [str(card.get("city") or "").strip(), date[:1].lower() + date[1:]]
    parts = [part for part in parts if part]
    return f" ({', '.join(parts)})" if parts else ""


def name_citations(answer: str, cards: Sequence[Mapping[str, Any]], exact: bool = False) -> str:
    """Complète une réponse qui cite des événements sans les nommer.

    Simulation du 30/10/2026 (100 questions) : 11 réponses sur 80 finissaient par
    « Voici des idées voisines : » sans en citer aucune, et une se réduisait à
    « Voici quelques concerts à Frontignan : [1], [2], [3] et [4]. ». Le texte
    doit se lire seul : une citation « nue » (ni avant ni après elle, jusqu'à la
    citation voisine ou le dernier « : », autre chose que « et », « ou » et de la ponctuation) reçoit
    le titre de sa carte ; une réponse sans citation, ou qui finit par « : »,
    reçoit les titres des premières cartes qu'elle ne cite pas encore.

    Parameters
    ----------
    answer : str
        La réponse lue par `parse_answer` (citations vérifiées).
    cards : Sequence[Mapping[str, Any]]
        Les cartes données à Mistral, numérotées par `ref`.
    exact : bool
        Vrai si les cartes répondent à la demande : les titres ajoutés sont
        annoncés par « À découvrir », pas « Idées voisines » (02/11/2026). Faux par défaut.

    Returns
    -------
    str
        La réponse, inchangée si chaque citation est déjà nommée.
    """

    titles = {int(card["ref"]): short_title(card["title"]) for card in cards}
    matches = list(CITATION.finditer(answer))
    # Citations nues : leur titre est ajouté devant
    pieces, last = [], 0
    for index, match in enumerate(matches):
        before = answer[matches[index - 1].end() if index else 0:match.start()]
        before = CLAUSE_END.split(before)[-1]             # seulement depuis le dernier « : » ou « . »
        after = answer[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(answer)]
        ref = int(match.group(1))
        # Titre déjà écrit juste avant (un titre finissant par « ! » coupait la clause)
        named = ref in titles and " ".join(answer[:match.start()].split()).endswith(
            " ".join(titles[ref].rstrip("…").split()))          # espaces insécables compris (« histoire ! »)
        bare = not named and not any(LETTER.search(LINK_WORDS.sub("", text)) for text in (before, after))
        pieces.append(answer[last:match.start()])
        pieces.append(f"{titles[ref]} {match.group(0)}" if bare and ref in titles else match.group(0))
        last = match.end()
    pieces.append(answer[last:])
    answer = "".join(pieces)
    # Réponse sans citation, ou qui annonce une liste sans la donner
    text = answer.rstrip()
    if matches and not text.endswith(":"):
        return answer
    if not matches:
        # Sans aucune citation, les noms cités ne viennent pas de la liste (30/10/2026 :
        # « la Fête de la soupe et le Marché de Noël de Sète » pour un atelier de
        # poterie) : seule la première phrase, le constat, est gardée.
        text = SENTENCE_END.split(text, maxsplit=1)[0].rstrip(" :")
        text = text if text.endswith((".", "!", "?")) else text + "."
    cited = {int(match.group(1)) for match in matches}
    # Lieu et date de chaque idée : ce sont souvent eux qui diffèrent de la demande
    # (30/10/2026 : « pas d'atelier de poterie près de Thau », puis un atelier de
    # poterie… à Saint-Paul-et-Valmalle, sans le dire).
    where = {int(card["ref"]): where_and_when(card) for card in cards}
    names = ", ".join([f"{titles[ref]} [{ref}]{where[ref]}" for ref in sorted(titles) if ref not in cited][:3])
    if not names:
        return answer
    if text.endswith(":"):
        return f"{text} {names}."
    return f"{text} {'À découvrir' if exact else 'Idées voisines'}\u00a0: {names}."


# Faits que la liste ne donne pas (02/11/2026, 200 questions difficiles : 10
# réponses inventaient un public, un accès, un lieu) : contrôlés dans le code,
# la consigne seule ne suffisant pas. Une affirmation sans appui dans les cartes
# qu'elle cite est retirée.
ACCESS_CLAIM = re.compile(r"\b(?:accessibles?|accessibilit[ée]|transports? en commun|tram(?:way)?s?|bus|navettes?"
                          r"|fauteuils? roulants?|PMR|mobilit[ée] r[ée]duite|parkings?|stationnement|se garer)\b",
                          re.IGNORECASE)
PUBLIC_CLAIMS = {
    "famille": r"tout[- ]petits?|b[ée]b[ée]s?|enfants?|familles?|familial(?:e|es|aux)?|jeune public|gosses",
    "ados": r"ados?|adolescent(?:e|s|es)?",
    "seniors": r"seniors?|a[îi]n[ée]s|personnes [âa]g[ée]es",
}
PUBLIC_PHRASE = (r",?\s*(?:(?:sp[ée]cialement |sp[ée]cifiquement )?(?:con[çc]u(?:e|s|es)? |adapt[ée](?:e|s|es)? |"
                 r"destin[ée](?:e|s|es)? |id[ée]a(?:l|le|ux|les) |parfait(?:e|s|es)? )?(?:pour|avec|aux|à|en)\s+"
                 r"(?:[\w-]+\s+){{0,2}}?(?:les |des |vos |leurs |aux |vos |nos )?(?:{words})"
                 r"(?:\s+et\s+(?:leurs |les |vos )?(?:familles?|parents|adultes))?)")
ABSENCE = re.compile(r"\b(?:pas|aucun|aucune|rien|ni|sans|hors|ne|n['’]|non)\b", re.IGNORECASE)
PLACE_NAMES = {plain(city_label(city)): city_label(city)
               for city in (*MONTPELLIER_CITIES, *THAU_CITIES, *(t for targets in CITY_ALIASES.values() for t in targets))
               if city}
PLACE_NAMES.update({plain(name): name for name in OUTSIDE_PLACES.values()})
# « autour de Sète », « à 10 km de Mèze », « entre Montpellier et Sète » : un repère,
# pas le lieu de l'événement (texte passé par `plain`).
NEAR_WORDS = re.compile(r"\b(?:autour|pres|proche|loin|alentours|environs|cote|depuis|vers|km|kilometres)"
                        r"(?: de| d| du)?(?: la| le| l)?$|\bentre(?: \w+){1,3} et$")
ENGLISH_WORDS = frozenset("the and you your here are some for this with is of to in great options you're "
                          "event events weekend there can".split())
FRENCH_WORDS = frozenset("le la les des vous de et est pour une un du au aux avec dans voici".split())


def card_text(card: Mapping[str, Any]) -> str:
    """Ce que Mistral sait d'une carte, sous forme simple (titre, lieu, public, résumé…)."""
    parts = [card.get("title"), card.get("place"), card.get("city"), card.get("category"),
             card.get("price"), card.get("summary"), " ".join(card.get("audiences") or [])]
    return f" {plain(' '.join(str(part or '') for part in parts))} "


def check_facts(answer: str, cards: Sequence[Mapping[str, Any]], plan: QueryPlan | SearchFilters | None = None,
                request: str = "") -> str:
    """Retire de la réponse ce que les cartes citées ne disent pas.

    Contrôlé phrase par phrase (02/11/2026) :
    - accès (« accessible », transports, fauteuil, stationnement) : la phrase est
      retirée si aucune carte qu'elle cite (toutes, si elle n'en cite aucune) n'en parle ;
    - public (enfants, ados, seniors) : s'il n'est ni demandé ni dans les cartes
      citées, l'expression est retirée (« pour occuper vos ados »), sinon la phrase ;
    - commune : une phrase affirmative qui nomme une commune absente des cartes
      qu'elle cite (« À La Grande-Motte, … [1] » pour un événement de Frontignan)
      est retirée, sauf « autour de », « près de » et Montpellier (la zone).

    Parameters
    ----------
    answer : str
        La réponse lue par `parse_answer`.
    cards : Sequence[Mapping[str, Any]]
        Les cartes données à Mistral.
    plan : QueryPlan | SearchFilters | None
        Les contraintes comprises (le public demandé) ; None si inconnues.
    request : str
        La demande du visiteur (question précédente comprise).

    Returns
    -------
    str
        La réponse sans les affirmations sans appui.

    Raises
    ------
    RefusedAnswer
        Si la réponse est en anglais, s'il ne reste rien, ou plus aucune citation
        après avoir retiré une phrase qui en avait.
    """

    # Toujours en français (une question en anglais recevait une réponse en anglais)
    words = re.findall(r"[a-zA-Z']+", answer.lower())
    english = sum(word in ENGLISH_WORDS for word in words)
    if english >= 4 and english > sum(word in FRENCH_WORDS for word in words):
        raise RefusedAnswer("réponse en anglais")
    by_ref = {int(card["ref"]): card_text(card) for card in cards}
    everything = " ".join(by_ref.values())
    asked = f" {plain(request)} "
    public = getattr(plan, "public", None)
    public = "famille" if public == "enfant" else public
    kept, dropped_citations = [], False
    for sentence in sentences(answer):
        refs = [int(ref) for ref in CITATION.findall(sentence) if int(ref) in by_ref]
        support = " ".join(by_ref[ref] for ref in refs) if refs else everything
        # Accès : jamais sans appui
        claim = ACCESS_CLAIM.search(sentence)
        if claim and f" {plain(claim.group(0))} " not in support and \
                not (plain(claim.group(0)).startswith("accessib") and " accessib" in support):
            dropped_citations = dropped_citations or bool(refs)
            continue
        # Public : demandé, ou dit par les cartes citées
        for name, pattern in PUBLIC_CLAIMS.items():
            words_re = re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
            if not words_re.search(sentence) or name == public or words_re.search(asked) \
                    or words_re.search(support):
                continue
            shorter = without_phrase(sentence, PUBLIC_PHRASE.format(words=pattern))
            while shorter != sentence:                          # « en famille … adaptée aux familles »
                sentence, shorter = shorter, without_phrase(shorter, PUBLIC_PHRASE.format(words=pattern))
            if words_re.search(sentence):
                sentence = ""
                break
        if not sentence.strip():
            dropped_citations = dropped_citations or bool(refs)
            continue
        # Commune nommée sans appui, dans une phrase qui affirme
        if refs and not ABSENCE.search(sentence):
            padded = f" {plain(sentence)} "
            wrong = False
            for key in PLACE_NAMES:
                index = padded.find(f" {key} ")
                # Une commune d'une des cartes (« à Montpellier ou Sète ») n'est pas inventée
                if index < 0 or key == "montpellier" or f" {key} " in everything:
                    continue
                if NEAR_WORDS.search(padded[:index].rstrip()):
                    continue
                wrong = True
                break
            if wrong:
                dropped_citations = True
                continue
        kept.append(re.sub(r"\s{2,}", " ", sentence).strip())
    result = " ".join(part for part in kept if part)
    # Plus rien, ou plus aucune citation après en avoir retiré : la réponse simple
    if not result or (dropped_citations and not CITATION.search(result)):
        raise RefusedAnswer("réponse sans appui dans les cartes")
    return result


def sentences(answer: str) -> list[str]:
    """Phrases de la réponse ; un « ! » ou un « ? » dans un titre ne coupe pas la phrase.

    02/11/2026 : « … ou l'exposition » restait seul quand la suite, après le
    « ! » d'un titre, était retirée. Un morceau qui commence par une minuscule,
    un chiffre ou une citation continue la phrase précédente.

    Parameters
    ----------
    answer : str
        La réponse.

    Returns
    -------
    list[str]
        Les phrases, dans l'ordre.
    """

    found: list[str] = []
    for piece in SENTENCE_END.split(answer.strip()):
        if found and piece and (piece[0].islower() or piece[0].isdigit() or piece[0] in "[«(\"*"):
            found[-1] = f"{found[-1]} {piece}"
        else:
            found.append(piece)
    return found


def without_phrase(sentence: str, pattern: str) -> str:
    """Retire une expression de la phrase, avec le « et » qui la relie au reste.

    « gratuite et en famille » -> « gratuite » ; « des ateliers en famille et
    gratuits » -> « des ateliers gratuits » ; « conçue pour les seniors et
    entièrement gratuite » -> « entièrement gratuite ».

    Parameters
    ----------
    sentence : str
        La phrase.
    pattern : str
        L'expression à retirer (expression régulière).

    Returns
    -------
    str
        La phrase sans l'expression.
    """

    for form in (rf"\s+et{pattern}", rf"{pattern}\s+et(?=\s)", pattern):
        changed = re.sub(form, "", sentence, flags=re.IGNORECASE)
        if changed != sentence:
            return re.sub(r" +([,.])", r"\1", re.sub(r" {2,}", " ", changed))
    return sentence


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
        return (f"Je n'ai rien trouvé d'exact pour {filters.describe().replace(' · ', ', ')}. "
                f"Quelques idées voisines : {names}."), []
    return f"Voici ce que j'ai trouvé pour {filters.describe().replace(' · ', ', ')} : {names}.", []


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
            raw, usage = call_mistral_json(WRITE_PROMPT, text, model, timeout=WRITE_TIMEOUT)
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
                    widened: Sequence[str] = (), asked: str = "", outside: Sequence[str] = (),
                    filters_met: bool = False) -> str:
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
    outside : Sequence[str]
        Lieux demandés hors de la zone couverte (« Agde »).
    filters_met : bool
        Vrai si les cartes respectent toutes les contraintes, sans rien de
        relâché ni de manquant : Mistral ne doit pas dire qu'il n'y a rien.

    Returns
    -------
    str
        La question suivie des précisions, une par ligne.
    """

    notes = []
    if outside:
        notes.append(f"{', '.join(outside)} est hors de la zone couverte (Montpellier, sa métropole et le "
                     "bassin de Thau) : dis-le d'abord, puis présente ces événements comme des idées dans "
                     "la zone couverte.")
    if context and context != question:
        notes.append(f"Relance de la question précédente ; demande complète : « {context} ».")
    if categories and not matching:
        notes.append(f"Type demandé : {category_label(categories)}. Aucun événement de la liste "
                     "n'est de ce type avec ces filtres : dis-le d'abord.")
    if missing_words:
        notes.append(f"Aucun événement de la liste ne mentionne « {' '.join(missing_words)} » "
                     ": dis-le d'abord, puis présente les autres comme des idées voisines.")
    if relaxed:
        notes.append(f"Rien ne correspond exactement à la demande ({relaxed.replace(' · ', ', ')}) : la recherche a été "
                     "élargie. Dis-le d'abord, puis présente ces idées voisines.")
    if widened:
        # Contraintes écrites en clair : la note était recopiée telle quelle (02/11/2026 :
        # « Rien sur ce sujet pour « sam. 14 nov. · Montpellier · danse » »).
        notes.append(f"Aucun événement sur ce sujet ne respecte toutes les contraintes demandées "
                     f"({asked.replace(' · ', ', ')}). Les événements de la liste parlent "
                     f"bien du sujet, mais sans ces contraintes : {', '.join(widened)}. Dis-le "
                     "d'abord en une phrase, puis présente-les en précisant ce qui diffère (prix, "
                     "date, lieu, public).")
    if filters_met and not notes:
        # 02/11/2026 : « et pour demain ? » répondait « pas d'événement demain » devant
        # des cartes de demain. Les filtres (date, lieu, public, gratuit) sont appliqués
        # par la base : le dire à Mistral.
        notes.append("Tous ces événements respectent la date, le lieu et le public demandés : "
                     "ne dis pas qu'il n'y a rien à cette date ou à cet endroit.")
    return question + "".join(f"\n({note})" for note in notes)
