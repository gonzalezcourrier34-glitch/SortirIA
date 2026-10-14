"""Constantes techniques partagées du projet CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `config/constants.py` (commit 93632c5).
Modifié pour SortirIA : nom du logger renommé en « sortiria ».
"""

from __future__ import annotations

# Sources
SOURCE_TYPE_RSS = "rss"
SOURCE_TYPE_API = "api"
SOURCE_TYPE_SOCIAL = "social"
SOURCE_TYPE_SCRAPER = "scraper"
SOURCE_TYPE_DATASET = "dataset"
# Calendriers iCal des lieux partenaires (ajout SortirIA, 14/10/2026).
SOURCE_TYPE_ICAL = "ical"

SOURCE_TYPES: frozenset[str] = frozenset({
    SOURCE_TYPE_RSS,
    SOURCE_TYPE_API,
    SOURCE_TYPE_SOCIAL,
    SOURCE_TYPE_SCRAPER,
    SOURCE_TYPE_DATASET,
    SOURCE_TYPE_ICAL,
})

# Rôles des articles
ARTICLE_ROLE_ACQUISITION = "acquisition"
ARTICLE_ROLE_DATASET = "dataset"

ARTICLE_ROLES: frozenset[str] = frozenset({
    ARTICLE_ROLE_ACQUISITION,
    ARTICLE_ROLE_DATASET,
})

# Formats d'images
SUPPORTED_LOCAL_IMAGE_FORMATS: dict[str, frozenset[str]] = {
    ".jpg": frozenset({"JPEG"}),
    ".jpeg": frozenset({"JPEG"}),
    ".png": frozenset({"PNG"}),
    ".webp": frozenset({"WEBP"}),
    ".gif": frozenset({"GIF"}),
    ".avif": frozenset({"AVIF"}),
}

SUPPORTED_DOWNLOAD_IMAGE_FORMATS: dict[str, str] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "AVIF": ".avif",
}

SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    SUPPORTED_LOCAL_IMAGE_FORMATS
)

SUPPORTED_IMAGE_MIME_TYPES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/avif",
})

# Statuts du traitement des images
IMAGE_STATUS_LOCAL_VALID = "local_valid"
IMAGE_STATUS_NOT_REQUESTED = "not_requested"
IMAGE_STATUS_ALREADY_AVAILABLE = "already_available"
IMAGE_STATUS_DOWNLOADED = "downloaded"
IMAGE_STATUS_MISSING_URL = "missing_url"
IMAGE_STATUS_DUPLICATE_URL = "duplicate_url"
IMAGE_STATUS_INVALID_ARTICLE = "invalid_article"
IMAGE_STATUS_BLOCKED_URL = "blocked_url"
IMAGE_STATUS_BLOCKED_REDIRECT = "blocked_redirect"
IMAGE_STATUS_REDIRECT_ERROR = "redirect_error"
IMAGE_STATUS_TIMEOUT = "timeout"
IMAGE_STATUS_HTTP_ERROR = "http_error"
IMAGE_STATUS_TOO_LARGE = "too_large"
IMAGE_STATUS_INVALID_CONTENT = "invalid_content"
IMAGE_STATUS_PROCESSING_ERROR = "processing_error"
IMAGE_STATUS_WRITE_ERROR = "write_error"
IMAGE_STATUS_UNEXPECTED_ERROR = "unexpected_error"
IMAGE_STATUS_INVALID_RESULT = "invalid_result"

IMAGE_REJECTION_STATUSES: frozenset[str] = frozenset({
    IMAGE_STATUS_MISSING_URL,
    IMAGE_STATUS_INVALID_ARTICLE,
    IMAGE_STATUS_BLOCKED_URL,
    IMAGE_STATUS_BLOCKED_REDIRECT,
    IMAGE_STATUS_REDIRECT_ERROR,
    IMAGE_STATUS_TIMEOUT,
    IMAGE_STATUS_HTTP_ERROR,
    IMAGE_STATUS_TOO_LARGE,
    IMAGE_STATUS_INVALID_CONTENT,
    IMAGE_STATUS_PROCESSING_ERROR,
    IMAGE_STATUS_WRITE_ERROR,
    IMAGE_STATUS_UNEXPECTED_ERROR,
    IMAGE_STATUS_INVALID_RESULT,
})

