"""Implémentation technique de l'extraction RSS et Atom.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/rss/rss_extractor.py` (commit 93632c5).
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.paths import SOURCES_FILE
from config.settings import (
    HTTP_HEADERS,
    MAX_ARTICLE_AGE_DAYS,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    RETRY_DELAY_SECONDS
)
from src.article.processing.article_cleaner import clean_text
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.rss.rss_adapter import RssAdapter

from src.logger import get_logger
from src.utils.date_utils import (
    convert_date_to_iso,
    get_current_datetime,
    parse_datetime
)
from src.extractors.rss.rss_engine import (
    RssExtractor,
    RssInvalidContentTypeError,
    RssInvalidFeedError,
    RssNetworkError,
    RssParsingError,
    RssRequestError,
    RssTimeoutError,
    increment_rss_request_count,
    raise_if_fatal_rss_error
)
from src.utils.value_utils import normalize_value
from src.utils.extractor_utils import build_standard_article
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.url_utils import canonicalize_url, is_valid_http_url

logger = get_logger(__name__)


# Contrat technique RSS

RSS_HTML_TEXT_FIELDS: tuple[str, ...] = ("summary", "description")
RSS_IMAGE_HTML_FIELDS: tuple[str, ...] = ("summary", "description")
RSS_XML_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/atom+xml",
    "application/rdf+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml"
})
RSS_HTML_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/xhtml+xml",
    "text/html"
})
UTF8_BOM = b"\xef\xbb\xbf"
LEADING_XML_WHITESPACE_PATTERN = re.compile(br"^[ \t\r\n]+(?=<\?xml\b)")
HTML_PREFIX_PATTERN = re.compile(
    br"^[ \t\r\n]*(?:<!doctype[ \t\r\n]+html\b|<html\b)",
    flags=re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class DownloadedFeed:
    """Contenu HTTP brut d'un flux et métadonnées utiles au parsing."""

    content: bytes
    content_type: str
    encoding: str
    final_url: str


# Nettoyage et URL

def clean_html(value: Any) -> str:
    """Nettoie un fragment HTML ou textuel."""

    return clean_text(value)


def resolve_http_url(
    value: Any,
    base_url: Any = "",
    canonicalize: bool = False
) -> str:
    """Résout une URL relative et vérifie son schéma HTTP."""

    raw_url = html.unescape(normalize_value(value))
    if not raw_url:
        return ""

    try:
        resolved_url = urljoin(normalize_value(base_url), raw_url)
    except (TypeError, ValueError):
        return ""

    if not is_valid_http_url(resolved_url):
        return ""
    return canonicalize_url(resolved_url) if canonicalize else resolved_url


# Lecture des champs RSS

def get_entry_url(entry: Mapping[str, Any]) -> str:
    """Retourne le lien principal d'une entrée RSS ou Atom."""

    direct_url = resolve_http_url(entry.get("link"), canonicalize=True)
    if direct_url:
        return direct_url

    links = entry.get("links", [])
    if not isinstance(links, list):
        return ""

    for link in links:
        if not isinstance(link, Mapping):
            continue
        if normalize_value(link.get("rel")).lower() != "alternate":
            continue
        alternate_url = resolve_http_url(link.get("href"), canonicalize=True)
        if alternate_url:
            return alternate_url

    for link in links:
        if not isinstance(link, Mapping):
            continue
        fallback_url = resolve_http_url(link.get("href"), canonicalize=True)
        if fallback_url:
            return fallback_url

    return ""


def get_entry_identifier(entry: Mapping[str, Any]) -> str:
    """Retourne le meilleur identifiant stable disponible."""

    return (
        normalize_value(entry.get("id"))
        or normalize_value(entry.get("guid"))
        or get_entry_url(entry)
    )


def get_entry_text(entry: Mapping[str, Any]) -> str:
    """Retourne le contenu textuel le plus complet."""

    content_items = entry.get("content", [])
    if isinstance(content_items, list):
        for content_item in content_items:
            if not isinstance(content_item, Mapping):
                continue
            text = clean_html(content_item.get("value"))
            if text:
                return text

    for field_name in RSS_HTML_TEXT_FIELDS:
        text = clean_html(entry.get(field_name))
        if text:
            return text

    return ""


# Images déclarées dans le flux

