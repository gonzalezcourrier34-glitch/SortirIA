"""Moteur commun utilisé par les extracteurs de datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/datasets/dataset_engine.py` (commit 93632c5).
Modifié pour SortirIA : la validation propre au dataset passe avant la construction de l'article (voir `process_dataset_item`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from config.constants import SOURCE_TYPE_DATASET
from config.paths import SOURCES_FILE
from config.source_config import load_validated_source
from src.article.processing.article_deduplicator import (
    is_duplicate_article,
    register_article,
)
from src.extractors.core.extractor_executor import execute_configured_extractor
from src.extractors.core.extractor_results import (
    ExtractorResult,
    build_disabled_result,
    build_empty_result,
    build_partial_result,
    build_success_result,
)
from src.extractors.datasets.dataset_adapter import (
    DatasetAdapter,
    DatasetItem,
    build_article_from_item,
    validate_adapter_item,
)
from src.extractors.datasets.dataset_file_utils import (
    resolve_dataset_directory,
    validate_dataset_files,
)
from src.logger import get_logger

from src.utils.value_utils import normalize_value
from src.utils.parsing_utils import (
    parse_boolean,
    parse_non_negative_integer
)
from config.settings import MAX_ARTICLES_PER_SOURCE

from src.utils.filter_utils import get_filter_configuration

from src.utils.extractor_utils import (
    log_extraction_summary,
    validate_article,
)

logger = get_logger(__name__)

# Extracteur

@dataclass(slots=True)
class DatasetExtractor:
    """Représente un extracteur de dataset configurable et exécutable."""

    source_id: str
    default_name: str
    adapter: DatasetAdapter
    section_name: str = "datasets"
    sources_file: Path = SOURCES_FILE
    _source: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Normalise l'identité et contrôle la cohérence de l'adaptateur."""

        self.source_id = normalize_value(self.source_id)
        self.default_name = normalize_value(self.default_name) or self.source_id
        self.section_name = normalize_value(self.section_name) or "datasets"

        if not self.source_id:
            raise ValueError(
                "L'identifiant d'un DatasetExtractor ne peut pas être vide."
            )
        if not isinstance(self.adapter, DatasetAdapter):
            raise TypeError("adapter doit être une instance de DatasetAdapter.")
        if self.sources_file is None:
            raise ValueError("Le fichier de configuration des sources est requis.")
        if self.source_id != self.adapter.source_id:
            raise ValueError(
                f"L'extracteur {self.source_id} utilise l'adaptateur "
                f"{self.adapter.source_id}."
            )

    # Configuration

    def load_source(self, force_reload: bool = False) -> dict[str, Any]:
        """Charge et met en cache la configuration validée du dataset."""

        if self._source and not force_reload:
            return dict(self._source)

        loaded_source = load_validated_source(
            self.sources_file,
            self.source_id,
        )
        self._source = dict(loaded_source)
        return dict(self._source)

    def reload_source(self) -> dict[str, Any]:
        """Recharge explicitement la configuration depuis le fichier YAML."""

        return self.load_source(force_reload=True)

    def get_source(self) -> dict[str, Any]:
        """Retourne la configuration actuellement disponible."""

        return self.load_source()

    @property
    def source(self) -> Mapping[str, Any]:
        """Retourne la configuration courante du dataset."""

        return self.load_source()

    # Exécution

    def extract(self, source: Mapping[str, Any]) -> ExtractorResult:
        """Extrait les articles depuis une configuration déjà chargée."""

        return extract_dataset_from_source(
            source=source,
            adapter=self.adapter,
        )

    def run(self) -> ExtractorResult:
        """Charge la configuration puis exécute le dataset."""

        return execute_configured_extractor(
            extractor_name=self.default_name,
            source_type=SOURCE_TYPE_DATASET,
            source_loader=self.reload_source,
            extraction_function=self.extract,
        )


# Contexte

@dataclass(slots=True)
class DatasetExtractionContext:
    """Conserve l'état partagé pendant l'extraction d'un dataset."""

    articles: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    processed_count: int = 0

    def reject(self, reason: str, default: str) -> None:
        """Enregistre un rejet avec une raison toujours exploitable."""

        self.rejection_stats[normalize_value(reason) or default] += 1


# Paramètres

def get_dataset_max_articles(source: Mapping[str, Any]) -> int:
    """Retourne le nombre maximal d'articles à extraire."""

    return parse_non_negative_integer(
        source.get("max_articles", MAX_ARTICLES_PER_SOURCE),
        default=MAX_ARTICLES_PER_SOURCE,
    )


