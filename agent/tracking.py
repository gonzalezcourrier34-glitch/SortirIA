"""Suivi des exécutions de l'agent avec MLflow.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). MLflow avait
servi au projet RAG de la formation (suivi des expériences) ; il sert ici à
suivre un agent en production : **une nuit = une exécution MLflow**.

Ce qui est enregistré :

- **paramètres** : sources lancées, seuils de décision, modèles Mistral,
  version de la consigne de lecture des PDF, mode essai ;
- **métriques** : événements extraits / rejetés / enregistrés, par source
  (gardés, rejetés, essais, durée), qualité de la lecture des PDF (titres
  absents du texte = inventions rattrapées, champs invalides, hors période),
  coût et tokens par modèle, couverture famille / soirée × Montpellier / Thau,
  durée de chaque nœud du graphe, nombre d'alertes ;
- **trace** : avec `mlflow.langchain.autolog()`, le graphe LangGraph nœud par
  nœud et chaque appel à Mistral (consigne, réponse, tokens, durée) ;
- **artefact** : le bilan complet de la nuit (`bilan.json`).

Le suivi ne doit jamais faire échouer la collecte : sans MLflow installé, ou
en cas d'erreur, l'agent continue et le journal le signale.

Réglages (`.env`) :

- `MLFLOW_TRACKING_URI` : serveur MLflow (ex. `http://localhost:5000`, service
  `mlflow` de docker-compose) ; par défaut, une base SQLite locale
  (`data/mlflow/mlflow.db`) ;
- `MLFLOW_EXPERIMENT` : nom de l'expérience (`sortiria-collecte`) ;
- `MLFLOW_ACTIF=false` : désactive le suivi.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from src.logger import get_logger

logger = get_logger(__name__)

DEFAULT_EXPERIMENT = "sortiria-collecte"
METRIC_KEY_PATTERN = re.compile(r"[^A-Za-z0-9_\-./ ]")
# Motifs de rejet de la lecture des PDF par Mistral (src/event/document_mapper.py).
LLM_REJECTION_REASONS = ("titre_absent_du_texte", "champs_llm_invalides", "hors_periode", "reponse_llm_illisible")


def metric_key(*parts: Any) -> str:
    """Nom de métrique accepté par MLflow : « events_kept/OpenAgenda - Montpellier »."""

    return "/".join(METRIC_KEY_PATTERN.sub("_", str(part)) for part in parts if str(part))


# ---------------------------------------------------------------------------
# Ce qu'on enregistre (fonctions pures, testées sans MLflow)
# ---------------------------------------------------------------------------

def run_metrics(state: Mapping[str, Any]) -> dict[str, float]:
    """Toutes les métriques d'une nuit, à partir de l'état final de l'agent."""

    metrics: dict[str, float] = {}
    preparation = state.get("preparation") or {}
    extracted = float(preparation.get("events_extracted", 0))
    rejected = float(preparation.get("events_rejected", 0))
    metrics.update({
        "events_extracted": extracted,
        "events_rejected": rejected,
        "rejection_rate": rejected / extracted if extracted else 0.0,
        "events_kept": float(len(state.get("events", []))),
        "events_upserted": float(state.get("events_upserted", 0)),
        "events_removed": float(state.get("events_removed", 0)),
        "events_expired": float(state.get("events_expired", 0)),
        "events_embedded": float(state.get("events_embedded", 0)),
        "events_enriched": float(state.get("events_enriched", 0)),
        "events_capped": float(state.get("events_capped", 0)),
        "notes/nouvelles_24h": float((state.get("ratings") or {}).get("new_24h", 0)),
        "notes/total": float((state.get("ratings") or {}).get("total", 0)),
        "events_total": float(state.get("events_total") or 0),
        "enrichment/invalid": float((state.get("enrichment") or {}).get("invalid", 0)),
        "enrichment/not_outing": float((state.get("enrichment") or {}).get("not_outing", 0)),
        "enrichment/failed_batches": float((state.get("enrichment") or {}).get("failed_batches", 0)),
        "attempts": float(state.get("attempts", 0)),
        "alerts_count": float(len(state.get("alerts", []))),
        "cost_usd": float(state.get("cost_usd", 0.0)),
    })

    # Par source : événements gardés après préparation, essais, échec.
    from agent.routing import failed_source_ids

    failed = set(failed_source_ids(state))
    for source in state.get("sources", []):
        kept = sum(1 for event in state.get("events", []) if event.get("source") == source)
        metrics[metric_key("source", source, "events")] = float(kept)
        metrics[metric_key("source", source, "attempts")] = float(state.get("source_attempts", {}).get(source, 0))
        metrics[metric_key("source", source, "failed")] = 1.0 if source in failed else 0.0

    # Par extracteur : bilan du moteur CheckIt.AI, et qualité de la lecture des PDF.
    for stat in state.get("extractor_stats", []):
        name = stat.get("name", "?")
        for field_name in ("analyzed", "kept", "rejected", "duration_s"):
            metrics[metric_key("extracteur", name, field_name)] = float(stat.get(field_name, 0))
        reasons = stat.get("rejection_reasons") or {}
        for reason, count in reasons.items():
            metrics[metric_key("extracteur", name, "rejet", reason)] = float(count)
        if stat.get("type") == "document":
            analyzed = float(stat.get("analyzed", 0)) or 1.0
            llm_rejections = sum(float(reasons.get(reason, 0)) for reason in LLM_REJECTION_REASONS)
            metrics["pdf/inventions_rattrapees"] = float(reasons.get("titre_absent_du_texte", 0))
            metrics["pdf/taux_rejet_llm"] = llm_rejections / analyzed

    quality = state.get("quality") or {}
    if quality.get("score") is not None:
        metrics["qualite/score"] = float(quality["score"])
        metrics["qualite/evenements_juges"] = float(quality.get("checked", 0))
        for check in ("coherent", "est_une_sortie", "publics_corrects", "categorie_correcte"):
            if check in quality:
                metrics[metric_key("qualite", check)] = float(quality[check])
    for model, tokens in (state.get("llm_usage") or {}).items():
        metrics[metric_key("tokens", model, "input")] = float(tokens.get("input", 0))
        metrics[metric_key("tokens", model, "output")] = float(tokens.get("output", 0))
    for zone, audiences in (state.get("coverage") or {}).items():
        for audience, count in audiences.items():
            metrics[metric_key("couverture", zone, audience)] = float(count)
    durations = state.get("node_durations") or {}
    for node, seconds in durations.items():
        metrics[metric_key("duree", node)] = float(seconds)
    metrics["duree/total"] = float(sum(durations.values()))
    # Dérive (19/10/2026) : PSI de chaque indicateur et valeur des mesures.
    from agent.drift import mlflow_metrics as drift_metrics

    metrics.update(drift_metrics(state.get("drift") or {}))
    return metrics


