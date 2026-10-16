"""Tests du moteur commun des extracteurs d'API CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/apis/test_api_extractor.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

import src.extractors.apis.api_extractor as module
from src.extractors.apis.api_adapter import ApiAdapter
from src.extractors.apis.api_extractor import (
    ApiAuthenticationError,
    ApiExtractionStoppedError,
    ApiExtractor,
    ApiInvalidParametersError,
    ApiMaintenanceError,
    ApiQuotaExceededError,
    ApiRateLimitError,
    ApiRequestError,
    calculate_page_size,
    contains_api_error_pattern,
    execute_independent_api_requests,
    extract_api_error_codes,
    extract_api_from_source,
    fetch_json_object,
    filter_mapping_items,
    get_api_max_article_age_days,
    get_api_max_articles,
    get_api_request_count,
    get_api_request_errors,
    get_http_error_message,
    get_pagination_configuration,
    get_pagination_value,
    get_queries_configuration,
    get_query_configuration_values,
    get_query_keywords,
    increment_api_request_count,
    is_retryable_http_error,
    is_secret_key,
    join_normalized_values,
    mask_mapping_secrets,
    mask_secret,
    normalize_api_error_message,
    normalize_page_limit,
    normalize_string_list,
    raise_if_fatal_api_error,
    record_api_request_error,
    reset_api_request_count,
    reset_api_request_errors,
    validate_api_secret
)


# Construction des données

def build_article(
    item: Any,
    item_index: int,
    item_identifier: str,
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Construit un article minimal valide."""

    title = (
        item.get("title", "Titre suffisamment long")
        if isinstance(item, Mapping)
        else "Titre suffisamment long"
    )

    return {
        "id": item_identifier,
        "title": title,
        "text": "Contenu suffisamment long pour être accepté.",
        "url": f"https://example.com/{item_identifier}",
        "source": item.get("publisher", "")
        if isinstance(item, Mapping)
        else ""
    }


def iter_items(
    source: Mapping[str, Any],
    maximum_articles: int
) -> Iterator[tuple[str, int, Any]]:
    """Retourne deux éléments API pour les tests."""

    for index in range(min(maximum_articles, 2)):
        yield (
            f"item-{index}",
            index,
            {
                "title": f"Titre suffisamment long numéro {index}",
                "publisher": "Éditeur externe"
            }
        )


def build_adapter(
    *,
    source_id: str = "test_api",
    default_name: str = "Test API",
    iterator: Any = iter_items,
    builder: Any = build_article,
    validator: Any = None
) -> ApiAdapter:
    """Construit un adaptateur API minimal."""

    return ApiAdapter(
        source_id=source_id,
        default_name=default_name,
        iter_items=iterator,
        build_article=builder,
        validate_item=validator
    )


