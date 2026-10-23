"""Tests du moteur commun d'orchestration RSS et Atom CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/rss copy/test_rss_engine.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from config.constants import (
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
    RSS_ERROR_ACCESS_DENIED,
    RSS_ERROR_EXTRACTION_STOPPED,
    RSS_ERROR_MAINTENANCE,
    RSS_ERROR_RATE_LIMITED,
    RSS_ERROR_SERVER,
    SOURCE_TYPE_RSS,
)
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.rss import rss_engine as module
from src.extractors.rss.rss_adapter import RssAdapter

# Fabriques

def iter_empty_items(
    source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Retourne une liste RSS vide."""

    return []


def iter_single_item(
    source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Retourne une entrée RSS valide."""

    return [{"title": "Article RSS de test"}]


def build_article(
    item: Any,
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Construit un article RSS minimal."""

    return {
        "title": item.get("title", ""),
        "text": "Contenu suffisamment long pour être considéré comme valide.",
        "url": "https://example.com/article",
        "source": source.get("name", "")
    }


def build_adapter(
    iter_items: Any = iter_single_item,
    article_builder: Any = build_article,
    validate_item: Any = None,
    validate_article: Any = None,
    default_name: str = "RSS Test"
) -> RssAdapter:
    """Construit un adaptateur RSS configurable."""

    return RssAdapter(
        iter_items=iter_items,
        build_article=article_builder,
        validate_item=validate_item,
        validate_article=validate_article,
        default_name=default_name
    )


def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une configuration RSS minimale."""

    source = {
        "name": "Flux RSS de test",
        "source_id": "rss_test",
        "type": SOURCE_TYPE_RSS,
        "enabled": True,
        "max_articles": 10,
        "filters": {
            "require_title": True,
            "require_text": False,
            "require_url": False,
            "min_title_length": 1,
            "min_total_text_length": 1,
            "remove_duplicates": True
        }
    }
    source.update(overrides)
    return source


def build_result(
    *,
    status: str = EXTRACTOR_STATUS_SUCCESS,
    articles: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    analyzed_count: int = 0,
    rejected_count: int = 0,
    requests_count: int = 0,
    rejection_reasons: dict[str, int] | None = None,
    name: str = "Flux RSS"
) -> ExtractorResult:
    """Construit un résultat d'extraction configurable."""

    return ExtractorResult(
        name=name,
        source_type=SOURCE_TYPE_RSS,
        status=status,
        articles=articles or [],
        errors=errors or [],
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=rejection_reasons or {}
    )


# Exceptions RSS

def test_rss_extraction_stopped_error_normalizes_values() -> None:
    error = module.RssExtractionStoppedError(
        source_name="  Flux RSS  ",
        reason="  Erreur distante  ",
        status_code=503
    )

    assert error.source_name == "Flux RSS"
    assert error.reason == "Erreur distante"
    assert error.status_code == 503
    assert str(error) == "Erreur distante"


@pytest.mark.parametrize(
    ("exception_class", "expected_code"),
    [
        (
            module.RssAccessDeniedError,
            RSS_ERROR_ACCESS_DENIED
        ),
        (
            module.RssRateLimitError,
            RSS_ERROR_RATE_LIMITED
        ),
        (
            module.RssMaintenanceError,
            RSS_ERROR_MAINTENANCE
        ),
        (
            module.RssServerError,
            RSS_ERROR_SERVER
        )
    ]
)
def test_specialized_rss_errors_define_expected_error_code(
    exception_class: type[module.RssExtractionStoppedError],
    expected_code: str
) -> None:
    error = exception_class("Flux", "Erreur")

    assert error.error_code == expected_code


def test_rss_request_error_normalizes_default_values() -> None:
    error = module.RssRequestError("", "", None)

    assert error.source_name == "source RSS"
    assert error.reason == "Erreur RSS non communiquée"
    assert error.status_code is None


def test_get_rss_failure_reason_returns_specialized_code() -> None:
    error = module.RssAccessDeniedError(
        "Flux",
        "Accès refusé",
        403
    )

    assert module.get_rss_failure_reason(error) == RSS_ERROR_ACCESS_DENIED


def test_get_rss_failure_reason_uses_default_code() -> None:
    error = module.RssExtractionStoppedError("Flux", "Erreur")
    error.error_code = ""

    assert (
        module.get_rss_failure_reason(error)
        == RSS_ERROR_EXTRACTION_STOPPED
    )


# Compteurs et erreurs HTTP

def test_rss_request_count_can_be_reset_and_incremented() -> None:
    token = module.reset_rss_request_count()

    try:
        assert module.get_rss_request_count() == 0

        module.increment_rss_request_count()
        module.increment_rss_request_count()

        assert module.get_rss_request_count() == 2

    finally:
        module._RSS_REQUEST_COUNT.reset(token)


def test_rss_request_errors_can_be_reset() -> None:
    token = module.reset_rss_request_errors()

    try:
        module.record_rss_request_error("Première erreur")
        module.record_rss_request_error("Deuxième erreur")

        assert module.get_rss_request_errors() == [
            "Première erreur",
            "Deuxième erreur"
        ]

    finally:
        module._RSS_REQUEST_ERRORS.reset(token)


def test_record_rss_request_error_uses_specialized_reason() -> None:
    token = module.reset_rss_request_errors()

    try:
        error = module.RssRequestError(
            "Flux",
            "Erreur contrôlée",
            500
        )

        result = module.record_rss_request_error(error)

        assert result == "Erreur contrôlée"
        assert module.get_rss_request_errors() == [
            "Erreur contrôlée"
        ]

    finally:
        module._RSS_REQUEST_ERRORS.reset(token)


def test_record_rss_request_error_uses_http_error_type() -> None:
    token = module.reset_rss_request_errors()

    try:
        request = httpx.Request(
            "GET",
            "https://example.com/feed.xml"
        )
        error = httpx.ConnectError(
            "Connexion impossible",
            request=request
        )

        result = module.record_rss_request_error(error)

        assert result == "ConnectError"
        assert module.get_rss_request_errors() == ["ConnectError"]

    finally:
        module._RSS_REQUEST_ERRORS.reset(token)


def test_get_http_error_message_uses_response_text() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/feed.xml?token=secret"
    )
    response = httpx.Response(
        403,
        request=request,
        text="Accès interdit"
    )
    error = httpx.HTTPStatusError(
        "Erreur HTTP",
        request=request,
        response=response
    )

    assert module.get_http_error_message(error) == "Accès interdit"


