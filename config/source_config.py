"""Validation centralisée de la configuration des sources CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `config/source_config.py` (commit 93632c5).
Modifié pour SortirIA : un secret peut aussi venir d'une variable d'environnement du même nom en majuscules (ex. OPENAGENDA_API_KEY).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

from config.environment import SecretError, get_environment_variable, get_secret
from src.logger import get_logger
from src.utils.yaml_utils import load_yaml_file

logger = get_logger(__name__)

SourceType = Literal["api", "rss", "dataset", "scraper", "social", "ical", "document"]
ApiQuery = str | dict[str, Any]
ConfigTextValue = str | tuple[str, ...]
T = TypeVar("T")

SECTION_SOURCE_TYPES: dict[str, SourceType] = {
    "api_sources": "api", "rss_sources": "rss", "datasets": "dataset",
    "scraper_sources": "scraper", "social_sources": "social",
    # Ajouts SortirIA (14/10/2026) : calendriers iCal des lieux partenaires,
    # programmes PDF lus par Mistral.
    "ical_sources": "ical",
    "document_sources": "document",
}
KNOWN_TOP_LEVEL_SECTIONS = {"defaults", *SECTION_SOURCE_TYPES}
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
SECRET_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class SourceConfigurationError(ValueError):
    """Signale une configuration de sources invalide."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(str(error).strip() for error in errors if str(error).strip())
        super().__init__("\n".join(f"- {error}" for error in self.errors))


# Conversions sûres

def is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def clean_string(value: Any) -> str:
    return str(value or "").strip()


def string_or_tuple(value: Any) -> ConfigTextValue:
    """Conserve une chaîne simple ou normalise une collection de chaînes."""

    if isinstance(value, str):
        return clean_string(value)

    if is_sequence(value):
        return string_tuple(value)

    return clean_string(value)


def has_configured_value(value: Any) -> bool:
    """Indique si une valeur simple ou multiple contient une donnée exploitable."""

    if isinstance(value, str):
        return bool(clean_string(value))

    if is_sequence(value):
        return bool(string_tuple(value))

    return bool(clean_string(value))


def optional_string(value: Any) -> str | None:
    cleaned = clean_string(value)
    return cleaned or None


def string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not is_sequence(value):
        return ()
    return tuple(cleaned for item in value if (cleaned := clean_string(item)))


def is_http_url(value: Any) -> bool:
    try:
        parsed = urlparse(clean_string(value))
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_secret_reference(
    source_id: str,
    field_name: str,
    secret_name: str | None,
    *,
    enabled: bool,
    required: bool,
    secrets_directory: str | Path | None = None
) -> list[str]:
    """Valide le nom du secret puis délègue sa résolution à environment.py."""

    if secret_name is None:
        return []

    if not SECRET_NAME_PATTERN.fullmatch(secret_name):
        return [
            f"{source_id}.{field_name} est invalide : {secret_name!r}."
        ]

    if not enabled or not required:
        return []

    # Modifié pour SortirIA : comme ApiExtractor.api_key, on accepte aussi une
    # variable d'environnement du même nom en majuscules (ex. OPENAGENDA_API_KEY
    # dans .env). Sans cela, la configuration était refusée alors que
    # l'extracteur, lui, savait lire la clé.
    if secrets_directory is None and get_environment_variable(secret_name.upper()):
        return []

    try:
        if secrets_directory is None:
            get_secret(secret_name, required=True)
        else:
            get_secret(
                secret_name,
                required=True,
                secrets_directory=secrets_directory
            )
    except SecretError as error:
        return [f"{source_id}: {error}"]

    return []


