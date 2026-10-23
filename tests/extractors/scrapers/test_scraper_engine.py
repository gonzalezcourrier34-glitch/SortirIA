"""Tests du moteur commun des extracteurs HTML CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_engine.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from config.constants import SOURCE_TYPE_SCRAPER
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.scrapers import scraper_engine as module
from src.extractors.scrapers.scraper_adapter import ScraperAdapter
from src.extractors.scrapers.scraper_http_utils import ScraperPageError

# Fabriques

def iter_items(
    source: Mapping[str, Any]
) -> Iterable[dict[str, Any]]:
    """Retourne deux éléments HTML minimaux."""

    return [
        {
            "title": "Article 1"
        },
        {
            "title": "Article 2"
        }
    ]


def build_article(
    item: Any,
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Construit un article HTML minimal."""

    return {
        "title": item.get("title", ""),
        "text": "Contenu suffisamment long pour être valide.",
        "url": f"https://example.com/{item.get('title', 'article')}",
        "source": source.get("name", "")
    }


def validate_item(
    item: Any,
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Valide un élément HTML."""

    return True, ""


def validate_scraped_article(
    article: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Valide un article HTML."""

    return True, ""


def build_adapter(
    **overrides: Any
) -> ScraperAdapter:
    """Construit un adaptateur HTML configurable."""

    values = {
        "source_id": "html_test",
        "default_name": "HTML Test",
        "iter_items": iter_items,
        "build_article": build_article,
        "validate_item": validate_item,
        "validate_article": validate_scraped_article
    }
    values.update(overrides)

    return ScraperAdapter(**values)


def build_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une source HTML minimale."""

    source = {
        "name": "Source HTML",
        "source_id": "html_test",
        "type": "scraper",
        "enabled": True,
        "max_articles": 10,
        "filters": {
            "remove_duplicates": True
        }
    }
    source.update(overrides)
    return source


def build_context(
    **overrides: Any
) -> module.ScraperExtractionContext:
    """Construit un contexte HTML configurable."""

    values = {
        "source": build_source(),
        "filters": {
            "remove_duplicates": True
        },
        "maximum_articles": 10,
        "remove_duplicates": True
    }
    values.update(overrides)

    return module.ScraperExtractionContext(**values)


def build_result(
    *,
    articles: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None
) -> ExtractorResult:
    """Construit un résultat d'extraction minimal."""

    return ExtractorResult(
        name="HTML Test",
        source_type=SOURCE_TYPE_SCRAPER,
        status="success" if articles else "empty",
        articles=articles or [],
        errors=errors or []
    )


# Contexte

def test_scraper_extraction_context_returns_source_name() -> None:
    context = build_context(
        source=build_source(name="  Source nettoyée  ")
    )

    assert context.source_name == "Source nettoyée"


def test_scraper_extraction_context_uses_default_source_name() -> None:
    context = build_context(
        source={}
    )

    assert context.source_name == "source HTML"


def test_scraper_extraction_context_returns_source_id() -> None:
    context = build_context(
        source=build_source(source_id="  afp_factuel  ")
    )

    assert context.source_id == "afp_factuel"


def test_scraper_extraction_context_uses_default_source_id() -> None:
    context = build_context(
        source={}
    )

    assert context.source_id == "scraper"


def test_scraper_extraction_context_returns_source_type() -> None:
    context = build_context()

    assert context.source_type == SOURCE_TYPE_SCRAPER


@pytest.mark.parametrize(
    ("article_count", "maximum_articles", "expected"),
    [
        (0, 2, False),
        (1, 2, False),
        (2, 2, True),
        (3, 2, True)
    ]
)
def test_scraper_extraction_context_completed(
    article_count: int,
    maximum_articles: int,
    expected: bool
) -> None:
    context = build_context(
        articles=[
            {
                "title": str(index)
            }
            for index in range(article_count)
        ],
        maximum_articles=maximum_articles
    )

    assert context.completed is expected


def test_scraper_extraction_context_counts_rejections() -> None:
    context = build_context()

    context.reject("article_invalide")
    context.reject("article_invalide")
    context.reject("")

    assert context.rejection_stats == {
        "article_invalide": 3
    }
    assert context.rejected_count == 3


def test_scraper_extraction_context_adds_non_empty_error() -> None:
    context = build_context()

    context.add_error(ValueError("Erreur HTML"))
    context.add_error("")

    assert context.errors == ["Erreur HTML"]


def test_scraper_extraction_context_adds_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    registered: list[tuple[dict[str, Any], set[str]]] = []

    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: registered.append(
            (article, seen_keys)
        )
    )

    context = build_context()
    article = {
        "title": "Article HTML"
    }

    context.add_article(article)

    assert context.articles == [article]
    assert registered == [
        (
            article,
            context.seen_keys
        )
    ]


