"""Tests des outils de métadonnées HTML des scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/scrapers/test_scraper_metadata_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.extractors.scrapers import scraper_metadata_utils as module


# Métadonnées vides

def test_empty_metadata_returns_expected_structure() -> None:
    result = module.empty_metadata()

    assert result == {
        "json-ld": [],
        "opengraph": []
    }


def test_empty_metadata_returns_new_lists() -> None:
    first_result = module.empty_metadata()
    second_result = module.empty_metadata()

    assert first_result is not second_result
    assert first_result["json-ld"] is not second_result["json-ld"]
    assert first_result["opengraph"] is not second_result["opengraph"]


# Extraction

def test_extract_metadata_returns_expected_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_html = "<html><head></head><body></body></html>"
    page_url = "https://example.com/article"
    expected_base_url = "https://example.com/base"
    received: dict[str, Any] = {}

    monkeypatch.setattr(
        module,
        "get_base_url",
        lambda html, url: expected_base_url
    )

    def fake_extract(
        html: str,
        *,
        base_url: str,
        syntaxes: list[str],
        uniform: bool
    ) -> dict[str, Any]:
        received["html"] = html
        received["base_url"] = base_url
        received["syntaxes"] = syntaxes
        received["uniform"] = uniform

        return {
            "json-ld": [
                {
                    "@type": "NewsArticle"
                }
            ],
            "opengraph": [
                {
                    "og:title": "Titre"
                }
            ]
        }

    monkeypatch.setattr(
        module.extruct,
        "extract",
        fake_extract
    )

    result = module.extract_metadata(
        raw_html,
        page_url
    )

    assert result == {
        "json-ld": [
            {
                "@type": "NewsArticle"
            }
        ],
        "opengraph": [
            {
                "og:title": "Titre"
            }
        ]
    }
    assert received == {
        "html": raw_html,
        "base_url": expected_base_url,
        "syntaxes": [
            "json-ld",
            "opengraph"
        ],
        "uniform": True
    }


@pytest.mark.parametrize(
    "error",
    [
        TypeError("Type invalide"),
        ValueError("Valeur invalide"),
        AttributeError("Attribut invalide")
    ]
)
def test_extract_metadata_returns_empty_on_supported_error(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_base_url",
        lambda html, url: url
    )
    monkeypatch.setattr(
        module.extruct,
        "extract",
        lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    result = module.extract_metadata(
        "<html></html>",
        "https://example.com/article"
    )

    assert result == {
        "json-ld": [],
        "opengraph": []
    }


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "invalid",
        42
    ]
)
def test_extract_metadata_returns_empty_for_non_mapping_result(
    metadata: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_base_url",
        lambda html, url: url
    )
    monkeypatch.setattr(
        module.extruct,
        "extract",
        lambda *args, **kwargs: metadata
    )

    result = module.extract_metadata(
        "<html></html>",
        "https://example.com/article"
    )

    assert result == {
        "json-ld": [],
        "opengraph": []
    }


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {},
            {
                "json-ld": [],
                "opengraph": []
            }
        ),
        (
            {
                "json-ld": {
                    "@type": "NewsArticle"
                },
                "opengraph": []
            },
            {
                "json-ld": [],
                "opengraph": []
            }
        ),
        (
            {
                "json-ld": [],
                "opengraph": {
                    "og:title": "Titre"
                }
            },
            {
                "json-ld": [],
                "opengraph": []
            }
        ),
        (
            {
                "json-ld": [
                    {
                        "@type": "NewsArticle"
                    }
                ],
                "opengraph": [
                    {
                        "og:title": "Titre"
                    }
                ]
            },
            {
                "json-ld": [
                    {
                        "@type": "NewsArticle"
                    }
                ],
                "opengraph": [
                    {
                        "og:title": "Titre"
                    }
                ]
            }
        )
    ]
)
def test_extract_metadata_normalizes_lists(
    metadata: dict[str, Any],
    expected: dict[str, list[Any]],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "get_base_url",
        lambda html, url: url
    )
    monkeypatch.setattr(
        module.extruct,
        "extract",
        lambda *args, **kwargs: metadata
    )

    result = module.extract_metadata(
        "<html></html>",
        "https://example.com/article"
    )

    assert result == expected


# Aplatissement JSON-LD

def test_flatten_json_ld_returns_mapping_itself() -> None:
    value = {
        "@type": "NewsArticle",
        "headline": "Titre"
    }

    result = module.flatten_json_ld(value)

    assert result == [
        value
    ]
    assert result[0] is not value


def test_flatten_json_ld_flattens_graph() -> None:
    value = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "NewsArticle",
                "headline": "Article"
            },
            {
                "@type": "Organization",
                "name": "Éditeur"
            }
        ]
    }

    result = module.flatten_json_ld(value)

    assert result == [
        value,
        {
            "@type": "NewsArticle",
            "headline": "Article"
        },
        {
            "@type": "Organization",
            "name": "Éditeur"
        }
    ]


def test_flatten_json_ld_flattens_nested_graphs() -> None:
    value = {
        "@graph": [
            {
                "@type": "NewsArticle",
                "@graph": [
                    {
                        "@type": "ImageObject"
                    }
                ]
            }
        ]
    }

    result = module.flatten_json_ld(value)

    assert result == [
        value,
        {
            "@type": "NewsArticle",
            "@graph": [
                {
                    "@type": "ImageObject"
                }
            ]
        },
        {
            "@type": "ImageObject"
        }
    ]


def test_flatten_json_ld_flattens_list() -> None:
    value = [
        {
            "@type": "NewsArticle"
        },
        {
            "@type": "BlogPosting"
        }
    ]

    result = module.flatten_json_ld(value)

    assert result == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        42,
        True,
        object()
    ]
)
def test_flatten_json_ld_ignores_unsupported_values(
    value: Any
) -> None:
    assert module.flatten_json_ld(value) == []


# Objets JSON-LD

def test_get_json_ld_objects_returns_all_objects() -> None:
    metadata = {
        "json-ld": [
            {
                "@type": "NewsArticle"
            },
            {
                "@graph": [
                    {
                        "@type": "Organization"
                    }
                ]
            }
        ]
    }

    result = module.get_json_ld_objects(metadata)

    assert result == [
        {
            "@type": "NewsArticle"
        },
        {
            "@graph": [
                {
                    "@type": "Organization"
                }
            ]
        },
        {
            "@type": "Organization"
        }
    ]


@pytest.mark.parametrize(
    "items",
    [
        None,
        {},
        "invalid",
        42
    ]
)
def test_get_json_ld_objects_returns_empty_for_invalid_collection(
    items: Any
) -> None:
    result = module.get_json_ld_objects(
        {
            "json-ld": items
        }
    )

    assert result == []


def test_get_json_ld_objects_returns_empty_when_missing() -> None:
    assert module.get_json_ld_objects({}) == []


# Types JSON-LD

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "NewsArticle",
            {
                "newsarticle"
            }
        ),
        (
            "  BlogPosting  ",
            {
                "blogposting"
            }
        ),
        (
            [
                "NewsArticle",
                "Article"
            ],
            {
                "newsarticle",
                "article"
            }
        ),
        (
            (
                "ReportageArticle",
                "OpinionNewsArticle"
            ),
            {
                "reportagearticle",
                "opinionnewsarticle"
            }
        ),
        (
            {
                "ReviewNewsArticle",
                "BackgroundNewsArticle"
            },
            {
                "reviewnewsarticle",
                "backgroundnewsarticle"
            }
        )
    ]
)
def test_normalize_json_ld_types(
    value: Any,
    expected: set[str]
) -> None:
    assert module.normalize_json_ld_types(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        42,
        {},
        object()
    ]
)
def test_normalize_json_ld_types_returns_empty_for_invalid_value(
    value: Any
) -> None:
    assert module.normalize_json_ld_types(value) == set()


def test_normalize_json_ld_types_ignores_empty_items() -> None:
    result = module.normalize_json_ld_types(
        [
            "NewsArticle",
            "",
            None,
            "   "
        ]
    )

    assert result == {
        "newsarticle"
    }


# Article JSON-LD

def test_get_article_json_ld_returns_empty_without_candidate() -> None:
    metadata = {
        "json-ld": [
            {
                "@type": "Organization"
            },
            {
                "@type": "Person"
            }
        ]
    }

    assert module.get_article_json_ld(metadata) == {}


def test_get_article_json_ld_accepts_supported_article_type() -> None:
    article = {
        "@type": "NewsArticle",
        "headline": "Titre"
    }

    result = module.get_article_json_ld(
        {
            "json-ld": [
                article
            ]
        }
    )

    assert result == article


def test_get_article_json_ld_accepts_multiple_types() -> None:
    article = {
        "@type": [
            "CreativeWork",
            "BlogPosting"
        ],
        "headline": "Titre"
    }

    result = module.get_article_json_ld(
        {
            "json-ld": [
                article
            ]
        }
    )

    assert result == article


def test_get_article_json_ld_selects_best_candidate() -> None:
    basic_article = {
        "@type": "NewsArticle",
        "headline": "Titre simple"
    }
    complete_article = {
        "@type": "NewsArticle",
        "articleBody": "Contenu",
        "headline": "Titre complet",
        "datePublished": "2026-07-28T10:00:00Z",
        "image": "https://example.com/image.jpg"
    }
    dated_article = {
        "@type": "Article",
        "headline": "Titre daté",
        "datePublished": "2026-07-28T10:00:00Z"
    }

    result = module.get_article_json_ld(
        {
            "json-ld": [
                basic_article,
                complete_article,
                dated_article
            ]
        }
    )

    assert result == complete_article


def test_get_article_json_ld_prioritizes_article_body() -> None:
    article_with_body = {
        "@type": "NewsArticle",
        "articleBody": "Contenu"
    }
    article_without_body = {
        "@type": "NewsArticle",
        "headline": "Titre",
        "datePublished": "2026-07-28",
        "image": "image.jpg"
    }

    result = module.get_article_json_ld(
        {
            "json-ld": [
                article_without_body,
                article_with_body
            ]
        }
    )

    assert result == article_with_body


def test_get_article_json_ld_finds_candidate_in_graph() -> None:
    article = {
        "@type": "AnalysisNewsArticle",
        "headline": "Analyse"
    }

    result = module.get_article_json_ld(
        {
            "json-ld": [
                {
                    "@graph": [
                        {
                            "@type": "Organization"
                        },
                        article
                    ]
                }
            ]
        }
    )

    assert result == article


# Open Graph

def test_get_open_graph_returns_first_mapping() -> None:
    first_mapping = {
        "og:title": "Premier titre"
    }
    second_mapping = {
        "og:title": "Deuxième titre"
    }

    result = module.get_open_graph(
        {
            "opengraph": [
                "invalid",
                first_mapping,
                second_mapping
            ]
        }
    )

    assert result == first_mapping
    assert result is not first_mapping


@pytest.mark.parametrize(
    "items",
    [
        None,
        {},
        "invalid",
        42
    ]
)
def test_get_open_graph_returns_empty_for_invalid_collection(
    items: Any
) -> None:
    result = module.get_open_graph(
        {
            "opengraph": items
        }
    )

    assert result == {}


def test_get_open_graph_returns_empty_without_mapping() -> None:
    result = module.get_open_graph(
        {
            "opengraph": [
                None,
                "invalid",
                42
            ]
        }
    )

    assert result == {}


def test_get_open_graph_returns_empty_when_missing() -> None:
    assert module.get_open_graph({}) == {}