def extract_image_from_html(value: Any, base_url: Any = "") -> str:
    """Extrait la première image exploitable d'un fragment HTML."""

    raw_html = normalize_value(value)
    if not raw_html:
        return ""

    image = BeautifulSoup(html.unescape(raw_html), "html.parser").find("img")
    if image is None:
        return ""

    for attribute_name in ("src", "data-src", "data-original"):
        image_url = resolve_http_url(image.get(attribute_name), base_url)
        if image_url:
            return image_url

    srcset = normalize_value(image.get("srcset"))
    if not srcset:
        return ""

    first_candidate = srcset.split(",", maxsplit=1)[0]
    first_url = first_candidate.strip().split(" ", maxsplit=1)[0]
    return resolve_http_url(first_url, base_url)


def get_media_url(items: Any, base_url: Any = "") -> str:
    """Retourne la première URL valide d'une collection média."""

    if not isinstance(items, list):
        return ""

    for item in items:
        if not isinstance(item, Mapping):
            continue
        media_url = resolve_http_url(item.get("url") or item.get("href"), base_url)
        if media_url:
            return media_url

    return ""


def get_enclosure_image_url(entry: Mapping[str, Any], base_url: str) -> str:
    """Retourne une image déclarée comme enclosure."""

    links = entry.get("links", [])
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, Mapping):
                continue

            relation = normalize_value(link.get("rel")).lower()
            mime_type = normalize_value(link.get("type")).lower()
            if relation != "enclosure":
                continue
            if mime_type and not mime_type.startswith("image/"):
                continue

            image_url = resolve_http_url(
                link.get("href") or link.get("url"),
                base_url
            )
            if image_url:
                return image_url

    enclosures = entry.get("enclosures", [])
    if isinstance(enclosures, list):
        for enclosure in enclosures:
            if not isinstance(enclosure, Mapping):
                continue

            mime_type = normalize_value(enclosure.get("type")).lower()
            if mime_type and not mime_type.startswith("image/"):
                continue

            image_url = resolve_http_url(
                enclosure.get("href") or enclosure.get("url"),
                base_url
            )
            if image_url:
                return image_url

    return ""


def get_entry_image_url(entry: Mapping[str, Any]) -> str:
    """Retourne la meilleure image disponible sans la rendre obligatoire."""

    entry_url = get_entry_url(entry)

    for field_name in ("media_content", "media_thumbnail"):
        image_url = get_media_url(entry.get(field_name), entry_url)
        if image_url:
            return image_url

    image_url = get_enclosure_image_url(entry, entry_url)
    if image_url:
        return image_url

    content_items = entry.get("content", [])
    if isinstance(content_items, list):
        for content_item in content_items:
            if not isinstance(content_item, Mapping):
                continue
            image_url = extract_image_from_html(content_item.get("value"), entry_url)
            if image_url:
                return image_url

    for field_name in RSS_IMAGE_HTML_FIELDS:
        image_url = extract_image_from_html(entry.get(field_name), entry_url)
        if image_url:
            return image_url

    return ""


# Auteur, catégorie et date

def get_entry_author(entry: Mapping[str, Any]) -> str:
    """Retourne l'auteur déclaré dans le flux."""

    author = normalize_value(entry.get("author"))
    if author:
        return author

    author_detail = entry.get("author_detail", {})
    if isinstance(author_detail, Mapping):
        author = normalize_value(author_detail.get("name"))
        if author:
            return author

    author = normalize_value(entry.get("dc_creator"))
    if author:
        return author

    authors = entry.get("authors", [])
    if not isinstance(authors, list):
        return ""

    names = [
        name
        for author_item in authors
        if isinstance(author_item, Mapping)
        and (name := normalize_value(author_item.get("name")))
    ]
    return ", ".join(dict.fromkeys(names))


def get_entry_category(
    entry: Mapping[str, Any],
    configured_category: Any
) -> str:
    """Privilégie la catégorie configurée puis celles du flux."""

    configured = normalize_value(configured_category)
    if configured:
        return configured

    tags = entry.get("tags", [])
    if not isinstance(tags, list):
        return ""

    categories = [
        category
        for tag in tags
        if isinstance(tag, Mapping)
        and (category := normalize_value(tag.get("term") or tag.get("label")))
    ]
    return ", ".join(dict.fromkeys(categories))


def get_entry_date(entry: Mapping[str, Any]) -> str:
    """Retourne la première date RSS normalisable."""

    for field_name in (
        "published",
        "updated",
        "created",
        "published_parsed",
        "updated_parsed",
        "created_parsed"
    ):
        if normalized_date := convert_date_to_iso(entry.get(field_name)):
            return normalized_date
    return ""


# Règle métier propre au RSS

