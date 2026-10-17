"""Conversion d'une ligne du fichier d'un partenaire en `Event`.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Il répond à
l'approche commerciale du projet : un professionnel (animation, soirées,
activités pour enfants...) envoie sa programmation, je la range dans le modèle
de fichier SortirIA (`docs/modeles/modele_partenaire.xlsx`), et la collecte de
nuit l'importe comme n'importe quelle autre source.

Le fichier est rempli par un humain : le contenu est légèrement nettoyé
(espaces, titres en majuscules, liens « www. ») et les formats sont tolérants
(« 25/10/2026 » ou « 2026-10-25 », « 20h30 » ou « 20:30 », « oui » ou « x »),
mais une ligne vraiment inexploitable est refusée **avec son motif et son
numéro de ligne**, pour pouvoir répondre au partenaire « ligne 12 : date
illisible ».
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any

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
from src.event.openagenda_mapper import infer_audiences
from src.utils.url_utils import is_valid_http_url

# Colonnes du modèle, dans l'ordre, avec l'aide affichée dans l'onglet « Aide ».
# Les colonnes marquées obligatoires doivent être remplies sur chaque ligne.
TEMPLATE_COLUMNS: tuple[tuple[str, bool, str], ...] = (
    ("titre", True, "Nom de l'événement, tel qu'il sera affiché."),
    ("date", True, "Jour de la séance : 25/10/2026. Une ligne par séance."),
    ("heure_debut", False, "20:30 ou 20h30. Vide si l'heure n'est pas fixée."),
    ("date_fin", False, "Dernier jour, pour un événement sur plusieurs jours (exposition, stage)."),
    ("heure_fin", False, "Heure de fin : 23:00."),
    ("lieu", True, "Nom de la salle, du club, du parc..."),
    ("adresse", False, "Numéro et rue."),
    ("code_postal", False, "34000"),
    ("commune", True, "Montpellier, Sète, Mèze..."),
    ("prix", False, "Tel qu'affiché : « 12 € », « 5 à 10 € », « Gratuit »."),
    ("gratuit", False, "oui / non"),
    ("public", False, "famille, soirée, tout public, adultes..."),
    ("age_min", False, "Âge minimum en années : 6"),
    ("age_max", False, "Âge maximum en années : 12"),
    ("categorie", False, "Concert, Atelier, Soirée DJ, Spectacle..."),
    ("description", False, "Deux ou trois phrases."),
    ("lien", False, "Page de l'événement ou de réservation (https://...)."),
    ("statut", False, "Vide, ou « complet », « annulé », « reporté »."),
)
REQUIRED_COLUMNS = tuple(name for name, required, _ in TEMPLATE_COLUMNS if required)

# Noms de colonnes acceptés en plus de ceux du modèle (fichiers « maison »).
COLUMN_ALIASES = {
    "nom": "titre", "evenement": "titre", "jour": "date", "date_debut": "date",
    "heure": "heure_debut", "debut": "heure_debut", "fin": "heure_fin",
    "salle": "lieu", "ville": "commune", "cp": "code_postal", "tarif": "prix",
    "tarifs": "prix", "url": "lien", "reservation": "lien", "lien_reservation": "lien",
    "type": "categorie", "age_minimum": "age_min", "age_maximum": "age_max",
}

REASON_MISSING_COLUMNS = "colonnes_manquantes"
REASON_UNREADABLE_FILE = "fichier_illisible"   # fichier corrompu, encodage inconnu, tableur protégé
REASON_MISSING_TITLE = "titre_manquant"
REASON_BAD_DATE = "date_illisible"
REASON_BAD_TIME = "heure_illisible"
REASON_MISSING_VENUE = "lieu_manquant"
REASON_MISSING_CITY = "commune_manquante"
REASON_BAD_AGES = "ages_incoherents"
REASON_BAD_LINK = "lien_invalide"
REASON_MISSING_LINK = "lien_manquant"

TRUE_WORDS = {"oui", "o", "x", "vrai", "true", "1", "yes"}
FALSE_WORDS = {"non", "n", "faux", "false", "0", "no"}
# « Tout public » écrit par un partenaire comprend les familles (15/10/2026).
FAMILY_PUBLIC_WORDS = ("famille", "enfant", "jeune public", "parent", "tout public", "tous publics",
                       "tout age", "tous ages")
NIGHT_PUBLIC_WORDS = ("soiree", "nuit", "club", "boite")
STATUS_WORDS = {"complet": "complet", "annule": "annule", "reporte": "reporte"}


# ---------------------------------------------------------------------------
# Lecture tolérante des cellules
# ---------------------------------------------------------------------------

def normalize_header(name: Any) -> str:
    """Nom de colonne comparable : « Heure début » -> « heure_debut »."""

    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return COLUMN_ALIASES.get(text, text)


def cell_text(value: Any) -> str:
    """Texte d'une cellule (les cellules vides de pandas valent NaN)."""

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return clean_text(str(value))


