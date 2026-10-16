"""Tests de la déduplication des articles CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/processing/test_article_deduplicator.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

import src.article.processing.article_deduplicator as module


# Fixtures


@pytest.fixture
def article() -> dict[str, Any]:
    """Retourne un article normalisé complet."""

    return {
        "id": "article-1",
        "source": "Source Test",
        "title": "Titre de l'article",
        "url": "https://example.com/article",
        "published_at": "2026-07-28T12:00:00Z",
        "dataset_role": "acquisition",
        "text": "Contenu initial"
    }


@pytest.fixture
def basic_article() -> dict[str, Any]:
    """Retourne un article sans identifiant ni URL."""

    return {
        "source": "Source Test",
        "title": "Titre de l'article",
        "published_at": "2026-07-28T12:00:00Z",
        "dataset_role": "acquisition"
    }


# Mapping personnalisé


class CustomMapping(Mapping[str, Any]):
    """Mapping minimal utilisé pour vérifier le contrat d'entrée."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


# Construction des clés


def test_get_article_duplicate_keys_rejects_non_mapping_value() -> None:
    assert module.get_article_duplicate_keys(["article"]) == set()


def test_get_article_duplicate_keys_returns_empty_set_for_empty_article() -> None:
    assert module.get_article_duplicate_keys({}) == set()


def test_get_article_duplicate_keys_builds_id_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(module, "get_article_source", lambda value: "")

    result = module.get_article_duplicate_keys({
        "id": " article-1 "
    })

    assert result == {
        "id:article-1"
    }


def test_get_article_duplicate_keys_prefixes_id_with_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_role",
        lambda value: "partenaire"
    )
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(module, "get_article_source", lambda value: "")

    result = module.get_article_duplicate_keys({
        "id": "article-1"
    })

    assert result == {
        "partenaire:id:article-1"
    }


def test_get_article_duplicate_keys_builds_canonical_url_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(
        module,
        "get_article_url",
        lambda value: "HTTPS://EXAMPLE.COM/Article?utm_source=test"
    )
    monkeypatch.setattr(module, "get_article_source", lambda value: "")
    monkeypatch.setattr(
        module,
        "canonicalize_url",
        lambda value: "https://example.com/Article"
    )

    result = module.get_article_duplicate_keys({})

    assert result == {
        "url:https://example.com/article"
    }


def test_get_article_duplicate_keys_builds_title_hash(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_role",
        lambda value: "acquisition"
    )
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(
        module,
        "get_article_source",
        lambda value: "Source Test"
    )

    article = {
        "title": "  Titre   de test  ",
        "published_at": "2026-07-28T12:00:00Z"
    }

    title_identifier = (
        "acquisition|source test|titre de test|2026-07-28"
    )
    expected_hash = hashlib.md5(
        title_identifier.encode("utf-8"),
        usedforsecurity=False
    ).hexdigest()

    result = module.get_article_duplicate_keys(article)

    assert result == {
        f"acquisition:title:{expected_hash}"
    }


def test_get_article_duplicate_keys_builds_all_available_keys(
    article: dict[str, Any]
) -> None:
    result = module.get_article_duplicate_keys(article)

    assert len(result) == 3
    assert "acquisition:id:article-1" in result
    assert "acquisition:url:https://example.com/article" in result

    title_keys = [
        key
        for key in result
        if key.startswith("acquisition:title:")
    ]

    assert len(title_keys) == 1


