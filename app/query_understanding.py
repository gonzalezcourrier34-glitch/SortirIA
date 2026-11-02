"""Comprendre la question de l'utilisateur avant de chercher.

**Provenance** : nouveau module écrit pour SortirIA (15/10/2026).

Constat sur la première version : la recherche ne comparait que le « sens
global » de la phrase (un seul vecteur) aux événements. « Du théâtre samedi à
Sète avec les enfants » ramenait des événements proches en esprit, mais pas
forcément samedi, ni à Sète, ni pour les enfants. Les filtres de l'interface
étaient les seules contraintes réelles.

Ce module lit dans la question ce qu'un humain y lirait, avec des règles
simples, rapides (aucun appel à Mistral) et testées :

- **quand** : aujourd'hui, ce soir, demain, ce week-end, samedi, la semaine
  prochaine, en novembre, le 24 octobre, le 24/10 ;
- **où** : une commune de la zone (Sète, Lattes, Frontignan…) ou une zone
  (« Thau », « l'étang ») ;
- **pour qui** : enfants, ados, seniors, soirée ;
- **combien** : gratuit ;
- **quoi** : le type de sortie (théâtre, concert, expo…) ;
- **les mots qui restent** (« jazz », « échecs », « Fabre ») : ils servent à la
  recherche par mots-clés, qui complète la recherche par le sens.

Ces contraintes sont fusionnées avec les filtres de l'interface
(`merge_with_filters`) : ce que l'utilisateur écrit précise ce qu'il a coché.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTH_NAMES = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
               "septembre", "octobre", "novembre", "decembre")
DAY_LABELS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
MONTH_LABELS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
EVENING_HOUR = 17          # « ce soir » : séances qui commencent à partir de 17 h


def plain(value: str) -> str:
    """Met un texte sous forme simple, pour comparer des mots sans se soucier de l'écriture.

    Minuscules, sans accents, ponctuation remplacée par des espaces :
    « Théâtre à Sète ? » -> « theatre a sete ».

    Parameters
    ----------
    value : str
        Le texte à simplifier (`None` ou vide donne une chaîne vide).

    Returns
    -------
    str
        Le texte simplifié, mots séparés par une seule espace.
    """

    decomposed = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


# ----------------------------------------------------------------------------
# Type de sortie
# ----------------------------------------------------------------------------

# Mots de la question qui désignent une catégorie (forme sans accents, sans
# « s » final). Une demande de théâtre accepte aussi les spectacles : beaucoup
# de pièces sont rangées en « spectacle » par les sources. « Spectacle »
# n'inclut plus la danse (15/10/2026) : un bal folk ou une soirée salsa n'est
# pas un spectacle ; un spectacle de danse le dit dans son titre.
QUESTION_CATEGORIES: dict[str, tuple[str, ...]] = {
    "theatre": ("theatre", "spectacle"), "piece": ("theatre", "spectacle"),
    "comedie": ("theatre", "spectacle"), "impro": ("theatre", "spectacle"),
    "improvisation": ("theatre", "spectacle"), "spectacle": ("spectacle", "theatre"),
    "cirque": ("spectacle",), "humour": ("spectacle", "theatre"), "humoriste": ("spectacle", "theatre"),
    "concert": ("concert",), "musique": ("concert",), "jazz": ("concert",), "rock": ("concert",),
    "danse": ("danse",), "ballet": ("danse",), "cinema": ("cinema",), "cine": ("cinema",), "film": ("cinema",),
    "projection": ("cinema",), "expo": ("exposition",), "exposition": ("exposition",), "tableau": ("exposition",),
    "musee": ("exposition", "visite"), "atelier": ("atelier",), "stage": ("atelier",),
    "conference": ("conference",), "visite": ("visite",), "festival": ("festival",),
    "marche": ("marche",), "brocante": ("marche",), "foire": ("marche", "fete"), "sport": ("sport",), "match": ("sport",),
    "course": ("sport",), "jeu": ("jeux",), "jeux": ("jeux",), "soiree": ("soiree",),
    "lecture": ("lecture",), "livre": ("lecture",), "conte": ("lecture", "spectacle"),
    "nature": ("nature",), "balade": ("nature",), "randonnee": ("nature",), "fete": ("fete",),
}
# Mots plus précis que leur catégorie (15/10/2026) : « cirque » est rangé en
# « spectacle », mais un spectacle de danse n'est pas du cirque. Ces mots
# doivent aussi figurer dans l'événement (recherche par mots-clés).
PRECISE_SUBJECTS = frozenset({
    "cirque", "humour", "humoriste", "jazz", "rock", "ballet", "impro", "improvisation",
    "brocante", "conte", "randonnee", "musee", "match", "foire",
})
SHORT_SUBJECTS = frozenset({"dj", "bd", "vr"})   # sujets de 2 lettres (« soirée DJ »)
# Verbes de pratique (17/10/2026) : « je veux chanter », c'est chanter soi-même
# (karaoké, cours ou stage de chant), pas écouter un concert. Le verbe devient
# le sujet ; app/search_query.py (PRACTICES) dit ce qui y répond. Types qui
# vont avec (« jouer aux jeux ») ; un autre type nommé (« jouer au théâtre »,
# « danser à un concert ») garde la recherche habituelle.
PRACTICE_VERBS: dict[str, frozenset[str]] = {
    "chanter": frozenset({"atelier"}),
    "danser": frozenset({"atelier", "danse", "soiree"}),
    "jouer": frozenset({"jeux", "soiree"}),
    "peindre": frozenset({"atelier"}),
    "dessiner": frozenset({"atelier"}),
    "sculpter": frozenset({"atelier"}),
    "cuisiner": frozenset({"atelier"}),
}
# Expressions de pratique sans verbe propre (18/10/2026) : « jouer au théâtre »,
# c'est le plus souvent en faire soi-même (cours, atelier d'impro).
PRACTICE_PHRASES = {"faire du theatre": "theatre", "jouer au theatre": "theatre", "faire de l impro": "theatre",
                    "faire de l improvisation": "theatre", "jouer la comedie": "theatre",
                    "apprendre le theatre": "theatre"}
PRACTICE_LABELS = {"theatre": "pour faire du théâtre", "chanter": "pour chanter", "danser": "pour danser",
                   "jouer": "pour jouer",
                   "peindre": "pour peindre", "dessiner": "pour dessiner", "sculpter": "pour sculpter",
                   "cuisiner": "pour cuisiner"}


# Regarder (17/10/2026), l'inverse de la pratique : « un spectacle de danse »
# proposait un bal folk et une soirée salsa, rangés comme lui en « danse ».
# Avec ces mots, ce qui se pratique (bal, cours, stage, atelier) n'est plus du
# type demandé (app/search_query.py, PARTICIPATION).
WATCH_WORDS = ("spectacle", "spectacles", "voir", "regarder", "assister", "representation", "representations",
               "show", "ballet", "ballets", "piece", "pieces", "voire")      # « voire » : « voir » mal écrit


def find_practice(padded: str, categories: tuple[str, ...]) -> str | None:
    """« je veux chanter » -> « chanter » ; rien si la question nomme un autre type.

    Les expressions (« jouer au théâtre ») sont cherchées avant les verbes seuls.

    Parameters
    ----------
    padded : str
        La question passée par `plain`, entourée d'une espace de chaque côté.
    categories : tuple[str, ...]
        Les types de sortie lus dans la question (`wanted_categories`).

    Returns
    -------
    str | None
        La pratique (clé de `PRACTICE_LABELS`), ou None.
    """

    for phrase, practice in PRACTICE_PHRASES.items():
        if f" {phrase} " in padded:
            return practice
    for verb, allowed in PRACTICE_VERBS.items():
        if f" {verb} " in padded and set(categories) <= allowed:
            return verb
    return None


CATEGORY_LABELS = {"theatre": "théâtre", "cinema": "cinéma", "conference": "conférence",
                   "marche": "marché", "soiree": "soirée", "fete": "fête"}


SPECTACLE_YIELDS = True     # « spectacle » s'efface devant un type précis (désactivable pour mesurer)


def wanted_categories(question: str) -> tuple[str, ...]:
    """Catégories désignées par la question.

    « j'aimerais aller au théâtre » -> ("theatre", "spectacle"). Vide si la
    question n'en nomme aucune.

    Parameters
    ----------
    question : str
        La question du visiteur (brute ou déjà simplifiée).

    Returns
    -------
    tuple[str, ...]
        Les catégories, dans l'ordre des mots, sans doublon.
    """

    # Dans l'ordre des mots : le type nommé d'abord (« théâtre ou spectacle »).
    # « Spectacle » s'efface devant un autre mot de type (17/10/2026) : « un
    # spectacle de danse » cherche de la danse, pas n'importe quel spectacle.
    found: dict[str, None] = {}
    generic: dict[str, None] = {}
    for word in plain(question).split():
        for key in (word, word.rstrip("sx")):
            target = generic if key == "spectacle" and SPECTACLE_YIELDS else found
            target.update(dict.fromkeys(QUESTION_CATEGORIES.get(key, ())))
    return tuple(found or generic)


# Types trop larges pour désigner seuls ce qu'on cherche quand la question en
# nomme un plus précis : dans « un spectacle de danse », c'est la danse.
GENERIC_CATEGORIES = frozenset({"spectacle"})


def main_categories(question: str) -> tuple[str, ...]:
    """Le type que la question vise vraiment (16/10/2026) : le premier type de
    chaque mot qui en désigne un (« théâtre » -> théâtre, pas « spectacle »,
    accepté seulement en plus). « Spectacle » s'efface devant un type plus
    précis (« un spectacle de danse » -> danse).

    Sert aux offres partenaires : un spectacle de marionnettes est rangé en
    « spectacle » et sortait en tête pour « j'aimerais aller au théâtre » et
    pour « un spectacle de danse » (grille de pertinence du 16/10/2026).

    Parameters
    ----------
    question : str
        La question du visiteur.

    Returns
    -------
    tuple[str, ...]
        Les types visés ; « spectacle » seulement si aucun type plus précis n'est nommé.
    """

    found: dict[str, None] = {}
    for word in plain(question).split():
        for key in (word, word.rstrip("sx")):
            if key in QUESTION_CATEGORIES:
                found[QUESTION_CATEGORIES[key][0]] = None
                break
    precise = [category for category in found if category not in GENERIC_CATEGORIES]
    return tuple(precise or found)


def category_label(categories: Sequence[str]) -> str:
    """« théâtre ou spectacle » pour l'affichage des étapes.

    Parameters
    ----------
    categories : Sequence[str]
        Les catégories, sous leur forme sans accents.

    Returns
    -------
    str
        Les libellés accentués, joints par « ou ».
    """

    return " ou ".join(CATEGORY_LABELS.get(c, c) for c in categories)


# ----------------------------------------------------------------------------
# Lieux (mêmes communes que sortiria.event_zone, forme de sortiria.normalize_city)
# ----------------------------------------------------------------------------

MONTPELLIER_CITIES = (
    "baillargues", "beaulieu", "castelnau le lez", "castries", "clapiers", "cournonsec", "cournonterral",
    "fabregues", "grabels", "jacou", "juvignac", "lattes", "laverune", "le cres", "montaud",
    "montferrier sur lez", "murviel les montpellier", "perols", "pignan", "prades le lez", "restinclieres",
    "saint bres", "saint drezery", "saint genies des mourgues", "saint georges d orques",
    "saint jean de vedas", "saussan", "sussargues", "vendargues", "villeneuve les maguelone",
)
THAU_CITIES = (
    "balaruc le vieux", "balaruc les bains", "bouzigues", "frontignan", "gigean", "loupian", "marseillan",
    "meze", "mireval", "montbazin", "poussan", "sete", "vic la gardiole", "villeveyrac",
)
# Formes courtes employées dans une phrase : « à Castelnau », « à Balaruc ».
CITY_ALIASES = {
    "castelnau": ("castelnau le lez",), "balaruc": ("balaruc le vieux", "balaruc les bains"),
    "villeneuve": ("villeneuve les maguelone",), "st jean de vedas": ("saint jean de vedas",),
    "vic": ("vic la gardiole",),
    # Communes proches rangées dans la zone par leurs coordonnées (02/11/2026 :
    # « que faire à La Grande-Motte » n'était pas compris, et Mistral annonçait
    # « À La Grande-Motte » des événements de Frontignan).
    "palavas": ("palavas les flots",), "palavas les flots": ("palavas les flots",),
    "carnon": ("mauguio", "carnon"), "mauguio": ("mauguio",),
    "grande motte": ("la grande motte",), "la grande motte": ("la grande motte",),
    "saint gely": ("saint gely du fesc",), "st gely": ("saint gely du fesc",),
    "saint clement de riviere": ("saint clement de riviere",), "montarnaud": ("montarnaud",),
    "saint mathieu de treviers": ("saint mathieu de treviers",), "teyran": ("teyran",),
    "saint aunes": ("saint aunes",), "assas": ("assas",),
}
# Lieux hors de la zone couverte (02/11/2026) : compris, pour le dire franchement
# au lieu de chercher le mot dans les titres (« Agde » trouvait « Agde » dans une
# description de Sète).
OUTSIDE_PLACES = {
    "agde": "Agde", "cap d agde": "Le Cap d'Agde", "pezenas": "Pézenas", "beziers": "Béziers",
    "nimes": "Nîmes", "lunel": "Lunel", "sommieres": "Sommières", "lodeve": "Lodève",
    "clermont l herault": "Clermont-l'Hérault", "aigues mortes": "Aigues-Mortes", "le grau du roi": "Le Grau-du-Roi",
    "grau du roi": "Le Grau-du-Roi", "narbonne": "Narbonne", "perpignan": "Perpignan", "avignon": "Avignon",
    "arles": "Arles", "marseille": "Marseille", "toulouse": "Toulouse", "lyon": "Lyon", "paris": "Paris",
    "bordeaux": "Bordeaux", "nice": "Nice", "carcassonne": "Carcassonne", "ales": "Alès",
}
# « Montpellier » désigne en pratique la métropole : c'est une zone, pas une commune.
ZONE_WORDS = {
    "montpellier": "montpellier", "metropole": "montpellier",
    # Abréviations et fautes fréquentes (01/11/2026) : « expo gratos montpel », « mtp ».
    "montpel": "montpellier", "montpelier": "montpellier", "monpellier": "montpellier",
    "mtp": "montpellier", "mtpl": "montpellier",
    "thau": "thau", "etang": "thau", "bassin": "thau",
}
CITY_LABELS = {"sete": "Sète", "meze": "Mèze", "perols": "Pérols", "fabregues": "Fabrègues",
               "laverune": "Lavérune", "le cres": "Le Crès", "saint bres": "Saint-Brès",
               "la grande motte": "La Grande-Motte", "saint gely du fesc": "Saint-Gély-du-Fesc",
               "saint clement de riviere": "Saint-Clément-de-Rivière",
               "saint mathieu de treviers": "Saint-Mathieu-de-Tréviers", "saint aunes": "Saint-Aunès"}


def city_label(city: str) -> str:
    """Nom affichable d'une commune : « sete » -> « Sète », « le cres » -> « Le Crès ».

    Parameters
    ----------
    city : str
        La commune sous sa forme simplifiée.

    Returns
    -------
    str
        Le nom avec majuscules, accents connus et traits d'union.
    """
    return CITY_LABELS.get(city, city.title().replace(" Le ", "-le-").replace(" Les ", "-les-"))


# « à Montpellier », « dans Montpellier » : la ville elle-même (16/10/2026).
# Sans préposition, ou « près de / autour de Montpellier », c'est la métropole.
# Grille de pertinence : « des spectacles gratuits à Montpellier » proposait
# Mauguio et Palavas-les-Flots (zone de 20 km).
MONTPELLIER_ITSELF = (" a montpellier ", " dans montpellier ", " en ville a montpellier ",
                      " montpellier centre ", " centre de montpellier ", " centre ville de montpellier ")


def find_places(text: str) -> tuple[tuple[str, ...], str | None]:
    """Communes et zone nommées dans la question (texte déjà passé par `plain`).

    « à Montpellier » donne la commune Montpellier ; « Montpellier » seul donne
    la zone (la métropole).

    Parameters
    ----------
    text : str
        La question passée par `plain`.

    Returns
    -------
    tuple[tuple[str, ...], str | None]
        Les communes trouvées (formes simplifiées), et la zone nommée ou None.
    """

    padded = f" {text} "
    cities: list[str] = []
    # Montpellier même (« à Montpellier »), puis les communes et leurs formes courtes
    if any(marker in padded for marker in MONTPELLIER_ITSELF):
        cities.append("montpellier")
    for city in (*MONTPELLIER_CITIES, *THAU_CITIES):
        if f" {city} " in padded:
            cities.append(city)
    for alias, targets in CITY_ALIASES.items():
        if f" {alias} " in padded:
            cities += [target for target in targets if target not in cities]
    # La zone : le premier mot de zone rencontré
    zone = None
    for word, value in ZONE_WORDS.items():
        if f" {word} " in padded:
            zone = value
            break
    return tuple(cities), zone


def find_outside(text: str) -> tuple[str, ...]:
    """Lieux nommés hors de la zone couverte (« à Agde ») : leurs noms affichables.

    Parameters
    ----------
    text : str
        La question passée par `plain`.

    Returns
    -------
    tuple[str, ...]
        Les noms (« Agde »), sans doublon ; vide si aucun.
    """

    padded = f" {text} "
    names = [name for word, name in OUTSIDE_PLACES.items() if f" {word} " in padded]
    return tuple(dict.fromkeys(name for name in names
                               if not any(name != other and name in other for other in names)))


def zone_of_city(city: str) -> str:
    """Zone d'une commune : « thau » pour le bassin de Thau, sinon « montpellier ».

    Parameters
    ----------
    city : str
        La commune sous sa forme simplifiée.

    Returns
    -------
    str
        « thau » ou « montpellier ».
    """
    return "thau" if city in THAU_CITIES else "montpellier"


# ----------------------------------------------------------------------------
# Dates
# ----------------------------------------------------------------------------

def day_bounds(day: date, now: datetime) -> tuple[datetime, datetime]:
    """Bornes d'une journée à Paris, sans remonter avant maintenant.

    Parameters
    ----------
    day : date
        Le jour voulu.
    now : datetime
        L'heure actuelle : le début ne la précède jamais (aujourd'hui commence maintenant).

    Returns
    -------
    tuple[datetime, datetime]
        Le début (minuit ou maintenant) et la fin exclue (minuit le lendemain).
    """
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS)
    return max(start, now), start + timedelta(days=1)


def day_label(day: date) -> str:
    """Libellé court d'un jour : « sam. 18 oct. ».

    Parameters
    ----------
    day : date
        Le jour à afficher.

    Returns
    -------
    str
        Le jour de la semaine, le numéro et le mois abrégés.
    """
    return f"{DAY_LABELS[day.weekday()]} {day.day} {MONTH_LABELS[day.month - 1]}"


def next_weekday(today: date, weekday: int, *, strictly_after: bool = False) -> date:
    """Prochain jour de la semaine voulu, à partir d'aujourd'hui.

    Parameters
    ----------
    today : date
        Le jour de départ.
    weekday : int
        Le jour cherché (0 pour lundi, 6 pour dimanche).
    strictly_after : bool
        Vrai pour exclure aujourd'hui (« samedi prochain » un samedi). Par défaut faux.

    Returns
    -------
    date
        Le jour trouvé, aujourd'hui compris sauf si `strictly_after`.
    """
    delta = (weekday - today.weekday()) % 7
    if strictly_after and delta == 0:
        delta = 7
    return today + timedelta(days=delta)


# Vacances scolaires de la zone C (académie de Montpellier), du samedi au lundi de
# reprise (exclu). Calendrier officiel 2026-2027 (service-public.gouv.fr, vérifié
# le 02/11/2026) ; l'été finit à la rentrée, non publiée : 1er septembre.
SCHOOL_HOLIDAYS = {
    "toussaint": ((date(2026, 10, 17), date(2026, 11, 2)),),
    "noel": ((date(2026, 12, 19), date(2027, 1, 4)),),
    "hiver": ((date(2027, 2, 6), date(2027, 2, 22)),),
    "printemps": ((date(2027, 4, 3), date(2027, 4, 19)),),
    "ete": ((date(2027, 7, 3), date(2027, 9, 1)),),
}
HOLIDAY_LABELS = {"toussaint": "les vacances de la Toussaint", "noel": "les vacances de Noël",
                  "hiver": "les vacances d'hiver", "printemps": "les vacances de printemps",
                  "ete": "les vacances d'été"}
# Les plus précises d'abord ; « vacances » seul : les prochaines.
HOLIDAY_PHRASES = (
    ("noel", ("vacances de noel", "vacances noel", "vacances de fin d annee", "pendant les fetes",
              "fetes de fin d annee", "pendant noel")),
    ("toussaint", ("vacances de la toussaint", "vacances de toussaint", "vacances toussaint", "toussaint")),
    ("hiver", ("vacances d hiver", "vacances de fevrier", "vacances de ski")),
    ("printemps", ("vacances de printemps", "vacances de paques", "vacances d avril")),
    ("ete", ("vacances d ete", "grandes vacances", "vacances d juillet")),
    ("", ("vacances scolaires", "pendant les vacances", "les vacances", "vacances")),
)
# Fêtes d'un jour ou deux : (mois, jour, nombre de jours), l'année qui vient.
FEAST_PHRASES = (
    ((12, 31, 1), "le réveillon du nouvel an", ("reveillon du nouvel an", "nouvel an", "saint sylvestre",
                                                "reveillon du 31")),
    ((1, 1, 1), "le jour de l'an", ("jour de l an",)),
    ((12, 24, 2), "Noël", ("a noel", "le jour de noel", "le soir de noel", "reveillon de noel", "veille de noel",
                          "pour noel", "reveillon")),
)
NUMBER_WORDS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
                "huit": 8, "neuf": 9, "dix": 10, "quinze": 15, "1er": 1, "premier": 1}
NUMBER = r"(\d{1,2}|1er|premier|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|quinze)"
MONTH_PATTERN = "|".join(MONTH_NAMES)
DAY_PATTERN = (rf"(\d{{1,2}}|1er|premier)(?: ({MONTH_PATTERN}|\d{{1,2}}))?"
               # pas un prix, un âge, une heure, une distance : « jusqu'à 20 € », « à partir de 6 ans »
               r"(?! ?(?:euros?|eur|e|km|kilometres?|ans?|h|heures?|mois|jours?|personnes|places|min)\b)")
# « ce we », « le we prochain » : abréviation de week-end (pas « we » seul, anglais).
WEEKEND_SHORT = re.compile(r"\b(ce|le|du|un|bon|premier|dernier|prochain) (?:we|wk|weekend)\b|\bwe(?= prochain\b)")
HOUR = re.compile(r"\b(?:(avant|apres|vers|a|des|entre)\s+)?(\d{1,2})\s?h(?:\s?(\d{2}))?\b")


def number_value(word: str) -> int:
    """« deux » -> 2, « 1er » -> 1, « 12 » -> 12."""
    return NUMBER_WORDS.get(word, 0) or int(word)


def school_holiday(key: str, now: datetime) -> tuple[datetime, datetime, str] | None:
    """Prochaines vacances scolaires (zone C) du type demandé, ou les prochaines tout court.

    Parameters
    ----------
    key : str
        « noel », « hiver »… ; vide pour les prochaines vacances, quelles qu'elles soient.
    now : datetime
        L'heure actuelle : des vacances finies ne comptent plus.

    Returns
    -------
    tuple[datetime, datetime, str] | None
        Début (pas avant maintenant), fin exclue et libellé ; None si aucune n'est connue.
    """

    today = now.date()
    for name, periods in sorted(SCHOOL_HOLIDAYS.items(), key=lambda item: item[1][0][0]):
        if key and name != key:
            continue
        for first, back in periods:
            if back > today:
                start = datetime.combine(first, datetime.min.time(), tzinfo=PARIS)
                return max(start, now), datetime.combine(back, datetime.min.time(), tzinfo=PARIS), \
                    HOLIDAY_LABELS[name]
    return None


def resolve_day(number: str, month: str | None, today: date) -> date | None:
    """Jour écrit « 14 », « 14 novembre » ou « 14 11 » : le prochain, l'an prochain s'il est passé.

    Parameters
    ----------
    number : str
        Le numéro du jour (« 14 », « 1er »).
    month : str | None
        Le mois en lettres ou en chiffres ; None pour ce mois-ci (le suivant si le jour est passé).
    today : date
        Aujourd'hui.

    Returns
    -------
    date | None
        Le jour, ou None s'il n'existe pas (« 31 novembre »).
    """

    day_number = number_value(number)
    try:
        if month is None:
            day = date(today.year, today.month, day_number)
            if day < today:
                day = date(today.year + (today.month == 12), today.month % 12 + 1, day_number)
            return day
        month_number = MONTH_NAMES.index(month) + 1 if month in MONTH_NAMES else int(month)
        day = date(today.year, month_number, day_number)
        return day if day >= today - timedelta(days=1) else date(today.year + 1, month_number, day_number)
    except ValueError:
        return None


def month_start(year: int, month: int) -> date:
    """Premier jour d'un mois, en acceptant un mois au-delà de 12 (année suivante)."""
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def weekend_of(day: date) -> date:
    """Samedi du week-end d'un jour : le jour même un samedi, la veille un dimanche, sinon le suivant."""
    if day.weekday() == 6:
        return day - timedelta(days=1)
    return next_weekday(day, 5)


