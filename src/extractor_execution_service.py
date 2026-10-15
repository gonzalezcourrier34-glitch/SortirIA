"""Service d'orchestration de l'exécution des extracteurs CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractor_execution_service.py` (commit 93632c5).
Modifié pour SortirIA : registre réduit à RSS, OpenAgenda, DATAtourisme, iCal, programmes PDF et fichiers des partenaires (extracteurs de presse et de fake news retirés).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any

from config.constants import (
    EXTRACTOR_STATUS_DISABLED,
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
    SOURCE_TYPE_API,
    SOURCE_TYPE_DATASET,
    SOURCE_TYPE_DOCUMENT,
    SOURCE_TYPE_ICAL,
    SOURCE_TYPE_RSS,
    SOURCE_TYPE_SCRAPER,
)
from src.extractors.apis.source.datatourisme_extractor import (
    extract_all_articles as extract_datatourisme_articles,
)
from src.extractors.apis.source.openagenda_extractor import (
    extract_all_articles as extract_openagenda_articles,
)
from src.extractors.core.extractor_service import (
    ArticleExtractor,
    ExtractorExecution,
    run_extractor,
)
from src.extractors.datasets.source.partner_extractor import (
    extract_all_articles as extract_partner_articles,
)
from src.extractors.documents.document_extractor import (
    extract_all_articles as extract_document_articles,
)
from src.extractors.ical.ical_extractor import (
    extract_all_articles as extract_ical_articles,
)
from src.extractors.rss.source.event_rss_extractor import (
    extract_all_articles as extract_rss_articles,
)
from src.extractors.scrapers.source.agenda_scraper import (
    extract_all_articles as extract_scraper_articles,
)
from src.logger import get_logger
from src.utils.value_utils import normalize_casefold

logger = get_logger(__name__)


# Registre des extracteurs

@dataclass(frozen=True, slots=True)
class ExtractorDefinition:
    """Décrit un extracteur disponible dans le pipeline."""

    source_type: str
    extraction_function: ArticleExtractor


EXTRACTORS: dict[str, ExtractorDefinition] = {
    # Moteur RSS de CheckIt.AI avec l'adaptateur « agenda » de SortirIA
    # (src/extractors/rss/source/event_rss_extractor.py) : il lit tous les
    # flux déclarés dans config/sources.yaml (rss_sources).
    "rss": ExtractorDefinition(
        source_type=SOURCE_TYPE_RSS,
        extraction_function=extract_rss_articles
    ),
    # Événements OpenAgenda (ajout SortirIA). Les extracteurs de presse et de
    # fake news de CheckIt.AI ont été retirés.
    "openagenda": ExtractorDefinition(
        source_type=SOURCE_TYPE_API,
        extraction_function=extract_openagenda_articles
    ),
    # Événements des offices de tourisme (DATAtourisme, ajout du 14/10/2026).
    "datatourisme": ExtractorDefinition(
        source_type=SOURCE_TYPE_API,
        extraction_function=extract_datatourisme_articles
    ),
    # Moteur iCal générique : il lit tous les calendriers déclarés dans
    # config/sources.yaml (ical_sources), comme le moteur RSS.
    "ical": ExtractorDefinition(
        source_type=SOURCE_TYPE_ICAL,
        extraction_function=extract_ical_articles
    ),
    # Programmes publiés en PDF, lus par Mistral (config/sources.yaml,
    # document_sources).
    "document": ExtractorDefinition(
        source_type=SOURCE_TYPE_DOCUMENT,
        extraction_function=extract_document_articles
    ),
    # Fichiers envoyés par les partenaires (moteur datasets de CheckIt.AI,
    # config/sources.yaml, datasets > partenaire).
    "partenaire": ExtractorDefinition(
        source_type=SOURCE_TYPE_DATASET,
        extraction_function=extract_partner_articles
    ),
    # Pages d'agenda lues avec l'autorisation de l'éditeur (moteur scraper de
    # CheckIt.AI, config/sources.yaml, scraper_sources).
    "scraper": ExtractorDefinition(
        source_type=SOURCE_TYPE_SCRAPER,
        extraction_function=extract_scraper_articles
    ),
}


# Résultat global

@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Représente le bilan global d'une extraction."""

    articles: list[dict[str, Any]]
    extractor_results: list[ExtractorExecution]
    total_articles: int
    successful_extractors: int
    partial_success_extractors: int
    empty_extractors: int
    disabled_extractors: int
    failed_extractors: int
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """Convertit le résultat en dictionnaire sérialisable."""

        return {
            "articles": self.articles,
            "extractor_results": [
                asdict(result)
                for result in self.extractor_results
            ],
            "total_articles": self.total_articles,
            "successful_extractors": self.successful_extractors,
            "partial_success_extractors": self.partial_success_extractors,
            "empty_extractors": self.empty_extractors,
            "disabled_extractors": self.disabled_extractors,
            "failed_extractors": self.failed_extractors,
            "duration_seconds": self.duration_seconds
        }


# Sélection des extracteurs

