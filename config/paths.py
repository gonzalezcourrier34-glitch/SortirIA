"""
Centralise tous les chemins utilisés par le projet CheckIt.AI.
Construit les chemins à partir de la racine du projet.
Autorise certains répertoires à être redéfinis par l'environnement.
Expose des constantes Path partagées par toute l'application.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `config/paths.py` (commit 93632c5).
"""

from __future__ import annotations

import os
from pathlib import Path

# Racine du projet
BASE_DIR = Path(__file__).resolve().parent.parent


# Utilitaires
def get_configurable_path(
    environment_variable: str,
    default_path: Path
) -> Path:
    """Retourne un chemin configurable via une variable d'environnement.

    Parameters
    ----------
    environment_variable : str
        Nom de la variable qui peut redéfinir le chemin (par exemple `CHECKIT_DATA_DIR`).
    default_path : Path
        Chemin utilisé si la variable est absente ou vide.

    Returns
    -------
    Path
        Le chemin lu dans la variable (avec `~` développé), sinon `default_path`.
    """

    value = os.getenv(environment_variable)

    if not value or not value.strip():
        return default_path

    return Path(value.strip()).expanduser()


# Dossiers du projet
CONFIG_DIR = BASE_DIR / "config"


# Configuration
SOURCES_FILE = CONFIG_DIR / "sources.yaml"


# Répertoires de travail
DATA_DIR = get_configurable_path(
    "CHECKIT_DATA_DIR",
    BASE_DIR / "data"
)
LOG_DIR = get_configurable_path(
    "CHECKIT_LOG_DIR",
    BASE_DIR / "logs"
)


# Secrets
LOCAL_SECRETS_DIR = get_configurable_path(
    "CHECKIT_LOCAL_SECRETS_DIR",
    BASE_DIR / "secrets"
)
DOCKER_SECRETS_DIR = Path("/run/secrets")


# Journaux
LOG_FILE = LOG_DIR / "pipeline.log"