def merge_mappings(*mappings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fusionne récursivement plusieurs dictionnaires sans modifier les originaux."""

    merged: dict[str, Any] = {}
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            continue
        for key, value in mapping.items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                merged[key] = merge_mappings(merged[key], value)
            elif isinstance(value, Mapping):
                merged[key] = merge_mappings(value)
            else:
                merged[key] = value
    return merged


# Structures validées
@dataclass(frozen=True, slots=True)
class FilterConfig:
    """Filtres métier appliqués aux articles d'une source."""

    require_title: bool = True
    require_text: bool = False
    require_image: bool = False
    require_url: bool = True
    min_title_length: int = 0
    min_text_length: int = 0
    min_total_text_length: int = 0
    remove_deleted_content: bool = True
    remove_duplicates: bool = True
    validate_urls: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "FilterConfig":
        configuration = dict(data or {})
        known = {
            "require_title", "require_text", "require_image", "require_url",
            "min_title_length", "min_text_length", "min_total_text_length",
            "remove_deleted_content", "remove_duplicates", "validate_urls"
        }
        return cls(
            require_title=configuration.get("require_title", True),
            require_text=configuration.get("require_text", False),
            require_image=configuration.get("require_image", False),
            require_url=configuration.get("require_url", True),
            min_title_length=configuration.get("min_title_length", 0),
            min_text_length=configuration.get("min_text_length", 0),
            min_total_text_length=configuration.get("min_total_text_length", 0),
            remove_deleted_content=configuration.get("remove_deleted_content", True),
            remove_duplicates=configuration.get("remove_duplicates", True),
            validate_urls=configuration.get("validate_urls", True),
            extra={key: value for key, value in configuration.items() if key not in known}
        )

    def validate(self, source_id: str) -> list[str]:
        errors: list[str] = []
        booleans = (
            "require_title", "require_text", "require_image", "require_url",
            "remove_deleted_content", "remove_duplicates", "validate_urls"
        )
        integers = ("min_title_length", "min_text_length", "min_total_text_length")
        for name in booleans:
            if not isinstance(getattr(self, name), bool):
                errors.append(f"{source_id}.filters.{name} doit être un booléen YAML.")
        for name in integers:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"{source_id}.filters.{name} doit être un entier positif ou nul.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra")
        return {**data, **extra}


@dataclass(frozen=True, slots=True)
class PaginationConfig:
    """Pagination par numéro de page ou par curseur."""

    enabled: bool = False
    parameter: str = "page"
    start: int | str | None = 1
    max_pages: int = 1
    page_size_parameter: str | None = None
    page_size: int | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "PaginationConfig":
        configuration = dict(data or {})
        return cls(
            enabled=configuration.get("enabled", False), parameter=clean_string(configuration.get("parameter", "page")),
            start=configuration.get("start", 1), max_pages=configuration.get("max_pages", 1),
            page_size_parameter=optional_string(configuration.get("page_size_parameter")),
            page_size=configuration.get("page_size")
        )

    def validate(self, source_id: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(self.enabled, bool):
            errors.append(f"{source_id}.pagination.enabled doit être un booléen YAML.")
        if not self.parameter:
            errors.append(f"{source_id}.pagination.parameter est obligatoire.")
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int) or self.max_pages <= 0:
            errors.append(f"{source_id}.pagination.max_pages doit être strictement positif.")
        if self.start is not None and (
            isinstance(self.start, bool) or not isinstance(self.start, (int, str))
            or isinstance(self.start, int) and self.start < 0 or isinstance(self.start, str) and not self.start.strip()
        ):
            errors.append(f"{source_id}.pagination.start doit être positif ou nul, non vide ou null pour un curseur.")
        if self.page_size is not None and (
            isinstance(self.page_size, bool) or not isinstance(self.page_size, int) or self.page_size <= 0
        ):
            errors.append(f"{source_id}.pagination.page_size doit être strictement positif.")
        if self.page_size is not None and not self.page_size_parameter:
            errors.append(f"{source_id}.pagination.page_size_parameter est requis lorsque page_size est défini.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Configuration commune à toutes les sources."""

    enabled: bool
    name: str
    source_id: str
    type: SourceType
    role: str
    language: ConfigTextValue
    country: ConfigTextValue
    category: ConfigTextValue
    max_articles: int
    filters: FilterConfig

    def validate(self, expected_type: SourceType) -> list[str]:
        errors: list[str] = []
        identifier = self.source_id or "<sans id>"
        if not isinstance(self.enabled, bool):
            errors.append(f"{identifier}.enabled doit être un booléen YAML.")
        for name in ("name", "source_id", "role"):
            if not clean_string(getattr(self, name, "")):
                errors.append(f"{identifier}.{name} est obligatoire.")
        for name in ("language", "country", "category"):
            if not has_configured_value(getattr(self, name, "")):
                errors.append(f"{identifier}.{name} est obligatoire.")
        if self.type != expected_type:
            errors.append(f"{identifier}.type={self.type!r} est incompatible avec la section {expected_type!r}.")
        if self.source_id and not SOURCE_ID_PATTERN.fullmatch(self.source_id):
            errors.append(f"{identifier}.source_id doit utiliser le format snake_case.")
        if isinstance(self.max_articles, bool) or not isinstance(self.max_articles, int):
            errors.append(f"{identifier}.max_articles doit être un entier.")
        elif self.max_articles <= 0:
            errors.append(f"{identifier}.max_articles doit être strictement positif.")
        errors.extend(self.filters.validate(identifier))
        return errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["language"] = list(self.language) if isinstance(self.language, tuple) else self.language
        data["country"] = list(self.country) if isinstance(self.country, tuple) else self.country
        data["category"] = list(self.category) if isinstance(self.category, tuple) else self.category
        data["filters"] = self.filters.to_dict()
        return data


@dataclass(frozen=True, slots=True)
class ApiSourceConfig(SourceConfig):
    endpoint: str
    api_key_secret_name: str | None
    queries: tuple[ApiQuery, ...]
    pagination: PaginationConfig
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(
        self, expected_type: SourceType = "api", *, require_secrets: bool = True,
        secrets_directory: str | Path | None = None
    ) -> list[str]:
        errors = SourceConfig.validate(self, expected_type)
        if not is_http_url(self.endpoint):
            errors.append(f"{self.source_id}.endpoint doit être une URL HTTP ou HTTPS valide.")
        errors.extend(validate_secret_reference(
            self.source_id, "api_key_secret_name", self.api_key_secret_name,
            enabled=self.enabled, required=require_secrets and self.api_key_secret_name is not None,
            secrets_directory=secrets_directory
        ))
        errors.extend(self.pagination.validate(self.source_id))
        for index, query in enumerate(self.queries):
            if isinstance(query, str):
                if not query.strip():
                    errors.append(f"{self.source_id}.queries[{index}] ne peut pas être vide.")
            elif not isinstance(query, Mapping):
                errors.append(
                    f"{self.source_id}.queries[{index}] doit être une chaîne "
                    "ou un dictionnaire."
                )
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            **SourceConfig.to_dict(self), **self.extra, "endpoint": self.endpoint,
            "api_key_secret_name": self.api_key_secret_name,
            "queries": [
                dict(query) if isinstance(query, Mapping) else query
                for query in self.queries
            ],
            "pagination": self.pagination.to_dict()
        }


