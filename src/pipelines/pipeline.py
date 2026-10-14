"""Pipeline principal d'acquisition multimodale CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/pipelines/pipeline.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from src.article.preparation.article_preparation_profiles import PreparationPolicyName
from src.article.preparation.article_preparation import prepare_articles
from src.article.validation.article_validator import validate_articles
from src.extractor_execution_service import extract_all_sources
from src.images.image_service import process_article_images
from src.logger import get_logger
from src.storage.files.storage_service import save_articles

logger = get_logger(__name__)

# Configuration du pipeline

PREPARATION_POLICIES: tuple[PreparationPolicyName, ...] = (
    "acquisition",
    "labeled_reference",
    "multimodal_reference"
)
DEFAULT_PREPARATION_POLICY: PreparationPolicyName = "acquisition"


# Utilitaires communs

def stop_pipeline(message: str) -> list[dict[str, Any]]:
    """Journalise l'arrêt du pipeline et retourne une liste vide."""

    normalized_message = str(message or "raison inconnue").strip()
    logger.warning("Pipeline arrêté : %s.", normalized_message)
    return []


def require_article_list(articles: Any, stage: str) -> list[Any]:
    """Vérifie le format d'une collection reçue par une étape."""

    if not isinstance(articles, list):
        raise TypeError(
            f"{stage} : articles doit être une liste, "
            f"pas {type(articles).__name__}."
        )

    return articles


def get_article_policy(article: Any) -> PreparationPolicyName:
    """Retourne la politique correspondant au rôle de l'article."""

    role = (
        str(article.get("role", "") or "").strip().casefold()
        if isinstance(article, Mapping)
        else ""
    )

    return (
        role
        if role in PREPARATION_POLICIES
        else DEFAULT_PREPARATION_POLICY
    )


def group_articles_by_policy(
    articles: list[Any]
) -> dict[PreparationPolicyName, list[Any]]:
    """Regroupe les articles selon leur rôle métier."""

    grouped_articles: dict[PreparationPolicyName, list[Any]] = {
        policy_name: [] for policy_name in PREPARATION_POLICIES
    }

    for article in articles:
        grouped_articles[get_article_policy(article)].append(article)

    return grouped_articles


# Préparation

def prepare_extracted_articles(
    articles: list[Any],
    remove_duplicates: bool = True
) -> list[dict[str, Any]]:
    """Prépare chaque article avec la politique associée à son rôle."""

    articles = require_article_list(articles, "Préparation")

    if not articles:
        logger.info("Aucun article à préparer.")
        return []

    prepared_articles: list[dict[str, Any]] = []

    for policy_name, policy_articles in group_articles_by_policy(articles).items():
        if not policy_articles:
            continue

        result = prepare_articles(
            policy_articles,
            policy_name=policy_name,
            remove_duplicates=remove_duplicates
        )
        report = result.report
        prepared_articles.extend(result.articles)

        logger.info(
            "Préparation %s : reçus=%s, normalisés=%s, nettoyés=%s, "
            "doublons=%s, invalides=%s, conservés=%s.",
            policy_name,
            report.received,
            report.normalized,
            report.cleaned,
            report.duplicates,
            report.invalid,
            report.kept
        )

        if report.rejection_reasons:
            logger.info(
                "Motifs de rejet %s : %s.",
                policy_name,
                dict(report.rejection_reasons)
            )

    logger.info(
        "%s article(s) préparé(s) avant le traitement des images.",
        len(prepared_articles)
    )

    return prepared_articles


# Images

def process_images_stage(
    articles: list[Any],
    require_image: bool,
    allowed_directories: Iterable[str | Path] | None = None
) -> list[dict[str, Any]]:
    """Télécharge et valide les images disponibles."""

    articles = require_article_list(articles, "Traitement des images")

    if not articles:
        logger.info("Aucun article fourni à l'étape des images.")
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

def finalize_articles(
    articles: list[Any],
    require_image: bool
) -> list[dict[str, Any]]:
    """Applique la validation finale après le traitement des images."""

    articles = require_article_list(articles, "Validation finale")

    if not articles:
        logger.info("Aucun article à valider après les images.")
        return []

    final_articles = validate_articles(
        articles,
        require_image=require_image
    )

    logger.info(
        "%s article(s) valide(s) à la fin du pipeline.",
        len(final_articles)
    )

    return final_articles


# Stockage

def store_articles_stage(
    articles: list[Any],
    save_json: bool = True,
    save_csv: bool = True
) -> bool:
    """Enregistre les articles dans les formats demandés."""

    articles = require_article_list(articles, "Stockage")

    if not articles:
        logger.info("Aucun article fourni à l'étape de stockage.")
        return False

    if not save_json and not save_csv:
        logger.warning("Aucun format de stockage n'est activé.")
        return False

    return save_articles(
        articles,
        save_json=save_json,
        save_csv=save_csv
    )


# Pipeline principal

def run_pipeline(
    extractor_names: Iterable[str] | None = None,
    require_image: bool = False,
    allowed_directories: Iterable[str | Path] | None = None,
    save_json: bool = True,
    save_csv: bool = True,
    fail_on_extractor_error: bool = False
) -> list[dict[str, Any]]:
    """Exécute localement le pipeline complet d'acquisition."""

    logger.info("Démarrage du pipeline CheckIt.AI.")

    try:
        extraction_result = extract_all_sources(
            extractor_names=extractor_names,
            fail_if_empty=False,
            fail_on_extractor_error=fail_on_extractor_error
        )
        articles = extraction_result.articles

        if not articles:
            return stop_pipeline("aucun article extrait")

        logger.info(
            "%s article(s) extrait(s) avant préparation.",
            len(articles)
        )

        articles = prepare_extracted_articles(
            articles,
            remove_duplicates=True
        )

        if not articles:
            return stop_pipeline("aucun article après préparation")

        articles = process_images_stage(
            articles,
            require_image=require_image,
            allowed_directories=allowed_directories
        )

        if not articles:
            return stop_pipeline("aucun article après traitement des images")

        articles = finalize_articles(
            articles,
            require_image=require_image
        )

        if not articles:
            return stop_pipeline("aucun article après validation finale")

        if not store_articles_stage(
            articles,
            save_json=save_json,
            save_csv=save_csv
        ):
            logger.error("Pipeline terminé avec une erreur de stockage.")
            return articles

    except Exception as error:
        logger.exception(
            "Erreur inattendue pendant le pipeline principal : %s",
            error
        )
        return []

    logger.info(
        "Pipeline terminé avec succès : %s article(s) sauvegardé(s).",
        len(articles)
    )

    return articles


if __name__ == "__main__":
    run_pipeline()