def test_scraper_extraction_context_skips_registration_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "register_article",
        lambda article, seen_keys: pytest.fail(
            "register_article ne doit pas être appelé"
        )
    )

    context = build_context(
        remove_duplicates=False
    )
    article = {
        "title": "Article HTML"
    }

    context.add_article(article)

    assert context.articles == [article]


# Extracteur

def test_scraper_extractor_builds_expected_extractor() -> None:
    adapter = build_adapter()
    sources_file = Path("config/sources.yaml")

    extractor = module.ScraperExtractor(
        adapter=adapter,
        sources_file=sources_file
    )

    assert extractor.adapter is adapter
    assert extractor.sources_file == sources_file


@pytest.mark.parametrize(
    "adapter",
    [
        None,
        "invalid",
        object()
    ]
)
def test_scraper_extractor_rejects_invalid_adapter(
    adapter: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de ScraperAdapter"
    ):
        module.ScraperExtractor(
            adapter=adapter
        )


def test_scraper_extractor_rejects_missing_sources_file() -> None:
    with pytest.raises(
        ValueError,
        match="Le fichier de configuration des sources est requis"
    ):
        module.ScraperExtractor(
            adapter=build_adapter(),
            sources_file=None  # type: ignore[arg-type]
        )


def test_scraper_extractor_loads_and_caches_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str]] = []
    source = build_source()
    sources_file = Path("config/sources.yaml")

    def fake_load_validated_source(
        path: Path,
        source_id: str
    ) -> Mapping[str, Any]:
        calls.append((path, source_id))
        return source

    monkeypatch.setattr(
        module,
        "load_validated_source",
        fake_load_validated_source
    )

    extractor = module.ScraperExtractor(
        adapter=build_adapter(),
        sources_file=sources_file
    )

    first_result = extractor.load_source()
    second_result = extractor.load_source()

    assert first_result == source
    assert second_result == source
    assert first_result is not second_result
    assert calls == [
        (
            sources_file,
            "html_test"
        )
    ]


def test_scraper_extractor_force_reloads_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_load_validated_source(
        path: Path,
        source_id: str
    ) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return build_source(name=f"Source {calls}")

    monkeypatch.setattr(
        module,
        "load_validated_source",
        fake_load_validated_source
    )

    extractor = module.ScraperExtractor(
        adapter=build_adapter()
    )

    first_result = extractor.load_source()
    second_result = extractor.reload_source()

    assert first_result["name"] == "Source 1"
    assert second_result["name"] == "Source 2"
    assert calls == 2


