"""
Requêtes HTTP centralisées pour le dashboard Streamlit.

Ce module contient tous les appels à l'API FastAPI utilisés par le dashboard.

Objectif
--------
- éviter les appels `requests` dispersés dans les pages Streamlit
- convertir les réponses API en DataFrame pandas
- normaliser les dates, nombres et formats de réponse
- garder un dashboard découplé de PostgreSQL

Principe
--------
Dashboard Streamlit -> dashboard_request.py -> API FastAPI -> PostgreSQL / services

Le dashboard ne lit jamais directement la base.
"""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
import requests


# =============================================================================
# Timeouts HTTP
# =============================================================================

DEFAULT_TIMEOUT = 30
DEFAULT_BATCH_TIMEOUT = 60
DEFAULT_SIMULATION_TIMEOUT = 120
DEFAULT_ANALYSIS_TIMEOUT = 300


# =============================================================================
# Helpers HTTP génériques
# =============================================================================

def build_headers(api_key: str | None = None) -> dict[str, str]:
    """
    Construit les headers envoyés à l'API.

    Notes
    -----
    Si une clé API existe, elle est transmise dans `X-API-Key`.
    """
    headers = {"Content-Type": "application/json"}

    if api_key:
        headers["X-API-Key"] = api_key

    return headers


def _normalize_base_url(base_url: str | None) -> str:
    """
    Nettoie l'URL de base pour éviter les doubles slash.
    """
    return str(base_url).strip().rstrip("/") if base_url else ""


def _build_url(base_url: str, endpoint: str) -> str:
    """
    Assemble l'URL finale.
    """
    return f"{base_url}/{endpoint.lstrip('/')}"


def _safe_json_response(response: requests.Response) -> Any:
    """
    Convertit une réponse HTTP en JSON si possible.

    Si l'API retourne du texte non JSON, on garde ce texte dans `detail`.
    """
    try:
        return response.json()
    except ValueError:
        text = response.text.strip()
        return {"detail": text or "Réponse sans JSON."}