# Statuts de validation des images
IMAGE_VALIDATION_STATUS_VALID = "valid"
IMAGE_VALIDATION_STATUS_INVALID = "invalid"
IMAGE_VALIDATION_STATUS_ERROR = "validation_error"
IMAGE_VALIDATION_STATUS_PENDING = "pending"

# Statuts des extracteurs
EXTRACTOR_STATUS_SUCCESS = "success"
EXTRACTOR_STATUS_PARTIAL_SUCCESS = "partial_success"
EXTRACTOR_STATUS_EMPTY = "empty"
EXTRACTOR_STATUS_DISABLED = "disabled"
EXTRACTOR_STATUS_ROBOTS_DENIED = "robots_denied"
EXTRACTOR_STATUS_FAILED = "failed"

EXTRACTOR_STATUSES: frozenset[str] = frozenset({
    EXTRACTOR_STATUS_SUCCESS,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_DISABLED,
    EXTRACTOR_STATUS_ROBOTS_DENIED,
    EXTRACTOR_STATUS_FAILED,
})

# Raisons d'échec RSS

RSS_ERROR_ACCESS_DENIED = "access_denied"
RSS_ERROR_RATE_LIMITED = "rate_limited"
RSS_ERROR_INVALID_FEED = "invalid_feed"
RSS_ERROR_INVALID_CONTENT_TYPE = "invalid_content_type"
RSS_ERROR_MAINTENANCE = "maintenance"
RSS_ERROR_TIMEOUT = "timeout"
RSS_ERROR_NETWORK = "network_error"
RSS_ERROR_PARSING = "parsing_error"
RSS_ERROR_EXTRACTION_STOPPED = "rss_extraction_stopped"

RSS_ACCESS_DENIED_STATUS_CODES = frozenset({
    401,
    403,
})

RSS_RATE_LIMIT_STATUS_CODES = frozenset({
    429,
})

RSS_MAINTENANCE_STATUS_CODES = frozenset({
    502,
    503,
    504,
})

RSS_ERROR_SERVER = "server_error"
RSS_SERVER_ERROR_STATUS_CODES = frozenset({
    500
})

# Motifs robots.txt
ROBOTS_REASON_ALLOWED = "allowed"
ROBOTS_REASON_DENIED = "robots_denied"
ROBOTS_REASON_UNAVAILABLE = "robots_unavailable"
ROBOTS_REASON_INVALID_URL = "invalid_url"
ROBOTS_REASON_CHECK_ERROR = "robots_check_error"

ROBOTS_REASONS: frozenset[str] = frozenset({
    ROBOTS_REASON_ALLOWED,
    ROBOTS_REASON_DENIED,
    ROBOTS_REASON_UNAVAILABLE,
    ROBOTS_REASON_INVALID_URL,
    ROBOTS_REASON_CHECK_ERROR,
})

# HTTP
DOWNLOAD_CHUNK_SIZE = 8192

RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({
    429,
    500,
    502,
    503,
    504,
})

RETRYABLE_HTTP_METHODS: frozenset[str] = frozenset({"GET"})

# Paramètres de suivi retirés des URL
TRACKING_QUERY_PARAMETERS: frozenset[str] = frozenset({
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
})

# Valeurs supprimées ou absentes
DELETED_CONTENT_VALUES: frozenset[str] = frozenset({
    "[deleted]",
    "[removed]",
    "deleted",
    "removed",
    "contenu supprimé",
    "contenu indisponible",
})

MISSING_TEXT_VALUES: frozenset[str] = frozenset({
    "",
    "none",
    "null",
    "nan",
    "nat",
    "na",
    "n/a",
    "<na>",
})

# Compatibilité Windows
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})

# Erreurs API génériques
GENERIC_API_ERROR_MESSAGES: frozenset[str] = frozenset({
    "error",
    "failed",
    "failure",
    "invalid",
})

# Logs
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
PROJECT_LOGGER_NAME = "sortiria"
LOGGER_CONFIGURED_ATTRIBUTE = "_sortiria_logger_configured"

