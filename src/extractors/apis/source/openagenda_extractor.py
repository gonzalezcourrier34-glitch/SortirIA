"""Extracteur OpenAgenda : récupère les événements à venir d'une zone.

**Provenance** : la logique d'appel est portée depuis `app/document_service.py`
de l'Assistant RAG OpenAgenda (projet 6, fonctions `search_agendas_for_zone` et
`fetch_openagenda_events`, dont les deux endpoints ont fonctionné en production
de POC). La structure (adaptateur + `ApiExtractor`) reprend celle des
extracteurs d'API de CheckIt.AI (projet 12, par exemple l'ancien
`gnews_extractor.py`).

Fonctionnement :

1. on trouve les agendas officiels de la zone (API v2 `/agendas?search=...`),
   ou on prend directement la liste `agenda_uids` de `config/sources.yaml` ;
2. pour chaque agenda, on lit les événements page par page sur la fenêtre
   [aujourd'hui ; aujourd'hui + `days_forward`] ;
3. chaque événement est converti en `Event` (voir `src/event/openagenda_mapper.py`).

Le moteur générique de CheckIt.AI attend des « articles » : l'événement complet
est donc rangé dans `article["metadata"]["event"]`, et `event_from_article`
(dans `src/event/event_pipeline.py`) le récupère ensuite. Ainsi, on garde la
pagination, les nouveaux essais, la gestion des secrets et les métriques du
moteur sans le réécrire.

La clé d'API est lue dans le secret `openagenda_api_key`, ou à défaut dans la
variable d'environnement `OPENAGENDA_API_KEY` (fichier `.env`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from config.paths import SOURCES_FILE
from src.event.event_normalizer import PARIS, normalize_for_comparison
from src.event.openagenda_mapper import get_path, map_openagenda_event
from src.extractors.apis.api_adapter import ApiAdapter
from src.extractors.apis.api_extractor import (
    ApiAuthenticationError,
    ApiExtractionStoppedError,
    ApiExtractor,
    extract_api_from_source,
    fetch_json_object,
    filter_mapping_items,
    record_api_request_error,
)
from src.extractors.core.extractor_results import ExtractorResult
from src.logger import get_logger
from src.utils.extractor_utils import build_standard_article
from src.utils.geo_utils import (
    get_areas,
    is_point_in_areas,
    to_float,
)
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)

# Valeurs par défaut (modifiables dans config/sources.yaml).
DEFAULT_AGENDAS_SEARCH_URL = "https://api.openagenda.com/v2/agendas"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_AGENDAS = 30
DEFAULT_DAYS_FORWARD = 30


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Retourne la clé OpenAgenda, ou lève une erreur claire si elle manque.

    Returns
    -------
    str
        La clé lue par `OPENAGENDA_EXTRACTOR.api_key`.

    Raises
    ------
    ApiAuthenticationError
        Si la clé est absente.
    """

    api_key = OPENAGENDA_EXTRACTOR.api_key
    if not api_key:
        raise ApiAuthenticationError(
            source_name=OPENAGENDA_EXTRACTOR.name,
            reason=(
                "Clé OpenAgenda absente : renseignez OPENAGENDA_API_KEY dans .env "
                "ou le secret openagenda_api_key."
            ),
        )
    return api_key


def get_date_window(source: Mapping[str, Any], today: date | None = None) -> tuple[str, str]:
    """Fenêtre de dates interrogée : d'aujourd'hui à `days_forward` jours.

    Parameters
    ----------
    source : Mapping[str, Any]
        La configuration de la source (clé `days_forward`, 30 jours par défaut).
    today : date | None
        Jour de référence. Par défaut `None` : aujourd'hui à Paris.

    Returns
    -------
    tuple[str, str]
        Dates de début et de fin au format ISO (`AAAA-MM-JJ`).
    """

    today = today or datetime.now(PARIS).date()
    days_forward = parse_non_negative_integer(source.get("days_forward"), DEFAULT_DAYS_FORWARD)
    return today.isoformat(), (today + timedelta(days=days_forward)).isoformat()


# ---------------------------------------------------------------------------
# Filtre géographique
# ---------------------------------------------------------------------------
#
# Ajouté après la première collecte du 13/10/2026 : la recherche d'agendas
# « Montpellier » renvoie aussi des agendas nationaux, et seuls 2,5 % des
# événements récupérés étaient à Montpellier (Paris arrivait en tête). On ne
# garde donc que les événements situés dans un rayon autour de la ville.

