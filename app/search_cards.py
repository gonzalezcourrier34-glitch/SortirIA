"""Mise en forme des événements pour l'interface : cartes, dates, prix, lieux.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

Une séance de la base devient une carte (`to_card`) : date lisible, prix au
format français, lieu, type, offre de démonstration (lien example.org). Les
séances d'un même événement sont regroupées en une carte avec ses autres
dates. Aussi : les offres partenaires proches, « À ne pas manquer » et la
liste des sources de l'en-tête.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.query_understanding import CATEGORY_LABELS, QueryPlan
from app.search_filters import PARIS, PUBLIC_LABELS
from app.search_query import EVENT_COLUMNS, build_partner_query, fetch_rows

# Événements montrés et donnés à Mistral (2 rangées de 4 cartes, 15/10).
MAX_PROPOSALS = 8
MAX_OTHER_DATES = 12     # autres dates gardées pour un même événement
SUMMARY_CHARS = 240

DAYS = ("Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim.")
MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.",
          "déc.")

# Autres dates du même événement (même titre, même lieu, même commune), pour
# le détail. Même normalisation que `occurrence_key` côté Python.
OTHER_DATES_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
LEFT JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.is_outing AND e.event_id <> %(event_id)s
  AND lower(regexp_replace(btrim(e.title), '\\s+', ' ', 'g')) = %(title)s
  AND lower(regexp_replace(btrim(COALESCE(e.venue_name, '')), '\\s+', ' ', 'g')) = %(venue)s
  AND lower(regexp_replace(btrim(COALESCE(e.city, '')), '\\s+', ' ', 'g')) = %(city)s
ORDER BY e.start_at
LIMIT %(limit)s"""

# « À ne pas manquer » : toutes les offres partenaires proposables (3 mois),
# mises en avant d'abord, puis celles des 30 prochains jours, puis les autres
# (« maintenant » : sortiria.reference_now(), figé sur la base de test).
HIGHLIGHTS_SQL = f"""
SELECT {EVENT_COLUMNS}
FROM sortiria.v_upcoming_events AS e
JOIN sortiria.partners AS p ON p.partner_id = e.partner_id
WHERE e.is_outing
ORDER BY partner_rank DESC, (e.start_at < sortiria.reference_now() + interval '30 days') DESC,
         e.start_at
LIMIT %(pool)s"""

SOURCES_SQL = """
SELECT source_name, count(*) AS events
FROM sortiria.v_upcoming_events
GROUP BY source_name
ORDER BY count(*) DESC"""


def date_label(start: datetime, time_known: bool = True) -> str:
    """« Sam. 17 oct. · 10:00 » (sans l'heure si elle est inconnue).

    Parameters
    ----------
    start : datetime
        Le début de la séance.
    time_known : bool
        Faux si l'heure n'est pas connue : elle n'est pas affichée. Par défaut vrai.

    Returns
    -------
    str
        La date à l'heure de Paris.
    """

    local = start.astimezone(PARIS)
    label = f"{DAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]}"
    return f"{label} · {local:%H:%M}" if time_known else label


def event_date_label(row: Mapping[str, Any], now: datetime | None = None) -> str:
    """Date affichée sur la carte d'un événement.

    Un événement déjà commencé et pas terminé (exposition,
    programme sur plusieurs semaines) affiche « En cours · jusqu'au 19 déc. »
    au lieu de sa date de début passée, qui laissait croire à une erreur.

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance (`start_at`, `end_at`, `time_known`).
    now : datetime | None
        Heure de référence ; par défaut maintenant.

    Returns
    -------
    str
        « En cours · jusqu'au … », « Du … au … », ou la date de la séance.
    """

    start, end = row["start_at"], row.get("end_at")
    now = now or datetime.now(PARIS)
    if end is not None and start < now <= end and (end - start) > timedelta(days=1):
        local_end = end.astimezone(PARIS)
        return f"En cours · jusqu'au {local_end.day} {MONTHS[local_end.month - 1]}"
    if end is not None and (end - start) > timedelta(days=1):
        # Festival, exposition à venir : « Du 30 oct. au 1 nov. » (un événement
        # du 31 n'apparaît plus comme une erreur de date).
        local_start, local_end = start.astimezone(PARIS), end.astimezone(PARIS)
        return (f"Du {local_start.day} {MONTHS[local_start.month - 1]} "
                f"au {local_end.day} {MONTHS[local_end.month - 1]}")
    return date_label(start, bool(row.get("time_known", True)))


