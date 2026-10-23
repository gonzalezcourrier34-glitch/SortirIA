"""Tests du nettoyage des champs textuels des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/article/processing/test_article_cleaner.py` (commit 93632c5).
"""

from __future__ import annotations

# Fixtures
from collections.abc import Generator, Mapping
from typing import Any

import pytest

import src.article.processing.article_cleaner as module


@pytest.fixture(autouse=True)
def clear_clean_text_cache() -> Generator[None, None, None]:
    """Vide le cache avant et après chaque test."""

    module._clean_cached_text.cache_clear()
    yield
    module._clean_cached_text.cache_clear()


@pytest.fixture
def complete_article() -> dict[str, Any]:
    """Retourne un article contenant plusieurs champs textuels."""

    return {
        "id": "article-1",
        "source": "Source test",
        "title": "  Titre   de test  ",
        "text": "<p>Contenu&nbsp;principal</p>",
        "author": "  Jean   Dupont ",
        "category": " actualité ",
        "language": " fr ",
        "url": "https://example.com/article",
        "published_at": "2026-07-28T12:00:00Z",
        "metadata": {
            "score": 0.9
        }
    }


# Normalisation Unicode


def test_normalize_unicode_returns_empty_string_for_empty_value() -> None:
    assert module.normalize_unicode("") == ""


def test_normalize_unicode_normalizes_compatible_characters() -> None:
    assert module.normalize_unicode("ＡＢＣ ①") == "ABC 1"


def test_normalize_unicode_preserves_regular_text() -> None:
    assert module.normalize_unicode("Texte français") == "Texte français"


def test_normalize_unicode_returns_original_text_on_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeUnicodeData:
        """Simule une erreur pendant la normalisation Unicode."""

        @staticmethod
        def normalize(form: str, text: str) -> str:
            raise ValueError("Erreur Unicode simulée")

    monkeypatch.setattr(module, "unicodedata", FakeUnicodeData())

    assert module.normalize_unicode("Texte") == "Texte"


# Caractères invisibles


def test_remove_invisible_characters_returns_empty_string_for_empty_text() -> None:
    assert module.remove_invisible_characters("") == ""


def test_remove_invisible_characters_removes_zero_width_characters() -> None:
    text = "Bon\u200bjour\u2060"

    assert module.remove_invisible_characters(text) == "Bonjour"


def test_remove_invisible_characters_removes_soft_hyphen() -> None:
    assert module.remove_invisible_characters("infor\u00admation") == "information"


def test_remove_invisible_characters_replaces_control_characters_with_spaces() -> None:
    assert module.remove_invisible_characters("Bonjour\x00monde") == "Bonjour monde"


def test_remove_invisible_characters_preserves_newlines_and_tabs() -> None:
    text = "Bonjour\nle\tmonde"

    assert module.remove_invisible_characters(text) == text


# Nettoyage interne


def test_clean_text_content_decodes_html_entities() -> None:
    assert module._clean_text_content("Tom &amp; Jerry") == "Tom & Jerry"


def test_clean_text_content_removes_html() -> None:
    text = "<article><h1>Titre</h1><p>Contenu</p></article>"

    assert module._clean_text_content(text) == "Titre Contenu"


def test_clean_text_content_decodes_entities_after_html_removal(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "remove_html",
        lambda value: value.replace("<p>", "").replace("</p>", "")
    )

    result = module._clean_text_content("<p>&amp;lt;texte&amp;gt;</p>")

    assert result == "<texte>"


def test_clean_text_content_normalizes_unicode() -> None:
    assert module._clean_text_content("Ｔｅｘｔｅ") == "Texte"


def test_clean_text_content_removes_invisible_characters() -> None:
    assert module._clean_text_content("Bon\u200bjour") == "Bonjour"


def test_clean_text_content_collapses_whitespace() -> None:
    text = "  Bonjour \n\t le    monde  "

    assert module._clean_text_content(text) == "Bonjour le monde"


@pytest.mark.parametrize(
    "exception",
    [
        TypeError("Erreur simulée"),
        ValueError("Erreur simulée"),
        UnicodeError("Erreur simulée"),
        RuntimeError("Erreur simulée")
    ]
)
def test_clean_text_content_uses_fallback_on_expected_error(
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception
) -> None:
    def raise_error(text: str) -> str:
        raise exception

    monkeypatch.setattr(module, "remove_html", raise_error)

    assert module._clean_text_content("  Texte   brut  ") == "Texte brut"


