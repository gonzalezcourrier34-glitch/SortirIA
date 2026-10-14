"""
Service de filtrage structuré pour le pipeline RAG OpenAgenda.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/filter_service.py` (commit 18f7ce1).

Ce module intervient avant la recherche vectorielle afin de réduire
le corpus candidat lorsque la question contient des contraintes fortes.

Rôle du service
---------------
Ce service permet de :

- normaliser la question utilisateur
- extraire des filtres structurés depuis cette question
- appliquer ces filtres sur les documents candidats
- produire, si besoin, une version détaillée du filtrage pour le debug

Philosophie
-----------
Le filtrage doit rester utile sans devenir trop agressif :

- lorsqu'une contrainte est explicite, on filtre fortement
- lorsqu'une information documentaire est absente, on reste plutôt souple
- on évite de supprimer trop tôt un bon document à cause d'une métadonnée incomplète

Ce service ne calcule aucun score métier.
Il se contente de sélectionner les documents compatibles avec les
contraintes explicites de la question.

Principes de conception
-----------------------
Le service suit quelques règles simples :

- le filtrage fort doit reposer sur des signaux explicites
- la logique doit rester lisible et explicable
- les champs documentaires enrichis dans `document_service`
  doivent être réutilisés autant que possible
- les filtres faibles ne doivent pas casser le rappel
- les étapes du pipeline doivent pouvoir être inspectées facilement
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from langchain_core.documents import Document

from app.lexical_service import LexicalService


class FilterService:
    """
    Service de préfiltrage structuré des documents.

    Cette classe est utilisée avant la recherche vectorielle afin de
    réduire le bruit documentaire lorsque l'utilisateur exprime des
    contraintes fortes dans sa question.

    L'idée n'est pas de classer les documents, mais simplement de
    retirer ceux qui semblent manifestement incompatibles avec la demande.
    """
    # Cette classe sert de garde-barrière avant la recherche vectorielle.
    # Elle ne classe pas les documents, elle enlève surtout
    # ceux qui sont clairement incompatibles avec la demande.

    MONTHS = {
        "janvier": 1,
        "fevrier": 2,
        "mars": 3,
        "avril": 4,
        "mai": 5,
        "juin": 6,
        "juillet": 7,
        "aout": 8,
        "septembre": 9,
        "octobre": 10,
        "novembre": 11,
        "decembre": 12,
    }
    # Table simple de conversion nom de mois -> numéro.

    KNOWN_CITIES = {
        "montpellier",
        "paris",
        "sete",
        "toulouse",
        "lyon",
        "marseille",
        "bordeaux",
        "lille",
        "nantes",
    }
    # Liste de villes connues détectables lexicalement dans la question.

    STRONG_CULTURAL_EVENT_TYPES = {
        "exposition",
        "concert",
        "festival",
        "projection",
        "spectacle",
        "conte",
        "lecture",
    }
    # Types culturels considérés comme forts.

    WEAK_CULTURAL_EVENT_TYPES = {
        "atelier",
        "conference",
        "visite",
    }
    # Types culturels plus faibles, à traiter avec prudence.

    WEEKEND_TIME_MODES = {
        "weekend_explicit",
        "weekend_this",
        "weekend_next",
    }
    # Modes temporels utilisés pour reconnaître
    # une logique spéciale de filtrage sur week-end.

    def __init__(self) -> None:
        """
        Initialise le service de filtrage avec son service lexical partagé.
        """
        # Le filtrage s'appuie sur le service lexical
        # pour garantir une logique homogène avec les autres couches.
        self.lexical_service = LexicalService()

    # ------------------------------------------------------------------
    # Utilitaires de base
    # ------------------------------------------------------------------

    def _safe(self, value: object) -> str:
        """
        Convertit une valeur potentiellement nulle en chaîne.

        Parameters
        ----------
        value : object
            Valeur brute.

        Returns
        -------
        str
            Chaîne vide si la valeur est nulle, sinon conversion textuelle.
        """
        # Petit garde-fou local pour éviter les None
        # dans les opérations de parsing.
        return "" if value is None else str(value)

    def normalize_text(self, text: object) -> str:
        """
        Normalise un texte pour faciliter les comparaisons lexicales.

        Cette méthode délègue au service lexical partagé afin de garantir
        une cohérence complète avec les autres couches du pipeline.

        Parameters
        ----------
        text : object
            Texte brut.

        Returns
        -------
        str
            Texte normalisé.
        """
        # Point d'entrée local vers la normalisation commune.
        return self.lexical_service.normalize_text(text)

    def parse_iso_date(self, value: object) -> date | None:
        """
        Convertit une date ISO en objet `date`.

        Parameters
        ----------
        value : object
            Valeur supposée contenir une date ISO.

        Returns
        -------
        date | None
            Date convertie si possible, sinon None.
        """
        # Sert à convertir les dates documentaires stockées en chaîne
        # vers un format exploitable pour les comparaisons temporelles.
        raw = self._safe(value).strip()
        if not raw:
            return None

        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def _build_date(self, year: int, month: int, day: int) -> date | None:
        """
        Construit une date de manière sécurisée.

        Parameters
        ----------
        year : int
            Année.
        month : int
            Mois.
        day : int
            Jour.

        Returns
        -------
        date | None
            Date valide, sinon None.
        """
        # Empêche qu'une date invalide
        # casse l'extraction des filtres temporels.
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Lecture des métadonnées documentaires
    # ------------------------------------------------------------------

    def _metadata(self, doc: Document) -> dict[str, Any]:
        """
        Retourne les métadonnées du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        dict[str, Any]
            Dictionnaire de métadonnées.
        """
        # Accès centralisé aux métadonnées pour éviter
        # de répéter doc.metadata or {} partout.
        return doc.metadata or {}

    def _doc_dates(self, doc: Document) -> tuple[date | None, date | None]:
        """
        Retourne les bornes temporelles d'un document.

        Si seule la date de début est disponible, l'événement est
        considéré comme mono-jour.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        tuple[date | None, date | None]
            Date de début, date de fin.
        """
        # Helper central pour lire les dates documentaires
        # de façon cohérente dans toutes les fonctions de matching temporel.
        md = self._metadata(doc)
        first_date = self.parse_iso_date(md.get("first_date"))
        last_date = self.parse_iso_date(md.get("last_date"))

        if first_date and not last_date:
            last_date = first_date

        return first_date, last_date

    def _doc_city(self, doc: Document) -> str:
        """
        Retourne la ville normalisée d'un document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        str
            Ville normalisée.
        """
        # Privilégie la version déjà normalisée si elle existe,
        # sinon normalise le champ brut.
        md = self._metadata(doc)
        return md.get("city_norm") or self.normalize_text(md.get("city", ""))

    def _doc_event_type(self, doc: Document) -> str:
        """
        Retourne le type d'événement normalisé d'un document.

        On privilégie le type canonique lorsqu'il est disponible.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        str
            Type d'événement normalisé.
        """
        # On lit d'abord le type canonique car il est plus stable
        # que le type brut pour le filtrage métier.
        md = self._metadata(doc)
        return (
            md.get("canonical_event_type_norm")
            or self.normalize_text(md.get("canonical_event_type", ""))
            or md.get("event_type_norm")
            or self.normalize_text(md.get("event_type", ""))
        )

    def _doc_music_genre(self, doc: Document) -> str:
        """
        Retourne le genre musical normalisé d'un document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        str
            Genre musical normalisé.
        """
        # Même logique que pour le type d'événement,
        # mais appliquée au genre musical.
        md = self._metadata(doc)
        return md.get("music_genre_norm") or self.normalize_text(md.get("music_genre", ""))

    def _doc_search_text(self, doc: Document) -> str:
        """
        Retourne un texte documentaire consolidé et normalisé.

        Le champ `search_text` est prioritaire car il a été conçu pour
        le matching lexical léger et la recherche sémantique.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        str
            Texte consolidé du document.
        """
        # Fournit un texte unique exploitable pour des vérifications lexicales.
        # search_text est prioritaire car il a été construit
        # précisément pour ce type d'usage.
        md = self._metadata(doc)
        search_text = md.get("search_text")
        if search_text:
            return self.normalize_text(search_text)

        text = (
            f"{md.get('title', '')} "
            f"{md.get('description', '')} "
            f"{md.get('location_name', '')} "
            f"{md.get('city', '')} "
            f"{md.get('event_type', '')} "
            f"{md.get('canonical_event_type', '')} "
            f"{md.get('music_genre', '')} "
            f"{doc.page_content}"
        )
        return self.normalize_text(text)

    def _doc_is_free(self, doc: Document) -> bool | None:
        """
        Retourne l'information de gratuité d'un document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool | None
            True si gratuit, False si payant, None si inconnu.
        """
        # Réutilise directement le signal tarifaire enrichi.
        md = self._metadata(doc)
        return md.get("is_free")

    def _doc_is_single_day(self, doc: Document) -> bool | None:
        """
        Indique si l'événement se déroule sur une seule journée.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool | None
            Booléen fiable si disponible, sinon None.
        """
        # Lit le booléen métier s'il a été calculé proprement en amont.
        md = self._metadata(doc)
        value = md.get("is_single_day")

        if isinstance(value, bool):
            return value

        return None

    def _doc_duration_days(self, doc: Document) -> int | None:
        """
        Retourne la durée de l'événement en jours.

        Si la durée n'est pas disponible directement, elle est recalculée
        à partir des dates du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        int | None
            Durée en jours, ou None si indéterminable.
        """
        # Essaie d'abord de réutiliser la durée calculée en amont,
        # puis recalcule à partir des dates si nécessaire.
        md = self._metadata(doc)
        value = md.get("duration_days")

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            pass

        first_date, last_date = self._doc_dates(doc)
        if not first_date:
            return None

        if not last_date:
            last_date = first_date

        return max(1, (last_date - first_date).days + 1)

    def _doc_derived_event_terms(self, doc: Document) -> set[str]:
        """
        Retourne les termes métier dérivés du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        set[str]
            Ensemble de termes normalisés.
        """
        # Lit les termes dérivés calculés par document_service / lexical_service
        # et les remet sous forme normalisée.
        md = self._metadata(doc)
        terms = md.get("derived_event_terms", [])
        if not isinstance(terms, list):
            return set()

        return {
            self.normalize_text(term)
            for term in terms
            if self.normalize_text(term)
        }

    def _doc_derived_music_terms(self, doc: Document) -> set[str]:
        """
        Retourne les termes musicaux dérivés du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        set[str]
            Ensemble de termes musicaux normalisés.
        """
        # Même principe que pour les termes métier,
        # mais appliqué à la musique.
        md = self._metadata(doc)
        terms = md.get("derived_music_terms", [])
        if not isinstance(terms, list):
            return set()

        return {
            self.normalize_text(term)
            for term in terms
            if self.normalize_text(term)
        }

    def _doc_audience_terms(self, doc: Document) -> set[str]:
        """
        Retourne les termes de public cible du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        set[str]
            Ensemble de termes de public normalisés.
        """
        # Sert au matching audience en réutilisant
        # les signaux déjà enrichis dans les documents.
        md = self._metadata(doc)
        terms = md.get("audience_terms", [])
        if not isinstance(terms, list):
            return set()

        return {
            self.normalize_text(term)
            for term in terms
            if self.normalize_text(term)
        }

    def _doc_is_strong_cultural_candidate(self, doc: Document) -> bool:
        """
        Retourne le drapeau culturel fort du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si le document est marqué comme culturel fort.
        """
        # Réutilise le flag enrichi calculé en amont.
        md = self._metadata(doc)
        return bool(md.get("is_strong_cultural_candidate", False))

    def _doc_is_weak_cultural_candidate(self, doc: Document) -> bool:
        """
        Retourne le drapeau culturel faible du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si le document est marqué comme culturel faible.
        """
        # Même logique, mais pour les cas plus ambigus.
        md = self._metadata(doc)
        return bool(md.get("is_weak_cultural_candidate", False))

    def _doc_has_market_signal(self, doc: Document) -> bool:
        """
        Retourne le drapeau marché / braderie du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si un signal de marché est présent.
        """
        # Sert à exclure des documents peu compatibles
        # avec une recherche culturelle stricte.
        md = self._metadata(doc)
        return bool(md.get("has_market_signal", False))

    def _doc_has_repair_signal(self, doc: Document) -> bool:
        """
        Retourne le drapeau réparation / repair café du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si un signal de réparation est présent.
        """
        # Idem pour les événements de type réparation.
        md = self._metadata(doc)
        return bool(md.get("has_repair_signal", False))

    def _doc_has_business_signal(self, doc: Document) -> bool:
        """
        Retourne le drapeau business / networking du document.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si un signal business est présent.
        """
        # Idem pour les signaux business / pro.
        md = self._metadata(doc)
        return bool(md.get("has_business_signal", False))

    # ------------------------------------------------------------------
    # Extraction des contraintes depuis la question
    # ------------------------------------------------------------------

    def _extract_city(self, question_norm: str) -> str | None:
        """
        Détecte une ville connue dans la question normalisée.

        Parameters
        ----------
        question_norm : str
            Question déjà normalisée.

        Returns
        -------
        str | None
            Ville détectée si présente.
        """
        # Cherche une ville explicite dans la question.
        # Le tri par longueur décroissante évite certains conflits.
        for city in sorted(self.KNOWN_CITIES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(city)}\b", question_norm):
                return city
        return None

    def _extract_duration_filter(self, question_norm: str) -> str | None:
        """
        Détecte une contrainte explicite de durée.

        Parameters
        ----------
        question_norm : str
            Question déjà normalisée.

        Returns
        -------
        str | None
            `"single_day"`, `"multi_day"` ou None.
        """
        # Ne s'active que si la contrainte de durée
        # est explicitement formulée.
        if re.search(
            r"\b(sur une journee|en une journee|a la journee|journee unique|ponctuel)\b",
            question_norm,
        ):
            return "single_day"

        if re.search(
            r"\b(sur plusieurs jours|plusieurs jours)\b",
            question_norm,
        ):
            return "multi_day"

        return None

    # ------------------------------------------------------------------
    # Utilitaires temporels
    # ------------------------------------------------------------------

    def _get_month_bounds(self, year: int, month: int) -> tuple[date, date]:
        """
        Retourne les bornes d'un mois sous forme d'intervalle semi-ouvert.

        Parameters
        ----------
        year : int
            Année.
        month : int
            Mois.

        Returns
        -------
        tuple[date, date]
            Début du mois, début du mois suivant.
        """
        # Produit un intervalle [début du mois ; début du mois suivant[
        # pratique pour tester un chevauchement de période.
        month_start = date(year, month, 1)

        if month == 12:
            month_end = date(year + 1, 1, 1)
        else:
            month_end = date(year, month + 1, 1)

        return month_start, month_end

    def _get_year_bounds(self, year: int) -> tuple[date, date]:
        """
        Retourne les bornes d'une année sous forme d'intervalle semi-ouvert.

        Parameters
        ----------
        year : int
            Année.

        Returns
        -------
        tuple[date, date]
            Début de l'année, début de l'année suivante.
        """
        # Même logique que pour le mois,
        # mais à l'échelle de l'année.
        return date(year, 1, 1), date(year + 1, 1, 1)

    def _get_weekend_range(
        self,
        reference: date,
        next_weekend: bool = False,
    ) -> tuple[date, date]:
        """
        Retourne l'intervalle du week-end correspondant à une date de référence.

        Parameters
        ----------
        reference : date
            Date de référence.
        next_weekend : bool, default=False
            Si True, renvoie le week-end suivant.

        Returns
        -------
        tuple[date, date]
            Samedi, dimanche.
        """
        # Calcule le samedi et le dimanche pertinents
        # à partir d'une date de référence.
        weekday = reference.weekday()

        if weekday == 5:
            saturday = reference
        elif weekday == 6:
            saturday = reference - timedelta(days=1)
        else:
            days_until_saturday = 5 - weekday
            saturday = reference + timedelta(days=days_until_saturday)

        if next_weekend:
            saturday = saturday + timedelta(days=7)

        sunday = saturday + timedelta(days=1)
        return saturday, sunday

    def _extract_explicit_weekend_range(
        self,
        question_norm: str,
    ) -> tuple[date | None, date | None]:
        """
        Extrait un week-end explicitement formulé dans la question.

        Exemples gérés :

        - `week-end du 28 et 29 mars 2026`
        - `week-end du 28 mars 2026`

        Parameters
        ----------
        question_norm : str
            Question normalisée.

        Returns
        -------
        tuple[date | None, date | None]
            Début et fin du week-end si détectés.
        """
        # Détecte un week-end écrit explicitement dans la question
        # et le convertit en bornes calendaires.
        month_names = "|".join(self.MONTHS.keys())

        patterns = [
            rf"\bweek(?:\s|-)?end\s+(?:du|des)\s+(\d{{1,2}})\s*(?:et|au|a|-)\s*(\d{{1,2}})\s+({month_names})\s+(\d{{4}})\b",
            rf"\bweek(?:\s|-)?end\s+(?:du|des)\s+(\d{{1,2}})\s+({month_names})\s+(\d{{4}})\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, question_norm)
            if not match:
                continue

            groups = match.groups()

            if len(groups) == 4:
                day_start = int(groups[0])
                day_end = int(groups[1])
                month_name = groups[2]
                year_num = int(groups[3])

                month_num = self.MONTHS[month_name]
                start_date = self._build_date(year_num, month_num, day_start)
                end_date = self._build_date(year_num, month_num, day_end)

                if start_date and end_date:
                    if start_date <= end_date:
                        return start_date, end_date
                    return end_date, start_date

            if len(groups) == 3:
                day_num = int(groups[0])
                month_name = groups[1]
                year_num = int(groups[2])

                month_num = self.MONTHS[month_name]
                single_date = self._build_date(year_num, month_num, day_num)

                if single_date:
                    if single_date.weekday() == 5:
                        return single_date, single_date + timedelta(days=1)
                    if single_date.weekday() == 6:
                        return single_date - timedelta(days=1), single_date

                    saturday = single_date + timedelta(days=(5 - single_date.weekday()))
                    return saturday, saturday + timedelta(days=1)

        return None, None

    def _matches_weekend_with_max_span(
        self,
        doc: Document,
        start_date: date,
        end_date: date,
        max_span_days: int = 3,
    ) -> bool:
        """
        Vérifie qu'un document chevauche le week-end demandé et reste
        raisonnablement concentré dans le temps.

        Cette contrainte évite de faire remonter de très longs événements
        pour une demande très ciblée sur un week-end.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        start_date : date
            Début du week-end.
        end_date : date
            Fin du week-end.
        max_span_days : int, default=3
            Durée maximale tolérée du document.

        Returns
        -------
        bool
            True si le document est compatible.
        """
        # Cas particulier des requêtes week-end :
        # on veut un chevauchement, mais sans accepter
        # des événements beaucoup trop longs.
        first_date, last_date = self._doc_dates(doc)

        if not first_date:
            return False

        if last_date is None:
            last_date = first_date

        overlaps = first_date <= end_date and last_date >= start_date
        if not overlaps:
            return False

        duration_days = max(1, (last_date - first_date).days + 1)
        return duration_days <= max_span_days

    def _extract_date_filters(self, question_norm: str) -> dict[str, Any]:
        """
        Extrait les filtres temporels présents dans la question.

        Cas gérés :

        - week-end explicite
        - ce week-end
        - week-end prochain
        - mois prochain
        - intervalle de dates
        - date exacte
        - mois + année
        - année seule

        Parameters
        ----------
        question_norm : str
            Question normalisée.

        Returns
        -------
        dict[str, Any]
            Dictionnaire de filtres temporels.
        """
        # Fonction centrale d'analyse temporelle de la question.
        # Elle fabrique un petit paquet de filtres calendaires structurés.
        filters: dict[str, Any] = {
            "exact_date": None,
            "month": None,
            "year": None,
            "start_date": None,
            "end_date": None,
            "time_mode": None,
        }

        today = date.today()
        month_names = "|".join(self.MONTHS.keys())

        explicit_weekend_start, explicit_weekend_end = self._extract_explicit_weekend_range(
            question_norm
        )
        if explicit_weekend_start and explicit_weekend_end:
            filters["start_date"] = explicit_weekend_start
            filters["end_date"] = explicit_weekend_end
            filters["month"] = explicit_weekend_start.month
            filters["year"] = explicit_weekend_start.year
            filters["time_mode"] = "weekend_explicit"
            return filters

        if re.search(r"\bce\s+week(?:\s|-)?end\b", question_norm):
            start_date, end_date = self._get_weekend_range(
                reference=today,
                next_weekend=False,
            )
            filters["start_date"] = start_date
            filters["end_date"] = end_date
            filters["time_mode"] = "weekend_this"
            return filters

        if re.search(r"\bweek(?:\s|-)?end\s+prochain\b", question_norm):
            start_date, end_date = self._get_weekend_range(
                reference=today,
                next_weekend=True,
            )
            filters["start_date"] = start_date
            filters["end_date"] = end_date
            filters["time_mode"] = "weekend_next"
            return filters

        if re.search(r"\bmois\s+prochain\b", question_norm):
            if today.month == 12:
                next_month = 1
                next_year = today.year + 1
            else:
                next_month = today.month + 1
                next_year = today.year

            filters["month"] = next_month
            filters["year"] = next_year
            filters["time_mode"] = "next_month"
            return filters

        range_pattern = (
            rf"\b(?:du\s+)?(\d{{1,2}})\s*(?:au|a|et|-)\s*(\d{{1,2}})\s+"
            rf"({month_names})\s+(\d{{4}})\b"
        )
        match_range = re.search(range_pattern, question_norm)
        if match_range:
            day_start, day_end, month_name, year_str = match_range.groups()
            month_num = self.MONTHS[month_name]
            year_num = int(year_str)

            start_date = self._build_date(year_num, month_num, int(day_start))
            end_date = self._build_date(year_num, month_num, int(day_end))

            if start_date and end_date:
                if start_date > end_date:
                    start_date, end_date = end_date, start_date

                filters["start_date"] = start_date
                filters["end_date"] = end_date
                filters["month"] = month_num
                filters["year"] = year_num
                filters["time_mode"] = "date_range"
                return filters

        exact_pattern = rf"\b(\d{{1,2}})\s+({month_names})\s+(\d{{4}})\b"
        match_exact = re.search(exact_pattern, question_norm)
        if match_exact:
            day_str, month_name, year_str = match_exact.groups()
            month_num = self.MONTHS[month_name]
            year_num = int(year_str)

            exact_date = self._build_date(year_num, month_num, int(day_str))
            if exact_date:
                filters["exact_date"] = exact_date
                filters["month"] = month_num
                filters["year"] = year_num
                filters["time_mode"] = "exact_date"
                return filters

        for month_name, month_num in self.MONTHS.items():
            match_month = re.search(rf"\b{month_name}\s+(\d{{4}})\b", question_norm)
            if match_month:
                filters["month"] = month_num
                filters["year"] = int(match_month.group(1))
                filters["time_mode"] = "month_year"
                return filters

        match_year = re.search(r"\b(20\d{2})\b", question_norm)
        if match_year:
            filters["year"] = int(match_year.group(1))
            filters["time_mode"] = "year"
            return filters

        return filters

    def extract_filters(
        self,
        question: str,
        default_city: str | None = None,
    ) -> dict[str, Any]:
        """
        Extrait les filtres structurés depuis la question utilisateur.

        Principe important :
        un filtre ne doit s'activer que si la contrainte est explicite
        dans la question.

        La ville par défaut peut servir à borner le corpus, mais ne compte
        pas comme contrainte forte.

        Parameters
        ----------
        question : str
            Question brute.
        default_city : str | None, default=None
            Ville par défaut éventuelle.

        Returns
        -------
        dict[str, Any]
            Ensemble des filtres détectés.
        """
        # Point d'entrée principal de lecture des contraintes utilisateur.
        # Cette fonction fusionne :
        # ville, temps, lexical, durée, prix, audience.
        question_norm = self.normalize_text(question)
        date_filters = self._extract_date_filters(question_norm)

        explicit_city = self._extract_city(question_norm)
        city = (
            explicit_city
            if explicit_city
            else self.normalize_text(default_city) if default_city else None
        )

        lexical_signals = self.lexical_service.extract_question_signals(question_norm)

        return {
            "question_norm": question_norm,
            "keywords": lexical_signals["keywords"],
            "city": city,
            "explicit_city": explicit_city,
            "event_type": lexical_signals.get("event_type"),
            "music_genre": lexical_signals.get("music_genre"),
            "audience_terms": lexical_signals.get("audience_terms", []),
            "is_cultural_query": lexical_signals.get("is_cultural_query", False),
            "is_broad_activity_query": lexical_signals.get("is_broad_activity_query", False),
            "duration_filter": self._extract_duration_filter(question_norm),
            "exact_date": date_filters["exact_date"],
            "month": date_filters["month"],
            "year": date_filters["year"],
            "start_date": date_filters["start_date"],
            "end_date": date_filters["end_date"],
            "time_mode": date_filters["time_mode"],
            "price_filter": lexical_signals.get("price_filter"),
        }

    # ------------------------------------------------------------------
    # Fonctions de matching
    # ------------------------------------------------------------------

    def matches_city(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte le filtre de ville.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le document respecte la ville.
        """
        # Filtre géographique simple et strict
        # lorsque la ville est connue.
        city = filters.get("city")
        if not city:
            return True

        return self._doc_city(doc) == city

    def _supports_any_variant(
        self,
        doc: Document,
        variants: list[str],
    ) -> bool:
        """
        Vérifie si un document supporte au moins une variante lexicale.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        variants : list[str]
            Variantes recherchées.

        Returns
        -------
        bool
            True si au moins une variante est présente.
        """
        # Permet de tester rapidement si un document
        # contient une des formes lexicales compatibles.
        if not variants:
            return False

        doc_text = self._doc_search_text(doc)
        return self.lexical_service.contains_any_term(doc_text, variants)

    def _event_type_match_level(self, doc: Document, requested_event_type: str) -> str:
        """
        Retourne le niveau de compatibilité du document avec le type demandé.

        Returns
        -------
        str
            `"exact"`, `"variant"` ou `"mismatch"`.
        """
        # Donne une granularité plus fine qu'un simple booléen :
        # exact, variante acceptable, ou incompatibilité.
        if not requested_event_type:
            return "variant"

        doc_event_type = self._doc_event_type(doc)
        if doc_event_type == requested_event_type:
            return "exact"

        variants = self.lexical_service.EVENT_TYPE_TERMS.get(requested_event_type, [])
        if self._supports_any_variant(doc, variants):
            derived_terms = self._doc_derived_event_terms(doc)

            if doc_event_type and doc_event_type != requested_event_type:
                if requested_event_type in derived_terms:
                    return "variant"
                return "mismatch"

            return "variant"

        derived_terms = self._doc_derived_event_terms(doc)
        if requested_event_type in derived_terms:
            if doc_event_type and doc_event_type != requested_event_type:
                return "mismatch"
            return "variant"

        return "mismatch"

    def _is_musical_document(self, doc: Document) -> bool:
        """
        Indique si le document semble réellement musical.

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si le document présente un signal musical suffisamment fort.
        """
        # Garde-fou pour éviter qu'un simple mot musical isolé
        # fasse passer un document pour un vrai événement musical.
        doc_event_type = self._doc_event_type(doc)
        if doc_event_type in self.lexical_service.MUSICAL_EVENT_TYPES:
            return True

        if self._doc_music_genre(doc):
            return True

        derived_music = self._doc_derived_music_terms(doc)
        if derived_music:
            return True

        doc_text = self._doc_search_text(doc)
        musical_hits = self.lexical_service.count_matching_terms(
            doc_text,
            [
                "concert",
                "live",
                "dj set",
                "showcase",
                "musique",
                "musical",
                "musicien",
                "groupe",
                "chanteur",
                "duo",
                "trio",
                "scene",
                "scène",
            ],
        )
        return musical_hits >= 2

    def _is_cultural_document(self, doc: Document) -> bool:
        """
        Indique si le document semble culturel.

        La logique reste volontairement resserrée :

        - priorité aux drapeaux documentaires enrichis
        - acceptation directe des types culturels forts
        - acceptation prudente des types faibles
        - rejet des signaux implicites négatifs comme marché, réparation ou business

        Parameters
        ----------
        doc : Document
            Document inspecté.

        Returns
        -------
        bool
            True si le document est jugé culturel.
        """
        # Décide si le document est vraiment culturel
        # en réutilisant les signaux enrichis et quelques garde-fous.
        if self._doc_has_business_signal(doc):
            return False

        if self._doc_has_market_signal(doc):
            return False

        if self._doc_has_repair_signal(doc):
            return False

        if self._doc_is_strong_cultural_candidate(doc):
            return True

        doc_event_type = self._doc_event_type(doc)
        if doc_event_type in self.STRONG_CULTURAL_EVENT_TYPES:
            return True

        if self._doc_is_weak_cultural_candidate(doc):
            doc_text = self._doc_search_text(doc)
            return self.lexical_service.contains_any_term(
                doc_text,
                self.lexical_service.STRONG_CULTURAL_TERMS,
            )

        derived_terms = self._doc_derived_event_terms(doc)
        if derived_terms & self.STRONG_CULTURAL_EVENT_TYPES:
            return True

        doc_text = self._doc_search_text(doc)
        return self.lexical_service.contains_any_term(
            doc_text,
            self.lexical_service.STRONG_CULTURAL_TERMS,
        )

    def matches_event_type(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte un type d'événement explicite.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le document respecte le type demandé.
        """
        # Active un filtre sur type uniquement
        # si ce type a été explicitement demandé.
        event_type = filters.get("event_type")
        if not event_type:
            return True

        return self._event_type_match_level(doc, event_type) != "mismatch"

    def matches_music_genre(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte un genre musical explicite.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le document respecte le genre musical demandé.
        """
        # Filtre musical prudent :
        # on veut un vrai document musical, pas juste un mot flottant.
        music_genre = filters.get("music_genre")
        if not music_genre:
            return True

        doc_music_genre = self._doc_music_genre(doc)
        if music_genre and doc_music_genre == music_genre:
            return self._is_musical_document(doc)

        derived_music = self._doc_derived_music_terms(doc)
        if music_genre in derived_music:
            return self._is_musical_document(doc)

        variants = self.lexical_service.MUSIC_GENRE_TERMS.get(music_genre, [])
        if self._supports_any_variant(doc, variants):
            if not self._is_musical_document(doc):
                return False

            if doc_music_genre and doc_music_genre != music_genre:
                return False

            return True

        return False

    def matches_cultural_scope(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte une contrainte culturelle explicite.

        Important :
        ce filtre ne doit s'activer que si la question contient
        explicitement une contrainte culturelle.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le document est compatible avec une contrainte culturelle.
        """
        # Ne filtre culturellement que si la question
        # exprimait vraiment une contrainte culturelle.
        if not filters.get("is_cultural_query"):
            return True

        return self._is_cultural_document(doc)

    def matches_duration(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte une contrainte explicite de durée.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si la durée est compatible.
        """
        # Filtre durée souple :
        # si l'information manque, on évite de rejeter trop vite.
        duration_filter = filters.get("duration_filter")
        if not duration_filter:
            return True

        is_single_day = self._doc_is_single_day(doc)
        duration_days = self._doc_duration_days(doc)

        if duration_filter == "single_day":
            if is_single_day is not None:
                return is_single_day is True
            if duration_days is not None:
                return duration_days == 1
            return True

        if duration_filter == "multi_day":
            if is_single_day is not None:
                return is_single_day is False
            if duration_days is not None:
                return duration_days > 1
            return True

        return True

    def matches_date(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte le filtre temporel.

        La logique repose principalement sur le chevauchement de période.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le document est compatible temporellement.
        """
        # Fonction centrale de matching temporel.
        # L'idée principale est de tester un chevauchement
        # entre la période demandée et celle du document.
        exact_date = filters.get("exact_date")
        month = filters.get("month")
        year = filters.get("year")
        start_date = filters.get("start_date")
        end_date = filters.get("end_date")

        if not any([exact_date, month, year, start_date, end_date]):
            return True

        first_date, last_date = self._doc_dates(doc)

        if not first_date:
            return False

        if last_date is None:
            last_date = first_date

        time_mode = filters.get("time_mode")

        if start_date and end_date:
            if time_mode in self.WEEKEND_TIME_MODES:
                return self._matches_weekend_with_max_span(
                    doc=doc,
                    start_date=start_date,
                    end_date=end_date,
                    max_span_days=3,
                )

            return first_date <= end_date and last_date >= start_date

        if exact_date:
            return first_date <= exact_date <= last_date

        if month and year:
            month_start, month_end = self._get_month_bounds(year, month)
            return first_date < month_end and last_date >= month_start

        if year:
            year_start, year_end = self._get_year_bounds(year)
            return first_date < year_end and last_date >= year_start

        return True

    def matches_price(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte le filtre de tarification.

        La logique reste souple si l'information tarifaire est absente.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le document est compatible avec le filtre tarifaire.
        """
        # Filtre prix volontairement tolérant si l'information manque,
        # afin de ne pas casser le rappel.
        price_filter = filters.get("price_filter")
        if not price_filter:
            return True

        is_free = self._doc_is_free(doc)

        if price_filter == "gratuit":
            if is_free is True:
                return True
            if is_free is False:
                return False
            return True

        if price_filter == "payant":
            if is_free is False:
                return True
            if is_free is True:
                return False
            return True

        return True

    def matches_audience(self, doc: Document, filters: dict[str, Any]) -> bool:
        """
        Vérifie si un document respecte une contrainte simple de public.

        Parameters
        ----------
        doc : Document
            Document inspecté.
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si le public cible est compatible.
        """
        # Filtre audience simple par intersection de termes.
        # Si le document n'a pas d'info audience, on reste souple.
        requested_terms = filters.get("audience_terms") or []
        if not requested_terms:
            return True

        doc_terms = self._doc_audience_terms(doc)
        if not doc_terms:
            return True

        requested_norm = {
            self.normalize_text(term)
            for term in requested_terms
            if self.normalize_text(term)
        }

        return bool(doc_terms & requested_norm)

    # ------------------------------------------------------------------
    # Pilotage global
    # ------------------------------------------------------------------

    def has_strong_filters(self, filters: dict[str, Any]) -> bool:
        """
        Indique si la question contient au moins une contrainte forte.

        Important :

        - la ville par défaut ne compte pas comme contrainte forte
        - seule une ville explicitement mentionnée active ce levier
        - une requête large d'activité (`que faire`) ne compte pas comme filtre fort

        Parameters
        ----------
        filters : dict[str, Any]
            Filtres extraits.

        Returns
        -------
        bool
            True si au moins une contrainte forte est présente.
        """
        # Sert à décider si le préfiltrage structuré
        # doit réellement se déclencher de façon forte.
        return any(
            [
                filters.get("explicit_city"),
                filters.get("event_type"),
                filters.get("music_genre"),
                filters.get("is_cultural_query"),
                filters.get("duration_filter"),
                filters.get("audience_terms"),
                filters.get("exact_date"),
                filters.get("month"),
                filters.get("year"),
                filters.get("start_date"),
                filters.get("end_date"),
                filters.get("price_filter"),
            ]
        )

    def _empty_pipeline_result(self, docs_input: list[Document]) -> dict[str, list[Document]]:
        """
        Retourne une structure de pipeline vide standardisée.

        Parameters
        ----------
        docs_input : list[Document]
            Documents d'entrée.

        Returns
        -------
        dict[str, list[Document]]
            Pipeline vide.
        """
        # Produit une structure uniforme de sortie
        # même lorsqu'une étape vide complètement le pipeline.
        return {
            "input": docs_input,
            "after_city": [],
            "after_date": [],
            "after_type": [],
            "after_music": [],
            "after_cultural": [],
            "after_audience": [],
            "after_duration": [],
            "after_price": [],
        }

    def _run_filter_pipeline(
        self,
        filters: dict[str, Any],
        docs: list[Document],
    ) -> dict[str, list[Document]]:
        """
        Exécute le pipeline complet de filtrage étape par étape.

        Ordre métier appliqué :

        1. lieu
        2. date
        3. type
        4. musique
        5. culturel
        6. audience
        7. durée
        8. prix

        Parameters
        ----------
        filters : dict[str, Any]
            Filtres extraits.
        docs : list[Document]
            Documents candidats.

        Returns
        -------
        dict[str, list[Document]]
            Documents intermédiaires à chaque étape.
        """
        # Fonction d'orchestration principale du préfiltrage.
        # Elle exécute les filtres dans un ordre métier lisible
        # et garde les états intermédiaires pour le debug.
        docs_input = docs[:]
        docs_after_city = docs_input[:]

        # 1. Ville
        # La ville par défaut peut être utilisée pour borner le corpus local.
        if filters.get("city"):
            docs_after_city = [
                doc for doc in docs_after_city
                if self.matches_city(doc, filters)
            ]

        if not docs_after_city:
            return self._empty_pipeline_result(docs_input)

        # 2. Date
        has_date_filter = any(
            [
                filters.get("exact_date"),
                filters.get("month"),
                filters.get("year"),
                filters.get("start_date"),
                filters.get("end_date"),
            ]
        )

        docs_after_date = (
            [doc for doc in docs_after_city if self.matches_date(doc, filters)]
            if has_date_filter
            else docs_after_city
        )

        if not docs_after_date:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            return result

        # Si aucune contrainte forte explicite autre que ville/date,
        # on n'active pas les autres filtres.
        has_other_explicit_filters = any(
            [
                filters.get("event_type"),
                filters.get("music_genre"),
                filters.get("is_cultural_query"),
                filters.get("duration_filter"),
                filters.get("audience_terms"),
                filters.get("price_filter"),
            ]
        )

        if not self.has_strong_filters(filters) or not has_other_explicit_filters:
            return {
                "input": docs_input,
                "after_city": docs_after_city,
                "after_date": docs_after_date,
                "after_type": docs_after_date,
                "after_music": docs_after_date,
                "after_cultural": docs_after_date,
                "after_audience": docs_after_date,
                "after_duration": docs_after_date,
                "after_price": docs_after_date,
            }

        # 3. Type
        docs_after_type = (
            [doc for doc in docs_after_date if self.matches_event_type(doc, filters)]
            if filters.get("event_type")
            else docs_after_date
        )
        if not docs_after_type:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            result["after_date"] = docs_after_date
            return result

        # 4. Musique
        docs_after_music = (
            [doc for doc in docs_after_type if self.matches_music_genre(doc, filters)]
            if filters.get("music_genre")
            else docs_after_type
        )
        if not docs_after_music:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            result["after_date"] = docs_after_date
            result["after_type"] = docs_after_type
            return result

        # 5. Culturel
        docs_after_cultural = (
            [doc for doc in docs_after_music if self.matches_cultural_scope(doc, filters)]
            if filters.get("is_cultural_query")
            else docs_after_music
        )
        if not docs_after_cultural:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            result["after_date"] = docs_after_date
            result["after_type"] = docs_after_type
            result["after_music"] = docs_after_music
            return result

        # 6. Audience
        docs_after_audience = (
            [doc for doc in docs_after_cultural if self.matches_audience(doc, filters)]
            if filters.get("audience_terms")
            else docs_after_cultural
        )
        if filters.get("audience_terms") and not docs_after_audience:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            result["after_date"] = docs_after_date
            result["after_type"] = docs_after_type
            result["after_music"] = docs_after_music
            result["after_cultural"] = docs_after_cultural
            return result

        # 7. Durée
        docs_after_duration = (
            [doc for doc in docs_after_audience if self.matches_duration(doc, filters)]
            if filters.get("duration_filter")
            else docs_after_audience
        )
        if filters.get("duration_filter") and not docs_after_duration:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            result["after_date"] = docs_after_date
            result["after_type"] = docs_after_type
            result["after_music"] = docs_after_music
            result["after_cultural"] = docs_after_cultural
            result["after_audience"] = docs_after_audience
            return result

        # 8. Prix
        docs_after_price = (
            [doc for doc in docs_after_duration if self.matches_price(doc, filters)]
            if filters.get("price_filter")
            else docs_after_duration
        )
        if filters.get("price_filter") and not docs_after_price:
            result = self._empty_pipeline_result(docs_input)
            result["after_city"] = docs_after_city
            result["after_date"] = docs_after_date
            result["after_type"] = docs_after_type
            result["after_music"] = docs_after_music
            result["after_cultural"] = docs_after_cultural
            result["after_audience"] = docs_after_audience
            result["after_duration"] = docs_after_duration
            return result

        return {
            "input": docs_input,
            "after_city": docs_after_city,
            "after_date": docs_after_date,
            "after_type": docs_after_type,
            "after_music": docs_after_music,
            "after_cultural": docs_after_cultural,
            "after_audience": docs_after_audience,
            "after_duration": docs_after_duration,
            "after_price": docs_after_price,
        }

    def filter_documents(
        self,
        question: str,
        docs: list[Document],
        default_city: str | None = None,
    ) -> list[Document]:
        """
        Préfiltre les documents avant la recherche vectorielle.

        Parameters
        ----------
        question : str
            Question utilisateur.
        docs : list[Document]
            Documents candidats.
        default_city : str | None, default=None
            Ville par défaut éventuelle.

        Returns
        -------
        list[Document]
            Documents retenus après filtrage.
        """
        # API principale du service :
        # extrait les filtres, exécute le pipeline,
        # renvoie uniquement les documents finaux retenus.
        if not docs:
            return []

        filters = self.extract_filters(
            question=question,
            default_city=default_city,
        )

        pipeline = self._run_filter_pipeline(filters, docs)
        return pipeline["after_price"]

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def _doc_to_debug_row(self, doc: Document) -> dict[str, Any]:
        """
        Convertit un document en ligne légère pour le debug.

        Parameters
        ----------
        doc : Document
            Document à sérialiser.

        Returns
        -------
        dict[str, Any]
            Représentation légère du document.
        """
        # Produit une vue compacte d'un document
        # pour comprendre pourquoi il a survécu au filtrage.
        md = self._metadata(doc)
        return {
            "title": md.get("title", ""),
            "city": md.get("city", ""),
            "canonical_event_type": md.get("canonical_event_type", ""),
            "event_type": md.get("event_type", ""),
            "music_genre": md.get("music_genre", ""),
            "first_date": md.get("first_date", ""),
            "last_date": md.get("last_date", ""),
            "duration_days": md.get("duration_days"),
            "is_single_day": md.get("is_single_day"),
            "price_info": md.get("price_info", ""),
            "is_free": md.get("is_free"),
            "keywords_title": md.get("keywords_title", []),
            "derived_event_terms": md.get("derived_event_terms", []),
            "derived_music_terms": md.get("derived_music_terms", []),
            "audience_terms": md.get("audience_terms", []),
            "is_strong_cultural_candidate": md.get("is_strong_cultural_candidate", False),
            "is_weak_cultural_candidate": md.get("is_weak_cultural_candidate", False),
            "has_market_signal": md.get("has_market_signal", False),
            "has_repair_signal": md.get("has_repair_signal", False),
            "has_business_signal": md.get("has_business_signal", False),
            "url": md.get("source_url", "") or md.get("url", ""),
        }

    def filter_documents_with_debug(
        self,
        question: str,
        docs: list[Document],
        default_city: str | None = None,
    ) -> dict[str, Any]:
        """
        Préfiltre les documents et retourne des informations de debug détaillées.

        Parameters
        ----------
        question : str
            Question utilisateur.
        docs : list[Document]
            Documents candidats.
        default_city : str | None, default=None
            Ville par défaut éventuelle.

        Returns
        -------
        dict[str, Any]
            Structure détaillée de debug.
        """
        # Variante de debug du service :
        # expose les filtres détectés, les volumes après chaque étape,
        # et une vue légère des documents finaux.
        filters = self.extract_filters(
            question=question,
            default_city=default_city,
        )

        pipeline = self._run_filter_pipeline(filters, docs)
        final_docs = pipeline["after_price"]

        return {
            "filters": filters,
            "n_input_docs": len(pipeline["input"]),
            "n_after_city": len(pipeline["after_city"]),
            "n_after_date": len(pipeline["after_date"]),
            "n_after_type": len(pipeline["after_type"]),
            "n_after_music": len(pipeline["after_music"]),
            "n_after_cultural": len(pipeline["after_cultural"]),
            "n_after_audience": len(pipeline["after_audience"]),
            "n_after_duration": len(pipeline["after_duration"]),
            "n_after_price": len(final_docs),
            "docs": final_docs,
            "docs_debug": [self._doc_to_debug_row(doc) for doc in final_docs],
        }