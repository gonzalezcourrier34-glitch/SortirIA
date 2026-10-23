"""Résultats métier communs utilisés par les extracteurs.

Définit `ExtractorResult`, le résultat homogène renvoyé par tout extracteur, ses
constructeurs par statut (succès, partiel, vide, échec, désactivé, robots.txt) et
la conversion des anciens retours (simple liste d'articles) vers ce format.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/core/extractor_results.py` (commit 93632c5).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from config.constants import (
    EXTRACTOR_STATUSES,
    EXTRACTOR_STATUS_DISABLED,
    EXTRACTOR_STATUS_EMPTY,
    EXTRACTOR_STATUS_FAILED,
    EXTRACTOR_STATUS_PARTIAL_SUCCESS,
    EXTRACTOR_STATUS_ROBOTS_DENIED,
    EXTRACTOR_STATUS_SUCCESS,
    SOURCE_TYPES,
)
from src.logger import get_logger

logger = get_logger(__name__)

REJECTION_REASON_INVALID_ARTICLE = "invalid_article"
REJECTION_REASON_ROBOTS_DENIED = "robots_denied"
REJECTION_REASON_UNKNOWN = "unknown"


@dataclass(slots=True)
class ExtractorResult:
    """Résultat métier homogène produit par un extracteur.

    Les compteurs sont rendus cohérents à la création : `extracted_count` suit la
    liste d'articles, les rejets suivent les motifs, et un statut « succès » sans
    article devient « vide ».

    Attributes
    ----------
    name : str
        Nom de l'extracteur ; obligatoire.
    source_type : str
        Type de source, parmi `SOURCE_TYPES`.
    status : str
        Statut d'exécution, parmi `EXTRACTOR_STATUSES`.
    message : str
        Message lisible accompagnant le statut.
    articles : list[dict[str, Any]]
        Éléments extraits (copiés ; les non-dictionnaires sont rejetés).
    errors : list[str]
        Messages d'erreur non vides.
    duration_seconds : float
        Durée de l'extraction, positive ou nulle.
    analyzed_count : int
        Nombre d'éléments examinés, au moins extraits + rejetés.
    extracted_count : int
        Nombre d'éléments extraits, recalculé depuis `articles`.
    rejected_count : int
        Nombre d'éléments rejetés.
    requests_count : int
        Nombre de requêtes émises.
    rejection_reasons : dict[str, int]
        Compteur des motifs de rejet.
    metadata : dict[str, Any]
        Métadonnées libres (source, cause d'échec...).
    """

    name: str
    source_type: str
    status: str
    message: str = ""
    articles: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    analyzed_count: int = 0
    extracted_count: int = 0
    rejected_count: int = 0
    requests_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise les données et garantit la cohérence du résultat.

        Raises
        ------
        ValueError
            Si le nom est vide, si le type de source ou le statut est inconnu, ou
            si un compteur ou la durée est négatif.
        TypeError
            Si un champ liste, dictionnaire, compteur ou durée n'a pas le bon type.
        """

        # Nettoyer et valider les champs d'identification et les types
        self.name = str(self.name or "").strip()
        self.source_type = str(self.source_type or "").strip().casefold()
        self.status = str(self.status or "").strip().casefold()
        self.message = str(self.message or "").strip()

        if not self.name:
            raise ValueError("Le nom de l'extracteur ne peut pas être vide.")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"Type de source invalide : {self.source_type or '<vide>'}")
        if self.status not in EXTRACTOR_STATUSES:
            raise ValueError(f"Statut d'extracteur invalide : {self.status or '<vide>'}")
        if not isinstance(self.articles, list):
            raise TypeError("articles doit être une liste.")
        if not isinstance(self.errors, list):
            raise TypeError("errors doit être une liste.")
        if not isinstance(self.rejection_reasons, Mapping):
            raise TypeError("rejection_reasons doit être un dictionnaire.")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata doit être un dictionnaire.")

        # Copier les articles valides et compter les éléments non conformes
        received_count = len(self.articles)
        self.articles = [
            article.copy() if isinstance(article, dict) else dict(article)
            for article in self.articles
            if isinstance(article, Mapping)
        ]
        ignored_count = received_count - len(self.articles)

        # Normaliser erreurs, durée, compteurs et motifs
        self.errors = [
            normalized
            for error in self.errors
            if (normalized := str(error or "").strip())
        ]
        self.duration_seconds = self._normalize_duration(self.duration_seconds)
        self.analyzed_count = self._normalize_counter(self.analyzed_count, "analyzed_count")
        self.rejected_count = self._normalize_counter(self.rejected_count, "rejected_count")
        self.requests_count = self._normalize_counter(self.requests_count, "requests_count")
        self.rejection_reasons = self._normalize_rejection_reasons(self.rejection_reasons)
        self.metadata = dict(self.metadata)
        self.extracted_count = len(self.articles)

        # Aligner rejets et motifs : l'écart non expliqué va dans « unknown »
        if ignored_count:
            self.rejected_count += ignored_count
            self.add_rejection_reason(REJECTION_REASON_INVALID_ARTICLE, ignored_count)

        reasons_total = sum(self.rejection_reasons.values())

        if reasons_total > self.rejected_count:
            self.rejected_count = reasons_total
        elif self.rejected_count > reasons_total:
            self.add_rejection_reason(
                REJECTION_REASON_UNKNOWN,
                self.rejected_count - reasons_total,
            )

        self.analyzed_count = max(
            self.analyzed_count,
            self.extracted_count + self.rejected_count,
        )

        # Corriger le statut : succès sans article = vide, partiel sans erreur = succès
        if self.status == EXTRACTOR_STATUS_SUCCESS and not self.articles:
            self.status = EXTRACTOR_STATUS_EMPTY
        if self.status == EXTRACTOR_STATUS_PARTIAL_SUCCESS and not self.errors:
            self.status = EXTRACTOR_STATUS_SUCCESS if self.articles else EXTRACTOR_STATUS_EMPTY

    @staticmethod
    def _normalize_counter(value: Any, field_name: str) -> int:
        """Normalise un compteur entier positif ou nul.

        Parameters
        ----------
        value : Any
            Valeur du compteur.
        field_name : str
            Nom du champ, repris dans les messages d'erreur.

        Returns
        -------
        int
            Le compteur validé.

        Raises
        ------
        TypeError
            Si la valeur n'est pas un entier (un booléen est refusé).
        ValueError
            Si la valeur est négative.
        """

        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} doit être un entier.")
        if value < 0:
            raise ValueError(f"{field_name} ne peut pas être négatif.")
        return value

    @staticmethod
    def _normalize_duration(value: Any) -> float:
        """Normalise une durée positive, finie ou nulle.

        Parameters
        ----------
        value : Any
            Durée en secondes.

        Returns
        -------
        float
            La durée convertie en flottant.

        Raises
        ------
        TypeError
            Si la valeur n'est pas un nombre (un booléen est refusé).
        ValueError
            Si la durée est négative, infinie ou NaN.
        """

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
        """Normalise les motifs de rejet et fusionne les clés équivalentes.

        Les motifs vides et les compteurs nuls sont écartés.

        Parameters
        ----------
        reasons : Mapping[Any, Any]
            Compteur brut `{motif: nombre}`.

        Returns
        -------
        dict[str, int]
            Le compteur avec des motifs en minuscules.

        Raises
        ------
        TypeError
            Si un compteur n'est pas un entier.
        ValueError
            Si un compteur est négatif.
        """

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

    def add_rejection_reason(self, reason: str, count: int = 1) -> None:
        """Ajoute un nombre de rejets à un motif normalisé.

        Ne modifie que `rejection_reasons`, pas `rejected_count`.

        Parameters
        ----------
        reason : str
            Motif de rejet ; ignoré s'il est vide.
        count : int
            Nombre de rejets à ajouter. Par défaut 1.
        """

        normalized_reason = str(reason or "").strip().casefold()
        normalized_count = self._normalize_counter(count, "count")

        if normalized_reason and normalized_count:
            self.rejection_reasons[normalized_reason] = (
                self.rejection_reasons.get(normalized_reason, 0) + normalized_count
            )

    def merge_rejection_reasons(self, reasons: Mapping[str, int]) -> None:
        """Fusionne de nouveaux motifs de rejet dans le résultat.

        Les compteurs de rejets et d'éléments analysés sont relevés si besoin.

        Parameters
        ----------
        reasons : Mapping[str, int]
            Compteur `{motif: nombre}` à ajouter.

        Raises
        ------
        TypeError
            Si `reasons` n'est pas un dictionnaire.
        """

        if not isinstance(reasons, Mapping):
            raise TypeError("reasons doit être un dictionnaire.")

        for reason, count in self._normalize_rejection_reasons(reasons).items():
            self.add_rejection_reason(reason, count)

        self.rejected_count = max(
            self.rejected_count,
            sum(self.rejection_reasons.values()),
        )
        self.analyzed_count = max(
            self.analyzed_count,
            self.extracted_count + self.rejected_count,
        )

    def merge_metadata(self, metadata: Mapping[str, Any]) -> None:
        """Ajoute les métadonnées absentes sans écraser les données existantes.

        Parameters
        ----------
        metadata : Mapping[str, Any]
            Métadonnées à ajouter.

        Raises
        ------
        TypeError
            Si `metadata` n'est pas un dictionnaire.
        """

        if not isinstance(metadata, Mapping):
            raise TypeError("metadata doit être un dictionnaire.")

        for key, value in metadata.items():
            self.metadata.setdefault(key, value)

    @property
    def main_reason(self) -> str:
        """Retourne le motif de rejet dominant.

        Returns
        -------
        str
            Le motif le plus fréquent (ordre alphabétique en cas d'égalité), ou une
            chaîne vide s'il n'y a aucun rejet.
        """

        if not self.rejection_reasons:
            return ""

        return min(
            self.rejection_reasons,
            key=lambda reason: (-self.rejection_reasons[reason], reason),
        )

    @property
    def success_rate(self) -> float:
        """Retourne le taux d'articles extraits parmi les articles analysés.

        Returns
        -------
        float
            Pourcentage arrondi à 2 décimales ; 0.0 si rien n'a été analysé.
        """

        return (
            round(self.extracted_count * 100 / self.analyzed_count, 2)
            if self.analyzed_count
            else 0.0
        )

    @property
    def rejection_rate(self) -> float:
        """Retourne le taux d'articles rejetés parmi les articles analysés.

        Returns
        -------
        float
            Pourcentage arrondi à 2 décimales ; 0.0 si rien n'a été analysé.
        """

        return (
            round(self.rejected_count * 100 / self.analyzed_count, 2)
            if self.analyzed_count
            else 0.0
        )

    @property
    def successful_operations(self) -> int:
        """Retourne le nombre d'opérations réussies (alias de `extracted_count`).

        Returns
        -------
        int
            Le nombre d'éléments extraits.
        """
        return self.extracted_count

    @property
    def failed_operations(self) -> int:
        """Retourne le nombre d'opérations échouées (alias de `rejected_count`).

        Returns
        -------
        int
            Le nombre d'éléments rejetés.
        """
        return self.rejected_count

    @property
    def has_articles(self) -> bool:
        """Indique si au moins un article a été extrait.

        Returns
        -------
        bool
            True si `articles` n'est pas vide.
        """
        return bool(self.articles)

    @property
    def has_errors(self) -> bool:
        """Indique si le résultat contient au moins un message d'erreur.

        Returns
        -------
        bool
            True si `errors` n'est pas vide.
        """
        return bool(self.errors)

    @property
    def is_successful(self) -> bool:
        """Indique si l'extraction a réussi, totalement ou partiellement.

        Returns
        -------
        bool
            True pour les statuts succès et succès partiel.
        """
        return self.status in {
            EXTRACTOR_STATUS_SUCCESS,
            EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        }

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le résultat avec ses métriques calculées.

        Returns
        -------
        dict[str, Any]
            Les champs du résultat, plus `main_reason`, `success_rate` et
            `rejection_rate`.
        """

        data = asdict(self)
        data.update({
            "main_reason": self.main_reason,
            "success_rate": self.success_rate,
            "rejection_rate": self.rejection_rate,
        })
        return data


RawExtractorResult = list[dict[str, Any]] | ExtractorResult
ArticleExtractor = Callable[[], RawExtractorResult]
ConfiguredExtractor = Callable[[Mapping[str, Any]], RawExtractorResult]
ConfiguredSourcesExtractor = Callable[[Sequence[Mapping[str, Any]]], RawExtractorResult]


# Constructeurs
def build_failed_result(
    name: str,
    source_type: str,
    message: str,
    duration_seconds: float = 0.0,
    analyzed_count: int = 0,
    rejected_count: int = 0,
    requests_count: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExtractorResult:
    """Construit un résultat correspondant à un échec.

    Parameters
    ----------
    name : str
        Nom de l'extracteur.
    source_type : str
        Type de source.
    message : str
        Cause lisible de l'échec, reprise aussi comme unique erreur.
    duration_seconds : float
        Durée de la tentative. Par défaut 0.
    analyzed_count : int
        Nombre d'éléments examinés. Par défaut 0.
    rejected_count : int
        Nombre d'éléments rejetés. Par défaut 0.
    requests_count : int
        Nombre de requêtes émises. Par défaut 0.
    rejection_reasons : Mapping[str, int] | None
        Compteur des motifs de rejet. Par défaut aucun.
    metadata : dict[str, Any] | None
        Métadonnées libres. Par défaut aucune.

    Returns
    -------
    ExtractorResult
        Un résultat au statut « échec ».
    """

    normalized_message = str(message or "").strip()
    return ExtractorResult(
        name=name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_FAILED,
        message=normalized_message,
        errors=[normalized_message] if normalized_message else [],
        duration_seconds=duration_seconds,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=dict(rejection_reasons or {}),
        metadata=metadata or {},
    )


def build_disabled_result(
    name: str,
    source_type: str,
    message: str = "Source désactivée.",
    metadata: dict[str, Any] | None = None,
) -> ExtractorResult:
    """Construit le résultat d'un extracteur désactivé.

    Parameters
    ----------
    name : str
        Nom de l'extracteur.
    source_type : str
        Type de source.
    message : str
        Message lisible. Par défaut « Source désactivée. ».
    metadata : dict[str, Any] | None
        Métadonnées libres. Par défaut aucune.

    Returns
    -------
    ExtractorResult
        Un résultat au statut « désactivé », sans article.
    """

    return ExtractorResult(
        name=name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_DISABLED,
        message=message,
        metadata=metadata or {},
    )


def build_empty_result(
    name: str,
    source_type: str,
    message: str = "",
    duration_seconds: float = 0.0,
    analyzed_count: int = 0,
    rejected_count: int = 0,
    requests_count: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExtractorResult:
    """Construit un résultat valide ne contenant aucun article.

    Parameters
    ----------
    name : str
        Nom de l'extracteur.
    source_type : str
        Type de source.
    message : str
        Message lisible. Par défaut vide.
    duration_seconds : float
        Durée de l'extraction. Par défaut 0.
    analyzed_count : int
        Nombre d'éléments examinés. Par défaut 0.
    rejected_count : int
        Nombre d'éléments rejetés. Par défaut 0.
    requests_count : int
        Nombre de requêtes émises. Par défaut 0.
    rejection_reasons : Mapping[str, int] | None
        Compteur des motifs de rejet. Par défaut aucun.
    metadata : dict[str, Any] | None
        Métadonnées libres. Par défaut aucune.

    Returns
    -------
    ExtractorResult
        Un résultat au statut « vide ».
    """

    return ExtractorResult(
        name=name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_EMPTY,
        message=message,
        duration_seconds=duration_seconds,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=dict(rejection_reasons or {}),
        metadata=metadata or {},
    )


def build_robots_denied_result(
    name: str,
    source_type: str,
    message: str = "Accès refusé par robots.txt.",
    duration_seconds: float = 0.0,
    analyzed_count: int = 0,
    rejected_count: int = 1,
    requests_count: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExtractorResult:
    """Construit un résultat lorsque robots.txt interdit l'extraction.

    Sans motif fourni, les rejets sont attribués à « robots_denied ».

    Parameters
    ----------
    name : str
        Nom de l'extracteur.
    source_type : str
        Type de source.
    message : str
        Message lisible. Par défaut « Accès refusé par robots.txt. ».
    duration_seconds : float
        Durée de la tentative. Par défaut 0.
    analyzed_count : int
        Nombre d'éléments examinés. Par défaut 0.
    rejected_count : int
        Nombre d'éléments rejetés. Par défaut 1.
    requests_count : int
        Nombre de requêtes émises. Par défaut 0.
    rejection_reasons : Mapping[str, int] | None
        Compteur des motifs de rejet. Par défaut aucun.
    metadata : dict[str, Any] | None
        Métadonnées libres. Par défaut aucune.

    Returns
    -------
    ExtractorResult
        Un résultat au statut « robots_denied ».
    """

    reasons = dict(rejection_reasons or {})

    if rejected_count and not reasons:
        reasons[REJECTION_REASON_ROBOTS_DENIED] = rejected_count

    return ExtractorResult(
        name=name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_ROBOTS_DENIED,
        message=message,
        duration_seconds=duration_seconds,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=reasons,
        metadata=metadata or {},
    )


def build_success_result(
    name: str,
    source_type: str,
    articles: list[dict[str, Any]],
    duration_seconds: float = 0.0,
    analyzed_count: int = 0,
    rejected_count: int = 0,
    requests_count: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
    message: str = "",
) -> ExtractorResult:
    """Construit un résultat d'extraction réussi.

    Parameters
    ----------
    name : str
        Nom de l'extracteur.
    source_type : str
        Type de source.
    articles : list[dict[str, Any]]
        Éléments extraits ; une liste vide donne un statut « vide ».
    duration_seconds : float
        Durée de l'extraction. Par défaut 0.
    analyzed_count : int
        Nombre d'éléments examinés. Par défaut 0.
    rejected_count : int
        Nombre d'éléments rejetés. Par défaut 0.
    requests_count : int
        Nombre de requêtes émises. Par défaut 0.
    rejection_reasons : Mapping[str, int] | None
        Compteur des motifs de rejet. Par défaut aucun.
    metadata : dict[str, Any] | None
        Métadonnées libres. Par défaut aucune.
    message : str
        Message lisible. Par défaut vide.

    Returns
    -------
    ExtractorResult
        Un résultat au statut « succès » (ou « vide » sans article).
    """

    return ExtractorResult(
        name=name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_SUCCESS,
        message=message,
        articles=articles,
        duration_seconds=duration_seconds,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=dict(rejection_reasons or {}),
        metadata=metadata or {},
    )


def build_partial_result(
    name: str,
    source_type: str,
    articles: list[dict[str, Any]],
    errors: list[str],
    duration_seconds: float = 0.0,
    analyzed_count: int = 0,
    rejected_count: int = 0,
    requests_count: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
    message: str = "",
) -> ExtractorResult:
    """Construit un résultat partiellement réussi.

    Parameters
    ----------
    name : str
        Nom de l'extracteur.
    source_type : str
        Type de source.
    articles : list[dict[str, Any]]
        Éléments extraits.
    errors : list[str]
        Erreurs rencontrées ; sans erreur, le statut devient succès ou vide.
    duration_seconds : float
        Durée de l'extraction. Par défaut 0.
    analyzed_count : int
        Nombre d'éléments examinés. Par défaut 0.
    rejected_count : int
        Nombre d'éléments rejetés. Par défaut 0.
    requests_count : int
        Nombre de requêtes émises. Par défaut 0.
    rejection_reasons : Mapping[str, int] | None
        Compteur des motifs de rejet. Par défaut aucun.
    metadata : dict[str, Any] | None
        Métadonnées libres. Par défaut aucune.
    message : str
        Message lisible. Par défaut vide.

    Returns
    -------
    ExtractorResult
        Un résultat au statut « succès partiel ».
    """

    return ExtractorResult(
        name=name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_PARTIAL_SUCCESS,
        message=message,
        articles=articles,
        errors=errors,
        duration_seconds=duration_seconds,
        analyzed_count=analyzed_count,
        rejected_count=rejected_count,
        requests_count=requests_count,
        rejection_reasons=dict(rejection_reasons or {}),
        metadata=metadata or {},
    )


# Compatibilité
def normalize_extracted_articles(
    extracted_articles: Any,
    extractor_name: str,
) -> tuple[list[dict[str, Any]], int]:
    """Normalise les articles et retourne le nombre d'éléments rejetés.

    Parameters
    ----------
    extracted_articles : Any
        Retour brut de l'extracteur : None ou une liste d'éléments.
    extractor_name : str
        Nom de l'extracteur, pour les journaux et le message d'erreur.

    Returns
    -------
    tuple[list[dict[str, Any]], int]
        Les copies des éléments de type dictionnaire, et le nombre d'éléments
        écartés.

    Raises
    ------
    TypeError
        Si le retour n'est ni None ni une liste.
    """

    # Accepter None (aucun article) mais refuser tout autre type qu'une liste
    if extracted_articles is None:
        logger.debug("L'extracteur %s a retourné None.", extractor_name)
        return [], 0

    if not isinstance(extracted_articles, list):
        raise TypeError(
            f"L'extracteur {extractor_name} doit retourner une liste "
            f"ou un ExtractorResult, pas {type(extracted_articles).__name__}."
        )

    # Copier les éléments de type dictionnaire et compter les autres
    articles = [
        article.copy() if isinstance(article, dict) else dict(article)
        for article in extracted_articles
        if isinstance(article, Mapping)
    ]
    rejected_count = len(extracted_articles) - len(articles)

    if rejected_count:
        logger.debug(
            "%s élément(s) non conforme(s) ignoré(s) pour %s.",
            rejected_count,
            extractor_name,
        )

    return articles, rejected_count


def normalize_extractor_result(
    raw_result: Any,
    extractor_name: str,
    source_type: str,
) -> ExtractorResult:
    """Convertit un ancien retour en résultat métier homogène.

    Parameters
    ----------
    raw_result : Any
        Retour de l'extracteur : un `ExtractorResult`, une liste d'articles ou None.
    extractor_name : str
        Nom de l'extracteur, utilisé quand le retour est une simple liste.
    source_type : str
        Type de source, utilisé quand le retour est une simple liste.

    Returns
    -------
    ExtractorResult
        Une copie nettoyée du résultat, ou un résultat succès/vide construit
        depuis la liste.

    Raises
    ------
    TypeError
        Si le retour n'est ni un `ExtractorResult`, ni une liste, ni None.
    """

    # Résultat déjà homogène : le recopier en écartant les articles non conformes
    if isinstance(raw_result, ExtractorResult):
        articles, ignored_count = normalize_extracted_articles(
            raw_result.articles,
            extractor_name,
        )
        rejection_reasons = dict(raw_result.rejection_reasons)

        if ignored_count:
            rejection_reasons[REJECTION_REASON_INVALID_ARTICLE] = (
                rejection_reasons.get(REJECTION_REASON_INVALID_ARTICLE, 0)
                + ignored_count
            )

        return ExtractorResult(
            name=raw_result.name,
            source_type=raw_result.source_type,
            status=raw_result.status,
            message=raw_result.message,
            articles=articles,
            errors=list(raw_result.errors),
            duration_seconds=raw_result.duration_seconds,
            analyzed_count=raw_result.analyzed_count,
            rejected_count=raw_result.rejected_count + ignored_count,
            requests_count=raw_result.requests_count,
            rejection_reasons=rejection_reasons,
            metadata=dict(raw_result.metadata),
        )

    # Ancien format (liste d'articles) : construire un résultat succès ou vide
    articles, rejected_count = normalize_extracted_articles(raw_result, extractor_name)
    rejection_reasons = (
        {REJECTION_REASON_INVALID_ARTICLE: rejected_count}
        if rejected_count
        else {}
    )
    return ExtractorResult(
        name=extractor_name,
        source_type=source_type,
        status=EXTRACTOR_STATUS_SUCCESS if articles else EXTRACTOR_STATUS_EMPTY,
        articles=articles,
        analyzed_count=len(raw_result) if isinstance(raw_result, list) else 0,
        rejected_count=rejected_count,
        rejection_reasons=rejection_reasons,
    )