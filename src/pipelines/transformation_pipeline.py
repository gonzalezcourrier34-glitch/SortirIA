"""Pipeline de transformation des données CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/pipelines/transformation_pipeline.py` (commit 93632c5).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config.paths import PROCESSED_DATA_DIR
from src.logger import get_logger

from src.article.schema.article_schema import STANDARD_ARTICLE_FIELDS
from src.article.preparation.article_preparation_models import ArticlePreparationReport
from src.article.preparation.article_preparation_profiles import PreparationPolicyName
from src.article.preparation.article_preparation import prepare_articles
from src.article.processing.article_transformer import transform_articles
from src.article.validation.article_validator import validate_article_with_reason
from src.article.article_utils import get_article_identifier

from src.storage.files.json_storage import load_articles

from src.transformers.features_generator import enrich_articles

from src.storage.files.storage_utils import (
    atomic_replace,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)

from src.utils.date_utils import get_current_datetime, get_extraction_date

logger = get_logger(__name__)


# Configuration du pipeline

TRANSFORMATION_VERSION = "1.0.0"

# Dossier contenant les articles transformés.
TRANSFORMED_DATA_DIR = PROCESSED_DATA_DIR / "transformed"

# Dossier contenant les rapports d'exécution.
TRANSFORMATION_REPORTS_DIR = PROCESSED_DATA_DIR / "reports"


# Prépare les dossiers nécessaires au chargement du module.
for directory in (TRANSFORMED_DATA_DIR, TRANSFORMATION_REPORTS_DIR):
    ensure_directory_exists(directory)


# Préparation des articles par rôle

PREPARATION_POLICIES: tuple[PreparationPolicyName, ...] = (
    "acquisition",
    "labeled_reference",
    "multimodal_reference"
)


def prepare_articles_by_role(
    articles: list[Any],
    remove_duplicates: bool = True
) -> tuple[list[dict[str, Any]], ArticlePreparationReport]:
    """Prépare chaque article avec la politique associée à son rôle."""

    grouped_articles: dict[PreparationPolicyName, list[Any]] = {
        policy_name: [] for policy_name in PREPARATION_POLICIES
    }
    combined_articles: list[dict[str, Any]] = []
    combined_report = ArticlePreparationReport()

    for article in articles:
        role = (
            str(article.get("dataset_role", "") or "").strip()
            if isinstance(article, Mapping)
            else ""
        )
        policy_name: PreparationPolicyName = (
            role if role in PREPARATION_POLICIES else "acquisition"
        )
        grouped_articles[policy_name].append(article)

    for policy_name, policy_articles in grouped_articles.items():
        if not policy_articles:
            continue

        result = prepare_articles(
            policy_articles,
            policy_name=policy_name,
            remove_duplicates=remove_duplicates
        )
        report = result.report

        combined_articles.extend(result.articles)
        combined_report.received += report.received
        combined_report.normalized += report.normalized
        combined_report.normalization_ignored += report.normalization_ignored
        combined_report.cleaned += report.cleaned
        combined_report.cleaning_ignored += report.cleaning_ignored
        combined_report.duplicates += report.duplicates
        combined_report.invalid += report.invalid
        combined_report.kept += report.kept
        combined_report.rejection_reasons.update(report.rejection_reasons)

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

    return combined_articles, combined_report


# Validation finale

def validate_transformed_articles(
    articles: list[dict[str, Any]],
    require_image: bool = False
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Valide les articles transformés et compte les rejets."""

    valid_articles: list[dict[str, Any]] = []
    rejection_stats: Counter[str] = Counter()

    # La présence d'une image peut être obligatoire ou facultative.
    policy = {
        "require_image": require_image
    }

    # Chaque article est validé indépendamment.
    for article in articles:
        is_valid, reason = validate_article_with_reason(
            article,
            policy
        )

        if not is_valid:
            rejection_reason = reason or "raison_inconnue"
            rejection_stats[rejection_reason] += 1

            logger.warning(
                "Article transformé rejeté [%s] : %s.",
                get_article_identifier(article),
                rejection_reason
            )
            continue

        # Travaille sur une copie pour préserver l'article reçu.
        validated_article = article.copy()
        validated_article["data_quality_status"] = "valid"
        validated_article["rejection_reason"] = ""

        valid_articles.append(validated_article)

    logger.info(
        "%s article(s) transformé(s) valide(s) sur %s.",
        len(valid_articles),
        len(articles)
    )

    return valid_articles, rejection_stats


