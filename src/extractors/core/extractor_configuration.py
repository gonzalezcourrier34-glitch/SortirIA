"""Gestion commune des configurations des extracteurs CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/core/extractor_configuration.py` (commit 93632c5).

Ce module centralise la normalisation, les métadonnées et le chargement
sécurisé des configurations. Il ne réalise aucune extraction d'article.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from config.source_config import SourceConfig, SourceConfigurationError, SourceRegistry
from src.extractors.core.extractor_models import ExtractorConfiguration
from src.extractors.core.extractor_results import ExtractorResult, build_failed_result
from src.logger import get_logger

logger = get_logger(__name__)

ConfigurationItem = Mapping[str, Any] | SourceConfig | ExtractorConfiguration | Any
ConfigurationObject = ConfigurationItem | Sequence[ConfigurationItem] | SourceRegistry


# Normalise une configuration typée ou dictionnaire

def configuration_to_dict(source: Any) -> dict[str, Any] | None:
    """Convertit une configuration prise en charge en dictionnaire indépendant.

    Formes acceptées, dans l'ordre : dictionnaire, objet avec `to_dict()`,
    modèle Pydantic (`model_dump()`), instance de dataclass.

    Parameters
    ----------
    source : Any
        La configuration à convertir.

    Returns
    -------
    dict[str, Any] | None
        Une copie sous forme de dictionnaire, ou `None` si la forme n'est pas prise en charge.
    """

    if isinstance(source, Mapping):
        return dict(source)

    to_dict = getattr(source, "to_dict", None)
    if callable(to_dict):
        configuration = to_dict()
        return dict(configuration) if isinstance(configuration, Mapping) else None

    model_dump = getattr(source, "model_dump", None)
    if callable(model_dump):
        configuration = model_dump()
        return dict(configuration) if isinstance(configuration, Mapping) else None

    if is_dataclass(source) and not isinstance(source, type):
        configuration = asdict(source)
        return dict(configuration) if isinstance(configuration, Mapping) else None

    return None


def normalize_source_configuration(
    source: Any,
    extractor_name: str
) -> dict[str, Any] | None:
    """Convertit une configuration valide en dictionnaire indépendant.

    Parameters
    ----------
    source : Any
        La configuration à convertir.
    extractor_name : str
        Nom de l'extracteur, pour le journal.

    Returns
    -------
    dict[str, Any] | None
        Le dictionnaire, ou `None` (avec un avertissement) si la configuration
        est vide ou d'une forme non prise en charge.
    """

    configuration = configuration_to_dict(source)

    if not configuration:
        logger.warning(
            "Configuration invalide ignorée pour %s : %s.",
            extractor_name,
            type(source).__name__
        )
        return None

    return configuration


def is_configuration_item(source: Any) -> bool:
    """Indique si une valeur représente une configuration exploitable.

    Parameters
    ----------
    source : Any
        La valeur à tester.

    Returns
    -------
    bool
        Vrai si `configuration_to_dict` sait la convertir (même vide).
    """

    return configuration_to_dict(source) is not None


def build_source_metadata(
    source: ConfigurationItem,
    source_type: str | None = None
) -> dict[str, Any]:
    """Extrait les métadonnées communes d'une configuration.

    Parameters
    ----------
    source : ConfigurationItem
        La configuration de la source.
    source_type : str | None
        Type de source utilisé si la configuration n'en déclare pas. Par défaut `None`.

    Returns
    -------
    dict[str, Any]
        Identifiant, type, langue, pays, catégorie, rôle et nombre maximal
        d'articles, sans les valeurs absentes.

    Raises
    ------
    TypeError
        Si la configuration est vide ou d'une forme non prise en charge.
    """

    configuration = normalize_source_configuration(source, extractor_name="métadonnées")

    if configuration is None:
        raise TypeError(
            "source doit être une configuration non vide exposant "
            "un dictionnaire, to_dict(), model_dump() ou une dataclass."
        )

    resolved_type = (
        configuration.get("type")
        or configuration.get("source_type")
        or source_type
    )
    metadata = {
        "source_id": configuration.get("source_id") or configuration.get("id"),
        "source_type": resolved_type,
        "language": configuration.get("language"),
        "country": configuration.get("country"),
        "category": configuration.get("category"),
        "role": configuration.get("role"),
        "max_articles": configuration.get("max_articles")
    }

    return {key: value for key, value in metadata.items() if value is not None}


# Construit un échec homogène

def build_configuration_failure(
    extractor_name: str,
    source_type: str,
    message: str
) -> tuple[None, ExtractorResult]:
    """Construit un résultat d'échec de chargement homogène.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur.
    source_type : str
        Type de la source.
    message : str
        Message d'erreur reporté dans le résultat.

    Returns
    -------
    tuple[None, ExtractorResult]
        `None` à la place de la configuration, et le résultat en échec.
    """

    return None, build_failed_result(
        name=extractor_name,
        source_type=source_type,
        message=message
    )


# Valide le résultat retourné par un chargeur

def validate_loaded_configuration(configuration: Any) -> ConfigurationObject:
    """Valide la structure retournée par un chargeur de configuration.

    Parameters
    ----------
    configuration : Any
        Ce que le chargeur a renvoyé : registre, configuration seule ou
        collection de configurations.

    Returns
    -------
    ConfigurationObject
        La configuration, inchangée si elle est valide.

    Raises
    ------
    ValueError
        Si la configuration, le registre ou la collection sont vides.
    TypeError
        Si la collection contient des éléments invalides ou si le type est inattendu.
    """

    # Cas vide, puis registre de sources
    if configuration is None:
        raise ValueError("Le chargeur a retourné une configuration vide.")

    if isinstance(configuration, SourceRegistry):
        if not configuration.sources:
            raise ValueError("Le chargeur a retourné un registre de sources vide.")
        return configuration

    # Configuration seule
    if is_configuration_item(configuration):
        return configuration

    # Collection : non vide, et chaque élément exploitable
    if isinstance(configuration, Sequence) and not isinstance(configuration, (str, bytes)):
        if not configuration:
            raise ValueError("Le chargeur a retourné une collection de sources vide.")

        invalid_types = sorted({
            type(source).__name__
            for source in configuration
            if not is_configuration_item(source)
        })

        if invalid_types:
            raise TypeError(
                "La collection contient des configurations invalides : "
                + ", ".join(invalid_types)
                + "."
            )

        return configuration

    raise TypeError(
        "Le chargeur a retourné un type de configuration inattendu : "
        f"{type(configuration).__name__}."
    )


# Charge la configuration et convertit les erreurs

def load_extractor_configuration(
    extractor_name: str,
    source_type: str,
    configuration_loader: Callable[[], ConfigurationObject]
) -> tuple[ConfigurationObject | None, ExtractorResult | None]:
    """Charge une configuration et convertit les erreurs en résultat métier.

    Aucune exception ne remonte : toute erreur de chargement devient un
    résultat en échec, pour que les autres extracteurs puissent tourner.

    Parameters
    ----------
    extractor_name : str
        Nom de l'extracteur, pour le journal et le résultat.
    source_type : str
        Type de la source, reporté dans un éventuel échec.
    configuration_loader : Callable[[], ConfigurationObject]
        Fonction sans argument qui renvoie la configuration.

    Returns
    -------
    tuple[ConfigurationObject | None, ExtractorResult | None]
        La configuration validée et `None`, ou `None` et le résultat en échec.
    """

    # Refuser un chargeur qui n'est pas appelable
    if not callable(configuration_loader):
        error = TypeError("Le chargeur de configuration doit être appelable.")
        logger.error("Configuration invalide pour %s : %s", extractor_name, error)
        return build_configuration_failure(
            extractor_name,
            source_type,
            f"Chargement de la configuration impossible : {error}"
        )

    # Charger et valider ; chaque famille d'erreur donne un message propre
    try:
        configuration = configuration_loader()
        return validate_loaded_configuration(configuration), None

    except SourceConfigurationError as error:
        logger.error(
            "Configuration des sources invalide pour %s :\n%s",
            extractor_name,
            error
        )
        return build_configuration_failure(
            extractor_name,
            source_type,
            f"Configuration des sources invalide : {error}"
        )

    except (TypeError, ValueError) as error:
        logger.error("Configuration invalide pour %s : %s", extractor_name, error)
        return build_configuration_failure(
            extractor_name,
            source_type,
            f"Chargement de la configuration impossible : {error}"
        )

    except Exception as error:
        logger.exception("Impossible de charger la configuration de %s.", extractor_name)
        return build_configuration_failure(
            extractor_name,
            source_type,
            f"Chargement de la configuration impossible : {error}"
        )