def parse_cell_date(value: Any) -> date | None:
    """Date d'une cellule : objet Excel, « 25/10/2026 », « 25-10-26 » ou « 2026-10-25 »."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = cell_text(value)
    if not text:
        return None
    text = text.split(" ")[0]   # « 2026-10-25 00:00:00 » (date Excel lue en texte)
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, pattern).date()  # noqa: DTZ007 (seul le jour compte)
        except ValueError:
            continue
    return None


def parse_cell_time(value: Any) -> time | None:
    """Heure d'une cellule : objet Excel, « 20:30 », « 20h30 », « 20h », « 20.30 ».

    Lève ValueError si la cellule est remplie mais illisible.
    """

    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = cell_text(value).lower().replace(" ", "")
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2})(?:[:h.](\d{2})?)?(?::\d{2})?", text)
    if not match:
        raise ValueError(text)
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        raise ValueError(text)
    return time(hour, minute)


def parse_cell_bool(value: Any) -> bool | None:
    """« oui », « x », « vrai »... -> True ; « non »... -> False ; vide -> None."""

    text = normalize_for_comparison(cell_text(value))
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


def parse_cell_int(value: Any) -> int | None:
    """Entier d'une cellule (« 6 », « 6 ans », 6.0) ; None si vide ou illisible."""

    match = re.search(r"\d+", cell_text(value))
    return int(match.group(0)) if match else None


def tidy_title(value: Any) -> str:
    """Titre nettoyé : espaces en trop retirés, titre TOUT EN MAJUSCULES adouci.

    « SOIRÉE  SALSA » -> « Soirée salsa ». Un sigle court (« DJ ») est gardé.
    """

    title = cell_text(value).strip(" -–•*")
    if len(title) > 4 and title.isupper():
        title = title.capitalize()
    return title


def tidy_link(value: Any) -> str:
    """Lien nettoyé : « www.exemple.fr » devient « https://www.exemple.fr »."""

    link = cell_text(value).replace(" ", "")
    if link.lower().startswith("www."):
        link = f"https://{link}"
    return link


def missing_columns(headers: list[str]) -> list[str]:
    """Colonnes obligatoires absentes de l'en-tête du fichier."""

    return [column for column in REQUIRED_COLUMNS if column not in headers]


def is_empty_row(row: Mapping[str, Any]) -> bool:
    """Ligne entièrement vide (fréquent en bas d'un tableur)."""

    return not any(cell_text(value) for value in row.values())


# Formules de partenariat (17/10/2026), de la moins à la plus visible :
# « gratuit » (présent, sans mise en avant), « mis_en_avant », « premium »
# (devant les autres offres partenaires, jamais devant ce qui répond mieux).
FORMULAS = ("gratuit", "mis_en_avant", "premium")


def partner_formula(partner: Mapping[str, Any]) -> str:
    """Formule déclarée ; sans formule, une date `sponsored_until` vaut « mis_en_avant »
    (configuration d'avant le 17/10/2026)."""

    value = "_".join(str(partner.get("formule") or partner.get("formula") or "").strip().lower()
                     .replace("-", " ").replace("é", "e").split())
    if value in FORMULAS:
        return value
    return "mis_en_avant" if partner.get("sponsored_until") else "gratuit"


def is_sponsored(partner: Mapping[str, Any], today: date) -> bool:
    """Le partenariat payant est-il en cours (formule payante et `sponsored_until` dans le futur) ?"""

    until = parse_cell_date(partner.get("sponsored_until"))
    return partner_formula(partner) != "gratuit" and until is not None and until >= today


