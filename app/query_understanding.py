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
    """Minuscules, sans accents, ponctuation remplacée par des espaces :
    « Théâtre à Sète ? » -> « theatre a sete »."""

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
    "projection": ("cinema",), "expo": ("exposition",), "exposition": ("exposition",),
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
# le sujet ; app/search_service.py (PRACTICES) dit ce qui y répond. Types qui
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
PRACTICE_LABELS = {"chanter": "pour chanter", "danser": "pour danser", "jouer": "pour jouer",
                   "peindre": "pour peindre", "dessiner": "pour dessiner", "sculpter": "pour sculpter",
                   "cuisiner": "pour cuisiner"}


def find_practice(padded: str, categories: tuple[str, ...]) -> str | None:
    """« je veux chanter » -> « chanter » ; rien si la question nomme un autre type."""

    for verb, allowed in PRACTICE_VERBS.items():
        if f" {verb} " in padded and set(categories) <= allowed:
            return verb
    return None
CATEGORY_LABELS = {"theatre": "théâtre", "cinema": "cinéma", "conference": "conférence",
                   "marche": "marché", "soiree": "soirée", "fete": "fête"}


def wanted_categories(question: str) -> tuple[str, ...]:
    """Catégories désignées par la question : « j'aimerais aller au théâtre »
    -> ("theatre", "spectacle"). Vide si la question n'en nomme aucune."""

    # Dans l'ordre des mots : le type nommé d'abord (« théâtre ou spectacle »).
    found: dict[str, None] = {}
    for word in plain(question).split():
        for key in (word, word.rstrip("sx")):
            found.update(dict.fromkeys(QUESTION_CATEGORIES.get(key, ())))
    return tuple(found)


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
    """« théâtre ou spectacle » pour l'affichage des étapes."""

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
    "vic": ("vic la gardiole",), "palavas": (), "carnon": (),
}
# « Montpellier » désigne en pratique la métropole : c'est une zone, pas une commune.
ZONE_WORDS = {
    "montpellier": "montpellier", "metropole": "montpellier",
    "thau": "thau", "etang": "thau", "bassin": "thau",
}
CITY_LABELS = {"sete": "Sète", "meze": "Mèze", "perols": "Pérols", "fabregues": "Fabrègues",
               "laverune": "Lavérune", "le cres": "Le Crès", "saint bres": "Saint-Brès"}


def city_label(city: str) -> str:
    return CITY_LABELS.get(city, city.title().replace(" Le ", "-le-").replace(" Les ", "-les-"))


# « à Montpellier », « dans Montpellier » : la ville elle-même (16/10/2026).
# Sans préposition, ou « près de / autour de Montpellier », c'est la métropole.
# Grille de pertinence : « des spectacles gratuits à Montpellier » proposait
# Mauguio et Palavas-les-Flots (zone de 20 km).
MONTPELLIER_ITSELF = (" a montpellier ", " dans montpellier ", " en ville a montpellier ",
                      " montpellier centre ", " centre de montpellier ", " centre ville de montpellier ")


def find_places(text: str) -> tuple[tuple[str, ...], str | None]:
    """Communes et zone nommées dans la question (texte déjà passé par `plain`)."""

    padded = f" {text} "
    cities: list[str] = []
    if any(marker in padded for marker in MONTPELLIER_ITSELF):
        cities.append("montpellier")
    for city in (*MONTPELLIER_CITIES, *THAU_CITIES):
        if f" {city} " in padded:
            cities.append(city)
    for alias, targets in CITY_ALIASES.items():
        if f" {alias} " in padded:
            cities += [target for target in targets if target not in cities]
    zone = None
    for word, value in ZONE_WORDS.items():
        if f" {word} " in padded:
            zone = value
            break
    return tuple(cities), zone


def zone_of_city(city: str) -> str:
    return "thau" if city in THAU_CITIES else "montpellier"


# ----------------------------------------------------------------------------
# Dates
# ----------------------------------------------------------------------------

def day_bounds(day: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS)
    return max(start, now), start + timedelta(days=1)


def day_label(day: date) -> str:
    return f"{DAY_LABELS[day.weekday()]} {day.day} {MONTH_LABELS[day.month - 1]}"


