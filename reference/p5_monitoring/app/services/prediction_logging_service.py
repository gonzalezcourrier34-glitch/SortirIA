"""
Service de journalisation des prédictions et snapshots de features.

Ce module centralise l'orchestration métier des événements liés à l'inférence :
- log principal de prédiction dans `prediction_logs`
- snapshot détaillé des features dans `prediction_features_snapshot`
- alimentation optionnelle de `feature_store_monitoring`

Objectif
--------
Séparer la logique métier de journalisation des couches CRUD
afin de garder des routes et services lisibles.

Notes
-----
- Les commits et rollbacks sont gérés par l'appelant.
- En cas d'erreur de prédiction, la ligne est tout de même journalisée
  avec `prediction=0`, `score=0.0` et un `error_message` renseigné.
- La persistance dans `feature_store_monitoring` passe par `crud.monitoring`.
- La source journalisée par défaut correspond au CSV configuré dans l'application.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import APPLICATION_CSV
from app.crud import monitoring as monitoring_crud
from app.crud import prediction as prediction_crud
from app.model.model_SQLalchemy import PredictionLog


logger = logging.getLogger(__name__)


# =============================================================================
# Helpers
# =============================================================================

def _utc_now() -> datetime:
    """
    Retourne l'heure actuelle en UTC.

    Returns
    -------
    datetime
        Horodatage timezone-aware.
    """
    return datetime.now(timezone.utc)


def _is_missing(value: Any) -> bool:
    """
    Indique si une valeur doit être considérée comme manquante.

    Parameters
    ----------
    value : Any
        Valeur à tester.

    Returns
    -------
    bool
        True si la valeur est manquante, sinon False.
    """
    if value is None:
        return True

    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _to_python_scalar(value: Any) -> Any:
    """
    Convertit une valeur pandas / NumPy vers un type Python natif.

    Parameters
    ----------
    value : Any
        Valeur source.

    Returns
    -------
    Any
        Valeur convertie.
    """
    if _is_missing(value):
        return None

    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None

    return value


def _to_json_compatible(value: Any) -> Any:
    """
    Rend une structure compatible JSON / JSONB.

    Parameters
    ----------
    value : Any
        Valeur ou structure à convertir.

    Returns
    -------
    Any
        Structure compatible JSON.
    """
    value = _to_python_scalar(value)

    if value is None:
        return None

    if isinstance(value, dict):
        return {str(k): _to_json_compatible(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_json_compatible(v) for v in value]

    if isinstance(value, datetime):
        return value.isoformat()

    return value


def _resolve_source_table(source_table: str | None = None) -> str:
    """
    Résout la source logique à journaliser.

    Si `source_table` est fourni, il est conservé.
    Sinon, on utilise le nom du CSV configuré dans l'application.

    Parameters
    ----------
    source_table : str | None
        Source explicite éventuelle.

    Returns
    -------
    str
        Source logique à journaliser.
    """
    if source_table is not None and str(source_table).strip():
        return str(source_table).strip()

    return Path(APPLICATION_CSV).name


def dataframe_row_to_feature_records(
    features_df: pd.DataFrame,
    *,
    request_id: str,
    model_name: str,
    model_version: str,
    client_id: int | None = None,
    source_table: str | None = None,
) -> list[dict[str, Any]]:
    """
    Convertit une ligne de DataFrame en liste d'enregistrements feature.

    Parameters
    ----------
    features_df : pd.DataFrame
        DataFrame contenant exactement une ligne de features.
    request_id : str
        Identifiant unique de requête.
    model_name : str
        Nom du modèle.
    model_version : str
        Version du modèle.
    client_id : int | None, optional
        Identifiant client.
    source_table : str | None, optional
        Source logique éventuelle. Si absente, le CSV configuré est utilisé.

    Returns
    -------
    list[dict[str, Any]]
        Liste de dictionnaires, une entrée par feature.

    Raises
    ------
    TypeError
        Si `features_df` n'est pas un DataFrame pandas.
    ValueError
        Si le DataFrame ne contient pas exactement une ligne.
    """
    if not isinstance(features_df, pd.DataFrame):
        raise TypeError("`features_df` doit être un DataFrame pandas.")

    if len(features_df) != 1:
        raise ValueError(
            "Le DataFrame de features doit contenir exactement une ligne. "
            f"Nombre de lignes reçu : {len(features_df)}"
        )

    resolved_source_table = _resolve_source_table(source_table)

    row = features_df.iloc[0].to_dict()
    records: list[dict[str, Any]] = []

    for feature_name, raw_value in row.items():
        value = _to_python_scalar(raw_value)

        records.append(
            {
                "request_id": request_id,
                "client_id": client_id,
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": str(feature_name),
                "feature_value": None if value is None else str(value),
                "feature_type": type(value).__name__ if value is not None else None,
                "source_table": resolved_source_table,
            }
        )

    logger.info(
        "Feature records built from dataframe row",
        extra={
            "extra_data": {
                "event": "prediction_logging_build_feature_records_success",
                "request_id": request_id,
                "client_id": client_id,
                "model_name": model_name,
                "model_version": model_version,
                "source_table": resolved_source_table,
                "feature_count": len(records),
            }
        },
    )

    return records


# =============================================================================
# Service principal
# =============================================================================

class PredictionLoggingService:
    """
    Service applicatif de journalisation des inférences.

    Parameters
    ----------
    db : Session
        Session SQLAlchemy active.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def log_prediction(
        self,
        *,
        request_id: str,
        model_name: str,
        model_version: str,
        prediction: int,
        score: float,
        threshold_used: float | None,
        latency_ms: float | None,
        inference_latency_ms: float | None = None,
        input_data: dict[str, Any] | None,
        output_data: dict[str, Any] | None,
        client_id: int | None = None,
        status_code: int | None = 200,
        error_message: str | None = None,
        event_time: datetime | None = None,
    ) -> PredictionLog:
        """
        Crée un log principal de prédiction.

        Returns
        -------
        PredictionLog
            Entité SQLAlchemy ajoutée à la session.
        """
        logger.info(
            "PredictionLoggingService logging prediction",
            extra={
                "extra_data": {
                    "event": "prediction_logging_log_prediction_start",
                    "request_id": request_id,
                    "client_id": client_id,
                    "model_name": model_name,
                    "model_version": model_version,
                    "prediction": prediction,
                    "status_code": status_code,
                    "has_error": error_message is not None,
                }
            },
        )

        event_time = event_time or _utc_now()

        safe_input_data = _to_json_compatible(input_data)
        if safe_input_data is None:
            safe_input_data = {}

        entity = prediction_crud.create_prediction_log(
            self.db,
            request_id=request_id,
            client_id=client_id,
            model_name=model_name,
            model_version=model_version,
            prediction=int(prediction),
            score=float(score),
            threshold_used=None if threshold_used is None else float(threshold_used),
            latency_ms=None if latency_ms is None else float(latency_ms),
            inference_latency_ms=None if inference_latency_ms is None else float(inference_latency_ms),
            input_data=safe_input_data,
            output_data=_to_json_compatible(output_data),
            prediction_timestamp=event_time,
            status_code=status_code,
            error_message=error_message,
        )

        logger.info(
            "PredictionLoggingService logged prediction successfully",
            extra={
                "extra_data": {
                    "event": "prediction_logging_log_prediction_success",
                    "id": entity.id,
                    "request_id": entity.request_id,
                    "client_id": entity.client_id,
                    "model_name": entity.model_name,
                    "model_version": entity.model_version,
                    "prediction": entity.prediction,
                    "status_code": entity.status_code,
                }
            },
        )

        return entity

    def log_prediction_error(
        self,
        *,
        request_id: str,
        model_name: str,
        model_version: str,
        input_data: dict[str, Any] | None,
        error_message: str,
        client_id: int | None = None,
        status_code: int | None = 500,
        latency_ms: float | None = None,
        inference_latency_ms: float | None = None,
        event_time: datetime | None = None,
    ) -> PredictionLog:
        """
        Crée un log d'erreur de prédiction.

        Notes
        -----
        La ligne reste compatible avec la contrainte SQL sur `prediction`
        en utilisant `prediction=0` comme valeur technique de repli.

        Returns
        -------
        PredictionLog
            Entité SQLAlchemy ajoutée à la session.
        """
        logger.warning(
            "PredictionLoggingService logging prediction error",
            extra={
                "extra_data": {
                    "event": "prediction_logging_log_error_start",
                    "request_id": request_id,
                    "client_id": client_id,
                    "model_name": model_name,
                    "model_version": model_version,
                    "status_code": status_code,
                    "error_message": error_message,
                }
            },
        )

        return self.log_prediction(
            request_id=request_id,
            model_name=model_name,
            model_version=model_version,
            prediction=0,
            score=0.0,
            threshold_used=None,
            latency_ms=latency_ms,
            inference_latency_ms=inference_latency_ms,
            input_data=input_data,
            output_data={"status": "error", "message": error_message},
            client_id=client_id,
            status_code=status_code,
            error_message=error_message,
            event_time=event_time,
        )

    def log_prediction_features_snapshot(
        self,
        feature_records: list[dict[str, Any]],
        *,
        event_time: datetime | None = None,
    ) -> None:
        """
        Enregistre un snapshot détaillé des features de prédiction.
        """
        if not feature_records:
            raise ValueError(
                "Aucun feature record à insérer dans prediction_features_snapshot."
            )

        event_time = event_time or _utc_now()

        logger.info(
            "PredictionLoggingService logging feature snapshot",
            extra={
                "extra_data": {
                    "event": "prediction_logging_snapshot_start",
                    "feature_count": len(feature_records),
                    "request_id": feature_records[0].get("request_id"),
                    "model_name": feature_records[0].get("model_name"),
                    "model_version": feature_records[0].get("model_version"),
                }
            },
        )

        prediction_crud.create_feature_snapshots(
            self.db,
            records=feature_records,
            timestamp=event_time,
        )

        logger.info(
            "PredictionLoggingService logged feature snapshot successfully",
            extra={
                "extra_data": {
                    "event": "prediction_logging_snapshot_success",
                    "feature_count": len(feature_records),
                    "request_id": feature_records[0].get("request_id"),
                }
            },
        )

    def log_feature_store_monitoring(
        self,
        feature_records: list[dict[str, Any]],
        *,
        event_time: datetime | None = None,
    ) -> None:
        """
        Enregistre les features dans la table de monitoring.
        """
        if not feature_records:
            raise ValueError(
                "Aucun feature record à insérer dans feature_store_monitoring."
            )

        event_time = event_time or _utc_now()

        logger.info(
            "PredictionLoggingService logging feature store monitoring",
            extra={
                "extra_data": {
                    "event": "prediction_logging_feature_store_start",
                    "feature_count": len(feature_records),
                    "request_id": feature_records[0].get("request_id"),
                    "model_name": feature_records[0].get("model_name"),
                    "model_version": feature_records[0].get("model_version"),
                    "source_table": feature_records[0].get("source_table"),
                }
            },
        )

        monitoring_crud.create_feature_store_records(
            self.db,
            records=feature_records,
            timestamp=event_time,
        )

        logger.info(
            "PredictionLoggingService logged feature store monitoring successfully",
            extra={
                "extra_data": {
                    "event": "prediction_logging_feature_store_success",
                    "feature_count": len(feature_records),
                    "request_id": feature_records[0].get("request_id"),
                }
            },
        )

    def log_full_prediction_event(
        self,
        *,
        request_id: str,
        model_name: str,
        model_version: str,
        features_df: pd.DataFrame,
        raw_input_data: dict[str, Any] | None,
        prediction: int,
        score: float,
        threshold_used: float | None,
        latency_ms: float | None,
        inference_latency_ms: float | None = None,
        client_id: int | None = None,
        write_feature_store_monitoring: bool = True,
        source_table: str | None = None,
        output_data: dict[str, Any] | None = None,
        status_code: int | None = 200,
    ) -> None:
        """
        Orchestration complète de journalisation d'une prédiction réussie.

        Notes
        -----
        Les commits et rollbacks sont gérés par l'appelant.
        """
        logger.info(
            "PredictionLoggingService logging full prediction event",
            extra={
                "extra_data": {
                    "event": "prediction_logging_full_event_start",
                    "request_id": request_id,
                    "client_id": client_id,
                    "model_name": model_name,
                    "model_version": model_version,
                    "write_feature_store_monitoring": write_feature_store_monitoring,
                    "status_code": status_code,
                }
            },
        )

        if not isinstance(features_df, pd.DataFrame):
            raise TypeError("`features_df` doit être un DataFrame pandas.")

        if len(features_df) != 1:
            raise ValueError(
                "Le DataFrame de features doit contenir exactement une ligne dans "
                f"log_full_prediction_event. Reçu : {len(features_df)} lignes."
            )

        event_time = _utc_now()
        resolved_source_table = _resolve_source_table(source_table)

        feature_records = dataframe_row_to_feature_records(
            features_df,
            request_id=request_id,
            model_name=model_name,
            model_version=model_version,
            client_id=client_id,
            source_table=resolved_source_table,
        )

        self.log_prediction_features_snapshot(
            feature_records,
            event_time=event_time,
        )

        if write_feature_store_monitoring:
            self.log_feature_store_monitoring(
                feature_records,
                event_time=event_time,
            )

        final_output = output_data or {
            "request_id": request_id,
            "prediction": int(prediction),
            "score": float(score),
            "threshold_used": None if threshold_used is None else float(threshold_used),
            "model_name": model_name,
            "model_version": model_version,
            "latency_ms": None if latency_ms is None else float(latency_ms),
            "inference_latency_ms": None if inference_latency_ms is None else float(inference_latency_ms),
            "source_csv": str(Path(APPLICATION_CSV).name),
        }

        self.log_prediction(
            request_id=request_id,
            model_name=model_name,
            model_version=model_version,
            prediction=prediction,
            score=score,
            threshold_used=threshold_used,
            latency_ms=latency_ms,
            inference_latency_ms=inference_latency_ms,
            input_data=raw_input_data,
            output_data=final_output,
            client_id=client_id,
            status_code=status_code,
            error_message=None,
            event_time=event_time,
        )

        logger.info(
            "PredictionLoggingService logged full prediction event successfully",
            extra={
                "extra_data": {
                    "event": "prediction_logging_full_event_success",
                    "request_id": request_id,
                    "client_id": client_id,
                    "model_name": model_name,
                    "model_version": model_version,
                    "prediction": prediction,
                    "source_table": resolved_source_table,
                    "feature_count": len(feature_records),
                }
            },
        )