# ---------------------------------------------------------------------------
# Une ligne -> un Event
# ---------------------------------------------------------------------------

def map_partner_row(
    row: Mapping[str, Any],
    partner: Mapping[str, Any],
    *,
    today: date,
    updated_at: str = "",
    extracted_at: str = "",
) -> tuple[Event | None, str]:
    """Transforme une ligne en `Event`. Renvoie (événement, "") ou (None, motif).

    Une séance déjà passée renvoie (None, "") : ce n'est pas une erreur du
    partenaire, la ligne est simplement ignorée.
    """

    title = tidy_title(row.get("titre"))
    if not title:
        return None, REASON_MISSING_TITLE
    day = parse_cell_date(row.get("date"))
    last_day = parse_cell_date(row.get("date_fin"))
    if day is None or (cell_text(row.get("date_fin")) and last_day is None):
        return None, REASON_BAD_DATE
    try:
        start_time = parse_cell_time(row.get("heure_debut"))
        end_time = parse_cell_time(row.get("heure_fin"))
    except ValueError:
        return None, REASON_BAD_TIME
    venue = cell_text(row.get("lieu"))
    if not venue:
        return None, REASON_MISSING_VENUE
    city = normalize_city(cell_text(row.get("commune")))
    if not city:
        return None, REASON_MISSING_CITY
    age_min, age_max = parse_cell_int(row.get("age_min")), parse_cell_int(row.get("age_max"))
    if age_min is not None and age_max is not None and age_min > age_max:
        return None, REASON_BAD_AGES
    link = tidy_link(row.get("lien"))
    if link and not is_valid_http_url(link):
        return None, REASON_BAD_LINK
    if not link and not is_valid_http_url(tidy_link(partner.get("website"))):
        return None, REASON_MISSING_LINK   # chaque proposition doit renvoyer quelque part
    if (last_day or day) < today:
        return None, ""

    start = datetime.combine(day, start_time or time.min, tzinfo=PARIS)
    end = None
    if last_day and last_day > day:
        end = datetime.combine(last_day, end_time, tzinfo=PARIS) if end_time else end_of_day(last_day)
    elif end_time and start_time and end_time > start_time:
        end = datetime.combine(day, end_time, tzinfo=PARIS)

    price_text = cell_text(row.get("prix"))
    price_min, price_max, is_free = parse_price(price_text)
    free_flag = parse_cell_bool(row.get("gratuit"))
    if free_flag is not None:
        is_free = free_flag
        if free_flag:
            price_min = price_max = 0.0

    category = cell_text(row.get("categorie"))
    public = cell_text(row.get("public"))
    start_at = to_iso(start)
    audiences = infer_audiences([title, category, public], start_at, start_time is not None, age_min, age_max)
    public_words = normalize_for_comparison(public)
    adults_only = age_min is not None and age_min >= 18
    if any(word in public_words for word in FAMILY_PUBLIC_WORDS) and "famille" not in audiences and not adults_only:
        audiences.append("famille")
    if any(word in public_words for word in NIGHT_PUBLIC_WORDS) and "soiree" not in audiences:
        audiences.append("soiree")
    status = STATUS_WORDS.get(normalize_for_comparison(cell_text(row.get("statut"))), "programme")

    event_id = build_event_id(build_dedup_key(title, start_at, venue))
    return Event(
        event_id=event_id,
        source="partenaire",
        source_event_id=f"{partner.get('file', '')}:{event_id}",
        title=title,
        description=cell_text(row.get("description"))[:1000],
        category=category,
        keywords=[word for word in (category, public) if word],
        start_at=start_at,
        end_at=to_iso(end),
        time_known=start_time is not None,
        venue_name=venue,
        address=cell_text(row.get("adresse")),
        city=city,
        postal_code=cell_text(row.get("code_postal")).split(".")[0],
        price_text=price_text,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        status=status,
        age_min=age_min,
        age_max=age_max,
        audiences=sorted(audiences),
        # partner_id est attribué au chargement en base (table partners) ;
        # « mis en avant » seulement pendant la durée du partenariat.
        is_sponsored=is_sponsored(partner, today),
        url=link or tidy_link(partner.get("website")),
        extracted_at=extracted_at,
        source_credit=clean_text(partner.get("name")),
        source_updated_at=updated_at,
    ), ""
