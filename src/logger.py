"""Configuration centralisée du système de journalisation CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/logger.py` (commit 93632c5).
"""

from __future__ import annotations

import logging
import sys
from threading import Lock
from typing import Any

from config.constants import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOGGER_CONFIGURED_ATTRIBUTE,
    PROJECT_LOGGER_NAME,
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
    """Convertit une valeur en niveau de journalisation valide.

    Accepte un entier standard (`logging.INFO`...) ou un nom (« debug »,
    « WARNING »...) ; toute autre valeur, booléens compris, donne INFO.

    Parameters
    ----------
    level : Any
        Niveau lu dans la configuration.

    Returns
    -------
    int
        Le niveau `logging` correspondant, INFO à défaut.
    """

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
    """Crée le format commun des journaux.

    Returns
    -------
    logging.Formatter
        Formateur basé sur `LOG_FORMAT` et `LOG_DATE_FORMAT`.
    """

    return logging.Formatter(
        fmt=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT
    )


def create_console_handler(
    log_level: int,
    formatter: logging.Formatter
) -> logging.Handler:
    """Crée le handler de console compatible avec Airflow.

    Écrit sur la sortie standard, que les journaux de tâche capturent.

    Parameters
    ----------
    log_level : int
        Niveau minimal des messages affichés.
    formatter : logging.Formatter
        Format des messages.

    Returns
    -------
    logging.Handler
        Le handler de console configuré.
    """

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(formatter)

    return handler


def create_file_handler(
    log_level: int,
    formatter: logging.Formatter
) -> logging.Handler | None:
    """Crée le handler fichier ou retourne None en cas d'échec.

    Le dossier de `LOG_FILE` est créé si besoin ; le fichier n'est ouvert
    qu'au premier message (`delay=True`).

    Parameters
    ----------
    log_level : int
        Niveau minimal des messages écrits.
    formatter : logging.Formatter
        Format des messages.

    Returns
    -------
    logging.Handler | None
        Le handler fichier, ou `None` si le fichier ne peut pas être préparé.
    """

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
    """Ferme silencieusement un handler.

    Parameters
    ----------
    handler : logging.Handler
        Handler à vider puis fermer ; les erreurs d'E/S sont ignorées.
    """

    try:
        handler.flush()
    except (OSError, ValueError):
        pass

    try:
        handler.close()
    except (OSError, ValueError):
        pass


def close_logger_handlers(logger: logging.Logger) -> None:
    """Retire et ferme proprement tous les handlers d'un logger.

    Parameters
    ----------
    logger : logging.Logger
        Logger à vider de ses handlers.
    """

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        close_handler(handler)


def add_logger_handler(
    logger: logging.Logger,
    handler: logging.Handler | None
) -> bool:
    """Ajoute un handler lorsqu'il a été correctement créé.

    Parameters
    ----------
    logger : logging.Logger
        Logger qui reçoit le handler.
    handler : logging.Handler | None
        Handler à ajouter ; `None` est ignoré.

    Returns
    -------
    bool
        Vrai si le handler a été ajouté.
    """

    if handler is None:
        return False

    logger.addHandler(handler)

    return True


# Configuration du logger principal

def configure_project_logger() -> logging.Logger:
    """Configure une seule fois le logger parent du projet.

    La configuration est protégée par un verrou et marquée par un attribut
    sur le logger, pour rester unique même avec plusieurs threads. Si le
    fichier de log ne peut pas être créé, la journalisation continue dans la
    console avec un avertissement.

    Returns
    -------
    logging.Logger
        Le logger parent `PROJECT_LOGGER_NAME`, configuré.
    """

    project_logger = logging.getLogger(PROJECT_LOGGER_NAME)

    # Sortir tout de suite si la configuration a déjà été faite
    if getattr(
        project_logger,
        LOGGER_CONFIGURED_ATTRIBUTE,
        False
    ):
        return project_logger

    # Revérifier sous verrou : un autre thread a pu configurer entre-temps
    with _CONFIG_LOCK:
        if getattr(
            project_logger,
            LOGGER_CONFIGURED_ATTRIBUTE,
            False
        ):
            return project_logger

        # Fixer le niveau et repartir d'une liste de handlers vide
        log_level = get_log_level(LOG_LEVEL)
        formatter = create_formatter()

        project_logger.setLevel(log_level)
        project_logger.propagate = False

        close_logger_handlers(project_logger)

        # Brancher la console, puis le fichier s'il peut être créé
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

        # Marquer le logger comme configuré et signaler l'absence de fichier
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
    """Normalise le nom d'un logger enfant.

    Retire les préfixes `PROJECT_LOGGER_NAME.` et `src.` ; les alias du logger
    racine (chaîne vide, « src », « __main__ »...) donnent une chaîne vide.

    Parameters
    ----------
    name : Any
        Nom demandé, en général `__name__` du module appelant.

    Returns
    -------
    str
        Le nom relatif au logger du projet, vide pour le logger parent.
    """

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
    """Retourne un logger rattaché au logger parent CheckIt.AI.

    Le logger enfant n'a pas de handler propre : il hérite du niveau et
    transmet ses messages au parent, configuré au premier appel.

    Parameters
    ----------
    name : str | None
        Nom du logger, en général `__name__`. Par défaut `None`, qui donne le
        logger parent.

    Returns
    -------
    logging.Logger
        Le logger enfant, ou le logger parent pour un nom racine.
    """

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
