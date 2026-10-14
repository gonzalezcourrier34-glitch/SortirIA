"""Moteur commun utilisé par les extracteurs d'API.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/apis/api_extractor.py` (commit 93632c5).

Ce module contient uniquement les comportements propres aux API :

- représentation commune des adaptateurs et extracteurs API ;
- lecture des paramètres de configuration ;
- requêtes HTTP JSON avec nouvelles tentatives ;
- gestion des erreurs d'authentification et de quota ;
- pagination ;
- validation et transformation des réponses ;
- déduplication des articles.

L'orchestration générique, les statuts et les rapports d'exécution
restent centralisés dans les modules communs des extracteurs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.utils.url_utils import sanitize_url_for_logging

from config.paths import SOURCES_FILE
from config.source_config import load_validated_source

from config.constants import (
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
    SOURCE_TYPE_API,
    API_AUTHENTICATION_ERROR_PATTERNS,
    API_QUOTA_ERROR_PATTERNS,
    API_SECRET_KEY_PATTERNS,
    API_AUTHENTICATION_ERROR_CODES,
    API_QUOTA_ERROR_CODES,
    API_RATE_LIMIT_PATTERNS
)

from config.environment import get_secret_or_environment

from src.logger import get_logger
from src.extractors.core.extractor_executor import execute_configured_extractor
from src.extractors.core.extractor_results import ExtractorResult

from config.environment import is_valid_secret
from config.settings import (
    HTTP_HEADERS,
    MAX_ARTICLE_AGE_DAYS,
    MAX_ARTICLES_PER_SOURCE,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    RETRY_DELAY_SECONDS
)

from src.article.validation.article_validator import validate_article_with_reason
from src.article.processing.article_deduplicator import (
    is_duplicate_article,
    register_article
)

from src.utils.filter_utils import get_filter_configuration
from src.utils.value_utils import normalize_value

from src.utils.parsing_utils import (
    parse_boolean,
    parse_non_negative_integer
)
from src.utils.url_utils import is_valid_http_url

from src.utils.extractor_utils import log_extraction_summary


from src.extractors.apis.api_adapter import (
    ApiAdapter,
    ApiItem,
    build_article_from_api_item,
    validate_api_item
)

ApiExtractionResult = list[dict[str, Any]] | ExtractorResult

logger = get_logger(__name__)


# Motifs d'erreurs API connus

_API_REQUEST_COUNT: ContextVar[int] = ContextVar(
    "api_request_count",
    default=0
)
_API_REQUEST_ERRORS: ContextVar[tuple[str, ...]] = ContextVar(
    "api_request_errors",
    default=()
)

# Exceptions propres aux API

class ApiExtractionStoppedError(RuntimeError):
    """Demande l'arrêt immédiat de l'extracteur API courant."""

    def __init__(
        self,
        source_name: str,
        reason: str,
        status_code: int | None = None
    ) -> None:
        self.source_name = normalize_value(source_name) or "API inconnue"
        self.reason = normalize_value(reason) or "Erreur API non communiquée"
        self.status_code = status_code
        super().__init__(self.reason)


# Arrêts définitifs

class ApiQuotaExceededError(ApiExtractionStoppedError):
    """Signale qu'un quota ou une limite d'utilisation est épuisé."""


class ApiAuthenticationError(ApiExtractionStoppedError):
    """Signale qu'une clé, un compte ou un abonnement est refusé."""


# Arrêts temporaires ou métier

class ApiRateLimitError(ApiExtractionStoppedError):
    """Signale une limitation temporaire de fréquence."""


class ApiInvalidParametersError(ApiExtractionStoppedError):
    """Signale des paramètres API invalides."""


class ApiMaintenanceError(ApiExtractionStoppedError):
    """Signale une indisponibilité temporaire du service."""

        
class ApiRequestError(RuntimeError):
    """Signale l'échec non fatal d'une requête API isolée."""

    def __init__(
        self,
        source_name: str,
        reason: str,
        status_code: int | None = None,
        parameters: Mapping[str, Any] | None = None
    ) -> None:
        self.source_name = normalize_value(source_name) or "API inconnue"
        self.reason = normalize_value(reason) or "Erreur API non communiquée"
        self.status_code = status_code
        self.parameters = dict(parameters or {})
        super().__init__(self.reason)


# Normalisation des erreurs API

def normalize_api_error_message(value: Any) -> str:
    """Transforme une réponse d'erreur API en texte exploitable."""

    if isinstance(value, Mapping):
        detailed_parts: list[str] = []
        generic_parts: list[str] = []

        for key in (
            "code",
            "type",
            "message",
            "msg",
            "detail",
            "details",
            "description",
            "reason",
            "error",
            "errors",
            "results",
            "context",
            "status"
        ):
            item = value.get(key)
            normalized = (
                normalize_api_error_message(item)
                if isinstance(item, (Mapping, list, tuple, set))
                else normalize_value(item)
            )

            if not normalized:
                continue

            if normalized.casefold() in {"error", "failed", "failure"}:
                generic_parts.append(normalized)
            else:
                detailed_parts.append(normalized)

        parts = detailed_parts or generic_parts
        return " | ".join(dict.fromkeys(parts))

    if isinstance(value, (list, tuple, set)):
        parts = [normalize_api_error_message(item) for item in value]
        return " | ".join(dict.fromkeys(part for part in parts if part))

    return normalize_value(value)

