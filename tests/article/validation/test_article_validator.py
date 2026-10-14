"""Tests unitaires du validateur métier des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/validation/test_article_validator.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import UserDict
from collections.abc import Mapping
from typing import Any

import pytest

from src.article.validation import article_validator as module


# Fabriques

def build_article(**overrides: Any) -> dict[str, Any]:
    """Construit un article complet valide pour les tests."""

    article = {
        field: ""
        for field in module.STANDARD_ARTICLE_FIELDS
    }

    article.update(
        {
            "id": "article-001",
            "title": "Titre suffisamment long",
            "text": "Contenu suffisamment long pour être valide.",
            "url": "https://example.com/article",
            "image_url": "https://example.com/image.jpg",
            "image_path": "images/article-001.jpg",
            "source": "example",
            "source_id": "source-001",
            "source_type": "rss",
            "author": "Auteur",
            "category": "general",
            "language": "fr",
            "country": "fr",
            "label": "real",
            "published_at": "2026-07-28T08:00:00+00:00",
            "extracted_at": "2026-07-28T09:00:00+00:00",
            "role": "acquisition",
            "metadata": {},
            "image_is_valid": True
        }
    )
    article.update(overrides)
    return article


def build_policy(**overrides: Any) -> dict[str, Any]:
    """Construit une politique déjà normalisée pour les tests."""

    policy = {
        "require_title": True,
        "require_text": False,
        "require_image": False,
        "require_label": False,
        "remove_deleted_content": True,
        "validate_urls": True,
        "min_title_length": 10,
        "min_text_length": 10,
        "min_total_text_length": 30,
        "allowed_labels": set()
    }
    policy.update(overrides)
    return policy


# Validation des images

@pytest.mark.parametrize(
    "article",
    [
        None,
        "article",
        123,
        [],
        ()
    ]
)
def test_has_validated_local_image_returns_false_for_non_mapping(
    article: Any
) -> None:
    assert module.has_validated_local_image(article) is False


@pytest.mark.parametrize(
    "image_path",
    [
        None,
        "",
        "   ",
        123,
        [],
        {}
    ]
)
def test_has_validated_local_image_returns_false_for_invalid_path(
    image_path: Any
) -> None:
    article = {
        "image_path": image_path,
        "image_is_valid": True
    }

    assert module.has_validated_local_image(article) is False


@pytest.mark.parametrize(
    "validation_status",
    [
        None,
        False,
        0,
        1,
        "true"
    ]
)
def test_has_validated_local_image_requires_strict_true_status(
    validation_status: Any
) -> None:
    article = {
        "image_path": "images/article.jpg",
        "image_is_valid": validation_status
    }

    assert module.has_validated_local_image(article) is False


def test_has_validated_local_image_returns_true_for_valid_image() -> None:
    article = {
        "image_path": " images/article.jpg ",
        "image_is_valid": True
    }

    assert module.has_validated_local_image(article) is True


def test_has_validated_local_image_accepts_custom_mapping() -> None:
    article: Mapping[str, Any] = UserDict(
        {
            "image_path": "images/article.jpg",
            "image_is_valid": True
        }
    )

    assert module.has_validated_local_image(article) is True


# Format et politique

@pytest.mark.parametrize(
    "article",
    [
        None,
        "article",
        123,
        [],
        ()
    ]
)
def test_validate_article_with_reason_rejects_non_mapping(
    article: Any
) -> None:
    assert module.validate_article_with_reason(article) == (
        False,
        "format_article_invalide"
    )


def test_validate_article_with_reason_builds_policy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    supplied_policy = {
        "require_image": False
    }
    built_policy = build_policy()
    captured: list[Any] = []

    def fake_build_validation_policy(
        policy: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        captured.append(policy)
        return built_policy

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        fake_build_validation_policy
    )

    result = module.validate_article_with_reason(
        article,
        supplied_policy
    )

    assert result == (True, "")
    assert captured == [supplied_policy]


def test_validate_article_with_reason_uses_already_built_policy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    policy = build_policy()

    def fail_if_called(
        supplied_policy: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        pytest.fail("build_validation_policy ne doit pas être appelée.")

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        fail_if_called
    )

    result = module.validate_article_with_reason(
        article,
        policy,
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_copies_built_policy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    policy = build_policy()
    received_policies: list[Mapping[str, Any]] = []

    def fake_has_validated_local_image(
        supplied_article: Mapping[str, Any]
    ) -> bool:
        return True

    monkeypatch.setattr(
        module,
        "has_validated_local_image",
        fake_has_validated_local_image
    )

    result = module.validate_article_with_reason(
        article,
        policy,
        policy_is_built=True
    )

    assert result == (True, "")
    assert policy == build_policy()
    assert received_policies == []


def test_validate_article_with_reason_builds_policy_when_flag_is_true_but_policy_invalid(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    captured: list[Any] = []

    def fake_build_validation_policy(value: Any) -> dict[str, Any]:
        captured.append(value)
        return build_policy()

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        fake_build_validation_policy
    )

    result = module.validate_article_with_reason(
        article,
        "invalid",  # type: ignore[arg-type]
        policy_is_built=True
    )

    assert result == (True, "")
    assert captured == ["invalid"]


# Schéma et champs obligatoires

def test_validate_article_with_reason_rejects_incomplete_schema() -> None:
    article = build_article()
    article.pop("metadata")

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (False, "schema_incomplet")


@pytest.mark.parametrize(
    "identifier",
    [
        None,
        "",
        "   "
    ]
)
def test_validate_article_with_reason_rejects_missing_identifier(
    identifier: Any
) -> None:
    article = build_article(id=identifier)

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (False, "identifiant_absent")


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        "   "
    ]
)
def test_validate_article_with_reason_rejects_missing_source(
    source: Any
) -> None:
    article = build_article(source=source)

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (False, "source_absente")


# Contenus supprimés

@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("title", "titre_supprime"),
        ("text", "texte_supprime"),
        ("author", "auteur_supprime")
    ]
)
def test_validate_article_with_reason_rejects_deleted_content(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    reason: str
) -> None:
    article = build_article(**{field: "[deleted]"})

    monkeypatch.setattr(
        module,
        "is_deleted_value",
        lambda value: value == "[deleted]"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(remove_deleted_content=True),
        policy_is_built=True
    )

    assert result == (False, reason)


def test_validate_article_with_reason_ignores_deleted_content_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(author="[deleted]")

    monkeypatch.setattr(
        module,
        "is_deleted_value",
        lambda value: value == "[deleted]"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(remove_deleted_content=False),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_does_not_check_empty_deleted_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(author="")
    checked_values: list[str] = []

    def fake_is_deleted_value(value: str) -> bool:
        checked_values.append(value)
        return False

    monkeypatch.setattr(
        module,
        "is_deleted_value",
        fake_is_deleted_value
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (True, "")
    assert "" not in checked_values


# Titre

@pytest.mark.parametrize(
    "title",
    [
        None,
        "",
        "   "
    ]
)
def test_validate_article_with_reason_rejects_missing_required_title(
    title: Any
) -> None:
    article = build_article(title=title)

    result = module.validate_article_with_reason(
        article,
        build_policy(require_title=True),
        policy_is_built=True
    )

    assert result == (False, "titre_absent")


def test_validate_article_with_reason_accepts_missing_optional_title() -> None:
    article = build_article(title="")

    result = module.validate_article_with_reason(
        article,
        build_policy(require_title=False),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_rejects_short_title() -> None:
    article = build_article(title="Court")

    result = module.validate_article_with_reason(
        article,
        build_policy(min_title_length=10),
        policy_is_built=True
    )

    assert result == (False, "titre_trop_court")


def test_validate_article_with_reason_accepts_title_at_minimum_length() -> None:
    article = build_article(title="1234567890")

    result = module.validate_article_with_reason(
        article,
        build_policy(min_title_length=10),
        policy_is_built=True
    )

    assert result == (True, "")


# Texte

@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   "
    ]
)
def test_validate_article_with_reason_rejects_missing_required_text(
    text: Any
) -> None:
    article = build_article(text=text)

    result = module.validate_article_with_reason(
        article,
        build_policy(require_text=True),
        policy_is_built=True
    )

    assert result == (False, "texte_absent")


def test_validate_article_with_reason_accepts_missing_optional_text() -> None:
    article = build_article(text="")

    result = module.validate_article_with_reason(
        article,
        build_policy(require_text=False),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_rejects_short_text() -> None:
    article = build_article(text="Court")

    result = module.validate_article_with_reason(
        article,
        build_policy(
            require_text=True,
            min_text_length=10,
            min_total_text_length=0
        ),
        policy_is_built=True
    )

    assert result == (False, "texte_trop_court")


def test_validate_article_with_reason_accepts_text_at_minimum_length() -> None:
    article = build_article(
        title="Titre suffisamment long",
        text="1234567890"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(
            require_text=True,
            min_text_length=10,
            min_total_text_length=0
        ),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_rejects_short_total_text() -> None:
    article = build_article(
        title="1234567890",
        text="abcdefghij"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(
            require_text=True,
            min_title_length=10,
            min_text_length=10,
            min_total_text_length=25
        ),
        policy_is_built=True
    )

    assert result == (False, "contenu_textuel_trop_court")


def test_validate_article_with_reason_accepts_total_text_at_minimum_length() -> None:
    article = build_article(
        title="1234567890",
        text="abcdefghijklmno"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(
            require_text=True,
            min_title_length=10,
            min_text_length=10,
            min_total_text_length=25
        ),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_uses_get_article_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(text="")
    captured: list[Mapping[str, Any]] = []

    def fake_get_article_text(
        supplied_article: Mapping[str, Any]
    ) -> str:
        captured.append(supplied_article)
        return "Texte fourni par l'utilitaire."

    monkeypatch.setattr(
        module,
        "get_article_text",
        fake_get_article_text
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(
            require_text=True,
            min_text_length=10,
            min_total_text_length=20
        ),
        policy_is_built=True
    )

    assert result == (True, "")
    assert captured == [article]


# URL

def test_validate_article_with_reason_rejects_invalid_article_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(url="invalid-url")

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        lambda value: False
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(validate_urls=True),
        policy_is_built=True
    )

    assert result == (False, "url_invalide")


def test_validate_article_with_reason_rejects_invalid_image_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(
        url="https://example.com/article",
        image_url="invalid-image-url"
    )

    def fake_is_valid_http_url(value: Any) -> bool:
        return value == "https://example.com/article"

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fake_is_valid_http_url
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(validate_urls=True),
        policy_is_built=True
    )

    assert result == (False, "url_image_invalide")


def test_validate_article_with_reason_accepts_empty_urls(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(
        url="",
        image_url=""
    )

    def fail_if_called(value: Any) -> bool:
        pytest.fail("is_valid_http_url ne doit pas être appelée.")

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fail_if_called
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(validate_urls=True),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_skips_url_validation_when_disabled(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(
        url="invalid-url",
        image_url="invalid-image-url"
    )

    def fail_if_called(value: Any) -> bool:
        pytest.fail("is_valid_http_url ne doit pas être appelée.")

    monkeypatch.setattr(
        module,
        "is_valid_http_url",
        fail_if_called
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(validate_urls=False),
        policy_is_built=True
    )

    assert result == (True, "")


# Image

def test_validate_article_with_reason_rejects_missing_required_image(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()

    monkeypatch.setattr(
        module,
        "has_validated_local_image",
        lambda supplied_article: False
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(require_image=True),
        policy_is_built=True
    )

    assert result == (False, "image_absente_ou_invalide")


def test_validate_article_with_reason_accepts_valid_required_image(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()

    monkeypatch.setattr(
        module,
        "has_validated_local_image",
        lambda supplied_article: True
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(require_image=True),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_skips_image_check_when_optional(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(
        image_path="",
        image_is_valid=False
    )

    def fail_if_called(
        supplied_article: Mapping[str, Any]
    ) -> bool:
        pytest.fail("has_validated_local_image ne doit pas être appelée.")

    monkeypatch.setattr(
        module,
        "has_validated_local_image",
        fail_if_called
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(require_image=False),
        policy_is_built=True
    )

    assert result == (True, "")


# Label

def test_validate_article_with_reason_rejects_missing_required_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(label=None)

    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: ""
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(require_label=True),
        policy_is_built=True
    )

    assert result == (False, "label_absent")


def test_validate_article_with_reason_accepts_missing_optional_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(label=None)

    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: ""
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(require_label=False),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_rejects_disallowed_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(label="real")

    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: "real"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(allowed_labels={"fake"}),
        policy_is_built=True
    )

    assert result == (False, "label_non_autorise")


def test_validate_article_with_reason_logs_disallowed_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(
        source="example",
        label="real"
    )
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: "real"
    )
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda *args: logged_messages.append(args)
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(allowed_labels={"fake"}),
        policy_is_built=True
    )

    assert result == (False, "label_non_autorise")
    assert logged_messages == [
        (
            "Label refusé | source=%s | label=%r | autorisés=%s",
            "example",
            "real",
            {"fake"}
        )
    ]


def test_validate_article_with_reason_accepts_allowed_label(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(label="fake")

    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: "fake"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(allowed_labels={"fake", "real"}),
        policy_is_built=True
    )

    assert result == (True, "")


def test_validate_article_with_reason_accepts_any_label_when_allowed_set_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article(label="custom")

    monkeypatch.setattr(
        module,
        "normalize_label",
        lambda value: "custom"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(allowed_labels=set()),
        policy_is_built=True
    )

    assert result == (True, "")


# Ordre des validations

def test_validate_article_with_reason_prioritizes_schema_error() -> None:
    article = {
        "id": "",
        "source": ""
    }

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (False, "schema_incomplet")


def test_validate_article_with_reason_prioritizes_identifier_before_source() -> None:
    article = build_article(
        id="",
        source=""
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (False, "identifiant_absent")


def test_validate_article_with_reason_prioritizes_title_before_url() -> None:
    article = build_article(
        title="",
        url="invalid-url"
    )

    result = module.validate_article_with_reason(
        article,
        build_policy(),
        policy_is_built=True
    )

    assert result == (False, "titre_absent")


# Validation d'une collection

def test_validate_articles_raises_for_non_list() -> None:
    with pytest.raises(
        TypeError,
        match="articles doit être une liste"
    ):
        module.validate_articles(None)  # type: ignore[arg-type]


def test_validate_articles_returns_empty_for_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: logged_messages.append(args)
    )

    result = module.validate_articles([])

    assert result == []
    assert logged_messages == [
        (
            "Aucun article à valider.",
        )
    ]


def test_validate_articles_builds_policy_once(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(id="article-001"),
        build_article(id="article-002")
    ]
    supplied_policy = {
        "require_image": False
    }
    built_policy = build_policy()
    build_calls: list[Any] = []
    validation_calls: list[tuple[Any, Any, bool]] = []

    def fake_build_validation_policy(
        policy: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        build_calls.append(policy)
        return built_policy

    def fake_validate_article_with_reason(
        article: Mapping[str, Any],
        policy: Mapping[str, Any] | None,
        *,
        policy_is_built: bool = False
    ) -> tuple[bool, str]:
        validation_calls.append(
            (
                article,
                policy,
                policy_is_built
            )
        )
        return True, ""

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        fake_build_validation_policy
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        fake_validate_article_with_reason
    )

    result = module.validate_articles(
        articles,
        supplied_policy
    )

    assert len(result) == 2
    assert build_calls == [supplied_policy]
    assert validation_calls == [
        (
            articles[0],
            built_policy,
            True
        ),
        (
            articles[1],
            built_policy,
            True
        )
    ]


@pytest.mark.parametrize(
    ("require_image", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False)
    ]
)
def test_validate_articles_overrides_require_image(
    monkeypatch: pytest.MonkeyPatch,
    require_image: bool | int,
    expected: bool
) -> None:
    article = build_article()
    captured_policies: list[Mapping[str, Any]] = []

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy(require_image=not expected)
    )

    def fake_validate_article_with_reason(
        supplied_article: Mapping[str, Any],
        policy: Mapping[str, Any] | None,
        *,
        policy_is_built: bool = False
    ) -> tuple[bool, str]:
        assert policy is not None
        captured_policies.append(policy)
        return True, ""

    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        fake_validate_article_with_reason
    )

    result = module.validate_articles(
        [article],
        require_image=require_image
    )

    assert len(result) == 1
    assert captured_policies[0]["require_image"] is expected


def test_validate_articles_keeps_valid_articles_as_copies(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: (True, "")
    )

    result = module.validate_articles([article])

    assert result == [article]
    assert result[0] is not article


def test_validate_articles_filters_invalid_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(id="article-001"),
        build_article(id="article-002"),
        build_article(id="article-003")
    ]

    responses = iter(
        [
            (True, ""),
            (False, "titre_absent"),
            (True, "")
        ]
    )

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: next(responses)
    )

    result = module.validate_articles(articles)

    assert result == [
        articles[0],
        articles[2]
    ]


def test_validate_articles_uses_unknown_reason_fallback(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: (False, "")
    )
    monkeypatch.setattr(
        module,
        "get_article_identifier",
        lambda supplied_article: "article-001"
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: logged_messages.append(args)
    )

    result = module.validate_articles([article])

    assert result == []
    assert logged_messages == [
        (
            "Article ignoré [%s] : %s.",
            "article-001",
            "raison_inconnue"
        )
    ]


def test_validate_articles_logs_each_rejected_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(id="article-001"),
        build_article(id="article-002")
    ]
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: (
            False,
            "titre_absent"
        )
    )
    monkeypatch.setattr(
        module,
        "get_article_identifier",
        lambda article: article["id"]
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: logged_messages.append(args)
    )

    result = module.validate_articles(articles)

    assert result == []
    assert logged_messages == [
        (
            "Article ignoré [%s] : %s.",
            "article-001",
            "titre_absent"
        ),
        (
            "Article ignoré [%s] : %s.",
            "article-002",
            "titre_absent"
        )
    ]


def test_validate_articles_logs_summary_and_rejection_counts(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(id="article-001"),
        build_article(id="article-002"),
        build_article(id="article-003")
    ]
    responses = iter(
        [
            (True, ""),
            (False, "titre_absent"),
            (False, "titre_absent")
        ]
    )
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: next(responses)
    )
    monkeypatch.setattr(
        module,
        "get_article_identifier",
        lambda article: article["id"]
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: logged_messages.append(args)
    )

    result = module.validate_articles(articles)

    assert result == [articles[0]]
    assert logged_messages == [
        (
            "%s article(s) valide(s) sur %s.",
            1,
            3
        ),
        (
            "Motifs de rejet : %s.",
            {
                "titre_absent": 2
            }
        )
    ]


def test_validate_articles_does_not_log_rejection_summary_when_all_valid(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = build_article()
    logged_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: (True, "")
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: logged_messages.append(args)
    )

    result = module.validate_articles([article])

    assert result == [article]
    assert logged_messages == [
        (
            "%s article(s) valide(s) sur %s.",
            1,
            1
        )
    ]


def test_validate_articles_accepts_custom_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article: Mapping[str, Any] = UserDict(
        build_article()
    )

    monkeypatch.setattr(
        module,
        "build_validation_policy",
        lambda policy: build_policy()
    )
    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy, policy_is_built=False: (True, "")
    )

    result = module.validate_articles([article])

    assert result == [dict(article)]
    assert isinstance(result[0], dict)