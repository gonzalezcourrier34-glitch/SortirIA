"""
Routes FastAPI liées au monitoring du modèle.

Ce module expose les endpoints permettant de :
- consulter le modèle actif
- consulter le registre des modèles
- enregistrer une version de modèle
- consulter les métriques de drift
- consulter les métriques d'évaluation
- consulter le feature store de monitoring
- consulter les alertes
- reconnaître ou résoudre une alerte
- récupérer une synthèse de monitoring
- vérifier rapidement l'état du monitoring

Objectif
--------
Fournir une interface API simple pour exploiter les données de monitoring
depuis le dashboard, un outil d'administration ou des jobs externes.

Principe d'architecture
-----------------------
Les routes FastAPI restent fines :
- elles valident les paramètres HTTP,
- elles appellent le service métier,
- elles retournent la réponse.

La logique métier et l'accès aux données sont portés
par `MonitoringService`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import verify_api_key
from app.core.schemas import (
    ActiveModelResponse,
    AlertActionResponse,
    AlertListResponse,
    AlertResponse,
    GenericItemsResponse,
    ModelRegistryCreateRequest,
    ModelRegistryItemResponse,
    ModelRegistryListResponse,
    ModelRegistryRegisterResponse,
    MonitoringHealthResponse,
    MonitoringSummaryResponse,
)
from app.services.monitoring_service import MonitoringService


logger = logging.getLogger(__name__)


# =============================================================================
# Initialisation du routeur
# =============================================================================

router = APIRouter(
    prefix="/monitoring",
    tags=["Monitoring"],
)


# =============================================================================
# Helpers
# =============================================================================

def _validate_window(
    window_start: datetime | None,
    window_end: datetime | None,
) -> None:
    """
    Vérifie la cohérence des bornes temporelles.
    """
    if (window_start is None) ^ (window_end is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="window_start et window_end doivent être fournis ensemble.",
        )

    if (
        window_start is not None
        and window_end is not None
        and window_end <= window_start
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="window_end doit être strictement supérieur à window_start.",
        )


def _safe_get(entity: Any, field_name: str, default: Any = None) -> Any:
    """
    Lit un champ depuis un objet ORM ou un dictionnaire.
    """
    if isinstance(entity, dict):
        return entity.get(field_name, default)
    return getattr(entity, field_name, default)


def _serialize_active_model(entity: Any) -> ActiveModelResponse:
    """
    Convertit une entité de modèle actif en schéma de réponse.

    Supporte :
    - objet ORM
    - dictionnaire
    """
    return ActiveModelResponse(
        model_name=_safe_get(entity, "model_name"),
        model_version=_safe_get(entity, "model_version"),
        stage=_safe_get(entity, "stage"),
        run_id=_safe_get(entity, "run_id"),
        source_path=_safe_get(entity, "source_path"),
        training_data_version=_safe_get(entity, "training_data_version"),
        feature_list=_safe_get(entity, "feature_list"),
        hyperparameters=_safe_get(entity, "hyperparameters"),
        metrics=_safe_get(entity, "metrics"),
        deployed_at=_safe_get(entity, "deployed_at"),
        is_active=_safe_get(entity, "is_active"),
        created_at=_safe_get(entity, "created_at"),
    )


def _serialize_model_registry_item(entity: Any) -> ModelRegistryItemResponse:
    """
    Convertit une ligne du registre des modèles en schéma de réponse.

    Supporte :
    - objet ORM
    - dictionnaire
    """
    return ModelRegistryItemResponse(
        id=_safe_get(entity, "id"),
        model_name=_safe_get(entity, "model_name"),
        model_version=_safe_get(entity, "model_version"),
        stage=_safe_get(entity, "stage"),
        run_id=_safe_get(entity, "run_id"),
        source_path=_safe_get(entity, "source_path"),
        training_data_version=_safe_get(entity, "training_data_version"),
        feature_list=_safe_get(entity, "feature_list"),
        hyperparameters=_safe_get(entity, "hyperparameters"),
        metrics=_safe_get(entity, "metrics"),
        deployed_at=_safe_get(entity, "deployed_at"),
        is_active=_safe_get(entity, "is_active"),
        created_at=_safe_get(entity, "created_at"),
    )


def _serialize_model_registry_items(
    raw_items: list[Any],
) -> list[ModelRegistryItemResponse]:
    """
    Sérialise une liste d'items de registre de modèles.

    Chaque item peut être :
    - un objet ORM
    - un dictionnaire
    """
    return [_serialize_model_registry_item(item) for item in raw_items]


def _serialize_alert(alert: Any) -> AlertResponse:
    """
    Convertit une entité ORM Alert ou un dict en schéma de réponse.
    """
    return AlertResponse(
        id=_safe_get(alert, "id"),
        alert_type=_safe_get(alert, "alert_type"),
        severity=_safe_get(alert, "severity"),
        model_name=_safe_get(alert, "model_name"),
        model_version=_safe_get(alert, "model_version"),
        feature_name=_safe_get(alert, "feature_name"),
        title=_safe_get(alert, "title"),
        message=_safe_get(alert, "message"),
        context=_safe_get(alert, "context"),
        status=_safe_get(alert, "status"),
        created_at=_safe_get(alert, "created_at"),
        acknowledged_at=_safe_get(alert, "acknowledged_at"),
        resolved_at=_safe_get(alert, "resolved_at"),
    )


# =============================================================================
# Routes modèle
# =============================================================================

@router.get(
    "/active-model",
    response_model=ActiveModelResponse,
    summary="Retourne le modèle actif",
)
def get_active_model(
    model_name: str | None = Query(
        default=None,
        description="Nom du modèle à filtrer. Si absent, retourne le dernier actif.",
    ),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> ActiveModelResponse:
    """
    Retourne la version active d'un modèle.
    """
    logger.info(
        "Active model requested",
        extra={
            "extra_data": {
                "event": "monitoring_active_model_start",
                "model_name": model_name,
            }
        },
    )

    service = MonitoringService(db)

    try:
        entity = service.get_active_model(model_name=model_name)

        if entity is None:
            logger.warning(
                "No active model found",
                extra={
                    "extra_data": {
                        "event": "monitoring_active_model_not_found",
                        "model_name": model_name,
                    }
                },
            )

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Aucun modèle actif trouvé.",
            )

        logger.info(
            "Active model returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_active_model_success",
                    "model_name": _safe_get(entity, "model_name"),
                    "model_version": _safe_get(entity, "model_version"),
                    "stage": _safe_get(entity, "stage"),
                    "is_active": _safe_get(entity, "is_active"),
                }
            },
        )

        return _serialize_active_model(entity)

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving active model",
            extra={
                "extra_data": {
                    "event": "monitoring_active_model_exception",
                    "model_name": model_name,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération du modèle actif : {exc}",
        ) from exc


@router.get(
    "/models",
    response_model=ModelRegistryListResponse,
    summary="Retourne le registre des modèles",
)
def get_models(
    limit: int = Query(default=200, ge=1, le=1000),
    model_name: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> ModelRegistryListResponse:
    """
    Retourne les versions de modèles enregistrées.
    """
    logger.info(
        "Model registry requested",
        extra={
            "extra_data": {
                "event": "monitoring_models_start",
                "limit": limit,
                "model_name": model_name,
                "is_active": is_active,
            }
        },
    )

    service = MonitoringService(db)

    try:
        payload = service.get_models(
            limit=limit,
            model_name=model_name,
            is_active=is_active,
        )

        raw_items = payload.get("items", [])
        count = payload.get("count", len(raw_items))
        items = _serialize_model_registry_items(raw_items)

        logger.info(
            "Model registry returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_models_success",
                    "limit": limit,
                    "model_name": model_name,
                    "is_active": is_active,
                    "returned_items": len(items),
                    "count": count,
                }
            },
        )

        return ModelRegistryListResponse(
            count=count,
            items=items,
        )

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving model registry",
            extra={
                "extra_data": {
                    "event": "monitoring_models_exception",
                    "limit": limit,
                    "model_name": model_name,
                    "is_active": is_active,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération du registre des modèles : {exc}",
        ) from exc


@router.post(
    "/models/register",
    response_model=ModelRegistryRegisterResponse,
    summary="Enregistre ou met à jour une version de modèle",
)
def register_model_version(
    payload: ModelRegistryCreateRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> ModelRegistryRegisterResponse:
    """
    Enregistre ou met à jour une version de modèle dans le registry.
    """
    logger.info(
        "Model version registration requested",
        extra={
            "extra_data": {
                "event": "monitoring_register_model_start",
                "model_name": payload.model_name,
                "model_version": payload.model_version,
                "stage": payload.stage,
                "is_active": payload.is_active,
                "run_id": payload.run_id,
            }
        },
    )

    service = MonitoringService(db)

    try:
        result = service.register_model_version(
            model_name=payload.model_name,
            model_version=payload.model_version,
            stage=payload.stage,
            run_id=payload.run_id,
            source_path=payload.source_path,
            training_data_version=payload.training_data_version,
            feature_list=payload.feature_list,
            hyperparameters=payload.hyperparameters,
            metrics=payload.metrics,
            deployed_at=payload.deployed_at,
            is_active=payload.is_active,
        )
        db.commit()

        logger.info(
            "Model version registered successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_register_model_success",
                    "model_name": result.get("model_name", payload.model_name),
                    "model_version": result.get("model_version", payload.model_version),
                    "stage": result.get("stage", payload.stage),
                    "is_active": result.get("is_active", payload.is_active),
                }
            },
        )

        return ModelRegistryRegisterResponse(**result)

    except Exception as exc:
        db.rollback()

        logger.exception(
            "Unexpected error while registering model version",
            extra={
                "extra_data": {
                    "event": "monitoring_register_model_exception",
                    "model_name": payload.model_name,
                    "model_version": payload.model_version,
                    "stage": payload.stage,
                    "is_active": payload.is_active,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'enregistrement du modèle : {exc}",
        ) from exc


# =============================================================================
# Routes drift / évaluation / feature store
# =============================================================================

@router.get(
    "/drift",
    response_model=GenericItemsResponse,
    summary="Retourne les métriques de drift",
)
def get_drift_metrics(
    limit: int = Query(default=200, ge=1, le=2000),
    model_name: str | None = Query(default=None),
    model_version: str | None = Query(default=None),
    feature_name: str | None = Query(default=None),
    metric_name: str | None = Query(default=None),
    drift_detected: bool | None = Query(default=None),
    window_start: datetime | None = Query(default=None),
    window_end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> GenericItemsResponse:
    """
    Retourne les métriques de drift avec filtres optionnels.
    """
    logger.info(
        "Drift metrics requested",
        extra={
            "extra_data": {
                "event": "monitoring_drift_start",
                "limit": limit,
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": feature_name,
                "metric_name": metric_name,
                "drift_detected": drift_detected,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
            }
        },
    )

    _validate_window(window_start, window_end)
    service = MonitoringService(db)

    try:
        payload = service.get_drift_metrics(
            limit=limit,
            model_name=model_name,
            model_version=model_version,
            feature_name=feature_name,
            metric_name=metric_name,
            drift_detected=drift_detected,
            window_start=window_start,
            window_end=window_end,
        )

        items = payload.get("items", []) if isinstance(payload, dict) else []
        count = payload.get("count", 0) if isinstance(payload, dict) else 0

        logger.info(
            "Drift metrics returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_drift_success",
                    "limit": limit,
                    "model_name": model_name,
                    "model_version": model_version,
                    "feature_name": feature_name,
                    "metric_name": metric_name,
                    "drift_detected": drift_detected,
                    "returned_items": len(items) if isinstance(items, list) else 0,
                    "count": count,
                }
            },
        )

        return GenericItemsResponse(**payload)

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving drift metrics",
            extra={
                "extra_data": {
                    "event": "monitoring_drift_exception",
                    "limit": limit,
                    "model_name": model_name,
                    "model_version": model_version,
                    "feature_name": feature_name,
                    "metric_name": metric_name,
                    "drift_detected": drift_detected,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération des métriques de drift : {exc}",
        ) from exc


@router.get(
    "/evaluation",
    response_model=GenericItemsResponse,
    summary="Retourne les métriques d'évaluation",
)
def get_evaluation_metrics(
    limit: int = Query(default=200, ge=1, le=2000),
    model_name: str | None = Query(default=None),
    model_version: str | None = Query(default=None),
    dataset_name: str | None = Query(default=None),
    window_start: datetime | None = Query(default=None),
    window_end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> GenericItemsResponse:
    """
    Retourne les métriques d'évaluation avec filtres optionnels.
    """
    logger.info(
        "Evaluation metrics requested",
        extra={
            "extra_data": {
                "event": "monitoring_evaluation_start",
                "limit": limit,
                "model_name": model_name,
                "model_version": model_version,
                "dataset_name": dataset_name,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
            }
        },
    )

    _validate_window(window_start, window_end)
    service = MonitoringService(db)

    try:
        payload = service.get_evaluation_metrics(
            limit=limit,
            model_name=model_name,
            model_version=model_version,
            dataset_name=dataset_name,
            window_start=window_start,
            window_end=window_end,
        )

        items = payload.get("items", []) if isinstance(payload, dict) else []
        count = payload.get("count", 0) if isinstance(payload, dict) else 0

        logger.info(
            "Evaluation metrics returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_evaluation_success",
                    "limit": limit,
                    "model_name": model_name,
                    "model_version": model_version,
                    "dataset_name": dataset_name,
                    "returned_items": len(items) if isinstance(items, list) else 0,
                    "count": count,
                }
            },
        )

        return GenericItemsResponse(**payload)

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving evaluation metrics",
            extra={
                "extra_data": {
                    "event": "monitoring_evaluation_exception",
                    "limit": limit,
                    "model_name": model_name,
                    "model_version": model_version,
                    "dataset_name": dataset_name,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération des métriques d'évaluation : {exc}",
        ) from exc


@router.get(
    "/feature-store",
    response_model=GenericItemsResponse,
    summary="Retourne le feature store de monitoring",
)
def get_feature_store(
    limit: int = Query(default=200, ge=1, le=5000),
    request_id: str | None = Query(default=None),
    client_id: int | None = Query(default=None),
    feature_name: str | None = Query(default=None),
    model_name: str | None = Query(default=None),
    model_version: str | None = Query(default=None),
    source_table: str | None = Query(default=None),
    window_start: datetime | None = Query(default=None),
    window_end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> GenericItemsResponse:
    """
    Retourne les snapshots de features stockés pour le monitoring.
    """
    logger.info(
        "Feature store requested",
        extra={
            "extra_data": {
                "event": "monitoring_feature_store_start",
                "limit": limit,
                "request_id": request_id,
                "client_id": client_id,
                "feature_name": feature_name,
                "model_name": model_name,
                "model_version": model_version,
                "source_table": source_table,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
            }
        },
    )

    _validate_window(window_start, window_end)
    service = MonitoringService(db)

    try:
        payload = service.get_feature_store(
            limit=limit,
            request_id=request_id,
            client_id=client_id,
            feature_name=feature_name,
            model_name=model_name,
            model_version=model_version,
            source_table=source_table,
            window_start=window_start,
            window_end=window_end,
        )

        items = payload.get("items", []) if isinstance(payload, dict) else []
        count = payload.get("count", 0) if isinstance(payload, dict) else 0

        logger.info(
            "Feature store returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_feature_store_success",
                    "limit": limit,
                    "request_id": request_id,
                    "client_id": client_id,
                    "feature_name": feature_name,
                    "model_name": model_name,
                    "model_version": model_version,
                    "source_table": source_table,
                    "returned_items": len(items) if isinstance(items, list) else 0,
                    "count": count,
                }
            },
        )

        return GenericItemsResponse(**payload)

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving feature store",
            extra={
                "extra_data": {
                    "event": "monitoring_feature_store_exception",
                    "limit": limit,
                    "request_id": request_id,
                    "client_id": client_id,
                    "feature_name": feature_name,
                    "model_name": model_name,
                    "model_version": model_version,
                    "source_table": source_table,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération du feature store : {exc}",
        ) from exc


# =============================================================================
# Routes alertes
# =============================================================================

@router.get(
    "/alerts",
    response_model=AlertListResponse,
    summary="Retourne les alertes récentes",
)
def get_recent_alerts(
    limit: int = Query(default=50, ge=1, le=1000),
    alert_status: str | None = Query(default=None, alias="status"),
    severity: str | None = Query(default=None),
    alert_type: str | None = Query(default=None),
    model_name: str | None = Query(default=None),
    model_version: str | None = Query(default=None),
    feature_name: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> AlertListResponse:
    """
    Retourne les alertes de monitoring récentes avec filtres optionnels.
    """
    logger.info(
        "Alerts requested",
        extra={
            "extra_data": {
                "event": "monitoring_alerts_start",
                "limit": limit,
                "status": alert_status,
                "severity": severity,
                "alert_type": alert_type,
                "model_name": model_name,
                "model_version": model_version,
                "feature_name": feature_name,
            }
        },
    )

    service = MonitoringService(db)

    try:
        alerts = service.get_recent_alerts(
            limit=limit,
            status=alert_status,
            severity=severity,
            alert_type=alert_type,
            model_name=model_name,
            model_version=model_version,
            feature_name=feature_name,
        )

        items = [_serialize_alert(alert) for alert in alerts]

        logger.info(
            "Alerts returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_alerts_success",
                    "limit": limit,
                    "status": alert_status,
                    "severity": severity,
                    "alert_type": alert_type,
                    "model_name": model_name,
                    "model_version": model_version,
                    "feature_name": feature_name,
                    "returned_items": len(items),
                }
            },
        )

        return AlertListResponse(
            count=len(items),
            items=items,
        )

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving alerts",
            extra={
                "extra_data": {
                    "event": "monitoring_alerts_exception",
                    "limit": limit,
                    "status": alert_status,
                    "severity": severity,
                    "alert_type": alert_type,
                    "model_name": model_name,
                    "model_version": model_version,
                    "feature_name": feature_name,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la récupération des alertes : {exc}",
        ) from exc


@router.post(
    "/alerts/{alert_id}/acknowledge",
    response_model=AlertActionResponse,
    summary="Reconnaît une alerte",
)
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> AlertActionResponse:
    """
    Marque une alerte comme reconnue.
    """
    logger.info(
        "Alert acknowledge requested",
        extra={
            "extra_data": {
                "event": "monitoring_alert_ack_start",
                "alert_id": alert_id,
            }
        },
    )

    service = MonitoringService(db)

    try:
        alert = service.acknowledge_alert(alert_id)

        if alert is None:
            logger.warning(
                "Alert to acknowledge not found",
                extra={
                    "extra_data": {
                        "event": "monitoring_alert_ack_not_found",
                        "alert_id": alert_id,
                    }
                },
            )

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Alerte introuvable.",
            )

        db.commit()
        db.refresh(alert)

        logger.info(
            "Alert acknowledged successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_alert_ack_success",
                    "alert_id": alert.id,
                    "status": alert.status,
                }
            },
        )

        return AlertActionResponse(
            id=alert.id,
            status=alert.status,
            acknowledged_at=alert.acknowledged_at,
            resolved_at=alert.resolved_at,
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        logger.exception(
            "Unexpected error while acknowledging alert",
            extra={
                "extra_data": {
                    "event": "monitoring_alert_ack_exception",
                    "alert_id": alert_id,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la reconnaissance de l'alerte : {exc}",
        ) from exc


@router.post(
    "/alerts/{alert_id}/resolve",
    response_model=AlertActionResponse,
    summary="Résout une alerte",
)
def resolve_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> AlertActionResponse:
    """
    Marque une alerte comme résolue.
    """
    logger.info(
        "Alert resolve requested",
        extra={
            "extra_data": {
                "event": "monitoring_alert_resolve_start",
                "alert_id": alert_id,
            }
        },
    )

    service = MonitoringService(db)

    try:
        alert = service.resolve_alert(alert_id)

        if alert is None:
            logger.warning(
                "Alert to resolve not found",
                extra={
                    "extra_data": {
                        "event": "monitoring_alert_resolve_not_found",
                        "alert_id": alert_id,
                    }
                },
            )

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Alerte introuvable.",
            )

        db.commit()
        db.refresh(alert)

        logger.info(
            "Alert resolved successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_alert_resolve_success",
                    "alert_id": alert.id,
                    "status": alert.status,
                }
            },
        )

        return AlertActionResponse(
            id=alert.id,
            status=alert.status,
            acknowledged_at=alert.acknowledged_at,
            resolved_at=alert.resolved_at,
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        logger.exception(
            "Unexpected error while resolving alert",
            extra={
                "extra_data": {
                    "event": "monitoring_alert_resolve_exception",
                    "alert_id": alert_id,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la résolution de l'alerte : {exc}",
        ) from exc


# =============================================================================
# Routes synthèse
# =============================================================================

@router.get(
    "/summary",
    response_model=MonitoringSummaryResponse,
    summary="Retourne une synthèse de monitoring",
)
def get_monitoring_summary(
    model_name: str = Query(...),
    model_version: str | None = Query(default=None),
    window_start: datetime | None = Query(default=None),
    window_end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> MonitoringSummaryResponse:
    """
    Retourne une synthèse du monitoring sur un modèle.
    """
    logger.info(
        "Monitoring summary requested",
        extra={
            "extra_data": {
                "event": "monitoring_summary_start",
                "model_name": model_name,
                "model_version": model_version,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
            }
        },
    )

    _validate_window(window_start, window_end)
    service = MonitoringService(db)

    try:
        payload = service.get_monitoring_summary(
            model_name=model_name,
            model_version=model_version,
            window_start=window_start,
            window_end=window_end,
        )

        logger.info(
            "Monitoring summary returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_summary_success",
                    "model_name": model_name,
                    "model_version": model_version,
                }
            },
        )

        return MonitoringSummaryResponse(**payload)

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving monitoring summary",
            extra={
                "extra_data": {
                    "event": "monitoring_summary_exception",
                    "model_name": model_name,
                    "model_version": model_version,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du calcul de la synthèse : {exc}",
        ) from exc


@router.get(
    "/health",
    response_model=MonitoringHealthResponse,
    summary="Retourne un état simple du monitoring",
)
def get_monitoring_health(
    model_name: str = Query(...),
    model_version: str | None = Query(default=None),
    window_start: datetime | None = Query(default=None),
    window_end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
) -> MonitoringHealthResponse:
    """
    Retourne un état simple et lisible du monitoring.
    """
    logger.info(
        "Monitoring health requested",
        extra={
            "extra_data": {
                "event": "monitoring_health_start",
                "model_name": model_name,
                "model_version": model_version,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
            }
        },
    )

    _validate_window(window_start, window_end)
    service = MonitoringService(db)

    try:
        payload = service.get_monitoring_health(
            model_name=model_name,
            model_version=model_version,
            window_start=window_start,
            window_end=window_end,
        )

        logger.info(
            "Monitoring health returned successfully",
            extra={
                "extra_data": {
                    "event": "monitoring_health_success",
                    "model_name": model_name,
                    "model_version": model_version,
                }
            },
        )

        return MonitoringHealthResponse(**payload)

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error while retrieving monitoring health",
            extra={
                "extra_data": {
                    "event": "monitoring_health_exception",
                    "model_name": model_name,
                    "model_version": model_version,
                    "error": str(exc),
                }
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du calcul de l'état du monitoring : {exc}",
        ) from exc