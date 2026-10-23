"""Tests des fonctions générales communes aux extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_extractor_utils.py` (commit 93632c5).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from typing import Any

import pytest

from src.utils.extractor_utils import (
    build_standard_article,
    get_numeric_value,
    get_value,
    log_extraction_summary,
    validate_article,
)

# Construction des articles

def test_build_standard_article_returns_normalized_article(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un article complet doit être normalisé selon le schéma standard."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: "2026-07-20T10:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: value
    )

    result = build_standard_article(
        identifier="article-123",
        source="  Reuters  ",
        title="  Titre   de   l'article  ",
        text="  Texte   complet   de l'article  ",
        image_url=" https://example.com/image.jpg ",
        image_path=" images/article.jpg ",
        published_at="2026-07-20",
        url=" https://example.com/article ",
        author=" Jean Dupont ",
        language=" FR ",
        category=" Politique ",
        label=" Concert ",
        role=" test_role ",
        metadata={
            "country": "France",
            "score": 0.8
        }
    )

    expected_id = hashlib.md5(
        b"article-123",
        usedforsecurity=False
    ).hexdigest()[:8]

    assert result == {
        "id": expected_id,
        "source": "Reuters",
        "title": "Titre de l'article",
        "text": "Texte complet de l'article",
        "image_url": "https://example.com/image.jpg",
        "image_path": "images/article.jpg",
        "published_at": "2026-07-20T10:00:00+00:00",
        "url": "https://example.com/article",
        "author": "Jean Dupont",
        "language": "fr",
        "category": "Politique",
        "label": "Concert",
        "role": "test_role",
        "extracted_at": "2026-07-27T12:00:00+00:00",
        "metadata": {
            "country": "France",
            "score": 0.8
        }
    }


def test_build_standard_article_uses_url_as_identifier(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'URL doit servir d'identifiant lorsque l'identifiant est vide."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: value
    )

    result = build_standard_article(
        identifier="",
        source="BBC",
        title="Un titre",
        url="https://example.com/article"
    )

    expected_id = hashlib.md5(
        b"https://example.com/article",
        usedforsecurity=False
    ).hexdigest()[:8]

    assert result["id"] == expected_id
    assert result["url"] == "https://example.com/article"


def test_build_standard_article_builds_identifier_from_article_fields(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les champs métier doivent former l'identifiant en dernier recours."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: "2026-07-20T10:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: value
    )

    result = build_standard_article(
        identifier="",
        source="Reuters",
        title="Titre de test",
        published_at="2026-07-20",
        url=""
    )

    identifier = (
        "Reuters:Titre de test:2026-07-20T10:00:00+00:00"
    )
    expected_id = hashlib.md5(
        identifier.encode("utf-8"),
        usedforsecurity=False
    ).hexdigest()[:8]

    assert result["id"] == expected_id


def test_build_standard_article_ignores_empty_identifier_parts(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les composantes vides ne doivent pas produire de séparateurs inutiles."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: value
    )

    result = build_standard_article(
        identifier=None,
        source="",
        title="Titre unique",
        published_at=None,
        url=None
    )

    expected_id = hashlib.md5(
        b"Titre unique",
        usedforsecurity=False
    ).hexdigest()[:8]

    assert result["id"] == expected_id


@pytest.mark.parametrize(
    "identifier",
    [
        None,
        "",
        "   "
    ]
)
def test_build_standard_article_rejects_missing_identity(
    monkeypatch: pytest.MonkeyPatch,
    identifier: Any
) -> None:
    """Un article sans identifiant, URL ni titre doit être refusé."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )

    with pytest.raises(
        ValueError,
        match="Impossible de construire un article"
    ):
        build_standard_article(
            identifier=identifier,
            source="Reuters",
            title="",
            url=""
        )


