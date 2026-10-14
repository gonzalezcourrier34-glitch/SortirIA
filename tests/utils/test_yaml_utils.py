"""Tests des fonctions utilitaires YAML.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_yaml_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.yaml_utils import load_yaml_file


# Chargement valide

def test_load_yaml_file_returns_dictionary(tmp_path: Path) -> None:
    """Un fichier YAML valide doit être chargé correctement."""

    file = tmp_path / "config.yaml"
    file.write_text(
        "name: CheckIt\nversion: 1\n",
        encoding="utf-8"
    )

    data = load_yaml_file(file)

    assert data == {
        "name": "CheckIt",
        "version": 1
    }


# Fichier inexistant

def test_load_yaml_file_missing_file_returns_empty_dict() -> None:
    """Un fichier absent retourne toujours un dictionnaire vide."""

    data = load_yaml_file("unknown.yaml")

    assert data == {}


# YAML vide

def test_load_yaml_file_empty_file_returns_empty_dict(
    tmp_path: Path
) -> None:
    """Un fichier YAML vide retourne un dictionnaire vide."""

    file = tmp_path / "empty.yaml"
    file.write_text("", encoding="utf-8")

    data = load_yaml_file(file)

    assert data == {}


# YAML invalide

def test_load_yaml_file_invalid_yaml_returns_empty_dict(
    tmp_path: Path
) -> None:
    """Un YAML invalide retourne un dictionnaire vide."""

    file = tmp_path / "invalid.yaml"
    file.write_text(
        "key: [1,2",
        encoding="utf-8"
    )

    data = load_yaml_file(file)

    assert data == {}


# Type invalide

@pytest.mark.parametrize(
    "content",
    [
        "- a\n- b",
        "42",
        "'hello'",
        "true"
    ]
)
def test_load_yaml_file_invalid_root_type_returns_empty_dict(
    tmp_path: Path,
    content: str
) -> None:
    """La racine du YAML doit être un dictionnaire."""

    file = tmp_path / "invalid_root.yaml"
    file.write_text(
        content,
        encoding="utf-8"
    )

    data = load_yaml_file(file)

    assert data == {}


# Chemin invalide

def test_load_yaml_file_invalid_path_returns_empty_dict() -> None:
    """Un chemin invalide retourne un dictionnaire vide."""

    data = load_yaml_file(None)

    assert data == {}