def test_scraper_extractor_get_source_uses_loader(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_source = build_source()
    extractor = module.ScraperExtractor(
        adapter=build_adapter()
    )

    monkeypatch.setattr(
        module.ScraperExtractor,
        "load_source",
        lambda self, force_reload=False: expected_source
    )

    assert extractor.get_source() is expected_source
    assert extractor.source is expected_source


def test_scraper_extractor_extract_delegates_to_engine(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = build_result(
        articles=[
            {
                "title": "Article HTML"
            }
        ]
    )
    source = build_source()
    adapter = build_adapter()
    extractor = module.ScraperExtractor(
        adapter=adapter
    )
    received: dict[str, Any] = {}

    def fake_extract_scraper_source(
        current_source: Mapping[str, Any],
        current_adapter: ScraperAdapter
    ) -> ExtractorResult:
        received["source"] = current_source
        received["adapter"] = current_adapter
        return expected_result

    monkeypatch.setattr(
        module,
        "extract_scraper_source",
        fake_extract_scraper_source
    )

    result = extractor.extract(source)

    assert result is expected_result
    assert received == {
        "source": source,
        "adapter": adapter
    }


def test_scraper_extractor_run_uses_executor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = build_result(
        articles=[
            {
                "title": "Article HTML"
            }
        ]
    )
    adapter = build_adapter()
    extractor = module.ScraperExtractor(
        adapter=adapter
    )
    received: dict[str, Any] = {}

    def fake_execute_configured_extractor(
        **kwargs: Any
    ) -> ExtractorResult:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "execute_configured_extractor",
        fake_execute_configured_extractor
    )

    result = extractor.run()

    assert result is expected_result
    assert received["extractor_name"] == "HTML Test"
    assert received["source_type"] == SOURCE_TYPE_SCRAPER
    assert received["source_loader"].__self__ is extractor
    assert received["extraction_function"].__self__ is extractor


# Préparation

def test_create_context_builds_expected_context(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_source()
    filters = {
        "remove_duplicates": "false",
        "require_title": True
    }

    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda current_source: filters
    )

    context = module.create_context(
        source,
        maximum_articles=25
    )

    assert context.source == source
    assert context.source is not source
    assert context.filters == filters
    assert context.maximum_articles == 25
    assert context.remove_duplicates is False


# Validation

def test_validate_scraper_item_accepts_without_validator() -> None:
    adapter = build_adapter(
        validate_item=None
    )
    context = build_context()

    assert (
        module.validate_scraper_item(
            {
                "title": "Article"
            },
            context,
            adapter
        )
        == (True, "")
    )


def test_validate_scraper_item_delegates_to_adapter() -> None:
    received: dict[str, Any] = {}
    item = {
        "title": "Article"
    }
    context = build_context()

    def fake_validate_item(
        current_item: Any,
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        received["item"] = current_item
        received["source"] = source
        return False, "element_refuse"

    adapter = build_adapter(
        validate_item=fake_validate_item
    )

    result = module.validate_scraper_item(
        item,
        context,
        adapter
    )

    assert result == (False, "element_refuse")
    assert received == {
        "item": item,
        "source": context.source
    }


def test_validate_scraper_article_rejects_specific_validation(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: pytest.fail(
            "La validation commune ne doit pas être appelée"
        )
    )

    adapter = build_adapter(
        validate_article=lambda article, source: (
            False,
            "article_specifique_invalide"
        )
    )

    result = module.validate_scraper_article(
        {
            "title": "Article"
        },
        build_context(),
        adapter
    )

    assert result == (
        False,
        "article_specifique_invalide"
    )


def test_validate_scraper_article_delegates_to_common_validation(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "title": "Article"
    }
    context = build_context(
        filters={
            "require_title": True
        }
    )
    received: dict[str, Any] = {}

    def fake_validate_article(
        current_article: Mapping[str, Any],
        filters: Mapping[str, Any]
    ) -> tuple[bool, str]:
        received["article"] = current_article
        received["filters"] = filters
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article",
        fake_validate_article
    )

    result = module.validate_scraper_article(
        article,
        context,
        build_adapter()
    )

    assert result == (True, "")
    assert received == {
        "article": article,
        "filters": context.filters
    }


# Traitement

def test_process_scraper_item_rejects_invalid_item() -> None:
    context = build_context()
    adapter = build_adapter(
        validate_item=lambda item, source: (
            False,
            "element_refuse"
        )
    )

    module.process_scraper_item(
        {
            "title": "Article"
        },
        context,
        adapter
    )

    assert context.processed_count == 1
    assert context.articles == []
    assert context.rejection_stats == {
        "element_refuse": 1
    }


def test_process_scraper_item_converts_empty_item_reason() -> None:
    context = build_context()
    adapter = build_adapter(
        validate_item=lambda item, source: (
            False,
            ""
        )
    )

    module.process_scraper_item(
        {},
        context,
        adapter
    )

    assert context.rejection_stats == {
        "element_invalide": 1
    }


def test_process_scraper_item_handles_page_error() -> None:
    context = build_context()

    def failing_builder(
        item: Any,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        raise ScraperPageError(
            "page_indisponible",
            "Page HTML indisponible."
        )

    module.process_scraper_item(
        {},
        context,
        build_adapter(
            build_article=failing_builder
        )
    )

    assert context.processed_count == 1
    assert context.articles == []
    assert context.rejection_stats == {
        "page_indisponible": 1
    }

def test_process_scraper_item_handles_unexpected_error() -> None:
    context = build_context()

    def failing_builder(
        item: Any,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        raise ValueError("Erreur technique")

    module.process_scraper_item(
        {},
        context,
        build_adapter(
            build_article=failing_builder
        )
    )

    assert context.rejection_stats == {
        "construction_invalide": 1
    }


@pytest.mark.parametrize(
    "article",
    [
        None,
        {},
        [],
        "invalid"
    ]
)
def test_process_scraper_item_rejects_empty_article(
    article: Any
) -> None:
    context = build_context()

    module.process_scraper_item(
        {},
        context,
        build_adapter(
            build_article=lambda item, source: article
        )
    )

    assert context.rejection_stats == {
        "article_vide": 1
    }


def test_process_scraper_item_normalizes_article_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_scraper_article",
        lambda article, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: False
    )

    context = build_context(
        source=build_source(
            name="Source lisible",
            source_id="source_technique"
        )
    )
    article = {
        "title": "Article HTML",
        "source": "Éditeur original"
    }

    module.process_scraper_item(
        {},
        context,
        build_adapter(
            build_article=lambda item, source: article
        )
    )

    assert context.articles == [
        {
            "title": "Article HTML",
            "source": "source_technique",
            "publisher": "Éditeur original",
            "source_type": SOURCE_TYPE_SCRAPER,
            "source_name": "Source lisible"
        }
    ]


def test_process_scraper_item_rejects_invalid_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_scraper_article",
        lambda article, context, adapter: (
            False,
            "article_refuse"
        )
    )

    context = build_context()

    module.process_scraper_item(
        {},
        context,
        build_adapter()
    )

    assert context.articles == []
    assert context.rejection_stats == {
        "article_refuse": 1
    }


def test_process_scraper_item_rejects_duplicate(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_scraper_article",
        lambda article, context, adapter: (True, "")
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda article, seen_keys: True
    )

    context = build_context()

    module.process_scraper_item(
        {},
        context,
        build_adapter()
    )

    assert context.articles == []
    assert context.rejection_stats == {
        "doublon": 1
    }


def test_process_scraper_item_keeps_valid_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_scraper_article",
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

    context = build_context()

    module.process_scraper_item(
        {
            "title": "Article HTML"
        },
        context,
        build_adapter()
    )

    assert len(context.articles) == 1
    assert context.processed_count == 1


# Résultat

def test_build_scraper_result_builds_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = object()
    context = build_context(
        articles=[
            {
                "title": "Article HTML"
            }
        ],
        processed_count=2,
        requests_count=3
    )
    received: dict[str, Any] = {}

    def fake_build_success_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "build_success_result",
        fake_build_success_result
    )

    result = module.build_scraper_result(
        context,
        build_adapter()
    )

    assert result is expected_result
    assert received["articles"] == context.articles
    assert received["name"] == "HTML Test"
    assert received["source_type"] == SOURCE_TYPE_SCRAPER
    assert received["analyzed_count"] == 2
    assert received["requests_count"] == 3


