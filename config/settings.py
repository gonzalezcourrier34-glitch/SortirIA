"""
Centralise les paramètres opérationnels globaux de CheckIt.AI.
Transforme les valeurs de l'environnement en constantes prêtes à l'emploi.
Applique les bornes minimales nécessaires à leur utilisation sûre.
Ne charge aucun secret et ne connaît aucune source déclarée dans le YAML.
Ne définit ni chemins applicatifs ni constantes métier immuables.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `config/settings.py` (commit 93632c5).
Modifié pour SortirIA : User-Agent par défaut renommé en « SortirIA/0.1 ».
"""

from __future__ import annotations

from config.environment import (
    get_boolean_environment_variable,
    get_environment_variable,
    get_float_environment_variable,
    get_integer_environment_variable
)


# HTTP
REQUEST_TIMEOUT = max(get_integer_environment_variable("REQUEST_TIMEOUT", 20), 1)
MAX_RETRIES = max(get_integer_environment_variable("MAX_RETRIES", 3), 0)
RETRY_DELAY_SECONDS = max(
    get_float_environment_variable("RETRY_DELAY_SECONDS", 6.0),
    0.0
)
MAX_RETRY_DELAY_SECONDS = max(
    get_float_environment_variable("MAX_RETRY_DELAY_SECONDS", 30.0),
    RETRY_DELAY_SECONDS
)
HTTP_POOL_CONNECTIONS = max(
    get_integer_environment_variable("HTTP_POOL_CONNECTIONS", 10),
    1
)
HTTP_POOL_MAXSIZE = max(
    get_integer_environment_variable("HTTP_POOL_MAXSIZE", 20),
    1
)
USER_AGENT = (
    get_environment_variable("USER_AGENT", "SortirIA/0.1")
    or "SortirIA/0.1"
).strip()
HTTP_HEADERS = {"User-Agent": USER_AGENT}


# Réseaux sociaux
REDDIT_USER_AGENT = (
    get_environment_variable("REDDIT_USER_AGENT", USER_AGENT)
    or USER_AGENT
).strip()


# Extraction
MAX_ARTICLES_PER_SOURCE = max(
    get_integer_environment_variable("MAX_ARTICLES_PER_SOURCE", 100),
    1
)
MAX_ARTICLE_AGE_DAYS = max(
    get_integer_environment_variable("MAX_ARTICLE_AGE_DAYS", 7),
    0
)


# Validation des articles
MIN_TITLE_LENGTH = max(
    get_integer_environment_variable("MIN_TITLE_LENGTH", 10),
    1
)
MIN_TEXT_LENGTH = max(
    get_integer_environment_variable("MIN_TEXT_LENGTH", 0),
    0
)
MIN_TOTAL_TEXT_LENGTH = max(
    get_integer_environment_variable("MIN_TOTAL_TEXT_LENGTH", 30),
    1
)


# Images
MIN_IMAGE_WIDTH = max(
    get_integer_environment_variable("MIN_IMAGE_WIDTH", 128),
    1
)
MIN_IMAGE_HEIGHT = max(
    get_integer_environment_variable("MIN_IMAGE_HEIGHT", 128),
    1
)
MIN_IMAGE_PIXEL_COUNT = max(
    get_integer_environment_variable("MIN_IMAGE_PIXEL_COUNT", 20_000),
    1
)
MIN_IMAGE_SIZE_BYTES = max(
    get_integer_environment_variable("MIN_IMAGE_SIZE_BYTES", 0),
    0
)
IMAGE_MAX_SIZE_BYTES = max(
    get_integer_environment_variable(
        "IMAGE_MAX_SIZE_BYTES",
        10 * 1024 * 1024
    ),
    1
)
IMAGE_MAX_PIXELS = max(
    get_integer_environment_variable("IMAGE_MAX_PIXELS", 40_000_000),
    1
)
IMAGE_DOWNLOAD_CONNECT_TIMEOUT = max(
    get_integer_environment_variable("IMAGE_DOWNLOAD_CONNECT_TIMEOUT", 5),
    1
)
IMAGE_DOWNLOAD_READ_TIMEOUT = max(
    get_integer_environment_variable("IMAGE_DOWNLOAD_READ_TIMEOUT", 15),
    1
)
IMAGE_DOWNLOAD_CHUNK_SIZE = max(
    get_integer_environment_variable(
        "IMAGE_DOWNLOAD_CHUNK_SIZE",
        64 * 1024
    ),
    1024
)
ALLOW_ANIMATED_IMAGES = get_boolean_environment_variable(
    "ALLOW_ANIMATED_IMAGES",
    False
)
ALLOW_AVIF_IMAGES = get_boolean_environment_variable(
    "ALLOW_AVIF_IMAGES",
    True
)
IMAGE_MAX_FRAME_COUNT = max(
    get_integer_environment_variable("IMAGE_MAX_FRAME_COUNT", 100),
    1
)
IMAGE_HASH_ALGORITHM = (
    get_environment_variable("IMAGE_HASH_ALGORITHM", "sha256")
    or "sha256"
).strip().lower()


# Stockage
STORAGE_LOCK_TIMEOUT = max(
    get_float_environment_variable("STORAGE_LOCK_TIMEOUT", 30.0),
    0.0
)


# Logging
LOG_LEVEL = (
    get_environment_variable("LOG_LEVEL", "INFO")
    or "INFO"
).strip().upper()