def test_build_standard_article_prefers_role_over_dataset_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le champ role doit être prioritaire sur dataset_role."""

    received: dict[str, Any] = {}

    def fake_normalize_role(value: Any) -> str:
        received["value"] = value
        return "normalized_role"

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        fake_normalize_role
    )

    result = build_standard_article(
        identifier="article-1",
        source="Reuters",
        title="Titre",
        role="acquisition",
        dataset_role="test_role"
    )

    assert received["value"] == "acquisition"
    assert result["role"] == "normalized_role"


def test_build_standard_article_uses_dataset_role_when_role_is_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """dataset_role doit être utilisé lorsque role est vide."""

    received: dict[str, Any] = {}

    def fake_normalize_role(value: Any) -> str:
        received["value"] = value
        return "normalized_role"

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        fake_normalize_role
    )

    build_standard_article(
        identifier="article-1",
        source="Reuters",
        title="Titre",
        role="",
        dataset_role="test_role"
    )

    assert received["value"] == "test_role"


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "metadata",
        42,
        True
    ]
)
def test_build_standard_article_replaces_invalid_metadata(
    monkeypatch: pytest.MonkeyPatch,
    metadata: Any
) -> None:
    """Des métadonnées invalides doivent produire un dictionnaire vide."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: ""
    )

    result = build_standard_article(
        identifier="article-1",
        source="Reuters",
        title="Titre",
        metadata=metadata
    )

    assert result["metadata"] == {}


def test_build_standard_article_copies_metadata(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les métadonnées retournées ne doivent pas partager le même dictionnaire."""

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        lambda article: article
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: ""
    )

    metadata = {"country": "France"}

    result = build_standard_article(
        identifier="article-1",
        source="Reuters",
        title="Titre",
        metadata=metadata
    )

    assert result["metadata"] == metadata
    assert result["metadata"] is not metadata


def test_build_standard_article_calls_schema_normalizer(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'article construit doit être transmis au normalisateur de schéma."""

    received: dict[str, Any] = {}

    def fake_schema_normalizer(
        article: Mapping[str, Any]
    ) -> dict[str, Any]:
        received["article"] = dict(article)
        return {
            "normalized": True,
            **article
        }

    monkeypatch.setattr(
        "src.utils.extractor_utils.get_extraction_date",
        lambda: "2026-07-27T12:00:00+00:00"
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_article_schema",
        fake_schema_normalizer
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.convert_date_to_iso",
        lambda value: ""
    )
    monkeypatch.setattr(
        "src.utils.extractor_utils.normalize_dataset_role",
        lambda value: ""
    )

    result = build_standard_article(
        identifier="article-1",
        source="Reuters",
        title="Titre"
    )

    assert received["article"]["source"] == "Reuters"
    assert received["article"]["title"] == "Titre"
    assert result["normalized"] is True


# Lecture des valeurs textuelles

@pytest.mark.parametrize(
    ("row", "candidates", "default", "expected"),
    [
        (
            {"title": "Titre"},
            ["title"],
            "",
            "Titre"
        ),
        (
            {
                "title": "",
                "headline": "Actualité"
            },
            ["title", "headline"],
            "",
            "Actualité"
        ),
        (
            {
                "title": "   ",
                "headline": " Actualité "
            },
            ["title", "headline"],
            "",
            "Actualité"
        ),
        (
            {
                "title": None,
                "headline": 42
            },
            ["title", "headline"],
            "",
            "42"
        ),
        (
            {
                "first": "Premier",
                "second": "Second"
            },
            ["first", "second"],
            "",
            "Premier"
        ),
        (
            {"other": "Valeur"},
            ["title", "headline"],
            "Défaut",
            "Défaut"
        ),
        (
            {},
            [],
            "Défaut",
            "Défaut"
        )
    ]
)
def test_get_value_returns_first_non_empty_value(
    row: Mapping[str, Any],
    candidates: list[str],
    default: str,
    expected: str
) -> None:
    """La première valeur textuelle non vide doit être retournée."""

    assert get_value(row, candidates, default) == expected


@pytest.mark.parametrize(
    ("row", "default", "expected"),
    [
        (None, "Défaut", "Défaut"),
        ([], "Défaut", "Défaut"),
        ("texte", " Défault ", "Défault"),
        (42, 100, "100"),
        (True, None, "")
    ]
)
def test_get_value_returns_default_for_invalid_row(
    row: Any,
    default: Any,
    expected: str
) -> None:
    """Une ligne invalide doit produire la valeur par défaut normalisée."""

    assert get_value(row, ["title"], default) == expected


def test_get_value_skips_missing_fields() -> None:
    """Les champs absents doivent être ignorés."""

    row = {
        "headline": "Titre secondaire"
    }

    result = get_value(
        row,
        ["title", "name", "headline"]
    )

    assert result == "Titre secondaire"


# Lecture des valeurs numériques

@pytest.mark.parametrize(
    ("row", "candidates", "expected"),
    [
        (
            {"score": 0},
            ["score"],
            0.0
        ),
        (
            {"score": 1},
            ["score"],
            1.0
        ),
        (
            {"score": 1.5},
            ["score"],
            1.5
        ),
        (
            {"score": "2.5"},
            ["score"],
            2.5
        ),
        (
            {
                "score": "invalid",
                "rating": "4.5"
            },
            ["score", "rating"],
            4.5
        ),
        (
            {
                "first": None,
                "second": 0
            },
            ["first", "second"],
            0.0
        ),
        (
            {
                "first": "   ",
                "second": -2.5
            },
            ["first", "second"],
            -2.5
        )
    ]
)
def test_get_numeric_value_returns_first_valid_number(
    row: Mapping[str, Any],
    candidates: list[str],
    expected: float
) -> None:
    """La première valeur numérique valide doit être retournée."""

    assert get_numeric_value(row, candidates) == expected


@pytest.mark.parametrize(
    "row",
    [
        None,
        [],
        "row",
        42,
        True
    ]
)
def test_get_numeric_value_returns_none_for_invalid_row(
    row: Any
) -> None:
    """Une ligne invalide ne doit produire aucune valeur numérique."""

    assert get_numeric_value(row, ["score"]) is None


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"score": None},
        {"score": ""},
        {"score": "invalid"},
        {"score": True},
        {"score": float("nan")},
        {"score": float("inf")},
        {"score": float("-inf")}
    ]
)
def test_get_numeric_value_returns_none_without_valid_number(
    row: Mapping[str, Any]
) -> None:
    """Une ligne sans nombre fini exploitable doit retourner None."""

    assert get_numeric_value(row, ["score"]) is None