# Les fonctions de distance et de zones sont dans src/utils/geo_utils.py
# (communes à OpenAgenda, DATAtourisme et aux scripts d'audit).


def is_in_area(item: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    """Vrai si l'événement se trouve dans l'une des zones configurées.

    - Avec des zones GPS et un événement géolocalisé : distance au centre d'au
      moins une zone <= son rayon.
    - Sinon : le nom de la ville doit être dans `cities` (ou être la `zone`).
    - Sans zone configurée du tout, tout est gardé.

    Parameters
    ----------
    item : Mapping[str, Any]
        L'événement brut OpenAgenda.
    source : Mapping[str, Any]
        La configuration de la source (zones GPS, `cities`, `zone`).

    Returns
    -------
    bool
        Vrai si l'événement est dans la zone, ou si aucune zone n'est configurée.
    """

    areas = get_areas(source)
    lat = to_float(get_path(item, ("location", "latitude"), ("latitude",)))
    lon = to_float(get_path(item, ("location", "longitude"), ("longitude",)))
    if areas and lat is not None and lon is not None:
        return is_point_in_areas(lat, lon, areas)

    allowed = [normalize_for_comparison(city) for city in (source.get("cities") or [source.get("zone")]) if city]
    if not allowed:
        return True
    city = normalize_for_comparison(get_path(item, ("location", "city"), ("city",)))
    return city in allowed


# ---------------------------------------------------------------------------
# Appels réseau
# ---------------------------------------------------------------------------

def search_agenda_uids(source: Mapping[str, Any], api_key: str) -> list[str]:
    """Liste les agendas à lire : configurés à la main, ou trouvés par recherche.

    La recherche ne porte que sur les agendas officiels, une fois par terme de
    `search_terms` (à défaut la `zone`, « Montpellier » si rien n'est configuré).

    Parameters
    ----------
    source : Mapping[str, Any]
        La configuration de la source (`agenda_uids`, `search_terms`, `zone`,
        `max_agendas`, `agendas_search_url`).
    api_key : str
        La clé OpenAgenda.

    Returns
    -------
    list[str]
        Les identifiants d'agendas, sans doublon.
    """

    configured = source.get("agenda_uids") or []
    if configured:
        return [normalize_value(uid) for uid in configured if normalize_value(uid)]

    # Plusieurs termes de recherche (« Montpellier », « Hérault »...) trouvent
    # plus d'agendas locaux qu'un seul ; les doublons sont retirés.
    zone = normalize_value(source.get("zone")) or "Montpellier"
    terms = [normalize_value(term) for term in (source.get("search_terms") or [zone]) if normalize_value(term)]
    uids: list[str] = []
    for term in terms:
        data = fetch_json_object(
            url=normalize_value(source.get("agendas_search_url")) or DEFAULT_AGENDAS_SEARCH_URL,
            params={
                "search": term,
                "size": parse_non_negative_integer(source.get("max_agendas"), DEFAULT_MAX_AGENDAS),
                "official": 1,
            },
            headers={"key": api_key, "Accept": "application/json"},
        )
        agendas = filter_mapping_items(data.get("agendas", []), OPENAGENDA_EXTRACTOR.name)
        found = [normalize_value(agenda.get("uid")) for agenda in agendas]
        uids += [uid for uid in found if uid and uid not in uids]
        logger.info("%s agenda(s) OpenAgenda trouvé(s) pour « %s ».", len(found), term)
    return uids


def fetch_agenda_page(
    source: Mapping[str, Any],
    agenda_uid: str,
    api_key: str,
    offset: int,
    page_size: int,
) -> list[dict[str, Any]]:
    """Lit une page d'événements d'un agenda.

    Parameters
    ----------
    source : Mapping[str, Any]
        La configuration de la source (`endpoint`, `days_forward`, `search_text`).
    agenda_uid : str
        Identifiant de l'agenda.
    api_key : str
        La clé OpenAgenda.
    offset : int
        Rang du premier événement de la page.
    page_size : int
        Nombre d'événements par page.

    Returns
    -------
    list[dict[str, Any]]
        Les événements bruts de la page, dans la fenêtre de dates.
    """

    date_from, date_to = get_date_window(source)
    params: dict[str, Any] = {
        "key": api_key,
        "limit": page_size,
        "offset": offset,
        "oaq[from]": date_from,
        "oaq[to]": date_to,
    }
    # Filtre textuel optionnel (le RAG cherchait le nom de la ville).
    if normalize_value(source.get("search_text")):
        params["oaq[what]"] = normalize_value(source.get("search_text"))

    endpoint = normalize_value(source.get("endpoint")).rstrip("/")
    data = fetch_json_object(url=f"{endpoint}/{agenda_uid}/events.json", params=params)
    return filter_mapping_items(data.get("events", []), OPENAGENDA_EXTRACTOR.name)


# ---------------------------------------------------------------------------
# Adaptateur pour le moteur API de CheckIt.AI
# ---------------------------------------------------------------------------

def iter_openagenda_items(
    source: Mapping[str, Any],
    maximum_articles: int,
) -> Iterator[tuple[str, int, Mapping[str, Any]]]:
    """Parcourt tous les événements des agendas, sans dépasser `maximum_articles`.

    Parameters
    ----------
    source : Mapping[str, Any]
        La configuration de la source (agendas, zones, pagination, fenêtre de dates).
    maximum_articles : int
        Nombre maximal d'événements à donner ; 0 ou moins : aucun.

    Yields
    ------
    tuple[str, int, Mapping[str, Any]]
        Identifiant (`agenda=...|page=...`), rang dans la page, et l'événement
        brut complété de son agenda sous la clé `_agenda_uid`.

    Raises
    ------
    ApiAuthenticationError
        Si la clé OpenAgenda manque (voir `get_api_key`).
    ApiExtractionStoppedError
        La première erreur d'agenda, relevée une fois tous les agendas lus
        si aucun événement n'a été donné (le moteur en fait un échec).
    """

    maximum = parse_non_negative_integer(maximum_articles, 0)
    if maximum <= 0:
        return

    # Lire la clé et les réglages de pagination
    api_key = get_api_key()
    page_size = max(parse_non_negative_integer(source.get("page_size"), DEFAULT_PAGE_SIZE), 1)
    max_pages = max(parse_non_negative_integer(source.get("max_pages"), DEFAULT_MAX_PAGES), 1)

    seen_uids: set[str] = set()
    yielded = 0
    outside_area = 0
    kept_by_agenda: Counter[str] = Counter()
    # Un agenda supprimé, privé ou en quota (404, 403, 429) ne doit pas priver la
    # nuit des agendas suivants (15/10/2026) : l'erreur est enregistrée tout de
    # suite comme erreur de requête, pour que l'extracteur soit noté « réussite
    # partielle » (même si le plafond d'articles arrête la lecture avant la fin)
    # et que l'agent garde les événements non relus.
    agenda_errors: list[Exception] = []
    # Parcourir agenda par agenda, page par page, en filtrant par zone
    try:
        for agenda_uid in search_agenda_uids(source, api_key):
            read_before, kept_before = len(seen_uids), kept_by_agenda[agenda_uid]
            for page in range(max_pages):
                try:
                    events = fetch_agenda_page(source, agenda_uid, api_key, page * page_size, page_size)
                except ApiExtractionStoppedError as error:
                    logger.warning("Agenda %s : lecture interrompue (%s) ; on passe au suivant.", agenda_uid, error)
                    agenda_errors.append(error)
                    record_api_request_error(error.reason)
                    break
                for index, item in enumerate(events):
                    # Un même événement peut être partagé par plusieurs agendas.
                    uid = normalize_value(item.get("uid"))
                    if uid and uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    # Le filtre passe AVANT le plafond : les événements lointains
                    # ne consomment plus la limite max_articles.
                    if not is_in_area(item, source):
                        outside_area += 1
                        continue
                    kept_by_agenda[agenda_uid] += 1
                    yield f"agenda={agenda_uid}|page={page}", index, {**item, "_agenda_uid": agenda_uid}
                    yielded += 1
                    if yielded >= maximum:
                        return
                if len(events) < page_size:
                    break  # dernière page atteinte
            # Une ligne par agenda : on voit la collecte avancer.
            logger.info(
                "Agenda %s : %s événement(s) lu(s), %s dans la zone.",
                agenda_uid, len(seen_uids) - read_before, kept_by_agenda[agenda_uid] - kept_before,
            )
        # Sans aucun événement, relever la première erreur d'agenda (échec)
        if agenda_errors and not yielded:
            raise agenda_errors[0]
    finally:
        # Journaliser le bilan même si l'itération est interrompue
        logger.info(
            "OpenAgenda : %s événement(s) dans la zone, %s hors zone écarté(s). "
            "Agendas utiles : %s.",
            yielded, outside_area, dict(kept_by_agenda.most_common(15)),
        )


def build_openagenda_article(
    item: Any,
    item_index: int,
    item_identifier: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Transforme un événement OpenAgenda en « article » du moteur, événement inclus.

    L'`Event` complet est rangé dans `metadata["event"]`, avec l'agenda d'origine.

    Parameters
    ----------
    item : Any
        L'événement brut, avec son agenda sous la clé `_agenda_uid`.
    item_index : int
        Rang de l'événement (non utilisé).
    item_identifier : str
        Identifiant de repli si l'événement n'a ni identifiant ni URL.
    source : Mapping[str, Any]
        La configuration de la source (`source_id`, `name`, `language`, `role`).

    Returns
    -------
    dict[str, Any]
        L'article standard, ou un dictionnaire vide si l'événement est inexploitable.
    """

    del item_index
    if not isinstance(item, Mapping):
        return {}

    event = map_openagenda_event(
        item,
        source=normalize_value(source.get("source_id")) or "openagenda",
        extracted_at=datetime.now(PARIS).isoformat(),
        language=normalize_value(source.get("language")) or "fr",
    )
    if event is None:
        return {}

    return build_standard_article(
        identifier=event.source_event_id or event.url or item_identifier,
        source=source.get("name", "OpenAgenda"),
        title=event.title,
        text=event.description,
        image_url=event.image_url,
        published_at=event.start_at,
        url=event.url,
        language=event.language,
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "agenda_uid": item.get("_agenda_uid", "")},
    )


def validate_openagenda_item(
    item: Any,
    filters: Mapping[str, Any],
    source: Mapping[str, Any],
) -> tuple[bool, str]:
    """Contrôle minimal avant conversion : un titre et un identifiant.

    Dans le moteur API, ce contrôle est appelé après la construction de
    l'article et la validation commune (voir `extract_api_from_source`).

    Parameters
    ----------
    item : Any
        L'événement brut OpenAgenda.
    filters : Mapping[str, Any]
        Les filtres de la source (non utilisés).
    source : Mapping[str, Any]
        La configuration de la source (non utilisée).

    Returns
    -------
    tuple[bool, str]
        Vrai et une chaîne vide si l'événement est complet, sinon faux et le motif.
    """

    del filters, source
    if not isinstance(item, Mapping):
        return False, "evenement_openagenda_invalide"
    if not item.get("title"):
        return False, "titre_openagenda_absent"
    if not (item.get("uid") or item.get("canonicalUrl")):
        return False, "identifiant_openagenda_absent"
    return True, ""


OPENAGENDA_ADAPTER = ApiAdapter(
    source_id="openagenda",
    default_name="OpenAgenda",
    iter_items=iter_openagenda_items,
    build_article=build_openagenda_article,
    validate_item=validate_openagenda_item,
)

OPENAGENDA_EXTRACTOR = ApiExtractor(
    source_id="openagenda",
    default_name="OpenAgenda",
    adapter=OPENAGENDA_ADAPTER,
    section_name="api_sources",
    sources_file=SOURCES_FILE,
)


# ---------------------------------------------------------------------------
# API publique (même interface que les autres extracteurs)
# ---------------------------------------------------------------------------

def extract_articles_from_source(source: Mapping[str, Any]) -> ExtractorResult:
    """Lance l'extraction depuis une configuration déjà chargée.

    Parameters
    ----------
    source : Mapping[str, Any]
        La configuration de la source OpenAgenda.

    Returns
    -------
    ExtractorResult
        Le bilan de l'extraction avec les articles retenus.
    """

    return extract_api_from_source(source=source, adapter=OPENAGENDA_ADAPTER)


def extract_all_articles() -> ExtractorResult:
    """Charge `config/sources.yaml` puis lance l'extraction OpenAgenda.

    Returns
    -------
    ExtractorResult
        Le bilan de l'extraction, via l'exécuteur générique.
    """

    return OPENAGENDA_EXTRACTOR.run()


if __name__ == "__main__":
    result = extract_all_articles()
    print(f"{len(result.articles)} événement(s) OpenAgenda. Statut : {result.status}.")
    if result.message:
        print(result.message)
