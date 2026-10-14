"""Services utilisés par l'agent : extraction, base de données, embeddings.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026). Comme dans
Chess Agent (projet 13), les services sont regroupés dans un objet injecté au
graphe : les nœuds restent testables avec de faux services, sans réseau ni base.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.event.event_normalizer import PARIS

# Modèle d'embeddings Mistral (même valeur par défaut que app/config.py).
DEFAULT_EMBEDDING_MODEL = "mistral-embed"


@dataclass(frozen=True, slots=True)
class ExtractionOutput:
    """Ce que renvoie une extraction, quel que soit le moteur utilisé."""

    articles: list[dict[str, Any]]
    failed_sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CollectionServices:
    """Regroupe les dépendances de l'agent."""

    # Lance les extracteurs demandés.
    extract: Callable[[Sequence[str]], ExtractionOutput]
    # Ouvre une connexion PostgreSQL (gestionnaire de contexte : commit / fermeture).
    connect: Callable[[], AbstractContextManager[Any]]
    # Calcule les embeddings d'une liste de textes.
    embed: Callable[[list[str]], list[list[float]]]
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # Sources lancées chaque nuit.
    default_sources: tuple[str, ...] = ("openagenda", "datatourisme", "ical")
    # Horloge (remplaçable dans les tests).
    now: Callable[[], datetime] = lambda: datetime.now(PARIS)
    # Dit si une source a au moins une configuration active (par défaut : oui).
    is_configured: Callable[[str], bool] = lambda name: True


# ---------------------------------------------------------------------------
# Implémentations réelles (utilisées en production)
# ---------------------------------------------------------------------------

def extract_with_checkit(source_names: Sequence[str]) -> ExtractionOutput:
    """Lance les extracteurs du moteur CheckIt.AI et renvoie leurs résultats."""

    from src.extractor_execution_service import extract_all_sources, get_failed_extractor_names

    result = extract_all_sources(list(source_names), fail_if_empty=False, fail_on_extractor_error=False)
    return ExtractionOutput(
        articles=list(result.articles),
        failed_sources=list(get_failed_extractor_names(result.extractor_results)),
    )


@contextmanager
def connect_postgres() -> Iterable[Any]:
    """Ouvre une connexion PostgreSQL (DATABASE_URL), validée si tout se passe bien."""

    import psycopg2

    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def embed_with_mistral(texts: list[str]) -> list[list[float]]:
    """Calcule les embeddings avec Mistral (clé MISTRAL_API_KEY)."""

    from langchain_mistralai import MistralAIEmbeddings

    model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    return MistralAIEmbeddings(model=model).embed_documents(texts)


def source_is_configured(name: str) -> bool:
    """Vrai si `config/sources.yaml` déclare au moins une source active pour cet extracteur.

    « ical » regroupe tous les calendriers (section `ical_sources`) ; pour les
    API, on cherche une source active portant cet identifiant.
    """

    from config.paths import SOURCES_FILE
    from config.source_config import load_source_registry

    registry = load_source_registry(SOURCES_FILE, require_secrets=False)
    if name == "ical":
        return any(source.enabled for source in registry.by_type("ical"))
    return any(source.enabled and source.source_id == name for source in registry.sources)


def build_default_services() -> CollectionServices:
    """Services réels : moteur CheckIt.AI, PostgreSQL, Mistral."""

    return CollectionServices(
        extract=extract_with_checkit,
        connect=connect_postgres,
        embed=embed_with_mistral,
        embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        is_configured=source_is_configured,
    )