def test_clean_text_content_logs_debug_on_expected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[str, Exception]] = []

    error = RuntimeError("Erreur simulée")

    def raise_error(text: str) -> str:
        raise error

    monkeypatch.setattr(module, "remove_html", raise_error)
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda message, exception: logged_messages.append((message, exception))
    )

    module._clean_text_content("Texte")

    assert logged_messages == [
        ("Texte impossible à nettoyer : %s", error)
    ]


# Cache


def test_clean_cached_text_returns_cleaned_text() -> None:
    assert module._clean_cached_text("<p>Texte</p>") == "Texte"


def test_clean_cached_text_reuses_cached_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_clean_text_content(text: str) -> str:
        calls.append(text)
        return f"nettoyé:{text}"

    monkeypatch.setattr(module, "_clean_text_content", fake_clean_text_content)

    first_result = module._clean_cached_text("Texte")
    second_result = module._clean_cached_text("Texte")

    assert first_result == "nettoyé:Texte"
    assert second_result == "nettoyé:Texte"
    assert calls == ["Texte"]


def test_clean_cached_text_cache_has_expected_maximum_size() -> None:
    cache_parameters = module._clean_cached_text.cache_parameters()

    assert cache_parameters["maxsize"] == module.CLEAN_TEXT_CACHE_SIZE
    assert cache_parameters["typed"] is False


# Nettoyage d'une valeur


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Texte", "Texte"),
        ("  Texte   nettoyé ", "Texte nettoyé"),
        ("<p>Texte</p>", "Texte"),
        ("Tom &amp; Jerry", "Tom & Jerry"),
        ("Bon\u200bjour", "Bonjour"),
        (123, "123"),
        (True, "True")
    ]
)
def test_clean_text_returns_expected_value(
    value: Any,
    expected: str
) -> None:
    assert module.clean_text(value) == expected


def test_clean_text_uses_cache_for_short_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_clean_cached_text(text: str) -> str:
        calls.append(text)
        return "texte en cache"

    monkeypatch.setattr(module, "_clean_cached_text", fake_clean_cached_text)

    result = module.clean_text("Texte court")

    assert result == "texte en cache"
    assert calls == ["Texte court"]


def test_clean_text_uses_cache_at_maximum_cached_length(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "a" * module.MAX_CACHED_TEXT_LENGTH

    monkeypatch.setattr(
        module,
        "_clean_cached_text",
        lambda value: "résultat en cache"
    )

    assert module.clean_text(text) == "résultat en cache"


def test_clean_text_bypasses_cache_for_long_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "a" * (module.MAX_CACHED_TEXT_LENGTH + 1)
    cached_calls: list[str] = []
    direct_calls: list[str] = []

    def fake_cached_text(value: str) -> str:
        cached_calls.append(value)
        return "cache"

    def fake_clean_text_content(value: str) -> str:
        direct_calls.append(value)
        return "nettoyage direct"

    monkeypatch.setattr(module, "_clean_cached_text", fake_cached_text)
    monkeypatch.setattr(module, "_clean_text_content", fake_clean_text_content)

    result = module.clean_text(text)

    assert result == "nettoyage direct"
    assert cached_calls == []
    assert direct_calls == [text]


def test_clean_text_does_not_process_empty_normalized_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "normalize_value", lambda value: "")
    monkeypatch.setattr(
        module,
        "_clean_cached_text",
        lambda value: pytest.fail("Le cache ne doit pas être appelé.")
    )
    monkeypatch.setattr(
        module,
        "_clean_text_content",
        lambda value: pytest.fail(
            "Le nettoyage direct ne doit pas être appelé."
        )
    )

    assert module.clean_text(object()) == ""


# Nettoyage d'un article


def test_clean_article_rejects_non_mapping_value() -> None:
    with pytest.raises(
        TypeError,
        match="article doit être une structure de type Mapping"
    ):
        module.clean_article(["article"])


def test_clean_article_returns_new_dictionary(
    complete_article: dict[str, Any]
) -> None:
    result = module.clean_article(complete_article)

    expected = dict(complete_article)

    for field in module.TEXT_FIELDS_TO_CLEAN:
        if field in expected:
            expected[field] = module.clean_text(expected[field])

    assert result is not complete_article
    assert result == expected
    

def test_clean_article_does_not_modify_original_article(
    complete_article: dict[str, Any]
) -> None:
    original_article = dict(complete_article)

    module.clean_article(complete_article)

    assert complete_article == original_article


def test_clean_article_only_cleans_configured_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TEXT_FIELDS_TO_CLEAN",
        ("title", "text")
    )

    article = {
        "title": "  Titre  ",
        "text": "  Texte  ",
        "source": "  Source non nettoyée  "
    }

    result = module.clean_article(article)

    assert result == {
        "title": "Titre",
        "text": "Texte",
        "source": "  Source non nettoyée  "
    }


