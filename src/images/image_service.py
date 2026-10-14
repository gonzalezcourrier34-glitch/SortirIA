"""Service d'orchestration du traitement des images des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/images/image_service.py` (commit 93632c5).

Ce module prend en charge :

- la préparation des articles avant téléchargement ;
- la conservation des images locales déjà valides ;
- la recherche d'images déjà téléchargées ;
- l'appel du téléchargeur uniquement lorsque nécessaire ;
- la validation finale des images locales ;
- l'application du paramètre require_image ;
- la journalisation du résumé du lot.

Les opérations HTTP sont déléguées à image_downloader.
La validation locale et l'enrichissement des métadonnées sont délégués
à image_validator.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.constants import (
    IMAGE_STATUS_ALREADY_AVAILABLE,
    IMAGE_STATUS_DOWNLOADED,
    IMAGE_STATUS_INVALID_RESULT,
    IMAGE_STATUS_LOCAL_VALID,
    IMAGE_STATUS_MISSING_URL,
    IMAGE_STATUS_DUPLICATE_URL,
    IMAGE_STATUS_NOT_REQUESTED,
    IMAGE_VALIDATION_STATUS_ERROR,
    IMAGE_VALIDATION_STATUS_INVALID,
    IMAGE_VALIDATION_STATUS_VALID
)
from config.paths import IMAGES_DIR
from src.article.article_utils import get_article_identifier
from src.images.image_downloader import (
    download_images_for_articles,
    normalize_article_id
)
from src.images.image_validator import (
    is_valid_image_path,
    validate_article_image
)
from src.logger import get_logger
from src.utils.path_utils import normalize_source_name

logger = get_logger(__name__)

SUCCESS_DOWNLOAD_STATUSES = {
    IMAGE_STATUS_ALREADY_AVAILABLE,
    IMAGE_STATUS_DOWNLOADED,
    IMAGE_STATUS_LOCAL_VALID,
    IMAGE_STATUS_DUPLICATE_URL
}


# Normalisation

def normalize_optional_text(value: Any) -> str:
    """Normalise une valeur textuelle facultative."""

    if not isinstance(value, (str, Path)):
        return ""

    try:
        return str(value).strip()
    except (OSError, TypeError, ValueError):
        return ""


def get_current_iso_datetime() -> str:
    """Retourne la date UTC courante au format ISO 8601."""

    return datetime.now(timezone.utc).isoformat()


def set_image_download_result(
    article: dict[str, Any],
    status: str,
    error: str = ""
) -> None:
    """Enregistre le résultat du téléchargement dans un article."""

    article["image_download_status"] = normalize_optional_text(status)
    article["image_download_error"] = normalize_optional_text(error)


def set_image_validation_result(
    article: dict[str, Any],
    status: str,
    error: str = ""
) -> None:
    """Enregistre le résultat de validation dans un article."""

    article["image_validation_status"] = normalize_optional_text(status)
    article["image_validation_error"] = normalize_optional_text(error)


def get_image_download_status(article: dict[str, Any]) -> str:
    """Retourne le statut de téléchargement normalisé d'un article."""

    return normalize_optional_text(article.get("image_download_status"))


def get_image_validation_status(article: dict[str, Any]) -> str:
    """Retourne le statut de validation normalisé d'un article."""

    return normalize_optional_text(article.get("image_validation_status"))


# Recherche locale

def find_existing_article_image(
    article: dict[str, Any],
    allowed_directories: Iterable[str | Path] | None = None
) -> Path | None:
    """Recherche une image locale valide déjà associée à un article."""

    if not isinstance(article, dict):
        return None

    article_id = normalize_article_id(article.get("id"))
    source = normalize_source_name(article.get("source", "unknown"))

    if not article_id or not source:
        return None

    target_directory = IMAGES_DIR / source

    try:
        if not target_directory.is_dir():
            return None

        candidates = sorted(
            target_directory.glob(f"{source}_{article_id}.*"),
            key=lambda candidate: candidate.name.lower()
        )

        for candidate in candidates:
            if is_valid_image_path(candidate, allowed_directories):
                return candidate

    except OSError as error:
        logger.debug(
            "Impossible de rechercher l'image locale de l'article [%s] : %s",
            article_id,
            error
        )

    return None