# Préparation des exports

def get_export_columns(
    articles: list[dict[str, Any]]
) -> list[str]:
    """Retourne un ordre stable pour les colonnes exportées."""

    # Les colonnes du schéma standard sont toujours placées en premier.
    # Les champs supplémentaires sont triés pour obtenir un ordre stable.
    additional_columns = sorted({
        key
        for article in articles
        for key in article
        if key not in STANDARD_ARTICLE_FIELDS
    })

    return [
        *STANDARD_ARTICLE_FIELDS,
        *additional_columns
    ]


# Écriture atomique des fichiers

def write_json_file(
    data: Any,
    output_path: Path
) -> None:
    """Écrit une valeur JSON de manière atomique."""

    temporary_path = create_temporary_path(output_path)

    try:
        ensure_directory_exists(output_path.parent)

        # Écrit d'abord dans un fichier temporaire.
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=4,
                default=str
            )

        # Remplace le fichier cible uniquement si l'écriture a réussi.
        atomic_replace(
            temporary_path,
            output_path
        )

    except (OSError, TypeError, ValueError) as error:
        logger.exception(
            "Impossible d'écrire le fichier JSON %s : %s",
            output_path,
            error
        )
        raise

    finally:
        # Supprime un éventuel fichier temporaire restant.
        remove_file_if_exists(temporary_path)


def write_csv_file(
    articles: list[dict[str, Any]],
    output_path: Path
) -> None:
    """Écrit les articles au format CSV de manière atomique."""

    temporary_path = create_temporary_path(output_path)

    try:
        ensure_directory_exists(output_path.parent)

        # Construit un DataFrame avec un ordre de colonnes stable.
        dataframe = pd.DataFrame(
            articles,
            columns=get_export_columns(articles)
        )

        # Écrit d'abord dans un fichier temporaire.
        dataframe.to_csv(
            temporary_path,
            index=False,
            encoding="utf-8-sig"
        )

        # Le fichier final n'est remplacé qu'après une écriture complète.
        atomic_replace(
            temporary_path,
            output_path
        )

    except (OSError, ValueError, TypeError) as error:
        logger.exception(
            "Impossible d'écrire le fichier CSV %s : %s",
            output_path,
            error
        )
        raise

    finally:
        remove_file_if_exists(temporary_path)


# Transaction logique des exports

def rollback_exported_files(
    exported_files: Mapping[str, Path]
) -> None:
    """Supprime les exports créés pendant une exécution ayant échoué."""

    if not exported_files:
        return

    for export_format, filepath in exported_files.items():
        try:
            remove_file_if_exists(filepath)

            logger.warning(
                "Export %s supprimé après échec : %s",
                export_format.upper(),
                filepath
            )

        except OSError as error:
            # Le rollback continue même si un fichier ne peut pas être supprimé.
            logger.error(
                "Impossible de supprimer l'export %s pendant le rollback : %s",
                filepath,
                error
            )


def export_transformed_articles(
    articles: list[dict[str, Any]],
    execution_date: str,
    export_json: bool = True,
    export_csv: bool = True
) -> dict[str, Path]:
    """Exporte les articles avec rollback en cas d'échec."""

    if not articles:
        logger.warning(
            "Aucun article transformé à exporter."
        )
        return {}

    exported_files: dict[str, Path] = {}

    try:
        # Crée l'export JSON si demandé.
        if export_json:
            json_path = (
                TRANSFORMED_DATA_DIR
                / f"articles_transformed_{execution_date}.json"
            )

            write_json_file(
                articles,
                json_path
            )

            exported_files["json"] = json_path

        # Crée l'export CSV si demandé.
        if export_csv:
            csv_path = (
                TRANSFORMED_DATA_DIR
                / f"articles_transformed_{execution_date}.csv"
            )

            write_csv_file(
                articles,
                csv_path
            )

            exported_files["csv"] = csv_path

    except Exception:
        # Si un export échoue, les fichiers créés pendant cette
        # exécution sont supprimés pour éviter un état partiel.
        rollback_exported_files(exported_files)
        raise

    # Journalise uniquement les exports réellement terminés.
    for export_format, filepath in exported_files.items():
        logger.info(
            "Export %s créé : %s",
            export_format.upper(),
            filepath
        )

    return exported_files


# Rapport de transformation