# Un montant décimal dans un texte de prix : « 25.5 », « 7,5 » ; pas une durée
# ni une distance (« 1.5 h », « 2.5 km »), pas un nombre à séparateurs (« 1.500 »).
DECIMAL_AMOUNT = re.compile(r"(?<![\d.,])(\d+)[.,](\d{1,2})(?![\d.,])"
                            r"(?!\s*(?:h\b|min\b|km\b|kg\b|m\b|ans?\b|%))")
MONEY = re.compile(r"€|\beur(?:os?)?\b", re.IGNORECASE)


def french_amount(value: float) -> str:
    """12.5 -> « 12,50 » ; 12.0 -> « 12 » (16/10/2026 : « 25.5 € » s'affichait tel quel).

    Parameters
    ----------
    value : float
        Le montant.

    Returns
    -------
    str
        Le montant au format français, sans décimales s'il est rond.
    """

    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.2f}".replace(".", ",")


def french_prices(text: str) -> str:
    """Met les montants d'un texte de prix au format français.

    « De 5.5 à 8.5 € » -> « De 5,50 à 8,50 € ». Seulement dans un texte qui
    parle d'euros.

    Parameters
    ----------
    text : str
        Le texte de prix de la source.

    Returns
    -------
    str
        Le texte, montants décimaux réécrits avec une virgule.
    """

    if not MONEY.search(text):
        return text
    return DECIMAL_AMOUNT.sub(lambda m: f"{m.group(1)},{m.group(2):0<2}", text)


def price_label(row: Mapping[str, Any]) -> str:
    """Prix affiché sur la carte.

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance (`is_free`, `price_text`, `price_min`).

    Returns
    -------
    str
        « Gratuit », le texte de la source (40 caractères au plus), « dès … € »,
        ou « Prix non communiqué ».
    """

    if row.get("is_free"):
        return "Gratuit"
    text = french_prices(str(row.get("price_text") or "").strip())
    if text:
        return text if len(text) <= 40 else text[:39].rstrip() + "…"
    if row.get("price_min") is not None:
        return f"dès {french_amount(float(row['price_min']))} €"
    return "Prix non communiqué"


def place_label(row: Mapping[str, Any]) -> str:
    """Lieu affiché : « Salle · Ville », ou la ville seule quand le lieu porte son nom.

    La ville seule quand le lieu porte son nom (16/10/2026 : « Montpellier ·
    Montpellier »).

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance (`venue_name`, `city`).

    Returns
    -------
    str
        Le lieu et la ville, ou ce qui en est connu.
    """

    venue = str(row.get("venue_name") or "").strip()
    city = str(row.get("city") or "").strip()
    if venue and city and venue.casefold() == city.casefold():
        return city
    return " · ".join(part for part in (venue, city) if part)


