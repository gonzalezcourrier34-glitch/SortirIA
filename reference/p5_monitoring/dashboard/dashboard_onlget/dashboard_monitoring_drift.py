"""
Onglet Streamlit : dérive des données du monitoring MLOps.

Ce module contient uniquement l'affichage lié :
- aux métriques de drift
- aux colonnes en dérive
- aux visualisations Evidently
- au lancement manuel des analyses Evidently

Objectif pédagogique
--------------------
Séparer la partie "drift" du fichier principal dashboard_monitoring.py.

Cela permet :
- de garder un dashboard plus lisible
- de faciliter la maintenance
- de montrer clairement au jury que le monitoring est organisé par domaine
"""

from __future__ import annotations

import ast
import json
from typing import Any, Callable

import pandas as pd
import streamlit as st


# =============================================================================
# Helpers de parsing Evidently
# =============================================================================

def _parse_details(details: Any) -> dict[str, Any]:
    """
    Convertit la colonne `details` en dictionnaire Python.

    Selon la source, `details` peut être :
    - déjà un dictionnaire
    - une chaîne JSON
    - une chaîne représentant un dictionnaire Python
    - None

    Cette fonction évite que le dashboard plante si le format varie.
    """
    if isinstance(details, dict):
        return details

    if details is None:
        return {}

    if isinstance(details, str):
        text = details.strip()

        if not text:
            return {}

        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        try:
            parsed = ast.literal_eval(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


def _extract_drifted_columns_count(details: Any) -> float | None:
    """
    Extrait le nombre de colonnes en dérive depuis la colonne `details`.

    Cette information est parfois stockée dans :
    - number_of_drifted_columns
    - drifted_columns
    - n_drifted_columns
    - raw_summary
    """
    parsed = _parse_details(details)

    for key in [
        "number_of_drifted_columns",
        "drifted_columns",
        "n_drifted_columns",
    ]:
        value = parsed.get(key)

        try:
            if value is not None and not pd.isna(value):
                return float(value)
        except Exception:
            continue

    raw_summary = parsed.get("raw_summary")

    if isinstance(raw_summary, dict):
        for key in [
            "number_of_drifted_columns",
            "drifted_columns",
            "n_drifted_columns",
        ]:
            value = raw_summary.get(key)

            try:
                if value is not None and not pd.isna(value):
                    return float(value)
            except Exception:
                continue

    return None


# =============================================================================
# Helpers de préparation du drift
# =============================================================================

def _build_drift_severity_label(
    *,
    drift_detected: bool,
    metric_value: float | None,
    threshold_value: float | None,
) -> str:
    """
    Détermine une sévérité simple à partir du score de drift.

    Logique utilisée :
    - OK : aucun drift détecté
    - Drift détecté : score ou seuil indisponible
    - Faible : score légèrement au-dessus du seuil
    - Moyen : score nettement au-dessus du seuil
    - Fort : score très au-dessus du seuil
    """
    if not drift_detected:
        return "OK"

    if metric_value is None or threshold_value is None:
        return "Drift détecté"

    if threshold_value <= 0:
        return "Drift détecté"

    ratio = metric_value / threshold_value

    if ratio < 1.25:
        return "Faible"

    if ratio < 2.0:
        return "Moyen"

    return "Fort"

def _normalize_drift_feature_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise le nom de colonne de feature.

    Selon l'API ou le schéma SQL, la colonne peut s'appeler :
    - feature_name
    - column_name
    - column
    - variable
    """
    if df.empty:
        return df.copy()

    out = df.copy()

    if "feature_name" in out.columns:
        return out

    for candidate in ["column_name", "column", "variable", "feature"]:
        if candidate in out.columns:
            out["feature_name"] = out[candidate]
            return out

    return out

def _prepare_enriched_drift_table(
    *,
    drift_df: pd.DataFrame,
    safe_bool: Callable[[Any], bool],
) -> pd.DataFrame:
    """
    Prépare une table de drift enrichie pour l'affichage.

    Colonnes ajoutées :
    - drift_detected_bool : booléen fiable
    - distance_to_threshold : écart entre score et seuil
    - drift_severity : sévérité lisible
    """
    if drift_df.empty:
        return drift_df.copy()

    out = drift_df.copy()

    if "drift_detected" in out.columns:
        out["drift_detected_bool"] = out["drift_detected"].apply(safe_bool)
    else:
        out["drift_detected_bool"] = False

    if {"metric_value", "threshold_value"}.issubset(out.columns):
        out["distance_to_threshold"] = (
            out["metric_value"] - out["threshold_value"]
        )
    else:
        out["distance_to_threshold"] = pd.NA

    def _row_severity(row: pd.Series) -> str:
        """
        Calcule la sévérité ligne par ligne.
        """
        metric_value = row.get("metric_value")
        threshold_value = row.get("threshold_value")

        try:
            metric_value = float(metric_value) if pd.notna(metric_value) else None
        except Exception:
            metric_value = None

        try:
            threshold_value = (
                float(threshold_value)
                if pd.notna(threshold_value)
                else None
            )
        except Exception:
            threshold_value = None

        return _build_drift_severity_label(
            drift_detected=bool(row.get("drift_detected_bool", False)),
            metric_value=metric_value,
            threshold_value=threshold_value,
        )

    out["drift_severity"] = out.apply(_row_severity, axis=1)

    return out


def _extract_latest_execution_df(
    *,
    drift_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Retourne les lignes correspondant à la dernière exécution de drift connue.
    """
    if drift_df.empty:
        return drift_df.copy()

    out = drift_df.copy()

    if "computed_at" not in out.columns:
        return out

    out = out.dropna(subset=["computed_at"])

    if out.empty:
        return out

    latest_computed_at = out["computed_at"].max()

    return out[out["computed_at"] == latest_computed_at].copy()


def _render_drift_interpretation(drift_rate: float) -> None:
    """
    Affiche une interprétation automatique du taux de drift.
    """
    if drift_rate == 0:
        st.success(
            "Aucune dérive significative détectée sur les features analysées."
        )
    elif drift_rate < 10:
        st.warning(
            "Dérive faible : quelques variables changent, "
            "mais le signal reste globalement stable."
        )
    elif drift_rate < 30:
        st.warning(
            "Dérive modérée : plusieurs variables évoluent. "
            "Une analyse des features importantes est recommandée."
        )
    else:
        st.error(
            "Dérive forte : la population courante est très différente "
            "de la référence. Une réévaluation du modèle est nécessaire."
        )


# =============================================================================
# Blocs d'affichage drift
# =============================================================================

def _render_drift_kpis(
    *,
    drift_enriched_df: pd.DataFrame,
    per_feature_all_df: pd.DataFrame,
) -> None:
    """
    Affiche les KPI principaux de drift.
    """
    total_features = (
        per_feature_all_df["feature_name"].nunique()
        if "feature_name" in per_feature_all_df.columns
        else 0
    )

    drifted_features = (
        per_feature_all_df.loc[
            per_feature_all_df["drift_detected_bool"],
            "feature_name",
        ].nunique()
        if {"feature_name", "drift_detected_bool"}.issubset(
            per_feature_all_df.columns
        )
        else 0
    )

    drift_rate = (
        drifted_features / total_features * 100
        if total_features > 0
        else 0
    )

    detected_rows = (
        int(drift_enriched_df["drift_detected_bool"].sum())
        if "drift_detected_bool" in drift_enriched_df.columns
        else 0
    )

    d1, d2, d3, d4 = st.columns(4)

    d1.metric("Lignes drift", len(drift_enriched_df))
    d2.metric("Drifts détectés", detected_rows)
    d3.metric("Features monitorées", total_features)
    d4.metric("Taux de features en drift", f"{drift_rate:.1f} %")

    _render_drift_interpretation(drift_rate)


def _render_drift_filters(
    *,
    drift_enriched_df: pd.DataFrame,
) -> tuple[bool, str]:
    """
    Affiche les filtres utilisateur et retourne leurs valeurs.
    """
    filter_col1, filter_col2 = st.columns([1, 1])

    with filter_col1:
        only_drift = st.toggle(
            "Uniquement les drifts détectés",
            value=True,
            key="toggle_only_detected_drift",
        )

    with filter_col2:
        metric_names = (
            sorted(
                drift_enriched_df["metric_name"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )
            if "metric_name" in drift_enriched_df.columns
            else []
        )

        selected_metric_name = st.selectbox(
            "Métrique de drift",
            options=["Toutes"] + metric_names,
            key="drift_metric_name_selector",
        )

    return only_drift, selected_metric_name


def _filter_drift_view(
    *,
    drift_enriched_df: pd.DataFrame,
    only_drift: bool,
    selected_metric_name: str,
) -> pd.DataFrame:
    """
    Applique les filtres à la table de drift.
    """
    drift_view = drift_enriched_df.copy()

    if only_drift and "drift_detected_bool" in drift_view.columns:
        drift_view = drift_view[drift_view["drift_detected_bool"]]

    if selected_metric_name != "Toutes" and "metric_name" in drift_view.columns:
        drift_view = drift_view[
            drift_view["metric_name"].astype(str) == selected_metric_name
        ]

    return drift_view


def _render_drifted_columns(
    *,
    per_feature_all_df: pd.DataFrame,
) -> None:
    """
    Affiche la liste claire des colonnes en dérive.
    """
    st.markdown("")
    st.markdown("#### Colonnes en dérive")

    required_cols = {"feature_name", "drift_detected_bool"}

    if per_feature_all_df.empty or not required_cols.issubset(per_feature_all_df.columns):
        st.info("Impossible de déterminer les colonnes en dérive.")
        return

    drifted_cols = (
        per_feature_all_df.loc[
            per_feature_all_df["drift_detected_bool"],
            "feature_name",
        ]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    drifted_cols = sorted(drifted_cols)

    if not drifted_cols:
        st.info("Aucune colonne en dérive détectée.")
        return

    st.success(f"{len(drifted_cols)} colonnes en dérive détectées")
    st.code(", ".join(drifted_cols), language="text")


def _render_latest_drift_execution(
    *,
    per_feature_all_df: pd.DataFrame,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche un résumé de la dernière exécution de drift.
    """
    st.markdown("#### Dernière exécution de drift")

    latest_drift_df = _extract_latest_execution_df(
        drift_df=per_feature_all_df,
    )

    if latest_drift_df.empty:
        st.info("Aucune dernière exécution exploitable.")
        return

    latest_drifted_df = (
        latest_drift_df[latest_drift_df["drift_detected_bool"]].copy()
        if "drift_detected_bool" in latest_drift_df.columns
        else pd.DataFrame()
    )

    latest_total_features = (
        latest_drift_df["feature_name"].nunique()
        if "feature_name" in latest_drift_df.columns
        else 0
    )

    latest_drifted_features = (
        latest_drifted_df["feature_name"].nunique()
        if not latest_drifted_df.empty and "feature_name" in latest_drifted_df.columns
        else 0
    )

    latest_rate = (
        latest_drifted_features / latest_total_features * 100
        if latest_total_features > 0
        else 0
    )

    l1, l2, l3 = st.columns(3)

    l1.metric("Features dernière analyse", latest_total_features)
    l2.metric("Features driftées", latest_drifted_features)
    l3.metric("Taux dernière analyse", f"{latest_rate:.1f} %")

    if latest_drifted_df.empty:
        st.info("Aucune feature driftée sur la dernière exécution.")
        return

    latest_drifted_view = latest_drifted_df.copy()

    if "metric_value" in latest_drifted_view.columns:
        latest_drifted_view = latest_drifted_view.sort_values(
            "metric_value",
            ascending=False,
            na_position="last",
        )

    preferred_cols = choose_existing_columns(
        latest_drifted_view,
        [
            "computed_at",
            "feature_name",
            "metric_name",
            "metric_value",
            "threshold_value",
            "distance_to_threshold",
            "drift_severity",
            "drift_detected",
            "reference_window_start",
            "reference_window_end",
            "current_window_start",
            "current_window_end",
        ],
    )

    st.dataframe(
        latest_drifted_view[preferred_cols]
        if preferred_cols
        else latest_drifted_view,
        width="stretch",
    )


def _render_dataset_level_charts(
    *,
    dataset_level_df: pd.DataFrame,
) -> None:
    """
    Affiche les graphiques de drift au niveau dataset global.
    """
    share_plot_ready = pd.DataFrame()
    drifted_count_plot_ready = pd.DataFrame()

    if not dataset_level_df.empty:
        share_candidates = dataset_level_df.copy()

        if "metric_name" in share_candidates.columns:
            share_candidates = share_candidates[
                share_candidates["metric_name"]
                .astype(str)
                .isin(
                    [
                        "share_of_drifted_columns",
                        "dataset_drift_share",
                        "drift_share",
                    ]
                )
            ]

        if (
            not share_candidates.empty
            and "metric_value" in share_candidates.columns
        ):
            share_plot_ready = (
                share_candidates
                .dropna(subset=["computed_at", "metric_value"])
                .groupby("computed_at", as_index=True)["metric_value"]
                .mean()
                .to_frame("share_of_drifted_columns")
            )

        if "details" in dataset_level_df.columns:
            dataset_details_df = dataset_level_df.copy()
            dataset_details_df["number_of_drifted_columns"] = (
                dataset_details_df["details"].apply(_extract_drifted_columns_count)
            )

            drifted_count_plot_ready = (
                dataset_details_df
                .dropna(subset=["computed_at", "number_of_drifted_columns"])
                .groupby("computed_at", as_index=True)["number_of_drifted_columns"]
                .mean()
                .to_frame("number_of_drifted_columns")
            )

    g1, g2 = st.columns(2)

    with g1:
        st.markdown("##### Part des colonnes en dérive")
        if not share_plot_ready.empty:
            st.line_chart(share_plot_ready)
        else:
            st.info("Aucune métrique globale `share_of_drifted_columns` disponible.")

    with g2:
        st.markdown("##### Nombre de colonnes en dérive")
        if not drifted_count_plot_ready.empty:
            st.line_chart(drifted_count_plot_ready)
        else:
            st.info("Aucun nombre de colonnes en dérive disponible.")


def _render_per_feature_charts(
    *,
    per_feature_df: pd.DataFrame,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche les graphiques de drift au niveau des features.
    """
    if per_feature_df.empty:
        st.info(
            "Aucune métrique par feature disponible. "
            "Il faut que `EvidentlyService` persiste des lignes avec "
            "`feature_name != '__dataset__`."
        )
        return

    if "metric_name" in per_feature_df.columns:
        feature_metric_options = (
            per_feature_df["metric_name"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        feature_metric_options = sorted(feature_metric_options)

        selected_feature_metric = st.selectbox(
            "Métrique feature-level à visualiser",
            options=["Toutes"] + feature_metric_options,
            key="feature_level_drift_metric_selector",
        )

        if selected_feature_metric != "Toutes":
            per_feature_df = per_feature_df[
                per_feature_df["metric_name"].astype(str) == selected_feature_metric
            ]

    pf1, pf2 = st.columns(2)

    with pf1:
        st.markdown("##### Features les plus souvent en dérive")

        drift_only_features_df = (
            per_feature_df[per_feature_df["drift_detected_bool"]].copy()
            if "drift_detected_bool" in per_feature_df.columns
            else pd.DataFrame()
        )

        if (
            not drift_only_features_df.empty
            and "feature_name" in drift_only_features_df.columns
        ):
            top_drift_features = (
                drift_only_features_df["feature_name"]
                .fillna("unknown")
                .astype(str)
                .value_counts()
                .head(15)
            )

            st.bar_chart(top_drift_features)
        else:
            st.info("Aucune feature marquée en dérive pour l'instant.")

    with pf2:
        st.markdown("##### Top scores de drift dernière exécution")

        latest_feature_drift_df = _extract_latest_execution_df(
            drift_df=per_feature_df,
        )

        if (
            latest_feature_drift_df.empty
            or "metric_value" not in latest_feature_drift_df.columns
            or "feature_name" not in latest_feature_drift_df.columns
        ):
            st.info("Colonnes nécessaires absentes pour le top score par feature.")
        else:
            latest_feature_drift_df = (
                latest_feature_drift_df
                .dropna(subset=["metric_value"])
                .copy()
            )

            if latest_feature_drift_df.empty:
                st.info("Aucun score feature-level exploitable sur la dernière exécution.")
            else:
                latest_feature_drift_df["feature_name"] = (
                    latest_feature_drift_df["feature_name"]
                    .fillna("unknown")
                    .astype(str)
                )

                top_latest_feature_scores = (
                    latest_feature_drift_df
                    .groupby("feature_name", as_index=True)["metric_value"]
                    .mean()
                    .sort_values(ascending=False)
                    .head(15)
                    .to_frame("metric_value")
                )

                st.bar_chart(top_latest_feature_scores)

    st.markdown("##### Évolution moyenne des métriques de drift feature-level")

    if "computed_at" in per_feature_df.columns and "metric_value" in per_feature_df.columns:
        mean_drift_timeline_df = (
            per_feature_df
            .dropna(subset=["computed_at", "metric_value"])
            .groupby("computed_at", as_index=True)["metric_value"]
            .mean()
            .to_frame("mean_feature_drift_score")
        )

        if not mean_drift_timeline_df.empty:
            st.line_chart(mean_drift_timeline_df)
        else:
            st.info("Aucune évolution du score moyen disponible.")

    _render_feature_focus(
        per_feature_df=per_feature_df,
        choose_existing_columns=choose_existing_columns,
    )


def _render_feature_focus(
    *,
    per_feature_df: pd.DataFrame,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche une analyse détaillée d'une feature sélectionnée.
    """
    st.markdown("##### Analyse détaillée d'une feature")

    if "feature_name" not in per_feature_df.columns:
        st.info("Aucune colonne `feature_name` disponible.")
        return

    feature_list = sorted(
        per_feature_df["feature_name"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    if not feature_list:
        st.info("Aucune feature disponible pour le focus.")
        return

    selected_feature = st.selectbox(
        "Choisir une feature",
        options=feature_list,
        key="drift_feature_focus",
    )

    feature_df = per_feature_df[
        per_feature_df["feature_name"].astype(str) == selected_feature
    ].copy()

    if (
        feature_df.empty
        or "computed_at" not in feature_df.columns
        or "metric_value" not in feature_df.columns
    ):
        st.info("Aucune donnée temporelle exploitable pour cette feature.")
        return

    feature_df = feature_df.sort_values("computed_at")

    st.line_chart(
        feature_df.set_index("computed_at")[["metric_value"]]
    )

    preferred_cols = choose_existing_columns(
        feature_df,
        [
            "computed_at",
            "feature_name",
            "metric_name",
            "metric_value",
            "threshold_value",
            "distance_to_threshold",
            "drift_severity",
            "drift_detected",
        ],
    )

    st.dataframe(
        feature_df[preferred_cols] if preferred_cols else feature_df,
        width="stretch",
    )


def _render_drift_charts(
    *,
    drift_enriched_df: pd.DataFrame,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche toutes les visualisations liées au drift.
    """
    st.markdown("")
    st.markdown("#### Visualisations de drift")

    drift_graph_df = drift_enriched_df.copy()

    if "computed_at" in drift_graph_df.columns:
        drift_graph_df = drift_graph_df.dropna(subset=["computed_at"])

    if drift_graph_df.empty:
        st.info("Les métriques de drift ne contiennent pas de date exploitable.")
        return

    drift_graph_df = drift_graph_df.sort_values("computed_at")

    if "feature_name" not in drift_graph_df.columns:
        st.info("La colonne `feature_name` est absente des métriques de drift.")
        return

    dataset_level_df = drift_graph_df[
        drift_graph_df["feature_name"].astype(str).eq("__dataset__")
    ].copy()

    per_feature_df = drift_graph_df[
        ~drift_graph_df["feature_name"].astype(str).eq("__dataset__")
    ].copy()

    _render_dataset_level_charts(dataset_level_df=dataset_level_df)

    st.markdown("")

    _render_per_feature_charts(
        per_feature_df=per_feature_df,
        choose_existing_columns=choose_existing_columns,
    )


def _render_drift_detail_table(
    *,
    drift_view: pd.DataFrame,
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche le détail complet des métriques de drift.
    """
    with st.expander(
        "Voir le détail complet des métriques de drift",
        expanded=True,
    ):
        preferred_cols = choose_existing_columns(
            drift_view,
            [
                "computed_at",
                "model_name",
                "model_version",
                "feature_name",
                "metric_name",
                "metric_value",
                "threshold_value",
                "distance_to_threshold",
                "drift_severity",
                "drift_detected",
                "reference_window_start",
                "reference_window_end",
                "current_window_start",
                "current_window_end",
                "details",
            ],
        )

        st.dataframe(
            drift_view[preferred_cols] if preferred_cols else drift_view,
            width="stretch",
        )


# =============================================================================
# Bloc Evidently
# =============================================================================

def _render_evidently_raw_analysis(
    *,
    default_model_name: str | None,
    default_model_version: str | None,
    run_evidently_analysis: Callable[..., tuple[bool, Any]],
) -> None:
    """
    Affiche le bloc de lancement Evidently depuis les données brutes.
    """
    with st.expander(
        "Analyse Evidently depuis les données brutes applicatives",
        expanded=False,
    ):
        run_col1, run_col2 = st.columns([1, 1])

        with run_col1:
            evidently_max_rows = st.number_input(
                "Max rows cache",
                min_value=1000,
                max_value=100000,
                value=20000,
                step=1000,
                key="evidently_max_rows",
            )

        with run_col2:
            st.markdown(
                "<div style='height: 28px;'></div>",
                unsafe_allow_html=True,
            )

            run_evidently_clicked = st.button(
                "Analyser les données brutes",
                type="secondary",
                use_container_width=True,
                key="run_evidently_cache_button",
            )

        if not run_evidently_clicked:
            return

        if not default_model_name:
            st.error(
                "Impossible de lancer Evidently : aucun modèle actif ou enregistré n'a été trouvé."
            )
            return

        with st.spinner("Analyse Evidently sur données brutes en cours..."):
            ok, result = run_evidently_analysis(
                model_name=default_model_name,
                model_version=default_model_version,
                reference_kind="raw",
                current_kind="raw",
                max_rows=int(evidently_max_rows),
            )

        if ok and isinstance(result, dict) and result.get("success", False):
            st.success(
                result.get(
                    "message",
                    "Analyse Evidently sur données brutes exécutée avec succès.",
                )
            )

            with st.expander("Voir le résultat Evidently données brutes", expanded=False):
                st.json(result)

            st.cache_data.clear()
            st.rerun()
        else:
            detail = result.get("message") if isinstance(result, dict) else result
            st.error(f"Analyse Evidently données brutes non réussie : {detail}")


def _render_evidently_snapshot_analysis(
    *,
    feature_store_monitoring_df: pd.DataFrame,
    default_model_name: str | None,
    default_model_version: str | None,
    run_evidently_analysis_from_feature_store: Callable[..., tuple[bool, Any]],
) -> None:
    """
    Affiche le bloc de lancement Evidently depuis les snapshots de production.
    """
    with st.expander(
        "Analyse Evidently depuis les snapshots de production",
        expanded=True,
    ):
        snap_col1, snap_col2, snap_col3 = st.columns([1, 1, 1])

        available_sources = [
            "simulate_real_sample",
            "simulate_random",
            "api_request",
            "features_ready_cache",
        ]

        if (
            not feature_store_monitoring_df.empty
            and "source_table" in feature_store_monitoring_df.columns
        ):
            db_sources = (
                feature_store_monitoring_df["source_table"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )

            available_sources = sorted(set(available_sources + db_sources))

        with snap_col1:
            snapshot_source_table = st.selectbox(
                "Source snapshots",
                options=available_sources,
                index=(
                    available_sources.index("simulate_real_sample")
                    if "simulate_real_sample" in available_sources
                    else 0
                ),
                key="evidently_snapshot_source_table",
            )

        with snap_col2:
            snapshot_max_rows = st.number_input(
                "Max rows snapshots",
                min_value=100,
                max_value=100000,
                value=10000,
                step=100,
                key="evidently_snapshot_max_rows",
            )

        with snap_col3:
            st.markdown(
                "<div style='height: 28px;'></div>",
                unsafe_allow_html=True,
            )

            run_snapshot_evidently_clicked = st.button(
                "Analyser les snapshots bruts",
                type="primary",
                use_container_width=True,
                key="run_evidently_snapshot_button",
            )

        if not run_snapshot_evidently_clicked:
            return

        if not default_model_name:
            st.error(
                "Impossible de lancer Evidently : aucun modèle actif ou enregistré n'a été trouvé."
            )
            return

        with st.spinner("Analyse Evidently depuis les snapshots bruts en cours..."):
            ok, result = run_evidently_analysis_from_feature_store(
                model_name=default_model_name,
                model_version=default_model_version,
                source_table=snapshot_source_table,
                max_rows=min(int(snapshot_max_rows), 10000),
            )

        if ok and isinstance(result, dict) and result.get("success", False):
            st.success(
                result.get(
                    "message",
                    "Analyse Evidently snapshots bruts exécutée avec succès.",
                )
            )

            analyzed_columns = result.get("analyzed_columns")

            if isinstance(analyzed_columns, list):
                st.caption(f"Colonnes analysées : {len(analyzed_columns)}")

            st.caption(
                f"source_table={snapshot_source_table} | "
                f"reference_kind=raw | "
                f"current_kind=raw | "
                f"reference_rows={result.get('reference_rows')} | "
                f"current_rows={result.get('current_rows')}"
            )

            with st.expander("Voir le résultat Evidently snapshots bruts", expanded=False):
                st.json(result)

            st.cache_data.clear()
            st.rerun()
        else:
            if isinstance(result, dict):
                detail = (
                    result.get("message")
                    or result.get("detail")
                    or result.get("error")
                    or result
                )
            else:
                detail = result

            st.error(f"Analyse Evidently snapshots bruts non réussie : {detail}")


def _render_evidently_section(
    *,
    feature_store_monitoring_df: pd.DataFrame,
    default_model_name: str | None,
    default_model_version: str | None,
    run_evidently_analysis: Callable[..., tuple[bool, Any]],
    run_evidently_analysis_from_feature_store: Callable[..., tuple[bool, Any]],
) -> None:
    """
    Affiche toute la section d'exécution Evidently.
    """
    st.markdown("")
    st.markdown("#### Exécution Evidently")

    st.info(
        "Pour détecter la dérive créée par une simulation totalement aléatoire, "
        "utilise l'analyse depuis les snapshots avec source_table=`simulate_random`."
    )

    _render_evidently_raw_analysis(
        default_model_name=default_model_name,
        default_model_version=default_model_version,
        run_evidently_analysis=run_evidently_analysis,
    )

    _render_evidently_snapshot_analysis(
        feature_store_monitoring_df=feature_store_monitoring_df,
        default_model_name=default_model_name,
        default_model_version=default_model_version,
        run_evidently_analysis_from_feature_store=run_evidently_analysis_from_feature_store,
    )


# =============================================================================
# Fonction principale appelée par dashboard_monitoring.py
# =============================================================================

def render_drift_tab(
    *,
    drift_metrics_df: pd.DataFrame,
    feature_store_monitoring_df: pd.DataFrame,
    default_model_name: str | None,
    default_model_version: str | None,
    run_evidently_analysis: Callable[..., tuple[bool, Any]],
    run_evidently_analysis_from_feature_store: Callable[..., tuple[bool, Any]],
    render_section_title: Callable[[str, str], None],
    safe_bool: Callable[[Any], bool],
    choose_existing_columns: Callable[[pd.DataFrame, list[str]], list[str]],
) -> None:
    """
    Affiche l'onglet Dérive du dashboard monitoring.

    Parameters
    ----------
    drift_metrics_df : pd.DataFrame
        Métriques de drift récupérées depuis l'API.
    feature_store_monitoring_df : pd.DataFrame
        Snapshots de features disponibles pour les analyses Evidently.
    default_model_name : str | None
        Nom du modèle actif ou du modèle par défaut.
    default_model_version : str | None
        Version du modèle actif ou version par défaut.
    run_evidently_analysis : callable
        Fonction qui lance Evidently depuis les données brutes.
    run_evidently_analysis_from_feature_store : callable
        Fonction qui lance Evidently depuis le feature store.
    render_section_title : callable
        Helper UI provenant du fichier principal.
    safe_bool : callable
        Helper de conversion booléenne.
    choose_existing_columns : callable
        Helper pour afficher uniquement les colonnes disponibles.
    """
    render_section_title(
        "Dérive des données",
        "Détection des signaux de changement dans les features de production.",
    )

    if drift_metrics_df.empty:
        st.info("Aucune donnée disponible via `/monitoring/drift`.")
    else:
        drift_enriched_df = _prepare_enriched_drift_table(
            drift_df=drift_metrics_df,
            safe_bool=safe_bool,
        )
        
        drift_enriched_df = _normalize_drift_feature_column(drift_enriched_df)
        per_feature_all_df = drift_enriched_df.copy()

        if "feature_name" in per_feature_all_df.columns:
            per_feature_all_df = per_feature_all_df[
                ~per_feature_all_df["feature_name"]
                .fillna("")
                .astype(str)
                .eq("__dataset__")
            ].copy()

        _render_drift_kpis(
            drift_enriched_df=drift_enriched_df,
            per_feature_all_df=per_feature_all_df,
        )

        only_drift, selected_metric_name = _render_drift_filters(
            drift_enriched_df=drift_enriched_df,
        )

        drift_view = _filter_drift_view(
            drift_enriched_df=drift_enriched_df,
            only_drift=only_drift,
            selected_metric_name=selected_metric_name,
        )

        _render_drifted_columns(
            per_feature_all_df=per_feature_all_df,
        )

        _render_latest_drift_execution(
            per_feature_all_df=per_feature_all_df,
            choose_existing_columns=choose_existing_columns,
        )

        _render_drift_charts(
            drift_enriched_df=drift_enriched_df,
            choose_existing_columns=choose_existing_columns,
        )

        _render_drift_detail_table(
            drift_view=drift_view,
            choose_existing_columns=choose_existing_columns,
        )

    _render_evidently_section(
        feature_store_monitoring_df=feature_store_monitoring_df,
        default_model_name=default_model_name,
        default_model_version=default_model_version,
        run_evidently_analysis=run_evidently_analysis,
        run_evidently_analysis_from_feature_store=run_evidently_analysis_from_feature_store,
    )