"""
Point d'entrée principal du dashboard Streamlit.

Ce script orchestre :
- la configuration générale
- les helpers communs
- les chargements partagés via l'API FastAPI
- l'affichage des 3 grands modules du dashboard

Architecture
------------
Le dashboard ne lit jamais directement PostgreSQL.
Toutes les données sont récupérées via l'API FastAPI.

Architecture actuelle
---------------------
- les prédictions utilisent des données issues exclusivement de
  `application_test.csv` ou du cache CSV côté API
- le dashboard ne lit plus de table SQL de features métier
- PostgreSQL sert uniquement à l'historique, au logging
  et au monitoring
- les analyses avancées sont déclenchées via l'API :
    - analyse Evidently
    - évaluation monitoring
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard_config import (
    API_KEY,
    API_URL,
    DEFAULT_LIMIT,
    MODEL_NAME,
    MODEL_VERSION,
)
from dashboard.dashboard_onlget.dashboard_monitoring import render_monitoring_page
from dashboard.dashboard_onlget.dashboard_predictions import render_predictions_page
from dashboard_request import (
    build_preview_map,
    build_tables_status_dataframe,
    call_predict_api,
    call_predict_batch_api,
    call_predict_client_api,
    call_predict_fully_random_batch_api,
    call_predict_real_random_batch_api,
    get_active_model,
    get_alerts,
    get_drift_metrics,
    get_evaluation_metrics,
    get_feature_store_monitoring,
    get_ground_truth_by_request_id,
    get_ground_truth_history,
    get_health,
    get_models,
    get_monitoring_health,
    get_monitoring_summary,
    get_prediction_detail,
    get_prediction_features_snapshot,
    get_prediction_history,
    metric_safe_number,
    run_evidently_analysis,
    run_evidently_analysis_from_feature_store,
    run_monitoring_evaluation_analysis,
)
from dashboard.dashboard_onlget.dashboard_systeme import render_systeme_page


# =============================================================================
# Configuration Streamlit
# =============================================================================

st.set_page_config(
    page_title="Dashboard Scoring Crédit",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =============================================================================
# Style
# =============================================================================

def inject_css() -> None:
    """
    Injecte un style CSS léger pour le dashboard.
    """
    st.markdown(
        """
        <style>
            .main-title {
                font-size: 2rem;
                font-weight: 700;
                margin-bottom: 0.2rem;
            }
            .subtitle {
                color: #6B7280;
                margin-bottom: 1rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# Helpers
# =============================================================================

def safe_dataframe(value: Any) -> pd.DataFrame:
    """
    Retourne un DataFrame vide si la valeur reçue n'est pas un DataFrame.
    """
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def safe_dict(value: Any) -> dict[str, Any]:
    """
    Retourne un dictionnaire vide si la valeur reçue n'est pas un dict.
    """
    return value if isinstance(value, dict) else {}


# =============================================================================
# Wrappers API pour les pages
# =============================================================================

def call_predict_client_api_wrapper(client_id: int) -> tuple[bool, Any]:
    """
    Wrapper local pour appeler `/predict/{client_id}`
    avec la configuration du dashboard.
    """
    return call_predict_client_api(
        client_id=client_id,
        base_url=API_URL,
        api_key=API_KEY,
    )


def call_predict_api_wrapper(payload: dict[str, Any]) -> tuple[bool, Any]:
    """
    Wrapper local pour appeler `/predict`
    avec la configuration du dashboard.
    """
    return call_predict_api(
        payload,
        base_url=API_URL,
        api_key=API_KEY,
    )


def call_predict_batch_api_wrapper(payloads: list[dict[str, Any]]) -> tuple[bool, Any]:
    """
    Wrapper local pour appeler `/predict/batch`
    avec la configuration du dashboard.
    """
    return call_predict_batch_api(
        payloads,
        base_url=API_URL,
        api_key=API_KEY,
    )


def call_predict_real_random_batch_api_wrapper(
    batch_size: int,
    random_seed: int | None = None,
) -> tuple[bool, Any]:
    """
    Wrapper local pour appeler la route de simulation
    basée sur des données réelles.
    """
    return call_predict_real_random_batch_api(
        batch_size=batch_size,
        random_seed=random_seed,
        base_url=API_URL,
        api_key=API_KEY,
    )


def call_predict_fully_random_batch_api_wrapper(batch_size: int) -> tuple[bool, Any]:
    """
    Wrapper local pour appeler la route de simulation
    basée sur des données aléatoires.
    """
    return call_predict_fully_random_batch_api(
        batch_size=batch_size,
        base_url=API_URL,
        api_key=API_KEY,
    )


def get_prediction_detail_wrapper(request_id: str) -> dict[str, Any] | None:
    """
    Wrapper local pour récupérer le détail d'une prédiction.
    """
    return get_prediction_detail(
        request_id,
        base_url=API_URL,
        api_key=API_KEY,
    )


def get_prediction_features_snapshot_wrapper(request_id: str) -> dict[str, Any] | None:
    """
    Wrapper local pour récupérer le snapshot de features d'une requête.
    """
    return get_prediction_features_snapshot(
        request_id,
        base_url=API_URL,
        api_key=API_KEY,
    )


def get_ground_truth_by_request_id_wrapper(
    request_id: str,
    ground_truth_df: pd.DataFrame,
    prediction_logs_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Récupère la vérité terrain associée à une prédiction.

    Stratégie :
    1. cherche dans le DataFrame déjà chargé
    2. si rien, interroge l'API par request_id
    3. si rien, retrouve le client_id et interroge l'API par client_id
    """
    local_gt = get_ground_truth_by_request_id(
        request_id,
        ground_truth_df=ground_truth_df,
        prediction_logs_df=prediction_logs_df,
    )

    if isinstance(local_gt, pd.DataFrame) and not local_gt.empty:
        return local_gt

    api_gt = get_ground_truth_history(
        base_url=API_URL,
        api_key=API_KEY,
        limit=50,
        request_id=request_id,
    )

    if isinstance(api_gt, pd.DataFrame) and not api_gt.empty:
        return api_gt

    if (
        prediction_logs_df is not None
        and not prediction_logs_df.empty
        and "request_id" in prediction_logs_df.columns
        and "client_id" in prediction_logs_df.columns
    ):
        pred_row = prediction_logs_df[
            prediction_logs_df["request_id"].astype(str) == str(request_id)
        ]

        if not pred_row.empty:
            client_id = pred_row.iloc[0].get("client_id")

            if client_id is not None and not pd.isna(client_id):
                return get_ground_truth_history(
                    base_url=API_URL,
                    api_key=API_KEY,
                    limit=50,
                    client_id=int(client_id),
                )

    return pd.DataFrame()

def run_evidently_analysis_wrapper(
    *,
    model_name: str,
    model_version: str | None = None,
    reference_kind: str = "transformed",
    current_kind: str = "transformed",
    monitoring_dir: str | None = None,
    max_rows: int | None = 20000,
) -> tuple[bool, Any]:
    """
    Wrapper local pour lancer une analyse Evidently
    avec la configuration du dashboard.

    Parameters
    ----------
    model_name : str
        Nom du modèle surveillé.
    model_version : str | None, default=None
        Version du modèle surveillé.
    reference_kind : str, default="transformed"
        Type de dataset de référence.
    current_kind : str, default="transformed"
        Type de dataset courant.
    monitoring_dir : str | None, default=None
        Dossier de monitoring à utiliser.
    max_rows : int | None, default=20000
        Nombre maximal de lignes à analyser.
    """
    return run_evidently_analysis(
        base_url=API_URL,
        api_key=API_KEY,
        model_name=model_name,
        model_version=model_version,
        reference_kind=reference_kind,
        current_kind=current_kind,
        monitoring_dir=monitoring_dir,
        max_rows=max_rows,
    )

def run_monitoring_evaluation_analysis_wrapper(
    *,
    model_name: str,
    model_version: str | None = None,
    dataset_name: str = "scoring_prod",
    window_start: str | None = None,
    window_end: str | None = None,
    beta: float = 2.0,
    cost_fn: float = 10.0,
    cost_fp: float = 1.0,
) -> tuple[bool, Any]:
    """
    Wrapper local pour lancer une évaluation monitoring
    avec la configuration du dashboard.
    """
    return run_monitoring_evaluation_analysis(
        base_url=API_URL,
        api_key=API_KEY,
        model_name=model_name,
        model_version=model_version,
        dataset_name=dataset_name,
        window_start=window_start,
        window_end=window_end,
        beta=beta,
        cost_fn=cost_fn,
        cost_fp=cost_fp,
    )

def run_evidently_analysis_from_feature_store_wrapper(
    *,
    model_name: str,
    model_version: str | None = None,
    source_table: str | None = None,
    max_rows: int = 10000,
) -> tuple[bool, Any]:
    """
    Wrapper local pour lancer une analyse Evidently depuis
    les snapshots `feature_store_monitoring`.

    Cette fonction injecte automatiquement API_URL et API_KEY.
    """
    safe_max_rows = min(int(max_rows), 10000)

    return run_evidently_analysis_from_feature_store(
        base_url=API_URL,
        api_key=API_KEY,
        model_name=model_name,
        model_version=model_version,
        source_table=source_table,
        max_rows=safe_max_rows,
    )
# =============================================================================
# Chargements communs via l'API
# =============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def load_shared_data(limit: int) -> dict[str, Any]:
    """
    Charge toutes les données partagées du dashboard via l'API.
    """
    health_data = safe_dict(get_health(base_url=API_URL))

    # -------------------------------------------------------------------------
    # Résolution du modèle actif réel
    # -------------------------------------------------------------------------
    active_model_df = safe_dataframe(
        get_active_model(
            base_url=API_URL,
            api_key=API_KEY,
            model_name=MODEL_NAME or None,
        )
    )

    resolved_model_name = MODEL_NAME or None
    resolved_model_version = MODEL_VERSION or None

    if not active_model_df.empty:
        first_row = active_model_df.iloc[0].to_dict()

        resolved_model_name = (
            first_row.get("model_name")
            or resolved_model_name
        )
        resolved_model_version = (
            first_row.get("model_version")
            or resolved_model_version
        )

    prediction_logs_df = safe_dataframe(
        get_prediction_history(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    ground_truth_df = safe_dataframe(
        get_ground_truth_history(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
        )
    )

    monitoring_summary = safe_dict(
        get_monitoring_summary(
            base_url=API_URL,
            api_key=API_KEY,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    monitoring_health = safe_dict(
        get_monitoring_health(
            base_url=API_URL,
            api_key=API_KEY,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    model_registry_df = safe_dataframe(
        get_models(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
            model_name=None,
        )
    )

    evaluation_metrics_df = safe_dataframe(
        get_evaluation_metrics(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    drift_metrics_df = safe_dataframe(
        get_drift_metrics(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    alerts_df = safe_dataframe(
        get_alerts(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    feature_store_monitoring_df = safe_dataframe(
        get_feature_store_monitoring(
            base_url=API_URL,
            api_key=API_KEY,
            limit=limit,
            model_name=resolved_model_name,
            model_version=resolved_model_version,
        )
    )

    tables_status_df = build_tables_status_dataframe(
        prediction_logs_df=prediction_logs_df,
        ground_truth_df=ground_truth_df,
        model_registry_df=model_registry_df,
        active_model_df=active_model_df,
        feature_store_monitoring_df=feature_store_monitoring_df,
        drift_metrics_df=drift_metrics_df,
        evaluation_metrics_df=evaluation_metrics_df,
        alerts_df=alerts_df,
    )
    
    preview_map = build_preview_map(
        prediction_logs_df=prediction_logs_df,
        ground_truth_df=ground_truth_df,
        model_registry_df=model_registry_df,
        feature_store_monitoring_df=feature_store_monitoring_df,
        drift_metrics_df=drift_metrics_df,
        evaluation_metrics_df=evaluation_metrics_df,
        alerts_df=alerts_df,
        max_rows=min(limit, 200),
    )

    return {
        "health_data": health_data,
        "prediction_logs_df": prediction_logs_df,
        "ground_truth_df": ground_truth_df,
        "monitoring_summary": monitoring_summary,
        "monitoring_health": monitoring_health,
        "model_registry_df": model_registry_df,
        "active_model_df": active_model_df,
        "evaluation_metrics_df": evaluation_metrics_df,
        "drift_metrics_df": drift_metrics_df,
        "alerts_df": alerts_df,
        "feature_store_monitoring_df": feature_store_monitoring_df,
        "tables_status_df": tables_status_df,
        "preview_map": preview_map,
        "resolved_model_name": resolved_model_name,
        "resolved_model_version": resolved_model_version,
    }

# =============================================================================
# UI
# =============================================================================

inject_css()

st.markdown(
    '<div class="main-title">Dashboard scoring crédit</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="subtitle">Prédiction, traçabilité et monitoring MLOps dans une seule interface.</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.title("Navigation")

    st.caption(f"API : {API_URL}")
    st.caption(f"Modèle : {MODEL_NAME}")
    if MODEL_VERSION:
        st.caption(f"Version ciblée : {MODEL_VERSION}")

    page = st.radio(
        "Aller vers",
        [
            "Système / Données",
            "Prédictions / Traçabilité",
            "Monitoring",
        ],
        index=1,
    )

    history_limit = st.slider(
        "Nombre max de lignes",
        min_value=20,
        max_value=1000,
        value=DEFAULT_LIMIT,
        step=20,
    )

    if st.button("🔄 Rafraîchir", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# =============================================================================
# Chargement principal
# =============================================================================

shared = load_shared_data(history_limit)

health_data = safe_dict(shared.get("health_data"))
prediction_logs_df = safe_dataframe(shared.get("prediction_logs_df"))
ground_truth_df = safe_dataframe(shared.get("ground_truth_df"))
monitoring_summary = safe_dict(shared.get("monitoring_summary"))
monitoring_health = safe_dict(shared.get("monitoring_health"))
model_registry_df = safe_dataframe(shared.get("model_registry_df"))
active_model_df = safe_dataframe(shared.get("active_model_df"))
evaluation_metrics_df = safe_dataframe(shared.get("evaluation_metrics_df"))
drift_metrics_df = safe_dataframe(shared.get("drift_metrics_df"))
alerts_df = safe_dataframe(shared.get("alerts_df"))
feature_store_monitoring_df = safe_dataframe(shared.get("feature_store_monitoring_df"))
tables_status_df = safe_dataframe(shared.get("tables_status_df"))
preview_map = shared.get("preview_map", {})

if not isinstance(preview_map, dict):
    preview_map = {}


# =============================================================================
# Routage
# =============================================================================

if page == "Système / Données":
    render_systeme_page(
        health_data=health_data,
        tables_status_df=tables_status_df,
        selected_table_preview_map=preview_map,
        available_tables=list(preview_map.keys()),
        api_key=API_KEY,
    )

elif page == "Prédictions / Traçabilité":
    render_predictions_page(
        prediction_logs_df=prediction_logs_df,
        ground_truth_df=ground_truth_df,
        call_predict_client_api=call_predict_client_api_wrapper,
        call_predict_api=call_predict_api_wrapper,
        call_predict_batch_api=call_predict_batch_api_wrapper,
        get_prediction_detail=get_prediction_detail_wrapper,
        get_prediction_features_snapshot=get_prediction_features_snapshot_wrapper,
        get_ground_truth_by_request_id=get_ground_truth_by_request_id_wrapper,
        metric_safe_number=metric_safe_number,
        call_predict_real_random_batch_api=call_predict_real_random_batch_api_wrapper,
        call_predict_fully_random_batch_api=call_predict_fully_random_batch_api_wrapper,
    )
elif page == "Monitoring":
    render_monitoring_page(
        base_url=API_URL,
        api_key=API_KEY,
        monitoring_summary=monitoring_summary,
        monitoring_health=monitoring_health,
        prediction_logs_df=prediction_logs_df,
        model_registry_df=model_registry_df,
        active_model_df=active_model_df,
        evaluation_metrics_df=evaluation_metrics_df,
        drift_metrics_df=drift_metrics_df,
        alerts_df=alerts_df,
        feature_store_monitoring_df=feature_store_monitoring_df,
        metric_safe_number=metric_safe_number,
        run_evidently_analysis=run_evidently_analysis_wrapper,
        run_evidently_analysis_from_feature_store=run_evidently_analysis_from_feature_store_wrapper,
        run_monitoring_evaluation_analysis=run_monitoring_evaluation_analysis_wrapper,
    )