def next_weekday(today: date, weekday: int, *, strictly_after: bool = False) -> date:
    delta = (weekday - today.weekday()) % 7
    if strictly_after and delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def find_period(text: str, now: datetime) -> tuple[datetime, datetime, str] | None:
    """Période nommée dans la question : (début, fin exclue, libellé), ou None.

    `text` est déjà passé par `plain`. Les formes les plus précises sont
    cherchées d'abord (« demain soir » avant « demain »).
    """

    now = now.astimezone(PARIS)
    today = now.date()
    padded = f" {text} "

    def has(*words: str) -> bool:
        return any(f" {word} " in padded for word in words)

    if has("apres demain"):
        day = today + timedelta(days=2)
        return (*day_bounds(day, now), day_label(day))
    if has("demain"):
        day = today + timedelta(days=1)
        return (*day_bounds(day, now), "demain")
    if has("aujourd hui", "ce jour", "ce soir", "cet apres midi", "tout de suite", "maintenant"):
        return (*day_bounds(today, now), "aujourd'hui")
    if has("week end prochain", "weekend prochain"):
        saturday = next_weekday(today, 5, strictly_after=True)
        if today.weekday() == 6:
            saturday = today + timedelta(days=6)
        start = datetime.combine(saturday, datetime.min.time(), tzinfo=PARIS)
        return start, start + timedelta(days=2), "le week-end prochain"
    if has("ce week end", "ce weekend", "week end", "weekend"):
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
    match = re.search(r"\b(\d{1,2}) (" + "|".join(MONTH_NAMES) + r")\b", text)
    numeric = re.search(r"(?<![\d/])(\d{1,2}) (\d{1,2})(?![\d/])", text) if " le " in padded else None
    if match or numeric:
        day_number = int((match or numeric).group(1))
        month = MONTH_NAMES.index(match.group(2)) + 1 if match else int(numeric.group(2))
        try:
            day = date(today.year, month, day_number)
            if day < today - timedelta(days=1):
                day = date(today.year + 1, month, day_number)
        except ValueError:
            day = None
        if day:
            return (*day_bounds(day, now), day_label(day))
    for index, name in enumerate(WEEKDAYS):
        if has(name, name + "s"):
            day = next_weekday(today, index, strictly_after=has(f"{name} prochain"))
            return (*day_bounds(day, now), day_label(day))
    for index, name in enumerate(MONTH_NAMES):
        if has(f"en {name}", f"mois de {name}", f"debut {name}", f"fin {name}") or (
                name not in ("mars", "mai") and has(name)):
            year = today.year if index + 1 >= today.month else today.year + 1
            start = datetime(year, index + 1, 1, tzinfo=PARIS)
            end = datetime(year + (index == 11), (index + 1) % 12 + 1, 1, tzinfo=PARIS)
            return max(start, now), end, f"en {name.replace('fevrier', 'février').replace('aout', 'août').replace('decembre', 'décembre')}"
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
                "mes fils", "petits enfants", "petite enfance"),
    "ados": ("ado", "ados", "adolescent", "adolescents", "adolescente", "adolescentes", "lyceen", "lyceens",
             "collegien", "collegiens", "jeunes"),
    "seniors": ("senior", "seniors", "retraite", "retraites", "aines", "personnes agees", "grand mere",
                "grand pere", "grands parents"),
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
    """« samedi matin » -> « matin », « en journée » -> « journee » (sans le soir)."""

    for moment, phrases in MOMENT_PHRASES.items():
        if any(f" {phrase} " in padded for phrase in phrases):
            return moment
    return None