def get_max_article_age_days(source: Mapping[str, Any]) -> int:
    """Retourne l'âge maximal autorisé pour cette source RSS."""

    configured = parse_non_negative_integer(
        source.get("max_article_age_days", MAX_ARTICLE_AGE_DAYS),
        default=MAX_ARTICLE_AGE_DAYS
    )
    if MAX_ARTICLE_AGE_DAYS > 0:
        return min(configured or MAX_ARTICLE_AGE_DAYS, MAX_ARTICLE_AGE_DAYS)
    return configured


def validate_article_age(
    article: Mapping[str, Any],
    max_article_age_days: int
) -> tuple[bool, str]:
    """Vérifie la cohérence et l'ancienneté de la date de publication."""

    published_at = normalize_value(article.get("published_at"))
    if not published_at:
        return True, ""

    article_date = parse_datetime(published_at)
    if article_date is None:
        return False, "date_publication_invalide"

    now = get_current_datetime()
    if article_date > now + timedelta(minutes=5):
        return False, "date_publication_future"
    if (
        max_article_age_days > 0
        and article_date < now - timedelta(days=max_article_age_days)
    ):
        return False, "article_trop_ancien"

    return True, ""


# Téléchargement HTTP

def is_retryable_http_error(error: BaseException) -> bool:
    """Autorise les reprises uniquement sur les erreurs temporaires."""

    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code == 429 or 500 <= status_code < 600
    return False


@retry(
    retry=retry_if_exception(is_retryable_http_error),
    stop=stop_after_attempt(max(MAX_RETRIES, 1)),
    wait=wait_exponential(
        multiplier=max(RETRY_DELAY_SECONDS, 0.1),
        min=max(RETRY_DELAY_SECONDS, 0.1),
        max=max(RETRY_DELAY_SECONDS * 8, RETRY_DELAY_SECONDS, 0.1)
    ),
    reraise=True
)
def _download_feed_request(url: str) -> httpx.Response:
    """Effectue la requête HTTP brute avec reprise sur les erreurs temporaires."""

    headers = {
        **HTTP_HEADERS,
        "Accept": (
            "application/rss+xml,application/atom+xml,"
            "application/xml,text/xml,*/*;q=0.8"
        )
    }

    with httpx.Client(
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True
    ) as client:
        increment_rss_request_count()
        response = client.get(url)
        response.raise_for_status()
        return response


def download_feed(url: str, source_name: str = "source RSS") -> DownloadedFeed:
    """Télécharge un flux et convertit les erreurs finales en erreurs RSS."""

    try:
        response = _download_feed_request(url)
    except httpx.HTTPStatusError as error:
        reason = raise_if_fatal_rss_error(error, source_name)
        raise RssRequestError(
            source_name=source_name,
            reason=reason,
            status_code=error.response.status_code
        ) from None
    except httpx.TimeoutException:
        raise RssTimeoutError(
            source_name=source_name,
            reason="Délai dépassé pendant le téléchargement du flux RSS."
        ) from None
    except httpx.NetworkError as error:
        raise RssNetworkError(
            source_name=source_name,
            reason=normalize_value(error) or type(error).__name__
        ) from None

    content_type = normalize_value(
        response.headers.get("content-type")
    ).split(";", maxsplit=1)[0].strip().lower()

    return DownloadedFeed(
        content=response.content,
        content_type=content_type,
        encoding=normalize_value(response.encoding),
        final_url=str(response.url)
    )


# Contrôle et parsing XML

def looks_like_html(content: bytes) -> bool:
    """Détecte une page HTML à partir de ses premiers octets."""

    return bool(HTML_PREFIX_PATTERN.match(content[:4096].lstrip(UTF8_BOM)))


def looks_like_xml(content: bytes) -> bool:
    """Détecte une structure XML, RSS, Atom ou RDF probable."""

    sample = content[:4096].lstrip(UTF8_BOM + b" \t\r\n").lower()
    return sample.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf:rdf"))

def validate_feed_content_type(
    downloaded_feed: DownloadedFeed,
    source_name: str
) -> None:
    """Valide le type MIME tout en tolérant un XML mal déclaré."""

    # Lecture des métadonnées
    content_type = downloaded_feed.content_type
    content = downloaded_feed.content

    # Page HTML reçue à la place d'un flux
    if looks_like_html(content):
        raise RssInvalidContentTypeError(
            source_name=source_name,
            reason=(
                "Réponse HTML reçue à la place du flux RSS "
                f"(type={content_type or 'inconnu'}, "
                f"URL finale={downloaded_feed.final_url})."
            )
        )

    # Type MIME explicitement HTML
    if content_type in RSS_HTML_CONTENT_TYPES:
        raise RssInvalidContentTypeError(
            source_name=source_name,
            reason=(
                "Type de contenu HTML reçu à la place du flux RSS : "
                f"{content_type}."
            )
        )

    # Type MIME inhabituel ou incompatible
    if (
        content_type
        and content_type not in RSS_XML_CONTENT_TYPES
        and not content_type.endswith("+xml")
    ):
        if not looks_like_xml(content):
            raise RssInvalidContentTypeError(
                source_name=source_name,
                reason=(
                    "Type de contenu incompatible avec RSS ou Atom : "
                    f"{content_type}."
                )
            )

        logger.warning(
            "Type inhabituel mais contenu XML détecté pour %s : %s.",
            source_name,
            content_type
        )

