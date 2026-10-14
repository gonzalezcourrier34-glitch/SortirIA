"""
Centralise tous les chemins utilisés par le projet CheckIt.AI.
Construit les chemins à partir de la racine du projet.
Autorise certains répertoires à être redéfinis par l'environnement.
Expose des constantes Path partagées par toute l'application.
Crée les dossiers nécessaires au fonctionnement du projet.

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
    """Retourne un chemin configurable via une variable d'environnement."""

    value = os.getenv(environment_variable)

    if not value or not value.strip():
        return default_path

    return Path(value.strip()).expanduser()


# Dossiers du projet
CONFIG_DIR = BASE_DIR / "config"
SRC_DIR = BASE_DIR / "src"
DOCS_DIR = BASE_DIR / "docs"
TESTS_DIR = BASE_DIR / "tests"


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
LOTS_DIR = get_configurable_path(
    "CHECKIT_SHARED_LOTS_DIR",
    BASE_DIR / "shared" / "lots"
)


# Secrets
LOCAL_SECRETS_DIR = get_configurable_path(
    "CHECKIT_LOCAL_SECRETS_DIR",
    BASE_DIR / "secrets"
)
DOCKER_SECRETS_DIR = Path("/run/secrets")


# Données
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
DATASETS_DIR = DATA_DIR / "datasets"
IMAGES_DIR = DATA_DIR / "images"
TEMP_DIR = DATA_DIR / "temp"


# Journaux
LOG_FILE = LOG_DIR / "pipeline.log"
EXTRACTION_REPORT_FILE = LOG_DIR / "extraction_report.json"


# Arborescence
DIRECTORIES_TO_CREATE: tuple[Path, ...] = (
    DATA_DIR,
    LOG_DIR,
    LOTS_DIR,
    LOCAL_SECRETS_DIR,
    RAW_DATA_DIR,
    PROCESSED_DATA_DIR,
    DATASETS_DIR,
    IMAGES_DIR,
    TEMP_DIR,
)


def ensure_project_directories() -> None:
    """Crée les répertoires requis par le projet."""

    for directory in DIRECTORIES_TO_CREATE:
        directory.mkdir(parents=True, exist_ok=True)