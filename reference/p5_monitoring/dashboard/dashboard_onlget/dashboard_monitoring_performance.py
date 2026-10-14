"""
Onglet Streamlit : performance du monitoring MLOps.

Ce module contient uniquement l'affichage lié :
- aux métriques d'évaluation du modèle
- au lancement manuel de l'évaluation monitoring
- à la latence totale API
- au temps pur d'inférence du modèle

Objectif pédagogique
--------------------
Séparer l'onglet "Performance" du fichier principal permet :
- de rendre le dashboard plus lisible
- de limiter la taille des fichiers
- de mieux organiser le projet par responsabilité

Ce fichier ne lit pas directement PostgreSQL.
Il affiche uniquement des DataFrames déjà préparés par dashboard_request.py.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
import streamlit as st


# =============================================================================
# Helpers locaux
# =============================================================================

def _safe_round(value: Any, decimals: int = 2, default: float = 0.0) -> float:
    """
    Convertit une valeur en float puis l'arrondit.

    Cette fonction évite que Streamlit plante si une métrique est absente,
    nulle ou non numérique.
    """
    try:
        if value is None or pd.isna(value):
            return default

        return round(float(value), decimals)

    except Exception:
        return default


def _get_time_column(df: pd.DataFrame) -> str | None:
    """
    Retourne la meilleure colonne temporelle disponible pour les logs.

    Priorité :
    1. prediction_timestamp
    2. created_at
    """
    if "prediction_timestamp" in df.columns:
        return "prediction_timestamp"

    if "created_at" in df.columns:
        return "created_at"

    return None


def _build_metric_options(df: pd.DataFrame) -> list[str]:
    """
    Retourne les métriques disponibles pour le graphique d'évaluation.
    """
    preferred_metrics = [
        "roc_auc",
        "pr_auc",
        "precision_score",
        "recall_score",
        "f1_score",
        "fbeta_score",
        "business_cost",
        "sample_size",
    ]

    return [metric for metric in preferred_metrics if metric in df.columns]


def _format_api_error(result: Any, fallback: str) -> str:
    """
    Extrait un message d'erreur lisible depuis une réponse API.
    """
    if isinstance(result, dict):
        return (
            result.get("message")
            or result.get("detail")
            or result.get("error")
            or fallback
        )

    if result:
        return str(result)

    return fallback


# =============================================================================
# Bloc métriques d'évaluation
# =============================================================================

def _render_latest_evaluation_kpis(
    *,
    evaluation_metrics_df: pd.DataFrame,
    safe_metric_value: Callable[..., str],
) -> None:
    """
    Affiche les KPI de la dernière évaluation disponible.
    """
    if evaluation_metrics_df.empty:
        st.info("Aucune métrique disponible via `/monitoring/evaluation`.")
        return

    latest_eval_df = evaluation_metrics_df.copy()

    if "computed_at" in latest_eval_df.columns:
        latest_eval_df = latest_eval_df.sort_values(
            "computed_at",
            ascending=False,
        )

    latest_eval_df = latest_eval_df.head(1)

    if latest_eval_df.empty:
        st.info("Aucune dernière évaluation exploitable.")
        return

    row = latest_eval_df.iloc[0]

    m1, m2, m3, m4, m5 = st.columns(5)

    m1.metric("ROC AUC", safe_metric_value(row.get("roc_auc"), 3))
    m2.metric("PR AUC", safe_metric_value(row.get("pr_auc"), 3))
    m3.metric("Precision", safe_metric_value(row.get("precision_score"), 3))
    m4.metric("Recall", safe_metric_value(row.get("recall_score"), 3))
    m5.metric("Fbeta", safe_metric_value(row.get("fbeta_score"), 3))


def _render_evaluation_timeline(
    *,
    evaluation_metrics_df: pd.DataFrame,
) -> None:
    """
    Affiche l'évolution temporelle d'une métrique d'évaluation.
    """
    metric_options = _build_metric_options(evaluation_metrics_df)

    if not metric_options:
        st.info("Aucune métrique numérique exploitable pour le graphique.")
        return

    selected_metric = st.selectbox(
        "Métrique à tracer",
        options=metric_options,
        key="monitoring_metric_selector",
    )

    plot_df = evaluation_metrics_df.copy()

    if selected_metric in plot_df.columns:
        plot_df = plot_df.dropna(subset=[selected_metric])

    if "computed_at" in plot_df.columns:
        plot_df = plot_df.sort_values("computed_at")

    if not plot_df.empty and "computed_at" in plot_df.columns:
        st.line_chart(plot_df.set_index("computed_at")[[selected_metric]])
    elif not plot_df.empty:
        st.dataframe(plot_df[[selected_metric]], width="stretch")
    else:
        st.info("Aucune donnée disponible pour la métrique sélectionnée.")


def _render_evaluation_detail_table(
    *,
    evaluation_metrics_df: pd.DataFrame,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche la table complète des métriques d'évaluation.
    """
    with st.expander("Voir le détail complet des métriques"):
        preferred_cols = choose_existing_columns(
            evaluation_metrics_df,
            [
                "computed_at",
                "dataset_name",
                "model_name",
                "model_version",
                "roc_auc",
                "pr_auc",
                "precision_score",
                "recall_score",
                "f1_score",
                "fbeta_score",
                "business_cost",
                "tn",
                "fp",
                "fn",
                "tp",
                "sample_size",
            ],
        )

        st.dataframe(
            evaluation_metrics_df[preferred_cols]
            if preferred_cols
            else evaluation_metrics_df,
            width="stretch",
        )


