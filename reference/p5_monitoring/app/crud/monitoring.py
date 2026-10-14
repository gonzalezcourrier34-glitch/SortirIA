"""
Couche CRUD pour le monitoring.

Ce module contient uniquement les opérations d'accès aux données :
- lecture des tables de monitoring
- insertion des métriques
- insertion et mise à jour des alertes
- lecture / mise à jour du registre des modèles
- écriture du feature store de monitoring

Objectif
--------
Isoler les accès ORM/SQLAlchemy de la logique métier.

Notes
-----
- Aucune logique métier complexe ne doit vivre ici.
- Les commits sont laissés à l'appelant.
- Ce module ne gère que le monitoring.
- Les objets liés aux prédictions doivent vivre dans crud/prediction.py.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.model.model_SQLalchemy import (
    Alert,
    DriftMetric,
    EvaluationMetric,
    FeatureStoreMonitoring,
    ModelRegistry,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Helpers internes
# =============================================================================

def _build_drift_metrics_query(
    db: Session,
    *,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    metric_name: str | None = None,
    drift_detected: bool | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
):
    """
    Construit la requête ORM de base pour drift_metrics.
    """
    query = db.query(DriftMetric)

    if model_name is not None:
        query = query.filter(DriftMetric.model_name == model_name)

    if model_version is not None:
        query = query.filter(DriftMetric.model_version == model_version)

    if feature_name is not None:
        query = query.filter(DriftMetric.feature_name == feature_name)

    if metric_name is not None:
        query = query.filter(DriftMetric.metric_name == metric_name)

    if drift_detected is not None:
        query = query.filter(DriftMetric.drift_detected.is_(drift_detected))

    if window_start is not None:
        query = query.filter(DriftMetric.computed_at >= window_start)

    if window_end is not None:
        query = query.filter(DriftMetric.computed_at < window_end)

    return query


def _build_evaluation_metrics_query(
    db: Session,
    *,
    model_name: str | None = None,
    model_version: str | None = None,
    dataset_name: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
):
    """
    Construit la requête ORM de base pour evaluation_metrics.
    """
    query = db.query(EvaluationMetric)

    if model_name is not None:
        query = query.filter(EvaluationMetric.model_name == model_name)

    if model_version is not None:
        query = query.filter(EvaluationMetric.model_version == model_version)

    if dataset_name is not None:
        query = query.filter(EvaluationMetric.dataset_name == dataset_name)

    if window_start is not None:
        query = query.filter(EvaluationMetric.computed_at >= window_start)

    if window_end is not None:
        query = query.filter(EvaluationMetric.computed_at < window_end)

    return query


def _build_feature_store_query(
    db: Session,
    *,
    request_id: str | None = None,
    client_id: int | None = None,
    feature_name: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    source_table: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
):
    """
    Construit la requête ORM de base pour feature_store_monitoring.
    """
    query = db.query(FeatureStoreMonitoring)

    if request_id is not None:
        query = query.filter(FeatureStoreMonitoring.request_id == request_id)

    if client_id is not None:
        query = query.filter(FeatureStoreMonitoring.client_id == client_id)

    if feature_name is not None:
        query = query.filter(FeatureStoreMonitoring.feature_name == feature_name)

    if model_name is not None:
        query = query.filter(FeatureStoreMonitoring.model_name == model_name)

    if model_version is not None:
        query = query.filter(FeatureStoreMonitoring.model_version == model_version)

    if source_table is not None:
        query = query.filter(FeatureStoreMonitoring.source_table == source_table)

    if window_start is not None:
        query = query.filter(FeatureStoreMonitoring.snapshot_timestamp >= window_start)

    if window_end is not None:
        query = query.filter(FeatureStoreMonitoring.snapshot_timestamp < window_end)

    return query


def _build_alerts_query(
    db: Session,
    *,
    status: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
):
    """
    Construit la requête ORM de base pour alerts.
    """
    query = db.query(Alert)

    if status is not None:
        query = query.filter(Alert.status == status)

    if severity is not None:
        query = query.filter(Alert.severity == severity)

    if alert_type is not None:
        query = query.filter(Alert.alert_type == alert_type)

    if model_name is not None:
        query = query.filter(Alert.model_name == model_name)

    if model_version is not None:
        query = query.filter(Alert.model_version == model_version)

    if feature_name is not None:
        query = query.filter(Alert.feature_name == feature_name)

    if created_after is not None:
        query = query.filter(Alert.created_at >= created_after)

    if created_before is not None:
        query = query.filter(Alert.created_at < created_before)

    return query


# =============================================================================
# Model registry
# =============================================================================

def get_active_model_record(
    db: Session,
    *,
    model_name: str | None = None,
) -> ModelRegistry | None:
    """
    Retourne le modèle actif le plus récent.
    """
    logger.debug(
        "CRUD monitoring get_active_model_record",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_active_model_start",
                "model_name": model_name,
            }
        },
    )

    query = db.query(ModelRegistry).filter(ModelRegistry.is_active.is_(True))

    if model_name is not None:
        query = query.filter(ModelRegistry.model_name == model_name)

    entity = (
        query.order_by(
            ModelRegistry.deployed_at.desc(),
            ModelRegistry.created_at.desc(),
        )
        .first()
    )

    logger.debug(
        "CRUD monitoring get_active_model_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_active_model_success",
                "model_name": model_name,
                "found": entity is not None,
            }
        },
    )

    return entity


def get_model_record_by_name_version(
    db: Session,
    *,
    model_name: str,
    model_version: str,
) -> ModelRegistry | None:
    """
    Retourne une version précise du modèle si elle existe.
    """
    entity = (
        db.query(ModelRegistry)
        .filter(
            ModelRegistry.model_name == model_name,
            ModelRegistry.model_version == model_version,
        )
        .first()
    )

    logger.debug(
        "CRUD monitoring get_model_record_by_name_version done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_model_by_name_version_success",
                "model_name": model_name,
                "model_version": model_version,
                "found": entity is not None,
            }
        },
    )

    return entity


def list_model_records(
    db: Session,
    *,
    limit: int = 200,
    model_name: str | None = None,
    is_active: bool | None = None,
) -> list[ModelRegistry]:
    """
    Retourne les versions de modèles enregistrées.
    """
    query = db.query(ModelRegistry)

    if model_name is not None:
        query = query.filter(ModelRegistry.model_name == model_name)

    if is_active is not None:
        query = query.filter(ModelRegistry.is_active.is_(is_active))

    rows = (
        query.order_by(
            ModelRegistry.created_at.desc(),
            ModelRegistry.deployed_at.desc(),
        )
        .limit(limit)
        .all()
    )

    logger.debug(
        "CRUD monitoring list_model_records done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_list_models_success",
                "limit": limit,
                "model_name": model_name,
                "is_active": is_active,
                "count": len(rows),
            }
        },
    )

    return rows


def create_model_record(
    db: Session,
    *,
    model_name: str,
    model_version: str,
    stage: str,
    run_id: str | None = None,
    source_path: str | None = None,
    training_data_version: str | None = None,
    feature_list: list[str] | None = None,
    hyperparameters: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    deployed_at: datetime | None = None,
    is_active: bool = False,
) -> ModelRegistry:
    """
    Crée une nouvelle ligne dans le registre des modèles.
    """
    entity = ModelRegistry(
        model_name=model_name,
        model_version=model_version,
        stage=stage,
        run_id=run_id,
        source_path=source_path,
        training_data_version=training_data_version,
        feature_list=feature_list,
        hyperparameters=hyperparameters,
        metrics=metrics,
        deployed_at=deployed_at,
        is_active=is_active,
    )
    db.add(entity)
    db.flush()

    logger.debug(
        "CRUD monitoring create_model_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_create_model_success",
                "id": entity.id,
                "model_name": entity.model_name,
                "model_version": entity.model_version,
                "is_active": entity.is_active,
            }
        },
    )

    return entity


def update_model_record(
    db: Session,
    *,
    entity: ModelRegistry,
    stage: str,
    run_id: str | None = None,
    source_path: str | None = None,
    training_data_version: str | None = None,
    feature_list: list[str] | None = None,
    hyperparameters: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    deployed_at: datetime | None = None,
    is_active: bool = False,
) -> ModelRegistry:
    """
    Met à jour un enregistrement modèle existant.
    """
    entity.stage = stage
    entity.run_id = run_id
    entity.source_path = source_path
    entity.training_data_version = training_data_version
    entity.feature_list = feature_list
    entity.hyperparameters = hyperparameters
    entity.metrics = metrics
    entity.deployed_at = deployed_at
    entity.is_active = is_active
    db.flush()

    logger.debug(
        "CRUD monitoring update_model_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_update_model_success",
                "id": entity.id,
                "model_name": entity.model_name,
                "model_version": entity.model_version,
                "is_active": entity.is_active,
            }
        },
    )

    return entity


def deactivate_other_model_versions(
    db: Session,
    *,
    model_name: str,
    keep_model_id: int,
) -> None:
    """
    Désactive les autres versions actives du même modèle.
    """
    (
        db.query(ModelRegistry)
        .filter(
            ModelRegistry.model_name == model_name,
            ModelRegistry.id != keep_model_id,
            ModelRegistry.is_active.is_(True),
        )
        .update({"is_active": False}, synchronize_session=False)
    )
    db.flush()

    logger.debug(
        "CRUD monitoring deactivate_other_model_versions done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_deactivate_other_models_success",
                "model_name": model_name,
                "keep_model_id": keep_model_id,
            }
        },
    )


# =============================================================================
# Drift metrics
# =============================================================================

def create_drift_metric_record(
    db: Session,
    *,
    model_name: str,
    model_version: str,
    feature_name: str,
    metric_name: str,
    metric_value: float,
    threshold_value: float | None = None,
    drift_detected: bool = False,
    details: dict[str, Any] | None = None,
    reference_window_start: datetime | None = None,
    reference_window_end: datetime | None = None,
    current_window_start: datetime | None = None,
    current_window_end: datetime | None = None,
    computed_at: datetime | None = None,
) -> DriftMetric:
    """
    Crée une métrique de drift.
    """
    entity = DriftMetric(
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        metric_name=metric_name,
        reference_window_start=reference_window_start,
        reference_window_end=reference_window_end,
        current_window_start=current_window_start,
        current_window_end=current_window_end,
        metric_value=metric_value,
        threshold_value=threshold_value,
        drift_detected=drift_detected,
        details=details,
        computed_at=computed_at,
    )
    db.add(entity)
    db.flush()

    logger.debug(
        "CRUD monitoring create_drift_metric_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_create_drift_success",
                "id": entity.id,
                "model_name": entity.model_name,
                "model_version": entity.model_version,
                "feature_name": entity.feature_name,
                "metric_name": entity.metric_name,
                "drift_detected": entity.drift_detected,
            }
        },
    )

    return entity


def list_drift_metrics(
    db: Session,
    *,
    limit: int = 200,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    metric_name: str | None = None,
    drift_detected: bool | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[DriftMetric]:
    """
    Retourne les métriques de drift filtrées.
    """
    query = _build_drift_metrics_query(
        db,
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        metric_name=metric_name,
        drift_detected=drift_detected,
        window_start=window_start,
        window_end=window_end,
    )

    rows = query.order_by(DriftMetric.computed_at.desc()).limit(limit).all()

    logger.debug(
        "CRUD monitoring list_drift_metrics done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_list_drift_success",
                "limit": limit,
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": feature_name,
                "metric_name": metric_name,
                "drift_detected": drift_detected,
                "count": len(rows),
            }
        },
    )

    return rows


def count_drift_metrics(
    db: Session,
    *,
    model_name: str | None = None,
    model_version: str | None = None,
    drift_detected: bool | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> int:
    """
    Compte les métriques de drift.
    """
    query = _build_drift_metrics_query(
        db,
        model_name=model_name,
        model_version=model_version,
        drift_detected=drift_detected,
        window_start=window_start,
        window_end=window_end,
    )

    count = query.count()

    logger.debug(
        "CRUD monitoring count_drift_metrics done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_count_drift_success",
                "model_name": model_name,
                "model_version": model_version,
                "drift_detected": drift_detected,
                "count": count,
            }
        },
    )

    return count


def get_latest_drift_metric(
    db: Session,
    *,
    model_name: str | None = None,
    model_version: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> DriftMetric | None:
    """
    Retourne la dernière métrique de drift.
    """
    query = _build_drift_metrics_query(
        db,
        model_name=model_name,
        model_version=model_version,
        window_start=window_start,
        window_end=window_end,
    )

    entity = query.order_by(DriftMetric.computed_at.desc()).first()

    logger.debug(
        "CRUD monitoring get_latest_drift_metric done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_latest_drift_success",
                "model_name": model_name,
                "model_version": model_version,
                "found": entity is not None,
            }
        },
    )

    return entity


# =============================================================================
# Evaluation metrics
# =============================================================================

def create_evaluation_metric_record(
    db: Session,
    *,
    model_name: str,
    model_version: str,
    dataset_name: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    roc_auc: float | None = None,
    pr_auc: float | None = None,
    precision_score: float | None = None,
    recall_score: float | None = None,
    f1_score: float | None = None,
    fbeta_score: float | None = None,
    business_cost: float | None = None,
    tn: int | None = None,
    fp: int | None = None,
    fn: int | None = None,
    tp: int | None = None,
    sample_size: int | None = None,
    computed_at: datetime | None = None,
) -> EvaluationMetric:
    """
    Crée une métrique d'évaluation.
    """
    entity = EvaluationMetric(
        model_name=model_name,
        model_version=model_version,
        dataset_name=dataset_name,
        window_start=window_start,
        window_end=window_end,
        roc_auc=roc_auc,
        pr_auc=pr_auc,
        precision_score=precision_score,
        recall_score=recall_score,
        f1_score=f1_score,
        fbeta_score=fbeta_score,
        business_cost=business_cost,
        tn=tn,
        fp=fp,
        fn=fn,
        tp=tp,
        sample_size=sample_size,
        computed_at=computed_at,
    )
    db.add(entity)
    db.flush()

    logger.debug(
        "CRUD monitoring create_evaluation_metric_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_create_evaluation_success",
                "id": entity.id,
                "model_name": entity.model_name,
                "model_version": entity.model_version,
                "dataset_name": entity.dataset_name,
            }
        },
    )

    return entity


def list_evaluation_metrics(
    db: Session,
    *,
    limit: int = 200,
    model_name: str | None = None,
    model_version: str | None = None,
    dataset_name: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[EvaluationMetric]:
    """
    Retourne les métriques d'évaluation filtrées.
    """
    query = _build_evaluation_metrics_query(
        db,
        model_name=model_name,
        model_version=model_version,
        dataset_name=dataset_name,
        window_start=window_start,
        window_end=window_end,
    )

    rows = query.order_by(EvaluationMetric.computed_at.desc()).limit(limit).all()

    logger.debug(
        "CRUD monitoring list_evaluation_metrics done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_list_evaluation_success",
                "limit": limit,
                "model_name": model_name,
                "model_version": model_version,
                "dataset_name": dataset_name,
                "count": len(rows),
            }
        },
    )

    return rows


def get_latest_evaluation_metric(
    db: Session,
    *,
    model_name: str | None = None,
    model_version: str | None = None,
    dataset_name: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> EvaluationMetric | None:
    """
    Retourne la dernière métrique d'évaluation.
    """
    query = _build_evaluation_metrics_query(
        db,
        model_name=model_name,
        model_version=model_version,
        dataset_name=dataset_name,
        window_start=window_start,
        window_end=window_end,
    )

    entity = query.order_by(EvaluationMetric.computed_at.desc()).first()

    logger.debug(
        "CRUD monitoring get_latest_evaluation_metric done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_latest_evaluation_success",
                "model_name": model_name,
                "model_version": model_version,
                "dataset_name": dataset_name,
                "found": entity is not None,
            }
        },
    )

    return entity


# =============================================================================
# Feature store monitoring
# =============================================================================

def create_feature_store_record(
    db: Session,
    *,
    request_id: str | None = None,
    client_id: int | None = None,
    model_name: str,
    model_version: str,
    feature_name: str,
    feature_value: str | None = None,
    feature_type: str | None = None,
    source_table: str | None = None,
    snapshot_timestamp: datetime | None = None,
) -> FeatureStoreMonitoring:
    """
    Crée un enregistrement dans le feature store de monitoring.
    """
    entity = FeatureStoreMonitoring(
        request_id=request_id,
        client_id=client_id,
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        feature_value=feature_value,
        feature_type=feature_type,
        source_table=source_table,
        snapshot_timestamp=snapshot_timestamp,
    )
    db.add(entity)
    db.flush()

    logger.debug(
        "CRUD monitoring create_feature_store_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_create_feature_store_success",
                "id": entity.id,
                "request_id": entity.request_id,
                "client_id": entity.client_id,
                "feature_name": entity.feature_name,
            }
        },
    )

    return entity


def create_feature_store_records(
    db: Session,
    *,
    records: list[dict[str, Any]],
    timestamp: datetime,
) -> None:
    """
    Crée plusieurs enregistrements dans la table feature_store_monitoring.
    """
    entities = [
        FeatureStoreMonitoring(
            request_id=r["request_id"],
            client_id=r.get("client_id"),
            model_name=r["model_name"],
            model_version=r["model_version"],
            feature_name=r["feature_name"],
            feature_value=r.get("feature_value"),
            feature_type=r.get("feature_type"),
            source_table=r.get("source_table"),
            snapshot_timestamp=timestamp,
        )
        for r in records
    ]

    if entities:
        db.add_all(entities)
        db.flush()

    logger.debug(
        "CRUD monitoring create_feature_store_records done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_create_feature_store_batch_success",
                "count": len(entities),
                "timestamp": timestamp.isoformat() if timestamp else None,
            }
        },
    )


def list_feature_store_records(
    db: Session,
    *,
    limit: int = 1000,
    request_id: str | None = None,
    client_id: int | None = None,
    feature_name: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    source_table: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[FeatureStoreMonitoring]:
    """
    Retourne une liste d'enregistrements du feature store.
    """
    query = _build_feature_store_query(
        db,
        request_id=request_id,
        client_id=client_id,
        feature_name=feature_name,
        model_name=model_name,
        model_version=model_version,
        source_table=source_table,
        window_start=window_start,
        window_end=window_end,
    )

    rows = (
        query.order_by(FeatureStoreMonitoring.snapshot_timestamp.desc())
        .limit(limit)
        .all()
    )

    logger.debug(
        "CRUD monitoring list_feature_store_records done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_list_feature_store_success",
                "limit": limit,
                "request_id": request_id,
                "client_id": client_id,
                "feature_name": feature_name,
                "model_name": model_name,
                "model_version": model_version,
                "source_table": source_table,
                "count": len(rows),
            }
        },
    )

    return rows


def count_feature_store_records(
    db: Session,
    *,
    request_id: str | None = None,
    client_id: int | None = None,
    feature_name: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    source_table: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> int:
    """
    Compte les enregistrements du feature store.
    """
    query = _build_feature_store_query(
        db,
        request_id=request_id,
        client_id=client_id,
        feature_name=feature_name,
        model_name=model_name,
        model_version=model_version,
        source_table=source_table,
        window_start=window_start,
        window_end=window_end,
    )

    count = query.count()

    logger.debug(
        "CRUD monitoring count_feature_store_records done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_count_feature_store_success",
                "request_id": request_id,
                "client_id": client_id,
                "feature_name": feature_name,
                "model_name": model_name,
                "model_version": model_version,
                "source_table": source_table,
                "count": count,
            }
        },
    )

    return count


def get_latest_feature_store_record(
    db: Session,
    *,
    request_id: str | None = None,
    client_id: int | None = None,
    feature_name: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    source_table: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> FeatureStoreMonitoring | None:
    """
    Retourne l'enregistrement le plus récent du feature store.
    """
    query = _build_feature_store_query(
        db,
        request_id=request_id,
        client_id=client_id,
        feature_name=feature_name,
        model_name=model_name,
        model_version=model_version,
        source_table=source_table,
        window_start=window_start,
        window_end=window_end,
    )

    entity = query.order_by(FeatureStoreMonitoring.snapshot_timestamp.desc()).first()

    logger.debug(
        "CRUD monitoring get_latest_feature_store_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_latest_feature_store_success",
                "request_id": request_id,
                "client_id": client_id,
                "feature_name": feature_name,
                "model_name": model_name,
                "model_version": model_version,
                "source_table": source_table,
                "found": entity is not None,
            }
        },
    )

    return entity


# =============================================================================
# Alertes
# =============================================================================

def create_alert_record(
    db: Session,
    *,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    context: dict[str, Any] | None = None,
    status: str = "open",
    created_at: datetime | None = None,
) -> Alert:
    """
    Crée une alerte.
    """
    entity = Alert(
        alert_type=alert_type,
        severity=severity,
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        title=title,
        message=message,
        context=context,
        status=status,
        created_at=created_at,
    )
    db.add(entity)
    db.flush()

    logger.debug(
        "CRUD monitoring create_alert_record done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_create_alert_success",
                "id": entity.id,
                "alert_type": entity.alert_type,
                "severity": entity.severity,
                "status": entity.status,
            }
        },
    )

    return entity


def get_alert_by_id(
    db: Session,
    *,
    alert_id: int,
) -> Alert | None:
    """
    Retourne une alerte par identifiant.
    """
    entity = db.query(Alert).filter(Alert.id == alert_id).first()

    logger.debug(
        "CRUD monitoring get_alert_by_id done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_get_alert_by_id_success",
                "alert_id": alert_id,
                "found": entity is not None,
            }
        },
    )

    return entity


def list_alert_records(
    db: Session,
    *,
    limit: int = 50,
    status: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[Alert]:
    """
    Retourne les alertes filtrées.
    """
    query = _build_alerts_query(
        db,
        status=status,
        severity=severity,
        alert_type=alert_type,
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        created_after=created_after,
        created_before=created_before,
    )

    rows = query.order_by(Alert.created_at.desc()).limit(limit).all()

    logger.debug(
        "CRUD monitoring list_alert_records done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_list_alerts_success",
                "limit": limit,
                "status": status,
                "severity": severity,
                "alert_type": alert_type,
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": feature_name,
                "count": len(rows),
            }
        },
    )

    return rows


def count_alert_records(
    db: Session,
    *,
    status: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> int:
    """
    Compte les alertes filtrées.
    """
    query = _build_alerts_query(
        db,
        status=status,
        severity=severity,
        alert_type=alert_type,
        model_name=model_name,
        model_version=model_version,
        feature_name=feature_name,
        created_after=created_after,
        created_before=created_before,
    )

    count = query.count()

    logger.debug(
        "CRUD monitoring count_alert_records done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_count_alerts_success",
                "status": status,
                "severity": severity,
                "alert_type": alert_type,
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": feature_name,
                "count": count,
            }
        },
    )

    return count


def update_alert_status(
    db: Session,
    *,
    alert: Alert,
    status: str,
    acknowledged_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> Alert:
    """
    Met à jour le statut d'une alerte existante.
    """
    alert.status = status

    if acknowledged_at is not None:
        alert.acknowledged_at = acknowledged_at

    if resolved_at is not None:
        alert.resolved_at = resolved_at

    db.flush()

    logger.debug(
        "CRUD monitoring update_alert_status done",
        extra={
            "extra_data": {
                "event": "crud_monitoring_update_alert_success",
                "alert_id": alert.id,
                "status": alert.status,
                "acknowledged_at": (
                    acknowledged_at.isoformat() if acknowledged_at is not None else None
                ),
                "resolved_at": (
                    resolved_at.isoformat() if resolved_at is not None else None
                ),
            }
        },
    )

    return alert