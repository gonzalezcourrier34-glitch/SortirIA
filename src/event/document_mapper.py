"""Lecture d'un programme (PDF) par Mistral, puis conversion en `Event`.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). C'est le
« nœud LLM » prévu dès la note de cadrage : là où une source n'a ni API ni
format structuré (un programme de saison en PDF), un modèle de langage lit le
texte et le range dans notre format. Le reste de la chaîne est inchangé.

Garde-fous, parce qu'un LLM peut inventer :

1. Mistral reçoit une consigne stricte (`EXTRACTION_PROMPT`) et doit répondre
   en JSON ; la réponse est validée par Pydantic (`DocumentEvent`) : une date
   illisible, un titre vide, une heure fantaisiste -> événement rejeté ;
2. **le titre doit se retrouver dans le texte de la page** lue (au moins 60 %
   de ses mots significatifs) : un événement inventé est rejeté
   (`titre_absent_du_texte`) ;
3. la date doit tomber dans la période du document (ex. septembre à
   décembre 2026) : une année mal devinée est rejetée (`hors_periode`) ;
4. chaque proposition renvoie à la page du PDF d'origine.

Réglages faits le 14/10/2026 après lecture du vrai texte de l'Agenda Culture
de Sète agglopôle (64 pages) : séances avec sous-titre (« Cycle : film du
jour »), activités hebdomadaires déroulées par le code (`expand_dates`),
public déduit de la rubrique, commune déduite du lieu (`find_city`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from src.event.event_audiences import infer_audiences
from src.event.event_normalizer import (
    PARIS,
    build_dedup_key,
    build_event_id,
    clean_text,
    end_of_day,
    normalize_city,
    normalize_for_comparison,
    parse_price,
    to_iso,
)
from src.event.event_schema import Event
from src.utils.url_utils import is_valid_http_url

# À changer quand la consigne change : les extractions en cache sont refaites.
PROMPT_VERSION = "2026-10-14.2"

EXTRACTION_PROMPT = """Tu lis un extrait du programme culturel d'une collectivité.
Ta tâche : relever les événements (spectacles, concerts, expositions, ateliers,
visites, conférences, projections...) EXPLICITEMENT décrits dans le texte.

Mise en page habituelle : le TITRE en capitales, puis le LIEU en capitales
(souvent « LIEU, COMMUNE »), puis la ou les dates et heures, puis le texte.
Le texte de chaque page commence par « [Page N] ».

Règles :
- N'invente rien. Si une information n'est pas écrite, mets null.
- Une entrée par date : si un événement a plusieurs dates (« mardis 20 et
  27 octobre », « 18 sept/2 oct/27 nov »), une entrée par date.
- Si chaque séance a son propre sous-titre (un film, un thème de conférence),
  titre = « Titre général : sous-titre de la séance ».
- Une exposition qui dure (du 3 octobre au 20 décembre) : une seule entrée,
  avec date_debut et date_fin.
- Activité répétée SANS liste de dates (« tous les mercredis à 10h », « les
  jeudis ») : une seule entrée avec jours_semaine (ex. ["mercredi"]),
  date_debut et date_fin = début et fin de la période (sauf dates écrites),
  hors_vacances_scolaires = true si c'est écrit.
- Recopie le titre tel qu'il est écrit (sans la mise en forme).
- Les dates sont au format AAAA-MM-JJ, les heures au format HH:MM.
  Période du document : {periode}. Si l'année n'est pas écrite, déduis-la de
  cette période.
- public : ce qui est écrit pour l'événement, sinon la rubrique de la page
  (« Jeunesse et famille (0-12 ans) » -> age_max 12 ; « Ados et adultes
  (+13 ans) » -> age_min 13).
- sur_inscription = true si « Sur inscription » ou « Réservation » est écrit.
- Ignore le sommaire, les éditoriaux, les horaires d'ouverture des lieux et
  les mentions « Coup de cœur ».
- description : une ou deux phrases tirées du texte, 300 caractères au plus.

