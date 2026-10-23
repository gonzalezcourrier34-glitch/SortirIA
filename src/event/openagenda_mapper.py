"""Conversion d'un événement brut OpenAgenda en `Event` SortirIA.

**Provenance** : porté depuis `app/document_service.py` de l'Assistant RAG
OpenAgenda (projet 6, fonction `build_event_document` et `_extract_event_field`).
Le RAG construisait directement un document LangChain ; ici on produit un
`Event` neutre, qui sera stocké en base puis indexé.

OpenAgenda n'a pas toujours la même forme selon l'agenda et la version de
l'API : un champ peut être à plat (« city ») ou imbriqué (« location » > « city »),
et les textes peuvent être multilingues (`{"fr": ...}`). On essaie donc
plusieurs chemins pour chaque champ, comme le faisait le RAG.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.event.event_audiences import infer_audiences
from src.event.event_normalizer import (
    build_dedup_key,
    build_event_id,
    clean_text,
    has_time,
    normalize_city,
    parse_event_datetime,
    parse_price,
    pick_localized,
    to_iso,
)
from src.event.event_schema import Event
from src.utils.geo_utils import to_float
from src.utils.url_utils import is_valid_http_url


def get_path(data: Any, *paths: Sequence[str]) -> Any:
    """Renvoie la première valeur non vide trouvée parmi plusieurs chemins.

    Exemple : `get_path(event, ("city",), ("location", "city"))`.

    Parameters
    ----------
    data : Any
        Dictionnaire (éventuellement imbriqué) à explorer.
    *paths : Sequence[str]
        Chemins de clés à essayer, dans l'ordre.

    Returns
    -------
    Any
        La première valeur ni None, ni vide ; None si aucun chemin n'aboutit.
    """

    for path in paths:
        value: Any = data
        for key in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def build_image_url(image: Any) -> str:
    """Reconstitue l'URL de l'image.

    OpenAgenda donne soit une URL directe, soit `{"base": ..., "filename": ...}` :
    l'URL complète est alors la concaténation des deux.

    Parameters
    ----------
    image : Any
        Champ image de l'événement (URL ou dictionnaire).

    Returns
    -------
    str
        L'URL de l'image, ou une chaîne vide si elle n'est pas une URL http(s) valide.
    """

    if isinstance(image, Mapping):
        base = clean_text(image.get("base"))
        filename = clean_text(image.get("filename"))
        if base and filename:
            url = base.rstrip("/") + "/" + filename.lstrip("/")
        else:
            url = clean_text(image.get("url")) or filename
    else:
        url = clean_text(image)
    # Une image cassée ne doit pas faire rejeter l'événement : on la retire.
    # (Première collecte du 13/10 : 17 événements perdus pour cette raison.)
    return url if is_valid_http_url(url) else ""


def get_tag_labels(item: Mapping[str, Any]) -> list[str]:
    """Libellés des étiquettes OpenAgenda (`tags` puis `tagGroups[].tags`).

    Constat de l'audit du 13/10/2026 : le champ `category` est vide, la
    catégorie est rangée dans les étiquettes (ex. « Exposition »).

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda.

    Returns
    -------
    list[str]
        Les libellés nettoyés, sans doublons, dans l'ordre d'apparition.
    """

    labels: list[str] = []
    tags = list(item.get("tags") or [])
    for group in item.get("tagGroups") or []:
        if isinstance(group, Mapping):
            tags += list(group.get("tags") or [])
    for tag in tags:
        label = clean_text(tag.get("label")) if isinstance(tag, Mapping) else clean_text(tag)
        if label and label not in labels:
            labels.append(label)
    return labels


def get_category(item: Mapping[str, Any], language: str = "fr") -> str:
    """Catégorie : champ `category` s'il est rempli, sinon la première étiquette.

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda.
    language : str
        Langue voulue pour un texte multilingue. Par défaut « fr ».

    Returns
    -------
    str
        La catégorie, ou une chaîne vide.
    """

    category = pick_localized(get_path(item, ("category",), ("eventType",), ("type",)), language)
    if category:
        return category
    labels = get_tag_labels(item)
    return labels[0] if labels else ""


def get_start_and_end(item: Mapping[str, Any]) -> tuple[str, str, bool]:
    """Calcule (début, fin, heure connue ?) d'un événement OpenAgenda.

    Ordre de préférence :
    1. la liste `timings` (créneaux précis) : premier début, dernière fin ;
    2. `firstTiming` / `lastTiming` ;
    3. `firstDate` / `lastDate`, éventuellement complétés par
       `firstTimeStart` / `lastTimeEnd`.

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda.

    Returns
    -------
    tuple[str, str, bool]
        Début et fin en ISO 8601 (vides si inconnus), et vrai si l'heure est connue.
    """

    # 1. Créneaux précis
    timings = item.get("timings")
    if isinstance(timings, list) and timings:
        starts = [parse_event_datetime(t.get("start") or t.get("begin")) for t in timings if isinstance(t, Mapping)]
        ends = [parse_event_datetime(t.get("end")) for t in timings if isinstance(t, Mapping)]
        starts = [s for s in starts if s]
        ends = [e for e in ends if e]
        if starts:
            return to_iso(min(starts)), to_iso(max(ends)) if ends else "", True

    # 2. Premier et dernier créneau
    first = get_path(item, ("firstTiming", "begin"), ("firstTiming", "start"))
    last = get_path(item, ("lastTiming", "end"))
    if first:
        return to_iso(parse_event_datetime(first)), to_iso(parse_event_datetime(last)), has_time(first)

    # 3. Premier et dernier jour, complétés par les heures si elles sont données
    first_date = clean_text(get_path(item, ("firstDate",), ("first_date",)))
    last_date = clean_text(get_path(item, ("lastDate",), ("last_date",)))
    first_time = clean_text(item.get("firstTimeStart"))
    last_time = clean_text(item.get("lastTimeEnd"))
    start = f"{first_date}T{first_time}" if first_date and first_time else first_date
    end = f"{last_date}T{last_time}" if last_date and last_time else last_date
    return (
        to_iso(parse_event_datetime(start)),
        to_iso(parse_event_datetime(end)),
        bool(first_date and first_time),
    )


# ---------------------------------------------------------------------------
# Statut, participation, âge et accessibilité (ajouté le 14/10/2026)
# ---------------------------------------------------------------------------
# Codes de l'API OpenAgenda, relevés sur les événements bruts de l'audit
# (scripts/audit_openagenda.py --brut) : status, attendanceMode, age et
# accessibility sont présents sur 100 % des événements.

OPENAGENDA_STATUS: dict[int, str] = {
    1: "programme",
    2: "reprogramme",
    3: "deplace_en_ligne",
    4: "reporte",
    5: "complet",
    6: "annule",
}

OPENAGENDA_ATTENDANCE: dict[int, str] = {1: "sur_place", 2: "en_ligne", 3: "mixte"}

OPENAGENDA_ACCESSIBILITY: dict[str, str] = {
    "hi": "auditif",
    "ii": "intellectuel",
    "mi": "moteur",
    "pi": "psychique",
    "vi": "visuel",
}

def _to_int(value: Any) -> int | None:
    """Convertir une valeur en entier, ou None si vide ou illisible.

    Parameters
    ----------
    value : Any
        Valeur à convertir (texte, nombre, None).

    Returns
    -------
    int or None
        L'entier, ou None.
    """
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def get_status(item: Mapping[str, Any]) -> str:
    """Statut lisible (« annule », « complet »...). Par défaut : « programme ».

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda (champ `status`, code de 1 à 6).

    Returns
    -------
    str
        Une valeur de `OPENAGENDA_STATUS`.
    """

    return OPENAGENDA_STATUS.get(_to_int(item.get("status")) or 1, "programme")


def get_attendance(item: Mapping[str, Any]) -> str:
    """Mode de participation : sur place, en ligne ou les deux.

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda (champ `attendanceMode`, code de 1 à 3).

    Returns
    -------
    str
        Une valeur de `OPENAGENDA_ATTENDANCE` ; « sur_place » par défaut.
    """

    return OPENAGENDA_ATTENDANCE.get(_to_int(item.get("attendanceMode")) or 1, "sur_place")


def get_age_range(item: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Âge minimal et maximal (`age: {"min": 6, "max": 12}`), ou (None, None).

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda.

    Returns
    -------
    tuple[int or None, int or None]
        Les âges minimal et maximal ; (None, None) s'ils sont absents ou incohérents.
    """

    age = item.get("age")
    if not isinstance(age, Mapping):
        return None, None
    age_min, age_max = _to_int(age.get("min")), _to_int(age.get("max"))
    if age_min is not None and age_max is not None and age_min > age_max:
        return None, None
    return age_min, age_max


