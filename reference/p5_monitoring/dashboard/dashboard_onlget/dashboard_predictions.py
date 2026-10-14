"""
Page Streamlit : prédictions, simulations et traçabilité.

Cette page regroupe :
- la prédiction via un client existant
- le lancement de simulations de prédictions
- l'historique des prédictions
- la consultation du détail d'une requête
- la consultation de la vérité terrain associée
- la consultation du snapshot exact des features utilisées

Notes
-----
Cette page ne lit pas directement PostgreSQL.
Toutes les données sont récupérées via l'API FastAPI.
"""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
import streamlit as st


# =============================================================================
# Helpers UI
# =============================================================================

def _render_card(title: str, value: Any, subtitle: str = "") -> None:
    """
    Affiche une carte KPI légère.
    """
    st.markdown(
        f"""
        <div style="
            padding: 16px 18px;
            border-radius: 16px;
            background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 6px 18px rgba(0,0,0,0.08);
            min-height: 115px;
        ">
            <div style="font-size: 0.9rem; color: #9CA3AF; margin-bottom: 8px;">
                {title}
            </div>
            <div style="font-size: 1.8rem; font-weight: 700; color: white; margin-bottom: 8px;">
                {value}
            </div>
            <div style="font-size: 0.85rem; color: #D1D5DB;">
                {subtitle}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_section_title(title: str, subtitle: str = "") -> None:
    """
    Affiche un titre de section clair pour structurer la page.
    """
    st.markdown(
        f"""
        <div style="margin-top: 8px; margin-bottom: 14px;">
            <div style="font-size: 1.2rem; font-weight: 700; margin-bottom: 4px;">
                {title}
            </div>
            <div style="color: #6B7280; font-size: 0.95rem;">
                {subtitle}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_prediction_result_card(result: dict[str, Any]) -> None:
    """
    Affiche un résultat de prédiction unitaire.
    """
    if not isinstance(result, dict) or not result:
        st.info("Aucun résultat de prédiction à afficher.")
        return

    prediction = result.get("prediction")
    score = result.get("score")
    model_version = result.get("model_version", "unknown")
    latency_ms = result.get("latency_ms")
    request_id = result.get("request_id", "-")

    if prediction == 0:
        decision_label = "Crédit accepté"
        decision_color = "#10B981"
    elif prediction == 1:
        decision_label = "Crédit refusé"
        decision_color = "#EF4444"
    else:
        decision_label = "Décision inconnue"
        decision_color = "#6B7280"

    score_display = f"{_safe_float(score):.4f}" if score is not None else "N/A"
    latency_display = f"{_safe_float(latency_ms):.2f} ms" if latency_ms is not None else "N/A"

    st.markdown(
        f"""
        <div style="
            padding: 20px 22px;
            border-radius: 18px;
            background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 8px 22px rgba(0,0,0,0.10);
            margin-bottom: 14px;
        ">
            <div style="font-size: 0.95rem; color: #9CA3AF; margin-bottom: 8px;">
                Résultat de la prédiction
            </div>
            <div style="
                font-size: 1.6rem;
                font-weight: 800;
                color: {decision_color};
                margin-bottom: 12px;
            ">
                {decision_label}
            </div>
            <div style="color: white; font-size: 0.95rem; line-height: 1.8;">
                <strong>Score :</strong> {score_display}<br>
                <strong>Version modèle :</strong> {model_version}<br>
                <strong>Latence :</strong> {latency_display}<br>
                <strong>Request ID :</strong> {request_id}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# Helpers robustesse
# =============================================================================

def _safe_dataframe(value: Any) -> pd.DataFrame:
    """
    Garantit le retour d'un DataFrame.
    """
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _safe_float(value: Any, default: float = 0.0) -> float:
    """
    Convertit une valeur en float.
    """
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _coerce_columns_to_datetime(
    df: pd.DataFrame,
    columns: Iterable[str],
) -> pd.DataFrame:
    """
    Convertit plusieurs colonnes en datetime si elles existent.
    """
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
    """
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _choose_existing_columns(
    df: pd.DataFrame,
    preferred_cols: list[str],
) -> list[str]:
    """
    Retourne uniquement les colonnes présentes dans le DataFrame.
    """
    return [col for col in preferred_cols if col in df.columns]


def _normalize_result(result: Any) -> dict[str, Any]:
    """
    Normalise une réponse API pour éviter les erreurs d'affichage.
    """
    if isinstance(result, dict):
        return result
    return {"result": result}


def _extract_items_dataframe(result: Any) -> pd.DataFrame:
    """
    Extrait une liste d'items depuis une réponse batch ou simulation.
    """
    if isinstance(result, dict):
        items = result.get("items")
        if isinstance(items, list):
            return pd.DataFrame(items)

        data = result.get("data")
        if isinstance(data, list):
            return pd.DataFrame(data)

        results = result.get("results")
        if isinstance(results, list):
            return pd.DataFrame(results)

    return pd.DataFrame()


def _snapshot_to_dataframe(snapshot: Any) -> pd.DataFrame:
    """
    Convertit un snapshot de features en DataFrame.
    """
    if isinstance(snapshot, pd.DataFrame):
        return snapshot.copy()

    if isinstance(snapshot, list):
        return pd.DataFrame(snapshot)

    if not isinstance(snapshot, dict):
        return pd.DataFrame()

    for key in ["features", "items", "data"]:
        value = snapshot.get(key)

        if isinstance(value, list):
            return pd.DataFrame(value)

        if isinstance(value, dict):
            return pd.DataFrame([value])

    return pd.DataFrame()


# =============================================================================
# Préparation des données
# =============================================================================

def _prepare_prediction_logs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prépare l'historique des prédictions pour l'affichage.
    """
    df = _safe_dataframe(df)

    df = _coerce_columns_to_datetime(
        df,
        ["prediction_timestamp", "created_at"],
    )
    df = _coerce_columns_to_numeric(
        df,
        [
            "client_id",
            "prediction",
            "score",
            "threshold",
            "threshold_used",
            "latency_ms",
            "inference_latency_ms",
        ],
    )

    if "prediction_timestamp" in df.columns:
        df = df.sort_values("prediction_timestamp", ascending=False, na_position="last")
    elif "created_at" in df.columns:
        df = df.sort_values("created_at", ascending=False, na_position="last")

    return df


def _prepare_ground_truth(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prépare les vérités terrain pour l'affichage.
    """
    df = _safe_dataframe(df)

    df = _coerce_columns_to_datetime(
        df,
        ["observed_at", "created_at", "gt_created_at"],
    )
    df = _coerce_columns_to_numeric(
        df,
        ["client_id", "true_label", "ground_truth", "y_true"],
    )

    return df


# =============================================================================
# Blocs d'affichage
# =============================================================================

def _render_global_kpis(
    *,
    prediction_logs_df: pd.DataFrame,
    metric_safe_number,
) -> None:
    """
    Affiche les KPI globaux de la page prédictions.
    """
    total_predictions = len(prediction_logs_df)
    mean_latency = metric_safe_number(prediction_logs_df, "latency_ms", "mean", 0)
    p95_latency = metric_safe_number(prediction_logs_df, "latency_ms", "p95", 0)
    p99_latency = metric_safe_number(prediction_logs_df, "latency_ms", "p99", 0)

    error_rate = 0.0

    if not prediction_logs_df.empty:
        if "error_message" in prediction_logs_df.columns:
            error_rate = float(prediction_logs_df["error_message"].notna().mean() * 100)
        elif "status" in prediction_logs_df.columns:
            error_rate = float(
                prediction_logs_df["status"]
                .fillna("")
                .astype(str)
                .str.lower()
                .eq("error")
                .mean()
                * 100
            )

    k1, k2, k3, k4, k5 = st.columns(5)

    with k1:
        _render_card("Total prédictions", total_predictions, "Historique chargé")

    with k2:
        _render_card(
            "Latence moyenne",
            f"{_safe_float(mean_latency):.2f} ms",
            "Temps moyen total",
        )

    with k3:
        _render_card(
            "Latence p95",
            f"{_safe_float(p95_latency):.2f} ms",
            "Queue de distribution",
        )

    with k4:
        _render_card(
            "Latence p99",
            f"{_safe_float(p99_latency):.2f} ms",
            "Cas extrêmes",
        )

    with k5:
        _render_card("Erreur", f"{error_rate:.1f} %", "Taux basé sur les logs")

    if not prediction_logs_df.empty and "score" in prediction_logs_df.columns:
        avg_score = metric_safe_number(prediction_logs_df, "score", "mean", None)
        if avg_score is not None:
            st.caption(f"Score moyen observé : {_safe_float(avg_score):.4f}")


def _render_prediction_tab(
    *,
    prediction_logs_df: pd.DataFrame,
    call_predict_client_api,
) -> None:
    """
    Onglet : prédiction unitaire depuis un client existant.
    """
    _render_section_title(
        "Lancer une prédiction",
        "Tester le modèle avec un client existant dans les données de l'API.",
    )

    client_id = st.number_input(
        "SK_ID_CURR",
        min_value=100000,
        max_value=999999,
        value=100001,
        step=1,
        key="predict_client_id",
    )

    if st.button(
        "Lancer la prédiction du client",
        key="predict_single_client",
        use_container_width=True,
    ):
        try:
            ok, result = call_predict_client_api(int(client_id))
            normalized = _normalize_result(result)

            st.session_state["last_prediction_result"] = normalized

            if ok:
                st.success("Prédiction reçue.")
            else:
                st.error(normalized.get("detail", "Erreur API."))

        except Exception as exc:
            st.session_state["last_prediction_result"] = {"error": str(exc)}
            st.error(f"Erreur lors de l'appel API : {exc}")

    result = st.session_state.get("last_prediction_result")

    if isinstance(result, dict) and result:
        st.markdown("#### Dernier résultat")

        if "error" in result or "detail" in result:
            st.error("La prédiction n'a pas abouti.")
            st.json(result)
        else:
            _render_prediction_result_card(result)

            c1, c2, c3, c4 = st.columns(4)

            decision = result.get("prediction")

            with c1:
                _render_card(
                    "Décision",
                    "Accepté" if decision == 0 else "Refusé" if decision == 1 else "Inconnu",
                    "Décision calculée",
                )

            with c2:
                _render_card(
                    "Score",
                    f"{_safe_float(result.get('score')):.4f}",
                    "Probabilité de défaut",
                )

            with c3:
                _render_card(
                    "Version modèle",
                    result.get("model_version", "unknown"),
                    "Version utilisée",
                )

            with c4:
                _render_card(
                    "Latence",
                    f"{_safe_float(result.get('latency_ms')):.2f} ms",
                    "Temps API",
                )

            with st.expander("Voir la réponse JSON complète", expanded=False):
                st.json(result)

    st.markdown("")
    _render_section_title(
        "Historique des prédictions",
        "Vue séparée des crédits acceptés et refusés.",
    )

    if prediction_logs_df.empty:
        st.info("Aucune donnée d'historique disponible.")
        return

    if "prediction" not in prediction_logs_df.columns:
        st.warning("La colonne `prediction` est absente de l'historique.")
        st.dataframe(prediction_logs_df, width="stretch")
        return

    history_view = prediction_logs_df.copy()

    accepted_df = history_view[history_view["prediction"] == 0].copy()
    refused_df = history_view[history_view["prediction"] == 1].copy()

    if not accepted_df.empty:
        accepted_df["decision_credit"] = "Accepté"

    if not refused_df.empty:
        refused_df["decision_credit"] = "Refusé"

    preferred_cols = [
        "prediction_timestamp",
        "created_at",
        "request_id",
        "client_id",
        "model_name",
        "model_version",
        "decision_credit",
        "prediction",
        "score",
        "threshold",
        "threshold_used",
        "latency_ms",
        "inference_latency_ms",
        "status",
        "error_message",
    ]

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("### Demandes acceptées")
        st.caption(f"{len(accepted_df)} demande(s) acceptée(s)")

        if accepted_df.empty:
            st.info("Aucune demande acceptée.")
        else:
            cols = _choose_existing_columns(accepted_df, preferred_cols)
            st.dataframe(accepted_df[cols] if cols else accepted_df, width="stretch")

    with c2:
        st.markdown("### Demandes refusées")
        st.caption(f"{len(refused_df)} demande(s) refusée(s)")

        if refused_df.empty:
            st.info("Aucune demande refusée.")
        else:
            cols = _choose_existing_columns(refused_df, preferred_cols)
            st.dataframe(refused_df[cols] if cols else refused_df, width="stretch")


def _render_simulations_tab(
    *,
    call_predict_real_random_batch_api,
    call_predict_fully_random_batch_api,
) -> None:
    """
    Onglet : simulations de prédictions.
    """
    _render_section_title(
        "Simulations",
        "Lancer des lots de prédictions pour alimenter les logs et le monitoring.",
    )

    subtabs = st.tabs(
        [
            "Simulation réelle aléatoire",
            "Simulation totalement aléatoire",
        ]
    )

    with subtabs[0]:
        st.info(
            "Cette simulation utilise des clients réels tirés aléatoirement "
            "dans les données disponibles."
        )

        real_count = st.number_input(
            "Nombre de prédictions réelles aléatoires",
            min_value=1,
            max_value=500,
            value=200,
            step=1,
            key="real_random_batch_count",
        )

        use_seed = st.checkbox(
            "Utiliser une graine aléatoire",
            value=False,
            key="use_real_random_seed",
        )

        seed = st.number_input(
            "Graine aléatoire",
            min_value=0,
            value=0,
            step=1,
            key="real_random_batch_seed",
            disabled=not use_seed,
        )

        if call_predict_real_random_batch_api is None:
            st.warning("La route de simulation réelle n'est pas branchée.")
        elif st.button(
            "Lancer la simulation réelle",
            key="launch_real_random_batch",
            use_container_width=True,
        ):
            try:
                ok, result = call_predict_real_random_batch_api(
                    batch_size=int(real_count),
                    random_seed=int(seed) if use_seed else None,
                )

                st.session_state["last_simulation_result"] = _normalize_result(result)

                if ok:
                    st.success("Simulation réelle terminée.")
                else:
                    st.error("Erreur lors de la simulation réelle.")

            except Exception as exc:
                st.session_state["last_simulation_result"] = {"error": str(exc)}
                st.error(f"Erreur lors de la simulation : {exc}")

    with subtabs[1]:
        st.info(
            "Cette simulation génère des données entièrement aléatoires. "
            "Elle est utile pour tester la robustesse et créer du drift."
        )

        random_count = st.number_input(
            "Nombre de prédictions totalement aléatoires",
            min_value=1,
            max_value=500,
            value=200,
            step=1,
            key="fully_random_batch_count",
        )

        if call_predict_fully_random_batch_api is None:
            st.warning("La route de simulation aléatoire n'est pas branchée.")
        elif st.button(
            "Lancer la simulation totalement aléatoire",
            key="launch_fully_random_batch",
            use_container_width=True,
        ):
            try:
                ok, result = call_predict_fully_random_batch_api(
                    batch_size=int(random_count)
                )

                st.session_state["last_simulation_result"] = _normalize_result(result)

                if ok:
                    st.success("Simulation totalement aléatoire terminée.")
                else:
                    st.error("Erreur lors de la simulation aléatoire.")

            except Exception as exc:
                st.session_state["last_simulation_result"] = {"error": str(exc)}
                st.error(f"Erreur lors de la simulation : {exc}")

    result = st.session_state.get("last_simulation_result")

    st.markdown("#### Dernière simulation")

    if not result:
        st.info("Aucune simulation lancée pendant cette session.")
        return

    st.json(result)

    result_df = _extract_items_dataframe(result)

    if not result_df.empty:
        st.dataframe(result_df, width="stretch")


def _render_stored_data_tab(
    *,
    prediction_logs_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
    get_prediction_detail,
    get_ground_truth_by_request_id,
) -> None:
    """
    Onglet : détail d'une requête et vérité terrain associée.
    """
    _render_section_title(
        "Données stockées",
        "Inspecter le détail d'une requête et les données associées.",
    )

    if prediction_logs_df.empty:
        st.info("Aucune donnée disponible.")
        return

    if "request_id" not in prediction_logs_df.columns:
        st.warning("La colonne `request_id` est absente de l'historique.")
        return

    request_ids = (
        prediction_logs_df["request_id"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    if not request_ids:
        st.info("Aucun request_id disponible.")
        return

    selected = st.selectbox(
        "Choisir une requête",
        request_ids,
        key="stored_data_request_id",
    )

    st.markdown("#### Détail de la prédiction")

    try:
        detail = get_prediction_detail(selected)
    except Exception as exc:
        detail = None
        st.error(f"Erreur lors de la récupération du détail : {exc}")

    if isinstance(detail, dict) and detail:
        st.dataframe(pd.DataFrame([detail]), width="stretch")
    elif isinstance(detail, pd.DataFrame) and not detail.empty:
        st.dataframe(detail, width="stretch")
    else:
        st.info("Aucun détail disponible pour cette requête.")

    st.markdown("#### Vérité terrain associée")

    try:
        gt = get_ground_truth_by_request_id(
            selected,
            ground_truth_df=ground_truth_df,
            prediction_logs_df=prediction_logs_df,
        )
    except Exception as exc:
        gt = None
        st.error(f"Erreur lors de la récupération de la vérité terrain : {exc}")

    if isinstance(gt, pd.DataFrame) and not gt.empty:
        preferred_cols = _choose_existing_columns(
            gt,
            [
                "request_id",
                "client_id",
                "true_label",
                "ground_truth",
                "y_true",
                "label_source",
                "observed_at",
                "created_at",
                "notes",
            ],
        )
        st.dataframe(gt[preferred_cols] if preferred_cols else gt, width="stretch")
    elif isinstance(gt, dict) and gt:
        st.dataframe(pd.DataFrame([gt]), width="stretch")
    else:
        st.info("Aucune vérité terrain trouvée pour cette requête.")


def _render_snapshot_tab(
    *,
    prediction_logs_df: pd.DataFrame,
    get_prediction_features_snapshot,
) -> None:
    """
    Onglet : snapshot exact des features utilisées.
    """
    _render_section_title(
        "Snapshot exact des features",
        "Retrouver les variables réellement utilisées pour une prédiction donnée.",
    )

    if prediction_logs_df.empty:
        st.info("Aucune donnée disponible.")
        return

    if "request_id" not in prediction_logs_df.columns:
        st.warning("La colonne `request_id` est absente de l'historique.")
        return

    request_ids = (
        prediction_logs_df["request_id"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    if not request_ids:
        st.info("Aucun request_id disponible.")
        return

    selected = st.selectbox(
        "Choisir une requête snapshot",
        request_ids,
        key="snapshot_request_id",
    )

    try:
        snapshot = get_prediction_features_snapshot(selected)
    except Exception as exc:
        snapshot = None
        st.error(f"Erreur lors de la récupération du snapshot : {exc}")

    df_snapshot = _snapshot_to_dataframe(snapshot)

    if df_snapshot.empty:
        st.info("Aucun snapshot disponible.")
        return

    info1, info2, info3 = st.columns([1, 2, 1])

    with info1:
        st.metric("Lignes snapshot", len(df_snapshot))

    with info2:
        st.caption(f"request_id : {selected}")

    with info3:
        if "feature_name" in df_snapshot.columns:
            st.metric("Features distinctes", df_snapshot["feature_name"].nunique())

    preferred_cols = _choose_existing_columns(
        df_snapshot,
        [
            "request_id",
            "client_id",
            "model_name",
            "model_version",
            "feature_name",
            "feature_value",
            "feature_type",
            "snapshot_timestamp",
        ],
    )

    st.dataframe(
        df_snapshot[preferred_cols] if preferred_cols else df_snapshot,
        width="stretch",
    )

    csv = df_snapshot.to_csv(index=False).encode("utf-8")

    st.download_button(
        "Télécharger le snapshot CSV",
        csv,
        file_name=f"snapshot_{selected}.csv",
        mime="text/csv",
        use_container_width=True,
    )


# =============================================================================
# Page principale
# =============================================================================

def render_predictions_page(
    *,
    prediction_logs_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
    call_predict_client_api,
    call_predict_api,
    call_predict_batch_api,
    get_prediction_detail,
    get_prediction_features_snapshot,
    get_ground_truth_by_request_id,
    metric_safe_number,
    call_predict_real_random_batch_api=None,
    call_predict_fully_random_batch_api=None,
) -> None:
    """
    Affiche la page prédictions, simulations et traçabilité.

    Les paramètres call_predict_api et call_predict_batch_api sont conservés
    dans la signature pour ne pas casser dashboard_main.py, même si cette page
    optimisée ne les expose plus dans l'interface.
    """
    prediction_logs_df = _prepare_prediction_logs(prediction_logs_df)
    ground_truth_df = _prepare_ground_truth(ground_truth_df)

    st.session_state.setdefault("last_prediction_result", None)
    st.session_state.setdefault("last_simulation_result", None)

    st.markdown("## Prédictions et traçabilité")
    st.caption(
        "Lancer des prédictions, simuler des batches, consulter l'historique "
        "et inspecter les données stockées."
    )

    _render_global_kpis(
        prediction_logs_df=prediction_logs_df,
        metric_safe_number=metric_safe_number,
    )

    tabs = st.tabs(
        [
            "Prédiction",
            "Simulations",
            "Données stockées",
            "Snapshot des features",
        ]
    )

    with tabs[0]:
        _render_prediction_tab(
            prediction_logs_df=prediction_logs_df,
            call_predict_client_api=call_predict_client_api,
        )

    with tabs[1]:
        _render_simulations_tab(
            call_predict_real_random_batch_api=call_predict_real_random_batch_api,
            call_predict_fully_random_batch_api=call_predict_fully_random_batch_api,
        )

    with tabs[2]:
        _render_stored_data_tab(
            prediction_logs_df=prediction_logs_df,
            ground_truth_df=ground_truth_df,
            get_prediction_detail=get_prediction_detail,
            get_ground_truth_by_request_id=get_ground_truth_by_request_id,
        )

    with tabs[3]:
        _render_snapshot_tab(
            prediction_logs_df=prediction_logs_df,
            get_prediction_features_snapshot=get_prediction_features_snapshot,
        )