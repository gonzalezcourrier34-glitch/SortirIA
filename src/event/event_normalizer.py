"""Nettoyage des champs d'un événement : textes, dates, prix et lieux.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). La détection
des prix reprend l'idée de `LexicalService.extract_price_info` de l'Assistant
RAG OpenAgenda (projet 6), réécrite pour renvoyer des montants chiffrés.

Toutes les fonctions sont pures (pas de réseau, pas de base) : elles sont
faciles à tester et réutilisables par n'importe quelle source.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

# Fuseau de référence : les sorties ont lieu à Montpellier.
PARIS = ZoneInfo("Europe/Paris")

_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"\s+")

# Un montant suivi ou précédé du symbole euro ou du mot « euro(s) ».
_PRICE_RE = re.compile(
    r"(?:€\s*(\d+(?:[.,]\d{1,2})?))|(?:(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b|euros?\b))",
    re.IGNORECASE,
)
_FREE_WORDS = ("gratuit", "entree libre", "entree gratuite", "acces libre", "free")


# ---------------------------------------------------------------------------
# Textes
# ---------------------------------------------------------------------------

def clean_text(value: Any) -> str:
    """Retire le HTML, décode les entités (&amp;...) et compacte les espaces."""

    if value is None:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    return _SPACES_RE.sub(" ", text).strip()


def pick_localized(value: Any, language: str = "fr") -> str:
    """Retourne le texte dans la langue voulue.

    OpenAgenda renvoie souvent `{"fr": "...", "en": "..."}`. On prend la langue
    demandée, sinon la première valeur non vide. Une simple chaîne est renvoyée
    telle quelle (nettoyée).
    """

    if isinstance(value, dict):
        if clean_text(value.get(language)):
            return clean_text(value.get(language))
        for text in value.values():
            if clean_text(text):
                return clean_text(text)
        return ""
    return clean_text(value)


def normalize_for_comparison(value: Any) -> str:
    """Version « sans accents, minuscules, sans ponctuation » d'un texte.

    Sert à comparer deux titres ou deux lieux : « Théâtre Jean-Vilar » et
    « theatre jean vilar » donnent la même chaîne.
    """

    text = unicodedata.normalize("NFKD", clean_text(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


# Petits mots qui restent en minuscules dans un nom de commune
# (« Castelnau-le-Lez », « Saint-Jean-de-Védas »), sauf en première position.
# (« Lez » n'en fait pas partie : c'est le nom de la rivière.)
CITY_PARTICLES = frozenset({"le", "la", "les", "de", "du", "des", "sur", "sous", "en", "et"})


def normalize_city(value: Any) -> str:
    """Met une ville en forme lisible : « MONTPELLIER » devient « Montpellier ».

    Une ville déjà écrite en casse mixte (« Palavas-les-Flots ») est gardée
    telle quelle : la source l'a correctement écrite. Seules les villes tout
    en majuscules ou tout en minuscules sont reformatées. (Corrigé le
    14/10/2026 : on obtenait « Saint-Jean-De-Védas ».)
    """

    city = clean_text(value)
    if not city:
        return ""
    if city != city.upper() and city != city.lower():
        return city

    def format_part(part: str, position: int) -> str:
        if position > 0 and part in CITY_PARTICLES:
            return part
        if part.startswith(("l'", "d'")) and len(part) > 2:
            return part[:2] + part[2:].capitalize() if position > 0 else part[0].upper() + "'" + part[2:].capitalize()
        return part.capitalize()

    parts = re.split(r"([ -])", city.lower())
    words = [part for part in parts if part not in (" ", "-")]
    formatted = iter(format_part(word, index) for index, word in enumerate(words))
    return "".join(part if part in (" ", "-") else next(formatted) for part in parts)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_event_datetime(value: Any) -> datetime | None:
    """Convertit une date ISO (avec ou sans heure) en datetime à l'heure de Paris.

    - « 2026-10-24T18:30:00.000Z » : heure UTC, convertie en heure de Paris ;
    - « 2026-10-24T20:30:00 » : heure locale sans fuseau, considérée comme Paris ;
    - « 2026-10-24 » : jour seul, ramené à minuit (voir `has_time`).
    Renvoie None si la valeur est vide ou illisible.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = clean_text(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PARIS)
    return parsed.astimezone(PARIS)


def has_time(value: Any) -> bool:
    """Indique si la valeur brute contient une heure (et pas seulement un jour)."""

    if isinstance(value, datetime):
        return True
    return "T" in clean_text(value)


def to_iso(value: datetime | None) -> str:
    """Formate une date en ISO 8601 (chaîne vide si None)."""

    return value.isoformat() if value else ""


# ---------------------------------------------------------------------------
# Prix
# ---------------------------------------------------------------------------

def parse_price(text: Any) -> tuple[float | None, float | None, bool | None]:
    """Extrait (prix minimum, prix maximum, gratuit ?) d'un texte libre.

    Exemples :
    - « Gratuit » -> (0.0, 0.0, True)
    - « 12 € » -> (12.0, 12.0, False)
    - « De 8 à 15 euros » -> (8.0, 15.0, False)
    - « Tarif : 12,50€ / réduit 8€ » -> (8.0, 12.5, False)
    - « Sur réservation » -> (None, None, None) : on ne sait pas.
    """

    raw = clean_text(text)
    if not raw:
        return None, None, None

    amounts = [
        float((first or second).replace(",", "."))
        for first, second in _PRICE_RE.findall(raw)
    ]
    # « De 8 à 15 euros » : seul 15 est collé à « euros » ; on récupère aussi
    # les nombres d'une fourchette « de X à Y ».
    range_match = re.search(r"de\s+(\d+(?:[.,]\d{1,2})?)\s+[aà]\s+(\d+(?:[.,]\d{1,2})?)", raw, re.IGNORECASE)
    if range_match and amounts:
        amounts += [float(group.replace(",", ".")) for group in range_match.groups()]

    positive = [amount for amount in amounts if amount > 0]
    if positive:
        return min(positive), max(positive), False

    comparable = normalize_for_comparison(raw)
    if any(word in comparable for word in _FREE_WORDS) or amounts == [0.0]:
        return 0.0, 0.0, True

    return None, None, None


# ---------------------------------------------------------------------------
# Identité et déduplication
# ---------------------------------------------------------------------------

def build_dedup_key(title: Any, start_at: Any, place: Any) -> str:
    """Clé qui identifie un même événement, quelle que soit la source.

    Titre + jour de début + lieu, normalisés : deux agendas qui annoncent
    « Concert Jazz au Jam » le 24/10 au « Jam » produisent la même clé.
    """

    start = parse_event_datetime(start_at)
    day = start.date().isoformat() if start else ""
    return "|".join(
        (normalize_for_comparison(title), day, normalize_for_comparison(place))
    )


def build_event_id(dedup_key: str) -> str:
    """Identifiant stable (16 caractères hexadécimaux) tiré de la clé de déduplication."""

    return hashlib.sha1(dedup_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
