"""
Page Streamlit : système et données.

Vue globale du système MLOps :
- état de santé de l'API
- présence de la clé API côté dashboard
- disponibilité des ressources métier
- exploration simple des données exposées par l'API

Cette page ne lit jamais PostgreSQL directement.
Toutes les données proviennent de l'API FastAPI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st


# =============================================================================
# Helpers UI
# =============================================================================

def _render_card(title: str, value: Any, subtitle: str = "") -> None:
    """
    Affiche une carte KPI visuelle.
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
    Affiche un titre de section.
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


def _status_badge(label: str, color: str) -> str:
    """
    Retourne un badge HTML simple.
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


# =============================================================================
# Helpers robustesse
# =============================================================================

def _safe_dict(value: Any) -> dict[str, Any]:
    """
    Retourne un dictionnaire vide si la valeur reçue n'est pas un dict.
    """
    return value if isinstance(value, dict) else {}


def _safe_dataframe(value: Any) -> pd.DataFrame:
    """
    Retourne une copie du DataFrame ou un DataFrame vide.
    """
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _safe_bool(value: Any) -> bool:
    """
    Convertit une valeur en booléen de façon robuste.
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


def _normalize_available_tables(
    *,
    available_tables: list[str],
    preview_map: dict[str, pd.DataFrame],
) -> list[str]:
    """
    Résout la liste des ressources explorables.
    """
    if isinstance(available_tables, list) and available_tables:
        return sorted(str(x) for x in available_tables)

    if isinstance(preview_map, dict) and preview_map:
        return sorted(str(x) for x in preview_map.keys())

    return []


def _compute_resource_status(
    *,
    tables_status_df: pd.DataFrame,
) -> tuple[int, int, str, str]:
    """
    Calcule la disponibilité des ressources du dashboard.
    """
    total_resources = len(tables_status_df)
    available_count = 0

    if not tables_status_df.empty and "is_available" in tables_status_df.columns:
        available_count = int(
            tables_status_df["is_available"].apply(_safe_bool).sum()
        )

    if total_resources <= 0:
        return total_resources, available_count, "Aucune ressource", "#6B7280"

    if available_count == total_resources:
        return total_resources, available_count, "Toutes disponibles", "#16A34A"

    if available_count == 0:
        return total_resources, available_count, "Aucune disponible", "#DC2626"

    return total_resources, available_count, "Disponibilité partielle", "#D97706"


def _format_status_value(health_data: dict[str, Any]) -> str:
    """
    Normalise le statut API affiché.
    """
    status = str(health_data.get("status", "unknown")).upper()
    return status if status else "UNKNOWN"


# =============================================================================
# Blocs d'affichage
# =============================================================================