def test_get_article_duplicate_keys_normalizes_source_and_title(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(
        module,
        "get_article_source",
        lambda value: "SOURCE TEST"
    )

    first_keys = module.get_article_duplicate_keys({
        "title": "Titre   de Test",
        "published_at": "2026-07-28T10:00:00Z"
    })
    second_keys = module.get_article_duplicate_keys({
        "title": "  titre de test ",
        "published_at": "2026-07-28T18:00:00Z"
    })

    assert first_keys == second_keys


def test_get_article_duplicate_keys_uses_only_published_day(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(
        module,
        "get_article_source",
        lambda value: "Source"
    )

    morning_keys = module.get_article_duplicate_keys({
        "title": "Titre",
        "published_at": "2026-07-28T08:00:00Z"
    })
    evening_keys = module.get_article_duplicate_keys({
        "title": "Titre",
        "published_at": "2026-07-28T20:00:00Z"
    })

    assert morning_keys == evening_keys


def test_get_article_duplicate_keys_distinguishes_published_days(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(
        module,
        "get_article_source",
        lambda value: "Source"
    )

    first_keys = module.get_article_duplicate_keys({
        "title": "Titre",
        "published_at": "2026-07-27T20:00:00Z"
    })
    second_keys = module.get_article_duplicate_keys({
        "title": "Titre",
        "published_at": "2026-07-28T08:00:00Z"
    })

    assert first_keys != second_keys


def test_get_article_duplicate_keys_distinguishes_roles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_url", lambda value: "")
    monkeypatch.setattr(module, "get_article_source", lambda value: "")

    monkeypatch.setattr(
        module,
        "get_article_role",
        lambda value: value["dataset_role"]
    )

    acquisition_keys = module.get_article_duplicate_keys({
        "id": "article-1",
        "dataset_role": "acquisition"
    })
    reference_keys = module.get_article_duplicate_keys({
        "id": "article-1",
        "dataset_role": "partenaire"
    })

    assert acquisition_keys != reference_keys
    assert acquisition_keys == {
        "acquisition:id:article-1"
    }
    assert reference_keys == {
        "partenaire:id:article-1"
    }


def test_get_article_duplicate_keys_accepts_custom_mapping() -> None:
    article = CustomMapping({
        "id": "article-1"
    })

    result = module.get_article_duplicate_keys(article)

    assert "id:article-1" in result


def test_get_article_duplicate_keys_ignores_blank_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(module, "get_article_url", lambda value: "   ")
    monkeypatch.setattr(module, "get_article_source", lambda value: "   ")
    monkeypatch.setattr(module, "canonicalize_url", lambda value: "")

    result = module.get_article_duplicate_keys({
        "id": "   ",
        "title": "   ",
        "published_at": "   "
    })

    assert result == set()


def test_get_article_duplicate_keys_calls_canonicalize_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(module, "get_article_role", lambda value: "")
    monkeypatch.setattr(
        module,
        "get_article_url",
        lambda value: "https://example.com/article"
    )
    monkeypatch.setattr(module, "get_article_source", lambda value: "")

    def fake_canonicalize_url(value: str) -> str:
        calls.append(value)
        return value

    monkeypatch.setattr(
        module,
        "canonicalize_url",
        fake_canonicalize_url
    )

    module.get_article_duplicate_keys({})

    assert calls == [
        "https://example.com/article"
    ]


# Détection des doublons


def test_is_duplicate_article_rejects_non_mapping_article() -> None:
    with pytest.raises(
        TypeError,
        match="article doit être une structure de type Mapping"
    ):
        module.is_duplicate_article(["article"], set())


def test_is_duplicate_article_rejects_non_set_seen_keys() -> None:
    with pytest.raises(
        TypeError,
        match="seen_keys doit être un ensemble de chaînes"
    ):
        module.is_duplicate_article({}, [])


def test_is_duplicate_article_returns_false_without_seen_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: {"id:article-1"}
    )

    result = module.is_duplicate_article(
        {"id": "article-1"},
        set()
    )

    assert result is False


def test_is_duplicate_article_returns_true_for_matching_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: {
            "id:article-1",
            "url:https://example.com/article"
        }
    )

    result = module.is_duplicate_article(
        {"id": "article-1"},
        {
            "url:https://example.com/article"
        }
    )

    assert result is True


def test_is_duplicate_article_returns_false_for_different_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: {
            "id:article-1"
        }
    )

    result = module.is_duplicate_article(
        {"id": "article-1"},
        {
            "id:article-2"
        }
    )

    assert result is False


def test_is_duplicate_article_accepts_custom_mapping() -> None:
    article = CustomMapping({
        "id": "article-1"
    })

    assert module.is_duplicate_article(
        article,
        {"id:article-1"}
    ) is True


# Enregistrement des clés


def test_register_article_rejects_non_mapping_article() -> None:
    with pytest.raises(
        TypeError,
        match="article doit être une structure de type Mapping"
    ):
        module.register_article(["article"], set())


def test_register_article_rejects_non_set_seen_keys() -> None:
    with pytest.raises(
        TypeError,
        match="seen_keys doit être un ensemble de chaînes"
    ):
        module.register_article({}, [])