def clean_xml_prefix(content: bytes) -> tuple[bytes, bool]:
    """Retire le BOM UTF-8 et les espaces avant la déclaration XML."""

    cleaned_content = content
    changed = False

    if cleaned_content.startswith(UTF8_BOM):
        cleaned_content = cleaned_content[len(UTF8_BOM):]
        changed = True

    match = LEADING_XML_WHITESPACE_PATTERN.match(cleaned_content)
    if match:
        cleaned_content = cleaned_content[match.end():]
        changed = True

    return cleaned_content, changed


def validate_xml_normally(content: bytes) -> tuple[bool, Exception | None]:
    """Tente un parsing XML strict sans altérer le contenu."""

    try:
        ElementTree.fromstring(content)
        return True, None
    except (ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
        return False, error


def parse_with_feedparser(
    content: bytes,
    source_name: str,
    tolerant_mode: bool
) -> Mapping[str, Any]:
    """Analyse un flux avec Feedparser et contrôle son mode bozo."""

    # Parsing Feedparser
    try:
        feed = feedparser.parse(content)
    except Exception as error:
        raise RssParsingError(
            source_name=source_name,
            reason=(
                "Exception pendant l'analyse du flux RSS : "
                f"{normalize_value(error) or type(error).__name__}."
            )
        ) from error

    # Contrat de sortie
    if not isinstance(feed, Mapping):
        raise RssParsingError(
            source_name=source_name,
            reason=(
                "Feedparser a retourné un type inattendu : "
                f"{type(feed).__name__}."
            )
        )

    entries = getattr(feed, "entries", [])

    # Flux partiellement récupérable
    if getattr(feed, "bozo", False):
        bozo_error = getattr(
            feed,
            "bozo_exception",
            "erreur XML inconnue"
        )
        message = normalize_value(bozo_error) or "erreur XML inconnue"

        if entries:
            logger.warning(
                "Flux RSS %s récupéré en mode tolérant : %s",
                source_name,
                message
            )
        else:
            raise RssInvalidFeedError(
                source_name=source_name,
                reason=f"Flux RSS invalide : {message}."
            )

    # Flux accepté après échec XML strict
    elif tolerant_mode:
        logger.warning(
            "Flux RSS %s accepté par le parseur tolérant "
            "après échec XML strict.",
            source_name
        )

    return feed


def parse_feed_content(
    downloaded_feed: DownloadedFeed,
    source_name: str
) -> Mapping[str, Any]:
    """Valide puis analyse le contenu brut avec repli tolérant."""

    content = downloaded_feed.content

    # Flux vide
    if not content:
        raise RssInvalidFeedError(
            source_name=source_name,
            reason="Le flux RSS ne contient aucune donnée."
        )

    # Vérification du Content-Type
    validate_feed_content_type(downloaded_feed, source_name)

    # XML valide
    strict_valid, strict_error = validate_xml_normally(content)
    if strict_valid:
        return parse_with_feedparser(
            content,
            source_name,
            tolerant_mode=False
        )

    # Nettoyage éventuel du BOM
    cleaned_content, prefix_cleaned = clean_xml_prefix(content)

    if prefix_cleaned:
        cleaned_valid, cleaned_error = validate_xml_normally(cleaned_content)

        if cleaned_valid:
            logger.warning(
                "Flux RSS %s corrigé : BOM ou espaces avant la déclaration XML.",
                source_name
            )

            return parse_with_feedparser(
                cleaned_content,
                source_name,
                tolerant_mode=False
            )

        strict_error = cleaned_error

    # HTML après échec XML
    if looks_like_html(content):
        raise RssInvalidContentTypeError(
            source_name=source_name,
            reason="Une page HTML a été reçue à la place du flux RSS."
        )

    logger.warning(
        "Parsing XML strict impossible pour %s : %s. "
        "Tentative de récupération tolérante.",
        source_name,
        strict_error
    )

    return parse_with_feedparser(
        cleaned_content if prefix_cleaned else content,
        source_name,
        tolerant_mode=True
    )

def parse_feed(
    source_url: str,
    source_name: str
) -> Mapping[str, Any]:
    """Télécharge puis analyse un flux RSS ou Atom."""

    # Téléchargement du flux
    downloaded_feed = download_feed(source_url, source_name)

    # Métadonnées HTTP utiles au diagnostic
    if downloaded_feed.encoding:
        logger.debug(
            "Encodage HTTP déclaré pour le flux RSS %s : %s.",
            source_name,
            downloaded_feed.encoding
        )

    if downloaded_feed.final_url != source_url:
        logger.debug(
            "Redirection RSS pour %s : %s -> %s.",
            source_name,
            source_url,
            downloaded_feed.final_url
        )

    # Validation et parsing
    return parse_feed_content(downloaded_feed, source_name)

# Construction et validation spécifiques

def build_rss_article(
    entry: Mapping[str, Any],
    source: Mapping[str, Any]
) -> dict[str, Any]:
    """Transforme une entrée RSS au format standard CheckIt.AI."""

    if not isinstance(entry, Mapping):
        return {}

    identifier = get_entry_identifier(entry)
    if not identifier:
        return {}

    return build_standard_article(
        identifier=identifier,
        source=source.get("name", "RSS"),
        title=clean_html(entry.get("title")),
        text=get_entry_text(entry),
        image_url=get_entry_image_url(entry),
        image_path="",
        published_at=get_entry_date(entry),
        url=get_entry_url(entry),
        author=get_entry_author(entry),
        language=source.get("language", ""),
        category=get_entry_category(entry, source.get("category", "")),
        label="",
        dataset_role=source.get("role", "acquisition")
    )


def iter_rss_items(source: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Télécharge un flux puis retourne uniquement ses entrées mappées."""

    source_name = normalize_value(source.get("name")) or "source RSS"
    source_url = resolve_http_url(source.get("url"))
    if not source_url:
        raise ValueError(
            f"Source RSS {source_name} ignorée : URL absente ou invalide."
        )

    feed = parse_feed(source_url, source_name)
    if feed is None:
        return []

    raw_entries = getattr(feed, "entries", [])
    if not isinstance(raw_entries, list):
        raise TypeError(
            f"Le flux RSS {source_name} ne contient pas une liste d'entrées."
        )

    entries = [entry for entry in raw_entries if isinstance(entry, Mapping)]
    invalid_count = len(raw_entries) - len(entries)

    if invalid_count:
        logger.warning(
            "%s entrée(s) RSS invalide(s) ignorée(s) pour %s.",
            invalid_count,
            source_name
        )
    if not entries:
        logger.warning("Aucune entrée trouvée dans le flux RSS %s.", source_name)

    return entries


def validate_rss_entry(
    entry: Any,
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Vérifie qu'une entrée RSS possède un identifiant exploitable."""

    del source
    if not isinstance(entry, Mapping):
        return False, "entree_invalide"
    if not get_entry_identifier(entry):
        return False, "identifiant_absent"
    return True, ""


def validate_rss_article_age(
    article: Mapping[str, Any],
    source: Mapping[str, Any]
) -> tuple[bool, str]:
    """Applique la règle d'ancienneté propre aux publications RSS."""

    return validate_article_age(
        article=article,
        max_article_age_days=get_max_article_age_days(source)
    )


# Assemblage adaptateur et moteur

RSS_ADAPTER = RssAdapter(
    iter_items=iter_rss_items,
    build_article=build_rss_article,
    validate_item=validate_rss_entry,
    validate_article=validate_rss_article_age,
    default_name="RSS"
)

RSS_EXTRACTOR = RssExtractor(
    adapter=RSS_ADAPTER,
    sources_file=SOURCES_FILE
)


# API publique de l'extracteur concret

def extract_articles_from_source(
    source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Extrait une source RSS déjà chargée."""

    return RSS_EXTRACTOR.extract_source(source)


def extract_articles_from_sources(
    sources: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Extrait plusieurs sources RSS déjà chargées."""

    return RSS_EXTRACTOR.extract_sources(sources)


def load_rss_sources() -> list[dict[str, Any]]:
    """Recharge puis retourne les configurations RSS."""

    RSS_EXTRACTOR.reload_sources()
    return RSS_EXTRACTOR.get_sources()


def extract_all_articles() -> ExtractorResult:
    """Lance toutes les sources RSS actives."""

    return RSS_EXTRACTOR.run()


# Exécution locale

if __name__ == "__main__":
    result = extract_all_articles()
    print(
        f"{len(result.articles)} article(s) RSS extrait(s). "
        f"Statut : {result.status}."
    )
    if result.message:
        print(result.message)
    if result.articles:
        print(result.articles[0])