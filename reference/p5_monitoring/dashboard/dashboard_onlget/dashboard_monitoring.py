"""
Page Streamlit : monitoring MLOps.

Cette page orchestre le monitoring global du modèle de scoring crédit.

Elle regroupe :
- une vue d'ensemble du système de monitoring
- l'onglet Performance, délégué à dashboard_monitoring_performance.py
- l'onglet Dérive, délégué à dashboard_monitoring_drift.py
- les alertes
- le registre des modèles
- le modèle actif

Notes
-----
Cette page ne lit jamais directement PostgreSQL.

Les données affichées sont récupérées via l'API FastAPI, puis transmises
à cette page sous forme de dictionnaires ou de DataFrames préparés dans
dashboard_request.py.

Objectif pédagogique
--------------------
Cette page joue le rôle d'orchestrateur :
- elle prépare les données reçues
- elle affiche les KPI globaux
- elle délègue les gros onglets spécialisés à des modules séparés

Cela évite d'avoir un fichier Streamlit trop long et difficile à maintenir.
"""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
import streamlit as st

from .dashboard_monitoring_drift import render_drift_tab
from .dashboard_monitoring_performance import render_performance_tab


# =============================================================================
# Helpers génériques
# =============================================================================

def _safe_dict(value: Any) -> dict[str, Any]:
    """
    Retourne un dictionnaire exploitable.

    Si la valeur reçue n'est pas un dictionnaire, on retourne un dict vide.
    Cela évite les erreurs du type `.get()` sur une valeur None.
    """
    return value if isinstance(value, dict) else {}


def _safe_dataframe(value: Any) -> pd.DataFrame:
    """
    Retourne une copie de DataFrame ou un DataFrame vide.

    Streamlit manipule souvent les objets plusieurs fois. Faire une copie évite
    les effets de bord involontaires.
    """
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _coerce_columns_to_datetime(
    df: pd.DataFrame,
    columns: Iterable[str],
) -> pd.DataFrame:
    """
    Convertit plusieurs colonnes en datetime si elles existent.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame à préparer.
    columns : Iterable[str]
        Colonnes temporelles à convertir.

    Returns
    -------
    pd.DataFrame
        DataFrame copié avec colonnes converties si disponibles.
    """
    if df.empty:
        return df.copy()

    out = df.copy()

    for col in columns:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")

    return out


def _coerce_columns_to_numeric(
    df: pd.DataFrame,
    columns: Iterable[str],
) -> pd.DataFrame:
    """
    Convertit plusieurs colonnes en numérique si elles existent.

    Les valeurs non convertibles deviennent NaN grâce à errors='coerce'.
    """
    if df.empty:
        return df.copy()

    out = df.copy()

    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    return out


def _safe_metric_value(value: Any, decimals: int = 3) -> str:
    """
    Formate une valeur numérique pour l'affichage KPI.

    Exemple :
    - 0.81234 devient "0.812"
    - None devient "N/A"
    """
    try:
        if value is None or pd.isna(value):
            return "N/A"

        return f"{float(value):.{decimals}f}"

    except Exception:
        return "N/A"


def _safe_int(value: Any, default: int = 0) -> int:
    """
    Convertit une valeur en entier de façon robuste.
    """
    try:
        if value is None or pd.isna(value):
            return default

        return int(value)

    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    """
    Convertit une valeur en booléen de façon robuste.

    Utile car l'API, pandas et PostgreSQL peuvent renvoyer :
    - True / False
    - 1 / 0
    - "true" / "false"
    - pd.NA
    """
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "oui"}

    try:
        return bool(value)
    except Exception:
        return False


def _choose_existing_columns(
    df: pd.DataFrame,
    preferred_cols: list[str],
) -> list[str]:
    """
    Retourne uniquement les colonnes présentes dans le DataFrame.

    Cela permet d'afficher des tableaux robustes même si certaines colonnes
    ne sont pas encore disponibles côté API.
    """
    return [col for col in preferred_cols if col in df.columns]


def _pick_latest_row(
    df: pd.DataFrame,
    date_cols: list[str],
) -> pd.Series | None:
    """
    Retourne la ligne la plus récente selon les colonnes de date disponibles.

    La fonction teste les colonnes dans l'ordre donné.
    Si aucune colonne date n'existe, elle retourne simplement la première ligne.
    """
    if df.empty:
        return None

    working = df.copy()

    for col in date_cols:
        if col in working.columns:
            working[col] = pd.to_datetime(working[col], errors="coerce")
            working = working.sort_values(
                col,
                ascending=False,
                na_position="last",
            )

            if not working.empty:
                return working.iloc[0]

    return working.iloc[0] if not working.empty else None