@pytest.mark.parametrize(
    ("status_code", "expected_exception"),
    [
        (401, module.RssAccessDeniedError),
        (403, module.RssAccessDeniedError),
        (429, module.RssRateLimitError),
        (500, module.RssServerError),
        (502, module.RssMaintenanceError),
        (503, module.RssMaintenanceError),
        (504, module.RssMaintenanceError)
    ]
)
def test_raise_if_fatal_rss_error_raises_specialized_exception(
    status_code: int,
    expected_exception: type[module.RssExtractionStoppedError]
) -> None:
    with pytest.raises(expected_exception) as caught:
        module.raise_if_fatal_rss_error(
            error=f"Erreur HTTP {status_code}",
            source_name="Flux RSS",
            status_code=status_code
        )

    assert caught.value.status_code == status_code


def test_raise_if_fatal_rss_error_returns_non_fatal_reason() -> None:
    result = module.raise_if_fatal_rss_error(
        error="Erreur HTTP temporaire",
        source_name="Flux RSS",
        status_code=400
    )

    assert result == "Erreur HTTP temporaire"


# Contexte d'extraction

def test_rss_extraction_context_returns_source_properties() -> None:
    context = module.RssExtractionContext(
        source={
            "name": "  Flux principal  ",
            "source_id": "  rss_principal  "
        },
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    assert context.source_name == "Flux principal"
    assert context.source_id == "rss_principal"


def test_rss_extraction_context_uses_source_name_as_identifier() -> None:
    context = module.RssExtractionContext(
        source={"name": "Flux principal"},
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    assert context.source_id == "Flux principal"


def test_rss_extraction_context_completed_when_limit_is_reached() -> None:
    context = module.RssExtractionContext(
        source={"name": "Flux"},
        filters={},
        maximum_articles=1,
        remove_duplicates=False,
        articles=[{"title": "Article"}]
    )

    assert context.completed is True


def test_rss_extraction_context_reject_normalizes_reason() -> None:
    context = module.RssExtractionContext(
        source={"name": "Flux"},
        filters={},
        maximum_articles=10,
        remove_duplicates=False
    )

    context.reject("  titre_manquant  ")
    context.reject("")

    assert context.rejection_stats == {
        "titre_manquant": 1,
        "article_invalide": 1
    }


def test_rss_extraction_context_adds_article_without_deduplication() -> None:
    context = module.RssExtractionContext(
        source={"name": "Flux"},
        filters={},
        maximum_articles=10,
        remove_duplicates=False
    )
    article = {"title": "Article"}

    context.add_article(article)

    assert context.articles == [article]
    assert context.seen_keys == set()


def test_rss_extraction_context_registers_article_when_enabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def fake_register_article(
        article: Mapping[str, Any],
        seen_keys: set[str]
    ) -> None:
        received["article"] = article
        received["seen_keys"] = seen_keys
        seen_keys.add("article-key")

    monkeypatch.setattr(
        module,
        "register_article",
        fake_register_article
    )

    context = module.RssExtractionContext(
        source={"name": "Flux"},
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )
    article = {"title": "Article"}

    context.add_article(article)

    assert context.articles == [article]
    assert context.seen_keys == {"article-key"}
    assert received["article"] is article


# Contexte de lot

def test_rss_batch_context_aggregates_success_result() -> None:
    context = module.RssBatchContext()
    result = build_result(
        articles=[{"title": "Article"}],
        analyzed_count=2,
        rejected_count=1,
        requests_count=3,
        rejection_reasons={"titre_manquant": 1}
    )

    context.add_result(result)

    assert context.successful_sources == 1
    assert context.partial_sources == 0
    assert context.failed_sources == 0
    assert context.empty_sources == 0
    assert context.analyzed_count == 2
    assert context.rejected_count == 1
    assert context.requests_count == 3
    assert context.rejection_stats == {"titre_manquant": 1}
    assert context.articles == [{"title": "Article"}]


@pytest.mark.parametrize(
    ("status", "articles", "errors", "expected_attribute"),
    [
        (
            EXTRACTOR_STATUS_SUCCESS,
            [{"title": "Article réussi"}],
            [],
            "successful_sources"
        ),
        (
            EXTRACTOR_STATUS_PARTIAL_SUCCESS,
            [{"title": "Article partiel"}],
            ["Erreur RSS partielle"],
            "partial_sources"
        ),
        (
            EXTRACTOR_STATUS_EMPTY,
            [],
            [],
            "empty_sources"
        ),
        (
            EXTRACTOR_STATUS_FAILED,
            [],
            ["Erreur RSS"],
            "failed_sources"
        )
    ]
)
def test_rss_batch_context_counts_source_status(
    status: str,
    articles: list[dict[str, Any]],
    errors: list[str],
    expected_attribute: str
) -> None:
    context = module.RssBatchContext()

    context.add_result(
        build_result(
            status=status,
            articles=articles,
            errors=errors
        )
    )

    assert getattr(context, expected_attribute) == 1

def test_rss_batch_context_rejects_batch_duplicate(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: True
    )

    context = module.RssBatchContext()
    result = build_result(
        articles=[{"title": "Article dupliqué"}]
    )

    context.add_result(result)

    assert context.articles == []
    assert context.duplicate_count == 1
    assert context.rejected_count == 1
    assert context.rejection_stats["doublon_lot"] == 1


# RssExtractor

def test_rss_extractor_requires_rss_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de RssAdapter."
    ):
        module.RssExtractor(
            adapter=object()  # type: ignore[arg-type]
        )


def test_rss_extractor_requires_path_source_file() -> None:
    with pytest.raises(
        TypeError,
        match="sources_file doit être une instance de Path."
    ):
        module.RssExtractor(
            adapter=build_adapter(),
            sources_file="sources.yaml"  # type: ignore[arg-type]
        )


def test_rss_extractor_reload_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    registry = object()

    monkeypatch.setattr(
        module,
        "load_source_registry",
        lambda path, require_secrets: registry
    )

    extractor = module.RssExtractor(
        adapter=build_adapter(),
        sources_file=tmp_path / "sources.yaml"
    )

    result = extractor.reload_sources()

    assert result is registry
    assert extractor._registry is registry


def test_rss_extractor_get_registry_uses_cache(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = module.RssExtractor(adapter=build_adapter())
    registry = object()
    extractor._registry = registry  # type: ignore[assignment]

    def failing_reload_sources(
        self: module.RssExtractor
    ) -> Any:
        pytest.fail("reload_sources ne doit pas être appelé")

    monkeypatch.setattr(
        module.RssExtractor,
        "reload_sources",
        failing_reload_sources
    )

    assert extractor.get_registry() is registry


def test_rss_extractor_extract_source_returns_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_articles = [{"title": "Article"}]

    monkeypatch.setattr(
        module,
        "extract_rss_source",
        lambda source, adapter: build_result(
            articles=expected_articles
        )
    )

    extractor = module.RssExtractor(adapter=build_adapter())

    result = extractor.extract_source(build_source())

    assert result == expected_articles


def test_rss_extractor_extract_sources_returns_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_articles = [{"title": "Article"}]

    monkeypatch.setattr(
        module,
        "extract_rss_sources",
        lambda sources, adapter: build_result(
            articles=expected_articles
        )
    )

    extractor = module.RssExtractor(adapter=build_adapter())

    result = extractor.extract_sources([build_source()])

    assert result == expected_articles


def test_rss_extractor_run_delegates_to_common_service(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}
    expected_result = build_result()

    def fake_execute_configured_sources_extractor(
        **kwargs: Any
    ) -> ExtractorResult:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "execute_configured_sources_extractor",
        fake_execute_configured_sources_extractor
    )

    extractor = module.RssExtractor(adapter=build_adapter())

    result = extractor.run()

    assert result is expected_result
    assert received["extractor_name"] == "RSS Test"
    assert received["source_type"] == SOURCE_TYPE_RSS
    assert received["sources_loader"] == extractor.get_sources
    assert (
        received["extraction_function"]
        == extractor.extract_sources_result
    )


# Création du contexte

def test_create_context_builds_expected_context(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: {
            "remove_duplicates": False
        }
    )

    context = module.create_context(
        build_source(max_articles="12")
    )

    assert context.source_name == "Flux RSS de test"
    assert context.source_id == "rss_test"
    assert context.maximum_articles == 12
    assert context.remove_duplicates is False
    assert context.filters == {
        "remove_duplicates": False
    }


def test_create_context_rejects_invalid_filters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: None
    )

    with pytest.raises(
        ValueError,
        match="Configuration des filtres RSS invalide."
    ):
        module.create_context(build_source())