def find_hour(text: str) -> str | None:
    """Moment de la journée d'après une heure écrite : « à 15h » -> « apres-midi ».

    Parameters
    ----------
    text : str
        La question passée par `plain`.

    Returns
    -------
    str | None
        « matin » (avant midi), « apres-midi » (avant 17 h), « soir », ou None.
    """

    match = HOUR.search(text)
    # Sans « à », « vers », « après »… : une heure de la journée, pas une durée (« 1h30 de spectacle »)
    if not match or int(match.group(2)) > 23 or (not match.group(1) and int(match.group(2)) < 8):
        return None
    hour = int(match.group(2)) - (match.group(1) == "avant")
    if hour < 12:
        return "matin"
    return "apres-midi" if hour < EVENING_HOUR else "soir"


def find_period(text: str, now: datetime) -> tuple[datetime, datetime, str] | None:
    """Période nommée dans la question : (début, fin exclue, libellé), ou None.

    `text` est déjà passé par `plain`. Les formes les plus précises sont
    cherchées d'abord (« le week-end du 14 » avant « ce week-end », « demain
    soir » avant « demain »).

    Formes ajoutées le 02/11/2026 (200 questions difficiles, 11 dates mal lues) :
    « le week-end du 14 novembre », « le premier week-end de décembre », « la
    semaine du 23 », « après le 20 », « avant le 10 », « du 5 au 8 », « début /
    mi / fin novembre », « ce matin », « cette nuit », « dans 2 semaines »,
    « d'ici samedi », les vacances scolaires, Noël, le nouvel an, l'an prochain.

    Parameters
    ----------
    text : str
        La question passée par `plain`.
    now : datetime
        Heure de référence pour lire « demain », « ce week-end », « samedi ».

    Returns
    -------
    tuple[datetime, datetime, str] | None
        Le début, la fin exclue et le libellé affiché ; None si aucune date n'est nommée.
    """

    now = now.astimezone(PARIS)
    today = now.date()
    text = WEEKEND_SHORT.sub(lambda m: f"{m.group(1) or ''} week end".strip(), text)
    padded = f" {text} "

    def has(*words: str) -> bool:
        """Vrai si l'un des mots (ou expressions) figure dans la question."""
        return any(f" {word} " in padded for word in words)

    def midnight(day: date) -> datetime:
        """Minuit (heure de Paris) au début du jour."""
        return datetime.combine(day, datetime.min.time(), tzinfo=PARIS)

    def span(first: date, last_excluded: date, label: str) -> tuple[datetime, datetime, str]:
        """Du premier jour (pas avant maintenant) au dernier exclu."""
        return max(midnight(first), now), midnight(last_excluded), label

    # Vacances scolaires et fêtes
    for key, phrases in HOLIDAY_PHRASES:
        if has(*phrases):
            holiday = school_holiday(key, now)
            if holiday:
                return holiday
            break
    for (month, day_number, days), label, phrases in FEAST_PHRASES:
        if has(*phrases):
            day = date(today.year, month, day_number)
            if day + timedelta(days=days) <= today:
                day = date(today.year + 1, month, day_number)
            return span(day, day + timedelta(days=days), label)
    # Un jour précis autour duquel compter : week-end du, semaine du, après le, avant le, du … au
    match = re.search(rf"\bweek end (?:du|de|le) {DAY_PATTERN}\b", text)
    if match and (day := resolve_day(match.group(1), match.group(2), today)):
        saturday = weekend_of(day)
        return span(saturday, saturday + timedelta(days=2), f"le week-end du {day_label(saturday)}")
    match = re.search(rf"\bsemaine (?:du|de|le) {DAY_PATTERN}\b", text)
    if match and (day := resolve_day(match.group(1), match.group(2), today)):
        monday = day - timedelta(days=day.weekday())
        return span(monday, monday + timedelta(days=7), f"la semaine du {day_label(monday)}")
    match = re.search(rf"\b(?:du|entre le) {DAY_PATTERN} (?:au|et le) {DAY_PATTERN}\b", text)
    if match:
        last = resolve_day(match.group(3), match.group(4), today)
        first = resolve_day(match.group(1), match.group(2) or match.group(4), today) if last else None
        if first and last and first <= last:
            return span(first, last + timedelta(days=1), f"du {day_label(first)} au {day_label(last)}")
    match = re.search(rf"\b(?:apres le|a partir du|des le|a compter du) {DAY_PATTERN}\b", text)
    if match and (day := resolve_day(match.group(1), match.group(2), today)):
        if match.group(0).startswith("apres"):
            day += timedelta(days=1)
        return span(day, day + timedelta(days=90), f"à partir du {day_label(day)}")
    match = re.search(rf"\b(?:avant le|jusqu au|jusqu a) {DAY_PATTERN}\b", text)
    if match and (day := resolve_day(match.group(1), match.group(2), today)):
        last = day + timedelta(days=0 if match.group(0).startswith("avant") else 1)
        if last > today:
            return span(today, last, f"jusqu'au {day_label(last - timedelta(days=1))}")
    # Premier, deuxième, dernier week-end d'un mois (ou de ce mois-ci)
    match = re.search(rf"\b(premier|1er|deuxieme|2e|second|troisieme|3e|dernier) week end "
                      rf"(?:de|d|du mois de|du mois d|du mois)(?: ({MONTH_PATTERN}))?\b", text)
    if match:
        month = MONTH_NAMES.index(match.group(2)) + 1 if match.group(2) else today.month
        first = month_start(today.year + (month < today.month), month)
        following = month_start(first.year, first.month + 1)
        saturdays = [day for day in (first + timedelta(days=n) for n in range((following - first).days))
                     if day.weekday() == 5]
        rank = {"premier": 0, "1er": 0, "deuxieme": 1, "2e": 1, "second": 1, "troisieme": 2, "3e": 2}
        saturday = saturdays[-1] if match.group(1) == "dernier" else saturdays[rank[match.group(1)]]
        if saturday + timedelta(days=2) > today:
            return span(saturday, saturday + timedelta(days=2), f"le week-end du {day_label(saturday)}")
    # Dans N jours, N semaines, N mois ; d'ici N jours ; d'ici samedi
    match = re.search(rf"\b(dans|d ici) {NUMBER} (jours?|semaines?|mois)\b", text) or \
        re.search(r"\b(dans|d ici) (quinze|huit) (jours)\b", text)
    if match:
        count, unit = number_value(match.group(2)), match.group(3).rstrip("s")
        if match.group(1) == "d ici" and unit != "moi":
            days = count * (7 if unit == "semaine" else 1)
            return span(today, today + timedelta(days=days + 1), f"d'ici {count} {match.group(3)}")
        if unit == "moi":
            first = month_start(today.year, today.month + count)
            following = month_start(first.year, first.month + 1)
            label = MONTH_NAMES[first.month - 1].replace("fevrier", "février").replace(
                "aout", "août").replace("decembre", "décembre")
            return span(first, following, f"en {label}")
        target = today + timedelta(days=count * (7 if unit == "semaine" else 1))
        if unit == "semaine" or count >= 7:
            monday = target - timedelta(days=target.weekday())
            return span(monday, monday + timedelta(days=7), f"la semaine du {day_label(monday)}")
        return span(target, target + timedelta(days=1), day_label(target))
    for index, name in enumerate(WEEKDAYS):
        if has(f"d ici {name}", f"jusqu a {name}"):
            day = next_weekday(today, index)
            return span(today, day + timedelta(days=1), f"d'ici {day_label(day)}")
    # L'an prochain : le début de l'année (la base couvre les 3 prochains mois)
    if has("l an prochain", "l annee prochaine", f"en {today.year + 1}"):
        first = date(today.year + 1, 1, 1)
        return span(first, month_start(first.year, 4), f"début {first.year}")
    # Jours relatifs : après-demain, demain, aujourd'hui
    if has("apres demain"):
        day = today + timedelta(days=2)
        return (*day_bounds(day, now), day_label(day))
    if has("demain", "tomorrow", "manana"):
        day = today + timedelta(days=1)
        return (*day_bounds(day, now), "demain")
    if has("aujourd hui", "ce jour", "ce soir", "cet apres midi", "cette apres midi", "cet aprem", "ce matin",
           "ce midi", "cette nuit", "tout de suite", "maintenant", "tantot", "today", "tonight", "hoy",
           "esta noche"):
        return (*day_bounds(today, now), "aujourd'hui")
    # Week-ends et semaines
    if has("week end prochain", "weekend prochain"):
        saturday = next_weekday(today, 5, strictly_after=True)
        if today.weekday() == 6:
            saturday = today + timedelta(days=6)
        start = datetime.combine(saturday, datetime.min.time(), tzinfo=PARIS)
        return start, start + timedelta(days=2), "le week-end prochain"
    if has("ce week end", "ce weekend", "week end", "weekend", "fin de semana", "this weekend"):
        saturday = today - timedelta(days=1) if today.weekday() == 6 else next_weekday(today, 5)
        start = datetime.combine(saturday, datetime.min.time(), tzinfo=PARIS)
        return max(start, now), start + timedelta(days=2), "ce week-end"
    if has("semaine prochaine"):
        monday = next_weekday(today, 0, strictly_after=True)
        start = datetime.combine(monday, datetime.min.time(), tzinfo=PARIS)
        return start, start + timedelta(days=7), "la semaine prochaine"
    if has("cette semaine"):
        monday = next_weekday(today, 0, strictly_after=True)
        return now, datetime.combine(monday, datetime.min.time(), tzinfo=PARIS), "cette semaine"
    # « le 24 octobre », « 24 octobre » ; « le 24/10 ».
    match = re.search(r"\b(\d{1,2}|1er) (" + MONTH_PATTERN + r")\b", text)
    numeric = re.search(r"(?<![\d/])(\d{1,2}) (\d{1,2})(?![\d/])", text) if " le " in padded else None
    if match or numeric:
        found = match or numeric
        day = resolve_day(found.group(1), found.group(2), today)
        if day:
            return (*day_bounds(day, now), day_label(day))
    # Un jour de la semaine nommé (« samedi », « samedi prochain »)
    for index, name in enumerate(WEEKDAYS):
        if has(name, name + "s"):
            day = next_weekday(today, index, strictly_after=has(f"{name} prochain"))
            return (*day_bounds(day, now), day_label(day))
    # Un mois nommé (l'an prochain s'il est passé) : en entier, ou son début, milieu, fin
    for index, name in enumerate(MONTH_NAMES):
        part = next((word for word in ("debut", "mi", "fin") if has(f"{word} {name}", f"{word} de {name}",
                                                                    f"{word} d {name}")), None)
        if part or has(f"en {name}", f"mois de {name}", f"mois d {name}") or (
                name not in ("mars", "mai") and has(name)):
            year = today.year if index + 1 >= today.month else today.year + 1
            first = date(year, index + 1, 1)
            following = month_start(year, index + 2)
            shown = name.replace("fevrier", "février").replace("aout", "août").replace("decembre", "décembre")
            if part == "debut":
                return span(first, first + timedelta(days=15), f"début {shown}")
            if part == "mi":
                return span(first + timedelta(days=9), first + timedelta(days=21), f"mi-{shown}")
            if part == "fin":
                if following <= today:
                    continue
                return span(first + timedelta(days=15), following, f"fin {shown}")
            return span(first, following, f"en {shown}")
    if has("ce mois ci", "ce mois"):
        end = datetime(today.year + (today.month == 12), today.month % 12 + 1, 1, tzinfo=PARIS)
        return now, end, "ce mois-ci"
    return None