def normalize_extractor_names(
    extractor_names: Iterable[str] | None
) -> list[str]:
    """Normalise et déduplique les noms des extracteurs demandés."""

    if extractor_names is None:
        return list(EXTRACTORS)

    if isinstance(extractor_names, (str, bytes, bytearray)):
        raise TypeError(
            "Les noms d'extracteurs doivent être fournis dans un itérable."
        )

    normalized_names: list[str] = []

    for extractor_name in extractor_names:
        if not isinstance(extractor_name, str):
            raise TypeError(
                "Chaque nom d'extracteur doit être une chaîne."
            )

        normalized_name = normalize_casefold(extractor_name)

        if normalized_name:
            normalized_names.append(normalized_name)

    return list(dict.fromkeys(normalized_names))


def validate_extractor_names(
    extractor_names: Sequence[str]
) -> None:
    """Vérifie que tous les extracteurs demandés existent."""

    unknown_extractors = sorted(
        set(extractor_names).difference(EXTRACTORS)
    )

    if unknown_extractors:
        raise ValueError(
            "Extracteur(s) inconnu(s) : "
            + ", ".join(unknown_extractors)
        )


# Synthèse des résultats

def get_failed_extractor_names(
    executions: Iterable[ExtractorExecution]
) -> list[str]:
    """Retourne les noms des extracteurs en échec."""

    return [
        execution.extractor_name
        for execution in executions
        if execution.status == EXTRACTOR_STATUS_FAILED
    ]


def build_extraction_result(
    articles: list[dict[str, Any]],
    executions: list[ExtractorExecution],
    duration_seconds: float
) -> ExtractionResult:
    """Construit le bilan global des extractions exécutées."""

    status_counts = Counter(
        execution.status
        for execution in executions
    )

    return ExtractionResult(
        articles=articles,
        extractor_results=executions,
        total_articles=len(articles),
        successful_extractors=status_counts[
            EXTRACTOR_STATUS_SUCCESS
        ],
        partial_success_extractors=status_counts[
            EXTRACTOR_STATUS_PARTIAL_SUCCESS
        ],
        empty_extractors=status_counts[
            EXTRACTOR_STATUS_EMPTY
        ],
        disabled_extractors=status_counts[
            EXTRACTOR_STATUS_DISABLED
        ],
        failed_extractors=status_counts[
            EXTRACTOR_STATUS_FAILED
        ],
        duration_seconds=round(duration_seconds, 3)
    )


def log_extraction_result(result: ExtractionResult) -> None:
    """Journalise le bilan global de l'extraction."""

    logger.info(
        "Extraction terminée : %s article(s), %s réussite(s), "
        "%s réussite(s) partielle(s), %s résultat(s) vide(s), "
        "%s extracteur(s) désactivé(s), %s échec(s), "
        "durée %.3f seconde(s).",
        result.total_articles,
        result.successful_extractors,
        result.partial_success_extractors,
        result.empty_extractors,
        result.disabled_extractors,
        result.failed_extractors,
        result.duration_seconds
    )


# Validation du bilan

def validate_extraction_result(
    result: ExtractionResult,
    fail_if_empty: bool,
    fail_on_extractor_error: bool
) -> None:
    """Applique les règles d'échec configurées au bilan global."""

    if fail_on_extractor_error and result.failed_extractors:
        failed_names = get_failed_extractor_names(
            result.extractor_results
        )

        raise RuntimeError(
            "Échec d'un ou plusieurs extracteurs : "
            + ", ".join(failed_names)
        )

    if fail_if_empty and not result.articles:
        raise RuntimeError(
            "Aucun article n'a été extrait."
        )


# Exécution d'un extracteur

def execute_registered_extractor(
    extractor_name: str,
    definition: ExtractorDefinition
) -> tuple[list[dict[str, Any]], ExtractorExecution]:
    """Exécute un extracteur déclaré dans le registre."""

    return run_extractor(
        extractor_name=extractor_name,
        source_type=definition.source_type,
        extractor=definition.extraction_function
    )


# Orchestration globale

def extract_all_sources(
    extractor_names: Iterable[str] | None = None,
    fail_if_empty: bool = True,
    fail_on_extractor_error: bool = False
) -> ExtractionResult:
    """Exécute les extracteurs demandés et fusionne leurs résultats."""

    requested_names = normalize_extractor_names(extractor_names)

    if not requested_names:
        raise ValueError(
            "Aucun extracteur n'a été sélectionné."
        )

    validate_extractor_names(requested_names)

    logger.info(
        "Démarrage de l'extraction avec %s extracteur(s) : %s.",
        len(requested_names),
        ", ".join(requested_names)
    )

    started_at = perf_counter()
    articles: list[dict[str, Any]] = []
    executions: list[ExtractorExecution] = []

    for extractor_name in requested_names:
        extracted_articles, execution = execute_registered_extractor(
            extractor_name=extractor_name,
            definition=EXTRACTORS[extractor_name]
        )

        articles.extend(extracted_articles)
        executions.append(execution)

    result = build_extraction_result(
        articles=articles,
        executions=executions,
        duration_seconds=perf_counter() - started_at
    )

    log_extraction_result(result)

    validate_extraction_result(
        result=result,
        fail_if_empty=fail_if_empty,
        fail_on_extractor_error=fail_on_extractor_error
    )

    return result