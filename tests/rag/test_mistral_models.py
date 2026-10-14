"""Tests de la sélection des modèles Mistral dans `app/rag_service.py`.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

import pytest

from app.rag_service import is_mistral_model


@pytest.mark.parametrize(
    "name",
    ["mistral-embed", "mistral-small-latest", "ministral-8b-latest", " Mistral-Moderation-Latest "],
)
def test_mistral_models_are_accepted(name: str) -> None:
    assert is_mistral_model(name)


@pytest.mark.parametrize("name", ["gpt-4o-mini", "text-embedding-3-small", "qwen3-1.7b"])
def test_other_providers_are_refused(name: str) -> None:
    assert not is_mistral_model(name)