# ----------------------------------------------------------------------------
# Publics, prix, mots-clés
# ----------------------------------------------------------------------------

# Seulement des mots sans ambiguïté : le public devient un filtre strict.
# (« un petit concert », « tous âges », « avec des amis » ne disent rien du public.)
PUBLIC_WORDS = {
    "famille": ("enfant", "enfants", "famille", "familles", "familial", "familiale", "gamin", "gamins",
                "bebe", "bebes", "kids", "jeune public", "tout petits", "mon fils", "ma fille", "mes filles",
                "mes fils", "petits enfants", "petite enfance",
                # 02/11/2026 : « un truc sympa pr les gosses », « pour les petits »
                "gosse", "gosses", "marmots", "minots", "mioches", "loulous", "bambins", "pitchouns",
                "pour les petits", "avec les petits", "aux petits", "mes petits", "nos petits",
                "les tout petits", "des tout petits", "petits loups"),
    "ados": ("ado", "ados", "adolescent", "adolescents", "adolescente", "adolescentes", "lyceen", "lyceens",
             "collegien", "collegiens", "jeunes"),
    "seniors": ("senior", "seniors", "retraite", "retraites", "aines", "personnes agees", "grand mere",
                "grand pere", "grands parents", "les vieux", "personnes ainees"),
    "soiree": ("soiree", "soirees", "bar", "bars", "fiesta", "festif", "festive", "amoureux", "clubbing",
               "boite de nuit", "discotheque", "afterwork", "pub", "pubs"),
}
EVENING_WORDS = ("ce soir", "soir", "soiree", "nocturne", "nuit")
# Le moment de la journée, lu dans la phrase seulement (17/10/2026) : « en
# soirée » veut dire « le soir », pas le public « Soirée » (bars, fêtes) ;
# « le matin », « l'après-midi », « en journée » filtrent l'heure de début.
EVENING_PHRASES = ("en debut de soiree", "en fin de soiree", "dans la soiree", "en soiree", "le soir",
                   "ce soir")