FREE_WORDS = ("gratuit", "gratuite", "gratuits", "gratuites", "gratos", "sans payer", "entree libre")

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
"""
STOPWORDS = frozenset(STOPWORD_TEXT.split())


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
NEAR_PATTERN = re.compile(r"\b(?:pres de|autour de|a cote de|vers|non loin de|aux alentours de|proche de)\s+(?:l |la |le )?$")
RADIUS_PATTERN = re.compile(r"\b(?:a moins de|dans un rayon de|a)\s+(\d{1,2})\s*(?:km|kilometres?)\s+(?:de |d |autour de )?$")


def find_budget(text: str) -> float | None:
    """Prix maximum demandé (texte passé par `plain`), ou None."""

    for pattern in BUDGET_PATTERNS:
        match = pattern.search(text)
        if match and 0 < int(match.group(1)) <= 500:
            return float(match.group(1))
    if any(f" {word} " in f" {text} " for word in CHEAP_WORDS):
        return CHEAP_PRICE
    return None


def find_setting(text: str) -> str | None:
    """« interieur » (pluie, froid), « exterieur » (plein air), ou None."""

    padded = f" {text} "
    if any(f" {word} " in padded for word in INDOOR_WORDS):
        return "interieur"
    if any(f" {word} " in padded for word in OUTDOOR_WORDS):
        return "exterieur"
    return None


def find_near(text: str, cities: Sequence[str]) -> tuple[str, float] | None:
    """Commune citée comme point de repère (« près de Sète ») et rayon en km."""

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
    padded = f" {text} "
    for public, words in PUBLIC_WORDS.items():
        if any(f" {word} " in padded for word in words):
            return public
    return None


def keywords_of(text: str, consumed: set[str]) -> tuple[str, ...]:
    """Mots porteurs de sens qui restent : pour la recherche plein texte."""

    words = []
    for word in text.split():
        # Mots de 2 lettres ignorés, sauf quelques sujets (« DJ », « BD »).
        if (len(word) < 3 and word not in SHORT_SUBJECTS) or word in STOPWORDS or word in consumed \
                or word.isdigit():
            continue
        if word not in words:
            words.append(word)
    return tuple(words[:8])


# ----------------------------------------------------------------------------
# Le plan de recherche
# ----------------------------------------------------------------------------

@dataclass(slots=True)
class QueryPlan:
    """Ce que la recherche doit respecter, et d'où vient chaque contrainte."""

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
    max_price: float | None = None           # « moins de 15 € » : gratuit ou prix minimum sous ce seuil
    setting: str | None = None               # « interieur » (s'il pleut) ou « exterieur » (plein air)
    near: tuple[str, float] | None = None    # (commune, rayon en km) : « près de Sète »
    # Contraintes lues dans la question (et non cochées) : elles peuvent être
    # relâchées si elles ne laissent aucun résultat.
    from_question: set[str] = field(default_factory=set)

    def describe(self) -> str:
        """Résumé lisible : « sam. 18 oct. · Sète · famille · gratuit · théâtre ou spectacle »."""

        moment = "soir" if self.evening else self.moment
        parts = [self.period_label + (f" ({MOMENT_LABELS[moment]})" if moment else "")]
        if self.near:
            parts.append(f"à moins de {self.near[1]:g} km de {city_label(self.near[0])}")
        elif self.cities:
            parts.append(", ".join(city_label(city) for city in self.cities))
        elif self.zone:
            parts.append({"montpellier": "Montpellier et sa métropole", "thau": "bassin de Thau"}[self.zone])
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
            parts.append(category_label(self.categories))
        return " · ".join(parts)

    def relaxed(self) -> QueryPlan:
        """Le même plan sans les contraintes lues dans la question (sauf la date) :
        utilisé quand elles ne laissent aucun résultat."""

        return QueryPlan(
            start=self.start, end=self.end, period_label=self.period_label,
            public=None if "public" in self.from_question else self.public,
            zone=None if "zone" in self.from_question else self.zone,
            cities=(), free_only=False if "free" in self.from_question else self.free_only,
            evening=False, categories=self.categories, keywords=self.keywords, from_question=set(),
            max_price=None, setting=None, near=None, practice=self.practice,
        )


@dataclass(slots=True)
class Understanding:
    """Ce que la question dit, indépendamment des filtres cochés."""

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

    def is_empty(self) -> bool:
        return not (self.period or self.public or self.zone or self.cities or self.free_only
                    or self.categories or self.keywords or self.max_price is not None or self.setting
                    or self.near)


# ----------------------------------------------------------------------------
# Synonymes appris (config/synonymes.yaml, validés par un humain)
# ----------------------------------------------------------------------------

SYNONYMS_FILE = Path(__file__).resolve().parent.parent / "config" / "synonymes.yaml"
_synonyms_cache: dict[str, Any] = {"key": None, "values": {}}


def learned_synonyms(path: Path = SYNONYMS_FILE) -> dict[str, str]:
    """Synonymes et corrections validés : {« zumba » : « fitness »}.

    Relu seulement quand le fichier change : une proposition acceptée sert
    dès la question suivante, sans redémarrer l'API.
    """

    try:
        stat = path.stat()
    except OSError:
        return {}
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
    """Ajoute à la question les mots connus de ses synonymes appris :
    « de la zumba » -> « de la zumba fitness ». Le mot tapé reste."""

    synonyms = learned_synonyms() if synonyms is None else synonyms
    padded = f" {plain(question)} "
    added = [target for word, target in synonyms.items() if f" {word} " in padded and f" {target} " not in padded]
    return f"{question} {' '.join(added)}" if added else question