def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une configuration API minimale."""

    source = {
        "source_id": "test_api",
        "name": "Test API",
        "enabled": True,
        "endpoint": "https://example.com/api",
        "max_articles": 10,
        "max_article_age_days": 30,
        "filters": {
            "remove_duplicates": True,
            "require_title": True
        }
    }
    source.update(overrides)
    return source


def build_response(
    status_code: int = 200,
    *,
    json_data: Any = None,
    text: str = "",
    headers: Mapping[str, str] | None = None,
    reason_phrase: str | None = None,
    url: str = "https://example.com/api"
) -> httpx.Response:
    """Construit une réponse HTTPX exploitable dans les tests."""

    request = httpx.Request(
        "GET",
        url
    )

    response_headers = dict(headers or {})

    if json_data is not None:
        return httpx.Response(
            status_code=status_code,
            json=json_data,
            headers=response_headers,
            request=request,
            extensions={
                "reason_phrase": (
                    reason_phrase.encode()
                    if reason_phrase
                    else b""
                )
            }
        )

    return httpx.Response(
        status_code=status_code,
        text=text,
        headers=response_headers,
        request=request,
        extensions={
            "reason_phrase": (
                reason_phrase.encode()
                if reason_phrase
                else b""
            )
        }
    )


def build_http_status_error(
    status_code: int,
    *,
    json_data: Any = None,
    text: str = "",
    reason_phrase: str = ""
) -> httpx.HTTPStatusError:
    """Construit une erreur HTTPStatusError."""

    response = build_response(
        status_code=status_code,
        json_data=json_data,
        text=text,
        reason_phrase=reason_phrase
    )

    return httpx.HTTPStatusError(
        f"Erreur HTTP {status_code}",
        request=response.request,
        response=response
    )


@pytest.fixture(autouse=True)
def reset_request_context() -> Iterator[None]:
    """Isole les compteurs ContextVar entre les tests."""

    count_token = module._API_REQUEST_COUNT.set(0)
    errors_token = module._API_REQUEST_ERRORS.set(())

    yield

    module._API_REQUEST_COUNT.reset(count_token)
    module._API_REQUEST_ERRORS.reset(errors_token)


# Exceptions

def test_api_extraction_stopped_error_normalizes_values() -> None:
    error = ApiExtractionStoppedError(
        source_name="  Test API  ",
        reason="  Arrêt demandé  ",
        status_code=503
    )

    assert error.source_name == "Test API"
    assert error.reason == "Arrêt demandé"
    assert error.status_code == 503
    assert str(error) == "Arrêt demandé"


def test_api_extraction_stopped_error_uses_defaults() -> None:
    error = ApiExtractionStoppedError(
        source_name="",
        reason=""
    )

    assert error.source_name == "API inconnue"
    assert error.reason == "Erreur API non communiquée"
    assert error.status_code is None


def test_api_request_error_normalizes_values() -> None:
    error = ApiRequestError(
        source_name="  Test API  ",
        reason="  Requête refusée  ",
        status_code=400,
        parameters={
            "q": "actualité"
        }
    )

    assert error.source_name == "Test API"
    assert error.reason == "Requête refusée"
    assert error.status_code == 400
    assert error.parameters == {
        "q": "actualité"
    }
    assert str(error) == "Requête refusée"


def test_api_request_error_uses_defaults() -> None:
    error = ApiRequestError(
        source_name="",
        reason="",
        parameters=None
    )

    assert error.source_name == "API inconnue"
    assert error.reason == "Erreur API non communiquée"
    assert error.parameters == {}


@pytest.mark.parametrize(
    "error_class",
    [
        ApiQuotaExceededError,
        ApiAuthenticationError,
        ApiRateLimitError,
        ApiInvalidParametersError,
        ApiMaintenanceError
    ]
)
def test_specialized_errors_inherit_stopped_error(
    error_class: type[ApiExtractionStoppedError]
) -> None:
    error = error_class(
        "Test API",
        "Erreur",
        500
    )

    assert isinstance(
        error,
        ApiExtractionStoppedError
    )


# Normalisation des erreurs

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("  Erreur API  ", "Erreur API"),
        (42, "42"),
        (
            {
                "message": "Clé invalide"
            },
            "Clé invalide"
        ),
        (
            {
                "error": "error",
                "message": "Quota dépassé"
            },
            "Quota dépassé"
        ),
        (
            {
                "error": "failed"
            },
            "failed"
        ),
        (
            [
                "Erreur 1",
                "Erreur 1",
                "Erreur 2"
            ],
            "Erreur 1 | Erreur 2"
        ),
        (
            {
                "errors": [
                    {
                        "message": "Erreur imbriquée"
                    }
                ]
            },
            "Erreur imbriquée"
        )
    ]
)
def test_normalize_api_error_message(
    value: Any,
    expected: str
) -> None:
    assert normalize_api_error_message(value) == expected


def test_normalize_api_error_message_ignores_unknown_mapping_keys() -> None:
    assert normalize_api_error_message({
        "unknown": "Valeur"
    }) == ""


def test_extract_api_error_codes_from_nested_payload() -> None:
    payload = {
        "code": "invalid-api-key",
        "details": {
            "status": "quota exceeded",
            "items": [
                {
                    "error_code": "rate-limit"
                }
            ]
        }
    }

    result = extract_api_error_codes(payload)

    assert result == {
        "INVALID_API_KEY",
        "QUOTA_EXCEEDED",
        "RATE_LIMIT"
    }


def test_extract_api_error_codes_from_sequence() -> None:
    result = extract_api_error_codes([
        {
            "type": "authentication-error"
        },
        {
            "reason": "daily quota"
        }
    ])

    assert result == {
        "AUTHENTICATION_ERROR",
        "DAILY_QUOTA"
    }


def test_extract_api_error_codes_returns_empty_set() -> None:
    assert extract_api_error_codes("erreur") == set()


def test_get_http_error_message_reads_json() -> None:
    error = build_http_status_error(
        400,
        json_data={
            "message": "Paramètre invalide"
        }
    )

    assert get_http_error_message(error) == "Paramètre invalide"


def test_get_http_error_message_uses_text_when_json_invalid() -> None:
    error = build_http_status_error(
        500,
        text="Erreur serveur"
    )

    assert get_http_error_message(error) == "Erreur serveur"


def test_get_http_error_message_uses_reason_phrase() -> None:
    error = build_http_status_error(
        500,
        text="",
        reason_phrase="Internal Server Error"
    )

    assert get_http_error_message(error) == "Internal Server Error"


@pytest.mark.parametrize(
    ("message", "patterns", "expected"),
    [
        ("Invalid API key", ["api key"], True),
        ("QUOTA EXCEEDED", ["quota exceeded"], True),
        ("Service indisponible", ["quota", "token"], False),
        ("", ["error"], False)
    ]
)
def test_contains_api_error_pattern(
    message: str,
    patterns: Sequence[str],
    expected: bool
) -> None:
    assert contains_api_error_pattern(
        message,
        patterns
    ) is expected


# Qualification des erreurs

@pytest.mark.parametrize(
    "status_code",
    [
        401,
        403
    ]
)
def test_raise_if_fatal_api_error_detects_authentication_status(
    status_code: int
) -> None:
    with pytest.raises(ApiAuthenticationError) as captured:
        raise_if_fatal_api_error(
            {
                "message": "Accès refusé"
            },
            "Test API",
            status_code
        )

    assert captured.value.status_code == status_code


def test_raise_if_fatal_api_error_detects_authentication_pattern(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "API_AUTHENTICATION_ERROR_PATTERNS",
        (
            "invalid api key",
        )
    )

    with pytest.raises(ApiAuthenticationError):
        raise_if_fatal_api_error(
            "Invalid API key",
            "Test API"
        )


@pytest.mark.parametrize(
    "status_code",
    [
        429
    ]
)
def test_raise_if_fatal_api_error_detects_rate_limit(
    status_code: int
) -> None:
    with pytest.raises(ApiRateLimitError) as captured:
        raise_if_fatal_api_error(
            "Too many requests",
            "Test API",
            status_code
        )

    assert captured.value.status_code == status_code


def test_raise_if_fatal_api_error_detects_quota_status() -> None:
    with pytest.raises(ApiQuotaExceededError):
        raise_if_fatal_api_error(
            "Quota dépassé",
            "Test API",
            402
        )


def test_raise_if_fatal_api_error_detects_quota_code(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "API_QUOTA_ERROR_CODES",
        {
            "DAILY_LIMIT_EXCEEDED"
        }
    )

    with pytest.raises(ApiQuotaExceededError):
        raise_if_fatal_api_error(
            {
                "code": "daily limit exceeded"
            },
            "Test API"
        )


@pytest.mark.parametrize(
    "status_code",
    [
        400,
        404,
        422
    ]
)
def test_raise_if_fatal_api_error_detects_invalid_parameters(
    status_code: int
) -> None:
    with pytest.raises(ApiInvalidParametersError):
        raise_if_fatal_api_error(
            "Paramètres invalides",
            "Test API",
            status_code
        )


@pytest.mark.parametrize(
    "status_code",
    [
        502,
        503,
        504
    ]
)
def test_raise_if_fatal_api_error_detects_maintenance(
    status_code: int
) -> None:
    with pytest.raises(ApiMaintenanceError):
        raise_if_fatal_api_error(
            "Service indisponible",
            "Test API",
            status_code
        )


def test_raise_if_fatal_api_error_returns_reason_for_nonfatal_error() -> None:
    result = raise_if_fatal_api_error(
        "Erreur serveur",
        "Test API",
        500
    )

    assert result == "Erreur serveur"


def test_raise_if_fatal_api_error_uses_http_status_error() -> None:
    error = build_http_status_error(
        401,
        json_data={
            "message": "Authentification refusée"
        }
    )

    with pytest.raises(ApiAuthenticationError) as captured:
        raise_if_fatal_api_error(
            error,
            "Test API"
        )

    assert captured.value.reason == "Authentification refusée"
    assert captured.value.status_code == 401


# Secrets

def test_validate_api_secret_accepts_valid_secret(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_secret",
        lambda value: True
    )

    assert validate_api_secret(
        "secret",
        "API_KEY",
        "Test API"
    ) is True


def test_validate_api_secret_rejects_invalid_secret(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_valid_secret",
        lambda value: False
    )

    assert validate_api_secret(
        "",
        "API_KEY",
        "Test API"
    ) is False


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("api_key", True),
        ("access_token", True),
        ("client_secret", True),
        ("password", False),
        ("query", False),
        ("language", False),
        (None, False)
    ]
)
def test_is_secret_key(
    key: Any,
    expected: bool
) -> None:
    assert is_secret_key(key) is expected

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("secret", "********"),
        (" secret ", "********"),
        ("", ""),
        (" ", ""),
        (None, "")
    ]
)
def test_mask_secret(
    value: Any,
    expected: str
) -> None:
    assert mask_secret(value) == expected


def test_mask_mapping_secrets() -> None:
    result = mask_mapping_secrets({
        "api_key": "abcdef",
        "token": "123456",
        "query": "actualité",
        "page": 2
    })

    assert result == {
        "api_key": "********",
        "token": "********",
        "query": "actualité",
        "page": 2
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "mapping",
        42
    ]
)
def test_mask_mapping_secrets_rejects_invalid_mapping(
    value: Any
) -> None:
    assert mask_mapping_secrets(value) == {}


# Compteurs ContextVar

def test_api_request_count() -> None:
    token = reset_api_request_count()

    try:
        assert get_api_request_count() == 0

        increment_api_request_count()
        increment_api_request_count()

        assert get_api_request_count() == 2
    finally:
        module._API_REQUEST_COUNT.reset(token)


def test_api_request_errors() -> None:
    token = reset_api_request_errors()

    try:
        assert get_api_request_errors() == []

        first = record_api_request_error(
            "Erreur 1"
        )
        second = record_api_request_error(
            ValueError("Erreur 2")
        )

        assert first == "Erreur 1"
        assert second == "Erreur 2"
        assert get_api_request_errors() == [
            "Erreur 1",
            "Erreur 2"
        ]
    finally:
        module._API_REQUEST_ERRORS.reset(token)


def test_record_api_request_error_uses_default_message() -> None:
    assert record_api_request_error(None) == (
        "Erreur API non communiquée"
    )


def test_record_api_request_error_handles_http_error() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com"
    )
    error = httpx.ConnectError(
        "Connexion impossible",
        request=request
    )

    assert record_api_request_error(error) == "ConnectError"


def test_record_api_request_error_handles_http_status_error() -> None:
    error = build_http_status_error(
        500,
        json_data={
            "message": "Erreur distante"
        }
    )

    assert record_api_request_error(error) == "Erreur distante"


# Listes et configuration

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fr,en,fr", ["fr", "en"]),
        (["fr", "en", "fr"], ["fr", "en"]),
        (("fr", "", "en"), ["fr", "en"]),
        ({"fr", "en"}, ["fr", "en"]),
        (None, []),
        (42, []),
        ({}, [])
    ]
)
def test_normalize_string_list(
    value: Any,
    expected: list[str]
) -> None:
    result = normalize_string_list(value)

    if isinstance(value, set):
        assert set(result) == set(expected)
    else:
        assert result == expected


def test_normalize_string_list_lowercase() -> None:
    assert normalize_string_list(
        ["FR", "En"],
        lowercase=True
    ) == [
        "fr",
        "en"
    ]


def test_normalize_string_list_uppercase() -> None:
    assert normalize_string_list(
        ["fr", "En"],
        uppercase=True
    ) == [
        "FR",
        "EN"
    ]


def test_normalize_string_list_rejects_conflicting_case_options() -> None:
    with pytest.raises(
        ValueError,
        match="lowercase et uppercase"
    ):
        normalize_string_list(
            ["fr"],
            lowercase=True,
            uppercase=True
        )


def test_get_queries_configuration_accepts_mapping() -> None:
    source = {
        "queries": {
            "keywords": [
                "actualité"
            ]
        }
    }

    assert get_queries_configuration(source) == [
        {
            "keywords": [
                "actualité"
            ]
        }
    ]


def test_get_queries_configuration_filters_invalid_items() -> None:
    source = {
        "queries": [
            {
                "query": "ia"
            },
            None,
            "actualité",
            {
                "query": "science"
            }
        ]
    }

    assert get_queries_configuration(source) == [
        {
            "query": "ia"
        },
        {
            "query": "science"
        }
    ]


@pytest.mark.parametrize(
    "queries",
    [
        None,
        "actualité",
        "actualité".encode("utf-8"),
        42,
        object()
    ]
)
def test_get_queries_configuration_rejects_invalid_values(
    queries: Any
) -> None:
    assert get_queries_configuration({
        "queries": queries
    }) == []


def test_get_query_configuration_values_prefers_queries() -> None:
    source = {
        "queries": [
            {
                "languages": [
                    "FR",
                    "EN"
                ]
            },
            {
                "language": "DE"
            }
        ],
        "languages": [
            "ES"
        ]
    }

    assert get_query_configuration_values(
        source,
        "languages",
        "language",
        lowercase=True
    ) == [
        "fr",
        "en",
        "de"
    ]


def test_get_query_configuration_values_uses_root_fallback() -> None:
    source = {
        "language": "FR"
    }

    assert get_query_configuration_values(
        source,
        "languages",
        "language",
        lowercase=True
    ) == [
        "fr"
    ]


def test_get_query_keywords_prefers_structured_queries() -> None:
    source = {
        "queries": [
            {
                "keywords": [
                    "ia",
                    "science"
                ]
            },
            {
                "q": "climat"
            }
        ],
        "keywords": [
            "fallback"
        ]
    }

    assert get_query_keywords(source) == [
        "ia",
        "science",
        "climat"
    ]


def test_get_query_keywords_uses_root_fallback() -> None:
    assert get_query_keywords({
        "query": "actualité"
    }) == [
        "actualité"
    ]


def test_get_pagination_configuration() -> None:
    pagination = {
        "page_size": 50
    }

    assert get_pagination_configuration({
        "pagination": pagination
    }) == pagination


@pytest.mark.parametrize(
    "pagination",
    [
        None,
        [],
        "pagination",
        42
    ]
)
def test_get_pagination_configuration_rejects_invalid_values(
    pagination: Any
) -> None:
    assert get_pagination_configuration({
        "pagination": pagination
    }) == {}


def test_get_pagination_value_prefers_nested_configuration() -> None:
    source = {
        "pagination": {
            "page_size": 25
        },
        "page_size": 100
    }

    assert get_pagination_value(
        source,
        "page_size",
        default=10
    ) == 25


def test_get_pagination_value_uses_root_fallback() -> None:
    source = {
        "max_pages": 4
    }

    assert get_pagination_value(
        source,
        "max_pages",
        default=1
    ) == 4


def test_get_pagination_value_uses_default() -> None:
    assert get_pagination_value(
        {},
        "max_pages",
        default=3
    ) == 3


def test_join_normalized_values() -> None:
    assert join_normalized_values(
        [
            "fr",
            "en",
            "fr"
        ],
        separator="|"
    ) == "fr|en"


def test_join_normalized_values_rejects_invalid_separator() -> None:
    with pytest.raises(
        TypeError,
        match="separator doit être une chaîne"
    ):
        join_normalized_values(
            ["fr"],
            separator=42  # type: ignore[arg-type]
        )


def test_get_api_max_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLES_PER_SOURCE",
        100
    )

    assert get_api_max_articles({
        "max_articles": 25
    }) == 25
    assert get_api_max_articles({}) == 100


def test_get_api_max_article_age_days_is_limited_globally(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        30
    )

    assert get_api_max_article_age_days({
        "max_article_age_days": 90
    }) == 30


def test_get_api_max_article_age_days_uses_configured_value_when_global_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        0
    )

    assert get_api_max_article_age_days({
        "max_article_age_days": 12
    }) == 12


def test_get_api_max_article_age_days_uses_global_for_zero_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_ARTICLE_AGE_DAYS",
        30
    )

    assert get_api_max_article_age_days({
        "max_article_age_days": 0
    }) == 30


# Pagination

@pytest.mark.parametrize(
    (
        "configured",
        "remaining",
        "default",
        "maximum",
        "expected"
    ),
    [
        (50, 100, 20, 100, 50),
        (200, 100, 20, 50, 50),
        (50, 10, 20, 100, 10),
        (0, 100, 20, 100, 1),
        (-5, 100, 20, 100, 20),
        ("invalid", 100, 20, 100, 20),
        (10, 0, 20, 100, 1)
    ]
)
def test_calculate_page_size(
    configured: Any,
    remaining: int,
    default: int,
    maximum: int,
    expected: int
) -> None:
    assert calculate_page_size(
        configured,
        remaining,
        default,
        maximum
    ) == expected

@pytest.mark.parametrize(
    ("value", "default", "maximum", "expected"),
    [
        (5, 2, 10, 5),
        (50, 2, 10, 10),
        (0, 2, 10, 1),
        (-5, 2, 10, 2),
        ("invalid", 2, 10, 2)
    ]
)
def test_normalize_page_limit(
    value: Any,
    default: int,
    maximum: int,
    expected: int
) -> None:
    assert normalize_page_limit(
        value,
        default,
        maximum
    ) == expected


# Erreurs pouvant être retentées

def test_is_retryable_http_error_accepts_timeout() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com"
    )

    assert is_retryable_http_error(
        httpx.ReadTimeout(
            "Timeout",
            request=request
        )
    ) is True


def test_is_retryable_http_error_accepts_network_error() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com"
    )

    assert is_retryable_http_error(
        httpx.ConnectError(
            "Connexion impossible",
            request=request
        )
    ) is True


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, False),
        (401, False),
        (408, True),
        (429, True),
        (500, True),
        (503, True)
    ]
)
def test_is_retryable_http_status_error(
    status_code: int,
    expected: bool
) -> None:
    error = build_http_status_error(
        status_code,
        text="Erreur"
    )

    assert is_retryable_http_error(error) is expected


def test_is_retryable_http_error_rejects_other_errors() -> None:
    assert is_retryable_http_error(
        ValueError("Erreur")
    ) is False


# Client HTTP simulé

class FakeClient:
    """Client HTTPX simulé."""

    response: httpx.Response
    created_arguments: dict[str, Any] = {}
    get_arguments: dict[str, Any] = {}

    def __init__(
        self,
        *,
        headers: Mapping[str, str],
        timeout: float,
        follow_redirects: bool
    ) -> None:
        type(self).created_arguments = {
            "headers": dict(headers),
            "timeout": timeout,
            "follow_redirects": follow_redirects
        }

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any
    ) -> None:
        return None

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None
    ) -> httpx.Response:
        # params vaut None quand il n'y a pas de paramètres (voir fetch_json_object).
        type(self).get_arguments = {
            "url": url,
            "params": dict(params or {})
        }
        return type(self).response


@pytest.mark.parametrize(
    "url",
    [
        "",
        " ",
        "example.com/api",
        "ftp://example.com/api",
        None
    ]
)
def test_fetch_json_object_rejects_invalid_url(
    url: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="URL API absente ou invalide"
    ):
        fetch_json_object(url)


@pytest.mark.parametrize(
    "params",
    [
        [],
        "params",
        42
    ]
)
def test_fetch_json_object_rejects_invalid_params(
    params: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="params doit être un dictionnaire"
    ):
        fetch_json_object(
            "https://example.com/api",
            params=params
        )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        "headers",
        42
    ]
)
def test_fetch_json_object_rejects_invalid_headers(
    headers: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="headers doit être un dictionnaire"
    ):
        fetch_json_object(
            "https://example.com/api",
            headers=headers
        )


@pytest.mark.parametrize(
    "timeout",
    [
        0,
        -1,
        True,
        False,
        "10",
        None,
        float("inf"),
        float("nan")
    ]
)
def test_fetch_json_object_rejects_invalid_timeout(
    timeout: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="timeout doit être un nombre positif"
    ):
        fetch_json_object(
            "https://example.com/api",
            timeout=timeout
        )


def test_fetch_json_object_returns_json_object(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeClient.response = build_response(
        200,
        json_data={
            "articles": [
                {
                    "id": 1
                }
            ]
        },
        headers={
            "content-type": "application/json"
        }
    )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )

    result = fetch_json_object(
        "https://example.com/api",
        params={
            "q": "actualité"
        },
        headers={
            "X-Test": "value"
        },
        timeout=5
    )

    assert result == {
        "articles": [
            {
                "id": 1
            }
        ]
    }
    assert FakeClient.get_arguments == {
        "url": "https://example.com/api",
        "params": {
            "q": "actualité"
        }
    }
    assert FakeClient.created_arguments["timeout"] == 5.0
    assert FakeClient.created_arguments["follow_redirects"] is True
    assert FakeClient.created_arguments["headers"]["X-Test"] == "value"
    assert get_api_request_count() == 1


def test_fetch_json_object_returns_empty_mapping_for_204(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeClient.response = build_response(
        204
    )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )

    assert fetch_json_object(
        "https://example.com/api"
    ) == {}


def test_fetch_json_object_rejects_html_response(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeClient.response = build_response(
        200,
        text="<html>Erreur</html>",
        headers={
            "content-type": "text/html"
        }
    )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )

    with pytest.raises(
        ValueError,
        match="HTML au lieu de JSON"
    ):
        fetch_json_object(
            "https://example.com/api"
        )


def test_fetch_json_object_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeClient.response = build_response(
        200,
        text="not-json",
        headers={
            "content-type": "application/json"
        }
    )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )

    with pytest.raises(
        ValueError,
        match="JSON valide"
    ):
        fetch_json_object(
            "https://example.com/api"
        )


def test_fetch_json_object_rejects_json_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeClient.response = build_response(
        200,
        json_data=[
            {
                "id": 1
            }
        ],
        headers={
            "content-type": "application/json"
        }
    )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )

    with pytest.raises(
        ValueError,
        match="objet JSON"
    ):
        fetch_json_object(
            "https://example.com/api"
        )


def test_fetch_json_object_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeClient.response = build_response(
        401,
        json_data={
            "message": "Invalid API key"
        },
        headers={
            "content-type": "application/json"
        }
    )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        FakeClient
    )

    with pytest.raises(ApiAuthenticationError):
        fetch_json_object(
            "https://example.com/api"
        )


# Requêtes indépendantes

def test_execute_independent_api_requests_combines_results() -> None:
    captured_parameters: list[dict[str, Any]] = []

    def request_function(
        parameters: Mapping[str, Any]
    ) -> Sequence[Any]:
        captured_parameters.append(
            dict(parameters)
        )
        return [
            parameters["query"]
        ]

    result = execute_independent_api_requests(
        requests=[
            {
                "query": "actualité",
                "empty": "",
                "none": None,
                "list": [],
                "tuple": (),
                "mapping": {}
            },
            {
                "query": "science"
            }
        ],
        request_function=request_function,
        source_name="Test API"
    )

    assert result == [
        "actualité",
        "science"
    ]
    assert captured_parameters == [
        {
            "query": "actualité"
        },
        {
            "query": "science"
        }
    ]


def test_execute_independent_api_requests_rejects_invalid_function() -> None:
    with pytest.raises(
        TypeError,
        match="request_function doit être une fonction appelable"
    ):
        execute_independent_api_requests(
            requests=[],
            request_function=None,  # type: ignore[arg-type]
            source_name="Test API"
        )


def test_execute_independent_api_requests_ignores_invalid_requests() -> None:
    result = execute_independent_api_requests(
        requests=[
            None,  # type: ignore[list-item]
            {
                "query": "actualité"
            }
        ],
        request_function=lambda parameters: [
            parameters["query"]
        ],
        source_name="Test API"
    )

    assert result == [
        "actualité"
    ]
    assert len(get_api_request_errors()) == 1


def test_execute_independent_api_requests_ignores_none_result() -> None:
    result = execute_independent_api_requests(
        requests=[
            {
                "query": "actualité"
            }
        ],
        request_function=lambda parameters: None,  # type: ignore[return-value]
        source_name="Test API"
    )

    assert result == []


@pytest.mark.parametrize(
    "invalid_result",
    [
        "article",
        b"article",
        42,
        {
            "id": 1
        }
    ]
)
def test_execute_independent_api_requests_rejects_invalid_result(
    invalid_result: Any
) -> None:
    result = execute_independent_api_requests(
        requests=[
            {
                "query": "actualité"
            }
        ],
        request_function=lambda parameters: invalid_result,
        source_name="Test API"
    )

    assert result == []
    assert len(get_api_request_errors()) == 1


def test_execute_independent_api_requests_continues_after_request_error() -> None:
    calls = 0

    def request_function(
        parameters: Mapping[str, Any]
    ) -> Sequence[Any]:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise ApiRequestError(
                "Test API",
                "Requête rejetée",
                400,
                parameters
            )

        return [
            "article"
        ]

    result = execute_independent_api_requests(
        requests=[
            {
                "query": "invalid"
            },
            {
                "query": "valid"
            }
        ],
        request_function=request_function,
        source_name="Test API"
    )

    assert result == [
        "article"
    ]
    assert get_api_request_errors() == [
        "Requête rejetée"
    ]


def test_execute_independent_api_requests_propagates_fatal_error() -> None:
    def request_function(
        parameters: Mapping[str, Any]
    ) -> Sequence[Any]:
        raise ApiQuotaExceededError(
            "Test API",
            "Quota dépassé",
            402
        )

    with pytest.raises(ApiQuotaExceededError):
        execute_independent_api_requests(
            requests=[
                {
                    "query": "actualité"
                }
            ],
            request_function=request_function,
            source_name="Test API"
        )


def test_execute_independent_api_requests_handles_http_error() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com"
    )

    def request_function(
        parameters: Mapping[str, Any]
    ) -> Sequence[Any]:
        raise httpx.ConnectError(
            "Connexion impossible",
            request=request
        )

    result = execute_independent_api_requests(
        requests=[
            {
                "query": "actualité"
            }
        ],
        request_function=request_function,
        source_name="Test API"
    )

    assert result == []
    assert get_api_request_errors() == [
        "ConnectError"
    ]


# Filtrage des éléments

def test_filter_mapping_items() -> None:
    result = filter_mapping_items(
        [
            {
                "id": 1
            },
            "invalid",
            None,
            {
                "id": 2
            }
        ],
        "Test API"
    )

    assert result == [
        {
            "id": 1
        },
        {
            "id": 2
        }
    ]


@pytest.mark.parametrize(
    "items",
    [
        None,
        (),
        {},
        "items",
        42
    ]
)
def test_filter_mapping_items_rejects_non_list(
    items: Any
) -> None:
    assert filter_mapping_items(
        items,
        "Test API"
    ) == []


# Moteur d'extraction

def configure_successful_extraction(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configure les dépendances communes pour une extraction valide."""

    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: {
            "remove_duplicates": True
        }
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, filters: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_api_item",
        lambda adapter, item, filters, source: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: seen_keys.add(
            article["id"]
        )
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )


