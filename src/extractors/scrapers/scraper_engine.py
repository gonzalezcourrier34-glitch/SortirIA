"""Moteur commun utilisé par les extracteurs HTML CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_engine.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.constants import SOURCE_TYPE_SCRAPER
from config.paths import SOURCES_FILE
from config.source_config import load_validated_source
from src.article.processing.article_deduplicator import is_duplicate_article, register_article
from src.extractors.core.extractor_executor import execute_configured_extractor
from src.extractors.core.extractor_results import (
    ExtractorResult,
    build_empty_result,
    build_failed_result,
    build_partial_result,
    build_success_result
)
from src.extractors.scrapers.scraper_adapter import ScraperAdapter, ScraperItem

from src.extractors.scrapers.scraper_http_utils import (
    ScraperPageError,
    get_scraper_requests_count,
    reset_scraper_requests_count
)

from src.logger import get_logger
from src.utils.extractor_utils import log_extraction_summary, validate_article
from src.utils.filter_utils import get_filter_configuration
from src.utils.parsing_utils import parse_boolean
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)


# Contexte
@dataclass(slots=True)
class ScraperExtractionContext:
    """Regroupe l'état d'une extraction HTML.

    Un contexte est créé par exécution : il porte la configuration de la
    source, les articles retenus, les clés de déduplication et les compteurs
    qui alimentent le résultat final.

    Attributes
    ----------
    source : dict[str, Any]
        Configuration de la source en cours d'extraction.
    filters : dict[str, Any]
        Filtres de validation lus dans la configuration.
    maximum_articles : int
        Nombre d'articles au-delà duquel l'extraction s'arrête.
    remove_duplicates : bool
        Active la déduplication des articles.
    articles : list[dict[str, Any]]
        Articles retenus. Vide au départ.
    seen_keys : set[str]
        Clés de déduplication déjà rencontrées.
    rejection_stats : Counter[str]
        Nombre de rejets par motif.
    errors : list[str]
        Erreurs non bloquantes rencontrées pendant l'extraction.
    processed_count : int
        Nombre d'éléments HTML examinés. Par défaut 0.
    requests_count : int
        Nombre de requêtes HTTP réellement tentées. Par défaut 0.
    """

    source: dict[str, Any]
    filters: dict[str, Any]
    maximum_articles: int
    remove_duplicates: bool
    articles: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    rejection_stats: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    processed_count: int = 0
    requests_count: int = 0

    @property
    def source_name(self) -> str:
        """Retourne le nom lisible de la source.

        Returns
        -------
        str
            Le nom configuré, ou « source HTML » à défaut.
        """

        return normalize_value(self.source.get("name")) or "source HTML"

    @property
    def source_id(self) -> str:
        """Retourne l'identifiant technique de la source.

        Returns
        -------
        str
            Le `source_id` configuré, ou « scraper » à défaut.
        """

        return normalize_value(self.source.get("source_id")) or "scraper"


    @property
    def source_type(self) -> str:
        """Retourne le type technique de la source.

        Returns
        -------
        str
            Toujours `SOURCE_TYPE_SCRAPER`.
        """

        return SOURCE_TYPE_SCRAPER

    @property
    def completed(self) -> bool:
        """Indique si la limite d'articles est atteinte.

        Returns
        -------
        bool
            Vrai dès que `maximum_articles` articles ont été retenus.
        """

        return len(self.articles) >= self.maximum_articles

    @property
    def rejected_count(self) -> int:
        """Retourne le nombre total d'éléments rejetés.

        Returns
        -------
        int
            Somme des rejets, tous motifs confondus.
        """

        return sum(self.rejection_stats.values())

    def reject(self, reason: str) -> None:
        """Enregistre un rejet.

        Parameters
        ----------
        reason : str
            Motif du rejet ; « article_invalide » s'il est vide.
        """

        self.rejection_stats[normalize_value(reason) or "article_invalide"] += 1

    def add_error(self, error: Exception | str) -> None:
        """Enregistre une erreur d'extraction non bloquante.

        Parameters
        ----------
        error : Exception | str
            Erreur ou message ; un message vide est ignoré.
        """

        message = normalize_value(str(error))
        if message:
            self.errors.append(message)

    def add_article(self, article: dict[str, Any]) -> None:
        """Ajoute un article et enregistre sa clé de déduplication.

        Parameters
        ----------
        article : dict[str, Any]
            Article validé à conserver.
        """

        if self.remove_duplicates:
            register_article(article, self.seen_keys)
        self.articles.append(article)


# Extracteur
@dataclass(slots=True)
class ScraperExtractor:
    """Représente un extracteur HTML configurable et exécutable.

    L'adaptateur décrit comment parcourir et transformer les pages d'une
    source ; l'extracteur charge la configuration validée de cette source et
    délègue le travail au moteur commun.

    Attributes
    ----------
    adapter : ScraperAdapter
        Adaptateur propre à la source.
    sources_file : Path
        Fichier de configuration des sources. Par défaut `SOURCES_FILE`.
    _source : dict[str, Any]
        Cache interne de la configuration validée (hors constructeur).
    """

    adapter: ScraperAdapter
    sources_file: Path = SOURCES_FILE
    _source: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Valide la configuration de l'extracteur.

        Raises
        ------
        TypeError
            Si `adapter` n'est pas un `ScraperAdapter`.
        ValueError
            Si `sources_file` vaut `None`.
        """

        if not isinstance(self.adapter, ScraperAdapter):
            raise TypeError("adapter doit être une instance de ScraperAdapter.")
        if self.sources_file is None:
            raise ValueError("Le fichier de configuration des sources est requis.")

    def load_source(self, force_reload: bool = False) -> dict[str, Any]:
        """Charge et met en cache la configuration validée.

        Parameters
        ----------
        force_reload : bool
            Relit le fichier même si la configuration est en cache.
            Par défaut False.

        Returns
        -------
        dict[str, Any]
            Une copie de la configuration de la source.
        """

        if self._source and not force_reload:
            return dict(self._source)

        self._source = dict(
            load_validated_source(self.sources_file, self.adapter.source_id)
        )
        return dict(self._source)

    def reload_source(self) -> dict[str, Any]:
        """Recharge explicitement la configuration.

        Returns
        -------
        dict[str, Any]
            Une copie de la configuration relue sur disque.
        """

        return self.load_source(force_reload=True)

    def get_source(self) -> dict[str, Any]:
        """Retourne la configuration disponible.

        Returns
        -------
        dict[str, Any]
            Une copie de la configuration, chargée si besoin.
        """

        return self.load_source()

    @property
    def source(self) -> Mapping[str, Any]:
        """Retourne la configuration courante.

        Returns
        -------
        Mapping[str, Any]
            Une copie de la configuration, chargée si besoin.
        """

        return self.load_source()

    def extract(self, source: Mapping[str, Any]) -> ExtractorResult:
        """Lance l'extraction HTML avec l'adaptateur configuré.

        Parameters
        ----------
        source : Mapping[str, Any]
            Configuration de la source à extraire.

        Returns
        -------
        ExtractorResult
            Le résultat de `extract_scraper_source`.
        """

        return extract_scraper_source(source, self.adapter)

    def run(self) -> ExtractorResult:
        """Charge la configuration puis exécute l'extracteur.

        Returns
        -------
        ExtractorResult
            Le résultat produit par `execute_configured_extractor`.
        """

        return execute_configured_extractor(
            extractor_name=self.adapter.default_name,
            source_type=SOURCE_TYPE_SCRAPER,
            source_loader=self.reload_source,
            extraction_function=self.extract
        )