def call_api(
    endpoint: str,
    *,
    base_url: str,
    api_key: str | None = None,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    json_data: Any | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Appelle un endpoint FastAPI.

    Returns
    -------
    tuple[bool, Any]
        - True + payload JSON si succès HTTP
        - False + détail erreur si échec
    """
    normalized_base_url = _normalize_base_url(base_url)

    if not normalized_base_url:
        return False, {"detail": "API_URL non configurée côté dashboard."}

    url = _build_url(normalized_base_url, endpoint)
    response: requests.Response | None = None

    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=build_headers(api_key),
            params=params,
            json=json_data,
            timeout=timeout,
        )
        response.raise_for_status()
        return True, _safe_json_response(response)

    except requests.Timeout:
        return False, {
            "detail": f"Timeout HTTP sur {method.upper()} {url} après {timeout}s."
        }

    except requests.RequestException as exc:
        if response is not None:
            return False, _safe_json_response(response)

        return False, {"detail": str(exc)}


# =============================================================================
# Helpers de conversion API -> DataFrame
# =============================================================================

def _extract_items_list(payload: Any) -> list[dict[str, Any]]:
    """
    Extrait une liste d'objets depuis une réponse API.

    Formats acceptés :
    - [{...}, {...}]
    - {"items": [{...}]}
    - {"data": [{...}]}
    - {"results": [{...}]}
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ["items", "data", "results"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def _extract_single_dict(payload: Any) -> dict[str, Any] | None:
    """
    Extrait un seul dictionnaire depuis une réponse API.

    Formats acceptés :
    - {...}
    - {"item": {...}}
    - {"data": {...}}
    - {"model": {...}}
    - {"result": {...}}
    - {"items": [{...}]}
    """
    if not isinstance(payload, dict):
        return None

    for key in ["item", "data", "model", "result"]:
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    items = payload.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]

    return payload


def _is_error_payload(payload: Any) -> bool:
    """
    Détecte une réponse d'erreur simple.
    """
    return isinstance(payload, dict) and "detail" in payload and len(payload) <= 3


def items_payload_to_dataframe(payload: Any) -> pd.DataFrame:
    """
    Convertit une réponse contenant plusieurs items en DataFrame.
    """
    if isinstance(payload, pd.DataFrame):
        return payload.copy()

    items = _extract_items_list(payload)
    return pd.DataFrame(items) if items else pd.DataFrame()


def dict_payload_to_dataframe(payload: Any) -> pd.DataFrame:
    """
    Convertit une réponse contenant un seul objet en DataFrame à une ligne.
    """
    if isinstance(payload, pd.DataFrame):
        return payload.copy()

    row = _extract_single_dict(payload)

    if not isinstance(row, dict) or not row or _is_error_payload(row):
        return pd.DataFrame()

    return pd.DataFrame([row])


def dataframe_to_payload(df: pd.DataFrame) -> dict[str, Any]:
    """
    Convertit une ligne de DataFrame en payload de prédiction.

    La colonne `SK_ID_CURR`, si présente, devient un champ séparé.
    Les autres colonnes deviennent les features.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}

    row = df.iloc[0].to_dict()
    client_id = row.pop("SK_ID_CURR", None)

    payload: dict[str, Any] = {"features": row}

    if client_id is not None and not pd.isna(client_id):
        try:
            payload["SK_ID_CURR"] = int(client_id)
        except Exception:
            payload["SK_ID_CURR"] = client_id

    return payload


# =============================================================================
# Nettoyage DataFrame
# =============================================================================

def _coerce_datetime_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """
    Convertit plusieurs colonnes en datetime si elles existent.
    """
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    out = df.copy()

    for col in columns:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")

    return out


def _coerce_numeric_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """
    Convertit plusieurs colonnes en numérique si elles existent.
    """
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    out = df.copy()

    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    return out


def _sort_by_first_existing_date(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """
    Trie un DataFrame sur la première colonne date disponible.
    """
    out = df.copy()

    for col in columns:
        if col in out.columns:
            return out.sort_values(col, ascending=False, na_position="last")

    return out


# =============================================================================
# Post-traitements métier
# =============================================================================

def _postprocess_prediction_logs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie les logs de prédiction.
    """
    df = _coerce_datetime_columns(df, ["prediction_timestamp", "created_at"])
    df = _coerce_numeric_columns(
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
    return _sort_by_first_existing_date(df, ["prediction_timestamp", "created_at"])


def _postprocess_ground_truth(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie les vérités terrain.
    """
    df = _coerce_datetime_columns(df, ["observed_at", "created_at", "gt_created_at"])
    df = _coerce_numeric_columns(
        df,
        ["client_id", "ground_truth", "y_true", "true_label"],
    )
    return _sort_by_first_existing_date(df, ["observed_at", "created_at", "gt_created_at"])


def _postprocess_monitoring_models(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie le registre des modèles.
    """
    df = _coerce_datetime_columns(df, ["deployed_at", "created_at"])

    if "is_active" in df.columns:
        try:
            df["is_active"] = df["is_active"].astype("boolean")
        except Exception:
            pass

    return _sort_by_first_existing_date(df, ["deployed_at", "created_at"])


def _postprocess_evaluation_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie les métriques d'évaluation.
    """
    df = _coerce_datetime_columns(df, ["computed_at", "window_start", "window_end"])
    df = _coerce_numeric_columns(
        df,
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
        ],
    )
    return _sort_by_first_existing_date(df, ["computed_at"])


def _postprocess_drift_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie les métriques de drift.
    """
    df = _coerce_datetime_columns(
        df,
        [
            "computed_at",
            "reference_window_start",
            "reference_window_end",
            "current_window_start",
            "current_window_end",
        ],
    )
    df = _coerce_numeric_columns(df, ["metric_value", "threshold_value"])

    if "drift_detected" in df.columns:
        try:
            df["drift_detected"] = df["drift_detected"].astype("boolean")
        except Exception:
            pass

    return _sort_by_first_existing_date(df, ["computed_at"])


def _postprocess_alerts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie les alertes.
    """
    df = _coerce_datetime_columns(df, ["created_at", "acknowledged_at", "resolved_at"])
    return _sort_by_first_existing_date(df, ["created_at"])


def _postprocess_feature_store(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie le feature store de monitoring.
    """
    df = _coerce_datetime_columns(df, ["snapshot_timestamp"])
    df = _coerce_numeric_columns(df, ["client_id"])
    return _sort_by_first_existing_date(df, ["snapshot_timestamp"])


# =============================================================================
# Métriques dashboard
# =============================================================================

def metric_safe_number(
    df: pd.DataFrame,
    col: str,
    metric: str,
    default: float | int | None = 0,
) -> float | int | None:
    """
    Calcule une métrique numérique simple sur une colonne.

    Métriques acceptées :
    - mean
    - min
    - max
    - median
    - p95
    - p99
    - sum
    """
    if not isinstance(df, pd.DataFrame) or df.empty or col not in df.columns:
        return default

    series = pd.to_numeric(df[col], errors="coerce").dropna()

    if series.empty:
        return default

    try:
        if metric == "mean":
            return float(series.mean())
        if metric == "min":
            return float(series.min())
        if metric == "max":
            return float(series.max())
        if metric == "median":
            return float(series.median())
        if metric == "p95":
            return float(series.quantile(0.95))
        if metric == "p99":
            return float(series.quantile(0.99))
        if metric == "sum":
            return float(series.sum())
    except Exception:
        return default

    return default


def normalize_prediction_result(payload: Any) -> dict[str, Any]:
    """
    Garantit un dictionnaire pour afficher une prédiction.
    """
    return payload if isinstance(payload, dict) else {"result": payload}


# =============================================================================
# Santé API
# =============================================================================

def get_health(*, base_url: str) -> dict[str, Any]:
    """
    Appelle `/predict/health`.
    """
    ok, result = call_api(
        "/predict/health",
        base_url=base_url,
        method="GET",
        api_key=None,
    )

    return result if ok and isinstance(result, dict) else {}


# =============================================================================
# Prédictions
# =============================================================================

def call_predict_api(
    payload: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une prédiction avec un payload JSON complet.
    """
    return call_api(
        "/predict",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        json_data=payload,
        timeout=timeout,
    )


def call_predict_client_api(
    client_id: int,
    *,
    base_url: str,
    api_key: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une prédiction à partir d'un SK_ID_CURR.
    """
    return call_api(
        f"/predict/{int(client_id)}",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        timeout=timeout,
    )


def call_predict_batch_api(
    payloads: list[dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
    timeout: int = DEFAULT_BATCH_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une prédiction batch.
    """
    return call_api(
        "/predict/batch",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        json_data=payloads,
        timeout=timeout,
    )


def call_predict_real_random_batch_api(
    *,
    batch_size: int,
    base_url: str,
    api_key: str,
    random_seed: int | None = None,
    timeout: int = DEFAULT_SIMULATION_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une simulation à partir de clients réels tirés aléatoirement.
    """
    params: dict[str, Any] = {"limit": int(batch_size)}

    if random_seed is not None:
        params["random_seed"] = int(random_seed)

    return call_api(
        "/predict/simulate/real-sample",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        params=params,
        timeout=timeout,
    )


def call_predict_fully_random_batch_api(
    *,
    batch_size: int,
    base_url: str,
    api_key: str,
    timeout: int = DEFAULT_SIMULATION_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une simulation avec données totalement aléatoires.
    """
    return call_api(
        "/predict/simulate/random",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        params={"limit": int(batch_size)},
        timeout=timeout,
    )


def prediction_result_to_dataframe(payload: Any) -> pd.DataFrame:
    """
    Convertit une prédiction unitaire en DataFrame.
    """
    return dict_payload_to_dataframe(payload)


def simulation_result_to_dataframe(payload: Any) -> pd.DataFrame:
    """
    Convertit une simulation batch en DataFrame.
    """
    return items_payload_to_dataframe(payload)


# =============================================================================
# Historique
# =============================================================================

def get_prediction_history(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    client_id: int | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    only_errors: bool = False,
    decision: str | None = None,
) -> pd.DataFrame:
    """
    Récupère l'historique des prédictions.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    optional_params = {
        "client_id": client_id,
        "model_name": model_name,
        "model_version": model_version,
        "decision": decision,
    }

    params.update({k: v for k, v in optional_params.items() if v is not None})

    if only_errors:
        params["only_errors"] = True

    ok, result = call_api(
        "/history/predictions",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_prediction_logs(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


def get_prediction_detail(
    request_id: str,
    *,
    base_url: str,
    api_key: str,
) -> dict[str, Any] | None:
    """
    Récupère le détail d'une prédiction.
    """
    ok, result = call_api(
        f"/history/predictions/{request_id}",
        base_url=base_url,
        api_key=api_key,
        method="GET",
    )

    if not ok:
        return None

    row = _extract_single_dict(result)
    return row if isinstance(row, dict) and not _is_error_payload(row) else None


def get_ground_truth_history(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    client_id: int | None = None,
    request_id: str | None = None,
) -> pd.DataFrame:
    """
    Récupère l'historique des vérités terrain.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    if client_id is not None:
        params["client_id"] = int(client_id)

    if request_id is not None:
        params["request_id"] = request_id

    ok, result = call_api(
        "/history/ground-truth",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_ground_truth(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


def get_prediction_features_snapshot(
    request_id: str,
    *,
    base_url: str,
    api_key: str,
) -> dict[str, Any] | None:
    """
    Récupère les features exactes utilisées pour une prédiction.
    """
    ok, result = call_api(
        f"/history/features/{request_id}",
        base_url=base_url,
        api_key=api_key,
        method="GET",
    )

    return result if ok and isinstance(result, dict) else None


def get_prediction_features_snapshot_df(
    request_id: str,
    *,
    base_url: str,
    api_key: str,
) -> pd.DataFrame:
    """
    Récupère le snapshot de features et le convertit en DataFrame.
    """
    payload = get_prediction_features_snapshot(
        request_id,
        base_url=base_url,
        api_key=api_key,
    )

    if not isinstance(payload, dict):
        return pd.DataFrame()

    if isinstance(payload.get("items"), list):
        return pd.DataFrame(payload["items"])

    if isinstance(payload.get("features"), dict):
        return pd.DataFrame([payload["features"]])

    if isinstance(payload.get("data"), dict):
        return pd.DataFrame([payload["data"]])

    return pd.DataFrame()


def get_ground_truth_by_request_id(
    request_id: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    ground_truth_df: pd.DataFrame | None = None,
    prediction_logs_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Retrouve une vérité terrain associée à une prédiction.

    Stratégie :
    1. recherche directe par request_id
    2. fallback par client_id si disponible
    3. fallback API si base_url et api_key sont fournis
    """
    if isinstance(ground_truth_df, pd.DataFrame) and not ground_truth_df.empty:
        gt_df = ground_truth_df.copy()

        if "request_id" in gt_df.columns:
            by_request = gt_df[gt_df["request_id"].astype(str) == str(request_id)]
            if not by_request.empty:
                return by_request.copy()

        can_lookup_client = (
            isinstance(prediction_logs_df, pd.DataFrame)
            and not prediction_logs_df.empty
            and "request_id" in prediction_logs_df.columns
            and "client_id" in prediction_logs_df.columns
            and "client_id" in gt_df.columns
        )

        if can_lookup_client:
            pred_row = prediction_logs_df[
                prediction_logs_df["request_id"].astype(str) == str(request_id)
            ]

            if not pred_row.empty:
                client_id = pred_row.iloc[0].get("client_id")

                if client_id is not None and not pd.isna(client_id):
                    return gt_df[gt_df["client_id"].astype(str) == str(int(client_id))].copy()

        return pd.DataFrame()

    if base_url and api_key:
        return get_ground_truth_history(
            base_url=base_url,
            api_key=api_key,
            limit=50,
            request_id=request_id,
        )

    return pd.DataFrame()


# =============================================================================
# Features client
# =============================================================================

def load_client_features(
    client_id: int,
    source_table: str,
    *,
    base_url: str,
    api_key: str,
) -> pd.DataFrame:
    """
    Charge les features d'un client depuis l'API.
    """
    ok, result = call_api(
        f"/features/client/{int(client_id)}",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params={"source_table": source_table},
    )

    if not ok:
        return pd.DataFrame()

    if isinstance(result, dict):
        for key in ["features", "data", "item"]:
            value = result.get(key)
            if isinstance(value, dict):
                return pd.DataFrame([{"SK_ID_CURR": client_id, **value}])

    df = items_payload_to_dataframe(result)

    if not df.empty and "SK_ID_CURR" not in df.columns:
        df["SK_ID_CURR"] = client_id

    return df


# =============================================================================
# Monitoring
# =============================================================================

def get_models(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    model_name: str | None = None,
    is_active: bool | None = None,
) -> pd.DataFrame:
    """
    Récupère le registre des modèles.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    if model_name is not None:
        params["model_name"] = model_name

    if is_active is not None:
        params["is_active"] = is_active

    ok, result = call_api(
        "/monitoring/models",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_monitoring_models(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


def get_active_model(
    *,
    base_url: str,
    api_key: str,
    model_name: str | None = None,
) -> pd.DataFrame:
    """
    Récupère le modèle actif.
    """
    params = {"model_name": model_name} if model_name is not None else None

    ok, result = call_api(
        "/monitoring/active-model",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    if not ok:
        return pd.DataFrame()

    row = _extract_single_dict(result)

    if not isinstance(row, dict) or not row or _is_error_payload(row):
        return pd.DataFrame()

    return _postprocess_monitoring_models(pd.DataFrame([row]))


def resolve_active_model_context(
    *,
    base_url: str,
    api_key: str,
    model_name: str | None = None,
) -> dict[str, Any]:
    """
    Résout le modèle actif utilisé par le dashboard.
    """
    active_df = get_active_model(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
    )

    if active_df.empty:
        return {
            "model_name": model_name,
            "model_version": None,
            "is_active_model_found": False,
        }

    row = active_df.iloc[0].to_dict()

    return {
        "model_name": row.get("model_name"),
        "model_version": row.get("model_version"),
        "stage": row.get("stage"),
        "is_active": row.get("is_active"),
        "deployed_at": row.get("deployed_at"),
        "is_active_model_found": True,
    }


def _resolve_model_params(
    *,
    base_url: str,
    api_key: str,
    model_name: str | None,
    model_version: str | None,
    auto_resolve_active_model: bool,
) -> tuple[str | None, str | None]:
    """
    Résout model_name/model_version si le dashboard ne les connaît pas encore.
    """
    resolved_model_name = model_name
    resolved_model_version = model_version

    if auto_resolve_active_model and resolved_model_name is None:
        ctx = resolve_active_model_context(
            base_url=base_url,
            api_key=api_key,
            model_name=None,
        )
        resolved_model_name = ctx.get("model_name")

        if resolved_model_version is None:
            resolved_model_version = ctx.get("model_version")

    return resolved_model_name, resolved_model_version


def get_monitoring_summary(
    *,
    base_url: str,
    api_key: str,
    model_name: str | None = None,
    model_version: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    auto_resolve_active_model: bool = True,
) -> dict[str, Any]:
    """
    Récupère le résumé global du monitoring.
    """
    resolved_model_name, resolved_model_version = _resolve_model_params(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        model_version=model_version,
        auto_resolve_active_model=auto_resolve_active_model,
    )

    if not resolved_model_name:
        return {
            "detail": "Impossible de récupérer le monitoring summary : aucun model_name disponible.",
            "model_name": None,
            "model_version": resolved_model_version,
        }

    params: dict[str, Any] = {"model_name": resolved_model_name}

    if resolved_model_version is not None:
        params["model_version"] = resolved_model_version

    if window_start is not None and window_end is not None:
        params["window_start"] = window_start
        params["window_end"] = window_end

    ok, result = call_api(
        "/monitoring/summary",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return result if ok and isinstance(result, dict) else {}


def get_monitoring_health(
    *,
    base_url: str,
    api_key: str,
    model_name: str | None = None,
    model_version: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    auto_resolve_active_model: bool = True,
) -> dict[str, Any]:
    """
    Récupère l'état de santé du monitoring.
    """
    resolved_model_name, resolved_model_version = _resolve_model_params(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        model_version=model_version,
        auto_resolve_active_model=auto_resolve_active_model,
    )

    if not resolved_model_name:
        return {
            "detail": "Impossible de récupérer le monitoring health : aucun model_name disponible.",
            "model_name": None,
            "model_version": resolved_model_version,
        }

    params: dict[str, Any] = {"model_name": resolved_model_name}

    if resolved_model_version is not None:
        params["model_version"] = resolved_model_version

    if window_start is not None and window_end is not None:
        params["window_start"] = window_start
        params["window_end"] = window_end

    ok, result = call_api(
        "/monitoring/health",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return result if ok and isinstance(result, dict) else {}


def get_evaluation_metrics(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    model_name: str | None = None,
    model_version: str | None = None,
    dataset_name: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> pd.DataFrame:
    """
    Récupère les métriques d'évaluation.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    for key, value in {
        "model_name": model_name,
        "model_version": model_version,
        "dataset_name": dataset_name,
    }.items():
        if value is not None:
            params[key] = value

    if window_start is not None and window_end is not None:
        params["window_start"] = window_start
        params["window_end"] = window_end

    ok, result = call_api(
        "/monitoring/evaluation",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_evaluation_metrics(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


def get_drift_metrics(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
    metric_name: str | None = None,
    drift_detected: bool | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> pd.DataFrame:
    """
    Récupère les métriques de drift.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    for key, value in {
        "model_name": model_name,
        "model_version": model_version,
        "feature_name": feature_name,
        "metric_name": metric_name,
        "drift_detected": drift_detected,
    }.items():
        if value is not None:
            params[key] = value

    if window_start is not None and window_end is not None:
        params["window_start"] = window_start
        params["window_end"] = window_end

    ok, result = call_api(
        "/monitoring/drift",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_drift_metrics(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


def get_alerts(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    status_filter: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    feature_name: str | None = None,
) -> pd.DataFrame:
    """
    Récupère les alertes de monitoring.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    for key, value in {
        "status": status_filter,
        "severity": severity,
        "alert_type": alert_type,
        "model_name": model_name,
        "model_version": model_version,
        "feature_name": feature_name,
    }.items():
        if value is not None:
            params[key] = value

    ok, result = call_api(
        "/monitoring/alerts",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_alerts(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


def get_feature_store_monitoring(
    *,
    base_url: str,
    api_key: str,
    limit: int = 200,
    request_id: str | None = None,
    client_id: int | None = None,
    feature_name: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    source_table: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> pd.DataFrame:
    """
    Récupère le feature store de monitoring.
    """
    params: dict[str, Any] = {"limit": int(limit)}

    for key, value in {
        "request_id": request_id,
        "client_id": client_id,
        "feature_name": feature_name,
        "model_name": model_name,
        "model_version": model_version,
        "source_table": source_table,
    }.items():
        if value is not None:
            params[key] = value

    if window_start is not None and window_end is not None:
        params["window_start"] = window_start
        params["window_end"] = window_end

    ok, result = call_api(
        "/monitoring/feature-store",
        base_url=base_url,
        api_key=api_key,
        method="GET",
        params=params,
    )

    return _postprocess_feature_store(items_payload_to_dataframe(result)) if ok else pd.DataFrame()


# =============================================================================
# Helpers système dashboard
# =============================================================================

def build_tables_status_dataframe(
    *,
    prediction_logs_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
    active_model_df: pd.DataFrame,
    feature_store_monitoring_df: pd.DataFrame,
    drift_metrics_df: pd.DataFrame,
    evaluation_metrics_df: pd.DataFrame,
    alerts_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Construit le tableau de disponibilité des ressources du dashboard.
    """
    resources = [
        ("prediction_logs", prediction_logs_df, "Historique des prédictions"),
        ("ground_truth_labels", ground_truth_df, "Vérités terrain"),
        ("model_registry", model_registry_df, "Registre des modèles"),
        ("active_model", active_model_df, "Modèle actif"),
        ("feature_store_monitoring", feature_store_monitoring_df, "Features de monitoring"),
        ("drift_metrics", drift_metrics_df, "Métriques de drift"),
        ("evaluation_metrics", evaluation_metrics_df, "Métriques d'évaluation"),
        ("alerts", alerts_df, "Alertes de monitoring"),
    ]

    rows = []

    for resource_name, df, comment in resources:
        is_df = isinstance(df, pd.DataFrame)
        row_count = len(df) if is_df else 0

        rows.append(
            {
                "resource_name": resource_name,
                "is_available": is_df and not df.empty,
                "row_count": row_count,
                "comment": comment,
            }
        )

    return pd.DataFrame(rows)


def build_preview_map(
    *,
    prediction_logs_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
    model_registry_df: pd.DataFrame,
    feature_store_monitoring_df: pd.DataFrame,
    drift_metrics_df: pd.DataFrame,
    evaluation_metrics_df: pd.DataFrame,
    alerts_df: pd.DataFrame,
    max_rows: int = 200,
) -> dict[str, pd.DataFrame]:
    """
    Prépare les aperçus affichés dans la page système.
    """
    return {
        "prediction_logs": prediction_logs_df.head(max_rows),
        "ground_truth_labels": ground_truth_df.head(max_rows),
        "model_registry": model_registry_df.head(max_rows),
        "feature_store_monitoring": feature_store_monitoring_df.head(max_rows),
        "drift_metrics": drift_metrics_df.head(max_rows),
        "evaluation_metrics": evaluation_metrics_df.head(max_rows),
        "alerts": alerts_df.head(max_rows),
    }


# =============================================================================
# Analyses
# =============================================================================

def run_evidently_analysis(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    model_version: str | None = None,
    reference_kind: str = "raw",
    current_kind: str = "raw",
    monitoring_dir: str | None = None,
    max_rows: int | None = 20000,
    timeout: int = DEFAULT_ANALYSIS_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une analyse Evidently depuis les données applicatives.
    """
    params: dict[str, Any] = {
        "model_name": model_name,
        "reference_kind": reference_kind,
        "current_kind": current_kind,
    }

    if model_version is not None:
        params["model_version"] = model_version

    if monitoring_dir is not None:
        params["monitoring_dir"] = monitoring_dir

    if max_rows is not None:
        params["max_rows"] = int(max_rows)

    return call_api(
        "/analyse/evidently/run",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        params=params,
        timeout=timeout,
    )


def run_evidently_analysis_from_feature_store(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    model_version: str | None = None,
    source_table: str | None = None,
    max_rows: int = 10000,
    timeout: int = DEFAULT_ANALYSIS_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance une analyse Evidently depuis les snapshots de production.

    C'est l'appel à utiliser pour voir les colonnes en drift depuis :
    - simulate_real_sample
    - simulate_random
    - api_request
    - features_ready_cache
    """
    params: dict[str, Any] = {
        "model_name": model_name,
        "max_rows": int(max_rows),
    }

    if model_version is not None:
        params["model_version"] = model_version

    if source_table is not None:
        params["source_table"] = source_table

    return call_api(
        "/analyse/evidently/run-from-feature-store",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        params=params,
        timeout=timeout,
    )


def run_monitoring_evaluation_analysis(
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    model_version: str | None = None,
    dataset_name: str = "scoring_prod",
    window_start: str | None = None,
    window_end: str | None = None,
    beta: float = 2.0,
    cost_fn: float = 10.0,
    cost_fp: float = 1.0,
    timeout: int = DEFAULT_ANALYSIS_TIMEOUT,
) -> tuple[bool, Any]:
    """
    Lance l'évaluation monitoring du modèle.
    """
    params: dict[str, Any] = {
        "model_name": model_name,
        "dataset_name": dataset_name,
        "beta": float(beta),
        "cost_fn": float(cost_fn),
        "cost_fp": float(cost_fp),
    }

    if model_version is not None:
        params["model_version"] = model_version

    if window_start is not None:
        params["window_start"] = window_start

    if window_end is not None:
        params["window_end"] = window_end

    return call_api(
        "/analyse/evaluation/run",
        base_url=base_url,
        api_key=api_key,
        method="POST",
        params=params,
        timeout=timeout,
    )