def test_extract_api_from_source_rejects_invalid_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de ApiAdapter"
    ):
        extract_api_from_source(
            build_source(),
            object()  # type: ignore[arg-type]
        )


def test_extract_api_from_source_rejects_invalid_source() -> None:
    with pytest.raises(
        TypeError,
        match="Configuration invalide"
    ):
        extract_api_from_source(
            None,  # type: ignore[arg-type]
            build_adapter()
        )


def test_extract_api_from_source_returns_empty_when_maximum_is_zero() -> None:
    result = extract_api_from_source(
        build_source(
            max_articles=0
        ),
        build_adapter()
    )

    assert result.status == module.EXTRACTOR_STATUS_EMPTY
    assert result.articles == []
    assert result.message == "Aucun article demandé."
    assert result.metadata["maximum_articles"] == 0


def test_extract_api_from_source_rejects_invalid_filters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: None
    )

    with pytest.raises(
        ValueError,
        match="Configuration des filtres invalide"
    ):
        extract_api_from_source(
            build_source(),
            build_adapter()
        )


def test_extract_api_from_source_returns_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    result = extract_api_from_source(
        build_source(
            max_articles=2
        ),
        build_adapter()
    )

    assert result.status == module.EXTRACTOR_STATUS_SUCCESS
    assert len(result.articles) == 2
    assert result.analyzed_count == 2
    assert result.rejected_count == 0
    assert result.metadata["processed_count"] == 2
    assert result.metadata["converted_count"] == 2

    first_article = result.articles[0]

    assert first_article["source"] == "test_api"
    assert first_article["source_type"] == module.SOURCE_TYPE_API
    assert first_article["source_name"] == "Test API"
    assert first_article["publisher"] == "Éditeur externe"