# Préparation
def create_context(
    source: Mapping[str, Any],
    maximum_articles: int
) -> ScraperExtractionContext:
    """Construit le contexte d'extraction HTML.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source.
    maximum_articles : int
        Nombre maximal d'articles à retenir.

    Returns
    -------
    ScraperExtractionContext
        Un contexte vierge ; la déduplication est active sauf si les filtres
        disent `remove_duplicates: false`.
    """

    normalized_source = dict(source)
    filters = dict(get_filter_configuration(normalized_source))

    return ScraperExtractionContext(
        source=normalized_source,
        filters=filters,
        maximum_articles=maximum_articles,
        remove_duplicates=parse_boolean(
            filters.get("remove_duplicates", True),
            default=True
        )
    )


# Validation
def validate_scraper_item(
    item: ScraperItem,
    context: ScraperExtractionContext,
    adapter: ScraperAdapter
) -> tuple[bool, str]:
    """Applique la validation spécifique d'un élément HTML.

    Parameters
    ----------
    item : ScraperItem
        Élément produit par l'adaptateur.
    context : ScraperExtractionContext
        Contexte d'extraction en cours.
    adapter : ScraperAdapter
        Adaptateur de la source ; sans `validate_item`, tout est accepté.

    Returns
    -------
    tuple[bool, str]
        Validité de l'élément et motif du rejet le cas échéant.
    """

    if adapter.validate_item is None:
        return True, ""
    return adapter.validate_item(item, context.source)


