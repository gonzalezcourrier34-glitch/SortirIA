"""Tests des fonctions utilitaires liées aux chemins et noms de fichiers.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_path_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.utils.path_utils import (
    DEFAULT_FILENAME,
    DEFAULT_SOURCE_NAME,
    clean_path_component,
    collapse_separators,
    normalize_filename,
    normalize_source_name,
    normalize_unicode,
    protect_windows_reserved_name,
    truncate_filename,
    truncate_name
)


# Normalisation Unicode

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("texte", "texte"),
        (42, "42"),
        (True, "True"),
        ("école", "école"),
        ("e\u0301cole", "école")
    ]
)
def test_normalize_unicode(
    value: Any,
    expected: str
) -> None:
    """Une valeur doit être convertie en texte Unicode NFC."""

    assert normalize_unicode(value) == expected


# Réduction des séparateurs

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("article", "article"),
        ("article___test", "article_test"),
        ("article---test", "article-test"),
        ("article__---___test", "article_-_test"),
        ("___article___", "_article_"),
        ("---article---", "-article-"),
        ("", "")
    ]
)
def test_collapse_separators(
    value: str,
    expected: str
) -> None:
    """Les suites de tirets et de soulignements doivent être réduites."""

    assert collapse_separators(value) == expected


# Nettoyage des composants de chemin

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("article", "article"),
        (" article ", "article"),
        ("...article...", "article"),
        ("___article___", "article"),
        ("---article---", "article"),
        (" ._-article-_. ", "article"),
        ("", ""),
        ("   ", ""),
        (".", ""),
        ("..", ""),
        ("...", "")
    ]
)
def test_clean_path_component(
    value: str,
    expected: str
) -> None:
    """Les extrémités dangereuses doivent être supprimées."""

    assert clean_path_component(value) == expected


# Protection des noms réservés Windows

@pytest.mark.parametrize(
    ("name", "prefix", "expected"),
    [
        ("article", "file", "article"),
        ("CON", "file", "file_CON"),
        ("con", "source", "source_con"),
        (" con ", "file", "file_con"),
        ("NUL.txt", "file", "file_NUL.txt"),
        ("com1.json", "file", "file_com1.json"),
        ("LPT9.csv", "source", "source_LPT9.csv"),
        ("com10.txt", "file", "com10.txt"),
        ("console.txt", "file", "console.txt"),
        ("", "file", ""),
        ("   ", "file", "")
    ]
)
def test_protect_windows_reserved_name(
    name: str,
    prefix: str,
    expected: str
) -> None:
    """Les noms réservés par Windows doivent être préfixés."""

    assert protect_windows_reserved_name(name, prefix) == expected


@pytest.mark.parametrize(
    "reserved_name",
    [
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com9",
        "lpt1",
        "lpt5",
        "lpt9"
    ]
)
def test_protect_windows_reserved_name_detects_all_reserved_names(
    reserved_name: str
) -> None:
    """Les principaux noms réservés Windows doivent être protégés."""

    result = protect_windows_reserved_name(
        reserved_name,
        prefix="file"
    )

    assert result == f"file_{reserved_name}"


# Troncature générique

@pytest.mark.parametrize(
    ("name", "max_length", "expected"),
    [
        ("article", 20, "article"),
        ("article", 7, "article"),
        ("article", 4, "arti"),
        ("article", 1, "a"),
        ("article", 0, "a"),
        ("article", -5, "a"),
        ("", 10, "")
    ]
)
def test_truncate_name(
    name: str,
    max_length: int,
    expected: str
) -> None:
    """Un nom doit être limité à la longueur demandée."""

    assert truncate_name(name, max_length) == expected


# Troncature des noms de fichiers

@pytest.mark.parametrize(
    ("filename", "max_length", "expected"),
    [
        ("article.json", 20, "article.json"),
        ("article.json", 12, "article.json"),
        ("article.json", 10, "artic.json"),
        ("article.json", 8, "art.json"),
        ("article", 4, "arti"),
        ("archive.tar.gz", 10, "archive.gz"),
        ("article.json", 5, "artic"),
        ("article.json", 1, "a"),
        ("article.json", 0, "a"),
        ("", 10, "")
    ]
)

def test_truncate_filename(
    filename: str,
    max_length: int,
    expected: str
) -> None:
    """L'extension doit être préservée lorsque la longueur le permet."""

    assert truncate_filename(filename, max_length) == expected


def test_truncate_filename_preserves_last_extension() -> None:
    """Seule la dernière extension doit être considérée."""

    result = truncate_filename(
        "long_archive.tar.gz",
        max_length=12
    )

    assert result == "long_arch.gz"
    assert len(result) == 12


def test_truncate_filename_cuts_extension_when_it_is_too_long() -> None:
    """Une extension trop longue ne peut pas être préservée."""

    result = truncate_filename(
        "article.verylongextension",
        max_length=8
    )

    assert result == "article."


# Normalisation des noms de sources

