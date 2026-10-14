"""Extracteur des événements DATAtourisme pour SortirIA.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), sur le
modèle de `openagenda_extractor.py` (même adaptateur pour le moteur API de
CheckIt.AI, mêmes zones, même filtre géographique).

DATAtourisme est la base nationale des offices de tourisme (Licence Ouverte
2.0). On l'interroge par l'API officielle (la plateforme Diffuseur ferme et le
CSV de data.gouv.fr n'a ni dates ni lieux, voir `scripts/explore_datatourisme.py`) :

1. `GET https://api.datatourisme.fr/v1/entertainmentAndEvent` (« Fêtes et
   manifestations »), une fois par zone avec `geo_distance=lat,lon,Rkm` ;
2. pages de 100 événements, en suivant les liens `meta.next` ;
3. chaque séance (`takesPlaceAt`) dans la fenêtre [aujourd'hui ;
   aujourd'hui + `days_forward`] devient un `Event`
   (`src/event/datatourisme_mapper.py`).

Exploration du 14/10/2026 : 735 événements autour de Montpellier, 296 sur le
bassin de Thau, en une douzaine de requêtes (limite : 1 000 par heure).

La clé est lue dans le secret `datatourisme_api_key`, ou à défaut dans la
variable d'environnement `DATATOURISME_API_KEY` (fichier `.env`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from config.paths import SOURCES_FILE
from src.event.datatourisme_mapper import get_occurrences, map_datatourisme_event
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import occurrence_article_url
from src.extractors.apis.api_adapter import ApiAdapter
from src.extractors.apis.api_extractor import (
    ApiAuthenticationError,
    ApiExtractor,
    extract_api_from_source,
    fetch_json_object,
)
from src.extractors.apis.source.openagenda_extractor import get_areas
from src.extractors.core.extractor_results import ExtractorResult
from src.logger import get_logger
from src.utils.extractor_utils import build_standard_article
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)

DEFAULT_ENDPOINT = "https://api.datatourisme.fr/v1/entertainmentAndEvent"
DEFAULT_PAGE_SIZE = 100          # maximum accepté par l'API
DEFAULT_MAX_PAGES = 20           # par zone : 2 000 événements au plus
DEFAULT_DAYS_FORWARD = 30

# Champs demandés : ceux par défaut, plus les séances, les prix et les thèmes
# (absents de la sélection par défaut, vérifié le 14/10/2026).
FIELDS = (
    "uuid,uri,label,type,isLocatedAt,hasBeenCreatedBy,lastUpdate,hasContact,"
    "hasMainRepresentation,hasDescription,takesPlaceAt,offers,hasTheme"
)


def get_api_key() -> str:
    """Retourne la clé DATAtourisme, ou lève une erreur claire si elle manque."""

    api_key = DATATOURISME_EXTRACTOR.api_key
    if not api_key:
        raise ApiAuthenticationError(
            source_name=DATATOURISME_EXTRACTOR.name,
            reason=(
                "Clé DATAtourisme absente : renseignez DATATOURISME_API_KEY dans .env "
                "(clé gratuite : https://www.datatourisme.fr/utiliser-les-donnees/)."
            ),
        )
    return api_key


def is_in_window(occurrence: Mapping[str, Any], source: Mapping[str, Any], today: date | None = None) -> bool:
    """Vrai si la séance n'est pas finie et commence dans les `days_forward` jours."""

    today = today or datetime.now(PARIS).date()
    days_forward = parse_non_negative_integer(source.get("days_forward"), DEFAULT_DAYS_FORWARD)
    start = date.fromisoformat(occurrence["start_at"][:10])
    end = date.fromisoformat((occurrence.get("end_at") or occurrence["start_at"])[:10])
    return end >= today and start <= today + timedelta(days=days_forward)


def fetch_area_pages(source: Mapping[str, Any], area: Mapping[str, float], api_key: str) -> Iterator[dict[str, Any]]:
    """Événements bruts d'une zone, page par page (liens `meta.next`).

    Garde-fou : on s'arrête si une page n'apporte aucun événement nouveau
    (lien « suivant » qui tournerait en rond), sans attendre `max_pages`.
    """

    endpoint = normalize_value(source.get("endpoint")) or DEFAULT_ENDPOINT
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    page_size = min(max(parse_non_negative_integer(source.get("page_size"), DEFAULT_PAGE_SIZE), 1), 100)
    max_pages = max(parse_non_negative_integer(source.get("max_pages"), DEFAULT_MAX_PAGES), 1)

    params: dict[str, Any] | None = {
        "geo_distance": f"{area['latitude']},{area['longitude']},{area['radius_km']:g}km",
        "page_size": page_size,
        "lang": normalize_value(source.get("language")) or "fr",
        "fields": FIELDS,
    }
    url = endpoint
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        data = fetch_json_object(url=url, params=params, headers=headers)
        objects = [obj for obj in data.get("objects") or [] if isinstance(obj, Mapping)]
        new = [obj for obj in objects if normalize_value(obj.get("uuid")) not in seen]
        seen.update(normalize_value(obj.get("uuid")) for obj in objects)
        meta = data.get("meta") or {}
        logger.info(
            "DATAtourisme, page %s : %s événement(s) reçu(s), %s nouveau(x), total annoncé %s.",
            page, len(objects), len(new), meta.get("total"),
        )
        yield from new
        next_url = normalize_value(meta.get("next"))
        if not next_url or not new:
            return
        # Le lien suivant contient déjà tous ses paramètres.
        url, params = next_url, None