def _render_evaluation_metrics_section(
    *,
    evaluation_metrics_df: pd.DataFrame,
    safe_metric_value: Callable[..., str],
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche toute la section des métriques d'évaluation modèle.
    """
    if evaluation_metrics_df.empty:
        st.info("Aucune métrique disponible via `/monitoring/evaluation`.")
        return

    _render_latest_evaluation_kpis(
        evaluation_metrics_df=evaluation_metrics_df,
        safe_metric_value=safe_metric_value,
    )

    _render_evaluation_timeline(
        evaluation_metrics_df=evaluation_metrics_df,
    )

    _render_evaluation_detail_table(
        evaluation_metrics_df=evaluation_metrics_df,
        choose_existing_columns=choose_existing_columns,
    )


# =============================================================================
# Bloc lancement d'évaluation monitoring
# =============================================================================

def _render_evaluation_run_form(
    *,
    default_model_name: str | None,
    default_model_version: str | None,
    run_monitoring_evaluation_analysis: Callable[..., tuple[bool, Any]],
) -> None:
    """
    Affiche le formulaire permettant de lancer une évaluation monitoring.

    Cette évaluation compare les prédictions loguées avec les vérités terrain
    disponibles, puis persiste les métriques côté API.
    """
    st.markdown("")
    st.markdown("#### Exécution évaluation monitoring")

    eval_col1, eval_col2, eval_col3, eval_col4 = st.columns([1, 1, 1, 1])

    with eval_col1:
        evaluation_dataset_name = st.text_input(
            "Dataset name",
            value="scoring_prod",
            key="monitoring_eval_dataset_name",
        )

    with eval_col2:
        evaluation_beta = st.number_input(
            "Beta",
            min_value=0.5,
            max_value=10.0,
            value=2.0,
            step=0.5,
            key="monitoring_eval_beta",
        )

    with eval_col3:
        evaluation_cost_fn = st.number_input(
            "Coût FN",
            min_value=0.0,
            value=10.0,
            step=1.0,
            key="monitoring_eval_cost_fn",
        )

    with eval_col4:
        evaluation_cost_fp = st.number_input(
            "Coût FP",
            min_value=0.0,
            value=1.0,
            step=1.0,
            key="monitoring_eval_cost_fp",
        )

    run_eval_clicked = st.button(
        "Lancer l'évaluation monitoring",
        type="primary",
        use_container_width=True,
        key="run_monitoring_evaluation_button",
    )

    if not run_eval_clicked:
        return

    if not default_model_name:
        st.error(
            "Impossible de lancer l'évaluation monitoring : "
            "aucun modèle actif ou enregistré n'a été trouvé."
        )
        return

    with st.spinner("Évaluation monitoring en cours..."):
        ok, result = run_monitoring_evaluation_analysis(
            model_name=default_model_name,
            model_version=default_model_version,
            dataset_name=evaluation_dataset_name,
            beta=float(evaluation_beta),
            cost_fn=float(evaluation_cost_fn),
            cost_fp=float(evaluation_cost_fp),
        )

    if not ok:
        detail = _format_api_error(
            result,
            "Erreur API pendant l'évaluation monitoring.",
        )
        st.error(f"Erreur API évaluation monitoring : {detail}")
        return

    if not isinstance(result, dict) or not result.get("success", False):
        detail = _format_api_error(
            result,
            "Évaluation monitoring non réussie.",
        )
        st.error(f"Évaluation monitoring non réussie : {detail}")
        return

    st.success(
        result.get(
            "message",
            "Évaluation monitoring exécutée avec succès.",
        )
    )

    info_parts = []

    for key in ["sample_size", "matched_rows", "threshold_used"]:
        value = result.get(key)
        if value is not None:
            info_parts.append(f"{key}={value}")

    if info_parts:
        st.caption(" | ".join(info_parts))

    with st.expander("Voir le résultat de l'évaluation", expanded=False):
        st.json(result)

    st.cache_data.clear()
    st.rerun()


# =============================================================================
# Bloc latence
# =============================================================================

def _prepare_latency_dataframe(
    *,
    prediction_logs_df: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """
    Prépare les logs de prédiction pour l'analyse de latence.
    """
    if prediction_logs_df.empty:
        return pd.DataFrame(), None

    latency_time_col = _get_time_column(prediction_logs_df)

    if latency_time_col is None:
        return pd.DataFrame(), None

    if "latency_ms" not in prediction_logs_df.columns:
        return pd.DataFrame(), latency_time_col

    latency_df = (
        prediction_logs_df
        .dropna(subset=[latency_time_col])
        .sort_values(latency_time_col)
        .copy()
    )

    return latency_df, latency_time_col


def _render_latency_chart(
    *,
    latency_df: pd.DataFrame,
    latency_time_col: str,
) -> None:
    """
    Affiche l'évolution de la latence totale et de l'inférence.
    """
    chart_cols = ["latency_ms"]

    if "inference_latency_ms" in latency_df.columns:
        chart_cols.append("inference_latency_ms")

    st.line_chart(latency_df.set_index(latency_time_col)[chart_cols])


def _compute_latency_stats(
    *,
    latency_df: pd.DataFrame,
    metric_safe_number: Callable[..., Any],
    safe_float: Callable[..., float | None],
) -> dict[str, float | None]:
    """
    Calcule les statistiques de latence utiles au dashboard.
    """
    stats: dict[str, float | None] = {
        "latency_mean": _safe_round(
            safe_float(metric_safe_number(latency_df, "latency_ms", "mean", 0), 0.0),
            2,
        ),
        "latency_p95": _safe_round(
            safe_float(metric_safe_number(latency_df, "latency_ms", "p95", 0), 0.0),
            2,
        ),
        "latency_p99": _safe_round(
            safe_float(metric_safe_number(latency_df, "latency_ms", "p99", 0), 0.0),
            2,
        ),
        "inference_mean": None,
        "inference_p95": None,
        "inference_p99": None,
    }

    if "inference_latency_ms" in latency_df.columns:
        stats["inference_mean"] = _safe_round(
            safe_float(
                metric_safe_number(
                    latency_df,
                    "inference_latency_ms",
                    "mean",
                    0,
                ),
                0.0,
            ),
            2,
        )
        stats["inference_p95"] = _safe_round(
            safe_float(
                metric_safe_number(
                    latency_df,
                    "inference_latency_ms",
                    "p95",
                    0,
                ),
                0.0,
            ),
            2,
        )
        stats["inference_p99"] = _safe_round(
            safe_float(
                metric_safe_number(
                    latency_df,
                    "inference_latency_ms",
                    "p99",
                    0,
                ),
                0.0,
            ),
            2,
        )

    return stats


def _render_latency_kpis(stats: dict[str, float | None]) -> None:
    """
    Affiche les KPI de latence.
    """
    l1, l2, l3 = st.columns(3)

    l1.metric("Latence totale moyenne", f"{stats['latency_mean']} ms")
    l2.metric("Latence totale p95", f"{stats['latency_p95']} ms")
    l3.metric("Latence totale p99", f"{stats['latency_p99']} ms")

    i1, i2, i3 = st.columns(3)

    if stats.get("inference_mean") is not None:
        i1.metric("Inférence moyenne", f"{stats['inference_mean']} ms")
        i2.metric("Inférence p95", f"{stats['inference_p95']} ms")
        i3.metric("Inférence p99", f"{stats['inference_p99']} ms")
    else:
        i1.metric("Inférence moyenne", "N/A")
        i2.metric("Inférence p95", "N/A")
        i3.metric("Inférence p99", "N/A")


def _render_latency_detail_table(
    *,
    latency_df: pd.DataFrame,
    latency_time_col: str,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche le détail des logs de latence.
    """
    with st.expander("Voir les logs de latence"):
        preferred_cols = choose_existing_columns(
            latency_df,
            [
                latency_time_col,
                "request_id",
                "client_id",
                "model_name",
                "model_version",
                "score",
                "prediction",
                "threshold",
                "threshold_used",
                "latency_ms",
                "inference_latency_ms",
                "status",
                "error_message",
            ],
        )

        st.dataframe(
            latency_df[preferred_cols] if preferred_cols else latency_df,
            width="stretch",
        )


def _render_latency_section(
    *,
    prediction_logs_df: pd.DataFrame,
    metric_safe_number: Callable[..., Any],
    safe_float: Callable[..., float | None],
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche toute la section latence.
    """
    st.markdown("")
    st.markdown("#### Latence et temps pur d'inférence")

    if prediction_logs_df.empty:
        st.info("Aucune donnée de prédiction disponible via `/history/predictions`.")
        return

    latency_time_col = _get_time_column(prediction_logs_df)

    if latency_time_col is None:
        st.info(
            "Aucune colonne temporelle exploitable n'est présente "
            "dans les logs de prédiction."
        )
        return

    if "latency_ms" not in prediction_logs_df.columns:
        st.info("La colonne `latency_ms` est absente des données reçues.")
        return

    latency_df, latency_time_col = _prepare_latency_dataframe(
        prediction_logs_df=prediction_logs_df,
    )

    if latency_df.empty or latency_time_col is None:
        st.info("Aucune ligne de latence exploitable après filtrage des dates.")
        return

    _render_latency_chart(
        latency_df=latency_df,
        latency_time_col=latency_time_col,
    )

    stats = _compute_latency_stats(
        latency_df=latency_df,
        metric_safe_number=metric_safe_number,
        safe_float=safe_float,
    )

    _render_latency_kpis(stats)

    st.caption(
        "`latency_ms` = temps total côté service. "
        "`inference_latency_ms` = temps pur du modèle, utile pour comparer ONNX."
    )

    _render_latency_detail_table(
        latency_df=latency_df,
        latency_time_col=latency_time_col,
        choose_existing_columns=choose_existing_columns,
    )


# =============================================================================
# Fonction principale appelée par dashboard_monitoring.py
# =============================================================================

def render_performance_tab(
    *,
    evaluation_metrics_df: pd.DataFrame,
    prediction_logs_df: pd.DataFrame,
    default_model_name: str | None,
    default_model_version: str | None,
    metric_safe_number: Callable[..., Any],
    run_monitoring_evaluation_analysis: Callable[..., tuple[bool, Any]],
    render_section_title: Callable[[str, str], None],
    safe_metric_value: Callable[..., str],
    safe_float: Callable[..., float | None],
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche l'onglet Performance du dashboard monitoring.

    Parameters
    ----------
    evaluation_metrics_df : pd.DataFrame
        Historique des métriques d'évaluation du modèle.
    prediction_logs_df : pd.DataFrame
        Historique des prédictions, utilisé ici pour analyser la latence.
    default_model_name : str | None
        Nom du modèle actif ou du dernier modèle connu.
    default_model_version : str | None
        Version du modèle actif ou du dernier modèle connu.
    metric_safe_number : callable
        Fonction utilitaire pour calculer mean, p95, p99, etc.
    run_monitoring_evaluation_analysis : callable
        Fonction qui déclenche l'évaluation monitoring côté API.
    render_section_title : callable
        Helper UI fourni par le fichier principal.
    safe_metric_value : callable
        Helper pour formater les métriques modèle.
    safe_float : callable
        Helper pour convertir proprement une valeur en float.
    choose_existing_columns : callable
        Helper pour afficher seulement les colonnes disponibles.
    """
    render_section_title(
        "Performance du modèle",
        "Évolution des métriques métier et comportement de latence.",
    )

    _render_evaluation_metrics_section(
        evaluation_metrics_df=evaluation_metrics_df,
        safe_metric_value=safe_metric_value,
        choose_existing_columns=choose_existing_columns,
    )

    _render_evaluation_run_form(
        default_model_name=default_model_name,
        default_model_version=default_model_version,
        run_monitoring_evaluation_analysis=run_monitoring_evaluation_analysis,
    )

    _render_latency_section(
        prediction_logs_df=prediction_logs_df,
        metric_safe_number=metric_safe_number,
        safe_float=safe_float,
        choose_existing_columns=choose_existing_columns,
    )