def test_clean_article_ignores_missing_text_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TEXT_FIELDS_TO_CLEAN",
        ("title", "text", "author")
    )

    article = {
        "title": "  Titre  ",
        "id": "article-1"
    }

    result = module.clean_article(article)

    assert result == {
        "title": "Titre",
        "id": "article-1"
    }


def test_clean_article_calls_clean_text_for_each_existing_field(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TEXT_FIELDS_TO_CLEAN",
        ("title", "text", "author")
    )

    calls: list[Any] = []

    def fake_clean_text(value: Any) -> str:
        calls.append(value)
        return f"cleaned:{value}"

    monkeypatch.setattr(module, "clean_text", fake_clean_text)

    result = module.clean_article({
        "title": "Titre",
        "text": "Texte",
        "id": "article-1"
    })

    assert result == {
        "title": "cleaned:Titre",
        "text": "cleaned:Texte",
        "id": "article-1"
    }
    assert calls == ["Titre", "Texte"]


class CustomMapping(Mapping[str, Any]):
    """Mapping minimal utilisé pour vérifier le contrat d'entrée."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def test_clean_article_accepts_custom_mapping() -> None:
    article = CustomMapping({
        "title": "  Titre  ",
        "id": "article-1"
    })

    result = module.clean_article(article)

    assert result["title"] == "Titre"
    assert result["id"] == "article-1"


# Nettoyage d'une collection


def test_clean_articles_rejects_non_list_collection() -> None:
    with pytest.raises(
        TypeError,
        match="articles doit être une liste"
    ):
        module.clean_articles(("article",))


def test_clean_articles_returns_empty_list_for_empty_collection(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[str] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda message, *args: logged_messages.append(message)
    )

    result = module.clean_articles([])

    assert result == []
    assert logged_messages == ["Aucun article à nettoyer."]


@pytest.mark.parametrize(
    ("articles", "expected_indexes"),
    [
        ([{"title": "Valide"}, None], [1]),
        ([None, {"title": "Valide"}], [0]),
        ([{}, "invalide", 42, None], [1, 2, 3])
    ]
)
def test_clean_articles_rejects_invalid_articles(
    articles: list[Any],
    expected_indexes: list[int]
) -> None:
    with pytest.raises(TypeError) as error:
        module.clean_articles(articles)

    assert str(expected_indexes) in str(error.value)


def test_clean_articles_limits_reported_invalid_indexes() -> None:
    articles: list[Any] = [None] * 15

    with pytest.raises(TypeError) as error:
        module.clean_articles(articles)

    assert str(list(range(10))) in str(error.value)
    assert "10, 11, 12, 13, 14" not in str(error.value)


def test_clean_articles_cleans_all_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {"id": "article-1", "title": "Titre 1"},
        {"id": "article-2", "title": "Titre 2"}
    ]

    calls: list[Mapping[str, Any]] = []

    def fake_clean_article(article: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(article)
        return {
            **article,
            "cleaned": True
        }

    monkeypatch.setattr(module, "clean_article", fake_clean_article)

    result = module.clean_articles(articles)

    assert result == [
        {
            "id": "article-1",
            "title": "Titre 1",
            "cleaned": True
        },
        {
            "id": "article-2",
            "title": "Titre 2",
            "cleaned": True
        }
    ]
    assert calls == articles


def test_clean_articles_preserves_article_order() -> None:
    articles = [
        {
            "id": "article-1",
            "title": "  Premier  "
        },
        {
            "id": "article-2",
            "title": "  Deuxième  "
        }
    ]

    result = module.clean_articles(articles)

    assert [article["id"] for article in result] == [
        "article-1",
        "article-2"
    ]


def test_clean_articles_logs_cleaning_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_messages: list[tuple[str, int, int]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda message, cleaned, total: logged_messages.append(
            (message, cleaned, total)
        )
    )

    module.clean_articles([
        {"title": "Article 1"},
        {"title": "Article 2"}
    ])

    assert logged_messages == [
        (
            "%s article(s) nettoyé(s) sur %s élément(s).",
            2,
            2
        )
    ]


def test_clean_articles_does_not_modify_original_articles() -> None:
    articles = [
        {
            "title": "  Article 1  ",
            "text": "<p>Texte 1</p>"
        },
        {
            "title": "  Article 2  ",
            "text": "<p>Texte 2</p>"
        }
    ]
    original_articles = [
        dict(article)
        for article in articles
    ]

    result = module.clean_articles(articles)

    assert articles == original_articles
    assert result is not articles
    assert result[0] is not articles[0]
    assert result[1] is not articles[1]