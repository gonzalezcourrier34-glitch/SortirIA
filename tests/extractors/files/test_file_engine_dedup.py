"""Dédoublonnage du moteur « files » : même identifiant ou même adresse, jamais « même titre le même jour »."""

from __future__ import annotations

from typing import Any

from src.extractors.files.file_engine import FileBatchContext, identity_keys, is_same_event
from src.utils.extractor_utils import build_standard_article


def article(identifier: str, title: str, url: str) -> dict[str, Any]:
    return build_standard_article(identifier=identifier, source="Agenda", title=title, text="", image_url="",
                                  published_at="2026-11-07T10:00:00+01:00", url=url, language="fr",
                                  category="evenement", role="acquisition", metadata={})


def test_same_title_same_day_different_places_are_two_events() -> None:
    """Avant le 15/10/2026 : « Visite guidée » dans deux musées le même jour -> un seul gardé."""

    seen: set[str] = set()
    first = article("agglopole@p12@2026-11-07@aaa", "Visite guidée", "https://x.fr/agenda.pdf#page=12")
    second = article("agglopole@p12@2026-11-07@bbb", "Visite guidée", "https://x.fr/agenda.pdf?occurrence=2#page=12")
    assert not is_same_event(first, seen)
    seen |= identity_keys(first)
    assert not is_same_event(second, seen)
    assert all(":title:" not in key and not key.startswith("title:") for key in identity_keys(first))


def test_same_identifier_or_same_url_is_a_duplicate() -> None:
    seen: set[str] = set()
    first = article("ical:42", "Concert", "https://x.fr/e/42")
    seen |= identity_keys(first)
    assert is_same_event(article("ical:42", "Concert (modifié)", "https://autre.fr"), seen)   # même identifiant
    assert is_same_event(article("autre", "Autre titre", "https://x.fr/e/42"), seen)           # même adresse


def test_batch_context_uses_identity_keys() -> None:
    from src.extractors.core.extractor_results import ExtractorResult

    context = FileBatchContext()
    result = ExtractorResult(name="Test", source_type="document", status="success", articles=[
        article("a", "Marché", "https://x.fr/1"), article("b", "Marché", "https://x.fr/2"),
        article("a", "Marché", "https://x.fr/3"),
    ])
    context.add_result(result)
    assert [item["id"] for item in context.articles] == [article("a", "", "")["id"], article("b", "", "")["id"]]
    assert context.rejection_stats["doublon_lot"] == 1