def get_accessibility(item: Mapping[str, Any]) -> list[str]:
    """Handicaps pris en charge. OpenAgenda donne une liste de codes
    (`["mi", "vi"]`) ou un dictionnaire (`{"mi": true, "vi": false}`).

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut OpenAgenda.

    Returns
    -------
    list[str]
        Les libellés de `OPENAGENDA_ACCESSIBILITY`, sans doublons ; les codes
        inconnus sont ignorés.
    """

    raw = item.get("accessibility")
    codes = [code for code, ok in raw.items() if ok] if isinstance(raw, Mapping) else list(raw or [])
    labels = [OPENAGENDA_ACCESSIBILITY[code] for code in codes if code in OPENAGENDA_ACCESSIBILITY]
    return list(dict.fromkeys(labels))


def map_openagenda_event(
    item: Mapping[str, Any],
    *,
    source: str = "openagenda",
    extracted_at: str = "",
    language: str = "fr",
) -> Event | None:
    """Transforme un événement OpenAgenda en `Event`. Renvoie None s'il n'a pas de titre.

    Parameters
    ----------
    item : Mapping[str, Any]
        Événement brut renvoyé par l'API OpenAgenda.
    source : str
        Nom de la source enregistré dans l'événement. Par défaut « openagenda ».
    extracted_at : str
        Date de l'extraction, recopiée dans l'événement. Par défaut vide.
    language : str
        Langue voulue pour les textes multilingues. Par défaut « fr ».

    Returns
    -------
    Event or None
        L'événement, ou None s'il n'a pas de titre.
    """

    # Titre, texte et dates
    title = pick_localized(get_path(item, ("title",)), language)
    if not title:
        return None

    description = (
        pick_localized(get_path(item, ("longDescription",)), language)
        or pick_localized(get_path(item, ("description",)), language)
    )
    start_at, end_at, time_known = get_start_and_end(item)

    # Lieu et prix
    venue_name = clean_text(get_path(item, ("location", "name"), ("location_name",), ("locationName",)))
    address = clean_text(get_path(item, ("location", "address"), ("address",)))
    city = normalize_city(get_path(item, ("location", "city"), ("city",)))
    postal_code = clean_text(get_path(item, ("location", "postalCode"), ("postalCode",)))

    price_text = pick_localized(get_path(item, ("conditions",), ("pricing",)), language)
    price_min, price_max, is_free = parse_price(price_text)

    # Mots-clés, liens et image
    keywords_raw = get_path(item, ("keywords", language), ("keywords",)) or []
    keywords = [clean_text(k) for k in keywords_raw if clean_text(k)] if isinstance(keywords_raw, list) else []
    # Les étiquettes OpenAgenda (« Exposition », « Concert »...) servent aussi de mots-clés.
    keywords += [tag for tag in get_tag_labels(item) if tag not in keywords]

    uid = clean_text(get_path(item, ("uid",), ("id",)))
    url = clean_text(get_path(item, ("canonicalUrl",), ("url",), ("link",)))
    image_url = build_image_url(get_path(item, ("image",), ("thumbnail",)))

    # Identité, âges, catégorie et publics, puis construction de l'événement
    dedup_key = build_dedup_key(title, start_at, venue_name or city)
    age_min, age_max = get_age_range(item)
    category = get_category(item, language)
    audiences = infer_audiences([title, category, *keywords], start_at, time_known, age_min, age_max)
    return Event(
        event_id=build_event_id(dedup_key),
        source=source,
        source_event_id=uid,
        title=title,
        description=description,
        category=category,
        keywords=keywords,
        language=language,
        start_at=start_at,
        end_at=end_at,
        time_known=time_known,
        venue_name=venue_name,
        address=address,
        city=city,
        postal_code=postal_code,
        latitude=to_float(get_path(item, ("location", "latitude"), ("latitude",))),
        longitude=to_float(get_path(item, ("location", "longitude"), ("longitude",))),
        price_text=price_text,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        status=get_status(item),
        attendance=get_attendance(item),
        age_min=age_min,
        age_max=age_max,
        audiences=audiences,
        accessibility=get_accessibility(item),
        url=url,
        image_url=image_url,
        extracted_at=extracted_at,
    )
