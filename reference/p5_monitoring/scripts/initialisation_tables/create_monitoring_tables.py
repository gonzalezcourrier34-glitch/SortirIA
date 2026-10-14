"""
Script de création des tables PostgreSQL de monitoring.

Ce module crée les tables utilisées pour suivre un modèle
de machine learning en production.

Objectif
--------
Mettre en place une couche de monitoring structurée afin de :
- suivre les versions de modèles déployées
- conserver les snapshots de features observées
- stocker les métriques de dérive
- historiser les métriques de performance
- centraliser les alertes

Tables créées
-------------
- model_registry
- feature_store_monitoring
- drift_metrics
- evaluation_metrics
- alerts
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# =============================================================================
# Chargement des variables d'environnement
# =============================================================================

load_dotenv()


# =============================================================================
# Configuration
# =============================================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL n'est pas défini dans les variables d'environnement.")


# =============================================================================
# SQL - Tables
# =============================================================================

CREATE_MODEL_REGISTRY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS model_registry (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('dev', 'staging', 'production', 'archived')),
    run_id TEXT,
    source_path TEXT,
    training_data_version TEXT,
    feature_list JSONB,
    hyperparameters JSONB,
    metrics JSONB,
    deployed_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_name, model_version)
);
"""

CREATE_FEATURE_STORE_MONITORING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feature_store_monitoring (
    id BIGSERIAL PRIMARY KEY,
    request_id TEXT,
    client_id BIGINT,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_value TEXT,
    feature_type TEXT,
    source_table TEXT,
    snapshot_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_DRIFT_METRICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS drift_metrics (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    reference_window_start TIMESTAMPTZ,
    reference_window_end TIMESTAMPTZ,
    current_window_start TIMESTAMPTZ,
    current_window_end TIMESTAMPTZ,
    metric_value DOUBLE PRECISION NOT NULL,
    threshold_value DOUBLE PRECISION,
    drift_detected BOOLEAN NOT NULL DEFAULT FALSE,
    details JSONB,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_EVALUATION_METRICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_metrics (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ,
    roc_auc DOUBLE PRECISION,
    pr_auc DOUBLE PRECISION,
    precision_score DOUBLE PRECISION,
    recall_score DOUBLE PRECISION,
    f1_score DOUBLE PRECISION,
    fbeta_score DOUBLE PRECISION,
    business_cost DOUBLE PRECISION,
    tn INTEGER CHECK (tn IS NULL OR tn >= 0),
    fp INTEGER CHECK (fp IS NULL OR fp >= 0),
    fn INTEGER CHECK (fn IS NULL OR fn >= 0),
    tp INTEGER CHECK (tp IS NULL OR tp >= 0),
    sample_size INTEGER CHECK (sample_size IS NULL OR sample_size >= 0),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_ALERTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    model_name TEXT,
    model_version TEXT,
    feature_name TEXT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    context JSONB,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);
"""


# =============================================================================
# SQL - Index optimisés
# =============================================================================

CREATE_INDEXES_SQL = [
    # -------------------------------------------------------------------------
    # model_registry
    # -------------------------------------------------------------------------
    """
    CREATE INDEX IF NOT EXISTS idx_model_registry_name_version
    ON model_registry(model_name, model_version);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_model_registry_active
    ON model_registry(is_active);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_model_registry_stage
    ON model_registry(stage);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_model_registry_active_model
    ON model_registry(model_name, is_active);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_model_registry_deployed_at
    ON model_registry(deployed_at DESC);
    """,

    # -------------------------------------------------------------------------
    # feature_store_monitoring
    # -------------------------------------------------------------------------
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_monitoring_request_id
    ON feature_store_monitoring(request_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_monitoring_client_id
    ON feature_store_monitoring(client_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_monitoring_feature_name
    ON feature_store_monitoring(feature_name);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_monitoring_snapshot_timestamp
    ON feature_store_monitoring(snapshot_timestamp DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_model_version_time
    ON feature_store_monitoring(model_name, model_version, snapshot_timestamp DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_request_feature
    ON feature_store_monitoring(request_id, feature_name);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_client_time
    ON feature_store_monitoring(client_id, snapshot_timestamp DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feature_store_source_table
    ON feature_store_monitoring(source_table);
    """,

    # -------------------------------------------------------------------------
    # drift_metrics
    # -------------------------------------------------------------------------
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_model_version
    ON drift_metrics(model_name, model_version);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_feature_name
    ON drift_metrics(feature_name);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_detected
    ON drift_metrics(drift_detected);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_computed_at
    ON drift_metrics(computed_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_model_time
    ON drift_metrics(model_name, model_version, computed_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_model_feature
    ON drift_metrics(model_name, model_version, feature_name);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_drift_metrics_detected_model
    ON drift_metrics(model_name, model_version, drift_detected);
    """,

    # -------------------------------------------------------------------------
    # evaluation_metrics
    # -------------------------------------------------------------------------
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_metrics_model_version
    ON evaluation_metrics(model_name, model_version);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_metrics_dataset_name
    ON evaluation_metrics(dataset_name);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_metrics_computed_at
    ON evaluation_metrics(computed_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_metrics_model_dataset_time
    ON evaluation_metrics(model_name, model_version, dataset_name, computed_at DESC);
    """,

    # -------------------------------------------------------------------------
    # alerts
    # -------------------------------------------------------------------------
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_status
    ON alerts(status);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_severity
    ON alerts(severity);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_created_at
    ON alerts(created_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_model_version_status
    ON alerts(model_name, model_version, status);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_open_recent
    ON alerts(created_at DESC)
    WHERE status = 'open';
    """,
]


# =============================================================================
# Fonctions de création
# =============================================================================

def create_model_registry_table(engine) -> None:
    """Crée la table `model_registry`."""
    with engine.begin() as connection:
        connection.execute(text(CREATE_MODEL_REGISTRY_TABLE_SQL))
    print("Table 'model_registry' créée ou déjà existante.")


def create_feature_store_monitoring_table(engine) -> None:
    """Crée la table `feature_store_monitoring`."""
    with engine.begin() as connection:
        connection.execute(text(CREATE_FEATURE_STORE_MONITORING_TABLE_SQL))
    print("Table 'feature_store_monitoring' créée ou déjà existante.")


def create_drift_metrics_table(engine) -> None:
    """Crée la table `drift_metrics`."""
    with engine.begin() as connection:
        connection.execute(text(CREATE_DRIFT_METRICS_TABLE_SQL))
    print("Table 'drift_metrics' créée ou déjà existante.")


def create_evaluation_metrics_table(engine) -> None:
    """Crée la table `evaluation_metrics`."""
    with engine.begin() as connection:
        connection.execute(text(CREATE_EVALUATION_METRICS_TABLE_SQL))
    print("Table 'evaluation_metrics' créée ou déjà existante.")


def create_alerts_table(engine) -> None:
    """Crée la table `alerts`."""
    with engine.begin() as connection:
        connection.execute(text(CREATE_ALERTS_TABLE_SQL))
    print("Table 'alerts' créée ou déjà existante.")


def create_indexes(engine) -> None:
    """Crée les index de monitoring."""
    with engine.begin() as connection:
        for sql in CREATE_INDEXES_SQL:
            connection.execute(text(sql))
    print("Index monitoring créés ou déjà existants.")


# =============================================================================
# Orchestration
# =============================================================================

TABLE_CREATORS = [
    create_model_registry_table,
    create_feature_store_monitoring_table,
    create_drift_metrics_table,
    create_evaluation_metrics_table,
    create_alerts_table,
]


def create_monitoring_tables(engine) -> None:
    """Crée toutes les tables de monitoring puis leurs index."""
    for create_table in TABLE_CREATORS:
        create_table(engine)

    create_indexes(engine)


def main() -> None:
    """Point d'entrée du script."""
    print("Connexion à PostgreSQL...")
    engine = create_engine(DATABASE_URL, echo=False)
    print("Connexion établie.")

    create_monitoring_tables(engine)

    print("Création des tables de monitoring terminée.")


if __name__ == "__main__":
    main()