def test_get_numeric_value_respects_candidate_order() -> None:
    """L'ordre des champs candidats doit être respecté."""

    row = {
        "first": 1.5,
        "second": 8.5
    }

    assert get_numeric_value(
        row,
        ["first", "second"]
    ) == 1.5


# Validation

def test_validate_article_delegates_to_central_validator(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La validation doit être déléguée au validateur métier central."""

    received: dict[str, Any] = {}

    def fake_validator(
        article: Mapping[str, Any],
        filters: Mapping[str, Any] | None
    ) -> tuple[bool, str]:
        received["article"] = article
        received["filters"] = filters
        return True, "valide"

    monkeypatch.setattr(
        "src.utils.extractor_utils.validate_article_with_reason",
        fake_validator
    )

    article = {
        "source": "Reuters",
        "title": "Titre"
    }
    filters = {
        "require_title": True
    }

    result = validate_article(article, filters)

    assert result == (True, "valide")
    assert received["article"] is article
    assert received["filters"] is filters


def test_validate_article_accepts_none_filters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'absence de filtres doit être transmise au validateur."""

    received: dict[str, Any] = {}

    def fake_validator(
        article: Mapping[str, Any],
        filters: Mapping[str, Any] | None
    ) -> tuple[bool, str]:
        received["filters"] = filters
        return False, "titre_absent"

    monkeypatch.setattr(
        "src.utils.extractor_utils.validate_article_with_reason",
        fake_validator
    )

    result = validate_article(
        {
            "source": "Reuters",
            "title": ""
        }
    )

    assert result == (False, "titre_absent")
    assert received["filters"] is None


@pytest.mark.parametrize(
    "filters",
    [
        [],
        "filters",
        42,
        True
    ]
)
def test_validate_article_rejects_invalid_filters(
    monkeypatch: pytest.MonkeyPatch,
    filters: Any
) -> None:
    """Une configuration de filtres invalide doit être refusée."""

    called = False

    def fake_validator(
        article: Mapping[str, Any],
        filters: Mapping[str, Any] | None
    ) -> tuple[bool, str]:
        nonlocal called
        called = True
        return True, "valide"

    monkeypatch.setattr(
        "src.utils.extractor_utils.validate_article_with_reason",
        fake_validator
    )

    result = validate_article(
        {
            "source": "Reuters",
            "title": "Titre"
        },
        filters
    )

    assert result == (False, "filtres_invalides")
    assert called is False


# Journalisation

def test_log_extraction_summary_logs_counts(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le bilan d'extraction doit contenir les compteurs normalisés."""

    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "src.utils.extractor_utils.logger.info",
        lambda *args: calls.append(args)
    )

    log_extraction_summary(
        source_name=" Reuters ",
        extracted_count=8,
        processed_count=10,
        rejection_stats=Counter()
    )

    assert calls == [
        (
            "%s : %s publication(s) extraite(s) sur %s analysée(s).",
            "Reuters",
            8,
            10
        )
    ]


def test_log_extraction_summary_logs_rejection_reasons(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les motifs de rejet doivent être journalisés lorsqu'ils existent."""

    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "src.utils.extractor_utils.logger.info",
        lambda *args: calls.append(args)
    )

    rejection_stats = Counter({
        "titre_absent": 2,
        "doublon": 1
    })

    log_extraction_summary(
        source_name="Reuters",
        extracted_count=7,
        processed_count=10,
        rejection_stats=rejection_stats
    )

    assert calls == [
        (
            "%s : %s publication(s) extraite(s) sur %s analysée(s).",
            "Reuters",
            7,
            10
        ),
        (
            "%s : motifs de rejet : %s.",
            "Reuters",
            {
                "titre_absent": 2,
                "doublon": 1
            }
        )
    ]


@pytest.mark.parametrize(
    ("source_name", "expected"),
    [
        ("Reuters", "Reuters"),
        (" Reuters ", "Reuters"),
        ("", "source_inconnue"),
        ("   ", "source_inconnue"),
        (None, "source_inconnue"),
        (42, "42")
    ]
)
def test_log_extraction_summary_normalizes_source_name(
    monkeypatch: pytest.MonkeyPatch,
    source_name: Any,
    expected: str
) -> None:
    """Le nom de la source doit être nettoyé ou remplacé."""

    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "src.utils.extractor_utils.logger.info",
        lambda *args: calls.append(args)
    )

    log_extraction_summary(
        source_name=source_name,
        extracted_count=1,
        processed_count=2,
        rejection_stats=Counter()
    )

    assert calls[0][1] == expected


