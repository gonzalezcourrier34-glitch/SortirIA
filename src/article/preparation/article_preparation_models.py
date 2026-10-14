"""Modèles métier liés à la préparation des articles.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/article/preparation/article_preparation_models.py` (commit 93632c5).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ArticlePreparationReport:
    """Rapport détaillé de préparation d'une collection d'articles."""

    received: int = 0
    normalized: int = 0
    normalization_ignored: int = 0
    cleaned: int = 0
    cleaning_ignored: int = 0
    duplicates: int = 0
    invalid: int = 0
    kept: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class ArticlePreparationResult:
    """Résultat complet retourné par le service de préparation."""

    articles: list[dict[str, Any]]
    report: ArticlePreparationReport