def test_build_scraper_result_builds_partial(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = object()
    context = build_context(
        articles=[
            {
                "title": "Article HTML"
            }
        ],
        errors=["Erreur partielle"]
    )

    monkeypatch.setattr(
        module,
        "build_partial_result",
        lambda **kwargs: expected_result
    )

    assert (
        module.build_scraper_result(
            context,
            build_adapter()
        )
        is expected_result
    )


def test_build_scraper_result_builds_failed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = object()
    received: dict[str, Any] = {}
    context = build_context(
        errors=[
            "Première erreur",
            "Deuxième erreur"
        ]
    )

    def fake_build_failed_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "build_failed_result",
        fake_build_failed_result
    )

    result = module.build_scraper_result(
        context,
        build_adapter()
    )

    assert result is expected_result
    assert received["message"] == "Première erreur"


def test_build_scraper_result_builds_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = object()
    received: dict[str, Any] = {}

    def fake_build_empty_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "build_empty_result",
        fake_build_empty_result
    )

    result = module.build_scraper_result(
        build_context(),
        build_adapter()
    )

    assert result is expected_result
    assert received["message"] == "Aucun article HTML valide extrait."


# Extraction

@pytest.mark.parametrize(
    "source",
    [
        None,
        [],
        "invalid"
    ]
)
def test_extract_scraper_source_rejects_invalid_source(
    source: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="Configuration HTML invalide"
    ):
        module.extract_scraper_source(
            source,
            build_adapter()
        )


