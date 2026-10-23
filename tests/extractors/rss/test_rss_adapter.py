"""Tests du contrat commun des adaptateurs RSS CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/rss copy/test_rss_adapter.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from src.extractors.rss import rss_adapter as module

# Doubles de test

class FakeContext:
    """Fournit un contexte minimal compatible avec RssValidationContext."""

    def __init__(
        self,
        source: Mapping[str, Any] | None = None,
        filters: Mapping[str, Any] | None = None
    ) -> None:
        self.source = source or {"name": "Flux de test"}
        self.filters = filters or {"require_title": True}


def iter_items(
    source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Retourne une entrée RSS minimale."""

    return [{"title": source.get("name", "RSS")}]


def build_article(
    item: Any,
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Construit un article minimal."""

    return {
        "title": item.get("title", ""),
        "source": source.get("name", "")
    }


def build_adapter(
    **overrides: Any
) -> module.RssAdapter:
    """Construit un adaptateur RSS configurable."""

    values = {
        "iter_items": iter_items,
        "build_article": build_article
    }
    values.update(overrides)

    return module.RssAdapter(**values)


# Construction de l'adaptateur

def test_rss_adapter_builds_with_required_callbacks() -> None:
    adapter = build_adapter()

    assert adapter.iter_items is iter_items
    assert adapter.build_article is build_article
    assert adapter.validate_item is None
    assert adapter.validate_article is None
    assert adapter.default_name == "RSS"


def test_rss_adapter_accepts_optional_callbacks() -> None:
    def validate_item(
        item: Any,
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return True, ""

    def validate_built_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return True, ""

    adapter = build_adapter(
        validate_item=validate_item,
        validate_article=validate_built_article
    )

    assert adapter.validate_item is validate_item
    assert adapter.validate_article is validate_built_article


def test_rss_adapter_normalizes_default_name() -> None:
    adapter = build_adapter(default_name="  Flux RSS personnalisé  ")

    assert adapter.default_name == "Flux RSS personnalisé"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   "
    ]
)
def test_rss_adapter_uses_rss_when_default_name_is_empty(
    value: Any
) -> None:
    adapter = build_adapter(default_name=value)

    assert adapter.default_name == "RSS"


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        (
            "iter_items",
            None,
            "iter_items doit être une fonction appelable."
        ),
        (
            "iter_items",
            "invalid",
            "iter_items doit être une fonction appelable."
        ),
        (
            "build_article",
            None,
            "build_article doit être une fonction appelable."
        ),
        (
            "build_article",
            42,
            "build_article doit être une fonction appelable."
        ),
        (
            "validate_item",
            "invalid",
            "validate_item doit être une fonction appelable ou None."
        ),
        (
            "validate_article",
            [],
            "validate_article doit être une fonction appelable ou None."
        )
    ]
)
def test_rss_adapter_rejects_non_callable_callbacks(
    field: str,
    value: Any,
    expected_message: str
) -> None:
    with pytest.raises(TypeError, match=expected_message):
        build_adapter(**{field: value})


def test_rss_adapter_is_immutable() -> None:
    adapter = build_adapter()

    with pytest.raises(FrozenInstanceError):
        adapter.default_name = "Nouveau nom"  # type: ignore[misc]


def test_rss_adapter_uses_slots() -> None:
    adapter = build_adapter()

    assert not hasattr(adapter, "__dict__")


# Normalisation des validations

@pytest.mark.parametrize(
    "result",
    [
        None,
        True,
        False,
        (),
        (True,),
        (True, "", "extra"),
        [True, ""],
        "invalid"
    ]
)
def test_normalize_validation_result_rejects_invalid_structure(
    result: Any
) -> None:
    normalized = module.normalize_validation_result(
        result=result,
        invalid_reason="resultat_invalide",
        rejected_reason="element_rejete"
    )

    assert normalized == (False, "resultat_invalide")


@pytest.mark.parametrize(
    "valid",
    [
        1,
        0,
        "True",
        "False",
        None,
        object()
    ]
)
def test_normalize_validation_result_requires_boolean_status(
    valid: Any
) -> None:
    result = module.normalize_validation_result(
        result=(valid, "raison"),
        invalid_reason="resultat_invalide",
        rejected_reason="element_rejete"
    )

    assert result == (False, "resultat_invalide")


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "raison ignorée",
        None,
        42
    ]
)
def test_normalize_validation_result_removes_reason_when_valid(
    reason: Any
) -> None:
    result = module.normalize_validation_result(
        result=(True, reason),
        invalid_reason="resultat_invalide",
        rejected_reason="element_rejete"
    )

    assert result == (True, "")


def test_normalize_validation_result_normalizes_rejection_reason() -> None:
    result = module.normalize_validation_result(
        result=(False, "  contenu insuffisant  "),
        invalid_reason="resultat_invalide",
        rejected_reason="element_rejete"
    )

    assert result == (False, "contenu insuffisant")


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "",
        "   "
    ]
)
def test_normalize_validation_result_uses_default_rejection_reason(
    reason: Any
) -> None:
    result = module.normalize_validation_result(
        result=(False, reason),
        invalid_reason="resultat_invalide",
        rejected_reason="element_rejete"
    )

    assert result == (False, "element_rejete")


# Validation des entrées RSS

def test_validate_rss_item_requires_rss_adapter() -> None:
    context = FakeContext()

    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de RssAdapter."
    ):
        module.validate_rss_item(
            item={"title": "Article"},
            context=context,
            adapter=object()  # type: ignore[arg-type]
        )


def test_validate_rss_item_accepts_item_without_specific_validator() -> None:
    context = FakeContext()
    adapter = build_adapter()

    result = module.validate_rss_item(
        item={"title": "Article"},
        context=context,
        adapter=adapter
    )

    assert result == (True, "")


def test_validate_rss_item_calls_specific_validator() -> None:
    received: dict[str, Any] = {}

    def validate_item(
        item: Any,
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        received["item"] = item
        received["source"] = source
        return True, "raison ignorée"

    item = {"title": "Article"}
    context = FakeContext(source={"name": "Source RSS"})
    adapter = build_adapter(validate_item=validate_item)

    result = module.validate_rss_item(
        item=item,
        context=context,
        adapter=adapter
    )

    assert result == (True, "")
    assert received == {
        "item": item,
        "source": context.source
    }


def test_validate_rss_item_returns_specific_rejection_reason() -> None:
    def validate_item(
        item: Any,
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return False, "  entree_sans_titre  "

    context = FakeContext()
    adapter = build_adapter(validate_item=validate_item)

    result = module.validate_rss_item(
        item={},
        context=context,
        adapter=adapter
    )

    assert result == (False, "entree_sans_titre")


def test_validate_rss_item_uses_default_rejection_reason() -> None:
    def validate_item(
        item: Any,
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return False, ""

    context = FakeContext()
    adapter = build_adapter(validate_item=validate_item)

    result = module.validate_rss_item(
        item={},
        context=context,
        adapter=adapter
    )

    assert result == (False, "entree_invalide")


def test_validate_rss_item_rejects_invalid_callback_result() -> None:
    def validate_item(
        item: Any,
        source: Mapping[str, Any]
    ) -> Any:
        return True

    context = FakeContext()
    adapter = build_adapter(validate_item=validate_item)

    result = module.validate_rss_item(
        item={},
        context=context,
        adapter=adapter
    )

    assert result == (False, "validation_entree_invalide")


def test_validate_rss_item_propagates_callback_exception() -> None:
    def validate_item(
        item: Any,
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        raise RuntimeError("Erreur du validateur")

    context = FakeContext()
    adapter = build_adapter(validate_item=validate_item)

    with pytest.raises(RuntimeError, match="Erreur du validateur"):
        module.validate_rss_item(
            item={},
            context=context,
            adapter=adapter
        )


# Validation des articles RSS

def test_validate_rss_article_requires_rss_adapter() -> None:
    context = FakeContext()

    with pytest.raises(
        TypeError,
        match="adapter doit être une instance de RssAdapter."
    ):
        module.validate_rss_article(
            article={"title": "Article"},
            context=context,
            adapter=object()  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "article",
    [
        None,
        "article",
        [],
        42
    ]
)
def test_validate_rss_article_rejects_non_mapping_article(
    article: Any
) -> None:
    context = FakeContext()
    adapter = build_adapter()

    result = module.validate_rss_article(
        article=article,
        context=context,
        adapter=adapter
    )

    assert result == (False, "article_invalide")


def test_validate_rss_article_calls_common_validator(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def fake_validate_article(
        article: Mapping[str, Any],
        filters: Mapping[str, Any]
    ) -> tuple[bool, str]:
        received["article"] = article
        received["filters"] = filters
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article",
        fake_validate_article
    )

    article = {"title": "Article RSS"}
    context = FakeContext(filters={"require_title": True})
    adapter = build_adapter()

    result = module.validate_rss_article(
        article=article,
        context=context,
        adapter=adapter
    )

    assert result == (True, "")
    assert received == {
        "article": article,
        "filters": context.filters
    }


def test_validate_rss_article_calls_specific_validator_before_common_one(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def validate_specific_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        calls.append("specific")
        return True, ""

    def validate_common_article(
        article: Mapping[str, Any],
        filters: Mapping[str, Any]
    ) -> tuple[bool, str]:
        calls.append("common")
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article",
        validate_common_article
    )

    context = FakeContext()
    adapter = build_adapter(
        validate_article=validate_specific_article
    )

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (True, "")
    assert calls == ["specific", "common"]


def test_validate_rss_article_passes_source_to_specific_validator(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def validate_specific_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        received["article"] = article
        received["source"] = source
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: (True, "")
    )

    article = {"title": "Article RSS"}
    context = FakeContext(source={"name": "Source spécifique"})
    adapter = build_adapter(
        validate_article=validate_specific_article
    )

    result = module.validate_rss_article(
        article=article,
        context=context,
        adapter=adapter
    )

    assert result == (True, "")
    assert received == {
        "article": article,
        "source": context.source
    }


def test_validate_rss_article_stops_after_specific_rejection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    common_validator_called = False

    def validate_specific_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return False, "  article_hors_sujet  "

    def validate_common_article(
        article: Mapping[str, Any],
        filters: Mapping[str, Any]
    ) -> tuple[bool, str]:
        nonlocal common_validator_called
        common_validator_called = True
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article",
        validate_common_article
    )

    context = FakeContext()
    adapter = build_adapter(
        validate_article=validate_specific_article
    )

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (False, "article_hors_sujet")
    assert common_validator_called is False


def test_validate_rss_article_uses_specific_default_rejection_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def validate_specific_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return False, ""

    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: (True, "")
    )

    context = FakeContext()
    adapter = build_adapter(
        validate_article=validate_specific_article
    )

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (False, "article_invalide")


def test_validate_rss_article_rejects_invalid_specific_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def validate_specific_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> Any:
        return "invalid"

    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: (True, "")
    )

    context = FakeContext()
    adapter = build_adapter(
        validate_article=validate_specific_article
    )

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (False, "validation_article_invalide")


def test_validate_rss_article_propagates_specific_validator_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def validate_specific_article(
        article: Mapping[str, Any],
        source: Mapping[str, Any]
    ) -> tuple[bool, str]:
        raise RuntimeError("Erreur de validation spécifique")

    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: (True, "")
    )

    context = FakeContext()
    adapter = build_adapter(
        validate_article=validate_specific_article
    )

    with pytest.raises(
        RuntimeError,
        match="Erreur de validation spécifique"
    ):
        module.validate_rss_article(
            article={"title": "Article RSS"},
            context=context,
            adapter=adapter
        )


def test_validate_rss_article_returns_common_rejection_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: (
            False,
            "  contenu_insuffisant  "
        )
    )

    context = FakeContext()
    adapter = build_adapter()

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (False, "contenu_insuffisant")


def test_validate_rss_article_uses_common_default_rejection_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: (False, "")
    )

    context = FakeContext()
    adapter = build_adapter()

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (False, "article_invalide")


def test_validate_rss_article_rejects_invalid_common_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "validate_article",
        lambda article, filters: True
    )

    context = FakeContext()
    adapter = build_adapter()

    result = module.validate_rss_article(
        article={"title": "Article RSS"},
        context=context,
        adapter=adapter
    )

    assert result == (False, "validation_commune_invalide")


def test_validate_rss_article_propagates_common_validator_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def validate_common_article(
        article: Mapping[str, Any],
        filters: Mapping[str, Any]
    ) -> tuple[bool, str]:
        raise RuntimeError("Erreur de validation commune")

    monkeypatch.setattr(
        module,
        "validate_article",
        validate_common_article
    )

    context = FakeContext()
    adapter = build_adapter()

    with pytest.raises(
        RuntimeError,
        match="Erreur de validation commune"
    ):
        module.validate_rss_article(
            article={"title": "Article RSS"},
            context=context,
            adapter=adapter
        )