"""Envoi d'un lancement d'évaluation dans MLflow, sans jamais faire échouer l'évaluation.

**Provenance** : écrit pour SortirIA (16/10/2026), en sortant de
`scripts/eval_recherche.py` le code qui y envoyait déjà ses lancements ; il
sert maintenant aussi au juge de pertinence (`scripts/eval_pertinence.py`).
Toutes les évaluations de modèles sont ainsi au même endroit dans MLflow
(http://localhost:5000) :

- `sortiria-collecte` : une exécution par nuit de collecte (`agent/tracking.py`) ;
- `sortiria-evaluation-recherche` : respect des contraintes (objectif 95 %) ;
- `sortiria-evaluation-pertinence` : pertinence@3 humaine et juge Mistral
  (modèle, version de consigne, accord, kappa, AC1).

MLflow est un plus : l'historique CSV local est toujours écrit avant. Variables :
`MLFLOW_TRACKING_URI` (serveur, sinon fichiers locaux), `MLFLOW_ACTIF=false`
pour couper.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

OFF_VALUES = ("false", "0", "non", "no")


def log_run(experiment: str, run_name: str, params: Mapping[str, Any], metrics: Mapping[str, Any],
            artifacts: Iterable[Path] = ()) -> str:
    """Un lancement = une exécution MLflow. Renvoie un message à afficher.

    Les métriques vides (None, "") sont laissées de côté ; les paramètres sont
    coupés à 250 caractères (limite de MLflow).
    """

    if os.getenv("MLFLOW_ACTIF", "true").strip().lower() in OFF_VALUES:
        return "MLflow désactivé (MLFLOW_ACTIF)."
    uri = os.getenv("MLFLOW_TRACKING_URI", "")
    try:
        import mlflow

        if uri.startswith("http"):
            # Serveur injoignable : on abandonne en 3 s au lieu des minutes de
            # réessais du client MLflow.
            from urllib.request import urlopen

            with urlopen(f"{uri.rstrip('/')}/health", timeout=3):
                pass
            mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params({key: str(value)[:250] for key, value in params.items()})
            mlflow.log_metrics({key: float(value) for key, value in metrics.items()
                                if value is not None and value != ""})
            for path in artifacts:
                if Path(path).exists():
                    mlflow.log_artifact(str(path))
        return f"Lancement enregistré dans MLflow (expérience {experiment})."
    except Exception as error:  # noqa: BLE001 (MLflow est un plus : l'historique local est déjà écrit)
        return f"MLflow indisponible ({type(error).__name__}) : historique local seulement."