def iter_datatourisme_items(
    source: Mapping[str, Any],
    maximum_articles: int,
) -> Iterator[tuple[str, int, Mapping[str, Any]]]:
    """Parcourt les séances des événements de toutes les zones."""

    maximum = parse_non_negative_integer(maximum_articles, 0)
    if maximum <= 0:
        return

    api_key = get_api_key()
    seen: set[str] = set()
    yielded = 0
    read_by_area: Counter[int] = Counter()
    outside_window = 0
    try:
        for area_index, area in enumerate(get_areas(source)):
            for item in fetch_area_pages(source, area, api_key):
                uuid = normalize_value(item.get("uuid"))
                # Un événement peut tomber dans deux zones qui se touchent.
                if uuid and uuid in seen:
                    continue
                seen.add(uuid)
                read_by_area[area_index] += 1
                for index, occurrence in enumerate(get_occurrences(item)):
                    if not is_in_window(occurrence, source):
                        outside_window += 1
                        continue
                    yield f"zone={area_index}|uuid={uuid}", index, {**item, "_occurrence": occurrence}
                    yielded += 1
                    if yielded >= maximum:
                        return
            logger.info("DATAtourisme, zone %s : %s événement(s) lu(s).", area_index, read_by_area[area_index])
    finally:
        logger.info(
            "DATAtourisme : %s séance(s) retenue(s), %s hors fenêtre de dates écartée(s).",
            yielded, outside_window,
        )


def build_datatourisme_article(
    item: Any,
    item_index: int,
    item_identifier: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Transforme une séance DATAtourisme en « article » du moteur, événement inclus."""

    del item_index
    if not isinstance(item, Mapping) or not isinstance(item.get("_occurrence"), Mapping):
        return {}

    event = map_datatourisme_event(
        item,
        item["_occurrence"],
        source=normalize_value(source.get("source_id")) or "datatourisme",
        extracted_at=datetime.now(PARIS).isoformat(),
        language=normalize_value(source.get("language")) or "fr",
    )
    if event is None:
        return {}

    return build_standard_article(
        identifier=event.source_event_id or event.url or item_identifier,
        source=source.get("name", "DATAtourisme"),
        title=event.title,
        text=event.description,
        image_url=event.image_url,
        published_at=event.start_at,
        url=occurrence_article_url(event),  # unique par séance (voir event_pipeline)
        language=event.language,
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "producer": event.source_credit},
    )


def validate_datatourisme_item(
    item: Any,
    filters: Mapping[str, Any],
    source: Mapping[str, Any],
) -> tuple[bool, str]:
    """Contrôle minimal avant conversion : un titre, un identifiant, une séance."""

    del filters, source
    if not isinstance(item, Mapping):
        return False, "evenement_datatourisme_invalide"
    if not item.get("label"):
        return False, "titre_datatourisme_absent"
    if not (item.get("uuid") or item.get("uri")):
        return False, "identifiant_datatourisme_absent"
    if not isinstance(item.get("_occurrence"), Mapping):
        return False, "seance_datatourisme_absente"
    return True, ""


DATATOURISME_ADAPTER = ApiAdapter(
    source_id="datatourisme",
    default_name="DATAtourisme",
    iter_items=iter_datatourisme_items,
    build_article=build_datatourisme_article,
    validate_item=validate_datatourisme_item,
)

DATATOURISME_EXTRACTOR = ApiExtractor(
    source_id="datatourisme",
    default_name="DATAtourisme",
    adapter=DATATOURISME_ADAPTER,
    section_name="api_sources",
    sources_file=SOURCES_FILE,
)


def extract_articles_from_source(source: Mapping[str, Any]) -> ExtractorResult:
    """Lance l'extraction depuis une configuration déjà chargée."""

    return extract_api_from_source(source=source, adapter=DATATOURISME_ADAPTER)


def extract_all_articles() -> ExtractorResult:
    """Charge `config/sources.yaml` puis lance l'extraction DATAtourisme."""

    return DATATOURISME_EXTRACTOR.run()


if __name__ == "__main__":
    result = extract_all_articles()
    print(f"{len(result.articles)} séance(s) DATAtourisme. Statut : {result.status}.")
    if result.message:
        print(result.message)