# Préparation

def prepare_articles_for_image_download(
    articles: list[Any],
    allowed_directories: Iterable[str | Path] | None = None
) -> list[dict[str, Any]]:
    """Prépare les articles avant le téléchargement de leurs images."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide pour la préparation des images : %s.",
            type(articles).__name__
        )
        return []

    if not articles:
        logger.info("Aucun article à préparer pour les images.")
        return []

    prepared_articles: list[dict[str, Any]] = []
    local_valid_count = 0
    invalid_path_count = 0
    missing_path_count = 0
    ignored_count = 0
    error_count = 0

    for article in articles:
        if not isinstance(article, dict):
            ignored_count += 1
            logger.debug(
                "Élément ignoré pendant la préparation des images : %s.",
                type(article).__name__
            )
            continue

        prepared_article = article.copy()
        article_identifier = get_article_identifier(prepared_article)
        image_path = normalize_optional_text(
            prepared_article.get("image_path")
        )

        if not image_path:
            existing_image_path = find_existing_article_image(
                prepared_article,
                allowed_directories
            )
            image_path = (
                str(existing_image_path)
                if existing_image_path
                else ""
            )

        prepared_article["image_path"] = image_path
        prepared_article["image_is_valid"] = False
        set_image_validation_result(prepared_article, "")

        if not image_path:
            missing_path_count += 1
            set_image_download_result(
                prepared_article,
                IMAGE_STATUS_NOT_REQUESTED
            )
            prepared_articles.append(prepared_article)
            continue

        try:
            local_image_is_valid = is_valid_image_path(
                image_path,
                allowed_directories
            )
        except Exception as error:
            error_count += 1
            local_image_is_valid = False
            logger.exception(
                "Erreur inattendue pendant la vérification de l'image locale "
                "de l'article [%s] : %s",
                article_identifier,
                error
            )

        if local_image_is_valid:
            local_valid_count += 1
            prepared_article["image_is_valid"] = True
            set_image_download_result(
                prepared_article,
                IMAGE_STATUS_LOCAL_VALID
            )
            set_image_validation_result(
                prepared_article,
                IMAGE_VALIDATION_STATUS_VALID
            )
            prepared_articles.append(prepared_article)
            continue

        invalid_path_count += 1
        prepared_article["image_path"] = ""
        prepared_article["image_is_valid"] = False
        set_image_download_result(
            prepared_article,
            IMAGE_STATUS_NOT_REQUESTED,
            "Le chemin d'image locale est invalide."
        )
        set_image_validation_result(
            prepared_article,
            IMAGE_VALIDATION_STATUS_INVALID,
            "Le chemin d'image locale est invalide."
        )
        prepared_articles.append(prepared_article)

    logger.debug(
        "%s article(s) préparé(s) : %s image(s) locale(s) valide(s), "
        "%s chemin(s) invalide(s), %s chemin(s) absent(s).",
        len(prepared_articles),
        local_valid_count,
        invalid_path_count,
        missing_path_count
    )

    if ignored_count:
        logger.warning(
            "%s élément(s) ignoré(s) pendant la préparation des images.",
            ignored_count
        )

    if error_count:
        logger.warning(
            "%s erreur(s) inattendue(s) pendant la préparation des images.",
            error_count
        )

    return prepared_articles


# Téléchargement

def download_articles_safely(
    articles: list[Any]
) -> list[dict[str, Any]]:
    """Télécharge les images avec isolation des erreurs."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide pour le téléchargement des images : %s.",
            type(articles).__name__
        )
        return []

    if not articles:
        logger.info("Aucun article à traiter pour le téléchargement.")
        return []

    downloadable_articles: list[dict[str, Any]] = []
    preserved_articles: list[dict[str, Any]] = []
    position_field = "__image_service_position__"
    local_count = 0
    missing_url_count = 0
    ignored_count = 0

    for position, article in enumerate(articles):
        if not isinstance(article, dict):
            ignored_count += 1
            logger.debug(
                "Élément ignoré pendant le téléchargement : %s.",
                type(article).__name__
            )
            continue

        prepared_article = article.copy()
        prepared_article[position_field] = position

        if prepared_article.get("image_is_valid") is True:
            local_count += 1
            set_image_download_result(
                prepared_article,
                IMAGE_STATUS_LOCAL_VALID
            )
            preserved_articles.append(prepared_article)
            continue

        image_url = normalize_optional_text(
            prepared_article.get("image_url")
        )
        prepared_article["image_url"] = image_url

        if not image_url:
            missing_url_count += 1
            prepared_article["image_path"] = ""
            prepared_article["image_is_valid"] = False
            set_image_download_result(
                prepared_article,
                IMAGE_STATUS_MISSING_URL,
                "Aucune URL d'image distante n'est disponible."
            )
            preserved_articles.append(prepared_article)
            continue

        downloadable_articles.append(prepared_article)

    downloaded_articles: list[dict[str, Any]] = []

    if downloadable_articles:
        try:
            results = download_images_for_articles(
                downloadable_articles
            )
        except Exception as error:
            logger.exception(
                "Erreur inattendue pendant le téléchargement "
                "du lot d'images : %s",
                error
            )
            results = []

        if len(results) != len(downloadable_articles):
            logger.warning(
                "Le téléchargeur a retourné %s résultat(s) "
                "pour %s article(s).",
                len(results),
                len(downloadable_articles)
            )

        for index, original_article in enumerate(downloadable_articles):
            downloaded_article = (
                results[index].copy()
                if index < len(results)
                and isinstance(results[index], dict)
                else original_article.copy()
            )

            downloaded_article.setdefault(
                position_field,
                original_article.get(position_field, index)
            )

            if not get_image_download_status(downloaded_article):
                downloaded_article["image_path"] = ""
                set_image_download_result(
                    downloaded_article,
                    IMAGE_STATUS_INVALID_RESULT,
                    "Le téléchargeur n'a retourné aucun statut exploitable."
                )

            downloaded_article["image_is_valid"] = False
            downloaded_articles.append(downloaded_article)

    combined_articles = preserved_articles + downloaded_articles
    combined_articles.sort(
        key=lambda article: int(article.get(position_field, 0))
    )

    for article in combined_articles:
        article.pop(position_field, None)

    status_counts = Counter(
        get_image_download_status(article) or "unknown"
        for article in combined_articles
    )
    failed_count = sum(
        count
        for status, count in status_counts.items()
        if status not in SUCCESS_DOWNLOAD_STATUSES
        and status != IMAGE_STATUS_MISSING_URL
    )

    logger.debug(
        "%s article(s) traité(s) : %s image(s) locale(s) conservée(s), "
        "%s téléchargement(s) demandé(s), %s URL absente(s), %s échec(s).",
        len(combined_articles),
        local_count,
        len(downloadable_articles),
        missing_url_count,
        failed_count
    )

    if ignored_count:
        logger.warning(
            "%s élément(s) ignoré(s) pendant le téléchargement.",
            ignored_count
        )

    return combined_articles