def write_transformation_report(
    report: Mapping[str, Any],
    execution_date: str
) -> Path:
    """Enregistre ou met à jour le rapport de transformation."""

    report_path = (
        TRANSFORMATION_REPORTS_DIR
        / f"transformation_report_{execution_date}.json"
    )

    write_json_file(
        dict(report),
        report_path
    )

    return report_path


def build_transformation_report(
    *,
    status: str,
    started_at: datetime,
    transformation_version: str,
    source: str | None,
    require_image: bool,
    raw_articles: list[Any] | None = None,
    prepared_articles: list[Any] | None = None,
    transformed_articles: list[Any] | None = None,
    enriched_articles: list[Any] | None = None,
    valid_articles: list[Any] | None = None,
    preparation_report: ArticlePreparationReport | None = None,
    rejection_stats: Mapping[str, int] | None = None,
    exported_files: Mapping[str, Path] | None = None,
    error: str = ""
) -> dict[str, Any]:
    """Construit un rapport cohérent quel que soit l'état du pipeline."""

    finished_at = get_current_datetime()
    
    preparation_rejections = (
        preparation_report.rejection_reasons
        if preparation_report
        else {}
    )

    combined_rejection_stats: Counter[str] = Counter(
        preparation_rejections
    )
    combined_rejection_stats.update(rejection_stats or {})

    return {
        "pipeline": "transformation",
        "status": status,
        "transformation_version": transformation_version,
        "started_at": started_at.isoformat(),
        "finished_at": get_extraction_date(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "source_filter": source or "all",
        "require_image": require_image,
        "raw_articles": len(raw_articles or []),
        "normalized_articles": (
            preparation_report.normalized
            if preparation_report
            else 0
        ),
        "normalization_ignored": (
            preparation_report.normalization_ignored
            if preparation_report
            else 0
        ),
        "cleaned_articles": (
            preparation_report.cleaned
            if preparation_report
            else 0
        ),
        "cleaning_ignored": (
            preparation_report.cleaning_ignored
            if preparation_report
            else 0
        ),
        "duplicate_articles": (
            preparation_report.duplicates
            if preparation_report
            else 0
        ),
        "prepared_articles": len(prepared_articles or []),
        "transformed_articles": len(transformed_articles or []),
        "enriched_articles": len(enriched_articles or []),
        "valid_articles": len(valid_articles or []),
        "preparation_invalid_articles": (
            preparation_report.invalid
            if preparation_report
            else 0
        ),
        "final_invalid_articles": sum((rejection_stats or {}).values()),
        "rejected_articles": sum(combined_rejection_stats.values()),
        "rejection_reasons": dict(combined_rejection_stats),
        "exported_files": {
            export_format: str(filepath)
            for export_format, filepath in (exported_files or {}).items()
        },
        "error": error
    }


# Pipeline principal

def run_transformation_pipeline(
    source: str | None = None,
    require_image: bool = False,
    export_json: bool = True,
    export_csv: bool = True,
    transformation_version: str = TRANSFORMATION_VERSION
) -> list[dict[str, Any]]:
    """Exécute le pipeline complet avec traçabilité des erreurs."""

    started_at = get_current_datetime()
    
    transformation_date = started_at.isoformat()
    execution_date = started_at.strftime("%Y%m%d_%H%M%S")

    # Initialise les variables pour pouvoir créer un rapport
    # même si une erreur survient au milieu du pipeline.
    raw_articles: list[dict[str, Any]] = []
    prepared_articles: list[dict[str, Any]] = []
    transformed_articles: list[dict[str, Any]] = []
    enriched_articles: list[dict[str, Any]] = []
    valid_articles: list[dict[str, Any]] = []
    preparation_report = ArticlePreparationReport()
    rejection_stats: Counter[str] = Counter()
    exported_files: dict[str, Path] = {}

    logger.info(
        "Démarrage du pipeline de transformation version %s.",
        transformation_version
    )

    try:
        # 1. Chargement des données brutes

        raw_articles = load_articles(source=source)

        if not raw_articles:
            logger.warning(
                "Pipeline arrêté : aucune donnée brute disponible."
            )
            return []

        # 2. Préparation métier des articles

        # Chaque groupe est préparé avec la politique correspondant à son
        # dataset_role : acquisition, labeled_reference ou multimodal_reference.
        prepared_articles, preparation_report = prepare_articles_by_role(
            raw_articles,
            remove_duplicates=True
        )

        logger.info(
            (
                "Préparation des articles : reçus=%s, normalisés=%s, "
                "nettoyés=%s, doublons=%s, invalides=%s, conservés=%s."
            ),
            preparation_report.received,
            preparation_report.normalized,
            preparation_report.cleaned,
            preparation_report.duplicates,
            preparation_report.invalid,
            preparation_report.kept
        )

        if preparation_report.rejection_reasons:
            logger.info(
                "Motifs de rejet pendant la préparation : %s.",
                dict(preparation_report.rejection_reasons)
            )

        if not prepared_articles:
            logger.warning(
                "Pipeline arrêté : aucun article après préparation."
            )
            return []

        # 3. Transformations métier

        transformed_articles = transform_articles(
            prepared_articles
        )

        if not transformed_articles:
            logger.warning(
                "Pipeline arrêté : aucun article "
                "après transformation métier."
            )
            return []

        # 4. Enrichissement

        enriched_articles = enrich_articles(
            articles=transformed_articles,
            transformation_date=transformation_date,
            transformation_version=transformation_version
        )

        if not enriched_articles:
            logger.warning(
                "Pipeline arrêté : aucun article après enrichissement."
            )
            return []

        # 5. Validation finale

        # Cette validation contrôle l'état obtenu après les transformations
        # et applique la règle finale concernant la présence d'une image.
        valid_articles, rejection_stats = validate_transformed_articles(
            enriched_articles,
            require_image=require_image
        )

        if not valid_articles:
            logger.warning(
                "Pipeline arrêté : aucun article transformé valide."
            )
            return []

        # 6. Rapport préparatoire

        # Le rapport est écrit avant les exports afin de conserver
        # une trace même si le JSON ou le CSV échoue ensuite.
        pending_report = build_transformation_report(
            status="export_pending",
            started_at=started_at,
            transformation_version=transformation_version,
            source=source,
            require_image=require_image,
            raw_articles=raw_articles,
            prepared_articles=prepared_articles,
            transformed_articles=transformed_articles,
            enriched_articles=enriched_articles,
            valid_articles=valid_articles,
            preparation_report=preparation_report,
            rejection_stats=rejection_stats
        )

        report_path = write_transformation_report(
            pending_report,
            execution_date
        )

        logger.info(
            "Rapport préparatoire créé : %s",
            report_path
        )

        # 7. Exports transactionnels

        exported_files = export_transformed_articles(
            valid_articles,
            execution_date,
            export_json=export_json,
            export_csv=export_csv
        )

        # 8. Rapport final

        success_report = build_transformation_report(
            status="success",
            started_at=started_at,
            transformation_version=transformation_version,
            source=source,
            require_image=require_image,
            raw_articles=raw_articles,
            prepared_articles=prepared_articles,
            transformed_articles=transformed_articles,
            enriched_articles=enriched_articles,
            valid_articles=valid_articles,
            preparation_report=preparation_report,
            rejection_stats=rejection_stats,
            exported_files=exported_files
        )

        report_path = write_transformation_report(
            success_report,
            execution_date
        )

        logger.info(
            "Rapport de transformation finalisé : %s",
            report_path
        )

        logger.info(
            "Pipeline terminé avec succès : "
            "%s article(s) transformé(s).",
            len(valid_articles)
        )

        return valid_articles

    except Exception as error:
        # Capture également les erreurs imprévues :
        # KeyError, ParserError, PermissionError, etc.
        logger.exception(
            "Échec inattendu du pipeline de transformation : %s",
            error
        )

        # Supprime les éventuels exports créés avant l'erreur.
        rollback_exported_files(exported_files)

        # Construit un rapport d'échec avec l'état atteint.
        failed_report = build_transformation_report(
            status="failed",
            started_at=started_at,
            transformation_version=transformation_version,
            source=source,
            require_image=require_image,
            raw_articles=raw_articles,
            prepared_articles=prepared_articles,
            transformed_articles=transformed_articles,
            enriched_articles=enriched_articles,
            valid_articles=valid_articles,
            preparation_report=preparation_report,
            rejection_stats=rejection_stats,
            error=f"{type(error).__name__}: {error}"
        )

        try:
            report_path = write_transformation_report(
                failed_report,
                execution_date
            )

            logger.info(
                "Rapport d'échec créé : %s",
                report_path
            )

        except Exception as report_error:
            # L'erreur du rapport est journalisée sans masquer
            # l'erreur originale du pipeline.
            logger.exception(
                "Impossible d'écrire le rapport d'échec : %s",
                report_error
            )

        return []


# Exécution directe du module

if __name__ == "__main__":
    run_transformation_pipeline()