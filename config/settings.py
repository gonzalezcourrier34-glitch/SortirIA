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
    get_environment_variable,
    get_float_environment_variable,
    get_integer_environment_variable,
)

# HTTP
REQUEST_TIMEOUT = max(get_integer_environment_variable("REQUEST_TIMEOUT", 20), 1)
MAX_RETRIES = max(get_integer_environment_variable("MAX_RETRIES", 3), 0)
RETRY_DELAY_SECONDS = max(
    get_float_environment_variable("RETRY_DELAY_SECONDS", 6.0),
    0.0
)
USER_AGENT = (
    get_environment_variable("USER_AGENT", "SortirIA/0.1")
    or "SortirIA/0.1"
).strip()
HTTP_HEADERS = {"User-Agent": USER_AGENT}


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


# Logging
LOG_LEVEL = (
    get_environment_variable("LOG_LEVEL", "INFO")
    or "INFO"
).strip().upper()