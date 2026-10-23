"""Tests du contrat commun des adaptateurs d'API CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/apis/test_api_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

import src.extractors.apis.api_adapter as module
from src.extractors.apis.api_adapter import (
    ApiAdapter,
    build_article_from_api_item,
    validate_api_item,
)

# Fonctions de test

def iter_items(
    source: dict[str, Any],
    maximum_articles: int
):
    """Retourne un itérateur minimal pour les tests."""

    yield "item-1", 0, {
        "title": "Article"
    }


def build_article(
    item: Any,
    item_index: int,
    item_identifier: str,
    source: dict[str, Any]
) -> dict[str, Any]:
    """Construit un article minimal."""

    return {
        "id": item_identifier,
        "title": item.get("title", ""),
        "source": source.get("source_id", ""),
        "item_index": item_index
    }


def validate_item(
    item: Any,
    filters: dict[str, Any],
    source: dict[str, Any]
) -> tuple[bool, str]:
    """Valide un élément minimal."""

    return True, ""


def build_adapter(
    *,
    source_id: str = "test_api",
    default_name: str = "Test API",
    iterator: Any = iter_items,
    builder: Any = build_article,
    validator: Any = None
) -> ApiAdapter:
    """Construit un adaptateur pour les tests."""

    return ApiAdapter(
        source_id=source_id,
        default_name=default_name,
        iter_items=iterator,
        build_article=builder,
        validate_item=validator
    )


# Construction de l'adaptateur

def test_api_adapter_normalizes_identity() -> None:
    adapter = build_adapter(
        source_id="  test_api  ",
        default_name="  Test API  "
    )

    assert adapter.source_id == "test_api"
    assert adapter.default_name == "Test API"


def test_api_adapter_uses_source_id_as_default_name() -> None:
    adapter = build_adapter(
        source_id="test_api",
        default_name="   "
    )

    assert adapter.source_id == "test_api"
    assert adapter.default_name == "test_api"


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        " ",
        "\n",
        None
    ]
)
def test_api_adapter_rejects_empty_source_id(
    source_id: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="L'identifiant d'un ApiAdapter ne peut pas être vide"
    ):
        build_adapter(
            source_id=source_id
        )


@pytest.mark.parametrize(
    "iterator",
    [
        None,
        42,
        "iter_items",
        [],
        {}
    ]
)
def test_api_adapter_rejects_invalid_iter_items(
    iterator: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="iter_items doit être une fonction appelable"
    ):
        build_adapter(
            iterator=iterator
        )


@pytest.mark.parametrize(
    "builder",
    [
        None,
        42,
        "build_article",
        [],
        {}
    ]
)
def test_api_adapter_rejects_invalid_build_article(
    builder: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="build_article doit être une fonction appelable"
    ):
        build_adapter(
            builder=builder
        )


@pytest.mark.parametrize(
    "validator",
    [
        42,
        "validate_item",
        [],
        {}
    ]
)
def test_api_adapter_rejects_invalid_validate_item(
    validator: Any
) -> None:
    with pytest.raises(
        TypeError,
        match=(
            "validate_item doit être une fonction appelable ou None"
        )
    ):
        build_adapter(
            validator=validator
        )


def test_api_adapter_accepts_callable_validator() -> None:
    adapter = build_adapter(
        validator=validate_item
    )

    assert adapter.validate_item is validate_item


def test_api_adapter_accepts_none_validator() -> None:
    adapter = build_adapter(
        validator=None
    )

    assert adapter.validate_item is None


def test_api_adapter_is_frozen() -> None:
    adapter = build_adapter()

    with pytest.raises(FrozenInstanceError):
        adapter.source_id = "modified"  # type: ignore[misc]


def test_api_adapter_uses_slots() -> None:
    adapter = build_adapter()

    assert not hasattr(adapter, "__dict__")


# Construction d'article

def test_build_article_from_api_item_returns_article() -> None:
    adapter = build_adapter()

    result = build_article_from_api_item(
        adapter=adapter,
        item={
            "title": "Un article valide"
        },
        item_index=3,
        item_identifier="article-3",
        source={
            "source_id": "test_api"
        },
        source_name="Test API"
    )

    assert result == {
        "id": "article-3",
        "title": "Un article valide",
        "source": "test_api",
        "item_index": 3
    }


def test_build_article_from_api_item_passes_all_arguments() -> None:
    captured_arguments: dict[str, Any] = {}

    def fake_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> dict[str, Any]:
        captured_arguments.update({
            "item": item,
            "item_index": item_index,
            "item_identifier": item_identifier,
            "source": source
        })
        return {
            "id": item_identifier
        }

    adapter = build_adapter(
        builder=fake_builder
    )
    item = {
        "title": "Article"
    }
    source = {
        "source_id": "test_api",
        "enabled": True
    }

    result = build_article_from_api_item(
        adapter=adapter,
        item=item,
        item_index=7,
        item_identifier="item-7",
        source=source,
        source_name="Test API"
    )

    assert result == {
        "id": "item-7"
    }
    assert captured_arguments == {
        "item": item,
        "item_index": 7,
        "item_identifier": "item-7",
        "source": source
    }


@pytest.mark.parametrize(
    "exception",
    [
        TypeError("Type invalide."),
        ValueError("Valeur invalide."),
        AttributeError("Attribut absent."),
        KeyError("clé"),
        OSError("Erreur système.")
    ]
)
def test_build_article_from_api_item_handles_expected_errors(
    exception: Exception
) -> None:
    def failing_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> dict[str, Any]:
        raise exception

    adapter = build_adapter(
        builder=failing_builder
    )

    result = build_article_from_api_item(
        adapter=adapter,
        item={},
        item_index=1,
        item_identifier="item-1",
        source={},
        source_name="Test API"
    )

    assert result == {}


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
def test_build_article_from_api_item_rejects_non_dictionary_result(
    invalid_result: Any
) -> None:
    def invalid_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> Any:
        return invalid_result

    adapter = build_adapter(
        builder=invalid_builder
    )

    result = build_article_from_api_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="item-0",
        source={},
        source_name="Test API"
    )

    assert result == {}


def test_build_article_from_api_item_accepts_empty_dictionary() -> None:
    def empty_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> dict[str, Any]:
        return {}

    adapter = build_adapter(
        builder=empty_builder
    )

    result = build_article_from_api_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="item-0",
        source={},
        source_name="Test API"
    )

    assert result == {}


def test_build_article_from_api_item_logs_builder_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warning_calls: list[tuple[Any, ...]] = []

    def failing_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> dict[str, Any]:
        raise ValueError("Article impossible.")

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append(args)
    )

    adapter = build_adapter(
        builder=failing_builder
    )

    build_article_from_api_item(
        adapter=adapter,
        item={},
        item_index=4,
        item_identifier="item-4",
        source={},
        source_name="Test API"
    )

    assert len(warning_calls) == 1
    assert warning_calls[0][1] == 4
    assert warning_calls[0][2] == "item-4"
    assert warning_calls[0][3] == "Test API"
    assert isinstance(
        warning_calls[0][4],
        ValueError
    )
    assert str(warning_calls[0][4]) == "Article impossible."


def test_build_article_from_api_item_logs_invalid_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warning_calls: list[tuple[Any, ...]] = []

    def invalid_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> list[Any]:
        return []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append(args)
    )

    adapter = build_adapter(
        builder=invalid_builder
    )

    build_article_from_api_item(
        adapter=adapter,
        item={},
        item_index=0,
        item_identifier="item-0",
        source={},
        source_name="Test API"
    )

    assert len(warning_calls) == 1
    assert warning_calls[0][1:] == (
        "test_api",
        "list"
    )


def test_build_article_from_api_item_does_not_catch_unexpected_error() -> None:
    def failing_builder(
        item: Any,
        item_index: int,
        item_identifier: str,
        source: dict[str, Any]
    ) -> dict[str, Any]:
        raise RuntimeError("Erreur inattendue.")

    adapter = build_adapter(
        builder=failing_builder
    )

    with pytest.raises(
        RuntimeError,
        match="Erreur inattendue"
    ):
        build_article_from_api_item(
            adapter=adapter,
            item={},
            item_index=0,
            item_identifier="item-0",
            source={},
            source_name="Test API"
        )


# Validation spécifique

def test_validate_api_item_accepts_item_without_validator() -> None:
    adapter = build_adapter(
        validator=None
    )

    result = validate_api_item(
        adapter=adapter,
        item={
            "title": "Article"
        },
        filters={
            "require_title": True
        },
        source={
            "source_id": "test_api"
        }
    )

    assert result == (
        True,
        ""
    )


def test_validate_api_item_returns_validator_result() -> None:
    adapter = build_adapter(
        validator=lambda item, filters, source: (
            True,
            "valide"
        )
    )

    result = validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        True,
        "valide"
    )


def test_validate_api_item_passes_all_arguments() -> None:
    captured_arguments: dict[str, Any] = {}

    def fake_validator(
        item: Any,
        filters: dict[str, Any],
        source: dict[str, Any]
    ) -> tuple[bool, str]:
        captured_arguments.update({
            "item": item,
            "filters": filters,
            "source": source
        })
        return True, ""

    adapter = build_adapter(
        validator=fake_validator
    )
    item = {
        "id": "item-1"
    }
    filters = {
        "require_title": True
    }
    source = {
        "source_id": "test_api"
    }

    validate_api_item(
        adapter=adapter,
        item=item,
        filters=filters,
        source=source
    )

    assert captured_arguments == {
        "item": item,
        "filters": filters,
        "source": source
    }


@pytest.mark.parametrize(
    ("valid", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("", False),
        (None, False)
    ]
)
def test_validate_api_item_converts_valid_value_to_boolean(
    valid: Any,
    expected: bool
) -> None:
    adapter = build_adapter(
        validator=lambda item, filters, source: (
            valid,
            ""
        )
    )

    result = validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        expected,
        ""
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (" raison ", "raison"),
        ("", ""),
        ("   ", ""),
        (None, ""),
        (42, "42"),
        (True, "True")
    ]
)
def test_validate_api_item_normalizes_reason(
    reason: Any,
    expected: str
) -> None:
    adapter = build_adapter(
        validator=lambda item, filters, source: (
            False,
            reason
        )
    )

    result = validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        expected
    )


@pytest.mark.parametrize(
    "exception",
    [
        TypeError("Type invalide."),
        ValueError("Valeur invalide."),
        AttributeError("Attribut absent."),
        KeyError("clé"),
        OSError("Erreur système.")
    ]
)
def test_validate_api_item_handles_expected_errors(
    exception: Exception
) -> None:
    def failing_validator(
        item: Any,
        filters: dict[str, Any],
        source: dict[str, Any]
    ) -> tuple[bool, str]:
        raise exception

    adapter = build_adapter(
        validator=failing_validator
    )

    result = validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        "validation_specifique_invalide"
    )


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        True,
        False,
        42,
        "result",
        [],
        {},
        (True,),
        (True, "", "extra")
    ]
)
def test_validate_api_item_rejects_invalid_result(
    invalid_result: Any
) -> None:
    def invalid_validator(
        item: Any,
        filters: dict[str, Any],
        source: dict[str, Any]
    ) -> Any:
        return invalid_result

    adapter = build_adapter(
        validator=invalid_validator
    )

    result = validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result == (
        False,
        "resultat_validation_invalide"
    )


def test_validate_api_item_accepts_list_inside_tuple() -> None:
    adapter = build_adapter(
        validator=lambda item, filters, source: (
            True,
            [
                "raison"
            ]
        )
    )

    result = validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert result[0] is True
    assert isinstance(result[1], str)


def test_validate_api_item_logs_validation_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warning_calls: list[tuple[Any, ...]] = []

    def failing_validator(
        item: Any,
        filters: dict[str, Any],
        source: dict[str, Any]
    ) -> tuple[bool, str]:
        raise ValueError("Validation impossible.")

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append(args)
    )

    adapter = build_adapter(
        validator=failing_validator
    )

    validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert len(warning_calls) == 1
    assert warning_calls[0][1] == "test_api"
    assert isinstance(
        warning_calls[0][2],
        ValueError
    )


def test_validate_api_item_logs_invalid_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warning_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append(args)
    )

    adapter = build_adapter(
        validator=lambda item, filters, source: True
    )

    validate_api_item(
        adapter=adapter,
        item={},
        filters={},
        source={}
    )

    assert len(warning_calls) == 1
    assert warning_calls[0][1] == "test_api"


def test_validate_api_item_does_not_catch_unexpected_error() -> None:
    def failing_validator(
        item: Any,
        filters: dict[str, Any],
        source: dict[str, Any]
    ) -> tuple[bool, str]:
        raise RuntimeError("Erreur inattendue.")

    adapter = build_adapter(
        validator=failing_validator
    )

    with pytest.raises(
        RuntimeError,
        match="Erreur inattendue"
    ):
        validate_api_item(
            adapter=adapter,
            item={},
            filters={},
            source={}
        )