def get_dataset_chunk_size(
    source: Mapping[str, Any],
    default: int,
    source_name: str,
) -> int:
    """Retourne une taille de bloc strictement positive."""

    configured = source.get("chunksize", source.get("chunk_size", default))
    chunk_size = parse_non_negative_integer(configured, default=default)

    if chunk_size > 0:
        return chunk_size

    fallback = max(parse_non_negative_integer(default, default=1), 1)
    logger.warning(
        "chunksize invalide pour %s. La valeur %s sera utilisée.",
        source_name,
        fallback,
    )
    return fallback


def get_dataset_filters(source: Mapping[str, Any]) -> dict[str, Any]:
    """Applique les filtres correspondant au rôle dataset."""

    filter_source = dict(source)
    filter_source["role"] = "dataset"
    filters = get_filter_configuration(filter_source)
    return dict(filters) if isinstance(filters, Mapping) else {}


def get_dataset_files(
    adapter: DatasetAdapter,
    dataset_directory: Path,
    source: Mapping[str, Any],
    source_name: str,
) -> list[Path]:
    """Recherche puis valide les fichiers fournis par un adaptateur."""

    discovered_files = adapter.find_files(dataset_directory, source)

    if not isinstance(discovered_files, list):
        raise TypeError(
            f"La recherche de fichiers de {source_name} doit retourner une liste."
        )

    dataset_files = validate_dataset_files(
        discovered_files,
        dataset_directory,
        adapter.supported_extensions,
        source_name,
    )
    if not dataset_files:
        raise FileNotFoundError(
            f"Aucun fichier exploitable trouvé pour {source_name}."
        )

    return dataset_files


# Traitement

def process_dataset_item(
    adapter: DatasetAdapter,
    item: DatasetItem,
    item_index: int,
    item_identifier: Any,
    source: Mapping[str, Any],
    source_name: str,
    filters: Mapping[str, Any],
    remove_duplicates: bool,
    context: DatasetExtractionContext
) -> None:
    """Construit, normalise, valide et conserve un élément du dataset."""

    context.processed_count += 1
    identifier = (
        normalize_value(item_identifier)
        or f"{adapter.source_id}:{item_index}"
    )

    # Modifié pour SortirIA (14/10/2026) : la validation propre au dataset
    # passe AVANT la construction de l'article. Elle porte sur l'élément brut ;
    # un élément refusé (ligne d'un fichier partenaire sans date, par exemple)
    # garde ainsi son motif au lieu de devenir « invalid_construction ».
    valid, reason = validate_adapter_item(
        adapter,
        item,
        filters,
        source
    )

    if not valid:
        context.reject(reason, "rejected_item")
        return

    article = build_article_from_item(
        adapter,
        item,
        item_index,
        identifier,
        source,
        source_name
    )

    if not article:
        context.reject("", "invalid_construction")
        return

    # L'adaptateur peut placer le nom lisible du dataset dans "source".
    # Cette information est conservée avant de restaurer l'identifiant
    # technique attendu par le pipeline.
    publisher = normalize_value(
        article.get("publisher")
        or article.get("source")
    )

    if (
        publisher
        and publisher.casefold() != adapter.source_id.casefold()
    ):
        article["publisher"] = publisher

    article["source"] = adapter.source_id
    article["source_type"] = (
        normalize_value(source.get("type"))
        or "dataset"
    )
    article.setdefault("source_name", source_name)

    valid, reason = validate_article(article, filters)

    if not valid:
        context.reject(reason, "invalid_article")
        return

    if (
        remove_duplicates
        and is_duplicate_article(article, context.seen_keys)
    ):
        context.reject("duplicate", "duplicate")
        return

    if remove_duplicates:
        register_article(article, context.seen_keys)

    context.articles.append(article)
    
# Bilan

def log_dataset_summary(
    source_name: str,
    maximum_articles: int,
    context: DatasetExtractionContext,
) -> None:
    """Journalise le bilan final d'une extraction de dataset."""

    log_extraction_summary(
        source_name=source_name,
        extracted_count=len(context.articles),
        processed_count=context.processed_count,
        rejection_stats=context.rejection_stats,
    )

    if len(context.articles) < maximum_articles:
        logger.warning(
            "%s : %s article(s) valide(s) trouvé(s) sur %s demandés.",
            source_name,
            len(context.articles),
            maximum_articles,
        )


# Extraction