def test_register_article_adds_article_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_keys = {
        "id:existing"
    }

    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: {
            "id:article-1",
            "url:https://example.com/article"
        }
    )

    result = module.register_article(
        {"id": "article-1"},
        seen_keys
    )

    assert result is None
    assert seen_keys == {
        "id:existing",
        "id:article-1",
        "url:https://example.com/article"
    }


def test_register_article_preserves_existing_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_keys = {
        "id:article-1"
    }

    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: {
            "id:article-1"
        }
    )

    module.register_article(
        {"id": "article-1"},
        seen_keys
    )

    assert seen_keys == {
        "id:article-1"
    }


def test_register_article_does_nothing_without_duplicate_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_keys = {
        "id:existing"
    }

    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: set()
    )

    module.register_article({}, seen_keys)

    assert seen_keys == {
        "id:existing"
    }


def test_register_article_accepts_custom_mapping() -> None:
    article = CustomMapping({
        "id": "article-1"
    })
    seen_keys: set[str] = set()

    module.register_article(article, seen_keys)

    assert "id:article-1" in seen_keys


# Validation de la collection


def test_deduplicate_articles_rejects_non_list_collection() -> None:
    with pytest.raises(
        TypeError,
        match="articles doit être une liste"
    ):
        module.deduplicate_articles(({},))


def test_deduplicate_articles_returns_empty_list_for_empty_collection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[str] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda message, *args: logged_messages.append(message)
    )

    result = module.deduplicate_articles([])

    assert result == []
    assert logged_messages == [
        "Aucun article à dédupliquer."
    ]


@pytest.mark.parametrize(
    ("articles", "expected_indexes"),
    [
        ([{}, None], [1]),
        ([None, {}], [0]),
        ([{}, "article", 42, None], [1, 2, 3])
    ]
)
def test_deduplicate_articles_rejects_invalid_articles(
    articles: list[Any],
    expected_indexes: list[int]
) -> None:
    with pytest.raises(TypeError) as error:
        module.deduplicate_articles(articles)

    assert str(expected_indexes) in str(error.value)


def test_deduplicate_articles_limits_reported_invalid_indexes() -> None:
    articles: list[Any] = [None] * 15

    with pytest.raises(TypeError) as error:
        module.deduplicate_articles(articles)

    assert str(list(range(10))) in str(error.value)
    assert "10, 11, 12, 13, 14" not in str(error.value)


# Déduplication d'une collection


def test_deduplicate_articles_preserves_unique_articles() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Premier article"
        },
        {
            "id": "article-2",
            "title": "Deuxième article"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == articles
    assert result is not articles


def test_deduplicate_articles_returns_new_dictionaries() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Premier article"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result[0] == articles[0]
    assert result[0] is not articles[0]


def test_deduplicate_articles_does_not_modify_original_articles() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Version initiale"
        },
        {
            "id": "article-1",
            "title": "Version récente"
        }
    ]
    original_articles = [
        dict(article)
        for article in articles
    ]

    module.deduplicate_articles(articles)

    assert articles == original_articles


def test_deduplicate_articles_keeps_last_version_by_id() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "Version initiale",
            "text": "Ancien contenu"
        },
        {
            "id": "article-1",
            "title": "Version récente",
            "text": "Nouveau contenu"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-1",
            "title": "Version récente",
            "text": "Nouveau contenu"
        }
    ]


def test_deduplicate_articles_keeps_last_version_by_url() -> None:
    articles = [
        {
            "id": "article-1",
            "url": "https://example.com/article",
            "text": "Ancien contenu"
        },
        {
            "id": "article-2",
            "url": "https://example.com/article",
            "text": "Nouveau contenu"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-2",
            "url": "https://example.com/article",
            "text": "Nouveau contenu"
        }
    ]


def test_deduplicate_articles_keeps_last_version_by_title_key() -> None:
    articles = [
        {
            "source": "Source Test",
            "title": "Titre identique",
            "published_at": "2026-07-28T08:00:00Z",
            "text": "Ancien contenu"
        },
        {
            "source": "source test",
            "title": "  titre   identique ",
            "published_at": "2026-07-28T20:00:00Z",
            "text": "Nouveau contenu"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "source": "source test",
            "title": "  titre   identique ",
            "published_at": "2026-07-28T20:00:00Z",
            "text": "Nouveau contenu"
        }
    ]


