"""Téléchargement sécurisé des images associées aux articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/images/image_downloader.py` (commit 93632c5).

Ce module prend en charge :

- la validation des URL distantes et la protection SSRF ;
- les redirections HTTP contrôlées ;
- la réutilisation des sessions HTTP ;
- les nouvelles tentatives limitées aux erreurs temporaires ;
- le téléchargement en streaming avec taille maximale ;
- la détection des contenus HTML déguisés ;
- l'inspection technique des images téléchargées ;
- la récupération du hash SHA-256 et de la durée de téléchargement ;
- l'écriture transactionnelle et le verrouillage des fichiers ;
- la mise à jour des statuts et métadonnées de téléchargement ;
- la déduplication des URL au sein d'un même lot ;
- l'agrégation des résultats et erreurs du lot.

Les opérations Pillow sont déléguées à image_utils et les opérations d'écriture
transactionnelle à storage_utils.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import local
from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.constants import (
    IMAGE_STATUS_ALREADY_AVAILABLE,
    IMAGE_STATUS_BLOCKED_REDIRECT,
    IMAGE_STATUS_BLOCKED_URL,
    IMAGE_STATUS_DOWNLOADED,
    IMAGE_STATUS_HTTP_ERROR,
    IMAGE_STATUS_DUPLICATE_URL,
    IMAGE_STATUS_INVALID_ARTICLE,
    IMAGE_STATUS_INVALID_CONTENT,
    IMAGE_STATUS_MISSING_URL,
    IMAGE_STATUS_NOT_REQUESTED,
    IMAGE_STATUS_PROCESSING_ERROR,
    IMAGE_STATUS_REDIRECT_ERROR,
    IMAGE_STATUS_TIMEOUT,
    IMAGE_STATUS_TOO_LARGE,
    IMAGE_STATUS_UNEXPECTED_ERROR,
    IMAGE_STATUS_WRITE_ERROR,
    RETRYABLE_HTTP_METHODS,
    RETRYABLE_HTTP_STATUS_CODES,
    SUPPORTED_DOWNLOAD_IMAGE_FORMATS
)
from config.paths import IMAGES_DIR
from config.settings import (
    HTTP_POOL_CONNECTIONS,
    HTTP_POOL_MAXSIZE,
    IMAGE_DOWNLOAD_CHUNK_SIZE,
    IMAGE_DOWNLOAD_CONNECT_TIMEOUT,
    IMAGE_DOWNLOAD_READ_TIMEOUT,
    IMAGE_MAX_SIZE_BYTES,
    MAX_RETRIES,
    MAX_RETRY_DELAY_SECONDS,
    MIN_IMAGE_HEIGHT,
    MIN_IMAGE_PIXEL_COUNT,
    MIN_IMAGE_SIZE_BYTES,
    MIN_IMAGE_WIDTH,
    RETRY_DELAY_SECONDS,
    USER_AGENT
)
from src.article.processing.article_cleaner import normalize_value
from src.images.image_utils import (
    ImageMetadata,
    inspect_image,
    is_image_large_enough
)
from src.logger import get_logger
from src.storage.files.storage_utils import (
    FileLock,
    atomic_replace,
    create_temporary_path,
    ensure_directory_exists,
    remove_file_if_exists
)
from src.utils.path_utils import normalize_source_name
from src.utils.url_utils import is_valid_http_url

logger = get_logger(__name__)

_HTTP_LOCAL = local()
MAX_IMAGE_REDIRECTS = 3
MAX_IMAGE_FILENAME_ID_LENGTH = 120
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
ALLOWED_REMOTE_PORTS = {80, 443}
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal"
}
HTML_PREFIXES = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<body",
    b"<?xml"
)
QUOTA_HTTP_STATUS_CODES = frozenset({429})
NON_RETRYABLE_HTTP_STATUS_CODES = frozenset({400, 401, 403, 404, 410, 422, 429})
IMAGE_STATUS_NOT_FOUND = "not_found"
IMAGE_STATUS_FORBIDDEN = "forbidden"
IMAGE_STATUS_QUOTA_EXCEEDED = "quota_exceeded"
IMAGE_STATUS_SERVER_ERROR = "server_error"

DOWNLOAD_IMAGE_FORMATS = {
    **SUPPORTED_DOWNLOAD_IMAGE_FORMATS,
    "AVIF": ".avif"
}
IMAGE_CACHE_FIELDS = (
    "image_path",
    "image_format",
    "image_mime_type",
    "image_width",
    "image_height",
    "image_aspect_ratio",
    "image_mode",
    "image_size_bytes",
    "image_file_hash",
    "image_download_duration_ms",
    "image_downloaded_at",
    "image_metadata"
)


class UnsafeImageUrlError(ValueError):
    """Signale qu'une URL ou une redirection distante est interdite."""

    def __init__(
        self,
        message: str,
        status: str = IMAGE_STATUS_BLOCKED_URL
    ) -> None:
        super().__init__(message)
        self.status = status


