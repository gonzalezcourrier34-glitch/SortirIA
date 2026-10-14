"""Tests du nettoyage HTML.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/cleaner/test_html_cleaner.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest
from bs4 import BeautifulSoup, FeatureNotFound
from bs4.builder import ParserRejectedMarkup

from src.cleaner import html_cleaner as module


# Détection

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", False),
        ("Texte simple", False),
        ("2 < 3 et 4 > 1", False),
        ("<p>Texte</p>", True),
        ("<br>", True),
        ("<img src=\"image.jpg\">", True),
        ("<!-- commentaire -->", True),
        ("<!DOCTYPE html>", True),
        ("<DIV>Texte</DIV>", True),
        ("Texte avec <strong>HTML</strong>", True)
    ]
)
def test_contains_html(
    text: str,
    expected: bool
) -> None:
    assert module.contains_html(text) is expected


# Création du parseur

def test_create_html_soup_uses_preferred_parser(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    expected = object()

    def fake_beautiful_soup(
        text: str,
        parser: str
    ) -> Any:
        calls.append((text, parser))
        return expected

    monkeypatch.setattr(
        module,
        "BeautifulSoup",
        fake_beautiful_soup
    )

    result = module.create_html_soup("<p>Texte</p>")

    assert result is expected
    assert calls == [
        (
            "<p>Texte</p>",
            module.PREFERRED_HTML_PARSER
        )
    ]


def test_create_html_soup_falls_back_to_native_parser(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    expected = object()

    def fake_beautiful_soup(
        text: str,
        parser: str
    ) -> Any:
        calls.append((text, parser))

        if parser == module.PREFERRED_HTML_PARSER:
            raise FeatureNotFound("parser unavailable")

        return expected

    monkeypatch.setattr(
        module,
        "BeautifulSoup",
        fake_beautiful_soup
    )

    result = module.create_html_soup("<p>Texte</p>")

    assert result is expected
    assert calls == [
        (
            "<p>Texte</p>",
            module.PREFERRED_HTML_PARSER
        ),
        (
            "<p>Texte</p>",
            module.FALLBACK_HTML_PARSER
        )
    ]


# Nettoyage simple

@pytest.mark.parametrize(
    "text",
    [
        "",
        "Texte simple",
        "2 < 3 et 4 > 1"
    ]
)
def test_remove_html_returns_original_text_without_html(
    text: str
) -> None:
    assert module.remove_html(text) == text


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>Bonjour</p>", "Bonjour"),
        ("<p>Bonjour <strong>tout le monde</strong></p>", "Bonjour tout le monde"),
        ("<div>Première ligne</div><div>Deuxième ligne</div>", "Première ligne Deuxième ligne"),
        ("<p>Texte&nbsp;espacé</p>", "Texte\xa0espacé"),
        ("<h1>Titre</h1><p>Contenu</p>", "Titre Contenu"),
        ("<p>Texte avec <a href=\"#\">un lien</a></p>", "Texte avec un lien")
    ]
)
def test_remove_html_returns_text_content(
    html: str,
    expected: str
) -> None:
    assert module.remove_html(html) == expected


def test_remove_html_removes_non_textual_elements() -> None:
    html = """
        <html>
            <head>
                <style>body { color: red; }</style>
                <script>alert('test')</script>
            </head>
            <body>
                <p>Contenu principal</p>
                <noscript>Navigation alternative</noscript>
                <iframe>Contenu iframe</iframe>
                <svg><text>Texte SVG</text></svg>
                <canvas>Contenu canvas</canvas>
            </body>
        </html>
    """

    assert module.remove_html(html) == "Contenu principal"


def test_remove_html_removes_multiple_non_textual_elements() -> None:
    html = (
        "<p>Avant</p>"
        "<script>script 1</script>"
        "<script>script 2</script>"
        "<style>style</style>"
        "<p>Après</p>"
    )

    assert module.remove_html(html) == "Avant Après"


# Troncature

def test_remove_html_truncates_long_content(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_HTML_LENGTH",
        20
    )

    text = "<p>Texte suffisamment long pour être tronqué</p>"

    result = module.remove_html(text)

    assert result == "Texte suffisammen"


def test_remove_html_logs_warning_when_content_is_truncated(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []
    text = "<p>Texte beaucoup trop long</p>"

    monkeypatch.setattr(
        module,
        "MAX_HTML_LENGTH",
        10
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    module.remove_html(text)

    assert warnings == [
        (
            "Contenu HTML tronqué de %s à %s caractères avant nettoyage.",
            len(text),
            10
        )
    ]

def test_remove_html_does_not_log_warning_at_exact_limit(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []
    text = "<p>Texte</p>"

    monkeypatch.setattr(
        module,
        "MAX_HTML_LENGTH",
        len(text)
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    assert module.remove_html(text) == "Texte"
    assert warnings == []


# Erreurs de parsing

@pytest.mark.parametrize(
    "error",
    [
        ParserRejectedMarkup("invalid"),
        TypeError("invalid"),
        ValueError("invalid"),
        AttributeError("invalid"),
        RecursionError("invalid")
    ]
)
def test_remove_html_returns_truncated_content_on_parsing_error(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "MAX_HTML_LENGTH",
        20
    )
    monkeypatch.setattr(
        module,
        "create_html_soup",
        lambda text: (_ for _ in ()).throw(error)
    )

    text = "<p>Texte HTML trop long pour le parseur</p>"

    assert module.remove_html(text) == text[:20]


def test_remove_html_logs_debug_on_parsing_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ValueError("invalid html")
    debug_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "create_html_soup",
        lambda text: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda *args: debug_calls.append(args)
    )

    html = "<p>Texte</p>"

    assert module.remove_html(html) == html
    assert debug_calls == [
        (
            "Contenu HTML impossible à analyser : %s",
            error
        )
    ]


# Interaction avec BeautifulSoup

def test_remove_html_calls_create_html_soup(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[str] = []
    soup = BeautifulSoup(
        "<p>Texte</p>",
        "html.parser"
    )

    def fake_create_html_soup(text: str) -> BeautifulSoup:
        received.append(text)
        return soup

    monkeypatch.setattr(
        module,
        "create_html_soup",
        fake_create_html_soup
    )

    result = module.remove_html("<p>Texte</p>")

    assert result == "Texte"
    assert received == [
        "<p>Texte</p>"
    ]


def test_remove_html_decomposes_configured_elements(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "NON_TEXTUAL_HTML_ELEMENTS",
        (
            "custom",
        )
    )

    html = "<p>Visible</p><custom>Masqué</custom>"

    assert module.remove_html(html) == "Visible"


def test_remove_html_preserves_unknown_elements_text() -> None:
    html = "<article><unknown>Texte conservé</unknown></article>"

    assert module.remove_html(html) == "Texte conservé"