Réponds uniquement avec un objet JSON de la forme :
{{"evenements": [{{"titre": "...", "date_debut": "AAAA-MM-JJ", "date_fin": null,
"heure_debut": "HH:MM", "heure_fin": null, "lieu": "...", "commune": "...",
"prix": "texte du prix tel qu'écrit, ou null", "gratuit": true/false/null,
"public": "Tout public, À partir de 6 ans...", "age_min": null, "age_max": null,
"categorie": "Concert, Exposition, Atelier...", "description": "...",
"lien_reservation": null, "sur_inscription": false, "jours_semaine": null,
"hors_vacances_scolaires": false}}]}}
S'il n'y a aucun événement dans l'extrait, réponds {{"evenements": []}}."""

REASON_TITLE_NOT_IN_TEXT = "titre_absent_du_texte"
REASON_OUT_OF_PERIOD = "hors_periode"
REASON_INVALID_JSON = "reponse_llm_illisible"
REASON_INVALID_FIELDS = "champs_llm_invalides"

TITLE_MATCH_RATIO = 0.6


class DocumentEvent(BaseModel):
    """Un événement tel que Mistral doit le rendre (validé par Pydantic).

    Les noms des champs sont ceux de la consigne (`EXTRACTION_PROMPT`), en français.

    Attributes
    ----------
    titre : str
        Titre de l'événement (au moins 3 caractères, espaces retirés).
    date_debut : date
        Date de début (ou de la séance).
    date_fin : date | None
        Date de fin d'un événement qui dure (exposition).
    heure_debut : time | None
        Heure de début.
    heure_fin : time | None
        Heure de fin.
    lieu : str | None
        Lieu tel qu'écrit dans le programme.
    commune : str | None
        Commune, si elle est écrite.
    prix : str | None
        Prix tel qu'écrit.
    gratuit : bool | None
        Gratuité annoncée.
    public : str | None
        Public visé (« Tout public », « À partir de 6 ans »...).
    age_min : int | None
        Âge minimal (entre 0 et 120, sinon ignoré).
    age_max : int | None
        Âge maximal (entre 0 et 120, sinon ignoré).
    categorie : str | None
        Catégorie (« Concert », « Exposition »...).
    description : str | None
        Une ou deux phrases tirées du texte.
    lien_reservation : str | None
        Lien de réservation.
    sur_inscription : bool | None
        True si l'inscription est demandée.
    jours_semaine : list[str] | None
        Jours d'une activité hebdomadaire (« mercredi »), déroulés par `expand_dates`.
    hors_vacances_scolaires : bool | None
        True si l'activité s'arrête pendant les vacances scolaires.
    """

    titre: str
    date_debut: date
    date_fin: date | None = None
    heure_debut: time | None = None
    heure_fin: time | None = None
    lieu: str | None = None
    commune: str | None = None
    prix: str | None = None
    gratuit: bool | None = None
    public: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    categorie: str | None = None
    description: str | None = None
    lien_reservation: str | None = None
    sur_inscription: bool | None = None
    jours_semaine: list[str] | None = None
    hors_vacances_scolaires: bool | None = None

    @field_validator("titre")
    @classmethod
    def title_not_empty(cls, value: str) -> str:
        """Refuse un titre de moins de 3 caractères et retire ses espaces.

        Parameters
        ----------
        value : str
            Le titre rendu par Mistral.

        Returns
        -------
        str
            Le titre sans espaces autour.

        Raises
        ------
        ValueError
            Si le titre fait moins de 3 caractères.
        """
        if len(value.strip()) < 3:
            raise ValueError("titre vide")
        return value.strip()

    @field_validator("age_min", "age_max")
    @classmethod
    def plausible_age(cls, value: int | None) -> int | None:
        """Ignore un âge hors de 0 à 120 ans (sans rejeter l'événement).

        Parameters
        ----------
        value : int | None
            L'âge rendu par Mistral.

        Returns
        -------
        int | None
            L'âge s'il est plausible, sinon None.
        """
        return value if value is None or 0 <= value <= 120 else None


def parse_llm_response(text: str) -> list[dict[str, Any]]:
    """Liste brute des événements d'une réponse Mistral (JSON, éventuellement entouré de ```).

    Parameters
    ----------
    text : str
        La réponse de Mistral.

    Returns
    -------
    list[dict[str, Any]]
        Les entrées de la clé `evenements` qui sont des objets.

    Raises
    ------
    ValueError
        Motif `reponse_llm_illisible` si la réponse ne contient pas d'objet JSON
        ou pas de liste `evenements` ; `json.JSONDecodeError` (sous-classe de
        ValueError) si le JSON est mal formé.
    """

    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError(REASON_INVALID_JSON)
    data = json.loads(match.group(0))
    events = data.get("evenements") if isinstance(data, Mapping) else None
    if not isinstance(events, list):
        raise ValueError(REASON_INVALID_JSON)  # noqa: TRY004 (réponse mal formée, pas une erreur de type Python)
    return [event for event in events if isinstance(event, Mapping)]