class ImageTooLargeError(ValueError):
    """Signale que la taille maximale autorisée a été dépassée."""


class InvalidImageContentError(ValueError):
    """Signale que le contenu distant ne correspond pas à une image valide."""


class ImageHttpError(requests.HTTPError):
    """Signale une réponse HTTP classifiée pour une image."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


# Sessions HTTP

def create_http_session() -> requests.Session:
    """Crée une session HTTP avec pool de connexions et retries temporaires."""

    retries = max(int(MAX_RETRIES), 0)
    retry_strategy = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        other=0,
        allowed_methods=frozenset(RETRYABLE_HTTP_METHODS),
        status_forcelist=sorted(
            set(RETRYABLE_HTTP_STATUS_CODES) - NON_RETRYABLE_HTTP_STATUS_CODES
        ),
        backoff_factor=max(float(RETRY_DELAY_SECONDS), 0.0),
        backoff_max=max(float(MAX_RETRY_DELAY_SECONDS), 0.0),
        respect_retry_after_header=True,
        raise_on_status=False
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=max(int(HTTP_POOL_CONNECTIONS), 1),
        pool_maxsize=max(int(HTTP_POOL_MAXSIZE), 1)
    )
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.5"
    })
    return session


def get_http_session() -> requests.Session:
    """Retourne la session HTTP du thread courant."""

    session = getattr(_HTTP_LOCAL, "session", None)

    if session is None:
        session = create_http_session()
        _HTTP_LOCAL.session = session

    return session


# Sécurité des URL

def is_forbidden_ip_address(address: str) -> bool:
    """Indique si une adresse IP cible un réseau interne ou protégé."""

    try:
        ip_address = ipaddress.ip_address(address)
    except ValueError:
        return True

    return (
        ip_address.is_private
        or ip_address.is_loopback
        or ip_address.is_link_local
        or ip_address.is_multicast
        or ip_address.is_reserved
        or ip_address.is_unspecified
    )


def resolve_hostname_addresses(hostname: str) -> set[str]:
    """Résout un nom d'hôte en adresses IPv4 et IPv6."""

    if not hostname:
        return set()

    try:
        address_information = socket.getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM
        )
    except (socket.gaierror, OSError) as error:
        logger.debug("Impossible de résoudre l'hôte %s : %s", hostname, error)
        return set()

    return {data[4][0] for data in address_information if data[4]}


