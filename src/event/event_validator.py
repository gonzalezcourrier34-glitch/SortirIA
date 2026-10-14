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

# Mots qui, dans la catégorie ou les mots-clés, signalent autre chose qu'une
# sortie. Audit du 14/10/2026 : 6 « Opportunité d'emploi » sur 82 événements.
NOT_AN_OUTING_WORDS = ("emploi", "recrutement", "job dating", "offre d emploi")


def is_past(event: Event, now: datetime | None = None) -> bool:
    """Vrai si l'événement est terminé.

    On regarde la fin si elle existe, sinon le début. Un événement sans heure
    connue reste visible toute la journée : « ce soir » ne doit pas disparaître
    à minuit une.
    """

    now = now or datetime.now(PARIS)
    reference = parse_event_datetime(event.end_at) or parse_event_datetime(event.start_at)
    if reference is None:
        return False
    if not event.time_known or reference.time() == datetime.min.time():
        return reference.date() < now.astimezone(PARIS).date()
    return reference < now


def validate_event(event: Event, now: datetime | None = None) -> tuple[bool, str]:
    """Vérifie un événement. Renvoie (True, "") ou (False, motif du refus)."""

    if not event.title.strip():
        return False, REASON_MISSING_TITLE
    if event.status in CANCELLED_STATUSES:
        return False, REASON_CANCELLED
    labels = normalize_for_comparison(" ".join([event.category, *event.keywords]))
    if any(f" {word} " in f" {labels} " for word in NOT_AN_OUTING_WORDS):
        return False, REASON_NOT_AN_OUTING
    # Un événement uniquement en ligne n'est pas une sortie.
    if event.attendance == "en_ligne" or event.status == "deplace_en_ligne":
        return False, REASON_ONLINE_ONLY
    if not event.start_at:
        return False, REASON_MISSING_START

    start = parse_event_datetime(event.start_at)
    if start is None:
        return False, REASON_INVALID_START
    end = parse_event_datetime(event.end_at)
    if end is not None and end < start:
        return False, REASON_END_BEFORE_START

    if not (event.city or event.venue_name or event.address):
        return False, REASON_MISSING_PLACE
    if not is_valid_http_url(event.url):
        return False, REASON_INVALID_URL
    if is_past(event, now):
        return False, REASON_PAST

    return True, ""