def validate_llm_event(raw: Mapping[str, Any]) -> tuple[DocumentEvent | None, str]:
    """Valide un événement rendu par Mistral. Renvoie (événement, "") ou (None, motif).

    Parameters
    ----------
    raw : Mapping[str, Any]
        Une entrée brute de la réponse.

    Returns
    -------
    tuple[DocumentEvent | None, str]
        L'événement validé et "", ou None et le motif `champs_llm_invalides`.
    """

    try:
        return DocumentEvent.model_validate(dict(raw)), ""
    except ValidationError:
        return None, REASON_INVALID_FIELDS


def title_found_in_text(title: str, text: str) -> bool:
    """Vrai si au moins 60 % des mots significatifs du titre sont dans le texte.

    Le texte d'un PDF est souvent espacé (« O n les appelait ») ou coupé en fin
    de ligne : on compare aussi le texte sans espaces.

    Parameters
    ----------
    title : str
        Le titre rendu par Mistral.
    text : str
        Le texte de la page lue.

    Returns
    -------
    bool
        True si assez de mots de plus de deux lettres sont retrouvés ; False
        pour un titre sans mot significatif.
    """

    words = [word for word in normalize_for_comparison(title).split() if len(word) > 2]
    if not words:
        return False
    normalized = normalize_for_comparison(text)
    squeezed = normalized.replace(" ", "")
    found = sum(1 for word in words if f" {word} " in f" {normalized} " or word in squeezed)
    return found / len(words) >= TITLE_MATCH_RATIO


def in_period(event: DocumentEvent, period: tuple[date, date] | None) -> bool:
    """Vrai si l'événement chevauche la période du document (ou s'il n'y en a pas).

    Parameters
    ----------
    event : DocumentEvent
        L'événement validé.
    period : tuple[date, date] | None
        Début et fin de la période du document ; None si inconnue.

    Returns
    -------
    bool
        True si l'événement chevauche la période, ou sans période.
    """

    if period is None:
        return True
    start, end = period
    return event.date_debut <= end and (event.date_fin or event.date_debut) >= start


WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def parse_holidays(source: Mapping[str, Any]) -> list[tuple[date, date]]:
    """Vacances scolaires de la configuration (`school_holidays: [{debut, fin}]`).

    Provisoire : remplacé par le jeu de données « calendrier scolaire » de
    l'Éducation nationale (prochaine méthode de collecte prévue).

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source document.

    Returns
    -------
    list[tuple[date, date]]
        Début et fin de chaque période de vacances lisible.
    """

    holidays = []
    for item in source.get("school_holidays") or []:
        try:
            holidays.append((date.fromisoformat(str(item["debut"])), date.fromisoformat(str(item["fin"]))))
        except (KeyError, TypeError, ValueError):
            continue
    return holidays


def expand_dates(
    event: DocumentEvent,
    first_day: date,
    last_day: date,
    holidays: list[tuple[date, date]] | None = None,
) -> list[DocumentEvent]:
    """Déroule une activité hebdomadaire (« tous les mercredis ») en séances.

    Seulement entre `first_day` et `last_day` (la fenêtre de collecte), et hors
    vacances scolaires si le programme le précise. Un événement sans
    `jours_semaine` est renvoyé tel quel.

    Parameters
    ----------
    event : DocumentEvent
        L'activité, avec `jours_semaine`.
    first_day : date
        Premier jour de la fenêtre de collecte.
    last_day : date
        Dernier jour de la fenêtre de collecte.
    holidays : list[tuple[date, date]] | None
        Périodes de vacances scolaires ; None : aucune.

    Returns
    -------
    list[DocumentEvent]
        Une séance d'un jour par date retenue (sans `jours_semaine`), ou
        `[event]` si aucun jour n'est reconnu.
    """

    # Jours de la semaine reconnus (0 = lundi)
    days = {WEEKDAYS.index(day) for day in (normalize_for_comparison(d) for d in event.jours_semaine or []) if day in WEEKDAYS}
    if not days:
        return [event]
    # Parcourir les jours communs à l'activité et à la fenêtre, hors vacances si demandé
    start = max(event.date_debut, first_day)
    end = min(event.date_fin or last_day, last_day)
    sessions = []
    current = start
    while current <= end:
        on_holiday = event.hors_vacances_scolaires and any(begin <= current <= stop for begin, stop in holidays or [])
        if current.weekday() in days and not on_holiday:
            sessions.append(event.model_copy(update={"date_debut": current, "date_fin": None, "jours_semaine": None}))
        current += timedelta(days=1)
    return sessions


