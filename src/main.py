"""Point d'entrée principal du projet CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/main.py` (commit 93632c5).
"""

from config.paths import ensure_project_directories
from src.pipelines.pipeline import run_pipeline


def main() -> None:
    """Prépare l'environnement local puis lance le pipeline."""

    # Crée les dossiers data, logs, images, temp et lots
    # uniquement au démarrage réel de l'application.
    ensure_project_directories()

    run_pipeline()


if __name__ == "__main__":
    main()