def validate_scraper_article(
    article: Mapping[str, Any],
    context: ScraperExtractionContext,
    adapter: ScraperAdapter
) -> tuple[bool, str]:
    """Applique les validations spécifique puis commune.

    Parameters
    ----------
    article : Mapping[str, Any]
        Article construit à partir d'un élément HTML.
    context : ScraperExtractionContext
        Contexte d'extraction (filtres de validation).
    adapter : ScraperAdapter
        Adaptateur de la source, avec son éventuel `validate_article`.

    Returns
    -------
    tuple[bool, str]
        Validité de l'article et motif du rejet le cas échéant.
    """

    if adapter.validate_article is not None:
        valid, reason = adapter.validate_article(article, context.source)
        if not valid:
            return False, reason

    return validate_article(article, context.filters)


# Traitement
def process_scraper_item(
    item: ScraperItem,
    context: ScraperExtractionContext,
    adapter: ScraperAdapter
) -> None:
    """Transforme, valide et conserve un élément HTML.

    Chaque échec (élément invalide, construction impossible, article vide ou
    invalide, doublon) est compté comme rejet dans le contexte, sans
    interrompre l'extraction.

    Parameters
    ----------
    item : ScraperItem
        Élément produit par l'adaptateur.
    context : ScraperExtractionContext
        Contexte d'extraction, mis à jour en place.
    adapter : ScraperAdapter
        Adaptateur de la source.
    """

    context.processed_count += 1

    # Valider l'élément brut avant de construire l'article
    valid, reason = validate_scraper_item(
        item,
        context,
        adapter
    )

    if not valid:
        context.reject(reason or "element_invalide")
        return

    # Construit l'article à partir de l'élément HTML
    try:
        article = adapter.build_article(
            item,
            context.source
        )

    except ScraperPageError as error:
        logger.warning(
            "Article HTML ignoré pour %s : %s",
            context.source_name,
            error
        )
        context.reject(error.reason)
        return

    except Exception as error:
        logger.exception(
            "Impossible de construire un article HTML pour %s : %s",
            context.source_name,
            error
        )
        context.reject("construction_invalide")
        return

    if not isinstance(article, dict) or not article:
        context.reject("article_vide")
        return

    # Certains adaptateurs utilisent "source" pour stocker le média
    # (Reuters, AFP, Full Fact...). Le moteur impose ensuite
    # l'identifiant technique commun utilisé dans tout le pipeline.
    publisher = normalize_value(
        article.get("publisher") or article.get("source")
    )

    if (
        publisher
        and context.source_id
        and publisher.casefold() != context.source_id.casefold()
    ):
        article["publisher"] = publisher

    article["source"] = context.source_id
    article["source_type"] = SOURCE_TYPE_SCRAPER
    article.setdefault("source_name", context.source_name)

    # Valider l'article complet, puis écarter les doublons
    valid, reason = validate_scraper_article(
        article,
        context,
        adapter
    )

    if not valid:
        context.reject(reason or "article_invalide")
        return

    if (
        context.remove_duplicates
        and is_duplicate_article(
            article,
            context.seen_keys
        )
    ):
        context.reject("doublon")
        return

    context.add_article(article)

# Résultat
def build_scraper_result(
    context: ScraperExtractionContext,
    adapter: ScraperAdapter
) -> ExtractorResult:
    """Construit le résultat métier complet de l'extraction HTML.

    Articles et erreurs donnent un résultat partiel, articles seuls un succès,
    erreurs seules un échec, et rien du tout un résultat vide.

    Parameters
    ----------
    context : ScraperExtractionContext
        Contexte d'extraction terminé.
    adapter : ScraperAdapter
        Adaptateur de la source (nom par défaut de l'extracteur).

    Returns
    -------
    ExtractorResult
        Le résultat avec ses compteurs et métadonnées.
    """

    # Réunir les compteurs et métadonnées communs à tous les cas
    common_arguments = {
        "name": adapter.default_name,
        "source_type": SOURCE_TYPE_SCRAPER,
        "analyzed_count": context.processed_count,
        "rejected_count": context.rejected_count,
        "requests_count": context.requests_count,
        "rejection_reasons": dict(context.rejection_stats),
        "metadata": {
            "source_id": normalize_value(context.source.get("source_id")),
            "source_name": context.source_name,
            "maximum_articles": context.maximum_articles
        }
    }

    # Choisir le type de résultat selon articles et erreurs
    if context.articles and context.errors:
        return build_partial_result(
            articles=context.articles,
            errors=context.errors,
            message="Extraction HTML partiellement réussie.",
            **common_arguments
        )

    if context.articles:
        return build_success_result(
            articles=context.articles,
            **common_arguments
        )

    if context.errors:
        return build_failed_result(
            message=context.errors[0],
            **common_arguments
        )

    return build_empty_result(
        message="Aucun article HTML valide extrait.",
        **common_arguments
    )