@pytest.mark.parametrize(
    ("extracted_count", "processed_count", "expected_extracted", "expected_processed"),
    [
        (5, 10, 5, 10),
        ("5", "10", 5, 10),
        (5.0, 10.0, 5, 10),
        (-1, -2, 0, 0),
        ("invalid", None, 0, 0),
        (True, False, 0, 0),
        (1.5, 2.5, 0, 0)
    ]
)
def test_log_extraction_summary_normalizes_counts(
    monkeypatch: pytest.MonkeyPatch,
    extracted_count: Any,
    processed_count: Any,
    expected_extracted: int,
    expected_processed: int
) -> None:
    """Les compteurs invalides doivent être remplacés par zéro."""

    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "src.utils.extractor_utils.logger.info",
        lambda *args: calls.append(args)
    )

    log_extraction_summary(
        source_name="Reuters",
        extracted_count=extracted_count,
        processed_count=processed_count,
        rejection_stats=Counter()
    )

    assert calls[0][2] == expected_extracted
    assert calls[0][3] == expected_processed


def test_log_extraction_summary_does_not_log_empty_rejections(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un compteur de rejets vide ne doit pas produire de second log."""

    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "src.utils.extractor_utils.logger.info",
        lambda *args: calls.append(args)
    )

    log_extraction_summary(
        source_name="Reuters",
        extracted_count=10,
        processed_count=10,
        rejection_stats=Counter()
    )

    assert len(calls) == 1