# Validation

def validate_downloaded_articles(
    articles: list[Any],
    require_image: bool,
    allowed_directories: Iterable[str | Path] | None = None
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Valide les images puis applique le mode strict ou tolérant."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide pour la validation des images : %s.",
            type(articles).__name__
        )
        return [], 0, 0, 0

    processed_articles: list[dict[str, Any]] = []
    valid_count = 0
    invalid_count = 0
    ignored_count = 0

    for article in articles:
        if not isinstance(article, dict):
            ignored_count += 1
            logger.debug(
                "Élément ignoré après téléchargement : %s.",
                type(article).__name__
            )
            continue

        processed_article = article.copy()
        article_identifier = get_article_identifier(processed_article)

        try:
            has_valid_image = validate_article_image(
                processed_article,
                allowed_directories
            )
        except Exception as error:
            has_valid_image = False
            set_image_validation_result(
                processed_article,
                IMAGE_VALIDATION_STATUS_ERROR,
                f"{type(error).__name__}: {error}"
            )
            logger.exception(
                "Impossible de valider l'image de l'article [%s] : %s",
                article_identifier,
                error
            )

        processed_article["image_is_valid"] = has_valid_image
        processed_article["image_validated_at"] = (
            get_current_iso_datetime()
        )

        if has_valid_image:
            valid_count += 1
            set_image_validation_result(
                processed_article,
                IMAGE_VALIDATION_STATUS_VALID
            )
            processed_articles.append(processed_article)
            continue

        invalid_count += 1

        if (
            get_image_validation_status(processed_article)
            != IMAGE_VALIDATION_STATUS_ERROR
        ):
            set_image_validation_result(
                processed_article,
                IMAGE_VALIDATION_STATUS_INVALID,
                "L'image locale est absente ou invalide."
            )

        if require_image:
            continue

        processed_article["image_path"] = ""
        processed_articles.append(processed_article)

    return (
        processed_articles,
        valid_count,
        invalid_count,
        ignored_count
    )