def short_summary(row: Mapping[str, Any]) -> str:
    """Le résumé d'enrichissement, sinon le début de la description.

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance (`summary`, `description`).

    Returns
    -------
    str
        Le texte, coupé à un mot entier avant `SUMMARY_CHARS` caractères.
    """

    text = (str(row.get("summary") or "").strip()
            or " ".join(str(row.get("description") or "").split()))
    if len(text) <= SUMMARY_CHARS:
        return text
    return text[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"


# Titres en capitales (30/10/2026) : DATAtourisme et les agendas des communes
# écrivent souvent « LES PAS PERDUS — THÉÂTRE AMATEUR », peu lisible dans une
# carte et recopié tel quel par Mistral. Passés en minuscules, sauf les noms
# propres connus, les sigles et le début de chaque partie du titre.
UPPERCASE_SHARE = 0.8
TITLE_WORD = re.compile(r"[^\W_][\w'’+]*")
# Une partie de titre commence après « : », « — », « | », « / », « ( », « « »…
TITLE_PART_START = re.compile(r"(?:^|[:\-–—|/(«\"!?.])\s*$")
PROPER_WORDS = frozenset({
    "montpellier", "sète", "sete", "thau", "frontignan", "mèze", "meze", "bouzigues", "marseillan",
    "balaruc", "agde", "mauguio", "palavas", "pérols", "lattes", "castries", "pignan", "gigean",
    "poussan", "vic", "carnon", "hérault", "occitanie", "méditerranée", "france", "noël",
    "halloween", "toussaint", "pâques", "saint", "sainte"})
VOWELS = set("aeiouyàâäéèêëîïôöùûü")


def readable_title(title: Any, city: Any = "") -> str:
    """Titre lisible : une phrase en minuscules si la source l'écrit en capitales.

    Un titre déjà en casse mixte est rendu tel quel. Sinon, chaque mot passe en
    minuscules, sauf : le premier mot de chaque partie du titre (après « : »,
    « — », « / »…), les noms propres connus (`PROPER_WORDS` et la commune de
    l'événement), et les sigles (un chiffre, ou 4 lettres au plus sans voyelle :
    « DJ », « UTL34 »).

    Parameters
    ----------
    title : Any
        Le titre donné par la source.
    city : Any
        La commune de l'événement, dont les mots gardent leur majuscule.

    Returns
    -------
    str
        Le titre, espaces réduits.
    """

    title = " ".join(str(title or "").split())
    letters = [char for char in title if char.isalpha()]
    if len(letters) < 4 or sum(char.isupper() for char in letters) < UPPERCASE_SHARE * len(letters):
        return title
    proper = PROPER_WORDS | set(squash(city).replace("-", " ").split())
    pieces, last = [], 0
    for match in TITLE_WORD.finditer(title):
        original = match.group(0)
        word = original.lower()
        # « L'UTL34 », « D’ART » : l'élision reste en minuscules, le mot suit la règle
        cut = max(word.rfind("'"), word.rfind("’")) + 1
        head, tail = word[:cut], word[cut:]
        starts_part = bool(TITLE_PART_START.search(title[:match.start()]))
        if any(char.isdigit() for char in original) or (len(tail) <= 4 and not set(tail) & VOWELS):
            tail = tail.upper()
        elif tail in proper or (starts_part and not head):
            tail = tail[:1].upper() + tail[1:]
        word = head + tail
        if starts_part:
            word = word[:1].upper() + word[1:]
        pieces.append(title[last:match.start()])
        pieces.append(word)
        last = match.end()
    pieces.append(title[last:])
    return "".join(pieces)


def squash(value: Any) -> str:
    """Minuscules, espaces réduits (même règle que OTHER_DATES_SQL).

    Parameters
    ----------
    value : Any
        La valeur à normaliser (`None` donne une chaîne vide).

    Returns
    -------
    str
        Le texte normalisé.
    """

    return " ".join(str(value or "").split()).lower()


def occurrence_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Un même événement à plusieurs dates : même titre, même lieu, même commune.

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance.

    Returns
    -------
    tuple[str, str, str]
        Le titre, le lieu et la commune normalisés par `squash`.
    """

    return squash(row.get("title")), squash(row.get("venue_name")), squash(row.get("city"))



MAX_PER_VENUE = 2          # diversité : pas plus de 2 propositions au même lieu


def group_occurrences(rows: Sequence[Mapping[str, Any]], now: datetime,
                      limit: int = MAX_PROPOSALS,
                      ) -> list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]:
    """Regroupe les séances d'un même événement, puis choisit les propositions.

    L'ordre des groupes est celui de leur meilleure séance (la plus proche de
    la question). Dans un groupe, la séance montrée est la prochaine à venir
    (une séance déjà commencée ne passe qu'ensuite) ; les autres sont
    renvoyées triées par date.

    Diversité (15/10/2026) : pas plus de 2 propositions au même lieu tant
    qu'il y a d'autres lieux à proposer ; six ateliers de la même
    médiathèque laissaient peu de choix.

    Parameters
    ----------
    rows : Sequence[Mapping[str, Any]]
        Les séances, dans l'ordre de pertinence.
    now : datetime
        Heure de référence : une séance déjà commencée passe après les autres.
    limit : int
        Nombre maximum de propositions. Par défaut `MAX_PROPOSALS` (8).

    Returns
    -------
    list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]
        Pour chaque proposition : la séance montrée et ses autres séances
        (`MAX_OTHER_DATES` au plus), triées par date.
    """

    # Regrouper les séances par événement (titre, lieu, commune)
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(occurrence_key(row), []).append(row)
    # Choisir les événements, pas plus de MAX_PER_VENUE au même lieu
    chosen: list[tuple[str, str, str]] = []
    waiting: list[tuple[str, str, str]] = []
    per_venue: dict[tuple[str, str], int] = {}
    for key in groups:
        venue = (key[1], key[2])
        if key[1] and per_venue.get(venue, 0) >= MAX_PER_VENUE:
            waiting.append(key)
            continue
        per_venue[venue] = per_venue.get(venue, 0) + 1
        chosen.append(key)
        if len(chosen) == limit:
            break
    chosen += waiting[:limit - len(chosen)]            # pas assez d'autres lieux : on complète
    # Dans chaque groupe : la prochaine séance montrée, les autres par date
    result = []
    for key in chosen:
        ordered = sorted(groups[key], key=lambda r: (r["start_at"] < now, r["start_at"]))
        main, others = ordered[0], sorted(ordered[1:], key=lambda r: r["start_at"])
        result.append((main, others[:MAX_OTHER_DATES]))
    return result


def sibling_rows(connection: Any, row: Mapping[str, Any], limit: int = 60) -> list[dict[str, Any]]:
    """Toutes les autres séances à venir du même événement (même titre, lieu, commune).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    row : Mapping[str, Any]
        La séance de référence (exclue du résultat).
    limit : int
        Nombre maximum de séances. Par défaut 60.

    Returns
    -------
    list[dict[str, Any]]
        Les autres séances, triées par date.
    """

    return fetch_rows(connection, OTHER_DATES_SQL, {
        "event_id": str(row["event_id"]), "title": squash(row.get("title")),
        "venue": squash(row.get("venue_name")),
        "city": squash(row.get("city")), "limit": limit})


def complete_occurrences(connection: Any, row: Mapping[str, Any], plan: QueryPlan,
                         now: datetime, found: Sequence[Mapping[str, Any]] = ()
                         ) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Séance montrée et autres dates d'une carte, d'après TOUTES les séances à venir.

    Correction du 15/10/2026 : la carte ne comptait que les séances trouvées par
    la recherche (« + 1 date » quand le détail en listait 3), et pouvait montrer
    le 12 décembre alors qu'une séance avait lieu le 17 octobre. Maintenant :
    la séance montrée est la prochaine dans la période demandée, et toutes les
    autres sont comptées, comme dans le détail.

    Correction du soir (évaluation des 100 recherches) : la séance montrée est
    choisie parmi celles que la recherche a **trouvées** (`row` et `found`),
    qui respectent toutes les contraintes. Chaque séance a son propre
    enrichissement : pour « Halloween », la carte montrait la séance du 24/10
    (sans Halloween) au lieu de celle du 31/10 ; pour « ados », une séance
    sans le public ados.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    row : Mapping[str, Any]
        La séance retenue pour la carte.
    plan : QueryPlan
        Le plan de recherche (période demandée).
    now : datetime
        Heure de référence : une séance déjà commencée passe après les autres.
    found : Sequence[Mapping[str, Any]]
        Les autres séances du même événement trouvées par la recherche.

    Returns
    -------
    tuple[Mapping[str, Any], list[Mapping[str, Any]]]
        La séance montrée, et toutes les autres séances à venir
        (`MAX_OTHER_DATES` au plus), triées par date.
    """

    everyone = [row, *sibling_rows(connection, row)]
    matching = [row, *found]
    in_period = [r for r in matching
                 if r["start_at"] < plan.end and (r.get("end_at") or r["start_at"]) >= plan.start]
    main = min(in_period or [row], key=lambda r: (r["start_at"] < now, r["start_at"]))
    others = sorted((r for r in everyone if r["event_id"] != main["event_id"]),
                    key=lambda r: r["start_at"])
    return main, others[:MAX_OTHER_DATES]


