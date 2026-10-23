"""Façade d'exécution des extracteurs.

Lance un extracteur sans bloquer les suivants, journalise son bilan sur une ligne
et renvoie ses articles avec un rapport `ExtractorExecution`.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/core/extractor_runner.py` (commit 93632c5).
"""

from __future__ import annotations

from typing import Any

from src.extractors.core.extractor_executor import execute_extractor_call
from src.extractors.core.extractor_models import ExtractorExecution
from src.extractors.core.extractor_results import ArticleExtractor, ExtractorResult
from src.logger import get_logger

logger = get_logger(__name__)


# Conversion

def build_execution_report(result: ExtractorResult) -> ExtractorExecution:
    """Convertit un ExtractorResult en rapport d'exécution.

    Parameters
    ----------
    result : ExtractorResult
        Le résultat d'extraction à résumer.

    Returns
    -------
    ExtractorExecution
        Le rapport figé, sans les articles.
    """

    return ExtractorExecution(
        extractor_name=result.name,
        source_type=result.source_type,
        status=result.status,
        article_count=result.extracted_count,
        duration_seconds=result.duration_seconds,
        message=result.message,
        errors=tuple(result.errors),
        analyzed_count=result.analyzed_count,
        rejected_count=result.rejected_count,
        requests_count=result.requests_count,
        main_reason=result.main_reason,
        rejection_reasons=dict(result.rejection_reasons),
        metadata=dict(result.metadata)
    )


def copy_articles(
    articles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retourne une copie superficielle de chaque article.

    Parameters
    ----------
    articles : list[dict[str, Any]]
        Les articles à copier.

    Returns
    -------
    list[dict[str, Any]]
        Une nouvelle liste de copies des dictionnaires.
    """

    return [article.copy() for article in articles]


# Exécution

def run_extractor(
    extractor_name: str,
    source_type: str,
    extractor: ArticleExtractor
) -> tuple[list[dict[str, Any]], ExtractorExecution]:
    """Exécute un extracteur sans bloquer les suivants.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, repris dans les journaux et le rapport.
    source_type : str
        Type de source (api, dataset, scraping...).
    extractor : ArticleExtractor
        Fonction sans argument qui renvoie les articles ou un `ExtractorResult`.

    Returns
    -------
    tuple[list[dict[str, Any]], ExtractorExecution]
        Les copies des articles extraits, et le rapport d'exécution.
    """

    # Lancer l'extracteur ; une erreur devient un résultat en échec
    logger.info("Démarrage de l'extracteur %s.", extractor_name)

    result = execute_extractor_call(
        extractor_name=extractor_name,
        source_type=source_type,
        extraction_function=extractor
    )
    execution = build_execution_report(result)

    # Journaliser le bilan de l'extraction sur une seule ligne
    logger.info(
        "%s | statut=%s | analysés=%s | extraits=%s | rejetés=%s | "
        "requêtes=%s | temps=%.3fs | réussite=%.2f%% | raison=%s%s",
        result.name,
        result.status,
        result.analyzed_count,
        result.extracted_count,
        result.rejected_count,
        result.requests_count,
        result.duration_seconds,
        result.success_rate,
        result.main_reason or "-",
        f" | message={result.message}" if result.message else ""
    )

    return copy_articles(result.articles), execution