def test_deduplicate_articles_preserves_articles_without_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: set()
    )

    articles = [
        {
            "text": "Premier article"
        },
        {
            "text": "Premier article"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == articles
    assert len(result) == 2


def test_deduplicate_articles_preserves_order_of_unique_groups() -> None:
    articles = [
        {
            "id": "article-1",
            "text": "Version initiale 1"
        },
        {
            "id": "article-2",
            "text": "Version initiale 2"
        },
        {
            "id": "article-1",
            "text": "Version récente 1"
        },
        {
            "id": "article-3",
            "text": "Article 3"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-1",
            "text": "Version récente 1"
        },
        {
            "id": "article-2",
            "text": "Version initiale 2"
        },
        {
            "id": "article-3",
            "text": "Article 3"
        }
    ]


def test_deduplicate_articles_merges_transitive_duplicate_groups(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    keys_by_id = {
        "article-a": {
            "key-a"
        },
        "article-b": {
            "key-b"
        },
        "article-bridge": {
            "key-a",
            "key-b"
        }
    }

    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: keys_by_id[value["id"]]
    )

    articles = [
        {
            "id": "article-a",
            "text": "Premier groupe"
        },
        {
            "id": "article-b",
            "text": "Deuxième groupe"
        },
        {
            "id": "article-bridge",
            "text": "Version reliant les deux groupes"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-bridge",
            "text": "Version reliant les deux groupes"
        }
    ]


def test_deduplicate_articles_reuses_merged_keys_for_later_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    keys_by_id = {
        "article-a": {
            "key-a"
        },
        "article-b": {
            "key-b"
        },
        "article-bridge": {
            "key-a",
            "key-b"
        },
        "article-latest": {
            "key-b"
        }
    }

    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        lambda value: keys_by_id[value["id"]]
    )

    articles = [
        {
            "id": "article-a"
        },
        {
            "id": "article-b"
        },
        {
            "id": "article-bridge"
        },
        {
            "id": "article-latest"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-latest"
        }
    ]


def test_deduplicate_articles_preserves_role_separation() -> None:
    articles = [
        {
            "id": "article-1",
            "dataset_role": "acquisition",
            "text": "Article acquis"
        },
        {
            "id": "article-1",
            "dataset_role": "partenaire",
            "text": "Article de référence"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == articles


def test_deduplicate_articles_deduplicates_same_id_without_role() -> None:
    articles = [
        {
            "id": "article-1",
            "text": "Première version"
        },
        {
            "id": "article-1",
            "text": "Deuxième version"
        }
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-1",
            "text": "Deuxième version"
        }
    ]


def test_deduplicate_articles_accepts_custom_mappings() -> None:
    articles: list[Any] = [
        CustomMapping({
            "id": "article-1",
            "text": "Première version"
        }),
        CustomMapping({
            "id": "article-1",
            "text": "Deuxième version"
        })
    ]

    result = module.deduplicate_articles(articles)

    assert result == [
        {
            "id": "article-1",
            "text": "Deuxième version"
        }
    ]
    assert isinstance(result[0], dict)


def test_deduplicate_articles_calls_key_builder_for_each_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get_article_duplicate_keys(
        value: Mapping[str, Any]
    ) -> set[str]:
        article = dict(value)
        calls.append(article)
        return {
            f"id:{article['id']}"
        }

    monkeypatch.setattr(
        module,
        "get_article_duplicate_keys",
        fake_get_article_duplicate_keys
    )

    articles = [
        {
            "id": "article-1"
        },
        {
            "id": "article-2"
        }
    ]

    module.deduplicate_articles(articles)

    assert calls == articles


def test_deduplicate_articles_logs_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[str, int, int]] = []

    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda message, kept, total: logged_messages.append(
            (message, kept, total)
        )
    )

    module.deduplicate_articles([
        {
            "id": "article-1"
        },
        {
            "id": "article-1"
        },
        {
            "id": "article-2"
        }
    ])

    assert logged_messages == [
        (
            "%s article(s) conservé(s) sur %s après déduplication.",
            2,
            3
        )
    ]