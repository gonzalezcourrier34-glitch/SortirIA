"""Tests de `src/event/event_embedding.py` et de l'écriture des embeddings.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
"""

from __future__ import annotations

from src.event import event_repository as repo
from src.event.event_embedding import MAX_DESCRIPTION_CHARS, build_embedding_text, to_pgvector

from .test_event_repository import FakeConnection


def test_build_embedding_text_combines_fields() -> None:
    text = build_embedding_text({
        "title": "Concert Jazz", "category": "concert", "venue_name": "Le Jam",
        "city": "Montpellier", "description": "Soirée jazz.",
    })
    assert text == "Concert Jazz. Catégorie : concert. Lieu : Le Jam, Montpellier. Soirée jazz."


def test_build_embedding_text_truncates_long_descriptions() -> None:
    text = build_embedding_text({"title": "T", "description": "x" * (MAX_DESCRIPTION_CHARS + 500)})
    assert len(text) < MAX_DESCRIPTION_CHARS + 20


def test_to_pgvector() -> None:
    assert to_pgvector([0.1, 2, -0.5]) == "[0.1,2,-0.5]"


def test_update_embeddings() -> None:
    connection = FakeConnection()
    assert repo.update_embeddings(connection, [("e1", [0.1, 0.2])], "mistral-embed") == 1
    _kind, sql, rows = connection.calls[0]
    assert "embedding = %(embedding)s::vector" in sql
    assert rows == [{"event_id": "e1", "embedding": "[0.1,0.2]", "model": "mistral-embed"}]
    assert repo.update_embeddings(connection, [], "mistral-embed") == 0