def run_params(services: Any, *, dry_run: bool, max_attempts: int) -> dict[str, str]:
    """Paramètres d'une nuit : modèles, seuils, version de consigne, mode."""

    from agent import nodes, watch
    from src.event.document_mapper import PROMPT_VERSION

    return {
        "dry_run": str(dry_run),
        "max_attempts": str(max_attempts),
        "sources_prevues": ",".join(services.default_sources),
        "embedding_model": str(services.embedding_model),
        "document_model": os.getenv("DOCUMENT_EXTRACTION_MODEL", "mistral-small-latest"),
        "document_prompt_version": PROMPT_VERSION,
        "drop_ratio": str(nodes.DROP_RATIO),
        "drop_min_previous": str(nodes.DROP_MIN_PREVIOUS),
        "rejection_alert_ratio": str(nodes.REJECTION_ALERT_RATIO),
        "coverage_min": str(watch.coverage_minimum()),
        "budget_usd": str(watch.nightly_budget()),
    }


# ---------------------------------------------------------------------------
# Traceurs
# ---------------------------------------------------------------------------

class NullTracker:
    """Aucun suivi (tests, ou MLflow désactivé)."""

    run_id = ""

    def start(self, params: Mapping[str, str]) -> None:
        del params

    def finish(self, state: Mapping[str, Any]) -> None:
        del state