def extract_api_error_codes(value: Any) -> set[str]:
    """Extrait récursivement les codes machine d'une erreur API."""

    codes: set[str] = set()

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = normalize_value(key).lower()

            if normalized_key in {
                "code",
                "status",
                "type",
                "reason",
                "error_code",
                "errorcode"
            }:
                normalized_code = normalize_value(item).upper()
                normalized_code = (
                    normalized_code
                    .replace("-", "_")
                    .replace(" ", "_")
                )

                if normalized_code:
                    codes.add(normalized_code)

            if isinstance(item, (Mapping, list, tuple, set)):
                codes.update(extract_api_error_codes(item))

    elif isinstance(value, (list, tuple, set)):
        for item in value:
            codes.update(extract_api_error_codes(item))

    return codes

def get_http_error_message(error: httpx.HTTPStatusError) -> str:
    """Extrait un message HTTP exploitable sans exposer de secret."""

    try:
        message = normalize_api_error_message(error.response.json())

        if message and message.casefold() not in {"error", "failed", "failure"}:
            return sanitize_url_for_logging(message)
    except (ValueError, TypeError):
        pass

    text = sanitize_url_for_logging(
        normalize_value(error.response.text[:1000])
    )

    if text:
        return text

    return normalize_value(error.response.reason_phrase)

def contains_api_error_pattern(message: str, patterns: Sequence[str]) -> bool:
    """Vérifie si un message contient l'un des motifs fournis."""

    normalized_message = normalize_value(message).lower()
    return any(pattern.lower() in normalized_message for pattern in patterns)


def raise_if_fatal_api_error(
    error: Any,
    source_name: str,
    status_code: int | None = None
) -> str:
    """Lève une exception spécialisée pour une erreur API fatale."""

    raw_error: Any = error

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code

        try:
            raw_error = error.response.json()
        except ValueError:
            raw_error = error.response.text

        message = get_http_error_message(error)
        reason = (
            message
            or normalize_value(error.response.reason_phrase)
            or f"Erreur HTTP {status_code}"
        )
    else:
        message = sanitize_url_for_logging(
            normalize_api_error_message(error)
        )
        reason = message or (
            f"Erreur HTTP {status_code}"
            if status_code is not None
            else "Erreur API non communiquée"
        )

    error_codes = extract_api_error_codes(raw_error)

    # Authentification
    authentication_error = (
        status_code == 401
        or bool(
            error_codes.intersection(
                API_AUTHENTICATION_ERROR_CODES
            )
        )
        or contains_api_error_pattern(
            message,
            API_AUTHENTICATION_ERROR_PATTERNS,
        )
    )

    # Un 403 seul reste considéré comme un refus d'accès,
    # sauf s'il correspond à un quota.
    if status_code == 403:
        authentication_error = True

    if authentication_error:
        raise ApiAuthenticationError(
            source_name=source_name,
            reason=reason,
            status_code=status_code,
        ) from None

    # Limitation temporaire
    rate_limit_error = (
        status_code == 429
        or contains_api_error_pattern(
            message,
            API_RATE_LIMIT_PATTERNS,
        )
    )

    if rate_limit_error:
        raise ApiRateLimitError(
            source_name=source_name,
            reason=reason,
            status_code=status_code,
        ) from None

    # Quota épuisé
    quota_error = (
        status_code == 402
        or bool(
            error_codes.intersection(
                API_QUOTA_ERROR_CODES
            )
        )
        or contains_api_error_pattern(
            message,
            API_QUOTA_ERROR_PATTERNS,
        )
    )

    if quota_error:
        raise ApiQuotaExceededError(
            source_name=source_name,
            reason=reason,
            status_code=status_code,
        ) from None

    # Paramètres invalides
    if status_code in {400, 404, 422}:
        raise ApiInvalidParametersError(
            source_name=source_name,
            reason=reason,
            status_code=status_code,
        ) from None

    # Maintenance / indisponibilité
    if status_code in {502, 503, 504}:
        raise ApiMaintenanceError(
            source_name=source_name,
            reason=reason,
            status_code=status_code,
        ) from None

    return reason

# Validation de la configuration API

def validate_api_secret(secret: Any, secret_name: str, source_name: str) -> bool:
    """Vérifie qu'une clé ou un secret API est exploitable."""

    if is_valid_secret(secret):
        return True

    logger.error(
        "Extraction %s impossible : %s absent ou invalide.",
        source_name,
        secret_name
    )
    return False


def is_secret_key(key: Any) -> bool:
    """Indique si une clé de paramètre semble contenir un secret."""

    normalized_key = normalize_value(key).lower()
    return any(
        pattern in normalized_key
        for pattern in API_SECRET_KEY_PATTERNS
    )


def mask_secret(value: Any) -> str:
    """Masque une valeur sensible avant son écriture dans les logs."""

    normalized_value = normalize_value(value)

    if not normalized_value:
        return ""

    return "********"