@dataclass(frozen=True, slots=True)
class RssSourceConfig(SourceConfig):
    url: str
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, expected_type: SourceType = "rss") -> list[str]:
        errors = SourceConfig.validate(self, expected_type)
        if not is_http_url(self.url):
            errors.append(f"{self.source_id}.url doit être une URL HTTP ou HTTPS valide.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {**SourceConfig.to_dict(self), **self.extra, "url": self.url}


@dataclass(frozen=True, slots=True)
class FileSourceConfig(SourceConfig):
    """Source « un fichier » (calendrier iCal, programme PDF) : une URL, le reste dans `extra`."""

    url: str
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, expected_type: SourceType = "ical") -> list[str]:
        errors = SourceConfig.validate(self, expected_type)
        if not is_http_url(self.url):
            errors.append(f"{self.source_id}.url doit être une URL HTTP ou HTTPS valide.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {**SourceConfig.to_dict(self), **self.extra, "url": self.url}


@dataclass(frozen=True, slots=True)
class DatasetSourceConfig(SourceConfig):
    path: Path
    format: str
    chunksize: int
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, expected_type: SourceType = "dataset") -> list[str]:
        errors = SourceConfig.validate(self, expected_type)
        if not clean_string(self.path):
            errors.append(f"{self.source_id}.path est obligatoire.")
        if not self.format:
            errors.append(f"{self.source_id}.format est obligatoire.")
        if isinstance(self.chunksize, bool) or not isinstance(self.chunksize, int):
            errors.append(f"{self.source_id}.chunksize doit être un entier.")
        elif self.chunksize <= 0:
            errors.append(f"{self.source_id}.chunksize doit être strictement positif.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            **SourceConfig.to_dict(self), **self.extra, "path": str(self.path), "format": self.format,
            "chunksize": self.chunksize
        }


