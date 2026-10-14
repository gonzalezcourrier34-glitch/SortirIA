"""Configuration centralisée de la journalisation.

Ce module fournit :

- la configuration globale des logs ;
- la résolution sécurisée du niveau de journalisation ;
- la création de loggers homogènes.

Il ne contient aucune logique métier.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.constants import LOG_DATE_FORMAT, LOG_FORMAT, PROJECT_LOGGER_NAME

# Configuration

DEFAULT_LOG_LEVEL = logging.INFO

VALID_LOG_LEVELS = frozenset(
    {logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL}
)

QUIET_LOGGERS = ("httpcore", "httpx", "pymongo", "urllib3")


# Configuration


def resolve_log_level(level: Any) -> int:
    """Convertit un niveau de log en constante logging valide."""

    if isinstance(level, int):
        return level if level in VALID_LOG_LEVELS else DEFAULT_LOG_LEVEL

    if not isinstance(level, str):
        return DEFAULT_LOG_LEVEL

    resolved_level = getattr(logging, level.strip().upper(), None)

    if isinstance(resolved_level, int) and resolved_level in VALID_LOG_LEVELS:
        return resolved_level

    return DEFAULT_LOG_LEVEL


def configure_logging() -> None:
    """Configure la journalisation globale de l'application."""

    log_level = resolve_log_level(settings.log_level)

    logging.basicConfig(
        level=log_level, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT, force=True
    )

    logging.getLogger(PROJECT_LOGGER_NAME).setLevel(log_level)

    # Réduit le niveau de verbosité des bibliothèques techniques.
    for logger_name in QUIET_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger rattaché au projet."""

    normalized_name = name.strip()

    if not normalized_name:
        return logging.getLogger(PROJECT_LOGGER_NAME)

    if normalized_name == PROJECT_LOGGER_NAME:
        return logging.getLogger(normalized_name)

    if normalized_name.startswith(f"{PROJECT_LOGGER_NAME}."):
        return logging.getLogger(normalized_name)

    return logging.getLogger(f"{PROJECT_LOGGER_NAME}.{normalized_name}")
