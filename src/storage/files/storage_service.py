"""Service centralisé et observable de stockage des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/storage/files/storage_service.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from src.logger import get_logger
from src.storage.files.csv_storage import CsvStorageReport, save_articles as save_articles_to_csv
from src.storage.files.json_storage import JsonStorageReport, save_articles as save_articles_to_json

logger = get_logger(__name__)

StorageStatus = Literal["success", "partial_success", "failed"]


class StorageReportProtocol(Protocol):
    """Contrat minimal attendu d'un module de stockage."""

    success: bool
    errors: list[str]


StorageFunction = Callable[[list[Any]], StorageReportProtocol]


@dataclass(slots=True)
class StorageFormatResult:
    """Résultat normalisé d'un format de stockage."""

    format: str
    requested: bool = False
    success: bool = False
    report: JsonStorageReport | CsvStorageReport | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StorageServiceReport:
    """Rapport global du service de stockage."""

    status: StorageStatus = "failed"
    success: bool = False
    partial_success: bool = False
    received: int = 0
    valid: int = 0
    ignored: int = 0
    json: StorageFormatResult = field(
        default_factory=lambda: StorageFormatResult(format="json")
    )
    csv: StorageFormatResult = field(
        default_factory=lambda: StorageFormatResult(format="csv")
    )
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success

    @property
    def json_success(self) -> bool:
        return self.json.requested and self.json.success

    @property
    def csv_success(self) -> bool:
        return self.csv.requested and self.csv.success


# Filtrage

def filter_article_dictionaries(
    articles: list[Any]
) -> tuple[list[dict[str, Any]], int]:
    """Conserve uniquement les articles représentés par des dictionnaires."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide fournie au stockage : %s.",
            type(articles).__name__
        )
        return [], 0

    valid_articles = [
        dict(article)
        for article in articles
        if isinstance(article, Mapping)
    ]
    ignored_count = len(articles) - len(valid_articles)

    if ignored_count:
        logger.warning("%s élément(s) ignoré(s) avant le stockage.", ignored_count)

    return valid_articles, ignored_count


# Exécution indépendante

def save_with_storage(
    articles: list[dict[str, Any]],
    storage_format: str,
    storage_function: StorageFunction
) -> StorageFormatResult:
    """Exécute un stockage et normalise son résultat sans masquer les autres."""

    result = StorageFormatResult(format=storage_format, requested=True)

    if not articles:
        result.errors.append(f"Aucun article fourni pour la sauvegarde {storage_format.upper()}.")
        return result

    if not callable(storage_function):
        result.errors.append(f"Fonction de stockage {storage_format.upper()} invalide.")
        return result

    logger.info(
        "Début de la sauvegarde %s de %s article(s).",
        storage_format.upper(),
        len(articles)
    )

    try:
        storage_report = storage_function([article.copy() for article in articles])
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        result.errors.append(message)
        logger.exception("Erreur pendant la sauvegarde %s.", storage_format.upper())
        return result

    if not hasattr(storage_report, "success") or not isinstance(storage_report.success, bool):
        message = (
            f"Le stockage {storage_format.upper()} a retourné un rapport invalide : "
            f"{type(storage_report).__name__}."
        )
        result.errors.append(message)
        logger.error("%s", message)
        return result

    result.report = storage_report
    result.success = storage_report.success
    result.errors.extend(
        str(error).strip()
        for error in getattr(storage_report, "errors", [])
        if str(error).strip()
    )

    if result.success:
        logger.info("Sauvegarde %s terminée avec succès.", storage_format.upper())
    else:
        logger.error("La sauvegarde %s a échoué.", storage_format.upper())

    return result


def save_json_articles(articles: list[dict[str, Any]]) -> StorageFormatResult:
    """Sauvegarde indépendamment les articles au format JSON."""

    return save_with_storage(articles, "json", save_articles_to_json)


def save_csv_articles(articles: list[dict[str, Any]]) -> StorageFormatResult:
    """Sauvegarde indépendamment les articles au format CSV."""

    return save_with_storage(articles, "csv", save_articles_to_csv)


# Rapport global

def finalize_storage_report(report: StorageServiceReport) -> StorageServiceReport:
    """Calcule le statut global à partir des formats demandés."""

    requested_results = [
        result
        for result in (report.json, report.csv)
        if result.requested
    ]
    successful_count = sum(result.success for result in requested_results)

    report.errors = [
        f"{result.format.upper()}: {error}"
        for result in requested_results
        for error in result.errors
    ]

    if requested_results and successful_count == len(requested_results):
        report.status = "success"
        report.success = True
        report.partial_success = False
    elif successful_count:
        report.status = "partial_success"
        report.success = False
        report.partial_success = True
    else:
        report.status = "failed"
        report.success = False
        report.partial_success = False

    logger.info(
        "Stockage terminé : statut=%s, JSON=%s, CSV=%s, valides=%s, ignorés=%s.",
        report.status,
        "réussi" if report.json_success else "non réussi" if report.json.requested else "non demandé",
        "réussi" if report.csv_success else "non réussi" if report.csv.requested else "non demandé",
        report.valid,
        report.ignored
    )
    return report


# Service principal

def save_articles(
    articles: list[Any],
    save_json: bool = True,
    save_csv: bool = True
) -> StorageServiceReport:
    """Stocke les articles et retourne un rapport global détaillé."""

    report = StorageServiceReport(
        received=len(articles) if isinstance(articles, list) else 0
    )

    if not isinstance(articles, list):
        report.errors.append(
            f"Collection invalide fournie au service : {type(articles).__name__}."
        )
        return report

    if not articles:
        report.errors.append("Aucun article fourni au service de stockage.")
        return report

    if not save_json and not save_csv:
        report.errors.append("Aucun format de stockage sélectionné.")
        return report

    valid_articles, ignored_count = filter_article_dictionaries(articles)
    report.valid = len(valid_articles)
    report.ignored = ignored_count

    if not valid_articles:
        report.errors.append("Aucun article exploitable à sauvegarder.")
        return report

    if save_json:
        report.json = save_json_articles(valid_articles)

    if save_csv:
        report.csv = save_csv_articles(valid_articles)

    return finalize_storage_report(report)