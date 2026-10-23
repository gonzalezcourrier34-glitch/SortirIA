"""Règles qui décident si un événement est exploitable par SortirIA.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `src/article/validation/article_validator.py` de CheckIt.AI : chaque refus
renvoie un motif court, compté ensuite dans les indicateurs de qualité.
"""

from __future__ import annotations

from datetime import datetime

from src.event.event_normalizer import PARIS, normalize_for_comparison, parse_event_datetime
from src.event.event_schema import Event
from src.utils.url_utils import is_valid_http_url

# Motifs de rejet (stables : ils servent de clés dans les statistiques).
REASON_MISSING_TITLE = "titre_absent"
REASON_MISSING_START = "date_absente"
REASON_INVALID_START = "date_illisible"
REASON_END_BEFORE_START = "fin_avant_debut"
REASON_MISSING_PLACE = "lieu_absent"
REASON_INVALID_URL = "url_invalide"
REASON_PAST = "evenement_passe"
REASON_CANCELLED = "annule_ou_reporte"
REASON_ONLINE_ONLY = "en_ligne_uniquement"
REASON_NOT_AN_OUTING = "pas_une_sortie"

# Statuts OpenAgenda qui rendent la sortie impossible à la date annoncée.
CANCELLED_STATUSES = frozenset({"annule", "reporte"})

# Valeurs admises par les contraintes CHECK de sortiria.events (02_events.sql).
# Un événement hors contrainte ferait échouer l'écriture de TOUTE la nuit
# (une seule transaction) : on ramène les champs libres dans les clous.
KNOWN_AUDIENCES = frozenset({"famille", "soiree", "ados", "seniors"})
KNOWN_STATUSES = frozenset({"programme", "reprogramme", "deplace_en_ligne", "reporte", "complet", "annule"})
KNOWN_ATTENDANCES = frozenset({"sur_place", "en_ligne", "mixte"})
MAX_AGE = 120


def conform_to_schema(event: Event) -> Event:
    """Ramène les champs d'un événement aux valeurs admises en base (modifie l'événement).

    - âges hors de 0-120 ou incohérents (min > max) : oubliés, un âge faux
      venu d'un PDF ou d'un fichier partenaire ne doit pas bloquer la nuit ;
    - prix minimum au-dessus du maximum : échangés ; fin avant le début : fin oubliée ;
    - publics inconnus : retirés ; statut ou présence inconnus : valeur par défaut.

    Parameters
    ----------
    event : Event
        L'événement à corriger, modifié sur place.

    Returns
    -------
    Event
        Le même événement, corrigé.
    """

    # Âges : hors bornes ou incohérents, ils sont oubliés
    ages = [age if isinstance(age, int) and 0 <= age <= MAX_AGE else None for age in (event.age_min, event.age_max)]
    if ages[0] is not None and ages[1] is not None and ages[0] > ages[1]:
        ages = [None, None]
    event.age_min, event.age_max = ages
    # 17/10/2026 : « de 32 à 29 € » (DATAtourisme) violait events_price_order
    # et a arrêté la nuit. Fourchette inversée : remise dans l'ordre.
    if event.price_min is not None and event.price_max is not None and event.price_min > event.price_max:
        event.price_min, event.price_max = event.price_max, event.price_min
    # Fin avant le début (events_end_after_start) : la fin est oubliée.
    start, end = parse_event_datetime(event.start_at), parse_event_datetime(event.end_at)
    if start is not None and end is not None and end < start:
        event.end_at = ""
    # Publics, statut et mode de présence ramenés aux listes de la base
    event.audiences = sorted({str(a) for a in (event.audiences or []) if str(a) in KNOWN_AUDIENCES})
    if event.status not in KNOWN_STATUSES:
        event.status = "programme"
    if event.attendance not in KNOWN_ATTENDANCES:
        event.attendance = "sur_place"
    return event


# Mots qui, dans la catégorie ou les mots-clés, signalent autre chose qu'une
# sortie. Audit du 14/10/2026 : 6 « Opportunité d'emploi » sur 82 événements.
NOT_AN_OUTING_WORDS = ("emploi", "recrutement", "job dating", "offre d emploi")

# Titres qui annoncent l'annulation alors que le statut est resté « programmé »
# (« ANNULÉ - MONTE-CRISTO », collecte du 15/10/2026), et services qui ne sont
# pas des sorties (« AIDE AUX DEVOIRS », relevé deux fois par le juge).
CANCELLED_TITLE_PREFIXES = ("annule", "annulee", "annulation", "reporte", "reportee")
NOT_AN_OUTING_TITLES = ("aide aux devoirs",)


def is_past(event: Event, now: datetime | None = None) -> bool:
    """Vrai si l'événement est terminé.

    On regarde la fin si elle existe, sinon le début. Un événement sans heure
    connue reste visible toute la journée : « ce soir » ne doit pas disparaître
    à minuit une.

    Parameters
    ----------
    event : Event
        L'événement à examiner.
    now : datetime or None
        Heure de référence. Par défaut, l'heure courante à Paris.

    Returns
    -------
    bool
        Vrai si l'événement est fini ; faux s'il n'a pas de date lisible.
    """

    now = now or datetime.now(PARIS)
    reference = parse_event_datetime(event.end_at) or parse_event_datetime(event.start_at)
    if reference is None:
        return False
    if not event.time_known or reference.time() == datetime.min.time():
        return reference.date() < now.astimezone(PARIS).date()
    return reference < now


def validate_event(event: Event, now: datetime | None = None) -> tuple[bool, str]:
    """Vérifie un événement. Renvoie (True, "") ou (False, motif du refus).

    Les règles sont appliquées dans l'ordre ; le premier refus donne le motif
    (une des constantes `REASON_...`).

    Parameters
    ----------
    event : Event
        L'événement à vérifier.
    now : datetime or None
        Heure de référence pour écarter les événements passés. Par défaut,
        l'heure courante.

    Returns
    -------
    tuple[bool, str]
        (True, "") si l'événement est exploitable, sinon (False, motif).
    """

    # Titre, annulation, et ce qui n'est pas une sortie de loisir
    if not event.title.strip():
        return False, REASON_MISSING_TITLE
    if event.status in CANCELLED_STATUSES:
        return False, REASON_CANCELLED
    title = normalize_for_comparison(event.title)
    if title.split(" ", 1)[0] in CANCELLED_TITLE_PREFIXES:
        return False, REASON_CANCELLED
    if any(phrase in title for phrase in NOT_AN_OUTING_TITLES):
        return False, REASON_NOT_AN_OUTING
    labels = normalize_for_comparison(" ".join([event.category, *event.keywords]))
    if any(f" {word} " in f" {labels} " for word in NOT_AN_OUTING_WORDS):
        return False, REASON_NOT_AN_OUTING
    # Un événement uniquement en ligne n'est pas une sortie.
    if event.attendance == "en_ligne" or event.status == "deplace_en_ligne":
        return False, REASON_ONLINE_ONLY
    if not event.start_at:
        return False, REASON_MISSING_START

    # Dates lisibles et dans le bon ordre
    start = parse_event_datetime(event.start_at)
    if start is None:
        return False, REASON_INVALID_START
    end = parse_event_datetime(event.end_at)
    if end is not None and end < start:
        return False, REASON_END_BEFORE_START

    # Lieu, lien et événement pas encore terminé
    if not (event.city or event.venue_name or event.address):
        return False, REASON_MISSING_PLACE
    if not is_valid_http_url(event.url):
        return False, REASON_INVALID_URL
    if is_past(event, now):
        return False, REASON_PAST

    return True, ""
