"""Pipeline local RSS de CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/pipelines/rss_pipeline.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.article.preparation.article_preparation import prepare_articles
from src.article.validation.article_validator import validate_articles
from src.extractors.rss.rss_extractor import extract_all_articles
from src.images.image_service import process_article_images
from src.logger import get_logger
from src.storage.files.storage_service import save_articles

logger = get_logger(__name__)


# Utilitaires communs

def stop_pipeline(message: str) -> list[dict[str, Any]]:
    """Journalise l'arrêt du pipeline RSS et retourne une liste vide."""

    normalized_message = str(message or "raison inconnue").strip()
    logger.warning("Pipeline RSS arrêté : %s.", normalized_message)
    return []


def require_article_list(articles: Any, stage: str) -> list[Any]:
    """Vérifie qu'une étape a retourné une liste d'articles."""

    if not isinstance(articles, list):
        raise TypeError(
            f"{stage} : une liste était attendue, "
            f"pas {type(articles).__name__}."
        )

    return articles


# Extraction RSS

def extract_rss_articles() -> list[Any]:
    """Extrait les articles depuis les flux RSS configurés."""

    return require_article_list(
        extract_all_articles(),
        "Extraction RSS"
    )


# Préparation métier

def prepare_rss_articles(
    articles: list[Any]
) -> list[dict[str, Any]]:
    """Normalise, nettoie, déduplique et valide les articles RSS."""

    articles = require_article_list(articles, "Préparation RSS")

    if not articles:
        logger.info("Aucun article RSS à préparer.")
        return []

    result = prepare_articles(
        articles,
        policy_name="acquisition",
        remove_duplicates=True
    )
    report = result.report

    logger.info(
        "Préparation RSS : reçus=%s, normalisés=%s, nettoyés=%s, "
        "doublons=%s, invalides=%s, conservés=%s.",
        report.received,
        report.normalized,
        report.cleaned,
        report.duplicates,
        report.invalid,
        report.kept
    )

    if report.rejection_reasons:
        logger.info(
            "Motifs de rejet pendant la préparation RSS : %s.",
            dict(report.rejection_reasons)
        )

    return result.articles


# Traitement des images

def process_rss_images(
    articles: list[Any],
    require_image: bool,
    allowed_directories: Iterable[str | Path] | None = None
) -> list[dict[str, Any]]:
    """Télécharge et valide les images des articles RSS."""

    articles = require_article_list(articles, "Traitement des images RSS")

    if not articles:
        logger.info("Aucun article RSS à traiter pour les images.")
        return []

    normalized_directories = (
        tuple(allowed_directories)
        if allowed_directories is not None
        else None
    )

    return process_article_images(
        articles,
        require_image=require_image,
        allowed_directories=normalized_directories
    )


# Validation finale

def finalize_rss_articles(
    articles: list[Any],
    require_image: bool
) -> list[dict[str, Any]]:
    """Applique la validation métier finale aux articles RSS."""

    articles = require_article_list(articles, "Validation finale RSS")

    if not articles:
        logger.info("Aucun article RSS à valider.")
        return []

    validated_articles = validate_articles(
        articles,
        require_image=require_image
    )

    logger.info(
        "%s article(s) RSS valide(s) après traitement des images.",
        len(validated_articles)
    )

    return validated_articles


# Stockage

def store_rss_articles(
    articles: list[Any],
    save_json: bool = True,
    save_csv: bool = True
) -> bool:
    """Enregistre les articles RSS dans les formats demandés."""

    articles = require_article_list(articles, "Stockage RSS")

    if not articles:
        logger.info("Aucun article RSS à stocker.")
        return False

    if not save_json and not save_csv:
        logger.warning("Aucun format de stockage RSS n'est activé.")
        return False

    return save_articles(
        articles,
        save_json=save_json,
        save_csv=save_csv
    )


# Pipeline principal

def run_rss_pipeline(
    require_image: bool = False,
    allowed_directories: Iterable[str | Path] | None = None,
    save_json: bool = True,
    save_csv: bool = True
) -> list[dict[str, Any]]:
    """Exécute localement le pipeline complet des articles RSS."""

    logger.info("Démarrage du pipeline local RSS.")

    try:
        articles = extract_rss_articles()

        if not articles:
            return stop_pipeline("aucun article extrait")

        logger.info("%s article(s) RSS extrait(s).", len(articles))

        articles = prepare_rss_articles(articles)

        if not articles:
            return stop_pipeline("aucun article exploitable après préparation")

        articles = process_rss_images(
            articles,
            require_image=require_image,
            allowed_directories=allowed_directories
        )

        if not articles:
            return stop_pipeline(
                "aucune image valide"
                if require_image
                else "aucun article après traitement des images"
            )

        articles = finalize_rss_articles(
            articles,
            require_image=require_image
        )

        if not articles:
            return stop_pipeline("aucun article après validation finale")

        if not store_rss_articles(
            articles,
            save_json=save_json,
            save_csv=save_csv
        ):
            logger.error(
                "Pipeline RSS terminé avec une erreur de stockage."
            )
            return articles

    except Exception as error:
        logger.exception(
            "Erreur inattendue pendant le pipeline RSS : %s",
            error
        )
        return []

    logger.info(
        "Pipeline local RSS terminé avec succès : "
        "%s article(s) sauvegardé(s).",
        len(articles)
    )

    return articles


if __name__ == "__main__":
    run_rss_pipeline()