# Traitement des items

def test_process_rss_item_rejects_invalid_item(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (
            False,
            "entree_invalide_test"
        )
    )

    context = module.RssExtractionContext(
        source=build_source(),
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    module.process_rss_item(
        item={"title": ""},
        context=context,
        adapter=build_adapter()
    )

    assert context.processed_count == 1
    assert context.converted_count == 0
    assert context.articles == []
    assert context.rejection_stats == {
        "entree_invalide_test": 1
    }


@pytest.mark.parametrize(
    "error",
    [
        OSError("Erreur disque"),
        ValueError("Valeur invalide"),
        TypeError("Type invalide"),
        UnicodeDecodeError(
            "utf-8",
            b"x",
            0,
            1,
            "Décodage impossible"
        ),
        RuntimeError("Erreur inattendue")
    ]
)
def test_process_rss_item_rejects_build_error(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )

    def failing_builder(
        item: Any,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        raise error

    context = module.RssExtractionContext(
        source=build_source(),
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    module.process_rss_item(
        item={"title": "Article"},
        context=context,
        adapter=build_adapter(article_builder=failing_builder)
    )

    assert context.processed_count == 1
    assert context.converted_count == 0
    assert context.articles == []
    assert context.rejection_stats == {
        "construction_invalide": 1
    }


@pytest.mark.parametrize(
    "article",
    [
        None,
        {},
        [],
        "article"
    ]
)
def test_process_rss_item_rejects_empty_or_invalid_article(
    monkeypatch: pytest.MonkeyPatch,
    article: Any
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )

    adapter = build_adapter(
        article_builder=lambda item, source: article
    )
    context = module.RssExtractionContext(
        source=build_source(),
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    module.process_rss_item(
        item={"title": "Article"},
        context=context,
        adapter=adapter
    )

    assert context.rejection_stats == {"article_vide": 1}
    assert context.converted_count == 0


def test_process_rss_item_normalizes_article_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "validate_rss_article",
        lambda article, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: None
    )

    def article_builder(
        item: Any,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "title": "Article RSS",
            "source": "BBC News"
        }

    context = module.RssExtractionContext(
        source=build_source(
            name="Flux technique",
            source_id="rss_bbc"
        ),
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    module.process_rss_item(
        item={"title": "Article RSS"},
        context=context,
        adapter=build_adapter(article_builder=article_builder)
    )

    assert context.converted_count == 1
    assert len(context.articles) == 1

    article = context.articles[0]

    assert article["source"] == "rss_bbc"
    assert article["source_type"] == SOURCE_TYPE_RSS
    assert article["source_name"] == "Flux technique"
    assert article["publisher"] == "BBC News"


def test_process_rss_item_rejects_invalid_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "validate_rss_article",
        lambda article, context, adapter: (
            False,
            "article_non_conforme"
        )
    )

    context = module.RssExtractionContext(
        source=build_source(),
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    module.process_rss_item(
        item={"title": "Article RSS"},
        context=context,
        adapter=build_adapter()
    )

    assert context.converted_count == 1
    assert context.articles == []
    assert context.rejection_stats == {
        "article_non_conforme": 1
    }


def test_process_rss_item_rejects_duplicate(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "validate_rss_article",
        lambda article, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: True
    )

    context = module.RssExtractionContext(
        source=build_source(),
        filters={},
        maximum_articles=10,
        remove_duplicates=True
    )

    module.process_rss_item(
        item={"title": "Article RSS"},
        context=context,
        adapter=build_adapter()
    )

    assert context.articles == []
    assert context.rejection_stats == {"doublon": 1}


# Extraction d'une source

def test_extract_rss_source_requires_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de RssAdapter."
    ):
        module.extract_rss_source(
            source=build_source(),
            adapter=object()  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "source",
    [
        None,
        [],
        "source"
    ]
)
def test_extract_rss_source_requires_mapping(
    source: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="Configuration RSS invalide"
    ):
        module.extract_rss_source(
            source=source,
            adapter=build_adapter()
        )


def test_extract_rss_source_returns_empty_when_disabled() -> None:
    result = module.extract_rss_source(
        source=build_source(enabled=False),
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert result.message == "Source RSS désactivée."
    assert result.articles == []


@pytest.mark.parametrize(
    "maximum_articles",
    [
        0,
        "0",
        -1
    ]
)
def test_extract_rss_source_returns_empty_when_limit_is_zero(
    maximum_articles: Any
) -> None:
    result = module.extract_rss_source(
        source=build_source(max_articles=maximum_articles),
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert result.message == "Aucun article demandé."


def test_extract_rss_source_rejects_none_items() -> None:
    adapter = build_adapter(
        iter_items=lambda source: None
    )

    with pytest.raises(
        RuntimeError,
        match="Aucune séquence retournée"
    ):
        module.extract_rss_source(
            source=build_source(),
            adapter=adapter
        )


@pytest.mark.parametrize(
    "items",
    [
        "article",
        b"article",
        {"title": "Article"},
        iter([{"title": "Article"}])
    ]
)
def test_extract_rss_source_requires_sequence(
    items: Any
) -> None:
    adapter = build_adapter(
        iter_items=lambda source: items
    )

    with pytest.raises(
        RuntimeError,
        match="doit retourner une séquence"
    ):
        module.extract_rss_source(
            source=build_source(),
            adapter=adapter
        )


def test_extract_rss_source_stops_at_maximum_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    processed_items: list[int] = []

    def fake_process_rss_item(
        item: Any,
        context: module.RssExtractionContext,
        adapter: RssAdapter
    ) -> None:
        processed_items.append(item)
        context.articles.append({"title": f"Article {item}"})

    monkeypatch.setattr(
        module,
        "process_rss_item",
        fake_process_rss_item
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )

    adapter = build_adapter(
        iter_items=lambda source: [1, 2, 3]
    )

    result = module.extract_rss_source(
        source=build_source(max_articles=2),
        adapter=adapter
    )

    assert processed_items == [1, 2]
    assert len(result.articles) == 2
    assert result.status == EXTRACTOR_STATUS_SUCCESS


def test_extract_rss_source_returns_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "validate_rss_article",
        lambda article, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: None
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )

    result = module.extract_rss_source(
        source=build_source(),
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_SUCCESS
    assert result.message == ""
    assert len(result.articles) == 1
    assert result.analyzed_count == 1
    assert result.rejected_count == 0
    assert result.metadata["processed_count"] == 1
    assert result.metadata["converted_count"] == 1


def test_extract_rss_source_returns_empty_without_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )

    result = module.extract_rss_source(
        source=build_source(),
        adapter=build_adapter(iter_items=iter_empty_items)
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert result.articles == []
    assert (
        result.message
        == "Aucun article exploitable retourné par le flux RSS."
    )


def test_extract_rss_source_raises_fatal_error_without_article() -> None:
    def failing_iterator(
        source: Mapping[str, Any]
    ) -> Iterable[Any]:
        raise module.RssAccessDeniedError(
            "Flux RSS",
            "Accès refusé",
            403
        )

    with pytest.raises(module.RssAccessDeniedError):
        module.extract_rss_source(
            source=build_source(),
            adapter=build_adapter(iter_items=failing_iterator)
        )


def test_extract_rss_source_returns_partial_success_after_fatal_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "validate_rss_item",
        lambda item, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "validate_rss_article",
        lambda article, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: None
    )

    def partial_iterator(
        source: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        return [{"title": "Article"}]

    adapter = build_adapter(iter_items=partial_iterator)

    original_process = module.process_rss_item

    def process_then_fail(
        item: Any,
        context: module.RssExtractionContext,
        current_adapter: RssAdapter
    ) -> None:
        original_process(item, context, current_adapter)
        raise module.RssRateLimitError(
            context.source_name,
            "Trop de requêtes",
            429
        )

    monkeypatch.setattr(
        module,
        "process_rss_item",
        process_then_fail
    )

    result = module.extract_rss_source(
        source=build_source(),
        adapter=adapter
    )

    assert result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert len(result.articles) == 1
    assert result.errors == ["Trop de requêtes"]
    assert (
        result.metadata["partial_failure_reason"]
        == RSS_ERROR_RATE_LIMITED
    )
    assert (
        result.metadata["partial_error_type"]
        == "RssRateLimitError"
    )
    assert result.metadata["partial_error_status_code"] == 429


def test_extract_rss_source_converts_timeout_to_specialized_error() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/feed.xml"
    )

    def failing_iterator(
        source: Mapping[str, Any]
    ) -> Iterable[Any]:
        raise httpx.ReadTimeout(
            "Délai dépassé",
            request=request
        )

    with pytest.raises(module.RssTimeoutError):
        module.extract_rss_source(
            source=build_source(),
            adapter=build_adapter(iter_items=failing_iterator)
        )


def test_extract_rss_source_converts_network_error() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/feed.xml"
    )

    def failing_iterator(
        source: Mapping[str, Any]
    ) -> Iterable[Any]:
        raise httpx.ConnectError(
            "Connexion impossible",
            request=request
        )

    with pytest.raises(module.RssNetworkError):
        module.extract_rss_source(
            source=build_source(),
            adapter=build_adapter(iter_items=failing_iterator)
        )


# Résultat d'échec

def test_build_failed_source_result_from_specialized_error() -> None:
    error = module.RssAccessDeniedError(
        "Flux RSS",
        "Accès refusé",
        403
    )

    result = module.build_failed_source_result(
        source_name="Flux RSS",
        error=error,
        source_id="rss_flux"
    )

    assert result.status == EXTRACTOR_STATUS_FAILED
    assert result.message == "Accès refusé"
    assert result.errors == ["Accès refusé"]
    assert result.metadata == {
        "source_id": "rss_flux",
        "failure_reason": RSS_ERROR_ACCESS_DENIED,
        "error_type": "RssAccessDeniedError",
        "error_status_code": 403
    }


def test_build_failed_source_result_from_unexpected_error() -> None:
    result = module.build_failed_source_result(
        source_name="Flux RSS",
        error=ValueError("Configuration invalide"),
        source_id="rss_flux"
    )

    assert result.status == EXTRACTOR_STATUS_FAILED
    assert result.message == "Configuration invalide"
    assert result.metadata == {
        "source_id": "rss_flux",
        "failure_reason": RSS_ERROR_EXTRACTION_STOPPED,
        "error_type": "ValueError"
    }


def test_build_failed_source_result_does_not_record_current_errors() -> None:
    token = module.reset_rss_request_errors()

    try:
        module.build_failed_source_result(
            source_name="Flux RSS",
            error=ValueError("Configuration invalide")
        )

        # L'échec reste dans le résultat, hors des erreurs RSS courantes.
        assert module.get_rss_request_errors() == []
    finally:
        module._RSS_REQUEST_ERRORS.reset(token)


# Extraction d'un lot

def test_extract_rss_sources_requires_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de RssAdapter."
    ):
        module.extract_rss_sources(
            sources=[],
            adapter=object()  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "sources",
    [
        "sources",
        b"sources",
        {"name": "Flux"},
        iter([])
    ]
)
def test_extract_rss_sources_requires_sequence(
    sources: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="sources doit être une séquence"
    ):
        module.extract_rss_sources(
            sources=sources,
            adapter=build_adapter()
        )


def test_extract_rss_sources_handles_invalid_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_rss_error_message",
        lambda error: "Configuration invalide"
    )

    result = module.extract_rss_sources(
        sources=[None],  # type: ignore[list-item]
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_FAILED
    assert result.metadata["failed_sources"] == 1
    assert result.articles == []
    assert result.errors == ["Configuration invalide"]


def test_extract_rss_sources_aggregates_successful_sources(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    results = [
        build_result(
            name="Flux 1",
            articles=[{"title": "Article 1"}]
        ),
        build_result(
            name="Flux 2",
            articles=[{"title": "Article 2"}]
        )
    ]

    monkeypatch.setattr(
        module,
        "extract_rss_source",
        lambda source, adapter: results.pop(0)
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: None
    )

    result = module.extract_rss_sources(
        sources=[
            build_source(name="Flux 1"),
            build_source(name="Flux 2")
        ],
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_SUCCESS
    assert len(result.articles) == 2
    assert result.metadata == {
        "configured_sources": 2,
        "successful_sources": 2,
        "partial_sources": 0,
        "empty_sources": 0,
        "failed_sources": 0,
        "duplicate_count": 0
    }


def test_extract_rss_sources_returns_partial_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    results = [
        build_result(
            status=EXTRACTOR_STATUS_SUCCESS,
            articles=[{"title": "Article"}]
        ),
        build_result(
            status=EXTRACTOR_STATUS_FAILED,
            errors=["Erreur RSS"]
        )
    ]

    monkeypatch.setattr(
        module,
        "extract_rss_source",
        lambda source, adapter: results.pop(0)
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: None
    )

    result = module.extract_rss_sources(
        sources=[
            build_source(name="Flux valide"),
            build_source(name="Flux invalide")
        ],
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS
    assert len(result.articles) == 1
    assert result.metadata["successful_sources"] == 1
    assert result.metadata["failed_sources"] == 1


def test_extract_rss_sources_returns_empty_for_empty_sources(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "extract_rss_source",
        lambda source, adapter: build_result(
            status=EXTRACTOR_STATUS_EMPTY
        )
    )

    result = module.extract_rss_sources(
        sources=[build_source()],
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_EMPTY
    assert (
        result.message
        == "Aucune source RSS n'a produit d'article exploitable."
    )


def test_extract_rss_sources_converts_specialized_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_extraction(
        source: Mapping[str, Any],
        adapter: RssAdapter
    ) -> ExtractorResult:
        raise module.RssMaintenanceError(
            "Flux RSS",
            "Service indisponible",
            503
        )

    monkeypatch.setattr(
        module,
        "extract_rss_source",
        failing_extraction
    )

    result = module.extract_rss_sources(
        sources=[build_source()],
        adapter=build_adapter()
    )

    assert result.status == EXTRACTOR_STATUS_FAILED
    assert result.metadata["failed_sources"] == 1
    assert result.errors == ["Service indisponible"]


# API publique

def test_extract_articles_from_sources_requires_extractor() -> None:
    with pytest.raises(
        TypeError,
        match="extractor doit être une instance de RssExtractor."
    ):
        module.extract_articles_from_sources(
            sources=[],
            extractor=object()  # type: ignore[arg-type]
        )


def test_extract_articles_from_sources_returns_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_articles = [{"title": "Article RSS"}]
    extractor = module.RssExtractor(adapter=build_adapter())

    def fake_extract_sources(
        self: module.RssExtractor,
        sources: Any
    ) -> list[dict[str, Any]]:
        return expected_articles

    monkeypatch.setattr(
        module.RssExtractor,
        "extract_sources",
        fake_extract_sources
    )

    result = module.extract_articles_from_sources(
        sources=[build_source()],
        extractor=extractor
    )

    assert result == expected_articles


def test_load_rss_sources_requires_extractor() -> None:
    with pytest.raises(
        TypeError,
        match="extractor doit être une instance de RssExtractor."
    ):
        module.load_rss_sources(
            extractor=object()  # type: ignore[arg-type]
        )


def test_load_rss_sources_reloads_sources(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    expected_sources = [build_source()]
    extractor = module.RssExtractor(adapter=build_adapter())

    def fake_reload_sources(
        self: module.RssExtractor
    ) -> Any:
        calls.append("reload")
        return object()

    def fake_get_sources(
        self: module.RssExtractor
    ) -> list[dict[str, Any]]:
        return expected_sources

    monkeypatch.setattr(
        module.RssExtractor,
        "reload_sources",
        fake_reload_sources
    )
    monkeypatch.setattr(
        module.RssExtractor,
        "get_sources",
        fake_get_sources
    )

    result = module.load_rss_sources(extractor)

    assert result == expected_sources
    assert calls == ["reload"]


def test_extract_all_articles_requires_extractor() -> None:
    with pytest.raises(
        TypeError,
        match="extractor doit être une instance de RssExtractor."
    ):
        module.extract_all_articles(
            extractor=object()  # type: ignore[arg-type]
        )


def test_extract_all_articles_runs_extractor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = build_result(
        articles=[{"title": "Article RSS"}]
    )
    extractor = module.RssExtractor(adapter=build_adapter())

    def fake_run(
        self: module.RssExtractor
    ) -> ExtractorResult:
        return expected_result

    monkeypatch.setattr(
        module.RssExtractor,
        "run",
        fake_run
    )

    result = module.extract_all_articles(extractor)

    assert result is expected_result