"""
Service Evidently pour l'analyse et la persistance de dérive.

Ce module orchestre toute la chaîne de monitoring de drift :
- chargement des données de référence
- chargement ou reconstruction des données courantes
- exécution d'un rapport Evidently
- extraction du drift global
- extraction du drift par feature
- persistance des métriques dans la base via MonitoringService

Objectif pédagogique
--------------------
Les routes FastAPI restent fines.
Toute la logique métier Evidently est centralisée ici.

Point important
---------------
Avec les versions récentes d'Evidently, les métriques de type ValueDrift
ont souvent cette forme :

{
    "metric_name": "ValueDrift(column=AGE_YEARS,method=...,threshold=0.1)",
    "config": {
        "type": "evidently:metric_v2:ValueDrift",
        "column": "AGE_YEARS",
        "threshold": 0.1
    },
    "value": 0.0775
}

Le champ `value` peut donc être un float direct.
Il ne faut pas supposer que `value` est toujours un dictionnaire.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import MONITORING_DIR
from app.services.features_builder_service import (
    build_transformed_features_from_loaded_data,
)
from app.services.loader_services.data_loading_service import (
    get_input_feature_names,
    get_raw_data_cache,
    get_reference_features_raw_df,
    get_reference_features_transformed_df,
    get_transformed_feature_names,
    init_monitoring_reference_cache,
)
from app.services.monitoring_service import MonitoringService


logger = logging.getLogger(__name__)


class EvidentlyService:
    """
    Service d'analyse Evidently et de persistance des métriques de drift.

    Parameters
    ----------
    db : Session
        Session SQLAlchemy utilisée pour écrire les résultats en base.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.monitoring_service = MonitoringService(db)

    # =========================================================================
    # Helpers génériques
    # =========================================================================

    def _ensure_dataframe(
        self,
        df: pd.DataFrame,
        name: str,
    ) -> pd.DataFrame:
        """
        Vérifie qu'un objet est bien un DataFrame pandas non vide.
        """
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"`{name}` doit être un DataFrame pandas.")

        if df.empty:
            raise ValueError(f"`{name}` est vide.")

        return df.copy()

    def _coerce_int(self, value: object, default: int = 0) -> int:
        """
        Convertit une valeur en entier de façon robuste.
        """
        try:
            if value is None or pd.isna(value):
                return default
            return int(value)
        except Exception:
            return default

    def _coerce_float(self, value: object, default: float = 0.0) -> float:
        """
        Convertit une valeur en float de façon robuste.
        """
        try:
            if value is None or pd.isna(value):
                return default
            return float(value)
        except Exception:
            return default

    def _resolve_monitoring_dir(
        self,
        monitoring_dir: str | None,
    ) -> Path:
        """
        Résout le dossier de monitoring à utiliser.

        Priorité :
        1. paramètre explicite de la route
        2. variable MONITORING_DIR
        """
        if monitoring_dir is not None and str(monitoring_dir).strip():
            return Path(str(monitoring_dir).strip())

        return Path(MONITORING_DIR)

    def _prepare_common_columns(
        self,
        reference_df: pd.DataFrame,
        current_df: pd.DataFrame,
        feature_names: list[str] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
        """
        Aligne reference_df et current_df sur les colonnes communes.

        Notes
        -----
        Evidently ne peut comparer correctement que les colonnes présentes
        dans les deux datasets.
        """
        ref = self._ensure_dataframe(reference_df, "reference_df")
        cur = self._ensure_dataframe(current_df, "current_df")

        common_cols = sorted(set(ref.columns).intersection(set(cur.columns)))

        if feature_names is not None:
            requested = [str(col) for col in feature_names]
            common_cols = [col for col in common_cols if col in requested]

        if not common_cols:
            raise ValueError(
                "Aucune colonne commune exploitable entre reference_df et current_df."
            )

        return ref[common_cols].copy(), cur[common_cols].copy(), common_cols

    def _limit_dataframe_rows(
        self,
        df: pd.DataFrame,
        max_rows: int | None,
        *,
        name: str,
    ) -> pd.DataFrame:
        """
        Limite le nombre de lignes d'un DataFrame.

        Pour un dashboard étudiant, cela évite de lancer Evidently sur
        un volume trop lourd.
        """
        checked_df = self._ensure_dataframe(df, name)

        if max_rows is None:
            return checked_df

        if max_rows <= 0:
            raise ValueError("`max_rows` doit être strictement positif.")

        if len(checked_df) <= max_rows:
            return checked_df

        limited_df = checked_df.tail(max_rows).copy()

        logger.info(
            "Dataframe limited for Evidently analysis",
            extra={
                "extra_data": {
                    "event": "evidently_dataframe_limited",
                    "name": name,
                    "original_rows": len(checked_df),
                    "limited_rows": len(limited_df),
                    "max_rows": max_rows,
                }
            },
        )

        return limited_df

    # =========================================================================
    # Conversion rapport Evidently
    # =========================================================================

    def _safe_as_dict(self, report_object: object) -> dict[str, Any]:
        """
        Convertit un objet Evidently en dictionnaire Python.

        Compatible avec plusieurs versions Evidently :
        - snapshot.json en propriété
        - snapshot.json() en méthode
        - dict()
        - as_dict()
        - model_dump()
        """
        if report_object is None:
            return {}

        def _parse_json_like(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return value

            if isinstance(value, bytes):
                value = value.decode("utf-8")

            if isinstance(value, str):
                text = value.strip()

                if not text:
                    return {}

                try:
                    parsed = json.loads(text)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}

            return {}

        for attr_name in ["as_dict", "dict", "model_dump", "json", "_repr_json_"]:
            attr = getattr(report_object, attr_name, None)

            if attr is None:
                continue

            try:
                if attr_name == "as_dict" and callable(attr):
                    try:
                        result = attr(include_render=False)
                    except TypeError:
                        result = attr()
                else:
                    result = attr() if callable(attr) else attr

                parsed = _parse_json_like(result)

                if parsed:
                    return parsed

            except Exception:
                continue

        return {}

    # =========================================================================
    # Chargement des données
    # =========================================================================

    def _load_reference_dataframe(
        self,
        *,
        reference_kind: Literal["raw", "transformed"],
    ) -> pd.DataFrame:
        """
        Charge le DataFrame de référence.
        """
        if reference_kind == "raw":
            return get_reference_features_raw_df()

        return get_reference_features_transformed_df()

    def _load_current_dataframe(
        self,
        *,
        current_kind: Literal["raw", "transformed"],
    ) -> pd.DataFrame:
        """
        Charge le DataFrame courant.

        raw :
            données brutes applicatives.

        transformed :
            features après preprocessing.
        """
        raw_cache = get_raw_data_cache()

        if current_kind == "raw":
            for key in ["application", "application_test", "app"]:
                if key in raw_cache:
                    return raw_cache[key].copy()

            raise ValueError(
                "Impossible de construire current_df brut : "
                "aucune source brute compatible trouvée dans RAW_DATA_CACHE."
            )

        transformed_df = build_transformed_features_from_loaded_data(
            raw_sources=raw_cache,
            client_ids=None,
            debug=False,
        )

        return self._ensure_dataframe(transformed_df, "current_df_transformed")

    def _load_feature_names(
        self,
        *,
        reference_kind: Literal["raw", "transformed"],
    ) -> list[str] | None:
        """
        Charge la liste des colonnes à surveiller.
        """
        if reference_kind == "raw":
            feature_names = get_input_feature_names()
        else:
            feature_names = get_transformed_feature_names()

        return feature_names if feature_names else None

    # =========================================================================
    # Réponse standard
    # =========================================================================

    def _build_response_payload(
        self,
        *,
        success: bool,
        message: str,
        model_name: str,
        model_version: str,
        reference_kind: str,
        current_kind: str,
        logged_metrics: int,
        reference_rows: int,
        current_rows: int,
        analyzed_columns: list[str],
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Construit une réponse homogène pour les routes API.
        """
        return {
            "success": success,
            "message": message,
            "model_name": model_name,
            "model_version": model_version,
            "reference_kind": reference_kind,
            "current_kind": current_kind,
            "logged_metrics": logged_metrics,
            "reference_rows": reference_rows,
            "current_rows": current_rows,
            "analyzed_columns": analyzed_columns,
            "report": report,
        }

    # =========================================================================
    # Exécution Evidently
    # =========================================================================

    def run_data_drift_report(
        self,
        *,
        reference_df: pd.DataFrame,
        current_df: pd.DataFrame,
        feature_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Exécute un rapport de drift Evidently.
        """
        ref, cur, used_columns = self._prepare_common_columns(
            reference_df=reference_df,
            current_df=current_df,
            feature_names=feature_names,
        )

        logger.info(
            "Starting Evidently data drift report",
            extra={
                "extra_data": {
                    "event": "evidently_report_start",
                    "reference_rows": len(ref),
                    "current_rows": len(cur),
                    "analyzed_columns_count": len(used_columns),
                }
            },
        )

        try:
            from evidently import Report
            from evidently.presets import DataDriftPreset

        except Exception as exc:
            logger.exception(
                "Failed to import Evidently",
                extra={
                    "extra_data": {
                        "event": "evidently_import_exception",
                        "error": str(exc),
                    }
                },
            )

            return {
                "success": False,
                "message": f"Impossible d'importer Evidently : {exc}",
                "report": {},
                "reference_rows": len(ref),
                "current_rows": len(cur),
                "analyzed_columns": used_columns,
            }

        try:
            report = Report([DataDriftPreset()])
            snapshot = report.run(
                current_data=cur,
                reference_data=ref,
            )

            output_dir = Path("artifacts") / "evidently" / "reports"
            output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = output_dir / f"data_drift_report_{timestamp}.html"

            try:
                report.save_html(str(html_path))
            except AttributeError:
                if hasattr(snapshot, "save_html"):
                    snapshot.save_html(str(html_path))

            report_dict = self._safe_as_dict(snapshot)

            if not report_dict:
                report_dict = self._safe_as_dict(report)

            logger.info(
                "Evidently report top-level structure",
                extra={
                    "extra_data": {
                        "event": "evidently_report_structure_debug",
                        "top_level_keys": (
                            list(report_dict.keys())
                            if isinstance(report_dict, dict)
                            else []
                        ),
                        "preview": json.dumps(report_dict, default=str)[:3000],
                    }
                },
            )

            logger.info(
                "Evidently data drift report generated successfully",
                extra={
                    "extra_data": {
                        "event": "evidently_report_success",
                        "reference_rows": len(ref),
                        "current_rows": len(cur),
                        "analyzed_columns_count": len(used_columns),
                    }
                },
            )

            return {
                "success": True,
                "message": "Rapport Evidently généré avec succès.",
                "report": report_dict,
                "reference_rows": len(ref),
                "current_rows": len(cur),
                "analyzed_columns": used_columns,
            }

        except Exception as exc:
            logger.exception(
                "Unexpected error during Evidently report execution",
                extra={
                    "extra_data": {
                        "event": "evidently_report_exception",
                        "error": str(exc),
                    }
                },
            )

            return {
                "success": False,
                "message": f"Erreur pendant l'exécution d'Evidently : {exc}",
                "report": {},
                "reference_rows": len(ref),
                "current_rows": len(cur),
                "analyzed_columns": used_columns,
            }

    # =========================================================================
    # Extraction dataset-level
    # =========================================================================

    def extract_dataset_drift_summary(
        self,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Extrait le résumé global de drift depuis le rapport Evidently.
        """
        metrics = report.get("metrics", [])

        if isinstance(metrics, list):
            for metric in metrics:
                if not isinstance(metric, dict):
                    continue

                metric_name = str(metric.get("metric_name", ""))
                config = metric.get("config", {})
                value = metric.get("value", {})

                config_type = ""
                if isinstance(config, dict):
                    config_type = str(config.get("type", ""))

                is_drifted_columns_count = (
                    "DriftedColumnsCount" in metric_name
                    or "DriftedColumnsCount" in config_type
                )

                if is_drifted_columns_count and isinstance(value, dict):
                    number_of_drifted_columns = self._coerce_int(
                        value.get("count"),
                        default=0,
                    )
                    share_of_drifted_columns = self._coerce_float(
                        value.get("share"),
                        default=0.0,
                    )

                    summary = {
                        "drift_detected": number_of_drifted_columns > 0,
                        "number_of_drifted_columns": number_of_drifted_columns,
                        "share_of_drifted_columns": share_of_drifted_columns,
                        "raw_summary": metric,
                    }

                    logger.info(
                        "Extracted dataset drift summary from Evidently metrics",
                        extra={
                            "extra_data": {
                                "event": "evidently_extract_dataset_summary_success",
                                "drift_detected": summary["drift_detected"],
                                "number_of_drifted_columns": number_of_drifted_columns,
                                "share_of_drifted_columns": share_of_drifted_columns,
                            }
                        },
                    )

                    return summary

        logger.warning(
            "No dataset-level drift summary found in Evidently report",
            extra={
                "extra_data": {
                    "event": "evidently_dataset_summary_not_found",
                }
            },
        )

        return {
            "drift_detected": False,
            "number_of_drifted_columns": 0,
            "share_of_drifted_columns": 0.0,
            "raw_summary": {},
        }

    # =========================================================================
    # Extraction feature-level corrigée
    # =========================================================================

    def extract_feature_drift_rows_from_report(
        self,
        *,
        report: dict[str, Any],
        model_name: str,
        model_version: str,
        reference_window_start: object = None,
        reference_window_end: object = None,
        current_window_start: object = None,
        current_window_end: object = None,
    ) -> list[dict[str, Any]]:
        """
        Extrait les métriques de drift par feature depuis un rapport Evidently.

        Cette version gère deux formats :
        - Evidently récent : value est souvent un float direct.
        - Ancien format : value peut être un dictionnaire avec drift_score,
        p_value, drift_detected, etc.

        Le résultat est normalisé pour être persisté dans drift_metrics.
        """
        rows: list[dict[str, Any]] = []

        metrics = report.get("metrics", [])
        if not isinstance(metrics, list):
            return rows

        for metric in metrics:
            if not isinstance(metric, dict):
                continue

            evidently_metric_name = str(metric.get("metric_name", ""))
            config = metric.get("config", {})
            value = metric.get("value")

            if not isinstance(config, dict):
                config = {}

            config_type = str(config.get("type", ""))

            is_feature_drift_metric = (
                "ValueDrift" in evidently_metric_name
                or "ColumnDriftMetric" in evidently_metric_name
                or "ValueDrift" in config_type
                or "ColumnDriftMetric" in config_type
            )

            if not is_feature_drift_metric:
                continue

            feature_name = (
                config.get("column")
                or config.get("column_name")
                or config.get("feature_name")
            )

            if feature_name is None:
                continue

            feature_name = str(feature_name)

            threshold_raw = config.get("threshold")
            threshold_value = (
                self._coerce_float(threshold_raw, default=0.0)
                if threshold_raw is not None
                else None
            )

            raw_drift_detected = None

            if isinstance(value, dict):
                raw_metric_value = (
                    value.get("drift_score")
                    or value.get("score")
                    or value.get("statistic")
                    or value.get("p_value")
                    or value.get("value")
                )

                raw_drift_detected = (
                    value.get("drift_detected")
                    if "drift_detected" in value
                    else value.get("detected")
                )

                if threshold_value is None and value.get("threshold") is not None:
                    threshold_value = self._coerce_float(
                        value.get("threshold"),
                        default=0.0,
                    )
            else:
                raw_metric_value = value

            metric_value = self._coerce_float(raw_metric_value, default=0.0)

            if isinstance(raw_drift_detected, bool):
                drift_detected = raw_drift_detected
            elif threshold_value is not None:
                drift_detected = metric_value >= threshold_value
            else:
                drift_detected = False

            rows.append(
                {
                    "model_name": model_name,
                    "model_version": model_version,
                    "feature_name": feature_name,
                    "metric_name": "value_drift",
                    "metric_value": metric_value,
                    "threshold_value": threshold_value,
                    "drift_detected": bool(drift_detected),
                    "details": {
                        "evidently_metric_name": evidently_metric_name,
                        "config_type": config_type,
                        "method": config.get("method"),
                        "feature_name": feature_name,
                        "raw_value": value,
                        "raw_config": config,
                    },
                    "reference_window_start": reference_window_start,
                    "reference_window_end": reference_window_end,
                    "current_window_start": current_window_start,
                    "current_window_end": current_window_end,
                }
            )

        logger.info(
            "Extracted feature-level drift metrics from Evidently report",
            extra={
                "extra_data": {
                    "event": "evidently_extract_feature_metrics_success",
                    "model_name": model_name,
                    "model_version": model_version,
                    "feature_rows": len(rows),
                    "drifted_feature_rows": sum(
                        1 for row in rows if bool(row.get("drift_detected"))
                    ),
                }
            },
        )

        return rows

    def extract_drift_metrics_from_report(
        self,
        *,
        report: dict[str, Any],
        model_name: str,
        model_version: str,
        reference_window_start: object = None,
        reference_window_end: object = None,
        current_window_start: object = None,
        current_window_end: object = None,
    ) -> list[dict[str, Any]]:
        """
        Extrait toutes les métriques de drift normalisées.

        Produit :
        - 1 ligne globale : feature_name="__dataset__"
        - N lignes feature-level : une ligne par colonne analysée
        """
        dataset_summary = self.extract_dataset_drift_summary(report)

        rows: list[dict[str, Any]] = [
            {
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": "__dataset__",
                "metric_name": "share_of_drifted_columns",
                "metric_value": self._coerce_float(
                    dataset_summary.get("share_of_drifted_columns"),
                    default=0.0,
                ),
                "threshold_value": None,
                "drift_detected": bool(dataset_summary.get("drift_detected", False)),
                "details": dataset_summary,
                "reference_window_start": reference_window_start,
                "reference_window_end": reference_window_end,
                "current_window_start": current_window_start,
                "current_window_end": current_window_end,
            }
        ]

        feature_rows = self.extract_feature_drift_rows_from_report(
            report=report,
            model_name=model_name,
            model_version=model_version,
            reference_window_start=reference_window_start,
            reference_window_end=reference_window_end,
            current_window_start=current_window_start,
            current_window_end=current_window_end,
        )

        rows.extend(feature_rows)

        logger.info(
            "Extracted normalized drift metrics from Evidently report",
            extra={
                "extra_data": {
                    "event": "evidently_extract_metrics_success",
                    "model_name": model_name,
                    "model_version": model_version,
                    "logged_metrics": len(rows),
                    "feature_level_metrics": len(feature_rows),
                }
            },
        )

        return rows

    # =========================================================================
    # Persistance commune
    # =========================================================================

    def _persist_drift_rows(
        self,
        *,
        drift_rows: list[dict[str, Any]],
    ) -> None:
        """
        Persiste les lignes de drift via MonitoringService.
        """
        for row in drift_rows:
            self.monitoring_service.log_drift_metric(
                model_name=str(row["model_name"]),
                model_version=str(row["model_version"]),
                feature_name=str(row["feature_name"]),
                metric_name=str(row["metric_name"]),
                metric_value=float(row["metric_value"]),
                threshold_value=(
                    float(row["threshold_value"])
                    if row.get("threshold_value") is not None
                    else None
                ),
                drift_detected=bool(row.get("drift_detected", False)),
                details=(
                    row.get("details")
                    if isinstance(row.get("details"), dict)
                    else None
                ),
                reference_window_start=row.get("reference_window_start"),
                reference_window_end=row.get("reference_window_end"),
                current_window_start=row.get("current_window_start"),
                current_window_end=row.get("current_window_end"),
            )

    # =========================================================================
    # Orchestration : feature store
    # =========================================================================

    def run_and_persist_data_drift_from_feature_store(
        self,
        *,
        model_name: str,
        model_version: str | None,
        source_table: str | None = None,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        """
        Lance Evidently depuis les snapshots feature_store_monitoring.

        Ce flux est celui utilisé après les simulations du dashboard.
        """
        resolved_model_version = model_version or "unknown"

        init_monitoring_reference_cache(Path(MONITORING_DIR))

        reference_df = get_reference_features_raw_df()
        feature_names = list(reference_df.columns)

        current_df = self.monitoring_service.get_feature_store_dataframe_for_drift(
            model_name=model_name,
            model_version=model_version,
            source_table=source_table,
            limit=max_rows,
        )

        common_cols_count = len(
            set(reference_df.columns).intersection(set(current_df.columns))
        )

        logger.info(
            "Feature store dataframe reconstructed for Evidently",
            extra={
                "extra_data": {
                    "event": "evidently_feature_store_dataframe_ready",
                    "model_name": model_name,
                    "model_version": resolved_model_version,
                    "source_table": source_table,
                    "reference_rows": len(reference_df),
                    "current_rows": len(current_df),
                    "reference_cols_count": len(reference_df.columns),
                    "current_cols_count": len(current_df.columns),
                    "common_cols_count": common_cols_count,
                    "reference_columns_sample": list(reference_df.columns)[:20],
                    "current_columns_sample": list(current_df.columns)[:20],
                }
            },
        )

        if current_df.empty:
            return self._build_response_payload(
                success=False,
                message="Aucune donnée trouvée dans feature_store_monitoring.",
                model_name=model_name,
                model_version=resolved_model_version,
                reference_kind="raw",
                current_kind="raw",
                logged_metrics=0,
                reference_rows=len(reference_df),
                current_rows=0,
                analyzed_columns=[],
                report={},
            )

        if common_cols_count == 0:
            return self._build_response_payload(
                success=False,
                message=(
                    "Aucune colonne commune entre la référence et les snapshots. "
                    "Vérifie que les features loguées ont les mêmes noms."
                ),
                model_name=model_name,
                model_version=resolved_model_version,
                reference_kind="raw",
                current_kind="raw",
                logged_metrics=0,
                reference_rows=len(reference_df),
                current_rows=len(current_df),
                analyzed_columns=[],
                report={},
            )

        return self.run_and_persist_data_drift_from_dataframes(
            model_name=model_name,
            model_version=resolved_model_version,
            reference_df=reference_df,
            current_df=current_df,
            feature_names=feature_names,
            max_rows=max_rows,
        )

    # =========================================================================
    # Orchestration : chargement automatique
    # =========================================================================

    def run_and_persist_data_drift_analysis(
        self,
        *,
        model_name: str,
        model_version: str | None,
        reference_kind: Literal["raw", "transformed"],
        current_kind: Literal["raw", "transformed"],
        monitoring_dir: str | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """
        Charge les données, exécute Evidently, puis persiste les métriques.
        """
        resolved_model_version = model_version or "unknown"
        resolved_monitoring_dir = self._resolve_monitoring_dir(monitoring_dir)

        logger.info(
            "Starting end-to-end Evidently drift analysis",
            extra={
                "extra_data": {
                    "event": "evidently_analysis_start",
                    "model_name": model_name,
                    "model_version": resolved_model_version,
                    "reference_kind": reference_kind,
                    "current_kind": current_kind,
                    "monitoring_dir": str(resolved_monitoring_dir),
                    "max_rows": max_rows,
                }
            },
        )

        init_monitoring_reference_cache(resolved_monitoring_dir)

        reference_df = self._load_reference_dataframe(reference_kind=reference_kind)
        current_df = self._load_current_dataframe(current_kind=current_kind)
        feature_names = self._load_feature_names(reference_kind=reference_kind)

        reference_df = self._limit_dataframe_rows(
            reference_df,
            max_rows,
            name="reference_df",
        )
        current_df = self._limit_dataframe_rows(
            current_df,
            max_rows,
            name="current_df",
        )

        result = self.run_data_drift_report(
            reference_df=reference_df,
            current_df=current_df,
            feature_names=feature_names,
        )

        if not bool(result.get("success", False)):
            return self._build_response_payload(
                success=False,
                message=str(result.get("message", "Échec de l'analyse Evidently.")),
                model_name=model_name,
                model_version=resolved_model_version,
                reference_kind=reference_kind,
                current_kind=current_kind,
                logged_metrics=0,
                reference_rows=self._coerce_int(result.get("reference_rows"), 0),
                current_rows=self._coerce_int(result.get("current_rows"), 0),
                analyzed_columns=[
                    str(col)
                    for col in result.get("analyzed_columns", [])
                    if isinstance(col, (str, int, float))
                ],
                report=(
                    result.get("report", {})
                    if isinstance(result.get("report"), dict)
                    else {}
                ),
            )

        report = result.get("report", {})

        if not isinstance(report, dict):
            raise ValueError("Le rapport Evidently généré n'est pas exploitable.")

        drift_rows = self.extract_drift_metrics_from_report(
            report=report,
            model_name=model_name,
            model_version=resolved_model_version,
        )

        self._persist_drift_rows(drift_rows=drift_rows)

        logger.info(
            "Evidently drift metrics persisted successfully",
            extra={
                "extra_data": {
                    "event": "evidently_analysis_persist_success",
                    "model_name": model_name,
                    "model_version": resolved_model_version,
                    "logged_metrics": len(drift_rows),
                    "max_rows": max_rows,
                }
            },
        )

        return self._build_response_payload(
            success=True,
            message="Analyse Evidently exécutée et persistée avec succès.",
            model_name=model_name,
            model_version=resolved_model_version,
            reference_kind=reference_kind,
            current_kind=current_kind,
            logged_metrics=len(drift_rows),
            reference_rows=self._coerce_int(result.get("reference_rows"), 0),
            current_rows=self._coerce_int(result.get("current_rows"), 0),
            analyzed_columns=[
                str(col)
                for col in result.get("analyzed_columns", [])
                if isinstance(col, (str, int, float))
            ],
            report=report,
        )

    # =========================================================================
    # Orchestration : DataFrames déjà construits
    # =========================================================================

    def run_and_persist_data_drift_from_dataframes(
        self,
        *,
        model_name: str,
        model_version: str | None,
        reference_df: pd.DataFrame,
        current_df: pd.DataFrame,
        feature_names: list[str] | None = None,
        reference_window_start: object = None,
        reference_window_end: object = None,
        current_window_start: object = None,
        current_window_end: object = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """
        Exécute Evidently à partir de deux DataFrames déjà construits.
        """
        resolved_model_version = model_version or "unknown"

        logger.info(
            "Starting Evidently drift analysis from provided dataframes",
            extra={
                "extra_data": {
                    "event": "evidently_analysis_from_dataframes_start",
                    "model_name": model_name,
                    "model_version": resolved_model_version,
                    "reference_rows": len(reference_df)
                    if isinstance(reference_df, pd.DataFrame)
                    else None,
                    "current_rows": len(current_df)
                    if isinstance(current_df, pd.DataFrame)
                    else None,
                    "feature_names_count": len(feature_names) if feature_names else 0,
                    "max_rows": max_rows,
                }
            },
        )

        limited_reference_df = self._limit_dataframe_rows(
            reference_df,
            max_rows,
            name="reference_df",
        )
        limited_current_df = self._limit_dataframe_rows(
            current_df,
            max_rows,
            name="current_df",
        )

        result = self.run_data_drift_report(
            reference_df=limited_reference_df,
            current_df=limited_current_df,
            feature_names=feature_names,
        )

        if not bool(result.get("success", False)):
            return {
                "success": False,
                "message": result.get("message", "Échec de l'analyse Evidently."),
                "model_name": model_name,
                "model_version": resolved_model_version,
                "logged_metrics": 0,
                "reference_rows": result.get("reference_rows", 0),
                "current_rows": result.get("current_rows", 0),
                "analyzed_columns": result.get("analyzed_columns", []),
                "report": result.get("report", {}),
            }

        report = result.get("report", {})

        if not isinstance(report, dict):
            raise ValueError("Le rapport Evidently généré n'est pas exploitable.")

        drift_rows = self.extract_drift_metrics_from_report(
            report=report,
            model_name=model_name,
            model_version=resolved_model_version,
            reference_window_start=reference_window_start,
            reference_window_end=reference_window_end,
            current_window_start=current_window_start,
            current_window_end=current_window_end,
        )

        self._persist_drift_rows(drift_rows=drift_rows)

        logger.info(
            "Evidently dataframe-based drift metrics persisted successfully",
            extra={
                "extra_data": {
                    "event": "evidently_analysis_from_dataframes_persist_success",
                    "model_name": model_name,
                    "model_version": resolved_model_version,
                    "logged_metrics": len(drift_rows),
                    "max_rows": max_rows,
                }
            },
        )

        return {
            "success": True,
            "message": "Analyse Evidently exécutée et persistée avec succès.",
            "model_name": model_name,
            "model_version": resolved_model_version,
            "logged_metrics": len(drift_rows),
            "reference_rows": result.get("reference_rows", 0),
            "current_rows": result.get("current_rows", 0),
            "analyzed_columns": result.get("analyzed_columns", []),
            "report": report,
        }