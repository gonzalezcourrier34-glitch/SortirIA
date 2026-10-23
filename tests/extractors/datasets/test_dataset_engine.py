"""Tests du moteur commun des extracteurs de datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/datasets/test_dataset_engine.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import src.extractors.datasets.dataset_engine as module
from config.constants import EXTRACTOR_STATUS_DISABLED
from src.extractors.datasets.dataset_adapter import DatasetAdapter

# Fabriques

def make_adapter(
    source_id: str = "fakeddit",
    default_name: str = "Fakeddit",
    supported_extensions: frozenset[str] | None = None
) -> DatasetAdapter:
    """Construit un double compatible avec DatasetAdapter."""

    adapter = Mock(spec=DatasetAdapter)
    adapter.source_id = source_id
    adapter.default_name = default_name
    adapter.supported_extensions = (
        supported_extensions
        or frozenset({".csv", ".json"})
    )
    adapter.find_files = Mock(return_value=[])
    adapter.iter_items = Mock(return_value=iter(()))
    return adapter


def make_source(
    **overrides: Any
) -> dict[str, Any]:
    """Construit une configuration dataset minimale."""

    source = {
        "source_id": "fakeddit",
        "name": "Fakeddit",
        "type": "dataset",
        "enabled": True,
        "path": "data/fakeddit",
        "max_articles": 10
    }
    source.update(overrides)
    return source


def make_article(
    **overrides: Any
) -> dict[str, Any]:
    """Construit un article minimal pour les tests."""

    article = {
        "id": "article-1",
        "source": "Fakeddit",
        "title": "Titre valide",
        "text": "Texte suffisamment long pour être valide."
    }
    article.update(overrides)
    return article


# DatasetExtractor

def test_dataset_extractor_normalizes_identity(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter(
        source_id="fakeddit",
        default_name="Fakeddit"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip().lower()
        if value is not None
        else ""
    )

    extractor = module.DatasetExtractor(
        source_id="  FAKEDDIT  ",
        default_name="  FAKEDDIT  ",
        adapter=adapter,
        section_name="  DATASETS  ",
        sources_file=Path("sources.yaml")
    )

    assert extractor.source_id == "fakeddit"
    assert extractor.default_name == "fakeddit"
    assert extractor.section_name == "datasets"


def test_dataset_extractor_uses_source_id_as_default_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )

    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )

    assert extractor.default_name == "fakeddit"


def test_dataset_extractor_uses_default_section_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )

    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        section_name="",
        sources_file=Path("sources.yaml")
    )

    assert extractor.section_name == "datasets"


def test_dataset_extractor_rejects_empty_source_id(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
        if value is None
        else str(value).strip()
    )

    with pytest.raises(
        ValueError,
        match="L'identifiant d'un DatasetExtractor ne peut pas être vide"
    ):
        module.DatasetExtractor(
            source_id=" ",
            default_name="Dataset",
            adapter=adapter,
            sources_file=Path("sources.yaml")
        )


def test_dataset_extractor_rejects_invalid_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de DatasetAdapter"
    ):
        module.DatasetExtractor(
            source_id="fakeddit",
            default_name="Fakeddit",
            adapter=object(),
            sources_file=Path("sources.yaml")
        )


def test_dataset_extractor_rejects_missing_sources_file() -> None:
    adapter = make_adapter()

    with pytest.raises(
        ValueError,
        match="Le fichier de configuration des sources est requis"
    ):
        module.DatasetExtractor(
            source_id="fakeddit",
            default_name="Fakeddit",
            adapter=adapter,
            sources_file=None
        )


def test_dataset_extractor_rejects_adapter_with_different_source_id() -> None:
    adapter = make_adapter(source_id="isot")

    with pytest.raises(
        ValueError,
        match="L'extracteur fakeddit utilise l'adaptateur isot"
    ):
        module.DatasetExtractor(
            source_id="fakeddit",
            default_name="Fakeddit",
            adapter=adapter,
            sources_file=Path("sources.yaml")
        )


def test_load_source_loads_and_caches_configuration(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    loaded_source = {
        "source_id": "fakeddit",
        "enabled": True
    }
    calls: list[tuple[Path, str]] = []

    def fake_load_validated_source(
        sources_file: Path,
        source_id: str
    ) -> dict[str, Any]:
        calls.append((sources_file, source_id))
        return loaded_source

    monkeypatch.setattr(
        module,
        "load_validated_source",
        fake_load_validated_source
    )

    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )

    first_result = extractor.load_source()
    second_result = extractor.load_source()

    assert first_result == loaded_source
    assert second_result == loaded_source
    assert calls == [
        (
            Path("sources.yaml"),
            "fakeddit"
        )
    ]


def test_load_source_returns_defensive_copy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()

    monkeypatch.setattr(
        module,
        "load_validated_source",
        lambda *args: {
            "enabled": True
        }
    )

    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )

    first_result = extractor.load_source()
    first_result["enabled"] = False

    second_result = extractor.load_source()

    assert second_result == {
        "enabled": True
    }


def test_load_source_force_reload_reloads_configuration(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    configurations = iter([
        {
            "version": 1
        },
        {
            "version": 2
        }
    ])

    monkeypatch.setattr(
        module,
        "load_validated_source",
        lambda *args: next(configurations)
    )

    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )

    first_result = extractor.load_source()
    second_result = extractor.load_source(force_reload=True)

    assert first_result == {
        "version": 1
    }
    assert second_result == {
        "version": 2
    }


def test_reload_source_forces_reload(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )
    calls: list[tuple[module.DatasetExtractor, bool]] = []

    def fake_load_source(
        self: module.DatasetExtractor,
        force_reload: bool = False
    ) -> dict[str, Any]:
        calls.append((self, force_reload))
        return {
            "enabled": True
        }

    monkeypatch.setattr(
        module.DatasetExtractor,
        "load_source",
        fake_load_source
    )

    result = extractor.reload_source()

    assert result == {
        "enabled": True
    }
    assert calls == [
        (
            extractor,
            True
        )
    ]


def test_get_source_uses_cached_loader(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )
    calls: list[module.DatasetExtractor] = []

    def fake_load_source(
        self: module.DatasetExtractor,
        force_reload: bool = False
    ) -> dict[str, Any]:
        calls.append(self)
        return {
            "enabled": True
        }

    monkeypatch.setattr(
        module.DatasetExtractor,
        "load_source",
        fake_load_source
    )

    result = extractor.get_source()

    assert result == {
        "enabled": True
    }
    assert calls == [
        extractor
    ]


def test_source_property_uses_loader(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )
    calls: list[module.DatasetExtractor] = []

    def fake_load_source(
        self: module.DatasetExtractor,
        force_reload: bool = False
    ) -> dict[str, Any]:
        calls.append(self)
        return {
            "enabled": True
        }

    monkeypatch.setattr(
        module.DatasetExtractor,
        "load_source",
        fake_load_source
    )

    result = extractor.source

    assert result == {
        "enabled": True
    }
    assert calls == [
        extractor
    ]


def test_process_dataset_item_adds_valid_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    article = make_article(
        source="External Publisher"
    )
    item = {
        "title": "Titre"
    }

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: article
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda *args: False
    )

    registered: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "register_article",
        lambda value, seen_keys: registered.append(value)
    )

    module.process_dataset_item(
        adapter=adapter,
        item=item,
        item_index=3,
        item_identifier="item-3",
        source=make_source(),
        source_name="fakeddit",
        filters={
            "remove_duplicates": True
        },
        remove_duplicates=True,
        context=context
    )

    assert context.processed_count == 1
    assert context.articles == [
        article
    ]
    assert article["source"] == "fakeddit"
    assert article["source_type"] == "dataset"
    assert article["source_name"] == "fakeddit"
    assert article["publisher"] == "External Publisher"
    assert registered == [
        article
    ]
    
def test_extract_delegates_to_extract_dataset_from_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )
    source = make_source()
    expected_result = object()
    received: dict[str, Any] = {}

    def fake_extract_dataset_from_source(
        source: Mapping[str, Any],
        adapter: DatasetAdapter
    ) -> Any:
        received["source"] = source
        received["adapter"] = adapter
        return expected_result

    monkeypatch.setattr(
        module,
        "extract_dataset_from_source",
        fake_extract_dataset_from_source
    )

    result = extractor.extract(source)

    assert result is expected_result
    assert received == {
        "source": source,
        "adapter": adapter
    }


def test_run_uses_configured_extractor(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    extractor = module.DatasetExtractor(
        source_id="fakeddit",
        default_name="Fakeddit",
        adapter=adapter,
        sources_file=Path("sources.yaml")
    )
    expected_result = object()
    received: dict[str, Any] = {}

    def fake_execute_configured_extractor(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "execute_configured_extractor",
        fake_execute_configured_extractor
    )

    result = extractor.run()

    assert result is expected_result
    assert received["extractor_name"] == "Fakeddit"
    assert received["source_type"] == module.SOURCE_TYPE_DATASET
    assert received["source_loader"] == extractor.reload_source
    assert received["extraction_function"] == extractor.extract


# DatasetExtractionContext

def test_dataset_extraction_context_has_empty_defaults() -> None:
    context = module.DatasetExtractionContext()

    assert context.articles == []
    assert context.seen_keys == set()
    assert context.rejection_stats == Counter()
    assert context.processed_count == 0


def test_dataset_extraction_context_instances_are_independent() -> None:
    first_context = module.DatasetExtractionContext()
    second_context = module.DatasetExtractionContext()

    first_context.articles.append({
        "id": "article-1"
    })
    first_context.seen_keys.add("key-1")
    first_context.rejection_stats["invalid"] += 1
    first_context.processed_count = 1

    assert second_context.articles == []
    assert second_context.seen_keys == set()
    assert second_context.rejection_stats == Counter()
    assert second_context.processed_count == 0


def test_context_reject_normalizes_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip().lower()
    )

    context.reject(
        " Invalid Article ",
        "fallback"
    )

    assert context.rejection_stats == Counter({
        "invalid article": 1
    })


def test_context_reject_uses_default_for_empty_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
    )

    context.reject(
        "",
        "invalid_article"
    )

    assert context.rejection_stats == Counter({
        "invalid_article": 1
    })


def test_context_reject_accumulates_reasons(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    context.reject(
        "duplicate",
        "fallback"
    )
    context.reject(
        "duplicate",
        "fallback"
    )

    assert context.rejection_stats == Counter({
        "duplicate": 2
    })


# Paramètres

def test_get_dataset_max_articles_uses_configured_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def fake_parse_non_negative_integer(
        value: Any,
        default: int
    ) -> int:
        received["value"] = value
        received["default"] = default
        return 25

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        fake_parse_non_negative_integer
    )

    result = module.get_dataset_max_articles({
        "max_articles": "25"
    })

    assert result == 25
    assert received == {
        "value": "25",
        "default": module.MAX_ARTICLES_PER_SOURCE
    }


def test_get_dataset_max_articles_uses_global_default(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def fake_parse_non_negative_integer(
        value: Any,
        default: int
    ) -> int:
        received["value"] = value
        received["default"] = default
        return default

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        fake_parse_non_negative_integer
    )

    result = module.get_dataset_max_articles({})

    assert result == module.MAX_ARTICLES_PER_SOURCE
    assert received["value"] == module.MAX_ARTICLES_PER_SOURCE


@pytest.mark.parametrize(
    ("source", "configured_value"),
    [
        (
            {
                "chunksize": 500
            },
            500
        ),
        (
            {
                "chunk_size": 250
            },
            250
        ),
        (
            {},
            100
        )
    ]
)
def test_get_dataset_chunk_size_uses_expected_value(
    source: Mapping[str, Any],
    configured_value: int,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[tuple[Any, int]] = []

    def fake_parse_non_negative_integer(
        value: Any,
        default: int
    ) -> int:
        received.append((value, default))
        return int(value)

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        fake_parse_non_negative_integer
    )

    result = module.get_dataset_chunk_size(
        source,
        default=100,
        source_name="Fakeddit"
    )

    assert result == configured_value
    assert received[0] == (
        configured_value,
        100
    )


def test_get_dataset_chunk_size_prefers_chunksize() -> None:
    result = module.get_dataset_chunk_size(
        {
            "chunksize": 500,
            "chunk_size": 250
        },
        default=100,
        source_name="Fakeddit"
    )

    assert result == 500


def test_get_dataset_chunk_size_uses_positive_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed_values = iter([
        0,
        25
    ])
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: next(parsed_values)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.get_dataset_chunk_size(
        {
            "chunksize": 0
        },
        default=25,
        source_name="Fakeddit"
    )

    assert result == 25
    assert warnings == [
        (
            "chunksize invalide pour %s. La valeur %s sera utilisée.",
            "Fakeddit",
            25
        )
    ]


def test_get_dataset_chunk_size_forces_fallback_to_one(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: 0
    )

    result = module.get_dataset_chunk_size(
        {
            "chunksize": 0
        },
        default=0,
        source_name="Dataset"
    )

    assert result == 1


def test_get_dataset_filters_forces_dataset_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "role": "custom",
        "require_url": True
    }
    received: dict[str, Any] = {}

    def fake_get_filter_configuration(
        configuration: Mapping[str, Any]
    ) -> dict[str, Any]:
        received.update(configuration)
        return {
            "remove_duplicates": True
        }

    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        fake_get_filter_configuration
    )

    result = module.get_dataset_filters(source)

    assert result == {
        "remove_duplicates": True
    }
    assert received["role"] == "dataset"
    assert source["role"] == "custom"


def test_get_dataset_filters_returns_copy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    filters = {
        "remove_duplicates": True
    }

    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: filters
    )

    result = module.get_dataset_filters({})

    assert result == filters
    assert result is not filters


@pytest.mark.parametrize(
    "invalid_filters",
    [
        None,
        [],
        (),
        "filters",
        42
    ]
)
def test_get_dataset_filters_returns_empty_dict_for_non_mapping(
    invalid_filters: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_filter_configuration",
        lambda source: invalid_filters
    )

    assert module.get_dataset_filters({}) == {}


# process_dataset_item

def test_process_dataset_item_uses_generated_identifier(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
        if value is None
        else str(value).strip()
    )

    def fake_build_article_from_item(
        adapter: DatasetAdapter,
        item: Any,
        item_index: int,
        identifier: str,
        source: Mapping[str, Any],
        source_name: str
    ) -> dict[str, Any]:
        received["identifier"] = identifier
        return make_article(source="fakeddit")

    monkeypatch.setattr(
        module,
        "build_article_from_item",
        fake_build_article_from_item
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=7,
        item_identifier=None,
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert received["identifier"] == "fakeddit:7"


def test_process_dataset_item_rejects_failed_construction(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    # SortirIA : la validation propre au dataset passe désormais en premier.
    monkeypatch.setattr(module, "validate_adapter_item", lambda *args: (True, ""))

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: None
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier=None,
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=True,
        context=context
    )

    assert context.processed_count == 1
    assert context.articles == []
    assert context.rejection_stats == Counter({
        "invalid_construction": 1
    })


def test_process_dataset_item_preserves_existing_publisher(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    article = make_article(
        source="Technical Dataset",
        publisher="Original Publisher"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: article
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert article["publisher"] == "Original Publisher"
    assert article["source"] == "fakeddit"


def test_process_dataset_item_does_not_create_publisher_for_same_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    article = make_article(
        source="FAKEDDIT"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: article
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert "publisher" not in article


def test_process_dataset_item_uses_dataset_as_default_source_type(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    article = make_article(source="fakeddit")

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: article
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )

    source = make_source()
    source.pop("type")

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=source,
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert article["source_type"] == "dataset"


def test_process_dataset_item_preserves_existing_source_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    article = make_article(
        source="fakeddit",
        source_name="Existing Name"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: article
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert article["source_name"] == "Existing Name"


def test_process_dataset_item_rejects_invalid_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: make_article()
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            False,
            "missing_title"
        )
    )

    adapter_validation_called = False

    def fake_validate_adapter_item(
        *args: Any
    ) -> tuple[bool, str]:
        nonlocal adapter_validation_called
        adapter_validation_called = True
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        fake_validate_adapter_item
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=True,
        context=context
    )

    assert context.articles == []
    assert context.rejection_stats == Counter({
        "missing_title": 1
    })
    # SortirIA : la validation propre au dataset est appelée avant la
    # construction de l'article (elle porte sur l'élément brut).
    assert adapter_validation_called is True


def test_process_dataset_item_uses_default_invalid_article_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()
    # SortirIA : la validation propre au dataset passe désormais en premier.
    monkeypatch.setattr(module, "validate_adapter_item", lambda *args: (True, ""))

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: make_article()
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            False,
            ""
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert context.rejection_stats == Counter({
        "invalid_article": 1
    })


def test_process_dataset_item_rejects_invalid_adapter_item(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: make_article()
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            False,
            "missing_label"
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert context.articles == []
    assert context.rejection_stats == Counter({
        "missing_label": 1
    })


def test_process_dataset_item_uses_default_adapter_rejection_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: make_article()
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            False,
            ""
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert context.rejection_stats == Counter({
        "rejected_item": 1
    })


def test_process_dataset_item_rejects_duplicate(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: make_article()
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda *args: True
    )

    registered = False

    def fake_register_article(
        *args: Any
    ) -> None:
        nonlocal registered
        registered = True

    monkeypatch.setattr(
        module,
        "register_article",
        fake_register_article
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=True,
        context=context
    )

    assert context.articles == []
    assert context.rejection_stats == Counter({
        "duplicate": 1
    })
    assert registered is False


def test_process_dataset_item_skips_duplicate_checks_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    context = module.DatasetExtractionContext()

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value or "")
    )
    monkeypatch.setattr(
        module,
        "build_article_from_item",
        lambda *args: make_article()
    )
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "validate_adapter_item",
        lambda *args: (
            True,
            ""
        )
    )
    monkeypatch.setattr(
        module,
        "is_duplicate_article",
        lambda *args: pytest.fail(
            "La déduplication ne doit pas être appelée."
        )
    )
    monkeypatch.setattr(
        module,
        "register_article",
        lambda *args: pytest.fail(
            "L'enregistrement ne doit pas être appelé."
        )
    )

    module.process_dataset_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="id",
        source=make_source(),
        source_name="fakeddit",
        filters={},
        remove_duplicates=False,
        context=context
    )

    assert len(context.articles) == 1


# log_dataset_summary

def test_log_dataset_summary_forwards_counts(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = module.DatasetExtractionContext(
        articles=[
            {
                "id": "1"
            },
            {
                "id": "2"
            }
        ],
        rejection_stats=Counter({
            "duplicate": 1
        }),
        processed_count=3
    )
    received: dict[str, Any] = {}

    def fake_log_extraction_summary(
        **kwargs: Any
    ) -> None:
        received.update(kwargs)

    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        fake_log_extraction_summary
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: None
    )

    module.log_dataset_summary(
        "Fakeddit",
        10,
        context
    )

    assert received == {
        "source_name": "Fakeddit",
        "extracted_count": 2,
        "processed_count": 3,
        "rejection_stats": Counter({
            "duplicate": 1
        })
    }


def test_log_dataset_summary_warns_when_target_not_reached(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = module.DatasetExtractionContext(
        articles=[
            {
                "id": "1"
            }
        ]
    )
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    module.log_dataset_summary(
        "Fakeddit",
        10,
        context
    )

    assert warnings == [
        (
            "%s : %s article(s) valide(s) trouvé(s) sur %s demandés.",
            "Fakeddit",
            1,
            10
        )
    ]


def test_log_dataset_summary_does_not_warn_when_target_reached(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    context = module.DatasetExtractionContext(
        articles=[
            {
                "id": "1"
            },
            {
                "id": "2"
            }
        ]
    )
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "log_extraction_summary",
        lambda **kwargs: None
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    module.log_dataset_summary(
        "Fakeddit",
        2,
        context
    )

    assert warnings == []


# extract_dataset_from_source

def test_extract_dataset_from_source_rejects_invalid_adapter() -> None:
    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de DatasetAdapter"
    ):
        module.extract_dataset_from_source(
            make_source(),
            object()
        )


@pytest.mark.parametrize(
    "invalid_source",
    [
        None,
        [],
        (),
        "source",
        42
    ]
)
def test_extract_dataset_from_source_rejects_non_mapping_source(
    invalid_source: Any
) -> None:
    adapter = make_adapter()

    with pytest.raises(
        TypeError,
        match="Configuration invalide pour Fakeddit"
    ):
        module.extract_dataset_from_source(
            invalid_source,
            adapter
        )


def test_extract_dataset_from_source_returns_disabled_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    source = make_source(
        enabled=False
    )
    received: dict[str, Any] = {}
    expected_result = object()

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: False
    )

    def fake_build_disabled_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "build_disabled_result",
        fake_build_disabled_result
    )

    result = module.extract_dataset_from_source(
        source,
        adapter
    )

    assert result is expected_result
    assert received == {
        "name": "Fakeddit",
        "source_type": module.SOURCE_TYPE_DATASET,
        "message": "Dataset désactivé.",
        "metadata": {
            "source_id": "fakeddit",
            "adapter_id": "fakeddit",
            "requests_count": 0
        }
    }


def test_extract_dataset_from_source_treats_missing_enabled_as_active() -> None:
    adapter = make_adapter()
    source = make_source(
        max_articles=0
    )
    del source["enabled"]

    result = module.extract_dataset_from_source(
        source,
        adapter
    )

    # Active par défaut, comme dans l'exécuteur : la source va jusqu'au plafond.
    assert result.status != EXTRACTOR_STATUS_DISABLED
    assert result.message == "Aucun article demandé."


def test_extract_dataset_from_source_returns_empty_when_maximum_is_zero(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    source = make_source(
        max_articles=0
    )
    received: dict[str, Any] = {}
    expected_result = object()

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 0
    )
    monkeypatch.setattr(
        module,
        "perf_counter",
        Mock(side_effect=[
            10.0,
            10.5
        ])
    )

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

    result = module.extract_dataset_from_source(
        source,
        adapter
    )

    assert result is expected_result
    assert received["name"] == "Fakeddit"
    assert received["message"] == "Aucun article demandé."
    assert received["duration_seconds"] == pytest.approx(0.5)
    assert received["metadata"]["max_articles"] == 0


def test_extract_dataset_from_source_warns_on_source_adapter_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter(source_id="fakeddit")
    source = make_source(
        source_id="other",
        max_articles=0
    )
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )
    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 0
    )
    monkeypatch.setattr(
        module,
        "build_empty_result",
        lambda **kwargs: kwargs
    )

    module.extract_dataset_from_source(
        source,
        adapter
    )

    assert warnings[0] == (
        "L'adaptateur %s est utilisé avec la source %s.",
        "fakeddit",
        "other"
    )


def test_extract_dataset_from_source_uses_adapter_identity_fallbacks(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter(
        source_id="fakeddit",
        default_name="Fakeddit"
    )
    source = {
        "enabled": False
    }

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: False
    )
    monkeypatch.setattr(
        module,
        "build_disabled_result",
        lambda **kwargs: kwargs
    )

    result = module.extract_dataset_from_source(
        source,
        adapter
    )

    assert result["name"] == "Fakeddit"
    assert result["metadata"]["source_id"] == "fakeddit"


def test_extract_dataset_from_source_raises_when_iterator_is_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    adapter.iter_items.return_value = None
    dataset_file = tmp_path / "dataset.csv"

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 10
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda source, name: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            dataset_file
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )

    with pytest.raises(
        RuntimeError,
        match="L'adaptateur fakeddit n'a retourné aucun itérateur"
    ):
        module.extract_dataset_from_source(
            make_source(),
            adapter
        )


def test_extract_dataset_from_source_returns_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    dataset_file = tmp_path / "dataset.csv"
    source = make_source(
        max_articles=2
    )
    received: dict[str, Any] = {}
    expected_result = object()

    adapter.iter_items.return_value = iter([
        (
            "id-1",
            0,
            {
                "title": "Article 1"
            }
        ),
        (
            "id-2",
            1,
            {
                "title": "Article 2"
            }
        )
    ])

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: bool(value)
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 2
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda source, name: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            dataset_file
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {
            "remove_duplicates": True
        }
    )
    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: int(value)
    )

    def fake_process_dataset_item(
        adapter: DatasetAdapter,
        item: Any,
        item_index: int,
        item_identifier: Any,
        source: Mapping[str, Any],
        source_name: str,
        filters: Mapping[str, Any],
        remove_duplicates: bool,
        context: module.DatasetExtractionContext
    ) -> None:
        context.processed_count += 1
        context.articles.append({
            "id": item_identifier
        })

    monkeypatch.setattr(
        module,
        "process_dataset_item",
        fake_process_dataset_item
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module,
        "perf_counter",
        Mock(side_effect=[
            10.0,
            10.5
        ])
    )

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

    result = module.extract_dataset_from_source(
        source,
        adapter
    )

    assert result is expected_result
    assert received["name"] == "Fakeddit"
    assert received["articles"] == [
        {
            "id": "id-1"
        },
        {
            "id": "id-2"
        }
    ]
    assert received["analyzed_count"] == 2
    assert received["rejected_count"] == 0
    assert received["message"] == "2 article(s) extrait(s)."
    assert received["metadata"] == {
        "source_id": "fakeddit",
        "adapter_id": "fakeddit",
        "requests_count": 0,
        "max_articles": 2,
        "dataset_directory": str(tmp_path),
        "dataset_files": [
            str(dataset_file)
        ],
        "files_count": 1
    }


def test_extract_dataset_from_source_stops_at_maximum_articles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    processed_identifiers: list[str] = []

    adapter.iter_items.return_value = iter([
        (
            "id-1",
            0,
            {}
        ),
        (
            "id-2",
            1,
            {}
        ),
        (
            "id-3",
            2,
            {}
        )
    ])

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 2
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )
    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: int(value)
    )

    def fake_process_dataset_item(
        adapter: DatasetAdapter,
        item: Any,
        item_index: int,
        item_identifier: str,
        source: Mapping[str, Any],
        source_name: str,
        filters: Mapping[str, Any],
        remove_duplicates: bool,
        context: module.DatasetExtractionContext
    ) -> None:
        processed_identifiers.append(item_identifier)
        context.processed_count += 1
        context.articles.append({
            "id": item_identifier
        })

    monkeypatch.setattr(
        module,
        "process_dataset_item",
        fake_process_dataset_item
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module,
        "build_success_result",
        lambda **kwargs: kwargs
    )

    module.extract_dataset_from_source(
        make_source(),
        adapter
    )

    assert processed_identifiers == [
        "id-1",
        "id-2"
    ]


def test_extract_dataset_from_source_rejects_invalid_iteration_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    adapter.iter_items.return_value = iter([
        None,
        (
            "only",
            "two"
        ),
        [
            "id",
            0,
            {}
        ]
    ])

    warnings: list[tuple[Any, ...]] = []
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 10
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    def fake_build_empty_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        module,
        "build_empty_result",
        fake_build_empty_result
    )

    module.extract_dataset_from_source(
        make_source(),
        adapter
    )

    assert received["analyzed_count"] == 3
    assert received["rejected_count"] == 3
    assert received["rejection_reasons"] == {
        "invalid_iteration_item": 3
    }
    assert len(warnings) >= 3


def test_extract_dataset_from_source_normalizes_item_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    adapter.iter_items.return_value = iter([
        (
            "id-1",
            "invalid",
            {}
        )
    ])
    received_indexes: list[int] = []

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 1
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )

    def fake_parse_non_negative_integer(
        value: Any,
        default: int
    ) -> int:
        assert value == "invalid"
        assert default == 0
        return 0

    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        fake_parse_non_negative_integer
    )

    def fake_process_dataset_item(
        adapter: DatasetAdapter,
        item: Any,
        item_index: int,
        item_identifier: Any,
        source: Mapping[str, Any],
        source_name: str,
        filters: Mapping[str, Any],
        remove_duplicates: bool,
        context: module.DatasetExtractionContext
    ) -> None:
        received_indexes.append(item_index)
        context.processed_count += 1
        context.articles.append({
            "id": item_identifier
        })

    monkeypatch.setattr(
        module,
        "process_dataset_item",
        fake_process_dataset_item
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module,
        "build_success_result",
        lambda **kwargs: kwargs
    )

    module.extract_dataset_from_source(
        make_source(),
        adapter
    )

    assert received_indexes == [
        0
    ]


@pytest.mark.parametrize(
    "raised_error",
    [
        TypeError("type"),
        ValueError("value"),
        AttributeError("attribute"),
        KeyError("key"),
        OSError("system")
    ]
)
def test_extract_dataset_from_source_returns_partial_on_reading_error(
    raised_error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()

    def failing_iterator() -> Iterator[tuple[str, int, dict[str, Any]]]:
        yield (
            "id-1",
            0,
            {}
        )
        raise raised_error

    adapter.iter_items.return_value = failing_iterator()
    received: dict[str, Any] = {}
    expected_result = object()

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 10
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )
    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: int(value)
    )

    def fake_process_dataset_item(
        *args: Any
    ) -> None:
        context = args[-1]
        context.processed_count += 1
        context.articles.append({
            "id": "id-1"
        })

    monkeypatch.setattr(
        module,
        "process_dataset_item",
        fake_process_dataset_item
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args: None
    )

    def fake_build_partial_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return expected_result

    monkeypatch.setattr(
        module,
        "build_partial_result",
        fake_build_partial_result
    )

    result = module.extract_dataset_from_source(
        make_source(),
        adapter
    )

    assert result is expected_result
    assert received["articles"] == [
        {
            "id": "id-1"
        }
    ]
    assert received["message"] == (
        "1 article(s) extrait(s) avec 1 erreur(s)."
    )
    assert len(received["errors"]) == 1
    assert "Extraction interrompue pendant la lecture de Fakeddit" in (
        received["errors"][0]
    )


def test_extract_dataset_from_source_returns_empty_on_reading_error_without_article(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()

    def failing_iterator() -> Iterator[Any]:
        raise ValueError("Lecture impossible")
        yield

    adapter.iter_items.return_value = failing_iterator()
    received: dict[str, Any] = {}
    expected_result = object()

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 10
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args: None
    )

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

    result = module.extract_dataset_from_source(
        make_source(),
        adapter
    )

    assert result is expected_result
    assert received.get("articles", []) == []
    assert received["analyzed_count"] == 0
    assert received["rejected_count"] == 0
    assert received["message"].startswith(
        "Extraction interrompue pendant la lecture de Fakeddit"
    )


def test_extract_dataset_from_source_returns_empty_without_valid_article(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    adapter.iter_items.return_value = iter([
        (
            "id-1",
            0,
            {}
        )
    ])
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 10
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )
    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: int(value)
    )

    def fake_process_dataset_item(
        *args: Any
    ) -> None:
        context = args[-1]
        context.processed_count += 1
        context.rejection_stats["invalid_article"] += 1

    monkeypatch.setattr(
        module,
        "process_dataset_item",
        fake_process_dataset_item
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )

    def fake_build_empty_result(
        **kwargs: Any
    ) -> Any:
        received.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        module,
        "build_empty_result",
        fake_build_empty_result
    )

    module.extract_dataset_from_source(
        make_source(),
        adapter
    )

    assert received["message"] == "Aucun article valide extrait."
    assert received["analyzed_count"] == 1
    assert received["rejected_count"] == 1
    assert received["rejection_reasons"] == {
        "invalid_article": 1
    }


def test_extract_dataset_from_source_passes_source_id_to_item_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = make_adapter()
    adapter.iter_items.return_value = iter([
        (
            "id-1",
            0,
            {}
        )
    ])
    received_source_names: list[str] = []

    monkeypatch.setattr(
        module,
        "parse_boolean",
        lambda value, default: True
    )
    monkeypatch.setattr(
        module,
        "get_dataset_max_articles",
        lambda source: 1
    )
    monkeypatch.setattr(
        module,
        "resolve_dataset_directory",
        lambda *args: tmp_path
    )
    monkeypatch.setattr(
        module,
        "get_dataset_files",
        lambda *args: [
            tmp_path / "dataset.csv"
        ]
    )
    monkeypatch.setattr(
        module,
        "get_dataset_filters",
        lambda source: {}
    )
    monkeypatch.setattr(
        module,
        "parse_non_negative_integer",
        lambda value, default: int(value)
    )

    def fake_process_dataset_item(
        adapter: DatasetAdapter,
        item: Any,
        item_index: int,
        item_identifier: Any,
        source: Mapping[str, Any],
        source_name: str,
        filters: Mapping[str, Any],
        remove_duplicates: bool,
        context: module.DatasetExtractionContext
    ) -> None:
        received_source_names.append(source_name)
        context.processed_count += 1
        context.articles.append({
            "id": item_identifier
        })

    monkeypatch.setattr(
        module,
        "process_dataset_item",
        fake_process_dataset_item
    )
    monkeypatch.setattr(
        module,
        "log_dataset_summary",
        lambda *args: None
    )
    monkeypatch.setattr(
        module,
        "build_success_result",
        lambda **kwargs: kwargs
    )

    module.extract_dataset_from_source(
        make_source(
            source_id="fakeddit",
            name="Nom lisible"
        ),
        adapter
    )

    assert received_source_names == [
        "fakeddit"
    ]