def validate_remote_image_url(url: Any) -> tuple[bool, str]:
    """Valide une URL HTTP distante et bloque les destinations internes."""

    normalized_url = normalize_value(url)

    if not is_valid_http_url(normalized_url):
        return False, "URL HTTP ou HTTPS invalide."

    try:
        parsed_url = urlparse(normalized_url)
        hostname = normalize_value(parsed_url.hostname).lower()
        port = parsed_url.port
    except (TypeError, ValueError):
        return False, "URL impossible à analyser."

    if not hostname:
        return False, "Nom d'hôte absent."
    if parsed_url.username is not None or parsed_url.password is not None:
        return False, "Identifiants intégrés à l'URL interdits."
    if port is not None and port not in ALLOWED_REMOTE_PORTS:
        return False, f"Port distant interdit : {port}"
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        return False, f"Hôte local interdit : {hostname}"

    try:
        direct_ip = ipaddress.ip_address(hostname)
    except ValueError:
        direct_ip = None

    if direct_ip is not None:
        if is_forbidden_ip_address(str(direct_ip)):
            return False, f"Adresse IP interdite : {direct_ip}"
        return True, ""

    addresses = resolve_hostname_addresses(hostname)

    if not addresses:
        return False, f"Hôte impossible à résoudre : {hostname}"

    forbidden_addresses = sorted(
        address for address in addresses if is_forbidden_ip_address(address)
    )

    if forbidden_addresses:
        return (
            False,
            "Destination interne interdite : "
            + ", ".join(forbidden_addresses)
        )

    return True, ""


# Redirections et réponses HTTP

def get_redirect_url(response: requests.Response, current_url: str) -> str:
    """Construit l'URL absolue indiquée par une redirection."""

    location = normalize_value(response.headers.get("Location"))
    return urljoin(current_url, location) if location else ""


def request_image_response(
    session: requests.Session,
    image_url: str,
    referer: str = ""
) -> requests.Response:
    """Effectue la requête en validant chaque destination et redirection."""

    current_url = image_url
    headers = {"Referer": referer} if is_valid_http_url(referer) else None
    timeout = (
        max(int(IMAGE_DOWNLOAD_CONNECT_TIMEOUT), 1),
        max(int(IMAGE_DOWNLOAD_READ_TIMEOUT), 1)
    )

    for redirect_count in range(MAX_IMAGE_REDIRECTS + 1):
        is_safe, reason = validate_remote_image_url(current_url)

        if not is_safe:
            raise UnsafeImageUrlError(f"URL distante bloquée : {reason}")

        response = session.get(
            current_url,
            headers=headers,
            timeout=timeout,
            stream=True,
            allow_redirects=False
        )

        if response.status_code not in REDIRECT_STATUS_CODES:
            return response

        redirect_url = get_redirect_url(response, current_url)
        response.close()

        if not redirect_url:
            raise requests.TooManyRedirects("Redirection sans en-tête Location.")
        if redirect_count >= MAX_IMAGE_REDIRECTS:
            raise requests.TooManyRedirects(
                f"Plus de {MAX_IMAGE_REDIRECTS} redirections."
            )

        is_safe, reason = validate_remote_image_url(redirect_url)

        if not is_safe:
            raise UnsafeImageUrlError(
                f"Redirection bloquée vers {redirect_url} : {reason}",
                status=IMAGE_STATUS_BLOCKED_REDIRECT
            )

        logger.debug(
            "Redirection d'image validée : %s -> %s",
            current_url,
            redirect_url
        )
        current_url = redirect_url

    raise requests.TooManyRedirects("Nombre maximal de redirections dépassé.")


def classify_http_response(response: requests.Response) -> None:
    """Lève une erreur métier correspondant au statut HTTP reçu."""

    status_code = int(response.status_code)

    if 200 <= status_code < 300:
        return
    if status_code == 404:
        raise ImageHttpError(
            IMAGE_STATUS_NOT_FOUND,
            "Image distante introuvable (HTTP 404)."
        )
    if status_code in {401, 403}:
        raise ImageHttpError(
            IMAGE_STATUS_FORBIDDEN,
            f"Accès à l'image refusé (HTTP {status_code})."
        )
    if status_code in QUOTA_HTTP_STATUS_CODES:
        raise ImageHttpError(
            IMAGE_STATUS_QUOTA_EXCEEDED,
            f"Quota distant dépassé (HTTP {status_code})."
        )
    if status_code >= 500:
        raise ImageHttpError(
            IMAGE_STATUS_SERVER_ERROR,
            f"Erreur du serveur d'images (HTTP {status_code})."
        )

    raise ImageHttpError(
        IMAGE_STATUS_HTTP_ERROR,
        f"Erreur HTTP pendant le téléchargement (HTTP {status_code})."
    )