MOMENT_PHRASES = {
    "matin": ("matin", "matinee", "matinees"),
    "apres-midi": ("apres midi", "aprem", "apres midis"),
    "journee": ("en journee", "dans la journee", "toute la journee", "de jour"),
}
# Heure de début (heure de Paris) acceptée pour chaque moment ; le soir : 17 h et après.
MOMENT_HOURS = {"matin": (0, 12), "apres-midi": (12, 18), "journee": (0, 18)}
MOMENT_LABELS = {"matin": "le matin", "apres-midi": "l'après-midi", "journee": "en journée", "soir": "le soir"}


def find_moment(padded: str) -> str | None:
    """« samedi matin » -> « matin », « en journée » -> « journee » (sans le soir).

    Parameters
    ----------
    padded : str
        La question passée par `plain`, entourée d'une espace de chaque côté.

    Returns
    -------
    str | None
        Le moment (clé de `MOMENT_PHRASES`), ou None.
    """

    for moment, phrases in MOMENT_PHRASES.items():
        if any(f" {phrase} " in padded for phrase in phrases):
            return moment
    return None


FREE_WORDS = ("gratuit", "gratuite", "gratuits", "gratuites", "gratos", "sans payer", "entree libre",
              "pas payant", "pas payante", "pas payants", "pas payantes", "non payant", "non payante")