# Journalisation

def log_image_batch_summary(
    input_count: int,
    prepared_count: int,
    downloaded_articles: list[dict[str, Any]],
    output_count: int,
    valid_count: int,
    invalid_count: int,
    ignored_count: int,
    require_image: bool
) -> None:
    """Journalise un résumé détaillé du traitement du lot."""

    download_status_counts = Counter(
        get_image_download_status(article) or "unknown"
        for article in downloaded_articles
        if isinstance(article, dict)
    )

    logger.info(
        "Résumé du lot d'images : %s reçu(s), %s préparé(s), %s conservé(s), "
        "%s image(s) valide(s), %s image(s) invalide(s).",
        input_count,
        prepared_count,
        output_count,
        valid_count,
        invalid_count
    )

    if download_status_counts:
        logger.info(
            "Statuts de téléchargement du lot : %s.",
            ", ".join(
                f"{status}={count}"
                for status, count in sorted(
                    download_status_counts.items()
                )
            )
        )

    if ignored_count:
        logger.warning(
            "%s élément(s) invalide(s) ignoré(s) pendant la validation.",
            ignored_count
        )

    if require_image and invalid_count:
        logger.info(
            "%s article(s) rejeté(s), car une image valide "
            "était obligatoire.",
            invalid_count
        )


# Orchestration

def process_article_images(
    articles: list[Any],
    require_image: bool = False,
    allowed_directories: Iterable[str | Path] | None = None
) -> list[dict[str, Any]]:
    """Prépare, télécharge, valide et filtre les images des articles."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide fournie au service d'images : %s.",
            type(articles).__name__
        )
        return []

    if not articles:
        logger.info("Aucun article fourni au service d'images.")
        return []

    prepared_articles = prepare_articles_for_image_download(
        articles,
        allowed_directories
    )

    if not prepared_articles:
        logger.warning(
            "Aucun article exploitable après préparation des images."
        )
        return []

    downloaded_articles = download_articles_safely(
        prepared_articles
    )

    if not downloaded_articles:
        logger.warning(
            "Aucun article exploitable après téléchargement des images."
        )
        return []

    processed_articles, valid_count, invalid_count, ignored_count = (
        validate_downloaded_articles(
            downloaded_articles,
            require_image=bool(require_image),
            allowed_directories=allowed_directories
        )
    )

    log_image_batch_summary(
        input_count=len(articles),
        prepared_count=len(prepared_articles),
        downloaded_articles=downloaded_articles,
        output_count=len(processed_articles),
        valid_count=valid_count,
        invalid_count=invalid_count,
        ignored_count=ignored_count,
        require_image=bool(require_image)
    )

    return processed_articles