@dataclass(frozen=True, slots=True)
class ScraperSourceConfig(SourceConfig):
    base_url: str
    start_urls: tuple[str, ...]
    selectors: dict[str, str | None]
    respect_robots_txt: bool
    request_delay_seconds: float
    pagination: PaginationConfig
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, expected_type: SourceType = "scraper") -> list[str]:
        errors = SourceConfig.validate(self, expected_type)
        if not is_http_url(self.base_url):
            errors.append(f"{self.source_id}.base_url doit être une URL HTTP ou HTTPS valide.")
        if not self.start_urls:
            errors.append(f"{self.source_id}.start_urls doit contenir au moins une URL.")
        elif any(not is_http_url(url) for url in self.start_urls):
            errors.append(f"{self.source_id}.start_urls contient une URL invalide.")
        required_selectors = ("article", "title", "link", "text")
        missing = [name for name in required_selectors if not clean_string(self.selectors.get(name))]
        if missing:
            errors.append(f"{self.source_id}.selectors incomplet : {', '.join(missing)}.")
        if not isinstance(self.respect_robots_txt, bool):
            errors.append(f"{self.source_id}.respect_robots_txt doit être un booléen YAML.")
        if isinstance(self.request_delay_seconds, bool) or not isinstance(self.request_delay_seconds, (int, float)):
            errors.append(f"{self.source_id}.request_delay_seconds doit être un nombre.")
        elif self.request_delay_seconds < 0:
            errors.append(f"{self.source_id}.request_delay_seconds ne peut pas être négatif.")
        errors.extend(self.pagination.validate(self.source_id))
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            **SourceConfig.to_dict(self), **self.extra, "base_url": self.base_url,
            "start_urls": list(self.start_urls), "selectors": dict(self.selectors),
            "respect_robots_txt": self.respect_robots_txt,
            "request_delay_seconds": self.request_delay_seconds, "pagination": self.pagination.to_dict()
        }


@dataclass(frozen=True, slots=True)
class SocialSourceConfig(SourceConfig):
    configuration: dict[str, Any] = field(default_factory=dict)

    def validate(
        self, expected_type: SourceType = "social", *, require_secrets: bool = True,
        secrets_directory: str | Path | None = None
    ) -> list[str]:
        errors = SourceConfig.validate(self, expected_type)
        endpoint = self.configuration.get("endpoint")
        if endpoint is not None and not is_http_url(endpoint):
            errors.append(f"{self.source_id}.endpoint doit être une URL HTTP ou HTTPS valide.")
        for field_name, value in self.configuration.items():
            if field_name.endswith("_secret_name"):
                secret_name = optional_string(value)
                errors.extend(validate_secret_reference(
                    self.source_id, field_name, secret_name,
                    enabled=self.enabled, required=require_secrets and secret_name is not None,
                    secrets_directory=secrets_directory
                ))
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {**SourceConfig.to_dict(self), **dict(self.configuration)}


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    """Registre validé de toutes les sources."""

    sources: tuple[SourceConfig, ...]

    def get(self, source_id: str) -> SourceConfig:
        normalized_id = clean_string(source_id)
        for source in self.sources:
            if source.source_id == normalized_id:
                return source
        raise KeyError(f"Source inconnue : {normalized_id}")

    def get_enabled(self, source_type: SourceType | None = None) -> list[SourceConfig]:
        return [source for source in self.sources if source.enabled and (source_type is None or source.type == source_type)]

    def by_type(self, source_type: SourceType) -> list[SourceConfig]:
        return [source for source in self.sources if source.type == source_type]

    def to_dicts(self, source_type: SourceType | None = None) -> list[dict[str, Any]]:
        return [source.to_dict() for source in self.sources if source_type is None or source.type == source_type]


# Construction du registre

def iter_section_sources(section_data: Any) -> list[dict[str, Any]]:
    if isinstance(section_data, Mapping):
        sources: list[dict[str, Any]] = []
        for source_key, source_data in section_data.items():
            if not isinstance(source_data, Mapping):
                continue
            source = dict(source_data)
            source.setdefault("source_id", clean_string(source_key))
            sources.append(source)
        return sources
    if is_sequence(section_data):
        return [dict(source) for source in section_data if isinstance(source, Mapping)]
    return []


def get_role_filter_defaults(defaults: Mapping[str, Any], role: str) -> dict[str, Any]:
    filters = defaults.get("filters", {})
    role_filters = filters.get(role, {}) if isinstance(filters, Mapping) else {}
    return dict(role_filters) if isinstance(role_filters, Mapping) else {}