# Extraction
def extract_scraper_source(
    source: Mapping[str, Any],
    adapter: ScraperAdapter
) -> ExtractorResult:
    """Extrait, valide et déduplique une source HTML.

    Les erreurs survenues pendant le parcours ne remontent pas : elles sont
    journalisées et consignées dans le résultat, qui garde les articles déjà
    retenus.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source.
    adapter : ScraperAdapter
        Adaptateur qui parcourt les éléments et construit les articles.

    Returns
    -------
    ExtractorResult
        Le résultat de l'extraction (vide si la source est désactivée ou si
        aucun article n'est demandé).

    Raises
    ------
    TypeError
        Si `source` n'est pas un `Mapping` ou `adapter` pas un `ScraperAdapter`.
    """

    # Validation des entrées
    if not isinstance(source, Mapping):
        raise TypeError(f"Configuration HTML invalide : {type(source).__name__}.")
    if not isinstance(adapter, ScraperAdapter):
        raise TypeError("adapter doit être une instance de ScraperAdapter.")

    # Construction du contexte
    from src.extractors.scrapers.scraper_extractor import get_max_articles

    context = create_context(source, maximum_articles=get_max_articles(source))

    # Gestion des sources inactives
    if not parse_boolean(context.source.get("enabled"), default=True):
        logger.info("Source HTML désactivée : %s.", context.source_name)
        return build_empty_result(
            name=adapter.default_name,
            source_type=SOURCE_TYPE_SCRAPER,
            message="Source HTML désactivée.",
            metadata={
                "source_id": normalize_value(context.source.get("source_id")),
                "source_name": context.source_name
            }
        )

    # Gestion d'une limite nulle
    if context.maximum_articles <= 0:
        logger.info("Aucun article demandé pour %s.", context.source_name)
        return build_empty_result(
            name=adapter.default_name,
            source_type=SOURCE_TYPE_SCRAPER,
            message="Aucun article demandé.",
            metadata={
                "source_id": normalize_value(context.source.get("source_id")),
                "source_name": context.source_name
            }
        )

    # Initialisation de l'extraction et des métriques HTTP
    logger.info("Début de l'extraction HTML : %s.", context.source_name)
    reset_scraper_requests_count()

    try:
        # Création et parcours différé de l'itérateur
        items = adapter.iter_items(context.source)

        for item in items:
            if context.completed:
                break
            process_scraper_item(item, context, adapter)

    except ScraperPageError as error:
        # Erreur HTTP ou HTML contrôlée
        logger.warning(
            "Extraction HTML interrompue pour %s : %s",
            context.source_name,
            error
        )
        context.add_error(error)

    except Exception as error:
        # Erreur technique inattendue
        logger.exception(
            "Erreur pendant l'extraction HTML de %s : %s",
            context.source_name,
            error
        )
        context.add_error(error)

    finally:
        # Conservation systématique du nombre réel de tentatives HTTP
        context.requests_count = get_scraper_requests_count()

    # Journalisation du résultat métier
    log_extraction_summary(
        source_name=context.source_name,
        extracted_count=len(context.articles),
        processed_count=context.processed_count,
        rejection_stats=context.rejection_stats
    )

    return build_scraper_result(context, adapter)


# Fonctions publiques
def extract_all_articles(extractor: ScraperExtractor) -> ExtractorResult:
    """Exécute un extracteur HTML configuré.

    Parameters
    ----------
    extractor : ScraperExtractor
        Extracteur à lancer.

    Returns
    -------
    ExtractorResult
        Le résultat de `ScraperExtractor.run`.

    Raises
    ------
    TypeError
        Si `extractor` n'est pas un `ScraperExtractor`.
    """

    if not isinstance(extractor, ScraperExtractor):
        raise TypeError("extractor doit être une instance de ScraperExtractor.")
    return extractor.run()


def extract_articles_from_source(
    source: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Extrait directement les articles d'une source HTML standard.

    Conservée pour l'ancienne API publique : crée un adaptateur standard et ne
    renvoie que la liste des articles.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source.

    Returns
    -------
    list[dict[str, Any]]
        Les articles extraits.

    Raises
    ------
    TypeError
        Si `source` n'est pas un `Mapping`.
    """

    # Validation de la source
    if not isinstance(source, Mapping):
        raise TypeError("source doit être une structure de type Mapping.")

    # Construction de l'adaptateur standard
    from src.extractors.scrapers.scraper_extractor import (
        create_standard_scraper_adapter
    )

    adapter = create_standard_scraper_adapter(
        source_id=normalize_value(source.get("source_id")) or "scraper",
        default_name=normalize_value(source.get("name")) or "Scraper HTML"
    )

    # Compatibilité avec l'ancienne API publique
    return extract_scraper_source(source, adapter).articles