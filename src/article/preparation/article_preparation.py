"""Service de préparation métier des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/preparation/article_preparation.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from src.article.processing.article_cleaner import clean_articles
from src.article.processing.article_deduplicator import deduplicate_articles
from src.article.preparation.article_preparation_models import (
    ArticlePreparationReport,
    ArticlePreparationResult
)
from src.article.preparation.article_preparation_profiles import (
    PreparationPolicyName,
    get_validation_policy
)
from src.article.schema.article_schema_normalizer import normalize_articles_schema
from src.article.validation.article_validator import validate_article_with_reason
from src.logger import get_logger

logger = get_logger(__name__)


# Regroupement

def group_articles_by_source(
    articles: list[Any]
) -> dict[str, list[dict[str, Any]]]:
    """Regroupe des articles déjà préparés par source."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection d'articles invalide : %s.",
            type(articles).__name__
        )
        return {}

    grouped_articles: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    ignored_count = 0

    for article in articles:
        if not isinstance(article, Mapping):
            ignored_count += 1
            continue

        normalized_article = dict(article)
        source = normalized_article.get("source")

        if not isinstance(source, str) or not source.strip():
            ignored_count += 1
            continue

        grouped_articles[source].append(normalized_article)

    logger.debug(
        "%s source(s) détectée(s) pour %s article(s).",
        len(grouped_articles),
        sum(len(items) for items in grouped_articles.values())
    )

    if ignored_count:
        logger.warning(
            "%s élément(s) ignoré(s) pendant le regroupement.",
            ignored_count
        )

    return dict(grouped_articles)


# Validation

def validate_prepared_articles(
    articles: list[dict[str, Any]],
    validation_policy: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Valide les articles préparés et agrège les motifs de rejet."""

    valid_articles: list[dict[str, Any]] = []
    rejection_reasons: Counter[str] = Counter()

    for article in articles:
        is_valid, reason = validate_article_with_reason(
            article,
            validation_policy
        )

        if is_valid:
            valid_articles.append(article)
        else:
            rejection_reasons[reason or "raison_inconnue"] += 1

    return valid_articles, rejection_reasons


# Préparation métier

def prepare_articles(
    articles: list[Any],
    policy_name: PreparationPolicyName = "acquisition",
    remove_duplicates: bool = True,
    validation_overrides: Mapping[str, Any] | None = None
) -> ArticlePreparationResult:
    """Normalise, nettoie, déduplique puis valide les articles."""

    report = ArticlePreparationReport()

    if not isinstance(articles, list):
        logger.warning(
            "Collection d'articles invalide : %s.",
            type(articles).__name__
        )
        return ArticlePreparationResult([], report)

    report.received = len(articles)

    if not articles:
        logger.warning("Aucun article fourni au service métier.")
        return ArticlePreparationResult([], report)

    validation_policy = get_validation_policy(
        policy_name,
        validation_overrides
    )

    # Normalisation

    normalized_articles, ignored_count = normalize_articles_schema(articles)
    report.normalized = len(normalized_articles)
    report.normalization_ignored = ignored_count

    if not normalized_articles:
        logger.warning("Aucun article exploitable après normalisation.")
        return ArticlePreparationResult([], report)

    # Nettoyage

    cleaned_articles = clean_articles(normalized_articles)
    report.cleaned = len(cleaned_articles)
    report.cleaning_ignored = max(
        report.normalized - report.cleaned,
        0
    )

    if not cleaned_articles:
        logger.warning("Aucun article exploitable après nettoyage.")
        return ArticlePreparationResult([], report)

    # Déduplication

    if remove_duplicates:
        initial_count = len(cleaned_articles)
        prepared_articles = deduplicate_articles(cleaned_articles)
        report.duplicates = initial_count - len(prepared_articles)
    else:
        prepared_articles = cleaned_articles

    # Validation

    valid_articles, rejection_reasons = validate_prepared_articles(
        prepared_articles,
        validation_policy
    )

    report.invalid = sum(rejection_reasons.values())
    report.kept = len(valid_articles)
    report.rejection_reasons = rejection_reasons

    logger.info(
        "Préparation terminée : reçus=%s, normalisés=%s, nettoyés=%s, "
        "doublons=%s, invalides=%s, conservés=%s.",
        report.received,
        report.normalized,
        report.cleaned,
        report.duplicates,
        report.invalid,
        report.kept
    )

    if report.normalization_ignored:
        logger.warning(
            "%s élément(s) ignoré(s) pendant la normalisation.",
            report.normalization_ignored
        )

    if report.cleaning_ignored:
        logger.warning(
            "%s article(s) ignoré(s) pendant le nettoyage.",
            report.cleaning_ignored
        )

    if report.rejection_reasons:
        logger.info(
            "Motifs de rejet des articles : %s.",
            dict(report.rejection_reasons)
        )

    return ArticlePreparationResult(valid_articles, report)