def build_queries(value: Any) -> tuple[ApiQuery, ...]:
    """Normalise les requêtes API sans imposer un format propre à une API."""

    if isinstance(value, Mapping):
        return (dict(value),)

    if isinstance(value, str):
        query = value.strip()
        return (query,) if query else ()

    if not is_sequence(value):
        return ()

    queries: list[ApiQuery] = []

    for query in value:
        if isinstance(query, Mapping):
            queries.append(dict(query))
        elif isinstance(query, str) and query.strip():
            queries.append(query.strip())

    return tuple(queries)


def extract_extra(configuration: Mapping[str, Any], excluded: set[str]) -> dict[str, Any]:
    return {key: value for key, value in configuration.items() if key not in excluded}


def build_source_config(raw_source: Mapping[str, Any], expected_type: SourceType, defaults: Mapping[str, Any]) -> SourceConfig:
    role = clean_string(raw_source.get("role") or defaults.get(expected_type, {}).get("role"))
    merged = merge_mappings(defaults.get("common"), defaults.get(expected_type), raw_source)
    merged["filters"] = merge_mappings(get_role_filter_defaults(defaults, role), merged.get("filters"))
    common_fields = {
        "enabled", "name", "source_id", "type", "role", "language", "country", "category",
        "max_articles", "filters"
    }
    common: dict[str, Any] = {
        "enabled": merged.get("enabled", True), "name": clean_string(merged.get("name")),
        "source_id": clean_string(merged.get("source_id")),
        "type": clean_string(merged.get("type", expected_type)).casefold(), "role": clean_string(merged.get("role")),
        "language": string_or_tuple(merged.get("language")),
        "country": string_or_tuple(merged.get("country")),
        "category": string_or_tuple(merged.get("category")),
        "max_articles": merged.get("max_articles", 0),
        "filters": FilterConfig.from_mapping(merged.get("filters") if isinstance(merged.get("filters"), Mapping) else None)
    }

    if expected_type == "api":
        specific = {"endpoint", "api_key_secret_name", "queries", "pagination"}
        return ApiSourceConfig(
            **common, endpoint=clean_string(merged.get("endpoint")),
            api_key_secret_name=optional_string(merged.get("api_key_secret_name")),
            queries=build_queries(merged.get("queries")),
            pagination=PaginationConfig.from_mapping(merged.get("pagination") if isinstance(merged.get("pagination"), Mapping) else None),
            extra=extract_extra(merged, common_fields | specific)
        )
    if expected_type == "rss":
        return RssSourceConfig(
            **common, url=clean_string(merged.get("url")),
            extra=extract_extra(merged, common_fields | {"url"})
        )
    if expected_type in ("ical", "document"):
        return FileSourceConfig(
            **common, url=clean_string(merged.get("url")),
            extra=extract_extra(merged, common_fields | {"url"})
        )
    if expected_type == "dataset":
        specific = {"path", "format", "chunksize"}
        return DatasetSourceConfig(
            **common, path=Path(clean_string(merged.get("path"))), format=clean_string(merged.get("format")).casefold(),
            chunksize=merged.get("chunksize", 0),
            extra=extract_extra(merged, common_fields | specific)
        )
    if expected_type == "scraper":
        selectors = merged.get("selectors", {})
        specific = {"base_url", "start_urls", "selectors", "respect_robots_txt", "request_delay_seconds", "pagination"}
        return ScraperSourceConfig(
            **common, base_url=clean_string(merged.get("base_url")), start_urls=string_tuple(merged.get("start_urls")),
            selectors={clean_string(key): optional_string(value) for key, value in selectors.items()} if isinstance(selectors, Mapping) else {},
            respect_robots_txt=merged.get("respect_robots_txt", True),
            request_delay_seconds=merged.get("request_delay_seconds", 0.0),
            pagination=PaginationConfig.from_mapping(merged.get("pagination") if isinstance(merged.get("pagination"), Mapping) else None),
            extra=extract_extra(merged, common_fields | specific)
        )
    return SocialSourceConfig(**common, configuration=extract_extra(merged, common_fields))


def validate_document_structure(document: Any) -> tuple[Mapping[str, Any], list[str]]:
    if not isinstance(document, Mapping):
        return {}, ["Le document YAML racine doit être un dictionnaire."]
    errors: list[str] = []
    unknown = sorted(set(document) - KNOWN_TOP_LEVEL_SECTIONS)
    if unknown:
        errors.append("Section(s) YAML inconnue(s) : " + ", ".join(unknown))
    defaults = document.get("defaults", {})
    if not isinstance(defaults, Mapping):
        errors.append("La section defaults doit être un dictionnaire.")
        defaults = {}
    return defaults, errors


