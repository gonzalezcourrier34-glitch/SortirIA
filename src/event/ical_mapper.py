"""Lecture d'un calendrier iCal (.ics) et conversion en `Event` SortirIA.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), sur le
modèle de `src/event/openagenda_mapper.py` et `datatourisme_mapper.py`.

Beaucoup de lieux (salles de concert, théâtres, associations) publient leur
programmation en iCal, souvent via un agenda Google. C'est la voie prévue
pour les **partenaires** : un lieu donne l'adresse de son flux, on l'ajoute
dans `ical_sources` de config/sources.yaml, sans écrire de code.

Le format iCal (RFC 5545) est simple, on le lit donc sans dépendance :

- une ligne trop longue continue sur la suivante, qui commence par un espace ;
- chaque propriété s'écrit `NOM;PARAMETRE=valeur:contenu`
  (ex. `DTSTART;TZID=Europe/Paris:20261024T203000`) ;
- `\\n`, `\\,` et `\\;` sont des caractères échappés ;
- un événement répété porte une règle `RRULE` : on développe les cas
  courants (tous les jours, toutes les semaines, éventuellement certains
  jours) dans la fenêtre de dates, et on retire les dates `EXDATE`.

Un calendrier de lieu ne répète pas l'adresse à chaque événement : la
configuration de la source peut donc fournir le lieu par défaut
(`venue_name`, `city`, `latitude`, `longitude`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.event.event_audiences import infer_audiences
from src.event.event_normalizer import (
    PARIS,
    build_dedup_key,
    build_event_id,
    clean_text,
    normalize_city,
    parse_price,
    to_iso,
)
from src.event.event_schema import Event
from src.utils.geo_utils import to_float
from src.utils.url_utils import is_valid_http_url

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
MAX_OCCURRENCES = 366  # garde-fou contre une règle de répétition sans fin


# ---------------------------------------------------------------------------
# Lecture du fichier
# ---------------------------------------------------------------------------

def unfold_lines(text: str) -> list[str]:
    """Recolle les lignes continuées (qui commencent par un espace ou une tabulation).

    Parameters
    ----------
    text : str
        Contenu brut du fichier .ics (fins de ligne quelconques).

    Returns
    -------
    list[str]
        Les lignes logiques, sans les lignes vides.
    """

    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    """Retire les échappements iCal (`\\n`, `\\,`, `\\;`, `\\\\`).

    Parameters
    ----------
    value : str
        Valeur d'une propriété iCal.

    Returns
    -------
    str
        La valeur avec de vrais sauts de ligne, virgules et points-virgules.
    """

    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def parse_property(line: str) -> tuple[str, dict[str, str], str]:
    """`DTSTART;TZID=Europe/Paris:20261024T203000` -> (nom, paramètres, valeur).

    Parameters
    ----------
    line : str
        Une ligne logique du calendrier (déjà recollée).

    Returns
    -------
    tuple[str, dict[str, str], str]
        Le nom de la propriété en majuscules, ses paramètres (clés en
        majuscules, guillemets retirés) et sa valeur brute ; valeur vide si la
        ligne n'a pas de deux-points.
    """

    # Les deux-points d'un paramètre entre guillemets ne séparent pas la valeur.
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ":" and not in_quotes:
            head, value = line[:index], line[index + 1:]
            break
    else:
        return line.upper(), {}, ""
    # Séparer le nom de ses paramètres `CLE=valeur`
    name, *raw_params = head.split(";")
    params = {}
    for raw in raw_params:
        key, _, param_value = raw.partition("=")
        params[key.upper()] = param_value.strip('"')
    return name.upper(), params, value


def parse_calendar(text: str) -> list[dict[str, Any]]:
    """Liste des événements (VEVENT) d'un calendrier.

    Chaque événement est un dictionnaire `{NOM: (paramètres, valeur)}` ;
    `EXDATE`, qui peut apparaître plusieurs fois, est une liste.

    Parameters
    ----------
    text : str
        Contenu brut du fichier .ics.

    Returns
    -------
    list[dict[str, Any]]
        Les événements du calendrier ; les blocs imbriqués (VALARM...) sont ignorés.
    """

    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    depth = 0  # pour ignorer les blocs imbriqués (VALARM...)
    for line in unfold_lines(text):
        name, params, value = parse_property(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current, depth = {"EXDATE": []}, 0
        elif current is not None and name == "BEGIN":
            depth += 1
        elif current is not None and name == "END" and value.upper() != "VEVENT":
            depth -= 1
        elif current is not None and name == "END":
            events.append(current)
            current = None
        elif current is not None and depth == 0:
            if name == "EXDATE":
                current["EXDATE"].append((params, value))
            else:
                current.setdefault(name, (params, value))
    return events


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_ical_datetime(params: Mapping[str, str], value: str) -> tuple[datetime | None, bool]:
    """Date iCal -> (datetime à l'heure de Paris, heure connue ?).

    - `20261024` ou `VALUE=DATE` : jour seul ;
    - `20261024T183000Z` : heure UTC ;
    - `TZID=Europe/Paris:20261024T203000` : heure dans ce fuseau ;
    - `20261024T203000` sans fuseau : heure « flottante », lue à Paris.

    Parameters
    ----------
    params : Mapping[str, str]
        Paramètres de la propriété (`VALUE`, `TZID`).
    value : str
        Valeur de la date.

    Returns
    -------
    tuple[datetime or None, bool]
        La date à l'heure de Paris (None si illisible) et vrai si l'heure est connue.
        Un fuseau inconnu est remplacé par Paris.
    """

    # Lire les trois écritures possibles : jour seul, UTC, heure locale
    value = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
            day = datetime.strptime(value[:8], "%Y%m%d").date()  # jour seul
            return datetime.combine(day, time.min, tzinfo=PARIS), False
        if value.endswith("Z"):
            moment = datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            return moment.astimezone(PARIS), True
        moment = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")  # fuseau ajouté plus bas
    except ValueError:
        return None, False
    # Appliquer le fuseau TZID s'il est reconnu, sinon Paris
    zone = PARIS
    if params.get("TZID"):
        try:
            zone = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            zone = PARIS
    return moment.replace(tzinfo=zone).astimezone(PARIS), True


def parse_rrule(value: str) -> dict[str, str]:
    """`FREQ=WEEKLY;BYDAY=TU,TH;COUNT=10` -> dictionnaire.

    Parameters
    ----------
    value : str
        Valeur de la propriété `RRULE`.

    Returns
    -------
    dict[str, str]
        Les parties de la règle, clés en majuscules.
    """

    return {key.upper(): val for key, _, val in (part.partition("=") for part in value.split(";")) if key}


def expand_occurrences(
    start: datetime,
    rrule: str,
    exdates: list[datetime],
    window_end: datetime,
) -> list[datetime]:
    """Dates de début d'un événement répété, jusqu'à `window_end`.

    Cas gérés : FREQ=DAILY ou WEEKLY, INTERVAL, COUNT, UNTIL, BYDAY (pour
    WEEKLY). Une règle plus rare (mensuelle...) ne donne que la première date :
    mieux vaut une proposition de moins qu'une date inventée.

    Parameters
    ----------
    start : datetime
        Début de la première séance (`DTSTART`).
    rrule : str
        Règle de répétition brute ; vide pour un événement non répété.
    exdates : list[datetime]
        Dates exclues (`EXDATE`), comparées à la minute près.
    window_end : datetime
        Fin de la fenêtre de collecte : aucune date au-delà.

    Returns
    -------
    list[datetime]
        Les débuts des séances, dans l'ordre, au plus `MAX_OCCURRENCES`.
    """

    # Lire la règle ; une fréquence non gérée ne donne que la première date
    if not rrule:
        return [start]
    rule = parse_rrule(rrule)
    frequency = rule.get("FREQ", "").upper()
    if frequency not in ("DAILY", "WEEKLY"):
        return [start]
    interval = max(int(rule.get("INTERVAL", "1") or 1), 1)
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        until, _ = parse_ical_datetime({}, rule["UNTIL"])
    last = min(dt for dt in (window_end, until) if dt is not None)
    excluded = {dt.replace(second=0, microsecond=0) for dt in exdates}

    # Jours de la semaine concernés (BYDAY), par défaut celui du début
    days = sorted({WEEKDAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS})
    if frequency == "WEEKLY" and not days:
        days = [start.weekday()]

    # Avancer jour par jour ou semaine par semaine jusqu'à la limite (fin, COUNT, garde-fou)
    occurrences: list[datetime] = []
    produced = 0
    week_start = start - timedelta(days=start.weekday())
    step = 0
    while produced < MAX_OCCURRENCES:
        if frequency == "DAILY":
            candidates = [start + timedelta(days=step * interval)]
        else:
            base = week_start + timedelta(weeks=step * interval)
            candidates = [
                datetime.combine((base + timedelta(days=d)).date(), start.timetz())
                for d in days
            ]
        stop = False
        for candidate in candidates:
            if candidate < start:
                continue
            if candidate > last or (count is not None and produced >= count):
                stop = True
                break
            # Une date exclue compte quand même pour COUNT (comme dans la RFC)
            produced += 1
            if candidate.replace(second=0, microsecond=0) not in excluded:
                occurrences.append(candidate)
        if stop:
            break
        step += 1
    return occurrences


# ---------------------------------------------------------------------------
# Occurrences d'un événement
# ---------------------------------------------------------------------------

def _value(vevent: Mapping[str, Any], name: str) -> str:
    """Valeur texte d'une propriété d'un événement, échappements retirés.

    Parameters
    ----------
    vevent : Mapping[str, Any]
        Événement lu par `parse_calendar`.
    name : str
        Nom de la propriété (« SUMMARY », « URL »...).

    Returns
    -------
    str
        La valeur nettoyée, ou une chaîne vide si la propriété est absente.
    """
    entry = vevent.get(name)
    return unescape(entry[1]).strip() if entry else ""


def get_occurrences(
    vevent: Mapping[str, Any],
    today: date,
    days_forward: int,
) -> list[dict[str, Any]]:
    """Séances d'un événement dans la fenêtre [aujourd'hui ; + days_forward].

    Parameters
    ----------
    vevent : Mapping[str, Any]
        Événement lu par `parse_calendar`.
    today : date
        Premier jour de la fenêtre.
    days_forward : int
        Nombre de jours couverts après aujourd'hui.

    Returns
    -------
    list[dict[str, Any]]
        Une entrée par séance : `start_at`, `end_at` (ISO, vide si inconnue)
        et `time_known`. Vide si l'événement n'a pas de début lisible.
    """

    # Lire le début et la fin de la première séance
    if not vevent.get("DTSTART"):
        return []
    start, time_known = parse_ical_datetime(*vevent["DTSTART"])
    if start is None:
        return []
    end = None
    if vevent.get("DTEND"):
        end, _ = parse_ical_datetime(*vevent["DTEND"])
    # En iCal, la fin d'un événement « journée entière » est EXCLUSIVE : DTEND
    # = le lendemain du dernier jour. On la ramène au dernier jour, et à rien
    # du tout pour un événement d'une seule journée (même convention que les
    # autres sources). Avant le 15/10/2026, seul le cas d'une journée était
    # corrigé : un événement de 3 jours restait visible un jour de trop.
    if end is not None and not time_known:
        end = end - timedelta(days=1)
        if end <= start:
            end = None
    duration = (end - start) if end is not None and end >= start else None

    # Fixer la fenêtre et lire les dates exclues
    window_start = datetime.combine(today, time.min, tzinfo=PARIS)
    window_end = window_start + timedelta(days=days_forward + 1)
    exdates = [dt for params, value in vevent.get("EXDATE", []) for dt in [parse_ical_datetime(params, value.split(",")[0])[0]] if dt]

    # Développer la répétition et garder les séances qui touchent la fenêtre
    occurrences = []
    for occurrence_start in expand_occurrences(start, _value(vevent, "RRULE"), exdates, window_end):
        occurrence_end = occurrence_start + duration if duration is not None else None
        if (occurrence_end or occurrence_start) < window_start or occurrence_start >= window_end:
            continue
        occurrences.append({
            "start_at": to_iso(occurrence_start),
            "end_at": to_iso(occurrence_end),
            "time_known": time_known,
        })
    return occurrences


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def map_ical_event(
    vevent: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    extracted_at: str = "",
) -> Event | None:
    """Transforme une séance d'un événement iCal en `Event`.

    Parameters
    ----------
    vevent : Mapping[str, Any]
        Événement lu par `parse_calendar`.
    occurrence : Mapping[str, Any]
        Séance produite par `get_occurrences` (`start_at`, `end_at`, `time_known`).
    source : Mapping[str, Any]
        Configuration de la source : lieu par défaut, coordonnées, site web,
        catégorie par défaut, langue, crédit.
    extracted_at : str
        Date de l'extraction, recopiée dans l'événement. Par défaut vide.

    Returns
    -------
    Event or None
        L'événement, ou None s'il n'a pas de titre.
    """

    title = _value(vevent, "SUMMARY")
    if not title:
        return None

    description = clean_text(_value(vevent, "DESCRIPTION"))
    location = _value(vevent, "LOCATION")
    categories = [clean_text(c) for c in _value(vevent, "CATEGORIES").split(",") if clean_text(c)]

    # Lieu : celui de l'événement s'il est donné, sinon celui de la source.
    venue_name = clean_text(source.get("venue_name"))
    address = clean_text(source.get("address"))
    if location:
        first_part, _, rest = location.partition(",")
        venue_name = clean_text(first_part) or venue_name
        address = clean_text(rest) or address
    city = normalize_city(source.get("city"))
    latitude, longitude = to_float(source.get("latitude")), to_float(source.get("longitude"))
    if vevent.get("GEO"):
        lat_text, _, lon_text = vevent["GEO"][1].partition(";")
        latitude, longitude = to_float(lat_text) or latitude, to_float(lon_text) or longitude

    # Lien (sinon le site de la source), statut, catégorie et prix
    url = _value(vevent, "URL")
    if not is_valid_http_url(url):
        url = clean_text(source.get("website"))

    status_text = _value(vevent, "STATUS").upper()
    status = "annule" if status_text == "CANCELLED" else "programme"

    start_at = occurrence["start_at"]
    category = categories[0] if categories else clean_text(source.get("default_category"))
    price_min, price_max, is_free = parse_price(description)
    uid = _value(vevent, "UID")
    dedup_key = build_dedup_key(title, start_at, venue_name or city)

    # Construire l'événement de cette séance
    return Event(
        event_id=build_event_id(dedup_key),
        # Tous les calendriers sont rangés sous la source « ical » déclarée
        # en base (sql/sortiria/01_sources.sql) ; le lieu est cité dans
        # source_credit. source_id de la config sert seulement au moteur.
        source="ical",
        source_event_id=f"{uid}@{start_at[:10]}" if uid else "",
        title=title,
        description=description,
        category=category,
        keywords=categories,
        language=clean_text(source.get("language")) or "fr",
        start_at=start_at,
        end_at=occurrence.get("end_at", ""),
        time_known=bool(occurrence.get("time_known")),
        venue_name=venue_name,
        address=address,
        city=city,
        postal_code=clean_text(source.get("postal_code")),
        latitude=latitude,
        longitude=longitude,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        status=status,
        audiences=infer_audiences([title, category, *categories], start_at, bool(occurrence.get("time_known")), None, None),
        url=url,
        # Le lieu qui publie le calendrier, cité avec la source.
        source_credit=clean_text(source.get("credit") or source.get("name")),
        extracted_at=extracted_at,
    )