def extract_dataset_from_source(
    source: Mapping[str, Any],
    adapter: DatasetAdapter
) -> ExtractorResult:
    """Extrait un dataset en utilisant son adaptateur."""

    # Vérifie les objets reçus avant de commencer l'extraction
    if not isinstance(adapter, DatasetAdapter):
        raise TypeError("adapter doit être une instance de DatasetAdapter.")

    if not isinstance(source, Mapping):
        raise TypeError(
            f"Configuration invalide pour {adapter.default_name} : "
            f"{type(source).__name__}."
        )

    started_at = perf_counter()

    # source_id représente l'identifiant technique utilisé dans les articles
    # source_name représente uniquement le nom lisible utilisé dans les logs
    source_id = normalize_value(source.get("source_id")) or adapter.source_id
    source_name = normalize_value(source.get("name")) or adapter.default_name

    metadata: dict[str, Any] = {
        "source_id": source_id,
        "adapter_id": adapter.source_id,
        "requests_count": 0
    }

    if source_id != adapter.source_id:
        logger.warning(
            "L'adaptateur %s est utilisé avec la source %s.",
            adapter.source_id,
            source_id
        )

    # Arrête proprement l'extracteur lorsque la source est désactivée
    if not parse_boolean(source.get("enabled"), default=False):
        logger.info("Dataset désactivé : %s.", source_name)

        return build_disabled_result(
            name=source_name,
            source_type=SOURCE_TYPE_DATASET,
            message="Dataset désactivé.",
            metadata=metadata
        )

    maximum_articles = get_dataset_max_articles(source)
    metadata["max_articles"] = maximum_articles

    if maximum_articles <= 0:
        logger.info("Aucun article demandé pour %s.", source_name)

        return build_empty_result(
            name=source_name,
            source_type=SOURCE_TYPE_DATASET,
            message="Aucun article demandé.",
            duration_seconds=perf_counter() - started_at,
            requests_count=0,
            metadata=metadata
        )

    # Recherche et valide les fichiers nécessaires à l'adaptateur
    dataset_directory = resolve_dataset_directory(source, source_name)
    dataset_files = get_dataset_files(
        adapter,
        dataset_directory,
        source,
        source_name
    )

    metadata.update({
        "dataset_directory": str(dataset_directory),
        "dataset_files": [str(path) for path in dataset_files],
        "files_count": len(dataset_files)
    })

    filters = get_dataset_filters(source)
    remove_duplicates = parse_boolean(
        filters.get("remove_duplicates", True),
        default=True
    )
    context = DatasetExtractionContext()

    logger.info(
        "%s fichier(s) exploitable(s) détecté(s) pour %s.",
        len(dataset_files),
        source_name
    )

    items = adapter.iter_items(dataset_files, source)

    if items is None:
        raise RuntimeError(
            f"L'adaptateur {source_id} n'a retourné aucun itérateur."
        )

    errors: list[str] = []

    try:
        for raw_item in items:
            if len(context.articles) >= maximum_articles:
                break

            # Un adaptateur doit toujours produire :
            # identifiant, index, contenu de l'élément
            if not isinstance(raw_item, tuple) or len(raw_item) != 3:
                context.processed_count += 1
                context.reject("", "invalid_iteration_item")

                logger.warning(
                    "Élément d'itération invalide retourné par %s.",
                    adapter.source_id
                )
                continue

            item_identifier, item_index, item = raw_item
            normalized_index = parse_non_negative_integer(
                item_index,
                default=context.processed_count
            )

            # L'identifiant technique est transmis à la construction de l'article.
            # Le nom lisible reste réservé aux logs et aux résultats d'exécution.
            process_dataset_item(
                adapter,
                item,
                normalized_index,
                item_identifier,
                source,
                source_id,
                filters,
                remove_duplicates,
                context
            )

    except (
        TypeError,
        ValueError,
        AttributeError,
        KeyError,
        OSError
    ) as error:
        error_message = (
            f"Extraction interrompue pendant la lecture de "
            f"{source_name} : {error}"
        )
        logger.exception(error_message)
        errors.append(error_message)

    log_dataset_summary(
        source_name,
        maximum_articles,
        context
    )

    duration_seconds = perf_counter() - started_at
    rejected_count = sum(context.rejection_stats.values())

    result_kwargs = {
        "name": source_name,
        "source_type": SOURCE_TYPE_DATASET,
        "articles": context.articles,
        "duration_seconds": duration_seconds,
        "analyzed_count": context.processed_count,
        "rejected_count": rejected_count,
        "requests_count": 0,
        "rejection_reasons": dict(context.rejection_stats),
        "metadata": metadata
    }

    # Une erreur survenue après plusieurs extractions produit un succès partiel
    if context.articles and errors:
        return build_partial_result(
            **result_kwargs,
            errors=errors,
            message=(
                f"{len(context.articles)} article(s) extrait(s) avec "
                f"{len(errors)} erreur(s)."
            )
        )

    if context.articles:
        return build_success_result(
            **result_kwargs,
            message=f"{len(context.articles)} article(s) extrait(s)."
        )

    return build_empty_result(
        name=source_name,
        source_type=SOURCE_TYPE_DATASET,
        message=errors[0] if errors else "Aucun article valide extrait.",
        duration_seconds=duration_seconds,
        analyzed_count=context.processed_count,
        rejected_count=rejected_count,
        requests_count=0,
        rejection_reasons=dict(context.rejection_stats),
        metadata=metadata
    )