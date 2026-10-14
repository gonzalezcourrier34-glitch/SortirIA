"""Tests de la lecture des secrets dans `config/source_config.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026), pour la
modification qui accepte une variable d'environnement à la place d'un fichier
secret.
"""

from __future__ import annotations

import pytest

from config.source_config import validate_secret_reference


def test_secret_from_environment_variable_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SORTIRIA_TEST_SECRET", "valeur")
    assert validate_secret_reference(
        "openagenda", "api_key_secret_name", "sortiria_test_secret", enabled=True, required=True
    ) == []


def test_missing_secret_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("SORTIRIA_ABSENT_SECRET", raising=False)
    errors = validate_secret_reference(
        "openagenda", "api_key_secret_name", "sortiria_absent_secret",
        enabled=True, required=True, secrets_directory=tmp_path,
    )
    assert errors and "sortiria_absent_secret" in errors[0]