def get_content_type(response: requests.Response) -> str:
    """Retourne le type MIME principal annoncé par le serveur."""

    return (
        response.headers.get("Content-Type", "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )


def get_content_length(response: requests.Response) -> int:
    """Retourne la taille positive annoncée par le serveur."""

    try:
        return max(int(response.headers.get("Content-Length", "0")), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def validate_response_headers(response: requests.Response) -> None:
    """Valide le type et la taille annoncés par la réponse HTTP."""

    content_type = get_content_type(response)
    content_length = get_content_length(response)
    maximum_size = max(int(IMAGE_MAX_SIZE_BYTES), 1)

    if content_type and not (
        content_type.startswith("image/")
        or content_type == "application/octet-stream"
    ):
        raise InvalidImageContentError(f"Content-Type inattendu : {content_type}")

    if content_length > maximum_size:
        raise ImageTooLargeError(
            f"Taille annoncée : {content_length} octets, limite : {maximum_size}."
        )


def looks_like_html(content: bytes) -> bool:
    """Détecte une page HTML ou XML dans le début d'un contenu."""

    normalized_content = content.lstrip().lower()
    return any(normalized_content.startswith(prefix) for prefix in HTML_PREFIXES)


# Normalisation et métadonnées

def normalize_article_id(article_id: Any) -> str:
    """Transforme un identifiant en fragment de nom de fichier sûr."""

    normalized_id = normalize_value(article_id)

    if not normalized_id:
        return ""

    normalized_id = re.sub(r"[^\w-]+", "_", normalized_id)
    normalized_id = re.sub(r"_+", "_", normalized_id).strip("_")
    return normalized_id[:MAX_IMAGE_FILENAME_ID_LENGTH]


def normalize_image_url(url: Any) -> str:
    """Normalise une URL d'image pour la déduplication du lot."""

    normalized_url = normalize_value(url)

    if not normalized_url:
        return ""

    try:
        parsed = urlparse(normalized_url)
    except (TypeError, ValueError):
        return normalized_url

    scheme = parsed.scheme.lower()
    hostname = normalize_value(parsed.hostname).lower()
    port = parsed.port
    netloc = hostname

    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"

    return parsed._replace(
        scheme=scheme,
        netloc=netloc,
        fragment=""
    ).geturl()


def set_download_status(
    article: dict[str, Any],
    status: str,
    error: str = ""
) -> None:
    """Met à jour le statut et l'erreur du téléchargement."""

    article["image_download_status"] = normalize_value(status)
    article["image_download_error"] = normalize_value(error)


def get_article_referer(article: dict[str, Any]) -> str:
    """Retourne l'URL d'article utilisable comme en-tête Referer."""

    for field in ("url", "article_url", "source_url"):
        referer = normalize_value(article.get(field))

        if is_valid_http_url(referer):
            return referer

    return ""


def get_current_datetime_iso() -> str:
    """Retourne la date courante UTC au format ISO 8601."""

    return datetime.now(timezone.utc).isoformat()


def apply_image_metadata(
    article: dict[str, Any],
    image_path: Path,
    metadata: ImageMetadata,
    download_duration_ms: int
) -> None:
    """Ajoute les métadonnées d'une image téléchargée à un article."""

    downloaded_at = get_current_datetime_iso()
    existing_metadata = article.get("image_metadata")
    image_metadata = (
        dict(existing_metadata)
        if isinstance(existing_metadata, dict)
        else {}
    )

    image_metadata.update(metadata.to_dict())
    image_metadata["download_duration_ms"] = download_duration_ms
    image_metadata["downloaded_at"] = downloaded_at

    article["image_path"] = str(image_path)
    article["image_format"] = metadata.format
    article["image_mime_type"] = metadata.mime_type
    article["image_width"] = metadata.width
    article["image_height"] = metadata.height
    article["image_aspect_ratio"] = metadata.aspect_ratio
    article["image_mode"] = metadata.mode
    article["image_size_bytes"] = metadata.file_size
    article["image_file_hash"] = metadata.file_hash
    article["image_download_duration_ms"] = download_duration_ms
    article["image_downloaded_at"] = downloaded_at
    article["image_metadata"] = image_metadata


# Écriture et inspection

def stream_response_to_file(
    response: requests.Response,
    temporary_path: Path
) -> int:
    """Écrit la réponse en streaming avec contrôle continu de sa taille."""

    maximum_size = max(int(IMAGE_MAX_SIZE_BYTES), 1)
    chunk_size = max(int(IMAGE_DOWNLOAD_CHUNK_SIZE), 1024)
    downloaded_size = 0
    prefix = bytearray()

    with temporary_path.open("wb") as temporary_file:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue

            downloaded_size += len(chunk)

            if downloaded_size > maximum_size:
                raise ImageTooLargeError(
                    f"Taille maximale dépassée : plus de {maximum_size} octets."
                )

            if len(prefix) < 512:
                prefix.extend(chunk[: 512 - len(prefix)])

                if looks_like_html(bytes(prefix)):
                    raise InvalidImageContentError(
                        "La ressource distante contient une page HTML ou XML."
                    )

            temporary_file.write(chunk)

    if downloaded_size <= 0:
        raise InvalidImageContentError("Contenu distant vide.")

    minimum_size = max(int(MIN_IMAGE_SIZE_BYTES), 0)

    if minimum_size and downloaded_size < minimum_size:
        raise InvalidImageContentError(
            f"Image trop légère : {downloaded_size} octets, minimum {minimum_size}."
        )

    return downloaded_size


def inspect_downloaded_image(temporary_path: Path) -> ImageMetadata:
    """Valide l'image téléchargée et retourne ses métadonnées techniques."""

    metadata = inspect_image(temporary_path)

    if metadata is None:
        raise InvalidImageContentError(
            "Le contenu téléchargé n'est pas une image valide."
        )

    extension = DOWNLOAD_IMAGE_FORMATS.get(metadata.format)

    if not isinstance(extension, str) or not extension.startswith("."):
        raise InvalidImageContentError(
            f"Format d'image non pris en charge : {metadata.format or 'inconnu'}."
        )

    if not is_image_large_enough(
        metadata,
        min_width=MIN_IMAGE_WIDTH,
        min_height=MIN_IMAGE_HEIGHT,
        min_pixel_count=MIN_IMAGE_PIXEL_COUNT
    ):
        raise InvalidImageContentError(
            "Image trop petite : "
            f"{metadata.width}x{metadata.height} pixels, "
            f"minimum {MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT}."
        )

    return metadata


def build_image_path(
    target_directory: Path,
    source: str,
    article_id: str,
    extension: str
) -> Path:
    """Construit le chemin définitif d'une image validée."""

    return target_directory / f"{source}_{article_id}{extension}"


# Téléchargement unitaire

def download_image(article: Any) -> dict[str, Any]:
    """Télécharge de manière sécurisée l'image distante d'un article."""

    if not isinstance(article, dict):
        logger.debug(
            "Article ignoré pendant le téléchargement : format %s.",
            type(article).__name__
        )
        return {}

    enriched_article = article.copy()
    set_download_status(enriched_article, IMAGE_STATUS_NOT_REQUESTED)

    if normalize_value(enriched_article.get("image_path")):
        set_download_status(enriched_article, IMAGE_STATUS_ALREADY_AVAILABLE)
        return enriched_article

    enriched_article["image_path"] = ""
    article_id = normalize_article_id(enriched_article.get("id"))
    image_url = normalize_value(enriched_article.get("image_url"))
    source = normalize_source_name(enriched_article.get("source", "unknown"))

    if not article_id:
        set_download_status(
            enriched_article,
            IMAGE_STATUS_INVALID_ARTICLE,
            "Identifiant article absent."
        )
        logger.debug("Article sans identifiant : image ignorée.")
        return enriched_article

    if not image_url:
        set_download_status(enriched_article, IMAGE_STATUS_MISSING_URL)
        logger.debug("Aucune URL d'image pour l'article %s.", article_id)
        return enriched_article

    is_safe_url, security_error = validate_remote_image_url(image_url)

    if not is_safe_url:
        set_download_status(
            enriched_article,
            IMAGE_STATUS_BLOCKED_URL,
            security_error
        )
        logger.debug(
            "URL d'image bloquée pour l'article %s : %s",
            article_id,
            security_error
        )
        return enriched_article

    target_directory = ensure_directory_exists(IMAGES_DIR / source)
    temporary_path = create_temporary_path(
        target_directory / f"{source}_{article_id}.download"
    )
    download_started_at = perf_counter()

    try:
        response = request_image_response(
            get_http_session(),
            image_url,
            referer=get_article_referer(enriched_article)
        )

        with response:
            classify_http_response(response)
            validate_response_headers(response)
            stream_response_to_file(response, temporary_path)

        metadata = inspect_downloaded_image(temporary_path)
        extension = DOWNLOAD_IMAGE_FORMATS[metadata.format].lower()
        image_path = build_image_path(
            target_directory,
            source,
            article_id,
            extension
        )

        with FileLock(image_path):
            atomic_replace(temporary_path, image_path)

        final_metadata = inspect_image(image_path)

        if final_metadata is None:
            remove_file_if_exists(image_path)
            raise InvalidImageContentError(
                "L'image finale est invalide après son enregistrement."
            )

        download_duration_ms = max(
            round((perf_counter() - download_started_at) * 1000),
            0
        )
        apply_image_metadata(
            enriched_article,
            image_path,
            final_metadata,
            download_duration_ms
        )
        set_download_status(enriched_article, IMAGE_STATUS_DOWNLOADED)

        logger.debug(
            "Image téléchargée pour l'article %s en %s ms : %s",
            article_id,
            download_duration_ms,
            image_path
        )

    except UnsafeImageUrlError as error:
        set_download_status(enriched_article, error.status, str(error))
        logger.debug(
            "Téléchargement bloqué pour l'article %s : %s",
            article_id,
            error
        )
    except ImageTooLargeError as error:
        set_download_status(enriched_article, IMAGE_STATUS_TOO_LARGE, str(error))
        logger.debug(
            "Image trop volumineuse pour l'article %s : %s",
            article_id,
            error
        )
    except InvalidImageContentError as error:
        set_download_status(enriched_article, IMAGE_STATUS_INVALID_CONTENT, str(error))
        logger.debug(
            "Image rejetée pour l'article %s : %s",
            article_id,
            error
        )
    except requests.TooManyRedirects as error:
        set_download_status(enriched_article, IMAGE_STATUS_REDIRECT_ERROR, str(error))
        logger.debug(
            "Erreur de redirection pour l'image %s : %s",
            article_id,
            error
        )
    except requests.Timeout as error:
        set_download_status(enriched_article, IMAGE_STATUS_TIMEOUT, str(error))
        logger.debug(
            "Délai dépassé pendant le téléchargement de l'image %s : %s",
            article_id,
            error
        )
    except ImageHttpError as error:
        set_download_status(enriched_article, error.status, str(error))
        logger.debug(
            "Réponse HTTP classifiée pour l'image %s : %s",
            article_id,
            error
        )
    except requests.RequestException as error:
        set_download_status(enriched_article, IMAGE_STATUS_HTTP_ERROR, str(error))
        logger.debug(
            "Erreur HTTP pendant le téléchargement de l'image %s : %s",
            article_id,
            error
        )
    except ValueError as error:
        set_download_status(enriched_article, IMAGE_STATUS_PROCESSING_ERROR, str(error))
        logger.debug(
            "Erreur de traitement de l'image %s : %s",
            article_id,
            error
        )
    except OSError as error:
        set_download_status(enriched_article, IMAGE_STATUS_WRITE_ERROR, str(error))
        logger.debug(
            "Impossible d'enregistrer l'image de l'article %s : %s",
            article_id,
            error
        )
    except Exception as error:
        set_download_status(
            enriched_article,
            IMAGE_STATUS_UNEXPECTED_ERROR,
            str(error)
        )
        logger.debug(
            "Erreur inattendue pour l'image de l'article %s : %s",
            article_id,
            error,
            exc_info=True
        )
    finally:
        remove_file_if_exists(temporary_path)

    return enriched_article


# Traitement d'un lot

def copy_cached_image_data(
    article: dict[str, Any],
    cached_article: dict[str, Any]
) -> dict[str, Any]:
    """Recopie les métadonnées d'une image déjà traitée vers un autre article."""

    enriched_article = article.copy()

    for field in IMAGE_CACHE_FIELDS:
        if field in cached_article:
            enriched_article[field] = cached_article[field]

    return enriched_article


def format_download_status_summary(statuses: Counter[str]) -> str:
    """Construit un résumé compact des statuts de téléchargement."""

    return ", ".join(
        f"{status or 'sans_statut'}={count}"
        for status, count in statuses.most_common()
    ) or "aucun"


def download_images_for_articles(articles: list[Any]) -> list[dict[str, Any]]:
    """Télécharge les images, déduplique les URL et agrège les résultats."""

    if not isinstance(articles, list):
        logger.warning(
            "Collection invalide pour le téléchargement des images : %s.",
            type(articles).__name__
        )
        return []

    if not articles:
        logger.info("Téléchargement images : aucun article à traiter.")
        return []

    enriched_articles: list[dict[str, Any]] = []
    image_cache: dict[str, dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    invalid_items = 0
    unexpected_errors = 0

    for article in articles:
        if not isinstance(article, dict):
            invalid_items += 1
            statuses[IMAGE_STATUS_INVALID_ARTICLE] += 1
            continue

        normalized_url = normalize_image_url(article.get("image_url"))

        if normalized_url and normalized_url in image_cache:
            cached_article = image_cache[normalized_url]
            enriched_article = copy_cached_image_data(article, cached_article)
            cached_path = normalize_value(enriched_article.get("image_path"))

            if cached_path:
                set_download_status(
                    enriched_article,
                    IMAGE_STATUS_DUPLICATE_URL,
                    "Image réutilisée depuis une URL déjà traitée dans ce lot."
                )
            else:
                set_download_status(
                    enriched_article,
                    normalize_value(
                        cached_article.get("image_download_status")
                    ),
                    normalize_value(
                        cached_article.get("image_download_error")
                    )
                )

            enriched_articles.append(enriched_article)
            duplicate_status = normalize_value(
                enriched_article.get("image_download_status")
            )
            statuses[duplicate_status or IMAGE_STATUS_NOT_REQUESTED] += 1
            continue

        try:
            enriched_article = download_image(article)
        except Exception as error:
            unexpected_errors += 1
            statuses[IMAGE_STATUS_UNEXPECTED_ERROR] += 1
            logger.debug(
                "Erreur inattendue pendant le téléchargement d'une image : %s",
                error,
                exc_info=True
            )
            continue

        if not enriched_article:
            statuses[IMAGE_STATUS_INVALID_ARTICLE] += 1
            continue

        enriched_articles.append(enriched_article)
        status = normalize_value(enriched_article.get("image_download_status"))
        statuses[status or IMAGE_STATUS_NOT_REQUESTED] += 1

        if normalized_url:
            image_cache[normalized_url] = enriched_article.copy()

    available_count = sum(
        bool(normalize_value(article.get("image_path")))
        for article in enriched_articles
    )
    downloaded_count = statuses[IMAGE_STATUS_DOWNLOADED]
    duplicate_count = statuses[IMAGE_STATUS_DUPLICATE_URL]

    logger.info(
        "Téléchargement images : analysées=%s, conservées=%s, téléchargées=%s, "
        "disponibles=%s, URL dupliquées=%s, invalides=%s, erreurs=%s, statuts=[%s].",
        len(articles),
        len(enriched_articles),
        downloaded_count,
        available_count,
        duplicate_count,
        invalid_items,
        unexpected_errors,
        format_download_status_summary(statuses)
    )
    return enriched_articles