# Stockage
FILE_LOCK_SUFFIX = ".lock"
TEMPORARY_FILE_SUFFIX = ".tmp"
FILE_LOCK_METADATA_ENCODING = "utf-8"
FILE_LOCK_PID_FIELD = "pid"
FILE_LOCK_CREATED_AT_FIELD = "created_at"

# ERREUR API
# Codes machine d'authentification connus
API_QUOTA_ERROR_PATTERNS: tuple[str, ...] = (
    "monthly usage limit",
    "daily usage limit",
    "weekly usage limit",
    "usage limit has been reached",
    "usage limit reached",
    "quota exceeded",
    "quota has been exceeded",
    "quota exhausted",
    "quota limit reached",
    "credits exhausted",
    "credit limit reached",
    "insufficient credits",
    "exceeded your assigned api credits",
    "assigned api credits",
    "api credits exceeded",
    "maximum number of requests",
    "maximum requests reached",
    "request limit reached",
    "reached your request limit",
    "you have reached your request limit",
    "request limit for today",
    "request limit for this month",
    "subscription limit reached",
    "plan limit reached"
)

API_AUTHENTICATION_ERROR_PATTERNS: tuple[str, ...] = (
    "invalid api key",
    "api key invalid",
    "api key is invalid",
    "api key not valid",
    "api key is not valid",
    "invalid access key",
    "access key invalid",
    "invalid token",
    "token invalid",
    "token has expired",
    "expired token",
    "missing api key",
    "missing access key",
    "missing token",
    "invalid credentials",
    "credentials are invalid",
    "unauthorized",
    "unauthenticated",
    "authentication failed",
    "authentication required",
    "permission denied",
    "access denied",
    "subscription inactive",
    "subscription expired",
    "account suspended",
    "account disabled",
    "account inactive"
)

API_AUTHENTICATION_ERROR_CODES: tuple[str, ...] = (
    "API_KEY_INVALID",
    "INVALID_API_KEY",
    "INVALID_ACCESS_KEY",
    "INVALID_TOKEN",
    "TOKEN_INVALID",
    "TOKEN_EXPIRED",
    "AUTHENTICATION_FAILED",
    "AUTHENTICATION_REQUIRED",
    "UNAUTHENTICATED",
    "UNAUTHORIZED",
    "ACCESS_DENIED",
    "PERMISSION_DENIED",
    "CREDENTIALS_INVALID",
    "INVALID_CREDENTIALS",
    "MISSING_CREDENTIALS"
)

API_QUOTA_ERROR_CODES: tuple[str, ...] = (
    "QUOTA_EXCEEDED",
    "RATE_LIMIT_EXCEEDED",
    "RESOURCE_EXHAUSTED",
    "TOO_MANY_REQUESTS",
    "USAGE_LIMIT_EXCEEDED",
    "CREDITS_EXHAUSTED"
)

API_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "access_key",
    "access_token",
    "auth_token",
    "authorization",
    "client_secret",
    "secret",
    "token"
)

API_RATE_LIMIT_PATTERNS: tuple[str, ...] = (
    "rate limit exceeded",
    "too many requests",
    "one every 5 seconds",
    "high-traffic",
    "too many api requests",
)
# Normalisation des langues

LANGUAGE_MAPPING: dict[str, str] = {
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
    "fr-fr": "fr",
    "français": "fr",
    "french": "fr"
}

# Normalisation des labels

LABEL_MAPPING: dict[str, str] = {
    "0": "real",
    "1": "fake",
    "fake": "fake",
    "real": "real"
}

EMPTY_CATEGORY_VALUES = frozenset({
    "",
    "none",
    "null",
    "unknown",
    "inconnue"
})

DEFAULT_DATASET_ROLE = "acquisition"

# Articles - Valeurs supprimées et valeurs par défaut

DELETED_ARTICLE_VALUES = frozenset({
    "[deleted]",
    "[removed]",
    "deleted",
    "removed",
    "contenu supprimé",
    "contenu indisponible"
})

DEFAULT_ARTICLE_IDENTIFIER = "article_inconnu"