def fix_listed_sessions(events: list[DocumentEvent]) -> list[DocumentEvent]:
    """Séances d'une même activité qui ont reçu la fin de la période comme date de fin.

    Cas réel (Agenda Culture de Sète, 15/10/2026) : « PAUSE JEUX » listée le
    3, le 5, le 7 septembre… et Mistral donnait à chaque séance la date de fin
    du programme (31 décembre). Chaque séance restait alors « en cours » des
    semaines après avoir eu lieu.

    Règle : deux séances de la même activité (même titre, même lieu) ne
    peuvent pas se chevaucher. Une date de fin qui dépasse le début d'une
    séance suivante est donc celle de la période : elle est retirée (la
    séance dure le jour indiqué). La dernière séance perd aussi cette fin si
    elle est la même que celle retirée aux autres. Une exposition seule (une
    seule entrée, « du 1er au 31 décembre ») n'est pas touchée.

    Parameters
    ----------
    events : list[DocumentEvent]
        Les événements validés d'un document.

    Returns
    -------
    list[DocumentEvent]
        Les mêmes événements, dans le même ordre, dates de fin abusives retirées.
    """

    # Regrouper les séances d'une même activité (titre, lieu), hors hebdomadaires
    groups: dict[tuple[str, str], list[int]] = {}
    for index, event in enumerate(events):
        if not event.jours_semaine:
            key = (normalize_for_comparison(event.titre), normalize_for_comparison(event.lieu or ""))
            groups.setdefault(key, []).append(index)
    # Dans chaque groupe d'au moins deux dates, retirer les fins qui débordent
    # sur la séance suivante, puis la même fin partout ailleurs
    fixed = list(events)
    for indexes in groups.values():
        ordered = sorted(indexes, key=lambda i: events[i].date_debut)
        if len({events[i].date_debut for i in ordered}) < 2:
            continue
        cleared: set[date] = set()
        for position, index in enumerate(ordered):
            end = events[index].date_fin
            later = [events[i].date_debut for i in ordered[position + 1:] if events[i].date_debut > events[index].date_debut]
            if end and later and end >= later[0]:
                cleared.add(end)
                fixed[index] = events[index].model_copy(update={"date_fin": None})
        for index in ordered:
            if fixed[index].date_fin in cleared:
                fixed[index] = events[index].model_copy(update={"date_fin": None})
    return fixed


def find_city(venue: str, venues: Mapping[str, str] | None) -> str:
    """Commune d'un lieu connu (`venues` de la configuration), sinon "".

    Le programme écrit souvent « MÉDIATHÈQUE MONTAIGNE » sans la commune ; les
    pages pratiques du document donnent l'adresse de chaque lieu.

    Parameters
    ----------
    venue : str
        Le lieu tel qu'écrit dans le programme.
    venues : Mapping[str, str] | None
        `{nom du lieu: commune}` ; un lieu correspond si tous les mots de son
        nom sont dans `venue`.

    Returns
    -------
    str
        La commune du premier lieu correspondant, ou "".
    """

    normalized = f" {normalize_for_comparison(venue)} "
    for name, city in (venues or {}).items():
        if all(f" {word} " in normalized for word in normalize_for_comparison(name).split()):
            return str(city)
    return ""