def test_extract_api_from_source_stops_at_maximum(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def many_items(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[tuple[str, int, Any]]:
        for index in range(10):
            yield (
                f"item-{index}",
                index,
                {
                    "title": f"Titre valide numéro {index}"
                }
            )

    result = extract_api_from_source(
        build_source(
            max_articles=3
        ),
        build_adapter(
            iterator=many_items
        )
    )

    assert len(result.articles) == 3
    assert result.analyzed_count == 3


def test_extract_api_from_source_rejects_invalid_iteration_item(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def invalid_items(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        yield "invalid"
        yield (
            "item-1",
            1,
            {
                "title": "Titre valide"
            }
        )

    result = extract_api_from_source(
        build_source(),
        build_adapter(
            iterator=invalid_items
        )
    )

    assert len(result.articles) == 1
    assert result.rejected_count == 1
    assert result.rejection_reasons == {
        "element_iteration_invalide": 1
    }


def test_extract_api_from_source_rejects_invalid_construction(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )
    monkeypatch.setattr(
        module,
        "build_article_from_api_item",
        lambda **kwargs: {}
    )

    result = extract_api_from_source(
        build_source(),
        build_adapter()
    )

    assert result.status == module.EXTRACTOR_STATUS_EMPTY
    assert result.rejected_count == 2
    assert result.rejection_reasons == {
        "construction_invalide": 2
    }


def test_extract_api_from_source_rejects_invalid_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, filters: (
            False,
            "titre_absent"
        )
    )

    result = extract_api_from_source(
        build_source(),
        build_adapter()
    )

    assert result.status == module.EXTRACTOR_STATUS_EMPTY
    assert result.rejection_reasons == {
        "titre_absent": 2
    }


def test_extract_api_from_source_rejects_adapter_validation(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )
    monkeypatch.setattr(
        module,
        "validate_api_item",
        lambda adapter, item, filters, source: (
            False,
            "element_rejete_source"
        )
    )

    result = extract_api_from_source(
        build_source(),
        build_adapter()
    )

    assert result.status == module.EXTRACTOR_STATUS_EMPTY
    assert result.rejection_reasons == {
        "element_rejete_source": 2
    }


def test_extract_api_from_source_rejects_duplicates(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    calls = 0

    def fake_duplicate(
        article: Mapping[str, Any],
        seen_keys: set[str]
    ) -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        fake_duplicate
    )

    result = extract_api_from_source(
        build_source(),
        build_adapter()
    )

    assert len(result.articles) == 1
    assert result.status == module.EXTRACTOR_STATUS_SUCCESS
    assert result.rejection_reasons == {
        "doublon": 1
    }


def test_extract_api_from_source_can_disable_deduplication(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )
    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: {
            "remove_duplicates": False
        }
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda *args: pytest.fail(
            "La déduplication ne devait pas être appelée."
        )
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda *args: pytest.fail(
            "L'enregistrement ne devait pas être appelé."
        )
    )

    result = extract_api_from_source(
        build_source(),
        build_adapter()
    )

    assert len(result.articles) == 2


def test_extract_api_from_source_raises_fatal_error_without_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def failing_iterator(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        raise ApiQuotaExceededError(
            "Test API",
            "Quota dépassé",
            402
        )
        yield

    with pytest.raises(ApiQuotaExceededError):
        extract_api_from_source(
            build_source(),
            build_adapter(
                iterator=failing_iterator
            )
        )


def test_extract_api_from_source_returns_partial_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def partial_iterator(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        yield (
            "item-1",
            0,
            {
                "title": "Titre valide"
            }
        )

        raise ApiRateLimitError(
            "Test API",
            "Trop de requêtes",
            429
        )

    result = extract_api_from_source(
        build_source(),
        build_adapter(
            iterator=partial_iterator
        )
    )

    assert result.status == module.EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert len(result.articles) == 1
    assert result.errors == [
        "Trop de requêtes"
    ]
    assert result.metadata["partial_failure_reason"] == "rate_limited"
    assert result.metadata["partial_error_type"] == "ApiRateLimitError"
    assert result.metadata["partial_error_status_code"] == 429


def test_extract_api_from_source_returns_failed_for_request_errors_without_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def iterator_with_request_error(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        record_api_request_error(
            "Erreur requête"
        )
        return
        yield

    result = extract_api_from_source(
        build_source(),
        build_adapter(
            iterator=iterator_with_request_error
        )
    )

    assert result.status == module.EXTRACTOR_STATUS_FAILED
    assert result.articles == []
    assert result.errors == [
        "Erreur requête"
    ]


def test_extract_api_from_source_returns_partial_for_request_errors_with_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def iterator_with_partial_error(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        record_api_request_error(
            "Erreur requête"
        )
        yield (
            "item-1",
            0,
            {
                "title": "Titre valide"
            }
        )

    result = extract_api_from_source(
        build_source(),
        build_adapter(
            iterator=iterator_with_partial_error
        )
    )

    assert result.status == module.EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert len(result.articles) == 1
    assert result.errors == [
        "Erreur requête"
    ]


def test_extract_api_from_source_wraps_value_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )

    def failing_iterator(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        raise ValueError("Réponse invalide")
        yield

    with pytest.raises(
        RuntimeError,
        match="Erreur pendant l'extraction de Test API"
    ):
        extract_api_from_source(
            build_source(),
            build_adapter(
                iterator=failing_iterator
            )
        )


def test_extract_api_from_source_wraps_http_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_successful_extraction(
        monkeypatch
    )
    request = httpx.Request(
        "GET",
        "https://example.com/api?api_key=secret"
    )

    def failing_iterator(
        source: Mapping[str, Any],
        maximum_articles: int
    ) -> Iterator[Any]:
        raise httpx.ConnectError(
            "Connexion impossible",
            request=request
        )
        yield

    with pytest.raises(
        RuntimeError,
        match="Erreur HTTP pendant l'extraction"
    ):
        extract_api_from_source(
            build_source(),
            build_adapter(
                iterator=failing_iterator
            )
        )


# ApiExtractor

def build_extractor(
    **overrides: Any
) -> ApiExtractor:
    """Construit un ApiExtractor minimal."""

    values = {
        "source_id": "test_api",
        "default_name": "Test API",
        "adapter": build_adapter(),
        "section_name": "api_sources",
        "sources_file": Path("sources.yaml")
    }
    values.update(overrides)

    return ApiExtractor(**values)


def test_api_extractor_normalizes_identity() -> None:
    extractor = build_extractor(
        source_id="  test_api  ",
        default_name="  Test API  ",
        section_name="  api_sources  "
    )

    assert extractor.source_id == "test_api"
    assert extractor.default_name == "Test API"
    assert extractor.section_name == "api_sources"


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        " ",
        None
    ]
)
def test_api_extractor_rejects_empty_source_id(
    source_id: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="identifiant d'un ApiExtractor"
    ):
        build_extractor(
            source_id=source_id
        )


def test_api_extractor_rejects_invalid_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de ApiAdapter"
    ):
        build_extractor(
            adapter=object()
        )


@pytest.mark.parametrize(
    "section_name",
    [
        "",
        " ",
        None
    ]
)
def test_api_extractor_rejects_empty_section_name(
    section_name: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="nom de section"
    ):
        build_extractor(
            section_name=section_name
        )


def test_api_extractor_rejects_missing_sources_file() -> None:
    with pytest.raises(
        ValueError,
        match="fichier de configuration"
    ):
        build_extractor(
            sources_file=None
        )


def test_api_extractor_load_source_and_cache(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_load(
        sources_file: Any,
        source_id: str
    ) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return build_source(
            name=f"Test API {calls}"
        )

    monkeypatch.setattr(
        module,
        "load_validated_source",
        fake_load
    )

    extractor = build_extractor()

    first = extractor.load_source()
    second = extractor.load_source()

    assert first is second
    assert calls == 1


def test_api_extractor_reload_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_load(
        sources_file: Any,
        source_id: str
    ) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return build_source(
            name=f"Version {calls}"
        )

    monkeypatch.setattr(
        module,
        "load_validated_source",
        fake_load
    )

    extractor = build_extractor()

    first = extractor.load_source()
    second = extractor.reload_source()

    assert first["name"] == "Version 1"
    assert second["name"] == "Version 2"
    assert calls == 2


def test_api_extractor_configuration_readers() -> None:
    extractor = build_extractor()
    extractor._source = {
        "name": "Mon API",
        "integer": "12",
        "boolean": "true",
        "languages": [
            "FR",
            "EN"
        ]
    }

    assert extractor.get_value(
        "missing",
        "name"
    ) == "Mon API"
    assert extractor.get_value(
        "missing",
        default="fallback"
    ) == "fallback"
    assert extractor.get_string(
        "name"
    ) == "Mon API"
    assert extractor.get_integer(
        "integer"
    ) == 12
    assert extractor.get_boolean(
        "boolean"
    ) is True
    assert extractor.get_string_list(
        "languages",
        lowercase=True
    ) == [
        "fr",
        "en"
    ]


def test_api_extractor_general_properties() -> None:
    extractor = build_extractor()
    extractor._source = {
        **build_source(),
        "role": "partenaire",
        "base_url": "https://example.com/base",
        "api_key_secret_name": "test_api_key"
    }
    extractor._source.pop("endpoint")

    assert extractor.name == "Test API"
    assert extractor.configured_source_id == "test_api"
    assert extractor.enabled is True
    assert extractor.role == "partenaire"
    assert extractor.endpoint == "https://example.com/base"
    assert extractor.base_url == "https://example.com/base"
    assert extractor.max_articles == 10
    assert extractor.max_article_age_days > 0
    assert extractor.api_key_secret_name == "test_api_key"


def test_api_extractor_default_properties() -> None:
    extractor = build_extractor()
    extractor._source = {
        "endpoint": "https://example.com/api"
    }

    assert extractor.name == "Test API"
    assert extractor.configured_source_id == "test_api"
    assert extractor.enabled is False
    assert extractor.role == "acquisition"


def test_api_extractor_filters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = build_extractor()
    extractor._source = build_source()

    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: {
            "require_title": True
        }
    )

    assert extractor.filters == {
        "require_title": True
    }


def test_api_extractor_filters_returns_empty_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = build_extractor()
    extractor._source = build_source()

    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: None
    )

    assert extractor.filters == {}


def test_api_extractor_api_key_without_secret_name() -> None:
    extractor = build_extractor()
    extractor._source = build_source()

    assert extractor.api_key == ""


def test_api_extractor_api_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_get_secret(
        *,
        secret_name: str,
        environment_variable: str,
        required: bool
    ) -> str:
        captured.update({
            "secret_name": secret_name,
            "environment_variable": environment_variable,
            "required": required
        })
        return "secret-value"

    monkeypatch.setattr(
        module,
        "get_secret_or_environment",
        fake_get_secret
    )

    extractor = build_extractor()
    extractor._source = {
        **build_source(),
        "api_key_secret_name": "test_api_key"
    }

    assert extractor.api_key == "secret-value"
    assert captured == {
        "secret_name": "test_api_key",
        "environment_variable": "TEST_API_KEY",
        "required": True
    }


def test_api_extractor_query_properties() -> None:
    extractor = build_extractor()
    extractor._source = {
        **build_source(),
        "queries": [
            {
                "languages": [
                    "FR",
                    "EN"
                ],
                "countries": [
                    "FR",
                    "US"
                ],
                "categories": [
                    "POLITICS",
                    "TECH"
                ],
                "keywords": [
                    "ia",
                    "science"
                ]
            }
        ]
    }

    assert extractor.languages == [
        "fr",
        "en"
    ]
    assert extractor.language == "fr"
    assert extractor.countries == [
        "fr",
        "us"
    ]
    assert extractor.country == "fr"
    assert extractor.categories == [
        "politics",
        "tech"
    ]
    assert extractor.category == "politics"
    assert extractor.keywords == [
        "ia",
        "science"
    ]
    assert extractor.keyword == "ia"
    assert len(extractor.queries_configuration) == 1


def test_api_extractor_empty_query_properties() -> None:
    extractor = build_extractor()
    extractor._source = build_source()

    assert extractor.language == ""
    assert extractor.country == ""
    assert extractor.category == ""
    assert extractor.keyword == ""


def test_api_extractor_pagination_methods() -> None:
    extractor = build_extractor()
    extractor._source = {
        **build_source(),
        "pagination": {
            "page_size": 25,
            "max_pages": 4
        }
    }

    assert extractor.pagination == {
        "page_size": 25,
        "max_pages": 4
    }
    assert extractor.get_pagination_value(
        "page_size"
    ) == 25
    assert extractor.get_page_size(
        remaining_articles=10,
        default=20,
        maximum=100
    ) == 10
    assert extractor.get_max_pages(
        default=1,
        maximum=10
    ) == 4


@pytest.mark.parametrize(
    "source",
    [
        None,
        [],
        "source",
        42
    ]
)
def test_api_extractor_validate_source_rejects_invalid_mapping(
    source: Any
) -> None:
    extractor = build_extractor()

    with pytest.raises(
        TypeError,
        match="configuration de l'API doit être un dictionnaire"
    ):
        extractor.validate_source(source)


@pytest.mark.parametrize(
    "source",
    [
        {},
        {
            "name": "Test API"
        },
        {
            "endpoint": "invalid"
        },
        {
            "url": "ftp://example.com"
        }
    ]
)
def test_api_extractor_validate_source_rejects_invalid_endpoint(
    source: Mapping[str, Any]
) -> None:
    extractor = build_extractor()

    with pytest.raises(
        ValueError,
        match="URL absente ou invalide"
    ):
        extractor.validate_source(source)


@pytest.mark.parametrize(
    "source",
    [
        {
            "endpoint": "https://example.com/api"
        },
        {
            "base_url": "https://example.com/api"
        },
        {
            "url": "https://example.com/api"
        }
    ]
)
def test_api_extractor_validate_source_accepts_valid_endpoint(
    source: Mapping[str, Any]
) -> None:
    extractor = build_extractor()

    extractor.validate_source(source)


def test_api_extractor_extract_from_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = build_extractor()
    source = build_source()
    expected = object()
    captured: dict[str, Any] = {}

    def fake_validate_source(
        self: ApiExtractor,
        current_source: Mapping[str, Any]
    ) -> None:
        captured["validated"] = current_source

    def fake_extract(
        *,
        source: Mapping[str, Any],
        adapter: ApiAdapter
    ) -> Any:
        captured.update({
            "source": source,
            "adapter": adapter
        })
        return expected

    monkeypatch.setattr(
        ApiExtractor,
        "validate_source",
        fake_validate_source
    )
    monkeypatch.setattr(
        module,
        "extract_api_from_source",
        fake_extract
    )

    result = extractor.extract_from_source(source)

    assert result is expected
    assert captured["validated"] is source
    assert captured["source"] is source
    assert captured["adapter"] is extractor.adapter
    
def test_api_extractor_extract(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = build_extractor()
    source = build_source()
    expected = object()
    captured: dict[str, Any] = {}

    extractor._source = source

    def fake_extract_from_source(
        self: ApiExtractor,
        current_source: Mapping[str, Any]
    ) -> Any:
        captured["source"] = current_source
        return expected

    monkeypatch.setattr(
        ApiExtractor,
        "extract_from_source",
        fake_extract_from_source
    )

    result = extractor.extract()

    assert result is expected
    assert captured["source"] is source

def test_api_extractor_run(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = build_extractor()
    expected = object()
    captured: dict[str, Any] = {}

    def fake_execute(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        module,
        "execute_configured_extractor",
        fake_execute
    )

    result = extractor.run()

    assert result is expected
    assert captured["extractor_name"] == "Test API"
    assert captured["source_type"] == module.SOURCE_TYPE_API
    assert captured["source_loader"] == extractor.reload_source
    assert captured["extraction_function"] == extractor.extract_from_source

# Ajout SortirIA (14/10/2026) : un lien « page suivante » garde ses paramètres.

def test_fetch_json_object_keeps_query_string_without_params(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"objects": []})

    monkeypatch.setattr(
        module.httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    next_link = "https://api.example.com/v1/events?geo_distance=43.6%2C3.8%2C20km&page_size=100&crs=abc"
    fetch_json_object(next_link)
    fetch_json_object("https://api.example.com/v1/events", params={"page": 2})
    assert requested == [next_link, "https://api.example.com/v1/events?page=2"]
