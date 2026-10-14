"""Tests du contrat commun des adaptateurs de datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/datasets/test_dataset_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

import src.extractors.datasets.dataset_adapter as module


# Doubles de test

def fake_find_files(
    dataset_directory: Path,
    source: Mapping[str, Any]
) -> list[Path]:
    """Retourne une liste vide de fichiers."""

    return []


def fake_iter_items(
    dataset_files: list[Path],
    source: Mapping[str, Any]
) -> Iterator[tuple[str, int, Any]]:
    """Retourne un itérateur vide."""

    return iter(())


def fake_build_article(
    item: Any,
    item_index: int,
    item_identifier: str,
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Construit un article minimal."""

    return {
        "id": item_identifier,
        "title": "Article"
    }


def fake_validate_item(
    item: Any,
    filters: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Valide systématiquement un élément."""

    return True, ""


def make_adapter(
    source_id: str = "fakeddit",
    default_name: str = "Fakeddit",
    supported_extensions: frozenset[str] | None = None,
    find_files: Any = fake_find_files,
    iter_items: Any = fake_iter_items,
    build_article: Any = fake_build_article,
    validate_item: Any = fake_validate_item
) -> module.DatasetAdapter:
    """Construit un adaptateur valide."""

    return module.DatasetAdapter(
        source_id=source_id,
        default_name=default_name,
        supported_extensions=(
            supported_extensions
            if supported_extensions is not None
            else frozenset({".csv", ".json"})
        ),
        find_files=find_files,
        iter_items=iter_items,
        build_article=build_article,
        validate_item=validate_item
    )


# DatasetAdapter

def test_dataset_adapter_normalizes_identity(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )

    adapter = make_adapter(
        source_id="  fakeddit  ",
        default_name="  Fakeddit Dataset  "
    )

    assert adapter.source_id == "fakeddit"
    assert adapter.default_name == "Fakeddit Dataset"


def test_dataset_adapter_uses_source_id_as_default_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value).strip()
        if value is not None
        else ""
    )

    adapter = make_adapter(
        source_id="fakeddit",
        default_name=""
    )

    assert adapter.default_name == "fakeddit"


@pytest.mark.parametrize(
    ("raw_extensions", "expected_extensions"),
    [
        (
            frozenset({".csv", ".json"}),
            frozenset({".csv", ".json"})
        ),
        (
            frozenset({"csv", "json"}),
            frozenset({".csv", ".json"})
        ),
        (
            frozenset({" CSV ", " .JSON "}),
            frozenset({".csv", ".json"})
        ),
        (
            frozenset({".CSV", "JSONL"}),
            frozenset({".csv", ".jsonl"})
        ),
        (
            frozenset({"csv", ".csv", " CSV "}),
            frozenset({".csv"})
        )
    ]
)
def test_dataset_adapter_normalizes_extensions(
    raw_extensions: frozenset[str],
    expected_extensions: frozenset[str]
) -> None:
    adapter = make_adapter(
        supported_extensions=raw_extensions
    )

    assert adapter.supported_extensions == expected_extensions


def test_dataset_adapter_ignores_empty_extensions(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_normalize_value(value: Any) -> str:
        if value is None:
            return ""

        return str(value).strip()

    monkeypatch.setattr(
        module,
        "normalize_value",
        fake_normalize_value
    )

    adapter = make_adapter(
        supported_extensions=frozenset({
            ".csv",
            "",
            "   "
        })
    )

    assert adapter.supported_extensions == frozenset({
        ".csv"
    })


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        "   ",
        None
    ]
)
def test_dataset_adapter_rejects_empty_source_id(
    source_id: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
        if value is None
        else str(value).strip()
    )

    with pytest.raises(
        ValueError,
        match="L'identifiant d'un DatasetAdapter ne peut pas être vide"
    ):
        make_adapter(
            source_id=source_id
        )


@pytest.mark.parametrize(
    "extensions",
    [
        frozenset(),
        frozenset({""}),
        frozenset({"   "})
    ]
)
def test_dataset_adapter_rejects_empty_extensions(
    extensions: frozenset[str]
) -> None:
    with pytest.raises(
        ValueError,
        match="Aucune extension prise en charge pour fakeddit"
    ):
        make_adapter(
            supported_extensions=extensions
        )


@pytest.mark.parametrize(
    "invalid_callable",
    [
        None,
        42,
        "function",
        [],
        {}
    ]
)
def test_dataset_adapter_rejects_invalid_find_files(
    invalid_callable: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="find_files doit être une fonction appelable"
    ):
        make_adapter(
            find_files=invalid_callable
        )


@pytest.mark.parametrize(
    "invalid_callable",
    [
        None,
        42,
        "function",
        [],
        {}
    ]
)
def test_dataset_adapter_rejects_invalid_iter_items(
    invalid_callable: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="iter_items doit être une fonction appelable"
    ):
        make_adapter(
            iter_items=invalid_callable
        )


@pytest.mark.parametrize(
    "invalid_callable",
    [
        None,
        42,
        "function",
        [],
        {}
    ]
)
def test_dataset_adapter_rejects_invalid_build_article(
    invalid_callable: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="build_article doit être une fonction appelable"
    ):
        make_adapter(
            build_article=invalid_callable
        )


@pytest.mark.parametrize(
    "invalid_callable",
    [
        42,
        "function",
        [],
        {}
    ]
)
def test_dataset_adapter_rejects_invalid_validate_item(
    invalid_callable: Any
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            "validate_item doit être une fonction appelable ou None"
        )
    ):
        make_adapter(
            validate_item=invalid_callable
        )


def test_dataset_adapter_accepts_none_validator() -> None:
    adapter = make_adapter(
        validate_item=None
    )

    assert adapter.validate_item is None


def test_dataset_adapter_is_frozen() -> None:
    adapter = make_adapter()

    with pytest.raises(FrozenInstanceError):
        adapter.source_id = "isot"


def test_dataset_adapter_uses_slots() -> None:
    adapter = make_adapter()

    assert not hasattr(adapter, "__dict__")


def test_dataset_adapter_preserves_callable_references() -> None:
    adapter = make_adapter()

    assert adapter.find_files is fake_find_files
    assert adapter.iter_items is fake_iter_items
    assert adapter.build_article is fake_build_article
    assert adapter.validate_item is fake_validate_item


def test_dataset_adapter_equality() -> None:
    first_adapter = make_adapter()
    second_adapter = make_adapter()

    assert first_adapter == second_adapter


def test_dataset_adapter_inequality() -> None:
    first_adapter = make_adapter()
    second_adapter = make_adapter(
        source_id="isot",
        default_name="ISOT"
    )

    assert first_adapter != second_adapter


# build_article_from_item

def test_build_article_from_item_returns_article() -> None:
    expected_article = {
        "id": "item-1",
        "title": "Article"
    }
    received: dict[str, Any] = {}

    def build_article(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        received["item"] = item
        received["item_index"] = item_index
        received["item_identifier"] = item_identifier
        received["source"] = source
        return expected_article

    adapter = make_adapter(
        build_article=build_article
    )
    item = {
        "title": "Article brut"
    }
    source = {
        "source_id": "fakeddit"
    }

    result = module.build_article_from_item(
        adapter=adapter,
        item=item,
        item_index=3,
        item_identifier="item-3",
        source=source,
        source_name="Fakeddit"
    )

    assert result is expected_article
    assert received == {
        "item": item,
        "item_index": 3,
        "item_identifier": "item-3",
        "source": source
    }


@pytest.mark.parametrize(
    "raised_error",
    [
        TypeError("type invalide"),
        ValueError("valeur invalide"),
        AttributeError("attribut absent"),
        KeyError("champ"),
        OSError("erreur système")
    ]
)
def test_build_article_from_item_handles_expected_errors(
    raised_error: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    def failing_build_article(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        raise raised_error

    adapter = make_adapter(
        build_article=failing_build_article
    )

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.build_article_from_item(
        adapter=adapter,
        item={},
        item_index=7,
        item_identifier="item-7",
        source={},
        source_name="Fakeddit"
    )

    assert result == {}
    assert warnings == [
        (
            "Impossible de construire l'élément %s de %s pour %s : %s",
            7,
            "item-7",
            "Fakeddit",
            raised_error
        )
    ]


def test_build_article_from_item_propagates_unexpected_error() -> None:
    expected_error = RuntimeError("Erreur inattendue")

    def failing_build_article(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: Mapping[str, Any]
    ) -> dict[str, Any]:
        raise expected_error

    adapter = make_adapter(
        build_article=failing_build_article
    )

    with pytest.raises(RuntimeError) as error_info:
        module.build_article_from_item(
            adapter=adapter,
            item={},
            item_index=0,
            item_identifier="item-0",
            source={},
            source_name="Fakeddit"
        )

    assert error_info.value is expected_error


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        [],
        (),
        "article",
        42,
        True
    ]
)
def test_build_article_from_item_rejects_non_dict_result(
    invalid_result: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    adapter = make_adapter(
        build_article=lambda *args: invalid_result
    )

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.build_article_from_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="item-0",
        source={},
        source_name="Fakeddit"
    )

    assert result == {}
    assert warnings == [
        (
            "L'adaptateur %s a retourné un type invalide : %s.",
            "fakeddit",
            type(invalid_result).__name__
        )
    ]


def test_build_article_from_item_accepts_empty_dict() -> None:
    adapter = make_adapter(
        build_article=lambda *args: {}
    )

    result = module.build_article_from_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="item-0",
        source={},
        source_name="Fakeddit"
    )

    assert result == {}


def test_build_article_from_item_accepts_dict_subclass() -> None:
    class ArticleDict(dict[str, Any]):
        pass

    expected_article = ArticleDict({
        "id": "item-1"
    })
    adapter = make_adapter(
        build_article=lambda *args: expected_article
    )

    result = module.build_article_from_item(
        adapter=adapter,
        item={},
        item_index=1,
        item_identifier="item-1",
        source={},
        source_name="Fakeddit"
    )

    assert result is expected_article


# validate_adapter_item

def test_validate_adapter_item_accepts_item_without_validator() -> None:
    adapter = make_adapter(
        validate_item=None
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={
            "title": "Article"
        },
        filters={
            "require_title": True
        },
        source={
            "source_id": "fakeddit"
        }
    )

    assert result == (
        True,
        ""
    )


def test_validate_adapter_item_calls_validator() -> None:
    received: dict[str, Any] = {}

    def validate_item(
        item: Any,
        filters: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        received["item"] = item
        received["filters"] = filters
        received["source"] = source
        return True, "valid"

    adapter = make_adapter(
        validate_item=validate_item
    )
    item = {
        "title": "Article"
    }
    filters = {
        "require_title": True
    }
    source = {
        "source_id": "fakeddit"
    }

    result = module.validate_adapter_item(
        adapter=adapter,
        item=item,
        filters=filters,
        source=source
    )

    assert result == (
        True,
        "valid"
    )
    assert received == {
        "item": item,
        "filters": filters,
        "source": source
    }


@pytest.mark.parametrize(
    ("raw_valid", "expected_valid"),
    [
        (
            True,
            True
        ),
        (
            False,
            False
        ),
        (
            1,
            True
        ),
        (
            0,
            False
        ),
        (
            "yes",
            True
        ),
        (
            "",
            False
        ),
        (
            None,
            False
        )
    ]
)
def test_validate_adapter_item_normalizes_valid_flag(
    raw_valid: Any,
    expected_valid: bool
) -> None:
    adapter = make_adapter(
        validate_item=lambda *args: (
            raw_valid,
            ""
        )
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        expected_valid,
        ""
    )


def test_validate_adapter_item_normalizes_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[Any] = []

    def fake_normalize_value(value: Any) -> str:
        received.append(value)
        return str(value).strip().lower()

    monkeypatch.setattr(
        module,
        "normalize_value",
        fake_normalize_value
    )

    adapter = make_adapter(
        validate_item=lambda *args: (
            False,
            " Missing Label "
        )
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        "missing label"
    )
    assert received[-1] == " Missing Label "


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "",
        "   "
    ]
)
def test_validate_adapter_item_returns_empty_normalized_reason(
    reason: Any
) -> None:
    adapter = make_adapter(
        validate_item=lambda *args: (
            False,
            reason
        )
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        ""
    )


@pytest.mark.parametrize(
    "raised_error",
    [
        TypeError("type invalide"),
        ValueError("valeur invalide"),
        AttributeError("attribut absent"),
        KeyError("champ"),
        OSError("erreur système")
    ]
)
def test_validate_adapter_item_handles_expected_errors(
    raised_error: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    def failing_validator(
        item: Any,
        filters: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        raise raised_error

    adapter = make_adapter(
        validate_item=failing_validator
    )

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        "invalid_specific_validation"
    )
    assert warnings == [
        (
            "Erreur pendant la validation de %s : %s",
            "fakeddit",
            raised_error
        )
    ]


def test_validate_adapter_item_propagates_unexpected_error() -> None:
    expected_error = RuntimeError("Erreur inattendue")

    def failing_validator(
        item: Any,
        filters: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        raise expected_error

    adapter = make_adapter(
        validate_item=failing_validator
    )

    with pytest.raises(RuntimeError) as error_info:
        module.validate_adapter_item(
            adapter=adapter,
            item={},
            filters={},
            source={}
        )

    assert error_info.value is expected_error


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        True,
        False,
        [],
        {},
        "valid",
        (
            True,
        ),
        (
            True,
            "",
            "extra"
        )
    ]
)
def test_validate_adapter_item_rejects_invalid_result_shape(
    invalid_result: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    adapter = make_adapter(
        validate_item=lambda *args: invalid_result
    )

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        "invalid_specific_validation"
    )
    assert warnings == [
        (
            "Le validateur de %s doit retourner un tuple (bool, raison).",
            "fakeddit"
        )
    ]


def test_validate_adapter_item_accepts_tuple_subclass() -> None:
    class ValidationResult(tuple):
        pass

    adapter = make_adapter(
        validate_item=lambda *args: ValidationResult((
            True,
            "valid"
        ))
    )

    result = module.validate_adapter_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        True,
        "valid"
    )


def test_validate_adapter_item_does_not_mutate_arguments() -> None:
    item = {
        "title": "Article"
    }
    filters = {
        "require_title": True
    }
    source = {
        "source_id": "fakeddit"
    }

    original_item = dict(item)
    original_filters = dict(filters)
    original_source = dict(source)

    adapter = make_adapter(
        validate_item=lambda *args: (
            True,
            ""
        )
    )

    module.validate_adapter_item(
        adapter=adapter,
        item=item,
        filters=filters,
        source=source
    )

    assert item == original_item
    assert filters == original_filters
    assert source == original_source


# Types publics

def test_dataset_item_alias_accepts_any_value() -> None:
    values: list[module.DatasetItem] = [
        None,
        {},
        [],
        "article",
        42
    ]

    assert values == [
        None,
        {},
        [],
        "article",
        42
    ]


def test_find_files_callable_contract() -> None:
    callable_value: module.FindFilesCallable = fake_find_files

    result = callable_value(
        Path("dataset"),
        {
            "source_id": "fakeddit"
        }
    )

    assert result == []


def test_iter_items_callable_contract() -> None:
    callable_value: module.IterItemsCallable = fake_iter_items

    result = callable_value(
        [],
        {
            "source_id": "fakeddit"
        }
    )

    assert list(result) == []


def test_build_article_callable_contract() -> None:
    callable_value: module.BuildArticleCallable = fake_build_article

    result = callable_value(
        {},
        0,
        "item-0",
        {}
    )

    assert result == {
        "id": "item-0",
        "title": "Article"
    }


def test_validate_item_callable_contract() -> None:
    callable_value: module.ValidateItemCallable = fake_validate_item

    result = callable_value(
        {},
        {},
        {}
    )

    assert result == (
        True,
        ""
    )