def map_document_event(
    event: DocumentEvent,
    source: Mapping[str, Any],
    *,
    page: int,
    document_url: str,
    extracted_at: str = "",
) -> Event:
    """Transforme un événement validé en `Event` SortirIA.

    Parameters
    ----------
    event : DocumentEvent
        L'événement validé.
    source : Mapping[str, Any]
        Configuration de la source document (`source_id`, `venues`, `city`,
        `credit` ou `name`, `published_on`).
    page : int
        Numéro de la page du PDF où l'événement a été lu.
    document_url : str
        Adresse du PDF ; le lien renvoie à la page (`#page=N`) faute de lien
        de réservation.
    extracted_at : str
        Date de l'extraction (ISO 8601).

    Returns
    -------
    Event
        L'événement SortirIA.
    """

    # Début et fin (fin de journée si plusieurs jours sans heure de fin)
    start_time = event.heure_debut
    start = datetime.combine(event.date_debut, start_time or time.min, tzinfo=PARIS)
    end = None
    if event.date_fin and event.date_fin > event.date_debut:
        end = datetime.combine(event.date_fin, event.heure_fin, tzinfo=PARIS) if event.heure_fin else end_of_day(event.date_fin)
    elif event.heure_fin and start_time and event.heure_fin > start_time:
        end = datetime.combine(event.date_debut, event.heure_fin, tzinfo=PARIS)

    # Prix : texte lu, complété par la gratuité annoncée
    price_text = clean_text(event.prix)
    price_min, price_max, is_free = parse_price(price_text)
    if event.gratuit is not None and is_free is None:
        is_free = event.gratuit
        if is_free:
            price_min = price_max = 0.0

    # Lieu et commune (écrite, sinon celle du lieu connu, sinon celle de la source),
    # description, mots-clés et lien
    start_at = to_iso(start)
    venue = clean_text(event.lieu)
    city = (
        normalize_city(event.commune)
        or normalize_city(find_city(venue, source.get("venues")))
        or normalize_city(source.get("city"))
    )
    description = clean_text(event.description)
    if event.sur_inscription and "inscription" not in description.lower():
        description = f"{description} Sur inscription.".strip()
    category = clean_text(event.categorie)
    keywords = [word for word in (category, clean_text(event.public)) if word]
    link = clean_text(event.lien_reservation)
    url = link if is_valid_http_url(link) else f"{document_url}#page={page}"

    # L'identifiant de source porte l'identifiant de l'événement (titre, jour,
    # lieu) : avant le 15/10/2026, deux événements distincts d'une même page le
    # même jour partageaient le même identifiant, et le second était jeté
    # comme « doublon ».
    event_id = build_event_id(build_dedup_key(event.titre, start_at, venue or city))
    return Event(
        event_id=event_id,
        source="document",
        source_event_id=f"{source.get('source_id', '')}@p{page}@{start_at[:10]}@{event_id}",
        title=clean_text(event.titre),
        description=description[:500],
        category=category,
        keywords=keywords,
        language="fr",
        start_at=start_at,
        end_at=to_iso(end),
        time_known=start_time is not None,
        venue_name=venue,
        city=city,
        price_text=price_text,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        age_min=event.age_min,
        age_max=event.age_max,
        audiences=infer_audiences(
            [event.titre, category, clean_text(event.public)], start_at, start_time is not None,
            event.age_min, event.age_max,
        ),
        url=url,
        source_credit=clean_text(source.get("credit") or source.get("name")),
        source_updated_at=clean_text(source.get("published_on"))[:10],
        extracted_at=extracted_at,
    )


def parse_period(source: Mapping[str, Any]) -> tuple[date, date] | None:
    """Période du document depuis la configuration (`period: {debut, fin}`).

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source document.

    Returns
    -------
    tuple[date, date] | None
        Début et fin, ou None si la période est absente ou illisible.
    """

    period = source.get("period")
    if not isinstance(period, Mapping):
        return None
    try:
        return date.fromisoformat(str(period["debut"])), date.fromisoformat(str(period["fin"]))
    except (KeyError, ValueError):
        return None


def describe_period(period: tuple[date, date] | None) -> str:
    """Période en clair pour la consigne (« du 2026-09-01 au 2026-12-31 »).

    Parameters
    ----------
    period : tuple[date, date] | None
        Début et fin de la période ; None si inconnue.

    Returns
    -------
    str
        La période en texte, ou « non précisée ».
    """

    if period is None:
        return "non précisée"
    return f"du {period[0].isoformat()} au {period[1].isoformat()}"