# =============================================================================
# Helpers UI
# =============================================================================

def _render_card(title: str, value: Any, subtitle: str = "") -> None:
    """
    Affiche une carte KPI visuelle.

    Cette fonction centralise le style pour garder une interface homogène.
    """
    st.markdown(
        f"""
        <div style="
            padding: 16px 18px;
            border-radius: 16px;
            background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 6px 18px rgba(0,0,0,0.08);
            min-height: 120px;
        ">
            <div style="font-size: 0.9rem; color: #9CA3AF; margin-bottom: 8px;">
                {title}
            </div>
            <div style="font-size: 1.9rem; font-weight: 700; color: white; margin-bottom: 8px;">
                {value}
            </div>
            <div style="font-size: 0.85rem; color: #D1D5DB;">
                {subtitle}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _status_badge(label: str, color: str) -> str:
    """
    Retourne un badge HTML simple.

    Le badge est retourné sous forme de chaîne HTML pour pouvoir être intégré
    dans des blocs st.markdown.
    """
    return (
        f"<span style='"
        f"display:inline-block;"
        f"padding:6px 12px;"
        f"border-radius:999px;"
        f"background:{color};"
        f"color:white;"
        f"font-size:0.82rem;"
        f"font-weight:600;'>"
        f"{label}</span>"
    )


def _render_section_title(title: str, subtitle: str = "") -> None:
    """
    Affiche un titre de section avec sous-titre.
    """
    st.markdown(
        f"""
        <div style="margin-top: 8px; margin-bottom: 14px;">
            <div style="font-size: 1.25rem; font-weight: 700; margin-bottom: 4px;">
                {title}
            </div>
            <div style="color: #6B7280; font-size: 0.95rem;">
                {subtitle}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# Préparation des données
# =============================================================================

def _prepare_monitoring_data(
    *,
    prediction_logs_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
    active_model_df: pd.DataFrame,
    evaluation_metrics_df: pd.DataFrame,
    drift_metrics_df: pd.DataFrame,
    alerts_df: pd.DataFrame,
    feature_store_monitoring_df: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    """
    Prépare tous les DataFrames utilisés par la page monitoring.

    Cette fonction centralise les conversions de type :
    - dates
    - nombres
    - booléens gérés plus tard via _safe_bool

    Returns
    -------
    tuple[pd.DataFrame, ...]
        Les DataFrames préparés dans le même ordre que les paramètres.
    """
    prediction_logs_df = _safe_dataframe(prediction_logs_df)
    model_registry_df = _safe_dataframe(model_registry_df)
    active_model_df = _safe_dataframe(active_model_df)
    evaluation_metrics_df = _safe_dataframe(evaluation_metrics_df)
    drift_metrics_df = _safe_dataframe(drift_metrics_df)
    alerts_df = _safe_dataframe(alerts_df)
    feature_store_monitoring_df = _safe_dataframe(feature_store_monitoring_df)

    prediction_logs_df = _coerce_columns_to_datetime(
        prediction_logs_df,
        ["prediction_timestamp", "created_at"],
    )
    prediction_logs_df = _coerce_columns_to_numeric(
        prediction_logs_df,
        [
            "score",
            "threshold",
            "threshold_used",
            "latency_ms",
            "inference_latency_ms",
            "prediction",
        ],
    )

    model_registry_df = _coerce_columns_to_datetime(
        model_registry_df,
        ["deployed_at", "created_at"],
    )

    active_model_df = _coerce_columns_to_datetime(
        active_model_df,
        ["deployed_at", "created_at"],
    )

    evaluation_metrics_df = _coerce_columns_to_datetime(
        evaluation_metrics_df,
        ["computed_at", "window_start", "window_end"],
    )
    evaluation_metrics_df = _coerce_columns_to_numeric(
        evaluation_metrics_df,
        [
            "roc_auc",
            "pr_auc",
            "precision_score",
            "recall_score",
            "f1_score",
            "fbeta_score",
            "business_cost",
            "sample_size",
            "tn",
            "fp",
            "fn",
            "tp",
            "metric_value",
        ],
    )

    drift_metrics_df = _coerce_columns_to_datetime(
        drift_metrics_df,
        [
            "computed_at",
            "reference_window_start",
            "reference_window_end",
            "current_window_start",
            "current_window_end",
        ],
    )
    drift_metrics_df = _coerce_columns_to_numeric(
        drift_metrics_df,
        ["metric_value", "threshold_value"],
    )

    alerts_df = _coerce_columns_to_datetime(
        alerts_df,
        ["created_at", "acknowledged_at", "resolved_at"],
    )

    feature_store_monitoring_df = _coerce_columns_to_datetime(
        feature_store_monitoring_df,
        ["snapshot_timestamp"],
    )

    return (
        prediction_logs_df,
        model_registry_df,
        active_model_df,
        evaluation_metrics_df,
        drift_metrics_df,
        alerts_df,
        feature_store_monitoring_df,
    )


def _resolve_default_model_context(
    *,
    active_model_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
) -> tuple[str | None, str | None, pd.Series | None, pd.Series | None]:
    """
    Résout le modèle et la version à utiliser pour les analyses.

    Priorité :
    1. modèle actif
    2. dernier modèle du registre
    """
    latest_active_model_row = _pick_latest_row(
        active_model_df,
        ["deployed_at", "created_at"],
    )

    latest_registry_row = _pick_latest_row(
        model_registry_df,
        ["deployed_at", "created_at"],
    )

    source_row = (
        latest_active_model_row
        if latest_active_model_row is not None
        else latest_registry_row
    )

    default_model_name: str | None = None
    default_model_version: str | None = None

    if source_row is not None:
        model_name = source_row.get("model_name")
        model_version = source_row.get("model_version")

        if model_name is not None and pd.notna(model_name):
            default_model_name = str(model_name)

        if model_version is not None and pd.notna(model_version):
            default_model_version = str(model_version)

    return (
        default_model_name,
        default_model_version,
        latest_active_model_row,
        latest_registry_row,
    )


def _compute_global_kpis(
    *,
    summary: dict[str, Any],
    health: dict[str, Any],
    prediction_logs_df: pd.DataFrame,
    drift_metrics_df: pd.DataFrame,
    alerts_df: pd.DataFrame,
    metric_safe_number,
) -> dict[str, Any]:
    """
    Calcule les KPI globaux affichés en haut de page.

    Les valeurs viennent en priorité du summary API.
    Si elles sont absentes, on fait un calcul local depuis les DataFrames.
    """
    predictions_summary = _safe_dict(summary.get("predictions"))
    drift_summary = _safe_dict(summary.get("drift"))
    latest_eval_summary = _safe_dict(summary.get("latest_evaluation"))
    alerts_summary = _safe_dict(summary.get("alerts"))

    detected_count_df = 0
    if not drift_metrics_df.empty and "drift_detected" in drift_metrics_df.columns:
        detected_count_df = int(
            drift_metrics_df["drift_detected"].apply(_safe_bool).sum()
        )

    open_alerts_df = 0
    if not alerts_df.empty and "status" in alerts_df.columns:
        open_alerts_df = int(
            alerts_df["status"]
            .fillna("")
            .astype(str)
            .str.lower()
            .eq("open")
            .sum()
        )

    critical_alerts_df = 0
    if not alerts_df.empty and "severity" in alerts_df.columns:
        critical_alerts_df = int(
            alerts_df["severity"]
            .fillna("")
            .astype(str)
            .str.lower()
            .isin(["critical", "high"])
            .sum()
        )

    avg_latency_ms = predictions_summary.get("avg_latency_ms")

    if avg_latency_ms is None:
        avg_latency_ms = metric_safe_number(
            prediction_logs_df,
            "latency_ms",
            "mean",
            None,
        )

    return {
        "predictions_summary": predictions_summary,
        "drift_summary": drift_summary,
        "latest_eval_summary": latest_eval_summary,
        "alerts_summary": alerts_summary,
        "recall_value": latest_eval_summary.get("recall_score"),
        "roc_auc_value": latest_eval_summary.get("roc_auc"),
        "avg_latency_ms": avg_latency_ms,
        "detected_count": drift_summary.get("detected_drifts", detected_count_df),
        "open_alerts": alerts_summary.get("open_alerts", open_alerts_df),
        "critical_alerts": critical_alerts_df,
        "has_predictions": _safe_bool(health.get("has_predictions")),
        "has_drift_metrics": _safe_bool(health.get("has_drift_metrics")),
        "has_latest_evaluation": _safe_bool(health.get("has_latest_evaluation")),
    }


# =============================================================================
# Blocs d'affichage principaux
# =============================================================================

def _render_header_kpis(kpis: dict[str, Any]) -> None:
    """
    Affiche les cartes KPI principales du monitoring.
    """
    latency_value = kpis.get("avg_latency_ms")

    latency_label = (
        f"{float(latency_value):.1f} ms"
        if latency_value is not None and not pd.isna(latency_value)
        else "N/A"
    )

    k1, k2, k3, k4, k5 = st.columns(5)

    with k1:
        _render_card(
            "Alertes ouvertes",
            kpis.get("open_alerts", 0),
            "Incidents actifs à surveiller",
        )

    with k2:
        _render_card(
            "Drifts détectés",
            kpis.get("detected_count", 0),
            "Features ou métriques en dérive",
        )

    with k3:
        _render_card(
            "Recall",
            _safe_metric_value(kpis.get("recall_value"), 3),
            "Sensibilité du modèle",
        )

    with k4:
        _render_card(
            "ROC AUC",
            _safe_metric_value(kpis.get("roc_auc_value"), 3),
            "Qualité de séparation",
        )

    with k5:
        _render_card(
            "Latence moyenne",
            latency_label,
            "Temps total API + modèle",
        )


def _render_model_status_block(
    *,
    latest_active_model_row: pd.Series | None,
    latest_registry_row: pd.Series | None,
    kpis: dict[str, Any],
) -> None:
    """
    Affiche le bloc d'état courant du modèle et le signal global.
    """
    left, right = st.columns([2, 1])

    with left:
        _render_section_title(
            "État courant du modèle",
            "Vue rapide sur la version active et la dernière évaluation connue.",
        )

        row = (
            latest_active_model_row
            if latest_active_model_row is not None
            else latest_registry_row
        )

        if row is None:
            st.warning("Aucun modèle actif remonté par l'API.")
            return

        model_name = row.get("model_name", "N/A")
        model_version = row.get("model_version", "N/A")
        stage = row.get("stage", "N/A")
        deployed_at = row.get("deployed_at", None)
        run_id = row.get("run_id", "N/A")

        deployed_label = (
            deployed_at.strftime("%Y-%m-%d %H:%M:%S")
            if deployed_at is not None and pd.notna(deployed_at)
            else "N/A"
        )

        st.markdown(
            f"""
            <div style="
                padding: 18px;
                border-radius: 18px;
                background: #F8FAFC;
                border: 1px solid #E5E7EB;
                margin-bottom: 12px;
            ">
                <div style="font-size: 1.05rem; font-weight: 700; margin-bottom: 8px;">
                    {model_name} | version {model_version}
                </div>
                <div style="margin-bottom: 8px;">
                    {_status_badge(f"Stage : {stage}", "#2563EB")}
                </div>
                <div style="color: #4B5563; font-size: 0.92rem; margin-bottom: 4px;">
                    Déployé le : {deployed_label}
                </div>
                <div style="color: #6B7280; font-size: 0.85rem;">
                    Run ID : {run_id}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        _render_section_title(
            "Signal global",
            "Lecture rapide de la situation.",
        )

        health_color = "#16A34A"
        health_label = "Stable"

        if kpis.get("critical_alerts", 0) > 0:
            health_color = "#DC2626"
            health_label = "Critique"
        elif kpis.get("open_alerts", 0) > 0 or kpis.get("detected_count", 0) > 0:
            health_color = "#D97706"
            health_label = "Sous surveillance"

        if not kpis.get("has_predictions", False):
            health_color = "#6B7280"
            health_label = "Peu de signal"

        st.markdown(
            f"""
            <div style="
                padding: 20px;
                border-radius: 18px;
                background: {health_color};
                color: white;
                text-align: center;
                font-weight: 700;
                font-size: 1.2rem;
                margin-top: 6px;
            ">
                {health_label}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div style="font-size: 0.9rem; color: #6B7280; margin-top: 14px;">
                Prédictions : {"Oui" if kpis.get("has_predictions") else "Non"}<br>
                Drift disponible : {"Oui" if kpis.get("has_drift_metrics") else "Non"}<br>
                Évaluation dispo : {"Oui" if kpis.get("has_latest_evaluation") else "Non"}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_overview_tab(
    *,
    summary: dict[str, Any],
    health: dict[str, Any],
    prediction_logs_df: pd.DataFrame,
    drift_metrics_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
    latest_eval_row: pd.Series | None,
    latest_eval_summary: dict[str, Any],
    critical_alerts: int,
) -> None:
    """
    Affiche l'onglet Vue d'ensemble.

    Cet onglet donne une vision synthétique sans entrer dans les détails
    de performance ou de drift.
    """
    _render_section_title(
        "Vue d'ensemble",
        "Panorama consolidé du monitoring actuel.",
    )

    c1, c2, c3, c4 = st.columns(4)

    predictions_summary = _safe_dict(summary.get("predictions"))
    drift_summary = _safe_dict(summary.get("drift"))

    c1.metric(
        "Prédictions",
        predictions_summary.get("total_predictions", len(prediction_logs_df)),
    )

    c2.metric(
        "Lignes de drift",
        drift_summary.get("total_drift_metrics", len(drift_metrics_df)),
    )

    c3.metric("Modèles enregistrés", len(model_registry_df))
    c4.metric("Alertes critiques / hautes", critical_alerts)

    st.markdown("")

    left, right = st.columns([1.4, 1])

    with left:
        st.markdown("#### Résumé de monitoring")
        if summary:
            st.json(summary)
        else:
            st.info("Aucun résumé global disponible.")

        with st.expander("Voir la santé monitoring brute", expanded=False):
            if health:
                st.json(health)
            else:
                st.info("Aucun état de santé monitoring disponible.")

    with right:
        st.markdown("#### Dernière évaluation")

        if latest_eval_row is not None:
            st.write(f"**Dataset** : {latest_eval_row.get('dataset_name', 'N/A')}")
            st.write(f"**Recall** : {_safe_metric_value(latest_eval_row.get('recall_score'))}")
            st.write(f"**Precision** : {_safe_metric_value(latest_eval_row.get('precision_score'))}")
            st.write(f"**F1** : {_safe_metric_value(latest_eval_row.get('f1_score'))}")
            st.write(f"**Fbeta** : {_safe_metric_value(latest_eval_row.get('fbeta_score'))}")
            st.write(f"**ROC AUC** : {_safe_metric_value(latest_eval_row.get('roc_auc'))}")
            st.write(f"**Business cost** : {_safe_metric_value(latest_eval_row.get('business_cost'), 2)}")
            st.write(f"**Taille échantillon** : {_safe_int(latest_eval_row.get('sample_size'))}")

        elif latest_eval_summary:
            st.write(f"**Dataset** : {latest_eval_summary.get('dataset_name', 'N/A')}")
            st.write(f"**Recall** : {_safe_metric_value(latest_eval_summary.get('recall_score'))}")
            st.write(f"**Precision** : {_safe_metric_value(latest_eval_summary.get('precision_score'))}")
            st.write(f"**F1** : {_safe_metric_value(latest_eval_summary.get('f1_score'))}")
            st.write(f"**Business cost** : {_safe_metric_value(latest_eval_summary.get('business_cost'), 2)}")

        else:
            st.info("Aucune synthèse d'évaluation disponible.")


def _render_alerts_and_models_tab(
    *,
    alerts_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
    active_model_df: pd.DataFrame,
) -> None:
    """
    Affiche l'onglet Alertes & Modèles.
    """
    _render_section_title(
        "Alertes et registre des modèles",
        "Lecture des incidents de monitoring et du cycle de vie des versions.",
    )

    # -------------------------------------------------------------------------
    # Alertes
    # -------------------------------------------------------------------------
    st.markdown("#### Alertes")

    if alerts_df.empty:
        st.info("Aucune alerte disponible via `/monitoring/alerts`.")
    else:
        a1, a2, a3 = st.columns(3)

        open_alerts = 0
        if "status" in alerts_df.columns:
            open_alerts = int(
                alerts_df["status"]
                .fillna("")
                .astype(str)
                .str.lower()
                .eq("open")
                .sum()
            )

        severity_count = (
            alerts_df["severity"].nunique()
            if "severity" in alerts_df.columns
            else 0
        )

        a1.metric("Total alertes", len(alerts_df))
        a2.metric("Alertes ouvertes", open_alerts)
        a3.metric("Sévérités", severity_count)

        if "severity" in alerts_df.columns:
            severity_counts = alerts_df["severity"].fillna("unknown").value_counts()
            st.bar_chart(severity_counts)

        with st.expander("Voir les alertes", expanded=True):
            preferred_cols = _choose_existing_columns(
                alerts_df,
                [
                    "created_at",
                    "alert_type",
                    "severity",
                    "status",
                    "model_name",
                    "model_version",
                    "feature_name",
                    "title",
                    "message",
                    "acknowledged_at",
                    "resolved_at",
                    "context",
                ],
            )

            st.dataframe(
                alerts_df[preferred_cols] if preferred_cols else alerts_df,
                width="stretch",
            )

    # -------------------------------------------------------------------------
    # Registre des modèles
    # -------------------------------------------------------------------------
    st.markdown("")
    st.markdown("#### Registre des modèles")

    if model_registry_df.empty:
        st.info("Aucune donnée disponible via `/monitoring/models`.")
    else:
        registry_view = model_registry_df.copy()

        if "deployed_at" in registry_view.columns:
            registry_view = registry_view.sort_values(
                "deployed_at",
                ascending=False,
                na_position="last",
            )
        elif "created_at" in registry_view.columns:
            registry_view = registry_view.sort_values(
                "created_at",
                ascending=False,
                na_position="last",
            )

        with st.expander("Voir le registre des modèles", expanded=True):
            preferred_cols = _choose_existing_columns(
                registry_view,
                [
                    "model_name",
                    "model_version",
                    "stage",
                    "is_active",
                    "run_id",
                    "training_data_version",
                    "deployed_at",
                    "created_at",
                    "source_path",
                    "metrics",
                    "hyperparameters",
                ],
            )

            st.dataframe(
                registry_view[preferred_cols] if preferred_cols else registry_view,
                width="stretch",
            )

    # -------------------------------------------------------------------------
    # Modèle actif
    # -------------------------------------------------------------------------
    st.markdown("")
    st.markdown("#### Modèle actif")

    active_row = _pick_latest_row(active_model_df, ["deployed_at", "created_at"])

    if active_row is None:
        st.warning("Aucun modèle actif disponible via `/monitoring/active-model`.")
        return

    st.success(
        f"Modèle actif : {active_row.get('model_name', 'N/A')} | "
        f"version {active_row.get('model_version', 'N/A')} | "
        f"stage {active_row.get('stage', 'N/A')}"
    )

    preferred_cols = _choose_existing_columns(
        active_model_df,
        [
            "model_name",
            "model_version",
            "stage",
            "is_active",
            "run_id",
            "training_data_version",
            "deployed_at",
            "created_at",
            "source_path",
            "metrics",
            "hyperparameters",
        ],
    )

    st.dataframe(
        active_model_df[preferred_cols] if preferred_cols else active_model_df,
        width="stretch",
    )


# =============================================================================
# Page principale
# =============================================================================

def render_monitoring_page(
    *,
    base_url: str,
    api_key: str,
    monitoring_summary: dict,
    monitoring_health: dict,
    prediction_logs_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
    active_model_df: pd.DataFrame,
    evaluation_metrics_df: pd.DataFrame,
    drift_metrics_df: pd.DataFrame,
    alerts_df: pd.DataFrame,
    feature_store_monitoring_df: pd.DataFrame,
    metric_safe_number,
    run_evidently_analysis,
    run_evidently_analysis_from_feature_store,
    run_monitoring_evaluation_analysis,
) -> None:
    """
    Affiche la page Streamlit de monitoring MLOps.

    Parameters
    ----------
    base_url : str
        URL de l'API. Conservée dans la signature pour compatibilité.
    api_key : str
        Clé API. Conservée dans la signature pour compatibilité.
    monitoring_summary : dict
        Résumé global du monitoring.
    monitoring_health : dict
        Santé globale du monitoring.
    prediction_logs_df : pd.DataFrame
        Historique des prédictions.
    model_registry_df : pd.DataFrame
        Registre des modèles.
    active_model_df : pd.DataFrame
        Modèle actif.
    evaluation_metrics_df : pd.DataFrame
        Métriques d'évaluation.
    drift_metrics_df : pd.DataFrame
        Métriques de drift.
    alerts_df : pd.DataFrame
        Alertes de monitoring.
    feature_store_monitoring_df : pd.DataFrame
        Snapshots de features utilisés pour le monitoring.
    metric_safe_number : callable
        Fonction utilitaire pour calculer mean, p95, p99, etc.
    run_evidently_analysis : callable
        Fonction API pour lancer Evidently.
    run_evidently_analysis_from_feature_store : callable
        Fonction API pour lancer Evidently depuis le feature store.
    run_monitoring_evaluation_analysis : callable
        Fonction API pour lancer l'évaluation monitoring.

    Notes
    -----
    base_url et api_key ne sont pas utilisés directement ici, mais ils restent
    dans la signature pour ne pas casser l'appel depuis dashboard_main.py.
    """
    _ = base_url
    _ = api_key

    summary = _safe_dict(monitoring_summary)
    health = _safe_dict(monitoring_health)

    (
        prediction_logs_df,
        model_registry_df,
        active_model_df,
        evaluation_metrics_df,
        drift_metrics_df,
        alerts_df,
        feature_store_monitoring_df,
    ) = _prepare_monitoring_data(
        prediction_logs_df=prediction_logs_df,
        model_registry_df=model_registry_df,
        active_model_df=active_model_df,
        evaluation_metrics_df=evaluation_metrics_df,
        drift_metrics_df=drift_metrics_df,
        alerts_df=alerts_df,
        feature_store_monitoring_df=feature_store_monitoring_df,
    )

    (
        default_model_name,
        default_model_version,
        latest_active_model_row,
        latest_registry_row,
    ) = _resolve_default_model_context(
        active_model_df=active_model_df,
        model_registry_df=model_registry_df,
    )

    latest_eval_row = _pick_latest_row(
        evaluation_metrics_df,
        ["computed_at"],
    )

    kpis = _compute_global_kpis(
        summary=summary,
        health=health,
        prediction_logs_df=prediction_logs_df,
        drift_metrics_df=drift_metrics_df,
        alerts_df=alerts_df,
        metric_safe_number=metric_safe_number,
    )

    latest_eval_summary = _safe_dict(kpis.get("latest_eval_summary"))

    # -------------------------------------------------------------------------
    # En-tête
    # -------------------------------------------------------------------------
    st.markdown("## Monitoring MLOps")
    st.caption(
        "Centre de pilotage du modèle en production : performance, dérive, alertes, latence et cycle de vie des versions."
    )

    _render_header_kpis(kpis)

    st.markdown("")

    _render_model_status_block(
        latest_active_model_row=latest_active_model_row,
        latest_registry_row=latest_registry_row,
        kpis=kpis,
    )

    # -------------------------------------------------------------------------
    # Onglets
    # -------------------------------------------------------------------------
    tabs = st.tabs(
        [
            "Vue d'ensemble",
            "Performance",
            "Dérive",
            "Alertes & Modèles",
        ]
    )

    with tabs[0]:
        _render_overview_tab(
            summary=summary,
            health=health,
            prediction_logs_df=prediction_logs_df,
            drift_metrics_df=drift_metrics_df,
            model_registry_df=model_registry_df,
            latest_eval_row=latest_eval_row,
            latest_eval_summary=latest_eval_summary,
            critical_alerts=int(kpis.get("critical_alerts", 0)),
        )

    with tabs[1]:
        render_performance_tab(
            evaluation_metrics_df=evaluation_metrics_df,
            prediction_logs_df=prediction_logs_df,
            default_model_name=default_model_name,
            default_model_version=default_model_version,
            metric_safe_number=metric_safe_number,
            run_monitoring_evaluation_analysis=run_monitoring_evaluation_analysis,
            render_section_title=_render_section_title,
            safe_metric_value=_safe_metric_value,
            safe_float=lambda value, default=None: (
                default if value is None or pd.isna(value) else float(value)
            ),
            choose_existing_columns=_choose_existing_columns,
        )

    with tabs[2]:
        render_drift_tab(
            drift_metrics_df=drift_metrics_df,
            feature_store_monitoring_df=feature_store_monitoring_df,
            default_model_name=default_model_name,
            default_model_version=default_model_version,
            run_evidently_analysis=run_evidently_analysis,
            run_evidently_analysis_from_feature_store=run_evidently_analysis_from_feature_store,
            render_section_title=_render_section_title,
            safe_bool=_safe_bool,
            choose_existing_columns=_choose_existing_columns,
        )

    with tabs[3]:
        _render_alerts_and_models_tab(
            alerts_df=alerts_df,
            model_registry_df=model_registry_df,
            active_model_df=active_model_df,
        )