def test_extract_scraper_source_rejects_invalid_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de ScraperAdapter"
    ):
        module.extract_scraper_source(
            build_source(),
            object()  # type: ignore[arg-type]
        )


def test_extract_scraper_source_returns_empty_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = object()

    monkeypatch.setattr(
        module,
        "create_context",
        lambda source, maximum_articles: build_context(
            source=build_source(enabled=False)
        )
    )
    monkeypatch.setattr(
        module,
        "build_empty_result",
        lambda **kwargs: expected_result
    )

    result = module.extract_scraper_source(
        build_source(enabled=False),
        build_adapter()
    )

    assert result is expected_result


def test_extract_scraper_source_returns_empty_when_limit_is_zero(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = object()

    monkeypatch.setattr(
        module,
        "create_context",
        lambda source, maximum_articles: build_context(
            maximum_articles=0
        )
    )
    monkeypatch.setattr(
        module,
        "build_empty_result",
        lambda **kwargs: expected_result
    )

    result = module.extract_scraper_source(
        build_source(max_articles=0),
        build_adapter()
    )

    assert result is expected_result


def test_extract_scraper_source_processes_items_and_metrics(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context(
        maximum_articles=2
    )
    processed: list[Any] = []
    expected_result = build_result(
        articles=[
            {
                "title": "Article HTML"
            }
        ]
    )

    monkeypatch.setattr(
        module,
        "create_context",
        lambda source, maximum_articles: context
    )
    monkeypatch.setattr(
        module,
        "reset_scraper_requests_count",
        lambda: None
    )
    monkeypatch.setattr(
        module,
        "get_scraper_requests_count",
        lambda: 7
    )
    monkeypatch.setattr(
        module,
        "process_scraper_item",
        lambda item, current_context, adapter: processed.append(item)
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "build_scraper_result",
        lambda current_context, adapter: expected_result
    )

    result = module.extract_scraper_source(
        build_source(),
        build_adapter()
    )

    assert result is expected_result
    assert processed == [
        {
            "title": "Article 1"
        },
        {
            "title": "Article 2"
        }
    ]
    assert context.requests_count == 7


def test_extract_scraper_source_stops_when_limit_is_reached(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context(
        maximum_articles=1,
        articles=[
            {
                "title": "Déjà présent"
            }
        ]
    )
    processed: list[Any] = []

    monkeypatch.setattr(
        module,
        "create_context",
        lambda source, maximum_articles: context
    )
    monkeypatch.setattr(
        module,
        "reset_scraper_requests_count",
        lambda: None
    )
    monkeypatch.setattr(
        module,
        "get_scraper_requests_count",
        lambda: 0
    )
    monkeypatch.setattr(
        module,
        "process_scraper_item",
        lambda item, current_context, adapter: processed.append(item)
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "build_scraper_result",
        lambda current_context, adapter: build_result(
            articles=current_context.articles
        )
    )

    module.extract_scraper_source(
        build_source(),
        build_adapter()
    )

    assert processed == []


def test_extract_scraper_source_handles_page_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context()
    expected_result = object()

    def failing_iterator(
        source: Mapping[str, Any]
    ) -> Iterable[Any]:
        raise ScraperPageError(
            "page_indisponible",
            "Page HTML indisponible."
        )

    monkeypatch.setattr(
        module,
        "create_context",
        lambda source, maximum_articles: context
    )
    monkeypatch.setattr(
        module,
        "reset_scraper_requests_count",
        lambda: None
    )
    monkeypatch.setattr(
        module,
        "get_scraper_requests_count",
        lambda: 4
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "build_scraper_result",
        lambda current_context, adapter: expected_result
    )

    result = module.extract_scraper_source(
        build_source(),
        build_adapter(
            iter_items=failing_iterator
        )
    )

    assert result is expected_result
    assert context.errors
    assert context.requests_count == 4


def test_extract_scraper_source_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context()
    expected_result = object()

    def failing_iterator(
        source: Mapping[str, Any]
    ) -> Iterable[Any]:
        raise ValueError("Erreur inattendue")

    monkeypatch.setattr(
        module,
        "create_context",
        lambda source, maximum_articles: context
    )
    monkeypatch.setattr(
        module,
        "reset_scraper_requests_count",
        lambda: None
    )
    monkeypatch.setattr(
        module,
        "get_scraper_requests_count",
        lambda: 2
    )
    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module,
        "build_scraper_result",
        lambda current_context, adapter: expected_result
    )

    result = module.extract_scraper_source(
        build_source(),
        build_adapter(
            iter_items=failing_iterator
        )
    )

    assert result is expected_result
    assert context.errors == ["Erreur inattendue"]
    assert context.requests_count == 2


# API publique

@pytest.mark.parametrize(
    "extractor",
    [
        None,
        object(),
        "invalid"
    ]
)
def test_extract_all_articles_rejects_invalid_extractor(
    extractor: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="extractor doit être une instance de ScraperExtractor"
    ):
        module.extract_all_articles(extractor)


def test_extract_all_articles_runs_extractor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_result = build_result(
        articles=[
            {
                "title": "Article HTML"
            }
        ]
    )
    extractor = module.ScraperExtractor(
        adapter=build_adapter()
    )

    def fake_run(
        self: Any
    ) -> ExtractorResult:
        return expected_result

    monkeypatch.setattr(
        module.ScraperExtractor,
        "run",
        fake_run
    )

    result = module.extract_all_articles(extractor)

    assert result is expected_result


@pytest.mark.parametrize(
    "source",
    [
        None,
        [],
        "invalid"
    ]
)
def test_extract_articles_from_source_rejects_invalid_source(
    source: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="source doit être une structure de type Mapping"
    ):
        module.extract_articles_from_source(source)


def test_extract_articles_from_source_builds_standard_adapter(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_source(
        source_id="afp_factuel",
        name="AFP Factuel"
    )
    expected_articles = [
        {
            "title": "Article HTML"
        }
    ]
    adapter = build_adapter(
        source_id="afp_factuel",
        default_name="AFP Factuel"
    )
    received: dict[str, Any] = {}

    from src.extractors.scrapers import scraper_extractor

    def fake_create_standard_scraper_adapter(
        source_id: str,
        default_name: str
    ) -> ScraperAdapter:
        received["source_id"] = source_id
        received["default_name"] = default_name
        return adapter

    monkeypatch.setattr(
        scraper_extractor,
        "create_standard_scraper_adapter",
        fake_create_standard_scraper_adapter
    )
    monkeypatch.setattr(
        module,
        "extract_scraper_source",
        lambda current_source, current_adapter: build_result(
            articles=expected_articles
        )
    )

    result = module.extract_articles_from_source(source)

    assert result == expected_articles
    assert received == {
        "source_id": "afp_factuel",
        "default_name": "AFP Factuel"
    }


def test_extract_articles_from_source_uses_defaults(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    from src.extractors.scrapers import scraper_extractor

    def fake_create_standard_scraper_adapter(
        source_id: str,
        default_name: str
    ) -> ScraperAdapter:
        received["source_id"] = source_id
        received["default_name"] = default_name
        return build_adapter(
            source_id=source_id,
            default_name=default_name
        )

    monkeypatch.setattr(
        scraper_extractor,
        "create_standard_scraper_adapter",
        fake_create_standard_scraper_adapter
    )
    monkeypatch.setattr(
        module,
        "extract_scraper_source",
        lambda source, adapter: build_result()
    )

    result = module.extract_articles_from_source({})

    assert result == []
    assert received == {
        "source_id": "scraper",
        "default_name": "Scraper HTML"
    }