def other_dates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Autres dates d'un événement, telles que l'interface les affiche.

    Parameters
    ----------
    rows : Sequence[Mapping[str, Any]]
        Les autres séances.

    Returns
    -------
    list[dict[str, str]]
        Pour chaque séance : `event_id` et la date affichée.
    """

    return [{"event_id": str(row["event_id"]), "date": event_date_label(row)} for row in rows]


def category_display(row: Mapping[str, Any]) -> str:
    """Type de sortie tel qu'on l'écrit : « soirée », « théâtre », « marché ».

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance (`category_norm`, sinon `category`).

    Returns
    -------
    str
        Le libellé accentué, ou la catégorie telle quelle.
    """

    category = str(row.get("category_norm") or row.get("category") or "").strip()
    return CATEGORY_LABELS.get(category.lower(), category)


DEMO_HOSTS = ("example.org", "example.com", "example.net")


def is_demo_link(url: str) -> bool:
    """Lien d'une offre de démonstration (partenaires fictifs : example.org).

    Parameters
    ----------
    url : str
        Le lien de l'événement.

    Returns
    -------
    bool
        Vrai si l'hôte est l'un des `DEMO_HOSTS` ou l'un de leurs sous-domaines.
    """

    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return any(host == demo or host.endswith(f".{demo}") for demo in DEMO_HOSTS)


def to_card(row: Mapping[str, Any], ref: int | None = None) -> dict[str, Any]:
    """Un événement tel que l'interface l'affiche (carte et détail).

    Parameters
    ----------
    row : Mapping[str, Any]
        La séance, avec les colonnes de `EVENT_COLUMNS`.
    ref : int | None
        Numéro de la carte, cité par la réponse ([1], [2]) ; None hors d'une réponse.

    Returns
    -------
    dict[str, Any]
        Les champs de la carte (titre, date, lieu, prix, source, partenaire…) ;
        `other_dates` est vide, à remplir par l'appelant.
    """

    place = place_label(row)
    rating = row.get("venue_rating")
    return {
        "ref": ref,
        "event_id": str(row["event_id"]),
        "title": readable_title(row["title"], row.get("city")),
        "date": event_date_label(row),
        "start_at": row["start_at"].isoformat(),
        "end_at": row["end_at"].isoformat() if row.get("end_at") else None,
        "place": place,
        "city": str(row.get("city") or "").strip(),
        "zone": str(row.get("zone") or ""),
        "category": str(row.get("category_norm") or row.get("category") or "").strip(),
        # Libellé affiché (« soirée », « théâtre ») ; la catégorie brute sert aux calculs.
        "category_label": category_display(row),
        "price": price_label(row),
        "price_min": float(row["price_min"]) if row.get("price_min") is not None else None,
        # Tous les publics (filtres, évaluation) ; l'interface ne répète pas
        # celui qui dit la même chose que le type (« soirée »).
        "audiences": [PUBLIC_LABELS.get(item, item) for item in row.get("audiences") or []],
        "summary": short_summary(row),
        "url": str(row.get("url") or ""),
        "source": str(row.get("source_name") or ""),
        "source_attribution": str(row.get("source_attribution") or ""),
        "partner": row.get("partner_id") is not None,
        # Passée en tête parce qu'elle répond au type et au public demandés.
        "partner_ahead": bool(row.get("partner_ahead")),
        "partner_name": str(row.get("partner_name") or ""),
        # Offre de démonstration (18/10/2026) : partenaire fictif, lien example.org.
        # L'interface l'annonce comme telle et n'envoie pas vers le faux lien.
        "demo": is_demo_link(str(row.get("url") or "")),
        "venue_rating": float(rating) if rating is not None else None,
        "venue_rating_count": int(row.get("venue_rating_count") or 0),
        "other_dates": [],
    }


def partner_offers(connection: Any, plan: QueryPlan, vector: str | None,
                   exclude: Sequence[str]) -> list[dict[str, Any]]:
    """Offres partenaires les plus proches, quand rien ne répond exactement.

    Au même endroit d'abord, puis n'importe où dans la zone couverte.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    plan : QueryPlan
        Le plan de recherche.
    vector : str | None
        Vecteur de la question au format pgvector, ou None.
    exclude : Sequence[str]
        Les événements déjà sur les cartes.

    Returns
    -------
    list[dict[str, Any]]
        Les cartes des offres, une par événement.
    """

    rows = fetch_rows(connection, *build_partner_query(plan, vector, exclude))
    if not rows and (plan.cities or plan.zone or plan.near):
        rows = fetch_rows(connection, *build_partner_query(plan, vector, exclude, keep_place=False))
    offers, seen = [], set()
    for row in rows:
        key = occurrence_key(row)                       # une offre répétée ne compte qu'une fois
        if key not in seen:
            seen.add(key)
            offers.append(to_card(row))
    return offers


def partner_highlights(connection: Any, limit: int = 3) -> list[dict[str, Any]]:
    """« À ne pas manquer » : les offres des partenaires à mettre en avant.

    Mises en avant d'abord, puis les plus proches dans le temps ; une offre par
    partenaire tant qu'il y a d'autres partenaires (chacun a sa place), et une
    offre répétée sur plusieurs dates ne compte qu'une fois.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    limit : int
        Nombre d'offres. Par défaut 3.

    Returns
    -------
    list[dict[str, Any]]
        Les cartes des offres.
    """

    rows = fetch_rows(connection, HIGHLIGHTS_SQL, {"pool": max(limit * 10, 30)})
    first, rest, seen = [], [], set()
    partners_shown: set[Any] = set()
    for row in rows:
        key = occurrence_key(row)
        if key in seen:
            continue
        seen.add(key)
        if row.get("partner_id") in partners_shown:
            rest.append(row)
        else:
            partners_shown.add(row.get("partner_id"))
            first.append(row)
    return [to_card(row) for row in (first + rest)[:limit]]


def active_sources(connection: Any) -> list[dict[str, Any]]:
    """Sources qui ont au moins un événement proposable (en-tête de l'interface).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.

    Returns
    -------
    list[dict[str, Any]]
        Pour chaque source : `name` et le nombre d'`events`, les plus fournies d'abord.
    """

    return [{"name": str(row["source_name"]), "events": int(row["events"])}
            for row in fetch_rows(connection, SOURCES_SQL, {})]