@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Le Monde", "le_monde"),
        ("  LE MONDE  ", "le_monde"),
        ("France Info", "france_info"),
        ("BBC-News", "bbc-news"),
        ("Reuters___Fact---Check", "reuters_fact-check"),
        ("AFP / Factuel", "afp_factuel"),
        ("école média", "école_média"),
        ("source.test", "source_test"),
        ("source@test", "source_test"),
        ("source:test", "source_test"),
        ("source\\test", "source_test"),
        (42, "42"),
        (True, "true")
    ]
)
def test_normalize_source_name(
    source: Any,
    expected: str
) -> None:
    """Une source doit devenir un nom de dossier sûr et stable."""

    assert normalize_source_name(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        "   ",
        ".",
        "..",
        "...",
        "___",
        "---",
        " / ",
        "@@@",
        "***"
    ]
)
def test_normalize_source_name_returns_default_for_empty_values(
    source: Any
) -> None:
    """Une source vide ou inutilisable doit retourner le nom par défaut."""

    assert normalize_source_name(source) == DEFAULT_SOURCE_NAME


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("CON", "source_con"),
        ("nul", "source_nul"),
        ("COM1", "source_com1"),
        ("LPT9", "source_lpt9")
    ]
)
def test_normalize_source_name_protects_windows_reserved_names(
    source: str,
    expected: str
) -> None:
    """Une source réservée par Windows doit être protégée."""

    assert normalize_source_name(source) == expected


def test_normalize_source_name_truncates_long_value() -> None:
    """Une source trop longue doit être tronquée."""

    source = "a" * 200

    result = normalize_source_name(
        source,
        max_length=20
    )

    assert result == "a" * 20
    assert len(result) == 20


def test_normalize_source_name_uses_safe_length_for_invalid_limit() -> None:
    """Une longueur invalide doit être remplacée par la valeur minimale."""

    result = normalize_source_name(
        "article",
        max_length=0
    )

    assert result == "a"


def test_normalize_source_name_cleans_after_truncation() -> None:
    """Une extrémité dangereuse créée par troncature doit être supprimée."""

    result = normalize_source_name(
        "article-test",
        max_length=8
    )

    assert result == "article"


# Normalisation des noms de fichiers

@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("article.json", "article.json"),
        (" Article.JSON ", "article.json"),
        ("mon article.json", "mon_article.json"),
        ("mon   article.json", "mon_article.json"),
        ("article<>test.json", "article_test.json"),
        ("article:test.json", "article_test.json"),
        ("article/test.json", "article_test.json"),
        ("article\\test.json", "article_test.json"),
        ("article|test?.json", "article_test_.json"),
        ("article___test.json", "article_test.json"),
        ("article---test.json", "article-test.json"),
        (".article.json", "article.json"),
        ("...article.json", "article.json"),
        ("école média.JSON", "école_média.json"),
        (42, "42"),
        (True, "true")
    ]
)
def test_normalize_filename(
    filename: Any,
    expected: str
) -> None:
    """Une valeur doit devenir un nom de fichier sûr."""

    assert normalize_filename(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        None,
        "",
        "   ",
        ".",
        "..",
        "...",
        "___",
        "---",
        " / ",
        "\\",
        "<>:\"/\\|?*"
    ]
)
def test_normalize_filename_returns_default_for_empty_values(
    filename: Any
) -> None:
    """Un nom vide ou inutilisable doit retourner le nom par défaut."""

    assert normalize_filename(filename) == DEFAULT_FILENAME


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("CON", "file_con"),
        ("nul.txt", "file_nul.txt"),
        ("COM1.json", "file_com1.json"),
        ("LPT9.csv", "file_lpt9.csv")
    ]
)
def test_normalize_filename_protects_windows_reserved_names(
    filename: str,
    expected: str
) -> None:
    """Un nom réservé par Windows doit être protégé."""

    assert normalize_filename(filename) == expected


def test_normalize_filename_truncates_long_name() -> None:
    """Un nom de fichier trop long doit être tronqué."""

    filename = f"{'a' * 300}.json"

    result = normalize_filename(
        filename,
        max_length=20
    )

    assert result == f"{'a' * 15}.json"
    assert len(result) == 20


def test_normalize_filename_preserves_extension_during_truncation() -> None:
    """La troncature doit conserver l'extension lorsque possible."""

    result = normalize_filename(
        "un_nom_de_fichier_tres_long.csv",
        max_length=15
    )

    assert result.endswith(".csv")
    assert len(result) == 15


def test_normalize_filename_uses_safe_length_for_invalid_limit() -> None:
    """Une longueur invalide doit produire un nom minimal sûr."""

    result = normalize_filename(
        "article.json",
        max_length=0
    )

    assert result == "a"


def test_normalize_filename_cleans_after_truncation() -> None:
    """Une extrémité interdite créée par troncature doit être nettoyée."""

    result = normalize_filename(
        "article-test",
        max_length=8
    )

    assert result == "article"


def test_normalize_filename_replaces_control_characters() -> None:
    """Les caractères de contrôle doivent être remplacés."""

    result = normalize_filename(
        "article\x00test\nfinal.json"
    )

    assert result == "article_test_final.json"


def test_normalize_filename_normalizes_decomposed_unicode() -> None:
    """Les caractères Unicode décomposés doivent être normalisés."""

    result = normalize_filename("e\u0301cole.json")

    assert result == "école.json"