def load_source_registry(
    file_path: str | Path, *, require_secrets: bool = True,
    secrets_directory: str | Path | None = None
) -> SourceRegistry:
    """Charge, fusionne et valide le contrat complet des sources."""

    document = load_yaml_file(file_path)
    resolved_secrets_directory = secrets_directory
    defaults, errors = validate_document_structure(document)
    sources: list[SourceConfig] = []

    for section_name, expected_type in SECTION_SOURCE_TYPES.items():
        section_data = document.get(section_name, []) if isinstance(document, Mapping) else []
        if section_data not in (None, [], {}) and not isinstance(section_data, Mapping) and not is_sequence(section_data):
            errors.append(f"La section {section_name} doit être une liste ou un dictionnaire.")
            continue
        for raw_source in iter_section_sources(section_data):
            try:
                source = build_source_config(raw_source, expected_type, defaults)
                if isinstance(source, (ApiSourceConfig, SocialSourceConfig)):
                    errors.extend(source.validate(
                        expected_type, require_secrets=require_secrets,
                        secrets_directory=resolved_secrets_directory
                    ))
                else:
                    errors.extend(source.validate(expected_type))
                sources.append(source)
            except (TypeError, ValueError) as error:
                errors.append(f"{clean_string(raw_source.get('source_id')) or '<sans id>'}: configuration impossible : {error}")

    duplicates = sorted(source_id for source_id, count in Counter(source.source_id for source in sources if source.source_id).items() if count > 1)
    if duplicates:
        errors.append("source_id dupliqué(s) : " + ", ".join(duplicates))
    if errors:
        raise SourceConfigurationError(errors)

    logger.info("%s source(s) validée(s), dont %s activée(s).", len(sources), sum(source.enabled for source in sources))
    return SourceRegistry(tuple(sources))


def load_validated_source(
    file_path: str | Path,
    source_id: str,
    *,
    require_secrets: bool = True,
    secrets_directory: str | Path | None = None
) -> dict[str, Any]:
    """Charge et valide uniquement la source demandée."""

    normalized_id = clean_string(source_id)

    if not normalized_id:
        raise SourceConfigurationError([
            "L'identifiant de source est obligatoire."
        ])

    if not SOURCE_ID_PATTERN.fullmatch(normalized_id):
        raise SourceConfigurationError([
            f"Identifiant de source invalide : {normalized_id!r}."
        ])

    document = load_yaml_file(file_path)
    defaults, errors = validate_document_structure(document)

    if not isinstance(document, Mapping):
        raise SourceConfigurationError(errors)

    matches: list[tuple[SourceType, dict[str, Any]]] = []

    # Recherche la source sans valider tout le registre
    for section_name, expected_type in SECTION_SOURCE_TYPES.items():
        section_data = document.get(section_name, [])

        if (
            section_data not in (None, [], {})
            and not isinstance(section_data, Mapping)
            and not is_sequence(section_data)
        ):
            errors.append(
                f"La section {section_name} doit être une liste "
                "ou un dictionnaire."
            )
            continue

        for raw_source in iter_section_sources(section_data):
            current_id = clean_string(raw_source.get("source_id"))

            if current_id == normalized_id:
                matches.append((expected_type, raw_source))

    if not matches:
        errors.append(f"Source inconnue : {normalized_id}.")

    if len(matches) > 1:
        errors.append(f"source_id dupliqué : {normalized_id}.")

    if errors:
        raise SourceConfigurationError(errors)

    expected_type, raw_source = matches[0]

    # Construit et valide seulement la source sélectionnée
    try:
        source = build_source_config(
            raw_source,
            expected_type,
            defaults
        )

        if isinstance(source, (ApiSourceConfig, SocialSourceConfig)):
            source_errors = source.validate(
                expected_type,
                require_secrets=require_secrets,
                secrets_directory=secrets_directory
            )
        else:
            source_errors = source.validate(expected_type)

    except (TypeError, ValueError) as error:
        raise SourceConfigurationError([
            f"{normalized_id}: configuration impossible : {error}"
        ]) from error

    if source_errors:
        raise SourceConfigurationError(source_errors)

    return source.to_dict()