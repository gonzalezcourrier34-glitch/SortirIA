"""Modèles de données communs utilisés par les extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/core/extractor_models.py` (commit 93632c5).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, TypeVar

from config.constants import (
    EXTRACTOR_STATUSES,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_SUCCESS,
    SOURCE_TYPES,
)


@dataclass(frozen=True, slots=True)
class ExtractorConfiguration:
    """Configuration commune à toutes les familles d'extracteurs."""

    name: str
    source_id: str
    source_type: str
    enabled: bool = True
    language: str | None = None
    country: str | None = None
    category: str | None = None
    role: str | None = None
    max_articles: int | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise et valide les métadonnées communes."""

        name = str(self.name or "").strip()
        source_id = str(self.source_id or "").strip()
        source_type = str(self.source_type or "").strip().casefold()

        if not name:
            raise ValueError("Le nom de la source ne peut pas être vide.")
        if not source_id:
            raise ValueError("L'identifiant de la source ne peut pas être vide.")
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"Type de source invalide : {source_type or '<vide>'}")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled doit être un booléen.")
        if self.max_articles is not None:
            if isinstance(self.max_articles, bool) or not isinstance(self.max_articles, int):
                raise TypeError("max_articles doit être un entier ou None.")
            if self.max_articles <= 0:
                raise ValueError("max_articles doit être strictement positif.")
        if not isinstance(self.filters, dict):
            raise TypeError("filters doit être un dictionnaire.")
        if not isinstance(self.configuration, dict):
            raise TypeError("configuration doit être un dictionnaire.")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_type", source_type)

        for field_name in ("language", "country", "category", "role"):
            object.__setattr__(
                self,
                field_name,
                self._normalize_optional_text(getattr(self, field_name)),
            )

        object.__setattr__(self, "filters", dict(self.filters))
        object.__setattr__(self, "configuration", dict(self.configuration))

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        """Normalise une métadonnée textuelle facultative."""

        normalized = str(value or "").strip()
        return normalized or None

    def is_enabled(self) -> bool:
        """Indique si la source doit être exécutée."""

        return self.enabled

    def to_dict(self) -> dict[str, Any]:
        """Convertit la configuration en dictionnaire indépendant."""

        data = asdict(self)
        specific_configuration = data.pop("configuration")
        return {**specific_configuration, **data}


@dataclass(frozen=True, slots=True)
class ExtractorExecution:
    """Rapport synthétique utilisé par le service d'orchestration."""

    extractor_name: str
    source_type: str
    status: str
    article_count: int
    duration_seconds: float
    message: str = ""
    errors: tuple[str, ...] = ()
    analyzed_count: int = 0
    rejected_count: int = 0
    requests_count: int = 0
    main_reason: str = ""
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Valide, normalise et détache les données du rapport."""

        extractor_name = str(self.extractor_name or "").strip()
        source_type = str(self.source_type or "").strip().casefold()
        status = str(self.status or "").strip().casefold()

        if not extractor_name:
            raise ValueError("Le nom de l'extracteur ne peut pas être vide.")
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"Type de source invalide : {source_type or '<vide>'}")
        if status not in EXTRACTOR_STATUSES:
            raise ValueError(f"Statut d'extracteur invalide : {status or '<vide>'}")

        duration = self._normalize_duration(self.duration_seconds)
        article_count = self._normalize_counter(self.article_count, "article_count")
        analyzed_count = self._normalize_counter(self.analyzed_count, "analyzed_count")
        rejected_count = self._normalize_counter(self.rejected_count, "rejected_count")
        requests_count = self._normalize_counter(self.requests_count, "requests_count")
        rejection_reasons = self._normalize_rejection_reasons(self.rejection_reasons)
        reasons_total = sum(rejection_reasons.values())

        if reasons_total > rejected_count:
            rejected_count = reasons_total
        elif rejected_count > reasons_total:
            rejection_reasons["unknown"] = (
                rejection_reasons.get("unknown", 0) + rejected_count - reasons_total
            )

        analyzed_count = max(analyzed_count, article_count + rejected_count)
        main_reason = str(self.main_reason or "").strip().casefold()

        metadata = dict(self.metadata)

        if not main_reason and rejection_reasons:
            main_reason = min(
                rejection_reasons,
                key=lambda reason: (-rejection_reasons[reason], reason)
            )

        if not main_reason:
            main_reason = str(metadata.get("failure_reason") or "").strip().casefold()

        object.__setattr__(self, "extractor_name", extractor_name)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "article_count", article_count)
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "analyzed_count", analyzed_count)
        object.__setattr__(self, "rejected_count", rejected_count)
        object.__setattr__(self, "requests_count", requests_count)
        object.__setattr__(self, "main_reason", main_reason)
        object.__setattr__(self, "rejection_reasons", rejection_reasons)
        object.__setattr__(
            self,
            "errors",
            tuple(
                normalized
                for error in self.errors
                if (normalized := str(error or "").strip())
            ),
        )
        object.__setattr__(self, "metadata", metadata)

    @staticmethod
    def _normalize_counter(value: Any, field_name: str) -> int:
        """Normalise un compteur entier positif ou nul."""

        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} doit être un entier.")
        if value < 0:
            raise ValueError(f"{field_name} ne peut pas être négatif.")
        return value

    @staticmethod
    def _normalize_duration(value: Any) -> float:
        """Normalise une durée positive, finie ou nulle."""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("duration_seconds doit être un nombre.")

        duration = float(value)

        if duration < 0 or not math.isfinite(duration):
            raise ValueError("duration_seconds doit être positive, finie ou nulle.")
        return duration

    @classmethod
    def _normalize_rejection_reasons(
        cls,
        reasons: Mapping[Any, Any],
    ) -> dict[str, int]:
        """Normalise et fusionne les motifs de rejet."""

        if not isinstance(reasons, Mapping):
            raise TypeError("rejection_reasons doit être un dictionnaire.")

        normalized_reasons: dict[str, int] = {}

        for reason, count in reasons.items():
            normalized_reason = str(reason or "").strip().casefold()

            if not normalized_reason:
                continue

            normalized_count = cls._normalize_counter(
                count,
                f"rejection_reasons[{normalized_reason}]",
            )

            if normalized_count:
                normalized_reasons[normalized_reason] = (
                    normalized_reasons.get(normalized_reason, 0) + normalized_count
                )

        return normalized_reasons

    @property
    def success_rate(self) -> float:
        """Retourne le taux d'articles extraits."""

        return (
            round(self.article_count * 100 / self.analyzed_count, 2)
            if self.analyzed_count
            else 0.0
        )

    @property
    def rejection_rate(self) -> float:
        """Retourne le taux d'articles rejetés."""

        return (
            round(self.rejected_count * 100 / self.analyzed_count, 2)
            if self.analyzed_count
            else 0.0
        )

    @property
    def successful_operations(self) -> int:
        return self.article_count

    @property
    def failed_operations(self) -> int:
        return self.rejected_count

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def is_successful(self) -> bool:
        return self.status in {
            EXTRACTOR_STATUS_SUCCESS,
            EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        }

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le rapport avec ses métriques calculées."""

        data = asdict(self)
        data.update({
            "success_rate": self.success_rate,
            "rejection_rate": self.rejection_rate,
        })
        return data


SourceConfiguration = Mapping[str, Any] | ExtractorConfiguration
SourceCollection = Sequence[SourceConfiguration]
SourceLoader = Callable[[], SourceConfiguration | None]
SourceCollectionLoader = Callable[[], SourceCollection]
ConfigurationType = TypeVar("ConfigurationType")