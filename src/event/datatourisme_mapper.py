"""Conversion d'un événement de l'API DATAtourisme en `Event` SortirIA.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), sur le
modèle de `src/event/openagenda_mapper.py`. La structure des champs a été
relevée sur de vraies réponses de `GET /v1/entertainmentAndEvent`
(`scripts/explore_datatourisme.py`, 791 événements le 14/10/2026).

Particularités de DATAtourisme :

- les textes sont rangés par langue avec un « @ » (`{"@fr": "..."}`) et
  beaucoup de valeurs sont des listes, même quand il n'y en a qu'une ;
- un même événement a plusieurs séances (`takesPlaceAt`), chacune avec ses
  dates. On produit **un `Event` par séance** : un film projeté trois fois
  devient trois propositions, chacune à la bonne date ;
- les séances n'ont qu'un jour ; l'heure est écrite en clair dans
  `openingDetails` (« Mercredi 14 octobre 2026 de 19h30 à 21h10. ») : on la
  relit avec une expression régulière ;
- la licence demande de citer le producteur de la fiche (`hasBeenCreatedBy`,
  ex. « 34 - ADT34 ») et la date de mise à jour (`lastUpdate`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any

from src.event.event_audiences import infer_audiences
from src.event.event_normalizer import (
    PARIS,
    build_dedup_key,
    build_event_id,
    clean_text,
    end_of_day,
    normalize_city,
    to_iso,
)
from src.event.event_schema import Event
from src.utils.geo_utils import to_float
from src.utils.url_utils import is_valid_http_url

# Types DATAtourisme trop généraux pour servir de catégorie.
GENERIC_TYPES = frozenset({"Event", "PointOfInterest", "EntertainmentAndEvent", "CulturalEvent"})

# Libellés français des types les plus courants (ontologie DATAtourisme).
TYPE_LABELS: dict[str, str] = {
    "TheaterEvent": "Théâtre",
    "ShowEvent": "Spectacle",
    "Concert": "Concert",
    "ScreeningEvent": "Cinéma",
    "ExhibitionEvent": "Exposition",
    "Exhibition": "Exposition",
    "Festival": "Festival",
    "SportsEvent": "Sport",
    "SportsCompetition": "Sport",
    "SaleEvent": "Marché",
    "Market": "Marché",
    "VisitEvent": "Visite",
    "TraditionalCelebration": "Fête traditionnelle",
    "LocalAnimation": "Animation locale",
    "Conference": "Conférence",
    "WorkshopEvent": "Atelier",
}

MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12, "décembre": 12,
}

# « 14 octobre 2026 de 19h30 à 21h10 », « 3 novembre 2026 à 20h », « de 9h à 12h ».
_DATED_TIME = re.compile(
    r"(\d{1,2})(?:er)?\s+([a-zéèûô]+)\s+(\d{4})\s*(?:,\s*)?(?:de|à|a)\s*(\d{1,2})\s*h\s*(\d{2})?"
    r"(?:\s*(?:à|a|-)\s*(\d{1,2})\s*h\s*(\d{2})?)?",
    re.IGNORECASE,
)
_ANY_TIME = re.compile(r"(?:de|à|a)\s*(\d{1,2})\s*h\s*(\d{2})?(?:\s*(?:à|a|-)\s*(\d{1,2})\s*h\s*(\d{2})?)?", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Lecture des valeurs DATAtourisme
# ---------------------------------------------------------------------------

def first(value: Any) -> Any:
    """Premier élément d'une liste, ou la valeur elle-même.

    Parameters
    ----------
    value : Any
        Une valeur DATAtourisme, liste ou non.

    Returns
    -------
    Any
        Le premier élément (None pour une liste vide), ou la valeur si ce n'est
        pas une liste.
    """

    if isinstance(value, list):
        return value[0] if value else None
    return value


def pick_text(value: Any, language: str = "fr") -> str:
    """Texte dans la langue voulue (`{"@fr": ...}`), sinon la première langue.

    Parameters
    ----------
    value : Any
        Texte simple, dictionnaire par langue, ou liste de l'un ou l'autre.
    language : str
        Langue voulue. Par défaut « fr ».

    Returns
    -------
    str
        Le texte nettoyé, ou "" s'il n'y en a dans aucune langue.
    """

    value = first(value)
    if isinstance(value, Mapping):
        wanted = value.get(f"@{language}", value.get(language))
        if clean_text(first(wanted)):
            return clean_text(first(wanted))
        for text in value.values():
            if clean_text(first(text)):
                return clean_text(first(text))
        return ""
    return clean_text(value)


def get_location(item: Mapping[str, Any]) -> dict[str, Any]:
    """Lieu : nom de rue, ville, code postal, coordonnées.

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme (`isLocatedAt`).

    Returns
    -------
    dict[str, Any]
        Clés `address`, `city`, `postal_code`, `latitude` et `longitude`
        (coordonnées à None si absentes).
    """

    place = first(item.get("isLocatedAt")) or {}
    address = first(place.get("address")) or {}
    city = address.get("addressLocality") or pick_text((address.get("hasAddressCity") or {}).get("label"))
    geo = place.get("geo") or {}
    return {
        "address": clean_text(first(address.get("streetAddress"))),
        "city": normalize_city(city),
        "postal_code": clean_text(address.get("postalCode")),
        "latitude": to_float(first(geo.get("latitude"))),
        "longitude": to_float(first(geo.get("longitude"))),
    }


def get_price(item: Mapping[str, Any]) -> tuple[str, float | None, float | None, bool | None]:
    """(texte, prix minimum, prix maximum, gratuit ?) à partir de `offers`.

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme.

    Returns
    -------
    tuple[str, float | None, float | None, bool | None]
        Texte affiché (« 12 € », « de 5 à 10 € », « Gratuit »), prix minimum,
        prix maximum, et gratuité ; `("", None, None, None)` sans tarif.
    """

    # Relever tous les prix minimum et maximum des offres
    minimums: list[float] = []
    maximums: list[float] = []
    for offer in item.get("offers") or []:
        for spec in (offer or {}).get("priceSpecification") or []:
            low, high = to_float(first(spec.get("minPrice"))), to_float(first(spec.get("maxPrice")))
            if low is not None:
                minimums.append(low)
            if high is not None:
                maximums.append(high)
    # Résumer en une fourchette et un texte
    if not minimums and not maximums:
        return "", None, None, None
    price_min = min(minimums or maximums)
    price_max = max(maximums or minimums)
    if price_max == 0:
        return "Gratuit", 0.0, 0.0, True
    text = f"{price_min:g} €" if price_min == price_max else f"de {price_min:g} à {price_max:g} €"
    return text, price_min, price_max, price_min == 0


def get_themes(item: Mapping[str, Any]) -> list[str]:
    """Libellés des thèmes (« Cinéma », « Théâtre »...).

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme (`hasTheme`).

    Returns
    -------
    list[str]
        Les libellés, sans doublons ni vides, dans l'ordre de la fiche.
    """

    labels = [pick_text(theme.get("label")) for theme in item.get("hasTheme") or [] if isinstance(theme, Mapping)]
    return [label for label in dict.fromkeys(labels) if label]


def get_category(item: Mapping[str, Any]) -> str:
    """Catégorie : premier thème, sinon le type précis traduit (« Théâtre »).

    Les types non génériques sont essayés dans l'ordre : le premier qui a un
    libellé dans `TYPE_LABELS` donne la catégorie (un type sans libellé est
    sauté au lieu de laisser la catégorie vide).

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme.

    Returns
    -------
    str
        La catégorie, ou "".
    """

    themes = get_themes(item)
    if themes:
        return themes[0]
    for kind in item.get("type") or []:
        if kind not in GENERIC_TYPES and kind in TYPE_LABELS:
            return TYPE_LABELS[kind]
    return ""


def get_image_url(item: Mapping[str, Any]) -> str:
    """URL de l'image principale (présente sur ~14 % des événements).

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme (`hasMainRepresentation`).

    Returns
    -------
    str
        La première URL HTTP valide trouvée, ou "".
    """

    for representation in item.get("hasMainRepresentation") or []:
        for resource in (representation or {}).get("hasRelatedResource") or []:
            url = clean_text(first((resource or {}).get("locator")))
            if is_valid_http_url(url):
                return url
    return ""


def get_url(item: Mapping[str, Any]) -> str:
    """Lien affiché : site de l'organisateur, sinon la fiche DATAtourisme.

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme (`hasContact`, `uri`).

    Returns
    -------
    str
        Le premier site valide d'un contact, sinon l'URI de la fiche.
    """

    for contact in item.get("hasContact") or []:
        url = clean_text(first((contact or {}).get("homepage")))
        if is_valid_http_url(url):
            return url
    return clean_text(item.get("uri"))


def get_producer(item: Mapping[str, Any]) -> str:
    """Producteur de la fiche, à citer (« 34 - ADT34 »).

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme (`hasBeenCreatedBy`).

    Returns
    -------
    str
        La raison sociale du producteur, ou "".
    """

    return clean_text((item.get("hasBeenCreatedBy") or {}).get("legalName"))


# ---------------------------------------------------------------------------
# Séances et heures
# ---------------------------------------------------------------------------

def parse_opening_times(text: str) -> dict[date, tuple[time, time | None]]:
    """Heures par jour lues dans `openingDetails`.

    « Mercredi 14 octobre 2026 de 19h30 à 21h10. » donne
    `{date(2026, 10, 14): (time(19, 30), time(21, 10))}`.

    Une date ou une heure impossible est ignorée ; pour un jour cité deux fois,
    la première heure est gardée.

    Parameters
    ----------
    text : str
        Le texte `openingDetails` d'une séance.

    Returns
    -------
    dict[date, tuple[time, time | None]]
        Pour chaque jour, l'heure de début et l'heure de fin (None si absente).
    """

    times: dict[date, tuple[time, time | None]] = {}
    for match in _DATED_TIME.finditer(text or ""):
        day, month_name, year, start_h, start_m, end_h, end_m = match.groups()
        month = MONTHS.get(month_name.lower())
        try:
            when = date(int(year), month, int(day)) if month else None
            start = time(int(start_h), int(start_m or 0))
            end = time(int(end_h), int(end_m or 0)) if end_h else None
        except ValueError:
            continue
        if when:
            times.setdefault(when, (start, end))
    return times


def single_time(text: str) -> tuple[time, time | None] | None:
    """Heure unique quand le texte n'en donne qu'une, sans date (« de 9h à 12h »).

    Parameters
    ----------
    text : str
        Le texte `openingDetails` d'une séance.

    Returns
    -------
    tuple[time, time | None] | None
        Début et fin (None si absente) ; None si le texte donne zéro ou
        plusieurs horaires différents, ou une heure impossible.
    """

    matches = list(_ANY_TIME.finditer(text or ""))
    distinct = {(m.group(1), m.group(2), m.group(3), m.group(4)) for m in matches}
    if len(distinct) != 1:
        return None
    start_h, start_m, end_h, end_m = distinct.pop()
    try:
        return time(int(start_h), int(start_m or 0)), (time(int(end_h), int(end_m or 0)) if end_h else None)
    except ValueError:
        return None


def get_occurrences(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Séances : début, fin, heure connue ? (une entrée par `takesPlaceAt`).

    L'heure vient d'abord de la date citée dans `openingDetails`, puis, pour
    une séance d'un seul jour, d'un horaire unique sans date. Sans heure, la
    séance commence à minuit et `time_known` vaut False.

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme.

    Returns
    -------
    list[dict[str, Any]]
        Une entrée par séance lisible : `start_at`, `end_at` (ISO 8601, ou "")
        et `time_known`.
    """

    occurrences = []
    for slot in item.get("takesPlaceAt") or []:
        if not isinstance(slot, Mapping):
            continue
        # Jours de début et de fin (fin absente : même jour)
        try:
            start_day = date.fromisoformat(str(slot.get("startDate"))[:10])
        except ValueError:
            continue
        try:
            end_day = date.fromisoformat(str(slot.get("endDate"))[:10])
        except ValueError:
            end_day = start_day
        # Chercher l'heure dans le texte des horaires
        details = pick_text(slot.get("openingDetails"))
        found = parse_opening_times(details).get(start_day)
        if found is None and start_day == end_day:
            found = single_time(details)
        # Heure trouvée : début et fin précis ; sinon séance à la journée
        if found:
            start_time, end_time = found
            start = datetime.combine(start_day, start_time, tzinfo=PARIS)
            if end_time:
                end = datetime.combine(end_day, end_time, tzinfo=PARIS)
            elif end_day > start_day:
                end = end_of_day(end_day)   # plusieurs jours, heure de fin inconnue
            else:
                end = None
            if end is not None and end < start:
                end = None
            occurrences.append({"start_at": to_iso(start), "end_at": to_iso(end), "time_known": True})
        else:
            occurrences.append({
                "start_at": to_iso(datetime.combine(start_day, time.min, tzinfo=PARIS)),
                "end_at": "" if end_day == start_day else to_iso(datetime.combine(end_day, time.min, tzinfo=PARIS)),
                "time_known": False,
            })
    return occurrences


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def map_datatourisme_event(
    item: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    *,
    source: str = "datatourisme",
    extracted_at: str = "",
    language: str = "fr",
) -> Event | None:
    """Transforme une séance d'un événement DATAtourisme en `Event`.

    L'identifiant est calculé à partir du titre, du début de la séance et du
    lieu (ou de la ville) : chaque séance est un `Event` distinct.

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme.
    occurrence : Mapping[str, Any]
        Une séance produite par `get_occurrences`.
    source : str
        Identifiant de la source. Par défaut « datatourisme ».
    extracted_at : str
        Date de l'extraction (ISO 8601).
    language : str
        Langue des textes à lire. Par défaut « fr ».

    Returns
    -------
    Event | None
        L'événement, ou None si la fiche n'a pas de titre.
    """

    # Titre obligatoire
    title = pick_text(item.get("label"), language)
    if not title:
        return None

    # Lire description, lieu, prix, thèmes et types, nom du lieu
    description_block = first(item.get("hasDescription")) or {}
    description = pick_text(description_block.get("description"), language)
    short_description = pick_text(description_block.get("shortDescription"), language)
    location = get_location(item)
    price_text, price_min, price_max, is_free = get_price(item)
    themes = get_themes(item)
    category = get_category(item)
    precise_types = [TYPE_LABELS.get(kind, kind) for kind in item.get("type") or [] if kind not in GENERIC_TYPES]
    keywords = list(dict.fromkeys([*themes, *precise_types]))
    venue_name = pick_text(first(item.get("hasContact") or [{}]).get("legalName")) if item.get("hasContact") else ""
    # Le contact porte souvent le nom de l'événement, pas celui du lieu.
    if venue_name.casefold() == title.casefold():
        venue_name = ""

    # Identifiants : clé de dédoublonnage, identifiant de la séance chez la source
    start_at = occurrence["start_at"]
    dedup_key = build_dedup_key(title, start_at, venue_name or location["city"])
    uuid = clean_text(item.get("uuid"))

    return Event(
        event_id=build_event_id(dedup_key),
        source=source,
        source_event_id=f"{uuid}@{start_at[:10]}" if uuid else "",
        title=title,
        description=description or short_description,
        category=category,
        keywords=keywords,
        language=language,
        start_at=start_at,
        end_at=occurrence.get("end_at", ""),
        time_known=bool(occurrence.get("time_known")),
        venue_name=venue_name,
        address=location["address"],
        city=location["city"],
        postal_code=location["postal_code"],
        latitude=location["latitude"],
        longitude=location["longitude"],
        price_text=price_text,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        # Pas la description : « drame familial » ferait passer un film pour
        # une sortie en famille.
        audiences=infer_audiences([title, category, *keywords], start_at, bool(occurrence.get("time_known")), None, None),
        url=get_url(item),
        image_url=get_image_url(item),
        source_credit=get_producer(item),
        source_updated_at=clean_text(item.get("lastUpdate"))[:10],
        extracted_at=extracted_at,
    )


def map_datatourisme_occurrences(item: Mapping[str, Any], **kwargs: Any) -> list[Event]:
    """Tous les `Event` d'un événement (un par séance).

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement DATAtourisme.
    **kwargs : Any
        Transmis à `map_datatourisme_event` (`source`, `extracted_at`, `language`).

    Returns
    -------
    list[Event]
        Les événements des séances lisibles ; vide si la fiche n'a pas de titre.
    """

    events = [map_datatourisme_event(item, occurrence, **kwargs) for occurrence in get_occurrences(item)]
    return [event for event in events if event is not None]

