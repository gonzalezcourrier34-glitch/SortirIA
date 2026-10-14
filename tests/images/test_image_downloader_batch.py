"""Tests du traitement par lot des images CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/images/test_image_downloader_batch.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

import src.images.image_downloader as module
from src.images.image_downloader import (
    IMAGE_CACHE_FIELDS,
    IMAGE_STATUS_DOWNLOADED,
    IMAGE_STATUS_DUPLICATE_URL,
    IMAGE_STATUS_INVALID_ARTICLE,
    IMAGE_STATUS_MISSING_URL,
    IMAGE_STATUS_NOT_REQUESTED,
    IMAGE_STATUS_TIMEOUT,
    IMAGE_STATUS_UNEXPECTED_ERROR,
    copy_cached_image_data,
    download_images_for_articles,
    format_download_status_summary
)


# Données de test

def build_article(
    article_id: str,
    image_url: str = "https://example.com/image.jpg"
) -> dict[str, Any]:
    """Construit un article minimal pour les tests."""

    return {
        "id": article_id,
        "source": "Reuters",
        "title": f"Article {article_id}",
        "image_url": image_url
    }


def build_downloaded_article(
    article_id: str,
    image_url: str = "https://example.com/image.jpg"
) -> dict[str, Any]:
    """Construit un article enrichi avec une image téléchargée."""

    article = build_article(article_id, image_url)
    article.update({
        "image_path": f"data/images/reuters/{article_id}.jpg",
        "image_download_status": IMAGE_STATUS_DOWNLOADED,
        "image_download_error": "",
        "image_format": "JPEG",
        "image_mime_type": "image/jpeg",
        "image_width": 800,
        "image_height": 600,
        "image_aspect_ratio": 1.3333,
        "image_mode": "RGB",
        "image_size_bytes": 50_000,
        "image_file_hash": "hash-image",
        "image_download_duration_ms": 125,
        "image_downloaded_at": "2026-07-27T12:00:00+00:00",
        "image_metadata": {
            "format": "JPEG",
            "width": 800,
            "height": 600
        }
    })
    return article


# Copie du cache

def test_copy_cached_image_data_copies_cache_fields() -> None:
    article = build_article(
        "article-2",
        "https://example.com/image.jpg"
    )
    cached_article = build_downloaded_article(
        "article-1",
        "https://example.com/image.jpg"
    )

    result = copy_cached_image_data(article, cached_article)

    for field in IMAGE_CACHE_FIELDS:
        if field in cached_article:
            assert result[field] == cached_article[field]


def test_copy_cached_image_data_preserves_article_fields() -> None:
    article = {
        "id": "article-2",
        "source": "Guardian",
        "title": "Titre du deuxième article",
        "text": "Contenu propre au deuxième article",
        "image_url": "https://example.com/image.jpg",
        "custom_field": "valeur"
    }
    cached_article = build_downloaded_article("article-1")

    result = copy_cached_image_data(article, cached_article)

    assert result["id"] == "article-2"
    assert result["source"] == "Guardian"
    assert result["title"] == "Titre du deuxième article"
    assert result["text"] == "Contenu propre au deuxième article"
    assert result["custom_field"] == "valeur"


def test_copy_cached_image_data_does_not_modify_original_article() -> None:
    article = build_article("article-2")
    original_article = article.copy()
    cached_article = build_downloaded_article("article-1")

    result = copy_cached_image_data(article, cached_article)

    assert article == original_article
    assert result is not article


def test_copy_cached_image_data_only_copies_declared_fields() -> None:
    article = build_article("article-2")
    cached_article = build_downloaded_article("article-1")
    cached_article["title"] = "Titre du premier article"
    cached_article["text"] = "Texte du premier article"
    cached_article["unknown_cache_field"] = "ne doit pas être copié"

    result = copy_cached_image_data(article, cached_article)

    assert result["title"] == "Article article-2"
    assert "text" not in result
    assert "unknown_cache_field" not in result


def test_copy_cached_image_data_ignores_missing_cache_fields() -> None:
    article = build_article("article-2")
    cached_article = {
        "image_path": "data/images/image.jpg"
    }

    result = copy_cached_image_data(
        article,
        cached_article
    )

    assert result["image_path"] == "data/images/image.jpg"
    assert "image_download_status" not in result
    assert "image_width" not in result    

def test_copy_cached_image_data_copies_nested_metadata_reference() -> None:
    article = build_article("article-2")
    cached_article = build_downloaded_article("article-1")

    result = copy_cached_image_data(article, cached_article)

    assert result["image_metadata"] == cached_article["image_metadata"]


# Résumé des statuts

def test_format_download_status_summary_returns_empty_summary() -> None:
    assert format_download_status_summary(Counter()) == "aucun"


def test_format_download_status_summary_formats_single_status() -> None:
    statuses = Counter({
        IMAGE_STATUS_DOWNLOADED: 3
    })

    result = format_download_status_summary(statuses)

    assert result == f"{IMAGE_STATUS_DOWNLOADED}=3"


def test_format_download_status_summary_formats_multiple_statuses() -> None:
    statuses = Counter({
        IMAGE_STATUS_DOWNLOADED: 5,
        IMAGE_STATUS_DUPLICATE_URL: 3,
        IMAGE_STATUS_MISSING_URL: 1
    })

    result = format_download_status_summary(statuses)

    assert result == (
        f"{IMAGE_STATUS_DOWNLOADED}=5, "
        f"{IMAGE_STATUS_DUPLICATE_URL}=3, "
        f"{IMAGE_STATUS_MISSING_URL}=1"
    )


def test_format_download_status_summary_uses_counter_order() -> None:
    statuses = Counter()
    statuses[IMAGE_STATUS_TIMEOUT] = 2
    statuses[IMAGE_STATUS_DOWNLOADED] = 4
    statuses[IMAGE_STATUS_MISSING_URL] = 1

    result = format_download_status_summary(statuses)

    assert result == (
        f"{IMAGE_STATUS_DOWNLOADED}=4, "
        f"{IMAGE_STATUS_TIMEOUT}=2, "
        f"{IMAGE_STATUS_MISSING_URL}=1"
    )


def test_format_download_status_summary_handles_empty_status() -> None:
    statuses = Counter({
        "": 2,
        IMAGE_STATUS_DOWNLOADED: 1
    })

    result = format_download_status_summary(statuses)

    assert result == (
        f"sans_statut=2, {IMAGE_STATUS_DOWNLOADED}=1"
    )


# Collection invalide

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "articles",
        42,
        object()
    ]
)
def test_download_images_for_articles_rejects_non_list_collection(
    monkeypatch: pytest.MonkeyPatch,
    articles: Any
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args, **kwargs: warnings.append(args)
    )
    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: pytest.fail(
            "Aucun téléchargement ne devait être lancé."
        )
    )

    result = download_images_for_articles(articles)

    assert result == []
    assert len(warnings) == 1
    assert "Collection invalide" in warnings[0][0]


def test_download_images_for_articles_handles_empty_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: messages.append(args)
    )
    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: pytest.fail(
            "Aucun téléchargement ne devait être lancé."
        )
    )

    result = download_images_for_articles([])

    assert result == []
    assert len(messages) == 1
    assert "aucun article" in messages[0][0].lower()


# Traitement nominal

def test_download_images_for_articles_downloads_each_unique_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/image-1.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/image-2.jpg"
        )
    ]
    calls: list[str] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(article["id"])
        return build_downloaded_article(
            article["id"],
            article["image_url"]
        )

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert calls == [
        "article-1",
        "article-2"
    ]
    assert len(result) == 2
    assert all(
        article["image_download_status"] == IMAGE_STATUS_DOWNLOADED
        for article in result
    )


def test_download_images_for_articles_preserves_order(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article("article-1", "https://example.com/1.jpg"),
        build_article("article-2", "https://example.com/2.jpg"),
        build_article("article-3", "https://example.com/3.jpg")
    ]

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: {
            **article,
            "image_path": f"images/{article['id']}.jpg",
            "image_download_status": IMAGE_STATUS_DOWNLOADED,
            "image_download_error": ""
        }
    )

    result = download_images_for_articles(articles)

    assert [
        article["id"]
        for article in result
    ] == [
        "article-1",
        "article-2",
        "article-3"
    ]


def test_download_images_for_articles_does_not_modify_input(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article("article-1")
    ]
    original_articles = [
        article.copy()
        for article in articles
    ]

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: {
            **article,
            "image_path": "images/article-1.jpg",
            "image_download_status": IMAGE_STATUS_DOWNLOADED,
            "image_download_error": ""
        }
    )

    download_images_for_articles(articles)

    assert articles == original_articles


# Articles invalides

def test_download_images_for_articles_skips_invalid_items(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles: list[Any] = [
        None,
        "article",
        42,
        [],
        build_article("article-1")
    ]
    calls: list[str] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(article["id"])
        return build_downloaded_article(article["id"])

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert calls == ["article-1"]
    assert len(result) == 1
    assert result[0]["id"] == "article-1"


def test_download_images_for_articles_skips_empty_download_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article("article-1"),
        build_article("article-2")
    ]

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        if article["id"] == "article-1":
            return {}

        return build_downloaded_article(article["id"])

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert len(result) == 1
    assert result[0]["id"] == "article-2"


# Cache et URL dupliquées

def test_download_images_for_articles_reuses_downloaded_image(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/image.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/image.jpg"
        )
    ]
    calls: list[str] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(article["id"])
        return build_downloaded_article(
            article["id"],
            article["image_url"]
        )

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert calls == ["article-1"]
    assert len(result) == 2

    first_article = result[0]
    duplicate_article = result[1]

    assert first_article["image_download_status"] == (
        IMAGE_STATUS_DOWNLOADED
    )
    assert duplicate_article["id"] == "article-2"
    assert duplicate_article["image_path"] == first_article["image_path"]
    assert duplicate_article["image_download_status"] == (
        IMAGE_STATUS_DUPLICATE_URL
    )
    assert duplicate_article["image_download_error"] == (
        "Image réutilisée depuis une URL déjà traitée dans ce lot."
    )
    assert duplicate_article["image_width"] == 800
    assert duplicate_article["image_height"] == 600


def test_download_images_for_articles_normalizes_duplicate_urls(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "HTTPS://EXAMPLE.COM/image.jpg#first"
        ),
        build_article(
            "article-2",
            "https://example.com/image.jpg#second"
        )
    ]
    calls: list[str] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(article["id"])
        return build_downloaded_article(
            article["id"],
            article["image_url"]
        )

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert calls == ["article-1"]
    assert result[1]["image_download_status"] == (
        IMAGE_STATUS_DUPLICATE_URL
    )


def test_download_images_for_articles_does_not_cache_empty_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article("article-1", ""),
        build_article("article-2", "")
    ]
    calls: list[str] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(article["id"])
        return {
            **article,
            "image_path": "",
            "image_download_status": IMAGE_STATUS_MISSING_URL,
            "image_download_error": ""
        }

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert calls == [
        "article-1",
        "article-2"
    ]
    assert len(result) == 2
    assert all(
        article["image_download_status"] == IMAGE_STATUS_MISSING_URL
        for article in result
    )


def test_download_images_for_articles_reuses_failed_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/unavailable.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/unavailable.jpg"
        )
    ]
    calls: list[str] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(article["id"])
        return {
            **article,
            "image_path": "",
            "image_download_status": IMAGE_STATUS_TIMEOUT,
            "image_download_error": "Délai dépassé."
        }

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert calls == ["article-1"]
    assert len(result) == 2
    assert result[0]["image_download_status"] == IMAGE_STATUS_TIMEOUT
    assert result[1]["image_download_status"] == IMAGE_STATUS_TIMEOUT
    assert result[1]["image_download_error"] == "Délai dépassé."


def test_download_images_for_articles_uses_not_requested_for_empty_cached_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/image.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/image.jpg"
        )
    ]

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: {
            **article,
            "image_path": "",
            "image_download_status": "",
            "image_download_error": ""
        }
    )

    summaries: list[Counter[str]] = []

    def fake_format_summary(
        statuses: Counter[str]
    ) -> str:
        summaries.append(statuses.copy())
        return "résumé"

    monkeypatch.setattr(
        module,
        "format_download_status_summary",
        fake_format_summary
    )

    result = download_images_for_articles(articles)

    assert len(result) == 2
    assert summaries[0][IMAGE_STATUS_NOT_REQUESTED] == 2


def test_download_images_for_articles_caches_copy_of_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/image.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/image.jpg"
        )
    ]

    first_result = build_downloaded_article("article-1")

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: first_result
    )

    result = download_images_for_articles(articles)

    assert len(result) == 2
    assert result[0] is first_result
    assert result[1] is not first_result


# Erreurs inattendues

def test_download_images_for_articles_continues_after_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/1.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/2.jpg"
        )
    ]

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        if article["id"] == "article-1":
            raise RuntimeError("Erreur inattendue.")

        return build_downloaded_article(
            article["id"],
            article["image_url"]
        )

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )

    result = download_images_for_articles(articles)

    assert len(result) == 1
    assert result[0]["id"] == "article-2"


def test_download_images_for_articles_logs_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    debug_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: (
            _ for _ in ()
        ).throw(RuntimeError("Erreur inattendue."))
    )
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda *args, **kwargs: debug_calls.append(
            (args, kwargs)
        )
    )

    result = download_images_for_articles([
        build_article("article-1")
    ])

    assert result == []
    assert len(debug_calls) == 1
    assert "Erreur inattendue" in debug_calls[0][0][0]
    assert debug_calls[0][1]["exc_info"] is True


# Statut absent

def test_download_images_for_articles_uses_not_requested_when_status_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_statuses: list[Counter[str]] = []

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: {
            **article,
            "image_path": ""
        }
    )

    def fake_format_summary(
        statuses: Counter[str]
    ) -> str:
        captured_statuses.append(statuses.copy())
        return "résumé"
    
    monkeypatch.setattr(
        module,
        "format_download_status_summary",
        fake_format_summary
    )

    result = download_images_for_articles([
        build_article("article-1")
    ])

    assert len(result) == 1
    assert captured_statuses[0][IMAGE_STATUS_NOT_REQUESTED] == 1


# Journal de synthèse

def test_download_images_for_articles_logs_complete_summary(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles: list[Any] = [
        build_article(
            "article-1",
            "https://example.com/image.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/image.jpg"
        ),
        None
    ]
    info_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: build_downloaded_article(
            article["id"],
            article["image_url"]
        )
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    result = download_images_for_articles(articles)

    assert len(result) == 2
    assert len(info_calls) == 1

    log_call = info_calls[0]

    assert "analysées=%s" in log_call[0]
    assert log_call[1] == 3
    assert log_call[2] == 2
    assert log_call[3] == 1
    assert log_call[4] == 2
    assert log_call[5] == 1
    assert log_call[6] == 1
    assert log_call[7] == 0

    assert f"{IMAGE_STATUS_DOWNLOADED}=1" in log_call[8]
    assert f"{IMAGE_STATUS_DUPLICATE_URL}=1" in log_call[8]
    assert f"{IMAGE_STATUS_INVALID_ARTICLE}=1" in log_call[8]


def test_download_images_for_articles_counts_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article("article-1"),
        build_article("article-2")
    ]
    info_calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: (
            _ for _ in ()
        ).throw(RuntimeError("Erreur."))
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    result = download_images_for_articles(articles)

    assert result == []
    assert info_calls[0][7] == 2
    assert (
        f"{IMAGE_STATUS_UNEXPECTED_ERROR}=2"
        in info_calls[0][8]
    )


def test_download_images_for_articles_counts_available_images(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        build_article(
            "article-1",
            "https://example.com/1.jpg"
        ),
        build_article(
            "article-2",
            "https://example.com/2.jpg"
        ),
        build_article(
            "article-3",
            "https://example.com/3.jpg"
        )
    ]
    info_calls: list[tuple[Any, ...]] = []

    def fake_download_image(
        article: dict[str, Any]
    ) -> dict[str, Any]:
        image_path = (
            f"images/{article['id']}.jpg"
            if article["id"] != "article-2"
            else ""
        )

        return {
            **article,
            "image_path": image_path,
            "image_download_status": (
                IMAGE_STATUS_DOWNLOADED
                if image_path
                else IMAGE_STATUS_TIMEOUT
            ),
            "image_download_error": (
                ""
                if image_path
                else "Délai dépassé."
            )
        }

    monkeypatch.setattr(
        module,
        "download_image",
        fake_download_image
    )
    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args, **kwargs: info_calls.append(args)
    )

    result = download_images_for_articles(articles)

    assert len(result) == 3
    assert info_calls[0][4] == 2
    assert info_calls[0][3] == 2


# Normalisation appelée pour chaque article valide

def test_download_images_for_articles_normalizes_each_valid_url(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles: list[Any] = [
        build_article(
            "article-1",
            "https://example.com/1.jpg"
        ),
        None,
        build_article(
            "article-2",
            "https://example.com/2.jpg"
        )
    ]
    normalized_urls: list[Any] = []

    def fake_normalize_image_url(value: Any) -> str:
        normalized_urls.append(value)
        return str(value or "").lower()

    monkeypatch.setattr(
        module,
        "normalize_image_url",
        fake_normalize_image_url
    )
    monkeypatch.setattr(
        module,
        "download_image",
        lambda article: {
            **article,
            "image_path": "",
            "image_download_status": IMAGE_STATUS_MISSING_URL,
            "image_download_error": ""
        }
    )

    download_images_for_articles(articles)

    assert normalized_urls == [
        "https://example.com/1.jpg",
        "https://example.com/2.jpg"
    ]