def mask_mapping_secrets(
    values: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Retourne une copie d'un dictionnaire avec ses secrets masqués."""

    if not isinstance(values, Mapping):
        return {}

    return {
        str(key): (
            mask_secret(value)
            if is_secret_key(key)
            else value
        )
        for key, value in values.items()
    }


def reset_api_request_count() -> object:
    """Réinitialise le compteur de requêtes pour une exécution API."""

    return _API_REQUEST_COUNT.set(0)


def increment_api_request_count() -> None:
    """Incrémente le compteur de requêtes HTTP de l'exécution courante."""

    _API_REQUEST_COUNT.set(_API_REQUEST_COUNT.get() + 1)


def get_api_request_count() -> int:
    """Retourne le nombre de requêtes HTTP de l'exécution courante."""

    return _API_REQUEST_COUNT.get()


def reset_api_request_errors() -> object:
    """Réinitialise les erreurs de requêtes isolées de l'exécution courante."""

    return _API_REQUEST_ERRORS.set(())


def record_api_request_error(error: Any) -> str:
    """Enregistre une erreur non fatale sans exposer de secret."""

    if isinstance(error, httpx.HTTPStatusError):
        message = get_http_error_message(error)
    elif isinstance(error, httpx.HTTPError):
        message = type(error).__name__
    else:
        message = normalize_api_error_message(error)

    if not message:
        message = "Erreur API non communiquée"

    message = sanitize_url_for_logging(message)
    _API_REQUEST_ERRORS.set((*_API_REQUEST_ERRORS.get(), message))
    return message


def get_api_request_errors() -> list[str]:
    """Retourne les erreurs de requêtes isolées enregistrées."""

    return list(_API_REQUEST_ERRORS.get())


# Normalisation des paramètres API

def normalize_string_list(
    value: Any,
    lowercase: bool = False,
    uppercase: bool = False
) -> list[str]:
    """Transforme une valeur en liste unique de textes nettoyés."""

    if lowercase and uppercase:
        raise ValueError(
            "lowercase et uppercase ne peuvent pas être activés ensemble."
        )

    if isinstance(value, str):
        values: Sequence[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return []

    normalized_values: list[str] = []

    for item in values:
        text = normalize_value(item)

        if not text:
            continue

        if lowercase:
            text = text.lower()
        elif uppercase:
            text = text.upper()

        normalized_values.append(text)

    return list(dict.fromkeys(normalized_values))


def get_queries_configuration(
    source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Retourne les requêtes structurées d'une source API."""

    queries = source.get("queries", [])
    if isinstance(queries, Mapping):
        return [dict(queries)]
    if not isinstance(queries, Sequence) or isinstance(
        queries,
        (str, bytes, bytearray)
    ):
        return []
    return [dict(query) for query in queries if isinstance(query, Mapping)]


def get_query_configuration_values(
    source: Mapping[str, Any],
    plural_key: str,
    singular_key: str = "",
    *,
    lowercase: bool = False,
    uppercase: bool = False
) -> list[str]:
    """Lit d'abord les requêtes structurées puis les clés racine."""

    values: list[str] = []
    for query in get_queries_configuration(source):
        configured = query.get(plural_key)
        if configured is None and singular_key:
            configured = query.get(singular_key)
        values.extend(
            normalize_string_list(
                configured,
                lowercase=lowercase,
                uppercase=uppercase
            )
        )

    if values:
        return list(dict.fromkeys(values))

    configured = source.get(plural_key)
    if configured is None and singular_key:
        configured = source.get(singular_key)

    return normalize_string_list(
        configured,
        lowercase=lowercase,
        uppercase=uppercase
    )


def get_query_keywords(source: Mapping[str, Any]) -> list[str]:
    """Retourne les mots-clés structurés ou historiques d'une source."""

    values: list[str] = []
    for query in get_queries_configuration(source):
        configured = (
            query.get("keywords")
            or query.get("keyword")
            or query.get("query")
            or query.get("q")
        )
        values.extend(normalize_string_list(configured))

    if values:
        return list(dict.fromkeys(values))

    return normalize_string_list(
        source.get("keywords", source.get("query"))
    )


def get_pagination_configuration(
    source: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Retourne la pagination imbriquée d'une source API."""

    pagination = source.get("pagination", {})
    return pagination if isinstance(pagination, Mapping) else {}


def get_pagination_value(
    source: Mapping[str, Any],
    *keys: str,
    default: Any = None
) -> Any:
    """Lit la pagination imbriquée puis les anciennes clés racine."""

    pagination = get_pagination_configuration(source)
    for key in keys:
        if key in pagination:
            return pagination[key]
    for key in keys:
        if key in source:
            return source[key]
    return default


def join_normalized_values(value: Any, separator: str = ",") -> str:
    """Réunit une collection de valeurs uniques dans une chaîne."""

    if not isinstance(separator, str):
        raise TypeError("separator doit être une chaîne de caractères.")

    return separator.join(normalize_string_list(value))


def get_api_max_articles(source: Mapping[str, Any]) -> int:
    """Retourne le nombre maximal d'articles à extraire."""

    return parse_non_negative_integer(
        source.get("max_articles", MAX_ARTICLES_PER_SOURCE),
        MAX_ARTICLES_PER_SOURCE
    )


def get_api_max_article_age_days(source: Mapping[str, Any]) -> int:
    """Retourne l'âge maximal autorisé pour une source API."""

    configured = parse_non_negative_integer(
        source.get("max_article_age_days", MAX_ARTICLE_AGE_DAYS),
        MAX_ARTICLE_AGE_DAYS
    )

    if configured <= 0:
        return MAX_ARTICLE_AGE_DAYS

    if MAX_ARTICLE_AGE_DAYS <= 0:
        return configured

    return min(configured, MAX_ARTICLE_AGE_DAYS)


# Pagination générique

def calculate_page_size(
    configured_value: Any,
    remaining_articles: int,
    default: int,
    maximum: int
) -> int:
    """Calcule une taille de page comprise dans toutes les limites."""

    default_size = max(parse_non_negative_integer(default, 1), 1)
    maximum_size = max(
        parse_non_negative_integer(maximum, default_size),
        1
    )
    configured_size = max(
        parse_non_negative_integer(configured_value, default_size),
        1
    )
    remaining = max(
        parse_non_negative_integer(remaining_articles, 1),
        1
    )

    return min(configured_size, remaining, maximum_size)


def normalize_page_limit(value: Any, default: int, maximum: int) -> int:
    """Normalise un nombre maximal de pages."""

    default_limit = max(parse_non_negative_integer(default, 1), 1)
    maximum_limit = max(
        parse_non_negative_integer(maximum, default_limit),
        1
    )
    configured_limit = parse_non_negative_integer(value, default_limit)

    return min(max(configured_limit, 1), maximum_limit)


# Requêtes HTTP

def is_retryable_http_error(error: BaseException) -> bool:
    """Indique si une erreur HTTP temporaire peut être retentée."""

    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return True

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code in {408, 429} or 500 <= status_code < 600

    return False


@retry(
    retry=retry_if_exception(is_retryable_http_error),
    stop=stop_after_attempt(max(MAX_RETRIES, 1)),
    wait=wait_exponential(
        multiplier=max(RETRY_DELAY_SECONDS, 0.1),
        min=max(RETRY_DELAY_SECONDS, 0.1),
        max=max(RETRY_DELAY_SECONDS * 8, RETRY_DELAY_SECONDS, 0.1)
    ),
    reraise=True
)
def fetch_json_object(
    url: str,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = REQUEST_TIMEOUT
) -> dict[str, Any]:
    """Effectue une requête GET et retourne un objet JSON."""

    normalized_url = normalize_value(url)

    if not is_valid_http_url(normalized_url):
        safe_url = sanitize_url_for_logging(normalized_url)

        raise ValueError(
            f"URL API absente ou invalide : {safe_url or 'valeur absente'}"
        )

    if params is not None and not isinstance(params, Mapping):
        raise TypeError("params doit être un dictionnaire ou None.")

    if headers is not None and not isinstance(headers, Mapping):
        raise TypeError("headers doit être un dictionnaire ou None.")

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("timeout doit être un nombre positif et fini.")

    request_headers = {**HTTP_HEADERS, **dict(headers or {})}
    request_params = dict(params or {})
    request_timeout = float(timeout)

    logger.debug(
        "Requête API GET %s avec paramètres %s.",
        sanitize_url_for_logging(normalized_url),
        mask_mapping_secrets(request_params)
    )

    with httpx.Client(
        headers=request_headers,
        timeout=request_timeout,
        follow_redirects=True
    ) as client:
        increment_api_request_count()
        # Modifié pour SortirIA (14/10/2026) : sans paramètres, on passe None.
        # Avec un dictionnaire vide, httpx efface la partie « ?... » de l'URL :
        # un lien « page suivante » (DATAtourisme) perdait ainsi ses filtres,
        # sa taille de page et son curseur.
        response = client.get(normalized_url, params=request_params or None)

        if response.status_code == 204:
            return {}

        content_type = normalize_value(
            response.headers.get("content-type")
        ).lower()

        try:
            response_data = response.json()
        except ValueError:
            response_data = None

        if response.status_code >= 400:
            error_payload = (
                response_data
                if response_data is not None
                else response.text
            )

            reason = raise_if_fatal_api_error(
                error=error_payload,
                source_name=sanitize_url_for_logging(normalized_url),
                status_code=response.status_code
            )

            if response.status_code in {400, 404, 422}:
                raise ApiRequestError(
                    source_name=sanitize_url_for_logging(normalized_url),
                    reason=reason,
                    status_code=response.status_code,
                    parameters=request_params
                )

            response.raise_for_status()

        if "text/html" in content_type:
            raise ValueError(
                "La réponse de l'API contient du HTML au lieu de JSON."
            )

        if response_data is None:
            raise ValueError(
                "La réponse de l'API ne contient pas un JSON valide."
            )

        data = response_data

    if not isinstance(data, dict):
        raise ValueError("La réponse de l'API n'est pas un objet JSON.")

    return data


def execute_independent_api_requests(
    requests: Sequence[Mapping[str, Any]],
    request_function: Callable[[Mapping[str, Any]], Sequence[ApiItem]],
    source_name: str
) -> list[ApiItem]:
    """Exécute des requêtes indépendantes et conserve les réussites."""

    if not callable(request_function):
        raise TypeError("request_function doit être une fonction appelable.")

    items: list[ApiItem] = []

    for request_index, parameters in enumerate(requests, start=1):
        if not isinstance(parameters, Mapping):
            message = (
                f"Requête {request_index} ignorée : paramètres invalides."
            )
            logger.warning("%s : %s", source_name, message)
            record_api_request_error(message)
            continue

        request_parameters = {
            key: value
            for key, value in parameters.items()
            if value is not None
            and value != ""
            and value != []
            and value != ()
            and value != {}
        }

        logger.debug(
            "Requête %s/%s pour %s avec paramètres %s.",
            request_index,
            len(requests),
            source_name,
            mask_mapping_secrets(request_parameters)
        )

        try:
            result = request_function(request_parameters)

            if result is None:
                continue

            if isinstance(result, (str, bytes)) or not isinstance(
                result,
                Sequence
            ):
                raise TypeError(
                    "Une requête API indépendante doit retourner une séquence."
                )

            items.extend(result)

        except ApiExtractionStoppedError:
            raise

        except ApiRequestError as error:
            message = record_api_request_error(error.reason)
            logger.warning(
                "Requête %s/%s rejetée par %s%s : %s Paramètres=%s.",
                request_index,
                len(requests),
                source_name,
                f" (HTTP {error.status_code})" if error.status_code else "",
                message,
                mask_mapping_secrets(error.parameters or request_parameters)
            )

        except httpx.HTTPStatusError as error:
            reason = raise_if_fatal_api_error(error, source_name)
            message = record_api_request_error(reason)
            logger.warning(
                "Requête %s/%s échouée pour %s : %s.",
                request_index,
                len(requests),
                source_name,
                message
            )

        except (httpx.HTTPError, OSError, ValueError, TypeError) as error:
            message = record_api_request_error(error)
            logger.warning(
                "Requête %s/%s échouée pour %s : %s.",
                request_index,
                len(requests),
                source_name,
                message
            )

    return items


# Validation des réponses API

def filter_mapping_items(items: Any, source_name: str) -> list[dict[str, Any]]:
    """Conserve uniquement les dictionnaires d'une réponse API."""

    if not isinstance(items, list):
        logger.error(
            "Les résultats retournés par %s ne sont pas une liste.",
            source_name
        )
        return []

    valid_items = [item for item in items if isinstance(item, dict)]

    if ignored_count := len(items) - len(valid_items):
        logger.warning(
            "%s résultat(s) invalide(s) ignoré(s) pour %s.",
            ignored_count,
            source_name
        )

    return valid_items


# Moteur commun d'extraction

def extract_api_from_source(
    source: Mapping[str, Any],
    adapter: ApiAdapter,
) -> ApiExtractionResult:
    """Extrait, valide et déduplique les articles d'une source API."""

    # Validation des entrées
    if not isinstance(adapter, ApiAdapter):
        raise TypeError("adapter doit être une instance de ApiAdapter.")
    if not isinstance(source, Mapping):
        raise TypeError(
            f"Configuration invalide pour {adapter.default_name} : "
            f"{type(source).__name__}."
        )

    # Identité de la source
    source_id = normalize_value(source.get("source_id")) or adapter.source_id
    source_name = normalize_value(source.get("name")) or adapter.default_name

    if source_id != adapter.source_id:
        logger.warning(
            "L'adaptateur %s est utilisé avec la source %s.",
            adapter.source_id,
            source_id,
        )

    # Limites et métadonnées communes
    maximum_articles = get_api_max_articles(source)
    base_metadata = {
        "source_id": source_id,
        "maximum_articles": maximum_articles,
    }

    if maximum_articles <= 0:
        logger.info("Aucun article demandé pour %s.", source_name)
        return ExtractorResult(
            name=source_name,
            source_type=SOURCE_TYPE_API,
            status=EXTRACTOR_STATUS_EMPTY,
            message="Aucun article demandé.",
            metadata=base_metadata,
        )

    # Configuration des filtres
    filters = get_filter_configuration(source)

    if not isinstance(filters, Mapping):
        raise ValueError(
            f"Configuration des filtres invalide pour {source_name}."
        )

    remove_duplicates = parse_boolean(
        filters.get("remove_duplicates", True),
        default=True,
    )

    # État de l'extraction
    articles: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    rejection_stats: Counter[str] = Counter()
    processed_count = converted_count = 0
    fatal_error: ApiExtractionStoppedError | None = None

    # Isolation des compteurs de l'exécution courante
    request_count_token = reset_api_request_count()
    request_errors_token = reset_api_request_errors()

    try:
        try:
            items = adapter.iter_items(source, maximum_articles)

            if items is None:
                raise ValueError(
                    "Aucun itérateur retourné par l'adaptateur "
                    f"{adapter.source_id}."
                )

            # Transformation des éléments API
            for raw_item in items:
                if len(articles) >= maximum_articles:
                    break

                processed_count += 1

                if not isinstance(raw_item, tuple) or len(raw_item) != 3:
                    logger.debug(
                        "Élément d'itération invalide retourné par %s.",
                        adapter.source_id,
                    )
                    rejection_stats["element_iteration_invalide"] += 1
                    continue

                item_identifier, item_index, item = raw_item
                normalized_index = parse_non_negative_integer(
                    item_index,
                    processed_count - 1,
                )
                identifier = (
                    normalize_value(item_identifier)
                    or f"{source_id}:{normalized_index}"
                )

                article = build_article_from_api_item(
                    adapter=adapter,
                    item=item,
                    item_index=normalized_index,
                    item_identifier=identifier,
                    source=source,
                    source_name=source_name
                )

                if not article:
                    rejection_stats["construction_invalide"] += 1
                    continue

                # Certaines API placent le nom du média dans "source".
                # On le conserve comme éditeur avant de rétablir la source technique.
                publisher = normalize_value(
                    article.get("publisher") or article.get("source")
                )

                if publisher and publisher.casefold() != source_id.casefold():
                    article["publisher"] = publisher

                # La source technique doit toujours correspondre à l'extracteur utilisé.
                article["source"] = source_id
                article["source_type"] = SOURCE_TYPE_API
                article.setdefault("source_name", source_name)

                converted_count += 1

                # Validation métier commune
                valid, reason = validate_article_with_reason(article, filters)

                if not valid:
                    rejection_stats[
                        normalize_value(reason) or "article_invalide"
                    ] += 1
                    continue

                # Validation propre à l'adaptateur
                valid, reason = validate_api_item(
                    adapter=adapter,
                    item=item,
                    filters=filters,
                    source=source,
                )

                if not valid:
                    rejection_stats[
                        normalize_value(reason) or "element_rejete"
                    ] += 1
                    continue

                # Déduplication
                if remove_duplicates and is_duplicate_article(
                    article,
                    seen_keys,
                ):
                    rejection_stats["doublon"] += 1
                    continue

                if remove_duplicates:
                    register_article(article, seen_keys)

                articles.append(article)

        # Conservation d'une erreur fatale pour construire un résultat partiel
        except ApiExtractionStoppedError as error:
            fatal_error = error

        except httpx.HTTPStatusError as error:
            try:
                reason = raise_if_fatal_api_error(error, source_name)
            except ApiExtractionStoppedError as stopped_error:
                fatal_error = stopped_error
            else:
                fatal_error = ApiExtractionStoppedError(
                    source_name=source_name,
                    reason=reason,
                    status_code=error.response.status_code,
                )

        except httpx.HTTPError as error:
            safe_url = (
                sanitize_url_for_logging(str(error.request.url))
                if getattr(error, "request", None) is not None
                else ""
            )
            details = f", URL={safe_url}" if safe_url else ""
            raise RuntimeError(
                f"Erreur HTTP pendant l'extraction de {source_name} : "
                f"{type(error).__name__}{details}."
            ) from None

        except (OSError, ValueError, TypeError, UnicodeDecodeError) as error:
            safe_message = sanitize_url_for_logging(normalize_value(error))
            raise RuntimeError(
                f"Erreur pendant l'extraction de {source_name} : "
                f"{safe_message or type(error).__name__}."
            ) from None

        # Lecture des compteurs avant leur restauration
        request_errors = get_api_request_errors()
        requests_count = get_api_request_count()

    finally:
        _API_REQUEST_COUNT.reset(request_count_token)
        _API_REQUEST_ERRORS.reset(request_errors_token)

    # Une erreur fatale sans article reste gérée par l'executor générique
    if fatal_error is not None and not articles:
        raise fatal_error

    # Qualification de l'arrêt partiel
    fatal_reason = ""
    fatal_message = ""

    if fatal_error is not None:
        fatal_message = sanitize_url_for_logging(fatal_error.reason)

        if isinstance(fatal_error, ApiQuotaExceededError):
            fatal_reason = "quota_exceeded"
        elif isinstance(fatal_error, ApiAuthenticationError):
            fatal_reason = "authentication_failed"
        elif isinstance(fatal_error, ApiRateLimitError):
            fatal_reason = "rate_limited"
        elif isinstance(fatal_error, ApiInvalidParametersError):
            fatal_reason = "invalid_parameters"
        elif isinstance(fatal_error, ApiMaintenanceError):
            fatal_reason = "maintenance"
        else:
            fatal_reason = "api_extraction_stopped"

        request_errors.append(fatal_message)

    # Statistiques finales
    rejected_count = sum(rejection_stats.values())
    analyzed_count = max(
        processed_count,
        len(articles) + rejected_count,
    )
    metadata = {
        **base_metadata,
        "converted_count": converted_count,
        "processed_count": processed_count,
        "request_errors_count": len(request_errors),
    }

    if fatal_error is not None:
        metadata.update(
            {
                "partial_failure_reason": fatal_reason,
                "partial_error_type": type(fatal_error).__name__,
                "partial_error_status_code": fatal_error.status_code,
            }
        )

    # Journalisation du bilan
    logger.debug(
        "%s : %s requête(s), %s résultat(s), %s converti(s), "
        "%s conservé(s).",
        source_name,
        requests_count,
        processed_count,
        converted_count,
        len(articles),
    )
    log_extraction_summary(
        source_name=source_name,
        extracted_count=len(articles),
        processed_count=processed_count,
        rejection_stats=rejection_stats,
    )

    if processed_count == 0:
        logger.warning("%s : aucun résultat reçu depuis l'API.", source_name)
    elif converted_count == 0:
        logger.warning(
            "%s : %s résultat(s) reçu(s), aucun converti.",
            source_name,
            processed_count,
        )
    elif not articles:
        logger.warning(
            "%s : %s article(s) converti(s), tous rejetés.",
            source_name,
            converted_count,
        )
    elif len(articles) < maximum_articles:
        logger.info(
            "%s : %s article(s) valide(s) sur %s demandé(s).",
            source_name,
            len(articles),
            maximum_articles,
        )

    # Construction du statut final
    if fatal_error is not None:
        status = EXTRACTOR_STATUS_PARTIAL_SUCCESS
        message = (
            f"Extraction interrompue après {len(articles)} article(s) "
            f"conservé(s) : {fatal_message}"
        )
    elif request_errors:
        status = (
            EXTRACTOR_STATUS_PARTIAL_SUCCESS
            if articles
            else EXTRACTOR_STATUS_FAILED
        )
        message = (
            f"{len(request_errors)} requête(s) API en erreur, "
            f"{len(articles)} article(s) conservé(s)."
            if articles
            else f"{len(request_errors)} requête(s) API en erreur."
        )
    else:
        status = (
            EXTRACTOR_STATUS_SUCCESS
            if articles
            else EXTRACTOR_STATUS_EMPTY
        )
        message = (
            ""
            if articles
            else "Aucun article exploitable retourné par l'API."
        )

    return ExtractorResult(
        name=source_name,
        source_type=SOURCE_TYPE_API,
        status=status,
        message=message,
        articles=articles,
        errors=request_errors,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=dict(rejection_stats),
        metadata=metadata,
    )

# Objet commun représentant un extracteur API

@dataclass(slots=True)
class ApiExtractor:
    """Représente une API utilisant le moteur d'extraction commun."""

    source_id: str
    default_name: str
    adapter: ApiAdapter
    section_name: str = "api_sources"
    sources_file: Any = SOURCES_FILE

    _source: Mapping[str, Any] | None = field(
        default=None,
        init=False,
        repr=False
    )

    def __post_init__(self) -> None:
        """Normalise l'identité et contrôle la cohérence de l'adaptateur."""

        self.source_id = normalize_value(self.source_id)

        if not self.source_id:
            raise ValueError(
                "L'identifiant d'un ApiExtractor ne peut pas être vide."
            )

        if not isinstance(self.adapter, ApiAdapter):
            raise TypeError("adapter doit être une instance de ApiAdapter.")

        self.default_name = normalize_value(self.default_name) or self.source_id
        self.section_name = normalize_value(self.section_name)

        if not self.section_name:
            raise ValueError(
                "Le nom de section d'un ApiExtractor ne peut pas être vide."
            )

        if self.sources_file is None:
            raise ValueError(
                "Le fichier de configuration d'un ApiExtractor est requis."
            )

        if self.adapter.source_id != self.source_id:
            logger.warning(
                "ApiExtractor %s associé à l'adaptateur %s.",
                self.source_id,
                self.adapter.source_id
            )

    # Chargement de la configuration

    def load_source(self, force_reload: bool = False) -> Mapping[str, Any]:
        """Charge et met en cache la configuration de l'API."""

        if self._source is not None and not force_reload:
            return self._source

        source = load_validated_source(
            self.sources_file,
            self.source_id
        )
        self._source = dict(source)
        return self._source

    def reload_source(self) -> Mapping[str, Any]:
        """Recharge explicitement la configuration depuis le fichier."""

        return self.load_source(force_reload=True)

    @property
    def source(self) -> Mapping[str, Any]:
        """Retourne la configuration courante de l'API."""

        return self.load_source()

    # Lecture générique de la configuration

    def get_value(self, *keys: str, default: Any = None) -> Any:
        """Retourne la première valeur trouvée parmi plusieurs clés."""

        for key in keys:
            if key in self.source:
                return self.source[key]

        return default

    def get_string(self, *keys: str, default: str = "") -> str:
        """Retourne la première valeur textuelle disponible."""

        return normalize_value(self.get_value(*keys, default=default))

    def get_integer(self, *keys: str, default: int = 0) -> int:
        """Retourne la première valeur entière non négative."""

        return parse_non_negative_integer(
            self.get_value(*keys, default=default),
            default
        )

    def get_boolean(self, *keys: str, default: bool = False) -> bool:
        """Retourne la première valeur booléenne disponible."""

        return parse_boolean(
            self.get_value(*keys, default=default),
            default=default
        )

    def get_string_list(
        self,
        *keys: str,
        lowercase: bool = False,
        uppercase: bool = False
    ) -> list[str]:
        """Retourne une liste textuelle normalisée."""

        return normalize_string_list(
            self.get_value(*keys),
            lowercase=lowercase,
            uppercase=uppercase
        )

    # Propriétés générales

    @property
    def name(self) -> str:
        """Retourne le nom affiché de l'API."""

        return self.get_string("name") or self.default_name

    @property
    def configured_source_id(self) -> str:
        """Retourne l'identifiant déclaré dans la configuration."""

        return self.get_string("source_id") or self.source_id

    @property
    def enabled(self) -> bool:
        """Indique si la source est activée."""

        return self.get_boolean("enabled", default=False)

    @property
    def role(self) -> str:
        """Retourne le rôle des articles produits."""

        return self.get_string("role") or "acquisition"

    @property
    def endpoint(self) -> str:
        """Retourne le point d'accès validé de l'API."""

        return self.get_string("endpoint", "base_url", "url")

    @property
    def base_url(self) -> str:
        """Conserve l'ancien alias utilisé par certains adaptateurs."""

        return self.endpoint

    @property
    def max_articles(self) -> int:
        """Retourne le nombre maximal d'articles à extraire."""

        return get_api_max_articles(self.source)

    @property
    def max_article_age_days(self) -> int:
        """Retourne l'âge maximal autorisé pour les articles."""

        return get_api_max_article_age_days(self.source)

    @property
    def filters(self) -> Mapping[str, Any]:
        """Retourne les filtres appliqués à cette source."""

        filters = get_filter_configuration(self.source)
        return filters if isinstance(filters, Mapping) else {}

    @property
    def api_key_secret_name(self) -> str:
        """Retourne le nom du secret déclaré pour cette API."""

        return self.get_string("api_key_secret_name")


    @property
    def api_key(self) -> str:
        """Résout et retourne la clé API depuis l'environnement d'exécution."""

        secret_name = self.api_key_secret_name
        if not secret_name:
            return ""

        value = get_secret_or_environment(
            secret_name=secret_name,
            environment_variable=secret_name.upper(),
            required=True
        )
        return value or ""

    # Langues, pays, catégories et recherches

    @property
    def languages(self) -> list[str]:
        """Retourne les langues structurées ou historiques."""

        return get_query_configuration_values(
            self.source,
            "languages",
            "language",
            lowercase=True
        )

    @property
    def language(self) -> str:
        """Retourne la première langue configurée."""

        return self.languages[0] if self.languages else ""

    @property
    def countries(self) -> list[str]:
        """Retourne les pays structurés ou historiques."""

        return get_query_configuration_values(
            self.source,
            "countries",
            "country",
            lowercase=True
        )

    @property
    def country(self) -> str:
        """Retourne le premier pays configuré."""

        return self.countries[0] if self.countries else ""

    @property
    def categories(self) -> list[str]:
        """Retourne les catégories structurées ou historiques."""

        return get_query_configuration_values(
            self.source,
            "categories",
            "category",
            lowercase=True
        )

    @property
    def category(self) -> str:
        """Retourne la première catégorie configurée."""

        return self.categories[0] if self.categories else ""

    @property
    def queries_configuration(self) -> list[dict[str, Any]]:
        """Retourne les requêtes API structurées du contrat validé."""

        return get_queries_configuration(self.source)

    @property
    def keywords(self) -> list[str]:
        """Retourne les mots-clés structurés ou historiques."""

        return get_query_keywords(self.source)

    @property
    def keyword(self) -> str:
        """Retourne le premier mot-clé configuré."""

        return self.keywords[0] if self.keywords else ""

    # Pagination commune

    @property
    def pagination(self) -> Mapping[str, Any]:
        """Retourne la pagination validée de la source."""

        return get_pagination_configuration(self.source)

    def get_pagination_value(self, *keys: str, default: Any = None) -> Any:
        """Lit la pagination imbriquée puis les anciennes clés racine."""

        return get_pagination_value(self.source, *keys, default=default)

    def get_page_size(
        self,
        remaining_articles: int,
        default: int,
        maximum: int,
        *keys: str
    ) -> int:
        """Calcule une taille de page adaptée aux limites de l'API."""

        configured_keys = keys or ("page_size", "max_per_request")

        return calculate_page_size(
            configured_value=self.get_pagination_value(
                *configured_keys,
                default=default
            ),
            remaining_articles=remaining_articles,
            default=default,
            maximum=maximum
        )

    def get_max_pages(
        self,
        default: int,
        maximum: int,
        *keys: str
    ) -> int:
        """Retourne le nombre maximal de pages à parcourir."""

        configured_keys = keys or ("max_pages",)

        return normalize_page_limit(
            value=self.get_pagination_value(
                *configured_keys,
                default=default
            ),
            default=default,
            maximum=maximum
        )

    # Exécution API

    def validate_source(self, source: Mapping[str, Any]) -> None:
        """Valide les éléments techniques communs à toutes les API."""

        if not isinstance(source, Mapping):
            raise TypeError(
                "La configuration de l'API doit être un dictionnaire."
            )

        source_name = normalize_value(source.get("name")) or self.default_name
        endpoint = normalize_value(
            source.get("endpoint")
            or source.get("base_url")
            or source.get("url")
        )

        if not is_valid_http_url(endpoint):
            raise ValueError(
                f"URL absente ou invalide pour {source_name}."
            )

    def extract_from_source(
        self,
        source: Mapping[str, Any]
    ) -> ApiExtractionResult:
        """Valide puis exécute le moteur commun sur une source chargée."""

        self.validate_source(source)

        return extract_api_from_source(
            source=source,
            adapter=self.adapter
        )

    def extract(self) -> ApiExtractionResult:
        """Exécute directement l'API avec la configuration courante."""

        return self.extract_from_source(self.source)

    def run(self) -> ExtractorResult:
        """Délègue l'orchestration au service générique des extracteurs."""

        return execute_configured_extractor(
            extractor_name=self.default_name,
            source_type=SOURCE_TYPE_API,
            source_loader=self.reload_source,
            extraction_function=self.extract_from_source
        )