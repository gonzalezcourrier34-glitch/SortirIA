"""
Service métier d'évaluation monitoring du modèle en production.

Ce module encapsule :
- le chargement des prédictions et des vérités terrain
- la construction du dataset d'évaluation
- le calcul des métriques métier et ML
- l'enregistrement des résultats via MonitoringService

Objectif
--------
Déporter hors des routes FastAPI toute la logique d'évaluation
afin de garder des routes fines, lisibles et testables.

Notes
-----
- La lecture des données passe par des requêtes ORM SQLAlchemy.
- L'écriture en base passe par MonitoringService.
- Ce service orchestre donc la chaîne complète d'évaluation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    fbeta_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.model.model_SQLalchemy import GroundTruthLabel, ModelRegistry, PredictionLog
from app.services.monitoring_service import MonitoringService


logger = logging.getLogger(__name__)


class MonitoringEvaluationService:
    """
    Service métier d'évaluation monitoring.

    Parameters
    ----------
    db : Session
        Session SQLAlchemy active.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.monitoring_service = MonitoringService(db)

    # =========================================================================
    # Helpers internes
    # =========================================================================

    def _get_model_columns(self, model_cls: type[Any]) -> set[str]:
        """
        Retourne l'ensemble des colonnes mappées d'un modèle SQLAlchemy.
        """
        return set(model_cls.__table__.columns.keys())

    def _pick_column(self, model_cls: type[Any], candidates: list[str]) -> str | None:
        """
        Sélectionne le premier nom de colonne existant dans le modèle.
        """
        cols = self._get_model_columns(model_cls)

        for name in candidates:
            if name in cols:
                return name

        return None

    def _safe_get(self, obj: Any, attr: str | None) -> Any:
        """
        Lit un attribut de manière sûre.
        """
        if attr is None:
            return None

        return getattr(obj, attr, None)

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

    def _build_response_payload(
        self,
        *,
        success: bool,
        message: str,
        model_name: str,
        model_version: str,
        dataset_name: str,
        logged_metrics: int,
        sample_size: int,
        matched_rows: int,
        threshold_used: float | None,
        window_start: datetime | None,
        window_end: datetime | None,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Construit un payload de réponse homogène pour succès et échec.
        """
        return {
            "success": success,
            "message": message,
            "model_name": model_name,
            "model_version": model_version,
            "dataset_name": dataset_name,
            "logged_metrics": logged_metrics,
            "sample_size": sample_size,
            "matched_rows": matched_rows,
            "threshold_used": threshold_used,
            "window_start": window_start,
            "window_end": window_end,
            "metrics": metrics,
        }

    # =========================================================================
    # Chargement des données
    # =========================================================================

    def _resolve_model_identity(
        self,
        *,
        model_name: str,
        model_version: str | None,
    ) -> tuple[str, str]:
        """
        Résout l'identité exacte du modèle.
        """
        model_name_col = self._pick_column(ModelRegistry, ["model_name"])
        model_version_col = self._pick_column(ModelRegistry, ["model_version", "version"])
        is_active_col = self._pick_column(ModelRegistry, ["is_active"])
        stage_col = self._pick_column(ModelRegistry, ["stage"])

        query = self.db.query(ModelRegistry)

        if model_name_col is not None:
            query = query.filter(getattr(ModelRegistry, model_name_col) == model_name)

        if model_version is not None and model_version_col is not None:
            query = query.filter(getattr(ModelRegistry, model_version_col) == model_version)
        else:
            if is_active_col is not None:
                query = query.filter(getattr(ModelRegistry, is_active_col).is_(True))
            elif stage_col is not None:
                query = query.filter(getattr(ModelRegistry, stage_col) == "production")

        row = query.order_by(ModelRegistry.id.desc()).first()

        if row is None:
            if model_version is not None:
                return model_name, model_version
            raise ValueError(
                f"Aucun modèle actif trouvé pour model_name={model_name}."
            )

        resolved_model_name = str(self._safe_get(row, model_name_col) or model_name)
        resolved_model_version = str(
            self._safe_get(row, model_version_col) or model_version or "unknown"
        )

        return resolved_model_name, resolved_model_version

    def _load_prediction_logs(
        self,
        *,
        model_name: str,
        model_version: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Charge les prédictions depuis prediction_logs.
        """
        request_id_col = self._pick_column(PredictionLog, ["request_id"])
        client_id_col = self._pick_column(PredictionLog, ["client_id"])
        prediction_col = self._pick_column(PredictionLog, ["prediction", "prediction_value"])
        score_col = self._pick_column(
            PredictionLog,
            ["score", "proba", "probability", "prediction_score"],
        )
        threshold_col = self._pick_column(PredictionLog, ["threshold_used", "threshold"])
        latency_col = self._pick_column(PredictionLog, ["latency_ms"])
        created_at_col = self._pick_column(
            PredictionLog,
            ["created_at", "timestamp", "requested_at", "prediction_timestamp"],
        )
        model_name_col = self._pick_column(PredictionLog, ["model_name"])
        model_version_col = self._pick_column(PredictionLog, ["model_version"])
        status_code_col = self._pick_column(PredictionLog, ["status_code"])
        has_error_col = self._pick_column(PredictionLog, ["has_error"])

        query = self.db.query(PredictionLog)

        filters = []
        if model_name_col is not None:
            filters.append(getattr(PredictionLog, model_name_col) == model_name)
        if model_version_col is not None:
            filters.append(getattr(PredictionLog, model_version_col) == model_version)
        if created_at_col is not None and window_start is not None:
            filters.append(getattr(PredictionLog, created_at_col) >= window_start)
        if created_at_col is not None and window_end is not None:
            filters.append(getattr(PredictionLog, created_at_col) <= window_end)
        if status_code_col is not None:
            filters.append(getattr(PredictionLog, status_code_col) == 200)
        if has_error_col is not None:
            filters.append(getattr(PredictionLog, has_error_col).is_(False))

        if filters:
            query = query.filter(and_(*filters))

        rows = query.all()

        records: list[dict[str, Any]] = []
        for row in rows:
            records.append(
                {
                    "request_id": self._safe_get(row, request_id_col),
                    "client_id": self._safe_get(row, client_id_col),
                    "prediction": self._safe_get(row, prediction_col),
                    "score": self._safe_get(row, score_col),
                    "threshold_used": self._safe_get(row, threshold_col),
                    "latency_ms": self._safe_get(row, latency_col),
                    "created_at": self._safe_get(row, created_at_col),
                }
            )

        df = pd.DataFrame(records)

        if df.empty:
            return df

        for col in ["prediction", "score", "threshold_used", "latency_ms"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _load_ground_truth_labels(
        self,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Charge les vérités terrain depuis ground_truth_labels.
        """
        request_id_col = self._pick_column(GroundTruthLabel, ["request_id"])
        client_id_col = self._pick_column(GroundTruthLabel, ["client_id"])
        target_col = self._pick_column(
            GroundTruthLabel,
            ["target", "true_label", "ground_truth", "label", "actual_target"],
        )
        created_at_col = self._pick_column(
            GroundTruthLabel,
            ["created_at", "timestamp", "labeled_at"],
        )

        if target_col is None:
            raise ValueError(
                "Impossible d'identifier la colonne de vérité terrain "
                "dans GroundTruthLabel."
            )

        query = self.db.query(GroundTruthLabel)

        filters = []
        if created_at_col is not None and window_start is not None:
            filters.append(getattr(GroundTruthLabel, created_at_col) >= window_start)
        if created_at_col is not None and window_end is not None:
            filters.append(getattr(GroundTruthLabel, created_at_col) <= window_end)

        if filters:
            query = query.filter(and_(*filters))

        rows = query.all()

        records: list[dict[str, Any]] = []
        for row in rows:
            records.append(
                {
                    "request_id": self._safe_get(row, request_id_col),
                    "client_id": self._safe_get(row, client_id_col),
                    "y_true": self._safe_get(row, target_col),
                    "gt_created_at": self._safe_get(row, created_at_col),
                }
            )

        df = pd.DataFrame(records)

        if df.empty:
            return df

        df["y_true"] = pd.to_numeric(df["y_true"], errors="coerce")

        return df

    def _build_evaluation_dataframe(
        self,
        *,
        predictions_df: pd.DataFrame,
        ground_truth_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Joint les prédictions et les vérités terrain.
        """
        pred = self._ensure_dataframe(predictions_df, "predictions_df")
        gt = self._ensure_dataframe(ground_truth_df, "ground_truth_df")

        can_join_on_request_id = (
            "request_id" in pred.columns
            and "request_id" in gt.columns
            and pred["request_id"].notna().any()
            and gt["request_id"].notna().any()
        )

        can_join_on_client_id = (
            "client_id" in pred.columns
            and "client_id" in gt.columns
            and pred["client_id"].notna().any()
            and gt["client_id"].notna().any()
        )

        if can_join_on_request_id:
            df = pred.merge(
                gt,
                on="request_id",
                how="inner",
                suffixes=("", "_gt"),
            )
        elif can_join_on_client_id:
            df = pred.merge(
                gt,
                on="client_id",
                how="inner",
                suffixes=("", "_gt"),
            )
        else:
            raise ValueError(
                "Impossible de joindre prediction_logs et ground_truth_labels : "
                "ni request_id ni client_id commun exploitable."
            )

        if df.empty:
            raise ValueError(
                "La jointure entre les prédictions et les vérités terrain est vide."
            )

        df = df.dropna(subset=["y_true"])

        if df.empty:
            raise ValueError(
                "Aucune ligne exploitable après suppression des y_true manquants."
            )

        return df

    # =========================================================================
    # Calcul des métriques
    # =========================================================================

    def compute_evaluation_metrics(
        self,
        *,
        evaluation_df: pd.DataFrame,
        beta: float = 2.0,
        cost_fn: float = 10.0,
        cost_fp: float = 1.0,
    ) -> dict[str, Any]:
        """
        Calcule les métriques d'évaluation sur un DataFrame joint.
        """
        df = self._ensure_dataframe(evaluation_df, "evaluation_df")

        y_true = df["y_true"].astype(int).to_numpy()

        threshold_used: float | None = None
        if "threshold_used" in df.columns and df["threshold_used"].notna().any():
            threshold_used = float(
                pd.to_numeric(df["threshold_used"], errors="coerce").dropna().iloc[-1]
            )

        y_score: np.ndarray | None = None
        y_pred: np.ndarray

        if "score" in df.columns and df["score"].notna().any():
            y_score = pd.to_numeric(df["score"], errors="coerce").fillna(0.0).to_numpy()

            if threshold_used is None:
                threshold_used = 0.5

            y_pred = (y_score >= threshold_used).astype(int)

        elif "prediction" in df.columns and df["prediction"].notna().any():
            y_pred = (
                pd.to_numeric(df["prediction"], errors="coerce")
                .fillna(0)
                .astype(int)
                .to_numpy()
            )

        else:
            raise ValueError(
                "Impossible de calculer les métriques : ni `score` ni `prediction` disponible."
            )

        precision = precision_score(y_true, y_pred, zero_division=0)
        recall = recall_score(y_true, y_pred, zero_division=0)
        accuracy = accuracy_score(y_true, y_pred)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        fbeta = fbeta_score(y_true, y_pred, beta=beta, zero_division=0)

        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()

        business_cost = float((cost_fn * fn) + (cost_fp * fp))

        metrics: dict[str, Any] = {
            "precision": float(precision),
            "recall": float(recall),
            "accuracy": float(accuracy),
            "f1": float(f1),
            "fbeta": float(fbeta),
            "tp": int(tp),
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "business_cost": business_cost,
            "cost_per_sample": float(business_cost / max(len(df), 1)),
            "positive_rate_true": float(np.mean(y_true)),
            "positive_rate_pred": float(np.mean(y_pred)),
            "sample_size": int(len(df)),
            "matched_rows": int(len(df)),
            "threshold_used": threshold_used,
        }

        if y_score is not None and len(np.unique(y_true)) >= 2:
            metrics["roc_auc"] = float(roc_auc_score(y_true, y_score))
            metrics["pr_auc"] = float(average_precision_score(y_true, y_score))

        if "latency_ms" in df.columns and df["latency_ms"].notna().any():
            metrics["latency_mean_ms"] = float(
                pd.to_numeric(df["latency_ms"], errors="coerce").mean()
            )

        logger.info(
            "Evaluation metrics computed successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_evaluation_compute_success",
                    "sample_size": metrics["sample_size"],
                    "threshold_used": metrics["threshold_used"],
                    "has_roc_auc": "roc_auc" in metrics,
                }
            },
        )

        return metrics

    # =========================================================================
    # Orchestration complète
    # =========================================================================

    def run_and_persist_monitoring_evaluation(
        self,
        *,
        model_name: str,
        model_version: str | None = None,
        dataset_name: str = "scoring_prod",
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        beta: float = 2.0,
        cost_fn: float = 10.0,
        cost_fp: float = 1.0,
    ) -> dict[str, Any]:
        """
        Charge les prédictions et les vérités terrain,
        calcule les métriques d'évaluation puis les persiste.
        """
        resolved_model_name, resolved_model_version = self._resolve_model_identity(
            model_name=model_name,
            model_version=model_version,
        )

        logger.info(
            "Starting end-to-end monitoring evaluation",
            extra={
                "extra_data": {
                    "event": "monitoring_evaluation_start",
                    "model_name": resolved_model_name,
                    "model_version": resolved_model_version,
                    "dataset_name": dataset_name,
                    "window_start": window_start.isoformat() if window_start else None,
                    "window_end": window_end.isoformat() if window_end else None,
                    "beta": beta,
                    "cost_fn": cost_fn,
                    "cost_fp": cost_fp,
                }
            },
        )

        try:
            predictions_df = self._load_prediction_logs(
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                window_start=window_start,
                window_end=window_end,
            )

            ground_truth_df = self._load_ground_truth_labels(
                window_start=window_start,
                window_end=window_end,
            )

            logger.info(
                "Prediction logs and ground truth loaded for evaluation",
                extra={
                    "extra_data": {
                        "event": "monitoring_evaluation_data_loaded",
                        "model_name": resolved_model_name,
                        "model_version": resolved_model_version,
                        "predictions_rows": len(predictions_df),
                        "ground_truth_rows": len(ground_truth_df),
                    }
                },
            )

            evaluation_df = self._build_evaluation_dataframe(
                predictions_df=predictions_df,
                ground_truth_df=ground_truth_df,
            )

            metrics = self.compute_evaluation_metrics(
                evaluation_df=evaluation_df,
                beta=beta,
                cost_fn=cost_fn,
                cost_fp=cost_fp,
            )

            self.monitoring_service.log_evaluation_metrics(
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                dataset_name=dataset_name,
                window_start=window_start,
                window_end=window_end,
                roc_auc=(
                    self._coerce_float(metrics.get("roc_auc"))
                    if metrics.get("roc_auc") is not None
                    else None
                ),
                pr_auc=(
                    self._coerce_float(metrics.get("pr_auc"))
                    if metrics.get("pr_auc") is not None
                    else None
                ),
                precision_score=self._coerce_float(metrics.get("precision")),
                recall_score=self._coerce_float(metrics.get("recall")),
                f1_score=self._coerce_float(metrics.get("f1")),
                fbeta_score=self._coerce_float(metrics.get("fbeta")),
                business_cost=self._coerce_float(metrics.get("business_cost")),
                tn=self._coerce_int(metrics.get("tn")),
                fp=self._coerce_int(metrics.get("fp")),
                fn=self._coerce_int(metrics.get("fn")),
                tp=self._coerce_int(metrics.get("tp")),
                sample_size=self._coerce_int(metrics.get("sample_size")),
            )

            logger.info(
                "Monitoring evaluation metrics persisted successfully",
                extra={
                    "extra_data": {
                        "event": "monitoring_evaluation_persist_success",
                        "model_name": resolved_model_name,
                        "model_version": resolved_model_version,
                        "dataset_name": dataset_name,
                        "logged_metrics": 1,
                        "sample_size": metrics.get("sample_size", 0),
                    }
                },
            )

            return self._build_response_payload(
                success=True,
                message="Évaluation monitoring exécutée et persistée avec succès.",
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                dataset_name=dataset_name,
                logged_metrics=1,
                sample_size=self._coerce_int(metrics.get("sample_size"), 0),
                matched_rows=self._coerce_int(metrics.get("matched_rows"), 0),
                threshold_used=(
                    self._coerce_float(metrics.get("threshold_used"))
                    if metrics.get("threshold_used") is not None
                    else None
                ),
                window_start=window_start,
                window_end=window_end,
                metrics=metrics,
            )

        except Exception as exc:
            logger.exception(
                "Unexpected error during monitoring evaluation",
                extra={
                    "extra_data": {
                        "event": "monitoring_evaluation_exception",
                        "model_name": resolved_model_name,
                        "model_version": resolved_model_version,
                        "dataset_name": dataset_name,
                        "window_start": window_start.isoformat() if window_start else None,
                        "window_end": window_end.isoformat() if window_end else None,
                        "error": str(exc),
                    }
                },
            )

            return self._build_response_payload(
                success=False,
                message=f"Erreur pendant l'évaluation monitoring : {exc}",
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                dataset_name=dataset_name,
                logged_metrics=0,
                sample_size=0,
                matched_rows=0,
                threshold_used=None,
                window_start=window_start,
                window_end=window_end,
                metrics={},
            )

    def run_and_persist_monitoring_evaluation_from_dataframes(
        self,
        *,
        model_name: str,
        model_version: str | None = None,
        prediction_df: pd.DataFrame,
        ground_truth_df: pd.DataFrame,
        dataset_name: str = "scoring_prod",
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        beta: float = 2.0,
        cost_fn: float = 10.0,
        cost_fp: float = 1.0,
    ) -> dict[str, Any]:
        """
        Exécute une évaluation monitoring à partir de DataFrames déjà construits,
        puis persiste les métriques dans la base.
        """
        resolved_model_name = model_name
        resolved_model_version = model_version or "unknown"

        logger.info(
            "Starting monitoring evaluation from provided dataframes",
            extra={
                "extra_data": {
                    "event": "monitoring_evaluation_from_dataframes_start",
                    "model_name": resolved_model_name,
                    "model_version": resolved_model_version,
                    "dataset_name": dataset_name,
                    "prediction_rows": len(prediction_df) if isinstance(prediction_df, pd.DataFrame) else None,
                    "ground_truth_rows": len(ground_truth_df) if isinstance(ground_truth_df, pd.DataFrame) else None,
                    "window_start": window_start.isoformat() if window_start else None,
                    "window_end": window_end.isoformat() if window_end else None,
                }
            },
        )

        try:
            evaluation_df = self._build_evaluation_dataframe(
                predictions_df=prediction_df,
                ground_truth_df=ground_truth_df,
            )

            metrics = self.compute_evaluation_metrics(
                evaluation_df=evaluation_df,
                beta=beta,
                cost_fn=cost_fn,
                cost_fp=cost_fp,
            )

            self.monitoring_service.log_evaluation_metrics(
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                dataset_name=dataset_name,
                window_start=window_start,
                window_end=window_end,
                roc_auc=(
                    self._coerce_float(metrics.get("roc_auc"))
                    if metrics.get("roc_auc") is not None
                    else None
                ),
                pr_auc=(
                    self._coerce_float(metrics.get("pr_auc"))
                    if metrics.get("pr_auc") is not None
                    else None
                ),
                precision_score=self._coerce_float(metrics.get("precision")),
                recall_score=self._coerce_float(metrics.get("recall")),
                f1_score=self._coerce_float(metrics.get("f1")),
                fbeta_score=self._coerce_float(metrics.get("fbeta")),
                business_cost=self._coerce_float(metrics.get("business_cost")),
                tn=self._coerce_int(metrics.get("tn")),
                fp=self._coerce_int(metrics.get("fp")),
                fn=self._coerce_int(metrics.get("fn")),
                tp=self._coerce_int(metrics.get("tp")),
                sample_size=self._coerce_int(metrics.get("sample_size")),
            )

            logger.info(
                "Monitoring evaluation from dataframes persisted successfully",
                extra={
                    "extra_data": {
                        "event": "monitoring_evaluation_from_dataframes_persist_success",
                        "model_name": resolved_model_name,
                        "model_version": resolved_model_version,
                        "dataset_name": dataset_name,
                        "logged_metrics": 1,
                    }
                },
            )

            return self._build_response_payload(
                success=True,
                message="Évaluation monitoring exécutée et persistée avec succès.",
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                dataset_name=dataset_name,
                logged_metrics=1,
                sample_size=self._coerce_int(metrics.get("sample_size"), 0),
                matched_rows=self._coerce_int(metrics.get("matched_rows"), 0),
                threshold_used=(
                    self._coerce_float(metrics.get("threshold_used"))
                    if metrics.get("threshold_used") is not None
                    else None
                ),
                window_start=window_start,
                window_end=window_end,
                metrics=metrics,
            )

        except Exception as exc:
            logger.exception(
                "Unexpected error during dataframe-based monitoring evaluation",
                extra={
                    "extra_data": {
                        "event": "monitoring_evaluation_from_dataframes_exception",
                        "model_name": resolved_model_name,
                        "model_version": resolved_model_version,
                        "dataset_name": dataset_name,
                        "error": str(exc),
                    }
                },
            )

            return self._build_response_payload(
                success=False,
                message=f"Erreur pendant l'évaluation monitoring : {exc}",
                model_name=resolved_model_name,
                model_version=resolved_model_version,
                dataset_name=dataset_name,
                logged_metrics=0,
                sample_size=0,
                matched_rows=0,
                threshold_used=None,
                window_start=window_start,
                window_end=window_end,
                metrics={},
            )