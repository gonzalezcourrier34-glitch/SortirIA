"""Tests unitaires de `app/security.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_security.py` (commit 18f7ce1).
"""

import pytest
from fastapi import HTTPException, status

from app.security import require_api_key


def test_require_api_key_server_not_configured(monkeypatch):
    monkeypatch.setattr("app.security.API_KEY", "")

    with pytest.raises(HTTPException) as exc_info:
        require_api_key("secret")

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "La clé API du serveur n'est pas configurée."


def test_require_api_key_missing_token(monkeypatch):
    monkeypatch.setattr("app.security.API_KEY", "secret")

    with pytest.raises(HTTPException) as exc_info:
        require_api_key(None)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Token manquant."


def test_require_api_key_invalid_token(monkeypatch):
    monkeypatch.setattr("app.security.API_KEY", "secret")

    with pytest.raises(HTTPException) as exc_info:
        require_api_key("wrong-token")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Token invalide."


def test_require_api_key_valid_token(monkeypatch):
    monkeypatch.setattr("app.security.API_KEY", "secret")

    result = require_api_key("secret")

    assert result == "secret"