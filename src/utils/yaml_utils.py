"""
Fonctions utilitaires de lecture des fichiers YAML.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/utils/yaml_utils.py` (commit 93632c5).

Ce module centralise la lecture sécurisée des fichiers YAML de configuration.

Il ne réalise aucune validation métier ni aucune transformation des données.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from src.logger import get_logger

logger = get_logger(__name__)


def load_yaml_file(
    file_path: str | Path
) -> dict[str, Any]:
    """
    Charge un fichier YAML et retourne toujours un dictionnaire.

    En cas d'erreur ou de contenu invalide, un dictionnaire vide est
    systématiquement retourné.
    """

    # Normalisation du chemin

    try:
        path = Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        logger.error(
            "Chemin YAML invalide : %s",
            error
        )
        return {}

    # Vérification du fichier

    if not path.is_file():
        logger.warning(
            "Fichier YAML introuvable : %s",
            path
        )
        return {}

    # Lecture

    try:
        with path.open(
            mode="r",
            encoding="utf-8"
        ) as file:
            data = yaml.safe_load(file)

    except yaml.YAMLError as error:
        logger.error(
            "Erreur YAML dans %s : %s",
            path,
            error
        )
        return {}

    except OSError as error:
        logger.error(
            "Impossible de lire %s : %s",
            path,
            error
        )
        return {}

    # Contenu vide

    if data is None:
        return {}

    # Type attendu

    if not isinstance(data, Mapping):
        logger.error(
            "Le contenu de %s doit être un dictionnaire.",
            path
        )
        return {}

    return dict(data)