class MlflowTracker:
    """Une nuit = une exécution MLflow ; aucune erreur de suivi ne remonte."""

    def __init__(self, tracking_uri: str, experiment: str = DEFAULT_EXPERIMENT, artifact_location: str = "") -> None:
        self.tracking_uri = tracking_uri
        self.experiment = experiment
        self.artifact_location = artifact_location
        self.run_id = ""

    def server_is_up(self) -> bool:
        """Serveur HTTP joignable en 3 s ? (sinon, le client MLflow réessaie plusieurs minutes)."""

        if not self.tracking_uri.startswith("http"):
            return True
        try:
            import httpx

            return httpx.get(f"{self.tracking_uri.rstrip('/')}/health", timeout=3).status_code == 200
        except Exception:  # noqa: BLE001 (toute erreur réseau = serveur absent)
            return False

    def start(self, params: Mapping[str, str]) -> None:
        if not self.server_is_up():
            logger.warning("Serveur MLflow injoignable (%s) : collecte sans suivi cette nuit.", self.tracking_uri)
            self.run_id = ""
            return
        try:
            import mlflow

            mlflow.set_tracking_uri(self.tracking_uri)
            if mlflow.get_experiment_by_name(self.experiment) is None:
                mlflow.create_experiment(self.experiment, artifact_location=self.artifact_location or None)
            mlflow.set_experiment(self.experiment)
            try:
                # Trace du graphe LangGraph et de chaque appel Mistral (LangChain).
                mlflow.langchain.autolog()
            except Exception as error:  # noqa: BLE001 (la trace est un plus, pas une obligation)
                logger.warning("Trace LangChain indisponible : %s", error)
            run = mlflow.start_run(run_name=f"collecte {params.get('started_at', '')}".strip())
            self.run_id = run.info.run_id
            mlflow.log_params(dict(params))
        except Exception:
            logger.exception("Suivi MLflow impossible au démarrage : la collecte continue sans.")
            self.run_id = ""

    def finish(self, state: Mapping[str, Any]) -> None:
        if not self.run_id:
            return
        try:
            import mlflow

            mlflow.log_param("sources_lancees", ",".join(state.get("sources", [])))
            mlflow.log_metrics(run_metrics(state))
            mlflow.set_tags({"status": str(state.get("status", "")), "run_id_base": str(state.get("run_id") or "")})
            mlflow.log_dict(
                {key: state.get(key) for key in (
                    "status", "sources", "skipped_sources", "disabled_sources", "failed_sources", "preparation", "alerts",
                    "coverage", "cost_usd", "llm_usage", "extractor_stats", "node_durations", "errors",
                    "quality", "enrichment",
                )},
                "bilan.json",
            )
            mlflow.end_run(status="FAILED" if state.get("status") == "failed" else "FINISHED")
        except Exception:
            logger.exception("Suivi MLflow impossible à la fin de la collecte.")


def build_tracker() -> NullTracker | MlflowTracker:
    """Traceur de production : MLflow, sauf `MLFLOW_ACTIF=false` ou MLflow absent."""

    if os.getenv("MLFLOW_ACTIF", "true").strip().lower() in ("false", "0", "non", "no"):
        return NullTracker()
    try:
        import mlflow  # noqa: F401
    except ImportError:
        logger.warning("MLflow n'est pas installé : collecte sans suivi (uv sync).")
        return NullTracker()
    from config.paths import DATA_DIR

    folder = DATA_DIR / "mlflow"
    folder.mkdir(parents=True, exist_ok=True)
    uri = os.getenv("MLFLOW_TRACKING_URI") or f"sqlite:///{(folder / 'mlflow.db').as_posix()}"
    artifacts = "" if os.getenv("MLFLOW_TRACKING_URI") else (folder / "artifacts").as_uri()
    return MlflowTracker(uri, os.getenv("MLFLOW_EXPERIMENT", DEFAULT_EXPERIMENT), artifacts)
