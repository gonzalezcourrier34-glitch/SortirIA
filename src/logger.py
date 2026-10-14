"""Configuration centralisée du système de journalisation CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/logger.py` (commit 93632c5).
"""

from __future__ import annotations

import logging
import sys
from threading import Lock
from typing import Any

from config.constants import (
    LOGGER_CONFIGURED_ATTRIBUTE,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    PROJECT_LOGGER_NAME
)
from config.paths import LOG_FILE
from config.settings import LOG_LEVEL

_CONFIG_LOCK = Lock()

VALID_LOG_LEVELS: frozenset[int] = frozenset({
    logging.DEBUG,
    logging.INFO,
    logging.WARNING,
    logging.ERROR,
    logging.CRITICAL
})

ROOT_LOGGER_ALIASES: frozenset[str] = frozenset({
    "",
    PROJECT_LOGGER_NAME,
    "src",
    "__main__"
})


# Résolution du niveau de journalisation

def get_log_level(level: Any) -> int:
    """Convertit une valeur en niveau de journalisation valide."""

    if isinstance(level, bool):
        return logging.INFO

    if isinstance(level, int):
        return level if level in VALID_LOG_LEVELS else logging.INFO

    if isinstance(level, str):
        normalized_level = level.strip().upper()

        if not normalized_level:
            return logging.INFO

        resolved_level = logging.getLevelName(normalized_level)

        if isinstance(resolved_level, int):
            return resolved_level

    return logging.INFO


# Création des composants

def create_formatter() -> logging.Formatter:
    """Crée le format commun des journaux."""

    return logging.Formatter(
        fmt=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT
    )


def create_console_handler(
    log_level: int,
    formatter: logging.Formatter
) -> logging.Handler:
    """Crée le handler de console compatible avec Airflow."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(formatter)

    return handler


def create_file_handler(
    log_level: int,
    formatter: logging.Formatter
) -> logging.Handler | None:
    """Crée le handler fichier ou retourne None en cas d'échec."""

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(
            filename=LOG_FILE,
            encoding="utf-8",
            delay=True
        )
        handler.setLevel(log_level)
        handler.setFormatter(formatter)

        return handler

    except (OSError, ValueError):
        return None


# Gestion des handlers

def close_handler(handler: logging.Handler) -> None:
    """Ferme silencieusement un handler."""

    try:
        handler.flush()
    except (OSError, ValueError):
        pass

    try:
        handler.close()
    except (OSError, ValueError):
        pass


def close_logger_handlers(logger: logging.Logger) -> None:
    """Retire et ferme proprement tous les handlers d'un logger."""

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        close_handler(handler)


def add_logger_handler(
    logger: logging.Logger,
    handler: logging.Handler | None
) -> bool:
    """Ajoute un handler lorsqu'il a été correctement créé."""

    if handler is None:
        return False

    logger.addHandler(handler)

    return True


# Configuration du logger principal

def configure_project_logger() -> logging.Logger:
    """Configure une seule fois le logger parent du projet."""

    project_logger = logging.getLogger(PROJECT_LOGGER_NAME)

    if getattr(
        project_logger,
        LOGGER_CONFIGURED_ATTRIBUTE,
        False
    ):
        return project_logger

    with _CONFIG_LOCK:
        if getattr(
            project_logger,
            LOGGER_CONFIGURED_ATTRIBUTE,
            False
        ):
            return project_logger

        log_level = get_log_level(LOG_LEVEL)
        formatter = create_formatter()

        project_logger.setLevel(log_level)
        project_logger.propagate = False

        close_logger_handlers(project_logger)

        add_logger_handler(
            project_logger,
            create_console_handler(
                log_level=log_level,
                formatter=formatter
            )
        )

        file_handler_added = add_logger_handler(
            project_logger,
            create_file_handler(
                log_level=log_level,
                formatter=formatter
            )
        )

        setattr(
            project_logger,
            LOGGER_CONFIGURED_ATTRIBUTE,
            True
        )

        if not file_handler_added:
            project_logger.warning(
                "Impossible de créer le fichier de log %s. "
                "La journalisation continue uniquement dans la console.",
                LOG_FILE
            )

    return project_logger


# Normalisation des noms

def normalize_logger_name(name: Any) -> str:
    """Normalise le nom d'un logger enfant."""

    if not isinstance(name, str):
        return ""

    normalized_name = name.strip()

    if normalized_name in ROOT_LOGGER_ALIASES:
        return ""

    project_prefix = f"{PROJECT_LOGGER_NAME}."

    if normalized_name.startswith(project_prefix):
        normalized_name = normalized_name.removeprefix(
            project_prefix
        )

    if normalized_name.startswith("src."):
        normalized_name = normalized_name.removeprefix("src.")

    return normalized_name.strip(".")


# Accès aux loggers

def get_logger(name: str | None = None) -> logging.Logger:
    """Retourne un logger rattaché au logger parent CheckIt.AI."""

    project_logger = configure_project_logger()
    normalized_name = normalize_logger_name(name)

    if not normalized_name:
        return project_logger

    child_logger = logging.getLogger(
        f"{PROJECT_LOGGER_NAME}.{normalized_name}"
    )
    child_logger.setLevel(logging.NOTSET)
    child_logger.propagate = True

    close_logger_handlers(child_logger)

    return child_logger


# Réinitialisation contrôlée

def reset_project_logger() -> None:
    """Réinitialise le logger du projet, notamment pour les tests."""

    with _CONFIG_LOCK:
        project_logger = logging.getLogger(PROJECT_LOGGER_NAME)

        close_logger_handlers(project_logger)

        project_logger.setLevel(logging.NOTSET)
        project_logger.propagate = True

        if hasattr(
            project_logger,
            LOGGER_CONFIGURED_ATTRIBUTE
        ):
            delattr(
                project_logger,
                LOGGER_CONFIGURED_ATTRIBUTE
            )