# Mots qui n'aident pas la recherche par mots-clés (le reste est gardé).
STOPWORD_TEXT = """
a au aux avec ce ces cet cette d de des du en et est il je j l la le les leur lui ma me mes moi mon ne
nous on ou par pas pour qu que quel quelle quelles quels qui sa se ses si son sur ta te tes toi ton tu un
une vos votre vous y ca c s n m t
aimerais aimerait voudrais voudrait veux veut souhaite cherche cherchons trouver trouve faire fait sortir
sortie sorties aller allez allons idee idees activite activites truc trucs chose choses quoi propose
proposer proposez conseil conseille conseilles conseillez recommande envie bien bon bonne sympa super
peut peux pouvez puis il y a ya possible prochain prochaine prochains prochaines ici coin autour pres
proche dans entre chez tout tous toute toutes plus moins tres quelque quelques aujourd hui demain
voir vu aime aimer aimons adore adorons passer passe passons moment moments temps journee journees
matin matinee apres midi lieu lieux endroit endroits genre type style sorte sortes programme programmes
evenement evenements animation animations amis ami amie amies copain copains copine copines potes
collegues couple deux seul seule guidee guidees guide commentee commentees nouveau nouvelle nouveaux
original originale chouette cool fun interessant interessante gens monde public publics agenda
quand ans annee annees heure heures minute minutes cher chere chers cheres
plutot sinon aussi mais pareil meme encore autre autres
bonjour bonsoir salut coucou hello merci svp stp sont sera seront existe existent donner donne donnez
proposition propositions savoir sais connaitre connais voire
"""
# Dernière ligne (19/10/2026, bilan de la semaine) : « quels sont les
# prochaines expositions de peinture » (9 fois) cherchait aussi « sont » dans
# les titres, et finissait en idées voisines ; « bonjour », « propositions »,
# « voire » (pour « voir ») faisaient de même.
# 01/11/2026 (200 questions difficiles) : ces mots, trouvés dans quelques titres,
# devenaient des mots exigés et écartaient le type demandé : « tu es nul, trouve-moi
# un vrai concert » ne gardait qu'une pièce titrée « C'est vrai que… » et répondait
# « pas de concert » ; « fin novembre une expo », « début décembre un spectacle »,
# « koi faire », « pr les vieux » (Balaruc-le-Vieux) de même.
FILLER_TEXT = """
nul nulle nuls vrai vraie vrais vraies vraiment fin debut koi kwa pr pk pq ptit ptite ptits
vieux vieille vieilles genial geniale top meilleur meilleure meilleurs meilleures
seulement uniquement juste trop vacances scolaires reveillon sylvestre nouvel premier premiere 1er
dernier derniere deuxieme partir compter jusqu jusque quinzaine jours semaines pendant pdt durant
sans meuf mec cherie cheri dure durer duree dispo disponible disponibles plz please pls tkt bcp
"""
# Mots d'une question en anglais ou en espagnol (02/11/2026 : « ¿qué hacer en
# Montpellier este fin de semana? » exigeait « hacer », « este », « semana » et
# répondait qu'il n'y avait rien à Montpellier).
FOREIGN_TEXT = """
what where things thing the this that there with and for today tonight tomorrow weekend
hacer este esta estos semana hoy manana noche donde algo para con
"""
# 02/11/2026 (200 questions difficiles) : « seulement gratuit ? », « juste le
# gratuit », « avec ma meuf » cherchaient « seulement », « juste », « meuf ».
STOPWORDS = frozenset(STOPWORD_TEXT.split()) | frozenset(FILLER_TEXT.split()) | frozenset(FOREIGN_TEXT.split())
HTML_SCRIPT = re.compile(r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
HTML_TAG = re.compile(r"<[^<>]{0,200}>")


# Budget (15/10/2026) : « moins de 15 € », « 20 euros max », « pas cher ».
CHEAP_PRICE = 15.0
BUDGET_PATTERNS = (
    # « moins de 15 € », « jusqu'à 20 euros » : l'unité est obligatoire (« moins de 15 ans », « 20 km »).
    re.compile(r"(?:moins de|max(?:imum)?|pas plus de|jusqu a|sous|inferieur a)\s*(\d{1,3})\s*(?:e|eur|euro|euros)\b"),
    # « 20 € max », « 20 euros au plus ».
    re.compile(r"\b(\d{1,3})\s*(?:e|eur|euro|euros)\s*(?:max(?:imum)?|au plus|maxi)\b"),
    # « budget 20 », « budget de 20 € ».
    re.compile(r"\bbudget(?: de)?\s*(\d{1,3})\b"),
)
CHEAP_WORDS = ("pas cher", "pas chere", "pas trop cher", "petit budget", "petits prix", "bon marche", "economique")

# Météo et cadre : « s'il pleut » -> sorties en intérieur ; « en plein air » -> dehors.
INDOOR_WORDS = ("s il pleut", "pluie", "pleut", "au chaud", "a l interieur", "en interieur", "a l abri",
                "au sec", "il fait froid", "mauvais temps")
OUTDOOR_WORDS = ("plein air", "dehors", "en exterieur", "a l exterieur", "au soleil", "il fait beau", "beau temps")
WEATHER_CONSUMED = {"pleut", "pluie", "chaud", "interieur", "abri", "sec", "froid", "mauvais", "plein", "air",
                    "dehors", "exterieur", "soleil", "beau"}

# Distance : « près de Sète », « autour de Mèze », « à moins de 10 km de Frontignan ».
NEAR_RADIUS_KM = 10.0
NEAR_PATTERN = re.compile(r"\b(?:pres de|autour de|a cote de|vers|non loin de|pas loin de|pas trop loin de"
                          r"|aux alentours de|proche de)\s+(?:l |la |le )?$")
RADIUS_PATTERN = re.compile(r"\b(?:a moins de|dans un rayon de|a)\s+(\d{1,2})\s*(?:km|kilometres?)\s+(?:de |d |autour de )?$")


def find_budget(text: str) -> float | None:
    """Prix maximum demandé (texte passé par `plain`), ou None.

    Un montant explicite (de 1 à 500 €) prime ; « pas cher » vaut `CHEAP_PRICE`.

    Parameters
    ----------
    text : str
        La question passée par `plain` (« € » déjà remplacé par « euros »).

    Returns
    -------
    float | None
        Le prix maximum en euros, ou None.
    """

    for pattern in BUDGET_PATTERNS:
        match = pattern.search(text)
        if match and 0 < int(match.group(1)) <= 500:
            return float(match.group(1))
    if any(f" {word} " in f" {text} " for word in CHEAP_WORDS):
        return CHEAP_PRICE
    return None


def find_setting(text: str) -> str | None:
    """« interieur » (pluie, froid), « exterieur » (plein air), ou None.

    Parameters
    ----------
    text : str
        La question passée par `plain`.

    Returns
    -------
    str | None
        « interieur », « exterieur » ou None ; l'intérieur l'emporte si les deux sont dits.
    """

    padded = f" {text} "
    if any(f" {word} " in padded for word in INDOOR_WORDS):
        return "interieur"
    if any(f" {word} " in padded for word in OUTDOOR_WORDS):
        return "exterieur"
    return None


def find_near(text: str, cities: Sequence[str]) -> tuple[str, float] | None:
    """Commune citée comme point de repère (« près de Sète ») et rayon en km.

    Parameters
    ----------
    text : str
        La question passée par `plain`.
    cities : Sequence[str]
        Les communes trouvées dans la question (`find_places`).

    Returns
    -------
    tuple[str, float] | None
        La commune et le rayon (écrit, ou `NEAR_RADIUS_KM`), ou None si aucune
        commune n'est précédée de « près de », « à 10 km de »…
    """

    padded = f" {text} "
    for city in cities:
        index = padded.find(f" {city} ")
        if index < 0:
            continue
        before = padded[:index + 1]
        radius = RADIUS_PATTERN.search(before)
        if radius:
            return city, float(radius.group(1))
        if NEAR_PATTERN.search(before):
            return city, NEAR_RADIUS_KM
    return None


def find_public(text: str) -> str | None:
    """Public nommé sans ambiguïté dans la question (famille, ados, seniors, soirée).

    Parameters
    ----------
    text : str
        La question passée par `plain`.

    Returns
    -------
    str | None
        Le public (clé de `PUBLIC_WORDS`), ou None.
    """
    padded = f" {text} "
    for public, words in PUBLIC_WORDS.items():
        if any(f" {word} " in padded for word in words):
            return public
    return None


# Négations (02/11/2026) : « tout sauf du théâtre » cherchait du théâtre, « un
# concert mais pas de rock ni d'électro » proposait du rock, « mais pas à
# Montpellier » ajoutait Montpellier. Le mot qui suit devient une exclusion et
# disparaît du reste de la lecture.
NEGATION = re.compile(r"\b(sauf|excepte|exceptes|hormis|a part|pas|sans|ni|evite|eviter)\b")
NEGATED_ARTICLES = frozenset({"de", "d", "du", "des", "le", "la", "les", "l", "un", "une", "a", "au", "aux",
                              "en", "dans", "sur", "pour", "trop", "forcement", "vraiment", "ce", "cette",
                              "ces", "mon", "ma", "mes", "ton", "ta", "tes"})
# « pas cher », « pas loin de Sète », « sans payer » : pas des exclusions.
NOT_EXCLUDED = frozenset({
    "cher", "chere", "chers", "cheres", "loin", "plus", "moins", "payer", "payant", "payante", "payants",
    "payantes", "mal", "besoin", "encore", "tres", "si", "grand", "souci", "probleme", "voiture",
    "reservation", "reserver", "inscription", "attendre", "attente", "frais", "bouger", "sortir", "aller",
    "faire", "beaucoup", "envie", "possible", "important", "obligatoire", "presse", "pressee",
    "pleuvoir", "pleut", "pluie", "mouiller", "sous", "plein", "budget", "argent", "voir", "rien",
    "seulement", "que", "qu", "tout", "toujours", "jamais", "assez",
})


NEGATED_ZONES = {"bassin de thau": "thau", "etang de thau": "thau", "metropole de montpellier": "montpellier",
                 "metropole montpellieraine": "montpellier"}


def find_exclusions(text: str) -> tuple[str, dict[str, tuple[str, ...]]]:
    """Exclusions écrites (« sauf », « pas de », « ni », « sans », « mais pas à »).

    Parameters
    ----------
    text : str
        La question passée par `plain`.

    Returns
    -------
    tuple[str, dict[str, tuple[str, ...]]]
        Le texte sans les exclusions (le reste de la question), et ce qui est
        exclu : `categories` (« theatre »), `words` (« rock »), `cities`,
        `zones`. Un genre du type demandé (« un concert mais pas de rock ») est
        un mot exclu, pas une catégorie.
    """

    words = text.split()
    removed: set[int] = set()
    objects: list[tuple[str, tuple[str, ...]]] = []     # (sorte, valeurs)
    negated = False
    last_end, last_kind = -1, ""                         # « sauf à Sète et Mèze », « pas de rock et de jazz »
    index = 0
    while index < len(words):
        word = words[index]
        trigger = 2 if word == "a" and index + 1 < len(words) and words[index + 1] == "part" else 1
        continued = word in ("et", "ou") and index == last_end
        if not continued and (not NEGATION.fullmatch(" ".join(words[index:index + trigger]))
                              or (word == "ni" and not negated)):
            index += 1
            continue
        # L'objet : après les articles, une commune (plusieurs mots possibles) ou un mot
        start = index + trigger
        while start < len(words) and words[start] in NEGATED_ARTICLES:
            start += 1
        rest = " ".join(words[start:start + 5])
        place = next((name for name in sorted((*MONTPELLIER_CITIES, *THAU_CITIES, *CITY_ALIASES, "montpellier",
                                               *ZONE_WORDS, *NEGATED_ZONES), key=len, reverse=True)
                      if f"{rest} ".startswith(f"{name} ")), None)
        if place and continued and last_kind != "place":
            index += 1
            continue
        if place:
            count = len(place.split())
            if place == "montpellier" and words[start - 1:start] != ["a"] and words[start - 1:start] != ["en"] \
                    and words[start - 1:start] != ["dans"]:
                objects.append(("zones", ("montpellier",)))
            elif place in NEGATED_ZONES or (place in ZONE_WORDS and place != "montpellier"):
                objects.append(("zones", (NEGATED_ZONES.get(place) or ZONE_WORDS[place],)))
            else:
                objects.append(("cities", CITY_ALIASES.get(place, (place,))))
        elif start < len(words) and words[start] not in NOT_EXCLUDED and words[start] not in STOPWORDS \
                and len(words[start]) > 2 and not words[start].isdigit() \
                and words[start] not in set(WEEKDAYS) | set(MONTH_NAMES) | {"soir", "week", "matin", "nuit"} \
                and not (continued and (last_kind != "word" or words[index + 1] not in {"de", "d", "du", "des"})):
            count = 1
            objects.append(("word", (words[start],)))
        else:
            index += trigger
            continue
        negated = True
        removed.update(range(index, start + count))
        index = last_end = start + count
        last_kind = "word" if objects[-1][0] == "word" else "place"
    positive = " ".join(word for position, word in enumerate(words) if position not in removed)
    # Un type de sortie exclu, sauf s'il est celui demandé (« un concert mais pas de rock »)
    wanted = set(wanted_categories(positive))
    found: dict[str, list[str]] = {"categories": [], "words": [], "cities": [], "zones": []}
    for kind, values in objects:
        if kind != "word":
            found[kind] += [value for value in values if value not in found[kind]]
            continue
        word = values[0]
        types = QUESTION_CATEGORIES.get(word) or QUESTION_CATEGORIES.get(word.rstrip("sx"))
        if types and types[0] not in wanted and word.rstrip("sx") not in PRECISE_SUBJECTS:
            if types[0] not in found["categories"]:
                found["categories"].append(types[0])
        if word not in found["words"]:
            found["words"].append(word)
    return positive, {kind: tuple(values) for kind, values in found.items()}


HOUR_WORD = re.compile(r"\d{1,2}h\d{0,2}|\d{1,2}(?:er|e|eme)")       # « 15h30 », « 1er », « 2e »


def keywords_of(text: str, consumed: set[str]) -> tuple[str, ...]:
    """Mots porteurs de sens qui restent : pour la recherche plein texte.

    Parameters
    ----------
    text : str
        La question passée par `plain`.
    consumed : set[str]
        Les mots déjà compris ailleurs (lieu, date, public…), à ne pas chercher.

    Returns
    -------
    tuple[str, ...]
        Au plus 8 mots, dans l'ordre de la question, sans doublon.
    """

    words = []
    for word in text.split():
        # Mots de 2 lettres ignorés, sauf quelques sujets (« DJ », « BD »).
        if (len(word) < 3 and word not in SHORT_SUBJECTS) or word in STOPWORDS or word in consumed \
                or word.isdigit() or HOUR_WORD.fullmatch(word):
            continue
        if word not in words:
            words.append(word)
    return tuple(words[:8])


# ----------------------------------------------------------------------------
# Le plan de recherche
# ----------------------------------------------------------------------------

@dataclass(slots=True)
class QueryPlan:
    """Ce que la recherche doit respecter, et d'où vient chaque contrainte.

    Attributes
    ----------
    start : datetime
        Début de la période cherchée.
    end : datetime
        Fin exclue de la période.
    period_label : str
        Libellé de la période (« ce week-end », « sam. 18 oct. »).
    public : str | None
        Public demandé (famille, ados, seniors, soirée), ou aucun.
    zone : str | None
        Zone demandée (« montpellier » ou « thau »), ou aucune.
    cities : tuple[str, ...]
        Communes demandées, formes simplifiées.
    free_only : bool
        Vrai pour ne garder que le gratuit.
    evening : bool
        Vrai pour les séances du soir (à partir de `EVENING_HOUR`).
    moment : str | None
        « matin », « apres-midi », « journee » (le soir : `evening`).
    period_from_question : bool
        Vrai si la date vient de la question (« samedi »).
    categories : tuple[str, ...]
        Types de sortie acceptés.
    keywords : tuple[str, ...]
        Mots pour la recherche plein texte.
    practice : str | None
        Pratique à faire soi-même (« chanter »), voir `PRACTICE_VERBS`.
    watch : bool
        Vrai pour regarder, pas pratiquer (« un spectacle de danse »).
    max_price : float | None
        Prix maximum en euros (« moins de 15 € »).
    setting : str | None
        « interieur » (s'il pleut) ou « exterieur » (plein air).
    near : tuple[str, float] | None
        Commune de repère et rayon en km (« près de Sète »).
    excluded_categories, excluded_words, excluded_cities, excluded_zones : tuple[str, ...]
        Ce que la question exclut (« sauf du théâtre », « mais pas à Montpellier »).
    outside : tuple[str, ...]
        Lieux nommés hors de la zone couverte.
    from_question : set[str]
        Contraintes lues dans la question (et non cochées), relâchables s'il n'y a
        aucun résultat.
    """

    start: datetime
    end: datetime
    period_label: str
    public: str | None = None
    zone: str | None = None
    cities: tuple[str, ...] = ()
    free_only: bool = False
    evening: bool = False
    moment: str | None = None                # « matin », « apres-midi », « journee » (le soir : `evening`)
    period_from_question: bool = False       # la date vient de la question (« samedi »)
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    practice: str | None = None              # « chanter » : le faire soi-même (PRACTICE_VERBS)
    watch: bool = False                      # « un spectacle de danse » : regarder, pas pratiquer
    max_price: float | None = None           # « moins de 15 € » : gratuit ou prix minimum sous ce seuil
    setting: str | None = None               # « interieur » (s'il pleut) ou « exterieur » (plein air)
    near: tuple[str, float] | None = None    # (commune, rayon en km) : « près de Sète »
    # Exclusions (02/11/2026) : « sauf du théâtre », « pas de rock », « mais pas à Montpellier »
    excluded_categories: tuple[str, ...] = ()
    excluded_words: tuple[str, ...] = ()
    excluded_cities: tuple[str, ...] = ()
    excluded_zones: tuple[str, ...] = ()
    outside: tuple[str, ...] = ()           # lieux hors zone nommés (« Agde »), dits dans la réponse
    # Contraintes lues dans la question (et non cochées) : elles peuvent être
    # relâchées si elles ne laissent aucun résultat.
    from_question: set[str] = field(default_factory=set)

    def describe(self) -> str:
        """Résumé lisible : « sam. 18 oct. · Sète · famille · gratuit · théâtre ou spectacle ».

        Returns
        -------
        str
            Les contraintes du plan, séparées par « · ».
        """

        # Quand : la période et le moment de la journée
        moment = "soir" if self.evening else self.moment
        parts = [self.period_label + (f" ({MOMENT_LABELS[moment]})" if moment else "")]
        # Où : rayon autour d'une commune, communes, ou zone
        if self.near:
            parts.append(f"à moins de {self.near[1]:g} km de {city_label(self.near[0])}")
        elif self.cities:
            parts.append(", ".join(city_label(city) for city in self.cities))
        elif self.zone:
            parts.append({"montpellier": "Montpellier et sa métropole", "thau": "bassin de Thau"}[self.zone])
        # Pour qui, combien, cadre, puis quoi (pratique ou type de sortie)
        if self.public:
            parts.append({"soiree": "soirée"}.get(self.public, self.public))
        if self.free_only:
            parts.append("gratuit")
        elif self.max_price is not None:
            parts.append(f"moins de {self.max_price:g} €")
        if self.setting:
            parts.append({"interieur": "en intérieur", "exterieur": "en plein air"}[self.setting])
        if self.practice:
            parts.append(PRACTICE_LABELS[self.practice] + " soi-même")
        elif self.categories:
            # « soirée électro » : public « soirée » et type « soirée » ; le libellé
            # n'est écrit qu'une fois (« soirée · soirée » le 30/10/2026).
            label = category_label(self.categories)
            if self.public == "soiree" and "soirée" in label and "soirée" in parts:
                parts.remove("soirée")              # « soirée · soirée ou jeux » aussi
            if label not in parts:
                parts.append(label)
        # Ce qui est exclu, puis les lieux hors zone
        excluded = [CATEGORY_LABELS.get(category, category) for category in self.excluded_categories]
        excluded += [word for word in self.excluded_words
                     if (QUESTION_CATEGORIES.get(word) or QUESTION_CATEGORIES.get(word.rstrip("sx"), ("",)))[0]
                     not in self.excluded_categories]
        excluded += [city_label(city) for city in self.excluded_cities]
        excluded += [{"montpellier": "Montpellier et sa métropole", "thau": "bassin de Thau"}[zone]
                     for zone in self.excluded_zones]
        if excluded:
            parts.append("sauf " + ", ".join(excluded))
        if self.outside:
            parts.append(", ".join(self.outside) + " (hors zone)")
        return " · ".join(parts)

    def relaxed(self) -> QueryPlan:
        """Le même plan sans les contraintes lues dans la question (sauf la date).

        Utilisé quand elles ne laissent aucun résultat. La date est gardée, avec
        son origine (`period_from_question`) : l'interface coche encore la date
        lue dans la question. Le type de sortie, les mots-clés et la pratique sont
        gardés aussi ; le moment de la journée (soir, matin…), les communes, le
        budget, le cadre et le rayon sont retirés.

        Returns
        -------
        QueryPlan
            Un nouveau plan, sans contrainte marquée comme venant de la question.
        """

        return QueryPlan(
            start=self.start, end=self.end, period_label=self.period_label,
            public=None if "public" in self.from_question else self.public,
            zone=None if "zone" in self.from_question else self.zone,
            cities=(), free_only=False if "free" in self.from_question else self.free_only,
            evening=False, moment=None, period_from_question=self.period_from_question,
            categories=self.categories, keywords=self.keywords, from_question=set(),
            max_price=None, setting=None, near=None, practice=self.practice, watch=self.watch,
            excluded_categories=self.excluded_categories, excluded_words=self.excluded_words,
            excluded_cities=self.excluded_cities, excluded_zones=self.excluded_zones, outside=self.outside,
        )


@dataclass(slots=True)
class Understanding:
    """Ce que la question dit, indépendamment des filtres cochés.

    Attributes
    ----------
    period : tuple[datetime, datetime, str] | None
        Période nommée : début, fin exclue, libellé ; ou None.
    public : str | None
        Public nommé, ou None.
    zone : str | None
        Zone nommée (« montpellier » ou « thau »), ou None.
    cities : tuple[str, ...]
        Communes nommées.
    free_only : bool
        Vrai si la question demande du gratuit.
    evening : bool
        Vrai si la question parle du soir.
    moment : str | None
        « matin », « apres-midi » ou « journee », ou None.
    categories : tuple[str, ...]
        Types de sortie nommés.
    keywords : tuple[str, ...]
        Mots restants, pour la recherche plein texte.
    max_price : float | None
        Prix maximum en euros, ou None.
    setting : str | None
        « interieur » ou « exterieur », ou None.
    near : tuple[str, float] | None
        Commune de repère et rayon en km, ou None.
    practice : str | None
        Pratique à faire soi-même (« chanter »), ou None.
    watch : bool
        Vrai si la question parle de regarder (« voir un spectacle »).
    excluded_categories, excluded_words : tuple[str, ...]
        Types de sortie et mots exclus (« sauf du théâtre », « pas de rock »).
    excluded_cities, excluded_zones : tuple[str, ...]
        Communes et zones exclues (« mais pas à Montpellier »).
    outside : tuple[str, ...]
        Lieux nommés hors de la zone couverte (« Agde »), à dire franchement.
    """

    period: tuple[datetime, datetime, str] | None = None
    public: str | None = None
    zone: str | None = None
    cities: tuple[str, ...] = ()
    free_only: bool = False
    evening: bool = False
    moment: str | None = None
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    max_price: float | None = None
    setting: str | None = None
    near: tuple[str, float] | None = None
    practice: str | None = None
    watch: bool = False
    excluded_categories: tuple[str, ...] = ()
    excluded_words: tuple[str, ...] = ()
    excluded_cities: tuple[str, ...] = ()
    excluded_zones: tuple[str, ...] = ()
    outside: tuple[str, ...] = ()


# ----------------------------------------------------------------------------
# Synonymes appris (config/synonymes.yaml, validés par un humain)
# ----------------------------------------------------------------------------

SYNONYMS_FILE = Path(__file__).resolve().parent.parent / "config" / "synonymes.yaml"
_synonyms_cache: dict[str, Any] = {"key": None, "values": {}}


def learned_synonyms(path: Path = SYNONYMS_FILE) -> dict[str, str]:
    """Synonymes et corrections validés : {« zumba » : « fitness »}.

    Relu seulement quand le fichier change : une proposition acceptée sert
    dès la question suivante, sans redémarrer l'API.

    Parameters
    ----------
    path : Path
        Le fichier YAML (clé `synonymes`). Par défaut `config/synonymes.yaml`.

    Returns
    -------
    dict[str, str]
        Une copie des synonymes, mots et cibles passés par `plain` ; vide si le
        fichier est absent ou illisible.
    """

    # Fichier absent : aucun synonyme
    try:
        stat = path.stat()
    except OSError:
        return {}
    # Relire le fichier seulement s'il a changé depuis la dernière lecture
    key = (str(path), stat.st_mtime_ns, stat.st_size)       # fichier, date et taille : relu s'il change
    if _synonyms_cache["key"] != key:
        import yaml

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        values = data.get("synonymes") if isinstance(data, dict) else None
        _synonyms_cache["values"] = {plain(str(word)): plain(str(target))
                                     for word, target in (values or {}).items() if plain(str(word)) and plain(str(target))}
        _synonyms_cache["key"] = key
    return dict(_synonyms_cache["values"])


def with_synonyms(question: str, synonyms: dict[str, str] | None = None) -> str:
    """Ajoute à la question les mots connus de ses synonymes appris.

    « de la zumba » -> « de la zumba fitness ». Le mot tapé reste.

    Parameters
    ----------
    question : str
        La question du visiteur.
    synonyms : dict[str, str] | None
        Les synonymes à appliquer ; par défaut ceux de `learned_synonyms`.

    Returns
    -------
    str
        La question, suivie des mots ajoutés s'il y en a.
    """

    synonyms = learned_synonyms() if synonyms is None else synonyms
    padded = f" {plain(question)} "
    added = [target for word, target in synonyms.items() if f" {word} " in padded and f" {target} " not in padded]
    return f"{question} {' '.join(added)}" if added else question


def understand(question: str, now: datetime) -> Understanding:
    """Lit la question : quand, où, pour qui, combien, quoi, et les mots restants.

    Chaque mot compris (lieu, date, public, prix…) est retiré des mots-clés, qui
    ne gardent que le sujet (« jazz », « échecs »).

    Parameters
    ----------
    question : str
        La question du visiteur, telle qu'il l'a écrite.
    now : datetime
        Heure de référence pour lire les dates (« ce soir », « samedi »).

    Returns
    -------
    Understanding
        Tout ce qui a été lu dans la question.
    """

    # Balises HTML retirées avec leur contenu de script (01/11/2026) :
    # « <script>alert(1)</script> concert » cherchait « script » et « alert ».
    question = HTML_SCRIPT.sub(" ", str(question or ""))
    question = HTML_TAG.sub(" ", question)
    question = TEST_MARKER.sub("", question)
    # Ajouter les synonymes appris, puis simplifier le texte
    question = with_synonyms(question)

    text = plain(question.replace("€", " euros "))      # le signe disparaîtrait avec la ponctuation
    free = any(f" {word} " in f" {text} " for word in FREE_WORDS)      # « pas payant » avant les négations
    # Exclusions (« sauf du théâtre ») : lues puis retirées, le reste est la demande
    text, excluded = find_exclusions(text)
    padded = f" {text} "
    # Où, et les mots de lieu et de date à ne pas chercher
    cities, zone = find_places(text)
    outside = find_outside(text) if not cities else ()
    consumed = {word for city in cities for word in city.split()}
    consumed |= {word for word, _ in ZONE_WORDS.items() if f" {word} " in padded}
    consumed |= set(WEEKDAYS) | set(MONTH_NAMES) | {"soir", "week", "end", "weekend", "semaine", "mois", "jour",
                                                     "we", "matin", "midi", "aprem", "tantot"}
    consumed |= {word for place in OUTSIDE_PLACES for word in place.split() if f" {place} " in padded}
    period = find_period(text, now)
    if period:
        # « vacances de Noël », « nouvel an » : la date, pas un sujet (« marché de Noël » en reste un)
        consumed |= {"noel", "toussaint", "paques", "fetes", "hiver", "printemps", "ete", "an", "nuit"} \
            if any(word in plain(period[2]) for word in ("vacances", "noel", "an", "reveillon")) \
            or " cette nuit " in padded else set()
    # Le moment de la journée, puis le public
    evening_phrase = any(f" {phrase} " in padded for phrase in EVENING_PHRASES)
    moment = None if evening_phrase else find_moment(padded)
    # Une heure écrite (« à 15h », « après 21h ») donne le moment (02/11/2026)
    hour_moment = None if (evening_phrase or moment) else find_hour(text)
    if hour_moment == "soir":
        evening_phrase = True
    elif hour_moment:
        moment = hour_moment
    # « en soirée » est un moment, pas le public « Soirée » : retiré avant de chercher le public.
    public_text = padded
    for phrase in EVENING_PHRASES:
        public_text = public_text.replace(f" {phrase} ", " soir ")
    public = find_public(public_text.strip())
    if evening_phrase:
        consumed |= {"debut", "fin", "soiree"}
    if public:
        consumed |= {word for phrase in PUBLIC_WORDS[public] for word in phrase.split()}
    # Gratuit, type de sortie, pratique, budget, cadre et rayon
    free = free or any(f" {word} " in padded for word in FREE_WORDS)
    consumed |= {"gratuit", "gratuite", "gratuits", "gratuites", "gratos", "payer", "libre", "entree",
                 "payant", "payante", "payants", "payantes"}
    categories = wanted_categories(text)
    practice = find_practice(padded, categories)
    max_price = None if free else find_budget(text)
    if max_price is not None:
        consumed |= {"cher", "chere", "budget", "prix", "euro", "euros", "eur", "max", "maximum", "economique",
                     "marche", "petits"}
    setting = find_setting(text)
    if setting:
        consumed |= WEATHER_CONSUMED
    if any(f" sans {word} " in padded for word in ("voiture", "vehicule", "permis")):
        consumed |= {"voiture", "vehicule", "permis"}             # « sans voiture » : un moyen, pas un sujet
    near = find_near(text, cities)
    if near:
        consumed |= {"pres", "autour", "cote", "loin", "alentours", "proche", "rayon", "km", "kilometres"}
    # Assembler la compréhension ; les mots-clés sont ce qui reste
    return Understanding(
        period=period,
        public=public,
        zone=zone,
        cities=cities,
        free_only=free,
        # « marché nocturne » : le soir aussi (pas « nuit » : « boîte de nuit », « Nuit des musées »).
        evening=evening_phrase or any(f" {word} " in padded for word in (*EVENING_WORDS[:2], "nocturne",
                                                                         "cette nuit", "tonight", "esta noche")),
        moment=moment,
        # Pratique : le verbe est le sujet (les autres mots, « apprendre »,
        # « envie », n'en disent pas plus).
        categories=() if practice else categories,
        # « theatre » est aussi un type de sortie : son mot de recherche est « theatral ».
        keywords=({"theatre": "theatral"}.get(practice, practice),) if practice else keywords_of(
            # « spectacle » effacé par un type précis (« un spectacle de danse ») :
            # pas un mot à chercher non plus.
            text, consumed | ({"spectacle", "spectacles"} if "spectacle" not in categories else set())
            # « regarder », « assister à » disent comment, pas quoi : pas des mots à chercher.
            | (set(WATCH_WORDS) - {"spectacle", "spectacles", "ballet", "ballets"})),
        max_price=max_price,
        setting=setting,
        near=near,
        practice=practice,
        watch=not practice and any(f" {word} " in padded for word in WATCH_WORDS),
        excluded_categories=excluded["categories"],
        excluded_words=excluded["words"],
        excluded_cities=excluded["cities"],
        excluded_zones=excluded["zones"],
        outside=outside,
    )


def combine(previous: Understanding, current: Understanding) -> Understanding:
    """Combine une question de relance avec la demande précédente.

    Question de relance (« et pour les enfants ? ») : ce que la nouvelle
    question précise remplace l'ancienne, le reste est gardé.

    Parameters
    ----------
    previous : Understanding
        Ce qui avait été compris de la demande précédente.
    current : Understanding
        Ce qui est compris de la relance.

    Returns
    -------
    Understanding
        La demande complète, relance appliquée.
    """

    return Understanding(
        period=current.period or previous.period,
        public=current.public or previous.public,
        zone=current.zone or (None if current.cities else previous.zone),
        cities=current.cities or (() if current.zone else previous.cities),
        free_only=current.free_only or previous.free_only,
        evening=current.evening or (previous.evening and not current.period and not current.moment),
        moment=current.moment or (None if current.evening else previous.moment),
        categories=current.categories or previous.categories,
        keywords=current.keywords if (current.categories or current.practice or len(current.keywords) >= 2) else
        tuple(dict.fromkeys((*previous.keywords, *current.keywords)))[:8],
        # Une relance sans nouveau sujet garde la pratique (« et à Sète ? ») ;
        # un nouveau sujet (« un concert », « du jazz et du rock ») la remplace.
        practice=current.practice or (None if (current.categories or current.keywords) else previous.practice),
        watch=current.watch or (previous.watch and not current.practice and not current.categories),
        max_price=current.max_price if current.max_price is not None else
        (None if current.free_only else previous.max_price),
        setting=current.setting or previous.setting,
        near=current.near or (None if (current.cities or current.zone) else previous.near),
        # Les exclusions s'ajoutent (« et sans théâtre ? »)
        excluded_categories=tuple(dict.fromkeys((*previous.excluded_categories, *current.excluded_categories))),
        excluded_words=tuple(dict.fromkeys((*previous.excluded_words, *current.excluded_words))),
        excluded_cities=tuple(dict.fromkeys((*previous.excluded_cities, *current.excluded_cities))),
        excluded_zones=tuple(dict.fromkeys((*previous.excluded_zones, *current.excluded_zones))),
        outside=current.outside or (() if (current.cities or current.zone) else previous.outside),
    )


# Relances (02/11/2026, 200 questions difficiles : 5 relances tapées sur 10
# perdaient la demande précédente). Mots de liaison : toujours une relance ;
# mots de précision : une relance s'ils ne nomment pas un nouveau sujet.
FOLLOW_UP_STARTS = frozenset({"et", "plutot", "mais", "sinon", "aussi", "pareil", "meme", "ou", "encore",
                              "seulement", "uniquement", "juste", "sauf", "hormis", "excepte"})
REFINING_STARTS = frozenset({"pour", "avec", "sans", "a", "en", "dans", "sur", "plus", "moins", "pas", "vers",
                             "pres", "autour", "apres", "avant", "s", "si"})
# Marque des questions de test (« [t] … », exclues des statistiques) : ignorée pour comprendre.
TEST_MARKER = re.compile(r"^\s*\[t\]\s*", re.IGNORECASE)


def is_follow_up(question: str) -> bool:
    """Vrai pour une relance courte qui dépend de la question précédente.

    « et pour les enfants ? », « plutôt samedi », « moins cher », « à Sète ? ».

    Parameters
    ----------
    question : str
        La nouvelle question du visiteur.

    Returns
    -------
    bool
        Vrai si elle commence par un mot de liaison (« et », « plutôt »…), ou si
        elle est courte (4 mots au plus) et ne nomme aucun sujet.
    """

    text = plain(TEST_MARKER.sub("", str(question or "")))
    words = text.split()
    if not words:
        return False
    if words[0] in FOLLOW_UP_STARTS:
        return True
    # Une question courte qui nomme un nouveau sujet (« photo », « du jazz »,
    # « un concert ») est une nouvelle demande, pas une relance (15/10/2026) :
    # « photo » après « du cirque » cherchait cirque ET photo.
    understood = understand(question, datetime.now(PARIS))
    if understood.categories or understood.keywords:
        return False
    # Sans sujet, une question courte ne fait que préciser (« plutôt samedi »), et
    # jusqu'à 8 mots si elle commence par une préposition (02/11/2026 : « à moins
    # de 10 euros ? », « pour les petits ? ») ; « que faire ce week-end à Sète »
    # reste une nouvelle demande.
    return len(words) <= 4 or (len(words) <= 8 and words[0] in REFINING_STARTS)


def merge_with_filters(understanding: Understanding, *, public: str | None, zone: str | None,
                       period_bounds: tuple[datetime, datetime], period_label: str,
                       free_only: bool) -> QueryPlan:
    """Fusionne la question avec les filtres de l'interface.

    - Date : la question précise la période cochée (intersection) ; si les
      deux ne se recoupent pas (« samedi » avec « aujourd'hui » coché), la
      question l'emporte : c'est ce que l'utilisateur vient d'écrire.
    - Lieu : une commune nommée l'emporte sur la zone cochée.
    - Public, gratuit : la case cochée d'abord, sinon la question.
    - Moment (le soir, le matin, l'après-midi, en journée) : seulement écrit.

    Parameters
    ----------
    understanding : Understanding
        Ce qui a été lu dans la question (`understand`, ou `combine` pour une relance).
    public : str | None
        Public coché, ou aucun.
    zone : str | None
        Zone cochée, ou aucune.
    period_bounds : tuple[datetime, datetime]
        Début et fin exclue de la période cochée.
    period_label : str
        Libellé de la période cochée.
    free_only : bool
        Vrai si « gratuit » est coché.

    Returns
    -------
    QueryPlan
        Le plan de recherche, avec la liste des contraintes venues de la question.
    """

    # Date : la question précise la période cochée, ou la remplace
    start, end = period_bounds
    label = period_label
    from_question: set[str] = set()
    if understanding.period:
        q_start, q_end, q_label = understanding.period
        if max(start, q_start) < min(end, q_end):
            start, end = max(start, q_start), min(end, q_end)
        else:
            start, end = q_start, q_end
        label = q_label
    # Lieu : rayon autour d'une commune, communes nommées, ou zone
    plan_zone, cities = zone, understanding.cities
    near = understanding.near
    if near:
        plan_zone, cities = None, ()
        from_question.add("near")
    elif cities:
        # « à Montpellier » garde aussi sa zone (la métropole), pour élargir à
        # la métropole avant de lâcher le lieu ; « à Sète » n'en a pas.
        plan_zone = understanding.zone
        from_question.add("cities")
        if plan_zone:
            from_question.add("zone")
    elif understanding.zone and not zone:
        plan_zone = understanding.zone
        from_question.add("zone")
    # Public et gratuit : la case cochée d'abord, sinon la question
    plan_public = public
    if not public and understanding.public:
        plan_public = understanding.public
        from_question.add("public")
    plan_free = free_only
    if not free_only and understanding.free_only:
        plan_free = True
        from_question.add("free")
    # Noter les autres contraintes écrites (relâchables)
    if understanding.max_price is not None and not plan_free:
        from_question.add("budget")
    if understanding.setting:
        from_question.add("setting")
    if understanding.evening or understanding.moment:
        from_question.add("moment")
    # Assembler le plan
    return QueryPlan(
        start=start, end=end, period_label=label, public=plan_public, zone=plan_zone, cities=cities,
        free_only=plan_free, evening=understanding.evening, moment=understanding.moment,
        period_from_question=understanding.period is not None, categories=understanding.categories,
        keywords=understanding.keywords, from_question=from_question,
        max_price=None if plan_free else understanding.max_price, setting=understanding.setting, near=near,
        practice=understanding.practice, watch=understanding.watch,
        excluded_categories=understanding.excluded_categories, excluded_words=understanding.excluded_words,
        excluded_cities=understanding.excluded_cities, excluded_zones=understanding.excluded_zones,
        outside=understanding.outside,
    )