def _render_system_overview(
    *,
    health_data: dict[str, Any],
    api_key: str | None,
    total_resources: int,
    available_count: int,
    resources_label: str,
    resources_color: str,
) -> None:
    """
    Affiche les KPI principaux du système.
    """
    status_upper = _format_status_value(health_data)
    api_key_present = bool(api_key and str(api_key).strip())
    api_key_status = "Configurée" if api_key_present else "Manquante"

    k1, k2, k3, k4 = st.columns(4)

    with k1:
        _render_card(
            "Health API",
            status_upper,
            "État remonté par /predict/health",
        )

    with k2:
        _render_card(
            "Clé API",
            api_key_status,
            "Configuration dashboard",
        )

    with k3:
        _render_card(
            "Ressources actives",
            f"{available_count}/{total_resources}",
            resources_label,
        )

    with k4:
        _render_card(
            "Heure de rendu",
            datetime.now().strftime("%H:%M:%S"),
            "Instant du diagnostic",
        )

    badge_color = "#16A34A"
    badge_label = "Système opérationnel"

    if status_upper != "OK":
        badge_color = "#DC2626"
        badge_label = "Healthcheck en anomalie"
    elif total_resources > 0 and available_count < total_resources:
        badge_color = "#D97706"
        badge_label = "Système partiellement disponible"

    st.markdown(
        f"""
        <div style="
            padding: 18px;
            border-radius: 18px;
            background: #F8FAFC;
            border: 1px solid #E5E7EB;
            margin-top: 18px;
            margin-bottom: 12px;
        ">
            <div style="font-size: 1.05rem; font-weight: 700; margin-bottom: 8px;">
                Statut global
            </div>
            <div style="margin-bottom: 10px;">
                {_status_badge(badge_label, badge_color)}
            </div>
            <div style="margin-bottom: 8px;">
                {_status_badge(resources_label, resources_color)}
            </div>
            <div style="color: #4B5563; font-size: 0.92rem;">
                Healthcheck API : {status_upper}<br>
                Clé API dashboard : {api_key_status}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_resources_table(
    *,
    tables_status_df: pd.DataFrame,
    total_resources: int,
    available_count: int,
) -> None:
    """
    Affiche la table de disponibilité des ressources.
    """
    _render_section_title(
        "Ressources du système",
        "Disponibilité logique des ressources exploitées par le dashboard.",
    )

    if tables_status_df.empty:
        st.info("Aucune information de statut disponible.")
        return

    display_df = tables_status_df.copy()

    if "is_available" in display_df.columns:
        display_df["is_available"] = display_df["is_available"].apply(
            lambda x: "✅ Oui" if _safe_bool(x) else "❌ Non"
        )

    preferred_cols = [
        col
        for col in ["resource_name", "is_available", "row_count", "comment"]
        if col in display_df.columns
    ]

    st.dataframe(
        display_df[preferred_cols] if preferred_cols else display_df,
        width="stretch",
    )

    if total_resources > 0 and available_count == total_resources:
        st.success(
            f"Toutes les ressources visibles sont disponibles "
            f"({available_count}/{total_resources})."
        )
    elif total_resources > 0:
        st.warning(
            f"Système partiellement disponible : "
            f"{available_count}/{total_resources} ressources actives."
        )


def _render_data_explorer(
    *,
    selected_table_preview_map: dict[str, pd.DataFrame],
    available_tables: list[str],
) -> None:
    """
    Affiche l'explorateur simple de données exposées par l'API.
    """
    _render_section_title(
        "Inspection des données",
        "Explorer les ressources exposées par l'API sans accès direct à PostgreSQL.",
    )

    if not available_tables:
        st.info("Aucune ressource explorable disponible.")
        return

    selected_table = st.selectbox(
        "Choisir une ressource",
        options=available_tables,
    )

    preview_df = _safe_dataframe(
        selected_table_preview_map.get(selected_table, pd.DataFrame())
    )

    if preview_df.empty:
        st.warning(f"Aucune donnée disponible pour `{selected_table}`.")
        return

    st.success(f"Ressource `{selected_table}` chargée avec succès.")

    m1, m2, m3 = st.columns(3)

    with m1:
        st.metric("Lignes aperçu", len(preview_df))

    with m2:
        st.metric("Colonnes", len(preview_df.columns))

    with m3:
        memory_kb = preview_df.memory_usage(deep=True).sum() / 1024
        st.metric("Mémoire approx.", f"{memory_kb:.1f} Ko")

    with st.expander("Colonnes disponibles", expanded=False):
        st.write(list(preview_df.columns))

    st.dataframe(preview_df, width="stretch")

    csv = preview_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        "Télécharger l'aperçu CSV",
        csv,
        file_name=f"{selected_table}_preview.csv",
        mime="text/csv",
        use_container_width=True,
    )


# =============================================================================
# Page principale
# =============================================================================

def render_systeme_page(
    *,
    health_data: dict,
    tables_status_df: pd.DataFrame,
    selected_table_preview_map: dict[str, pd.DataFrame],
    available_tables: list[str],
    api_key: str | None = None,
) -> None:
    """
    Affiche la page système et données.
    """
    health_data = _safe_dict(health_data)
    tables_status_df = _safe_dataframe(tables_status_df)

    selected_table_preview_map = (
        selected_table_preview_map
        if isinstance(selected_table_preview_map, dict)
        else {}
    )

    available_tables = _normalize_available_tables(
        available_tables=available_tables if isinstance(available_tables, list) else [],
        preview_map=selected_table_preview_map,
    )

    total_resources, available_count, resources_label, resources_color = (
        _compute_resource_status(tables_status_df=tables_status_df)
    )

    st.markdown("## Système et données")
    st.caption(
        "Vue d'ensemble de l'API, des ressources exposées et des jeux de données consultables."
    )

    tab1, tab2 = st.tabs(["Système", "Données"])

    with tab1:
        _render_section_title(
            "État du système",
            "Diagnostic rapide de l'API et des ressources visibles par le dashboard.",
        )

        _render_system_overview(
            health_data=health_data,
            api_key=api_key,
            total_resources=total_resources,
            available_count=available_count,
            resources_label=resources_label,
            resources_color=resources_color,
        )

        if st.button("Rafraîchir et revérifier", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        with st.expander("Réponse healthcheck brute", expanded=False):
            if health_data:
                st.json(health_data)
            else:
                st.info("Aucune réponse healthcheck disponible.")

        _render_resources_table(
            tables_status_df=tables_status_df,
            total_resources=total_resources,
            available_count=available_count,
        )

    with tab2:
        _render_data_explorer(
            selected_table_preview_map=selected_table_preview_map,
            available_tables=available_tables,
        )