def understand(question: str, now: datetime) -> Understanding:
    """Lit la question : quand, où, pour qui, combien, quoi, et les mots restants."""

    question = with_synonyms(question)

    text = plain(question.replace("€", " euros "))      # le signe disparaîtrait avec la ponctuation
    padded = f" {text} "
    cities, zone = find_places(text)
    consumed = {word for city in cities for word in city.split()}
    consumed |= {word for word, _ in ZONE_WORDS.items() if f" {word} " in padded}
    consumed |= set(WEEKDAYS) | set(MONTH_NAMES) | {"soir", "week", "end", "weekend", "semaine", "mois", "jour"}
    evening_phrase = any(f" {phrase} " in padded for phrase in EVENING_PHRASES)
    moment = None if evening_phrase else find_moment(padded)
    # « en soirée » est un moment, pas le public « Soirée » : retiré avant de chercher le public.
    public_text = padded
    for phrase in EVENING_PHRASES:
        public_text = public_text.replace(f" {phrase} ", " soir ")
    public = find_public(public_text.strip())
    if evening_phrase:
        consumed |= {"debut", "fin", "soiree"}
    if public:
        consumed |= {word for phrase in PUBLIC_WORDS[public] for word in phrase.split()}
    free = any(f" {word} " in padded for word in FREE_WORDS)
    consumed |= {"gratuit", "gratuite", "gratuits", "gratuites", "gratos", "payer", "libre", "entree"}
    categories = wanted_categories(question)
    practice = find_practice(padded, categories)
    max_price = None if free else find_budget(text)
    if max_price is not None:
        consumed |= {"cher", "chere", "budget", "prix", "euro", "euros", "eur", "max", "maximum", "economique",
                     "marche", "petits"}
    setting = find_setting(text)
    if setting:
        consumed |= WEATHER_CONSUMED
    near = find_near(text, cities)
    if near:
        consumed |= {"pres", "autour", "cote", "loin", "alentours", "proche", "rayon", "km", "kilometres"}
    return Understanding(
        period=find_period(text, now),
        public=public,
        zone=zone,
        cities=cities,
        free_only=free,
        # « marché nocturne » : le soir aussi (pas « nuit » : « boîte de nuit », « Nuit des musées »).
        evening=evening_phrase or any(f" {word} " in padded for word in (*EVENING_WORDS[:2], "nocturne")),
        moment=moment,
        # Pratique : le verbe est le sujet (les autres mots, « apprendre »,
        # « envie », n'en disent pas plus).
        categories=() if practice else categories,
        keywords=(practice,) if practice else keywords_of(text, consumed),
        max_price=max_price,
        setting=setting,
        near=near,
        practice=practice,
    )


def combine(previous: Understanding, current: Understanding) -> Understanding:
    """Question de relance (« et pour les enfants ? ») : ce que la nouvelle
    question précise remplace l'ancienne, le reste est gardé."""

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
        max_price=current.max_price if current.max_price is not None else
        (None if current.free_only else previous.max_price),
        setting=current.setting or previous.setting,
        near=current.near or (None if (current.cities or current.zone) else previous.near),
    )


def is_follow_up(question: str) -> bool:
    """Vrai pour une relance courte qui dépend de la question précédente :
    « et pour les enfants ? », « plutôt samedi », « moins cher », « à Sète ? »."""

    text = plain(question)
    words = text.split()
    if not words:
        return False
    if words[0] in {"et", "plutot", "mais", "sinon", "aussi", "pareil", "meme", "ou"}:
        return True
    # Une question courte qui nomme un nouveau sujet (« photo », « du jazz »,
    # « un concert ») est une nouvelle demande, pas une relance (15/10/2026) :
    # « photo » après « du cirque » cherchait cirque ET photo.
    if len(words) > 4:
        return False
    understood = understand(question, datetime.now(PARIS))
    return not understood.categories and not understood.keywords


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
    """

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
    plan_public = public
    if not public and understanding.public:
        plan_public = understanding.public
        from_question.add("public")
    plan_free = free_only
    if not free_only and understanding.free_only:
        plan_free = True
        from_question.add("free")
    if understanding.max_price is not None and not plan_free:
        from_question.add("budget")
    if understanding.setting:
        from_question.add("setting")
    if understanding.evening or understanding.moment:
        from_question.add("moment")
    return QueryPlan(
        start=start, end=end, period_label=label, public=plan_public, zone=plan_zone, cities=cities,
        free_only=plan_free, evening=understanding.evening, moment=understanding.moment,
        period_from_question=understanding.period is not None, categories=understanding.categories,
        keywords=understanding.keywords, from_question=from_question,
        max_price=None if plan_free else understanding.max_price, setting=understanding.setting, near=near,
        practice=understanding.practice,
    )
