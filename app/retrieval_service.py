"""
Service de ranking métier des documents après la recherche vectorielle.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/retrieval_service.py` (commit 18f7ce1).

Ce module intervient après la recherche FAISS afin d'affiner les
documents candidats avant la génération finale.

Objectifs :
- extraire des signaux métier utiles depuis la question utilisateur
- calculer un score hybride combinant :
  - un signal vectoriel issu de FAISS
  - un score métier explicable
- mieux respecter les contraintes fortes de la question :
  - ville
  - type d'événement
  - tarification
  - date exacte
  - période
  - mois / année
  - genre musical
  - intention culturelle explicite
- limiter les faux positifs sémantiques
- légèrement diversifier les résultats finaux

Important :
Le préfiltrage structuré fort peut être fait en amont par `filter_service`.
Cette classe conserve cependant quelques garde-fous métier afin
d'éviter qu'un document incompatible remonte trop haut dans le classement.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from langchain_core.documents import Document

from app.lexical_service import LexicalService


class RetrievalService:
    """
    Service de ranking métier après la recherche vectorielle.
    """
    # Ce service intervient après FAISS.
    # Son rôle n'est plus de filtrer brutalement,
    # mais de réordonner intelligemment les candidats
    # avec un score hybride vectoriel + métier.

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
    # Référentiel mois -> numéro pour le parsing temporel.

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
    # Liste simple de villes reconnues dans la question.

    STRONG_KEYWORDS = {
        "rock",
        "jazz",
        "blues",
        "electro",
        "folk",
        "rap",
        "photo",
        "photographie",
        "vinyl",
        "gratuit",
        "payant",
        "enfant",
        "enfants",
        "famille",
        "architecture",
        "concert",
        "expo",
        "exposition",
        "vernissage",
        "projection",
        "festival",
        "spectacle",
        "theatre",
        "théâtre",
    }
    # Mots-clés fortement discriminants,
    # utilisés pour renforcer ou pénaliser certains documents.

    STRONG_CULTURAL_EVENT_TYPES = {
        "exposition",
        "concert",
        "festival",
        "projection",
        "spectacle",
        "conte",
        "lecture",
    }
    # Types culturels forts.

    WEAK_CULTURAL_EVENT_TYPES = {
        "atelier",
        "conference",
        "visite",
    }
    # Types culturels plus faibles, moins déterminants.

    WEEKEND_TIME_MODES = {
        "weekend_explicit",
        "weekend_this",
        "weekend_next",
    }
    # Modes spéciaux de temporalité liés aux week-ends.

    SCORE_WEIGHTS = {
        "vector": 4.0,
        "city_bonus": 1.5,
        "event_type_exact_match": 13.0,
        "event_type_variant_match": 6.0,
        "music_genre_match": 7.5,
        "price_match": 4.5,
        "exact_date_match": 8.0,
        "period_match": 5.0,
        "month_match": 3.0,
        "year_match": 1.5,
        "keyword_text": 0.20,
        "keyword_title_present": 1.0,
        "derived_term_present": 1.0,
        "strong_keyword_present": 1.75,
        "content_quality": 0.20,
        "long_description_bonus": 0.15,
        "single_day_bonus": 0.10,
        "cultural_doc_bonus": 3.5,
        "strong_cultural_doc_bonus": 3.5,
        "weak_cultural_doc_bonus": 1.0,
        "musical_doc_bonus": 3.0,

        "wrong_city": -15.0,
        "event_type_soft_mismatch": -8.0,
        "event_type_hard_mismatch": -18.0,
        "music_genre_missing": -10.0,
        "music_doc_missing": -8.0,
        "music_genre_neighbor_mismatch": -6.0,
        "price_mismatch": -9.0,
        "date_mismatch": -10.0,
        "month_mismatch": -8.0,
        "strong_keyword_absent": -1.25,
        "non_cultural_doc": -10.0,
        "weak_cultural_doc_on_strong_cultural_query": -5.0,
        "empty_event_type_on_cultural_query": -5.0,
        "empty_event_type_on_typed_query": -8.0,
        "market_signal_penalty": -14.0,
        "repair_signal_penalty": -10.0,
        "business_signal_penalty": -14.0,
        "generic_doc_penalty": -5.0,

        "similarity_penalty": 1.0,
        "weekend_focus_bonus": 1.5,
        "long_event_on_weekend_query_penalty": -2.5,
    }
    # Table centrale des bonus et pénalités métier.
    # C'est elle qui rend le scoring explicable et ajustable.

    def __init__(self) -> None:
        # Le ranking s'appuie sur le même service lexical
        # que les autres briques pour garder une cohérence globale.
        self.lexical_service = LexicalService()

    # -------------------------------------------------------------------------
    # Utilitaires généraux
    # -------------------------------------------------------------------------

    def _safe(self, value: object) -> str:
        # Petit helper local pour éviter les None
        # dans les opérations de parsing texte/date.
        return "" if value is None else str(value)

    def normalize_text(self, text: object) -> str:
        # Point d'accès local à la normalisation partagée.
        return self.lexical_service.normalize_text(text)

    def parse_iso_date(self, value: str | None) -> date | None:
        # Convertit une chaîne ISO en objet date
        # pour les comparaisons temporelles.
        value = self._safe(value).strip()
        if not value:
            return None

        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def _build_date(self, year: int, month: int, day: int) -> date | None:
        # Construit une date de manière sûre
        # sans casser le service si elle est invalide.
        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _get_month_bounds(self, year: int, month: int) -> tuple[date, date]:
        # Retourne les bornes semi-ouvertes d'un mois
        # pour tester facilement un chevauchement.
        month_start = date(year, month, 1)

        if month == 12:
            month_end = date(year + 1, 1, 1)
        else:
            month_end = date(year, month + 1, 1)

        return month_start, month_end

    def _get_year_bounds(self, year: int) -> tuple[date, date]:
        # Retourne les bornes semi-ouvertes d'une année.
        return date(year, 1, 1), date(year + 1, 1, 1)

    def _get_weekend_range(
        self,
        reference: date,
        next_weekend: bool = False,
    ) -> tuple[date, date]:
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
        # Détecte un week-end explicitement formulé
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

    # -------------------------------------------------------------------------
    # Lecture / consolidation des métadonnées documentaires
    # -------------------------------------------------------------------------

    def _doc_dates(self, doc: Document) -> tuple[date | None, date | None]:
        # Lit les dates utiles d'un document
        # et considère mono-jour si seule la date de début existe.
        md = doc.metadata or {}
        first_date = self.parse_iso_date(md.get("first_date"))
        last_date = self.parse_iso_date(md.get("last_date"))

        if first_date and not last_date:
            last_date = first_date

        return first_date, last_date

    def _doc_text(self, doc: Document) -> str:
        # Produit un texte consolidé du document pour le matching lexical.
        # search_text est prioritaire car il a été construit pour ça.
        md = doc.metadata or {}
        search_text = md.get("search_text", "")

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
            f"{md.get('price_info', '')} "
            f"{doc.page_content}"
        )
        return self.normalize_text(text)

    def _doc_title_keywords(self, doc: Document) -> set[str]:
        # Récupère les keywords du titre déjà enrichis,
        # sous forme d'ensemble normalisé.
        md = doc.metadata or {}
        keywords = md.get("keywords_title", [])

        if not isinstance(keywords, list):
            return set()

        return {
            self.normalize_text(keyword)
            for keyword in keywords
            if self.normalize_text(keyword)
        }

    def _doc_derived_terms(self, doc: Document) -> set[str]:
        # Récupère les termes métier dérivés du document.
        md = doc.metadata or {}
        terms = md.get("derived_event_terms", [])

        if not isinstance(terms, list):
            return set()

        return {
            self.normalize_text(term)
            for term in terms
            if self.normalize_text(term)
        }

    def _doc_derived_music_terms(self, doc: Document) -> set[str]:
        # Récupère les termes musicaux dérivés du document.
        md = doc.metadata or {}
        terms = md.get("derived_music_terms", [])

        if not isinstance(terms, list):
            return set()

        return {
            self.normalize_text(term)
            for term in terms
            if self.normalize_text(term)
        }

    def _doc_city(self, doc: Document) -> str:
        # Lit la ville documentaire en version normalisée.
        md = doc.metadata or {}
        return md.get("city_norm") or self.normalize_text(md.get("city", ""))

    def _doc_event_type(self, doc: Document) -> str:
        # Privilégie le type canonique si disponible,
        # sinon retombe sur le type brut normalisé.
        md = doc.metadata or {}
        return (
            md.get("canonical_event_type_norm")
            or self.normalize_text(md.get("canonical_event_type", ""))
            or md.get("event_type_norm")
            or self.normalize_text(md.get("event_type", ""))
        )

    def _doc_music_genre(self, doc: Document) -> str:
        # Lit le genre musical en version normalisée.
        md = doc.metadata or {}
        return md.get("music_genre_norm") or self.normalize_text(md.get("music_genre", ""))

    def _doc_duration_days(self, doc: Document) -> int:
        # Récupère la durée documentaire.
        # Si elle manque totalement, retourne une très grande valeur
        # pour pénaliser les cas indéterminés dans certains scénarios.
        md = doc.metadata or {}
        value = md.get("duration_days")

        try:
            if value is not None:
                return max(1, int(value))
        except (TypeError, ValueError):
            pass

        first_date, last_date = self._doc_dates(doc)

        if not first_date:
            return 999999

        if not last_date:
            last_date = first_date

        return max(1, (last_date - first_date).days + 1)

    def _doc_is_single_day(self, doc: Document) -> bool | None:
        # Lit le booléen mono-jour si déjà disponible.
        md = doc.metadata or {}
        value = md.get("is_single_day")

        if isinstance(value, bool):
            return value

        return None

    def _doc_content_quality(self, doc: Document) -> int:
        # Lit le score de qualité documentaire calculé en amont.
        md = doc.metadata or {}
        value = md.get("content_quality")

        try:
            return max(0, int(value)) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def _doc_has_long_description(self, doc: Document) -> bool:
        # Sert à récompenser légèrement les documents plus riches.
        md = doc.metadata or {}
        return bool(md.get("has_long_description"))

    def _doc_is_strong_cultural_candidate(self, doc: Document) -> bool:
        # Réutilise le flag culturel fort enrichi.
        md = doc.metadata or {}
        return bool(md.get("is_strong_cultural_candidate", False))

    def _doc_is_weak_cultural_candidate(self, doc: Document) -> bool:
        # Réutilise le flag culturel faible enrichi.
        md = doc.metadata or {}
        return bool(md.get("is_weak_cultural_candidate", False))

    def _doc_has_market_signal(self, doc: Document) -> bool:
        # Détecte un signal de marché / vente.
        md = doc.metadata or {}
        return bool(md.get("has_market_signal", False))

    def _doc_has_repair_signal(self, doc: Document) -> bool:
        # Détecte un signal de réparation / repair café.
        md = doc.metadata or {}
        return bool(md.get("has_repair_signal", False))

    def _doc_has_business_signal(self, doc: Document) -> bool:
        # Détecte un signal business / networking.
        md = doc.metadata or {}
        return bool(md.get("has_business_signal", False))

    # -------------------------------------------------------------------------
    # Extraction des signaux de question
    # -------------------------------------------------------------------------

    def _extract_city(self, question: str) -> str | None:
        # Détecte une ville explicite dans la question.
        for city in sorted(self.KNOWN_CITIES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(city)}\b", question):
                return city
        return None

    def _extract_price_filter(self, question: str) -> str | None:
        # Petit fallback tarifaire local.
        # Dans la pratique, lexical_service en donne déjà un,
        # mais on garde un garde-fou simple.
        if re.search(r"\bgratuit(?:e|s)?\b", question):
            return "gratuit"

        if re.search(r"\bpayant(?:e|s)?\b", question):
            return "payant"

        return None

    def _extract_date_filters(self, question: str) -> dict[str, Any]:
        # Construit les filtres temporels détectés dans la question :
        # date exacte, intervalle, mois, année, week-end, etc.
        result = {
            "exact_date": None,
            "date_start": None,
            "date_end": None,
            "month": None,
            "year": None,
            "time_mode": None,
        }

        today = date.today()
        month_names = "|".join(self.MONTHS.keys())

        explicit_weekend_start, explicit_weekend_end = self._extract_explicit_weekend_range(
            question
        )
        if explicit_weekend_start and explicit_weekend_end:
            result["date_start"] = explicit_weekend_start
            result["date_end"] = explicit_weekend_end
            result["month"] = explicit_weekend_start.month
            result["year"] = explicit_weekend_start.year
            result["time_mode"] = "weekend_explicit"
            return result

        if re.search(r"\bce\s+week(?:\s|-)?end\b", question):
            start_date, end_date = self._get_weekend_range(
                reference=today,
                next_weekend=False,
            )
            result["date_start"] = start_date
            result["date_end"] = end_date
            result["month"] = start_date.month
            result["year"] = start_date.year
            result["time_mode"] = "weekend_this"
            return result

        if re.search(r"\bweek(?:\s|-)?end\s+prochain\b", question):
            start_date, end_date = self._get_weekend_range(
                reference=today,
                next_weekend=True,
            )
            result["date_start"] = start_date
            result["date_end"] = end_date
            result["month"] = start_date.month
            result["year"] = start_date.year
            result["time_mode"] = "weekend_next"
            return result

        if re.search(r"\bmois\s+prochain\b", question):
            if today.month == 12:
                next_month = 1
                next_year = today.year + 1
            else:
                next_month = today.month + 1
                next_year = today.year

            result["month"] = next_month
            result["year"] = next_year
            result["time_mode"] = "next_month"
            return result

        range_pattern = (
            rf"\b(?:du\s+)?(\d{{1,2}})\s*(?:au|a|et|-)\s*(\d{{1,2}})\s+"
            rf"({month_names})\s+(\d{{4}})\b"
        )
        match_range = re.search(range_pattern, question)
        if match_range:
            day_start, day_end, month_name, year_str = match_range.groups()
            month_num = self.MONTHS[month_name]
            year_num = int(year_str)

            start_date = self._build_date(year_num, month_num, int(day_start))
            end_date = self._build_date(year_num, month_num, int(day_end))

            if start_date and end_date:
                if start_date > end_date:
                    start_date, end_date = end_date, start_date

                result["date_start"] = start_date
                result["date_end"] = end_date
                result["month"] = month_num
                result["year"] = year_num
                result["time_mode"] = "date_range"
                return result

        exact_pattern = rf"\b(\d{{1,2}})\s+({month_names})\s+(\d{{4}})\b"
        match_exact = re.search(exact_pattern, question)
        if match_exact:
            day_str, month_name, year_str = match_exact.groups()
            month_num = self.MONTHS[month_name]
            year_num = int(year_str)

            exact_date = self._build_date(year_num, month_num, int(day_str))
            if exact_date:
                result["exact_date"] = exact_date
                result["date_start"] = exact_date
                result["date_end"] = exact_date
                result["month"] = month_num
                result["year"] = year_num
                result["time_mode"] = "exact_date"
                return result

        for month_name, month_num in self.MONTHS.items():
            match_month = re.search(rf"\b{month_name}\s+(\d{{4}})\b", question)
            if match_month:
                result["month"] = month_num
                result["year"] = int(match_month.group(1))
                result["time_mode"] = "month_year"
                return result

        match_year = re.search(r"\b(20\d{2})\b", question)
        if match_year:
            result["year"] = int(match_year.group(1))
            result["time_mode"] = "year"
            return result

        return result

    def extract_signals(self, question: str) -> dict[str, Any]:
        # Fonction de synthèse côté question.
        # Elle transforme une question libre en signaux structurés pour le ranking.
        q = self.normalize_text(question)
        date_filters = self._extract_date_filters(q)
        lexical_signals = self.lexical_service.extract_question_signals(q)

        price_filter = lexical_signals.get("price_filter") or self._extract_price_filter(q)

        strong_keywords = [
            kw for kw in lexical_signals["keywords"]
            if kw in self.STRONG_KEYWORDS
        ]

        is_cultural_query = bool(lexical_signals.get("is_cultural_query", False))
        is_broad_activity_query = bool(
            lexical_signals.get("is_broad_activity_query", False)
        )

        has_time_constraint = bool(
            date_filters["exact_date"]
            or date_filters["date_start"]
            or date_filters["date_end"]
            or (date_filters["month"] and date_filters["year"])
            or date_filters["year"]
        )

        has_type_constraint = bool(lexical_signals["event_type"])
        has_price_constraint = bool(price_filter)
        has_music_constraint = bool(lexical_signals["music_genre"])
        has_audience_constraint = bool(lexical_signals.get("audience_terms", []))
        has_explicit_cultural_constraint = bool(is_cultural_query)

        is_broad_query = not (
            has_time_constraint
            or has_type_constraint
            or has_price_constraint
            or has_music_constraint
            or has_audience_constraint
            or has_explicit_cultural_constraint
        )

        return {
            "question_norm": q,
            "city": self._extract_city(q),
            "event_type": lexical_signals["event_type"],
            "music_genre": lexical_signals["music_genre"],
            "price_filter": price_filter,
            "audience_terms": lexical_signals.get("audience_terms", []),
            "exact_date": date_filters["exact_date"],
            "date_start": date_filters["date_start"],
            "date_end": date_filters["date_end"],
            "month": date_filters["month"],
            "year": date_filters["year"],
            "time_mode": date_filters["time_mode"],
            "keywords": lexical_signals["keywords"],
            "strong_keywords": strong_keywords,
            "is_cultural_query": is_cultural_query,
            "is_broad_activity_query": is_broad_activity_query,
            "has_time_constraint": has_time_constraint,
            "has_type_constraint": has_type_constraint,
            "has_price_constraint": has_price_constraint,
            "has_music_constraint": has_music_constraint,
            "has_audience_constraint": has_audience_constraint,
            "has_explicit_cultural_constraint": has_explicit_cultural_constraint,
            "is_broad_query": is_broad_query,
        }

    # -------------------------------------------------------------------------
    # Matching métier
    # -------------------------------------------------------------------------

    def _vector_score_to_bonus(self, doc: Document) -> float:
        # Convertit la distance vectorielle FAISS en bonus métier.
        # Plus le document est proche vectoriellement, plus le bonus est élevé.
        md = doc.metadata or {}
        raw_score = md.get("vector_score")

        if raw_score is None:
            return 0.0

        try:
            raw_score = float(raw_score)
        except (TypeError, ValueError):
            return 0.0

        return max(0.0, self.SCORE_WEIGHTS["vector"] - raw_score * 4.0)

    def _supports_any_variant(
        self,
        doc_text: str,
        doc_title_keywords: set[str],
        doc_derived_terms: set[str],
        variants: list[str],
    ) -> bool:
        # Vérifie si le document supporte une variante lexicale
        # à travers son texte, ses keywords de titre et ses termes dérivés.
        support_text = f"{doc_text} {' '.join(doc_title_keywords)} {' '.join(doc_derived_terms)}"
        return self.lexical_service.contains_any_term(support_text, variants)

    def _date_overlaps(
        self,
        event_start: date | None,
        event_end: date | None,
        query_start: date | None,
        query_end: date | None,
    ) -> bool:
        # Teste le chevauchement entre la période du document
        # et la période demandée.
        if not event_start:
            return False

        if not event_end:
            event_end = event_start

        if not query_start:
            return False

        if not query_end:
            query_end = query_start

        return event_start <= query_end and event_end >= query_start

    def _keyword_text_score(self, doc_text: str, keywords: list[str]) -> float:
        # Score léger basé sur la présence brute des keywords dans le texte du document.
        if not keywords:
            return 0.0

        hits = sum(1 for kw in keywords if kw in doc_text)
        return hits * self.SCORE_WEIGHTS["keyword_text"]

    def _keyword_title_score(
        self,
        doc_title_keywords: set[str],
        keywords: list[str],
    ) -> tuple[float, int, int]:
        # Score spécifique basé sur les mots-clés présents dans le titre.
        # Renvoie aussi le nombre présents / absents pour le debug.
        if not keywords:
            return 0.0, 0, 0

        present = sum(1 for kw in keywords if kw in doc_title_keywords)
        absent = len(keywords) - present
        score = present * self.SCORE_WEIGHTS["keyword_title_present"]

        return score, present, absent

    def _derived_terms_score(
        self,
        doc_derived_terms: set[str],
        keywords: list[str],
    ) -> float:
        # Score basé sur les termes métier dérivés du document.
        if not keywords or not doc_derived_terms:
            return 0.0

        hits = sum(1 for kw in keywords if kw in doc_derived_terms)
        return hits * self.SCORE_WEIGHTS["derived_term_present"]

    def _strong_keywords_score(
        self,
        doc_text: str,
        doc_title_keywords: set[str],
        doc_derived_terms: set[str],
        strong_keywords: list[str],
    ) -> float:
        # Récompense ou pénalise selon la présence
        # des mots-clés les plus discriminants.
        if not strong_keywords:
            return 0.0

        score = 0.0
        support_text = f"{doc_text} {' '.join(doc_title_keywords)} {' '.join(doc_derived_terms)}"

        for kw in strong_keywords:
            if kw in support_text:
                score += self.SCORE_WEIGHTS["strong_keyword_present"]
            else:
                score += self.SCORE_WEIGHTS["strong_keyword_absent"]

        return score

    def _content_quality_score(self, doc: Document) -> float:
        # Récompense légèrement les documents plus riches et mieux renseignés.
        score = 0.0
        score += self._doc_content_quality(doc) * self.SCORE_WEIGHTS["content_quality"]

        if self._doc_has_long_description(doc):
            score += self.SCORE_WEIGHTS["long_description_bonus"]

        if self._doc_is_single_day(doc) is True:
            score += self.SCORE_WEIGHTS["single_day_bonus"]

        return score

    def _is_musical_document(
        self,
        doc_text: str,
        doc_event_type: str,
        doc_music_genre: str,
        doc_derived_terms: set[str],
        doc_derived_music_terms: set[str],
    ) -> bool:
        # Détermine si le document est vraiment musical.
        # Sert de garde-fou contre les faux positifs.
        if doc_event_type in self.lexical_service.MUSICAL_EVENT_TYPES:
            return True

        if doc_music_genre:
            return True

        if doc_derived_music_terms:
            return True

        support_text = f"{doc_text} {' '.join(doc_derived_terms)} {' '.join(doc_derived_music_terms)}"
        hits = self.lexical_service.count_matching_terms(
            support_text,
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
        return hits >= 2

    def _is_cultural_document(
        self,
        doc: Document,
        doc_text: str,
        doc_event_type: str,
        doc_derived_terms: set[str],
    ) -> bool:
        # Détermine si le document est culturel,
        # en réutilisant les flags et quelques règles métier.
        if self._doc_has_business_signal(doc):
            return False

        if self._doc_has_market_signal(doc):
            return False

        if self._doc_has_repair_signal(doc):
            return False

        if self._doc_is_strong_cultural_candidate(doc):
            return True

        if doc_event_type in self.STRONG_CULTURAL_EVENT_TYPES:
            return True

        if doc_derived_terms & self.STRONG_CULTURAL_EVENT_TYPES:
            return True

        if self._doc_is_weak_cultural_candidate(doc):
            return self.lexical_service.contains_any_term(
                doc_text,
                self.lexical_service.STRONG_CULTURAL_TERMS,
            )

        return self.lexical_service.contains_any_term(
            doc_text,
            self.lexical_service.STRONG_CULTURAL_TERMS,
        )

    def _event_type_match_level(
        self,
        requested_event_type: str,
        doc_event_type: str,
        doc_text: str,
        doc_title_keywords: set[str],
        doc_derived_terms: set[str],
    ) -> str:
        # Retourne exact / variant / mismatch
        # pour le matching de type d'événement.
        if not requested_event_type:
            return "variant"

        if requested_event_type == doc_event_type:
            return "exact"

        variants = self.lexical_service.EVENT_TYPE_TERMS.get(requested_event_type, [])
        if self._supports_any_variant(
            doc_text=doc_text,
            doc_title_keywords=doc_title_keywords,
            doc_derived_terms=doc_derived_terms,
            variants=variants,
        ):
            if doc_event_type and doc_event_type != requested_event_type:
                if requested_event_type in doc_derived_terms:
                    return "variant"
                return "mismatch"
            return "variant"

        if requested_event_type in doc_derived_terms:
            if doc_event_type and doc_event_type != requested_event_type:
                return "mismatch"
            return "variant"

        return "mismatch"

    def _is_neighbor_music_genre_mismatch(
        self,
        requested_genre: str,
        doc_music_genre: str,
    ) -> bool:
        # Détecte certains genres "voisins"
        # qui ne sont pas identiques mais pas totalement absurdes non plus.
        if not requested_genre or not doc_music_genre:
            return False

        if requested_genre == doc_music_genre:
            return False

        neighbor_pairs = {
            ("rock", "blues"),
            ("rock", "folk"),
            ("electro", "house"),
            ("rap", "folk"),
            ("jazz", "blues"),
        }

        return (requested_genre, doc_music_genre) in neighbor_pairs

    def _is_doc_compatible_with_query(
        self,
        doc: Document,
        signals: dict[str, Any],
    ) -> bool:
        """
        Garde-fou strict post-vectoriel.

        Vérifie si un document reste compatible avec les contraintes
        explicitement présentes dans la question.
        """
        # Sert de filet de sécurité après FAISS :
        # évite qu'un document franchement incompatible survive au ranking.
        doc_text = self._doc_text(doc)
        doc_title_keywords = self._doc_title_keywords(doc)
        doc_derived_terms = self._doc_derived_terms(doc)
        doc_derived_music_terms = self._doc_derived_music_terms(doc)
        doc_event_type = self._doc_event_type(doc)
        doc_music_genre = self._doc_music_genre(doc)

        if signals["city"] and signals["city"] != self._doc_city(doc):
            return False

        if signals["event_type"]:
            match_level = self._event_type_match_level(
                requested_event_type=signals["event_type"],
                doc_event_type=doc_event_type,
                doc_text=doc_text,
                doc_title_keywords=doc_title_keywords,
                doc_derived_terms=doc_derived_terms,
            )
            if match_level == "mismatch":
                return False

        if signals["music_genre"]:
            genre_variants = self.lexical_service.MUSIC_GENRE_TERMS.get(
                signals["music_genre"], []
            )
            genre_supported = (
                signals["music_genre"] == doc_music_genre
                or signals["music_genre"] in doc_derived_music_terms
                or self._supports_any_variant(
                    doc_text=doc_text,
                    doc_title_keywords=doc_title_keywords,
                    doc_derived_terms=doc_derived_terms | doc_derived_music_terms,
                    variants=genre_variants,
                )
            )
            if not genre_supported:
                return False

            if self._is_neighbor_music_genre_mismatch(
                requested_genre=signals["music_genre"],
                doc_music_genre=doc_music_genre,
            ):
                return False

            if not self._is_musical_document(
                doc_text=doc_text,
                doc_event_type=doc_event_type,
                doc_music_genre=doc_music_genre,
                doc_derived_terms=doc_derived_terms,
                doc_derived_music_terms=doc_derived_music_terms,
            ):
                return False

        if signals["is_cultural_query"]:
            if not self._is_cultural_document(
                doc=doc,
                doc_text=doc_text,
                doc_event_type=doc_event_type,
                doc_derived_terms=doc_derived_terms,
            ):
                return False

        if signals["date_start"] or signals["date_end"]:
            first_date, last_date = self._doc_dates(doc)
            if not self._date_overlaps(
                event_start=first_date,
                event_end=last_date,
                query_start=signals["date_start"],
                query_end=signals["date_end"],
            ):
                return False

        elif signals["month"] and signals["year"]:
            first_date, last_date = self._doc_dates(doc)
            if not first_date:
                return False
            if last_date is None:
                last_date = first_date

            month_start, month_end = self._get_month_bounds(
                signals["year"],
                signals["month"],
            )
            if not (first_date < month_end and last_date >= month_start):
                return False

        elif signals["year"]:
            first_date, last_date = self._doc_dates(doc)
            if not first_date:
                return False
            if last_date is None:
                last_date = first_date

            year_start, year_end = self._get_year_bounds(signals["year"])
            if not (first_date < year_end and last_date >= year_start):
                return False

        if signals["price_filter"] == "gratuit":
            is_free = (doc.metadata or {}).get("is_free")
            if is_free is False:
                return False

        if signals["price_filter"] == "payant":
            is_free = (doc.metadata or {}).get("is_free")
            if is_free is True:
                return False

        return True

    def _duration_penalty(self, doc: Document, signals: dict[str, Any]) -> float:
        # Pénalise les événements très longs,
        # surtout lorsque la question est temporellement précise.
        duration_days = self._doc_duration_days(doc)

        if duration_days == 999999:
            return -4.0

        if signals["has_time_constraint"]:
            if duration_days > 120:
                return -12.0
            if duration_days > 60:
                return -8.0
            if duration_days > 30:
                return -4.0
            return 0.0

        if signals["is_broad_query"]:
            if duration_days > 180:
                return -6.0
            if duration_days > 90:
                return -3.0

        return 0.0

    def _implicit_penalty_score(self, doc: Document, signals: dict[str, Any]) -> float:
        # Applique des pénalités implicites selon le contexte de la requête.
        score = 0.0

        if signals["is_cultural_query"]:
            if self._doc_has_market_signal(doc):
                score += self.SCORE_WEIGHTS["market_signal_penalty"]

            if self._doc_has_repair_signal(doc):
                score += self.SCORE_WEIGHTS["repair_signal_penalty"]

            if self._doc_has_business_signal(doc):
                score += self.SCORE_WEIGHTS["business_signal_penalty"]

            if self._doc_is_weak_cultural_candidate(doc):
                score += self.SCORE_WEIGHTS["weak_cultural_doc_on_strong_cultural_query"]

        elif signals["is_broad_query"]:
            if self._doc_has_business_signal(doc):
                score += self.SCORE_WEIGHTS["business_signal_penalty"] / 2

        return score

    # -------------------------------------------------------------------------
    # Scoring principal
    # -------------------------------------------------------------------------

    def score_document(self, doc: Document, signals: dict[str, Any]) -> float:
        """
        Calcule le score final métier d'un document.

        Ce score combine :
        - proximité vectorielle
        - compatibilité avec les contraintes explicites
        - bonus de qualité documentaire
        - pénalités métier explicables
        """
        # Cœur du service.
        # C'est ici que le document reçoit son score final avant tri.
        md = doc.metadata or {}

        doc_text = self._doc_text(doc)
        doc_title_keywords = self._doc_title_keywords(doc)
        doc_derived_terms = self._doc_derived_terms(doc)
        doc_derived_music_terms = self._doc_derived_music_terms(doc)
        doc_city = self._doc_city(doc)
        doc_event_type = self._doc_event_type(doc)
        doc_music_genre = self._doc_music_genre(doc)

        first_date, last_date = self._doc_dates(doc)
        score = 0.0

        score += self._vector_score_to_bonus(doc)

        if signals["city"]:
            if signals["city"] == doc_city:
                score += self.SCORE_WEIGHTS["city_bonus"]
            else:
                score += self.SCORE_WEIGHTS["wrong_city"]

        if signals["event_type"]:
            match_level = self._event_type_match_level(
                requested_event_type=signals["event_type"],
                doc_event_type=doc_event_type,
                doc_text=doc_text,
                doc_title_keywords=doc_title_keywords,
                doc_derived_terms=doc_derived_terms,
            )

            if match_level == "exact":
                score += self.SCORE_WEIGHTS["event_type_exact_match"]
            elif match_level == "variant":
                score += self.SCORE_WEIGHTS["event_type_variant_match"]
            else:
                score += self.SCORE_WEIGHTS["event_type_hard_mismatch"]

            if not doc_event_type:
                score += self.SCORE_WEIGHTS["empty_event_type_on_typed_query"]

        if signals["music_genre"]:
            genre_variants = self.lexical_service.MUSIC_GENRE_TERMS.get(
                signals["music_genre"], []
            )
            genre_supported = (
                signals["music_genre"] == doc_music_genre
                or signals["music_genre"] in doc_derived_music_terms
                or self._supports_any_variant(
                    doc_text=doc_text,
                    doc_title_keywords=doc_title_keywords,
                    doc_derived_terms=doc_derived_terms | doc_derived_music_terms,
                    variants=genre_variants,
                )
            )

            if genre_supported:
                score += self.SCORE_WEIGHTS["music_genre_match"]
            else:
                score += self.SCORE_WEIGHTS["music_genre_missing"]

            if self._is_neighbor_music_genre_mismatch(
                requested_genre=signals["music_genre"],
                doc_music_genre=doc_music_genre,
            ):
                score += self.SCORE_WEIGHTS["music_genre_neighbor_mismatch"]

            if self._is_musical_document(
                doc_text=doc_text,
                doc_event_type=doc_event_type,
                doc_music_genre=doc_music_genre,
                doc_derived_terms=doc_derived_terms,
                doc_derived_music_terms=doc_derived_music_terms,
            ):
                score += self.SCORE_WEIGHTS["musical_doc_bonus"]
            else:
                score += self.SCORE_WEIGHTS["music_doc_missing"]

        if signals["event_type"] == "concert":
            # Cas particulier : un concert doit vraiment être musical.
            is_musical_doc = self._is_musical_document(
                doc_text=doc_text,
                doc_event_type=doc_event_type,
                doc_music_genre=doc_music_genre,
                doc_derived_terms=doc_derived_terms,
                doc_derived_music_terms=doc_derived_music_terms,
            )

            if not is_musical_doc:
                score += self.SCORE_WEIGHTS["event_type_hard_mismatch"]
            elif doc_event_type != "concert":
                score += self.SCORE_WEIGHTS["event_type_soft_mismatch"]

        if signals["is_cultural_query"]:
            if self._is_cultural_document(
                doc=doc,
                doc_text=doc_text,
                doc_event_type=doc_event_type,
                doc_derived_terms=doc_derived_terms,
            ):
                score += self.SCORE_WEIGHTS["cultural_doc_bonus"]

                if self._doc_is_strong_cultural_candidate(doc):
                    score += self.SCORE_WEIGHTS["strong_cultural_doc_bonus"]
                elif self._doc_is_weak_cultural_candidate(doc):
                    score += self.SCORE_WEIGHTS["weak_cultural_doc_bonus"]
            else:
                score += self.SCORE_WEIGHTS["non_cultural_doc"]

            if not doc_event_type and not doc_derived_terms:
                score += self.SCORE_WEIGHTS["empty_event_type_on_cultural_query"]

        if signals["price_filter"] == "gratuit":
            if md.get("is_free") is True:
                score += self.SCORE_WEIGHTS["price_match"] + 2.0
            elif md.get("is_free") is False:
                score += self.SCORE_WEIGHTS["price_mismatch"]
            else:
                score -= 3.0

        if signals["price_filter"] == "payant":
            if md.get("is_free") is False:
                score += self.SCORE_WEIGHTS["price_match"] + 2.0
            elif md.get("is_free") is True:
                score += self.SCORE_WEIGHTS["price_mismatch"]
            else:
                score -= 3.0

        if signals["date_start"] or signals["date_end"]:
            overlaps = self._date_overlaps(
                event_start=first_date,
                event_end=last_date,
                query_start=signals["date_start"],
                query_end=signals["date_end"],
            )
            if overlaps:
                if signals["exact_date"]:
                    score += self.SCORE_WEIGHTS["exact_date_match"]
                else:
                    score += self.SCORE_WEIGHTS["period_match"]

                if signals.get("time_mode") in self.WEEKEND_TIME_MODES:
                    duration_days = self._doc_duration_days(doc)
                    if duration_days <= 3:
                        score += self.SCORE_WEIGHTS["weekend_focus_bonus"]
                    elif duration_days > 14:
                        score += self.SCORE_WEIGHTS["long_event_on_weekend_query_penalty"]
            else:
                score += self.SCORE_WEIGHTS["date_mismatch"]

        elif signals["month"] and signals["year"] and first_date:
            if last_date is None:
                last_date = first_date

            month_start, month_end = self._get_month_bounds(
                signals["year"],
                signals["month"],
            )

            if first_date < month_end and last_date >= month_start:
                score += self.SCORE_WEIGHTS["month_match"]
            else:
                score += self.SCORE_WEIGHTS["month_mismatch"]

        elif signals["year"] and first_date:
            if last_date is None:
                last_date = first_date

            year_start, year_end = self._get_year_bounds(signals["year"])
            if first_date < year_end and last_date >= year_start:
                score += self.SCORE_WEIGHTS["year_match"]
            else:
                score += self.SCORE_WEIGHTS["month_mismatch"]

        score += self._keyword_text_score(
            doc_text=doc_text,
            keywords=signals["keywords"],
        )

        keyword_title_score, n_present, n_absent = self._keyword_title_score(
            doc_title_keywords=doc_title_keywords,
            keywords=signals["keywords"],
        )
        score += keyword_title_score

        score += self._derived_terms_score(
            doc_derived_terms=doc_derived_terms,
            keywords=signals["keywords"],
        )

        score += self._strong_keywords_score(
            doc_text=doc_text,
            doc_title_keywords=doc_title_keywords,
            doc_derived_terms=doc_derived_terms | doc_derived_music_terms,
            strong_keywords=signals["strong_keywords"],
        )

        score += self._content_quality_score(doc)
        score += self._duration_penalty(doc, signals)
        score += self._implicit_penalty_score(doc, signals)

        if doc.metadata is None:
            doc.metadata = {}

        # On conserve des traces utiles dans les métadonnées
        # pour comprendre ensuite pourquoi le document a obtenu ce score.
        doc.metadata["matched_title_keywords"] = n_present
        doc.metadata["missing_title_keywords"] = n_absent
        doc.metadata["detected_city_signal"] = signals["city"]
        doc.metadata["detected_event_type_signal"] = signals["event_type"]
        doc.metadata["detected_music_genre_signal"] = signals["music_genre"]
        doc.metadata["detected_price_signal"] = signals["price_filter"]
        doc.metadata["detected_date_start_signal"] = (
            str(signals["date_start"]) if signals["date_start"] else None
        )
        doc.metadata["detected_date_end_signal"] = (
            str(signals["date_end"]) if signals["date_end"] else None
        )
        doc.metadata["detected_time_mode"] = signals.get("time_mode")
        doc.metadata["detected_is_cultural_query"] = signals["is_cultural_query"]
        doc.metadata["detected_is_broad_activity_query"] = signals["is_broad_activity_query"]

        return score

    # -------------------------------------------------------------------------
    # Tri et diversification
    # -------------------------------------------------------------------------

    def _recency_anchor_date(
        self,
        first_date: date | None,
        last_date: date | None,
    ) -> date:
        # Choisit une date de référence de tri pour le document.
        if first_date:
            return first_date
        if last_date:
            return last_date
        return date.min

    def _temporal_distance_days(
        self,
        doc: Document,
        signals: dict[str, Any],
    ) -> int:
        # Calcule une distance temporelle entre le document
        # et la contrainte de temps de la question.
        first_date, last_date = self._doc_dates(doc)

        if not first_date:
            return 999999

        if not last_date:
            last_date = first_date

        if signals.get("exact_date"):
            target = signals["exact_date"]
            if first_date <= target <= last_date:
                return 0
            if target < first_date:
                return (first_date - target).days
            return (target - last_date).days

        if signals.get("date_start") or signals.get("date_end"):
            start = signals.get("date_start") or signals.get("date_end")
            end = signals.get("date_end") or signals.get("date_start")
            if first_date <= end and last_date >= start:
                return 0
            if end < first_date:
                return (first_date - end).days
            return (start - last_date).days

        if signals.get("month") and signals.get("year"):
            month_start, month_end = self._get_month_bounds(
                signals["year"],
                signals["month"],
            )
            if first_date < month_end and last_date >= month_start:
                return 0
            if month_end <= first_date:
                return (first_date - month_end).days
            return (month_start - last_date).days

        if signals.get("year"):
            year_start, year_end = self._get_year_bounds(signals["year"])
            if first_date < year_end and last_date >= year_start:
                return 0
            if year_end <= first_date:
                return (first_date - year_end).days
            return (year_start - last_date).days

        return 999999

    def _sort_key(
        self,
        doc: Document,
        final_score: float,
        signals: dict[str, Any],
    ) -> tuple[float, int, int, int]:
        # Clé de tri finale :
        # score, proximité temporelle, récence, durée.
        first_date, last_date = self._doc_dates(doc)
        recency_date = self._recency_anchor_date(first_date, last_date)
        duration_days = self._doc_duration_days(doc)
        temporal_distance = self._temporal_distance_days(doc, signals)

        return (
            final_score,
            -temporal_distance,
            recency_date.toordinal(),
            -duration_days,
        )

    def _doc_similarity_signature(self, doc: Document) -> set[str]:
        # Produit une signature légère du document
        # pour détecter les résultats trop similaires.
        md = doc.metadata or {}
        title = self.normalize_text(md.get("title", ""))
        location = self.normalize_text(md.get("location_name", ""))

        title_tokens = {
            token
            for token in re.findall(r"\b[a-z0-9]{3,}\b", title)
            if token not in self.lexical_service.STOPWORDS
        }

        location_tokens = {
            token
            for token in re.findall(r"\b[a-z0-9]{3,}\b", location)
            if token not in self.lexical_service.STOPWORDS
        }

        return title_tokens | location_tokens

    def _apply_diversification(
        self,
        scored_docs: list[tuple[Document, float]],
        signals: dict[str, Any],
        top_k: int,
    ) -> list[Document]:
        # Réduit légèrement les doublons sémantiques ou quasi-jumeaux
        # dans la liste finale.
        if not scored_docs:
            return []

        selected: list[tuple[Document, float]] = []

        for doc, score in scored_docs:
            penalty = 0.0
            candidate_signature = self._doc_similarity_signature(doc)

            for selected_doc, selected_score in selected:
                selected_signature = self._doc_similarity_signature(selected_doc)

                if candidate_signature and selected_signature:
                    overlap = len(candidate_signature & selected_signature)
                    if overlap >= 3 and score <= selected_score:
                        penalty += self.SCORE_WEIGHTS["similarity_penalty"]

            adjusted_score = score - penalty

            if doc.metadata is None:
                doc.metadata = {}

            doc.metadata["diversified_score"] = float(adjusted_score)

            selected.append((doc, adjusted_score))
            selected.sort(
                key=lambda item: self._sort_key(item[0], item[1], signals),
                reverse=True,
            )

            if len(selected) > top_k:
                selected = selected[:top_k]

        return [doc for doc, _ in selected]

    def _apply_strict_post_filter(
        self,
        raw_docs: list[Document],
        signals: dict[str, Any],
    ) -> list[Document]:
        """
        Applique un garde-fou strict seulement sur les contraintes
        explicitement présentes dans la question.

        Important :
        - on ne post-filtre pas "culturel" si la question ne le demande pas
        - on ne restreint pas les requêtes larges de type "que faire"
        """
        # Filet de sécurité post-FAISS.
        # Si la question contient des contraintes explicites,
        # on tente d'écarter les documents franchement incompatibles.
        if not raw_docs:
            return []

        has_explicit_constraints = any(
            [
                signals["city"],
                signals["event_type"],
                signals["music_genre"],
                signals["price_filter"],
                signals["date_start"],
                signals["date_end"],
                signals["exact_date"],
                signals["month"],
                signals["year"],
                signals["is_cultural_query"],
            ]
        )

        if not has_explicit_constraints:
            return raw_docs

        compatible_docs = [
            doc for doc in raw_docs
            if self._is_doc_compatible_with_query(doc, signals)
        ]

        if compatible_docs:
            return compatible_docs

        return raw_docs

    # -------------------------------------------------------------------------
    # API publique
    # -------------------------------------------------------------------------

    def rank_documents(
        self,
        question: str,
        raw_docs: list[Document],
        top_k: int = 3,
    ) -> list[Document]:
        """
        Classe les documents candidats et retourne les meilleurs.

        Étapes :
        1. extraction des signaux de question
        2. garde-fou strict post-vectoriel
        3. scoring métier
        4. tri
        5. diversification légère
        """
        # API principale du service.
        if not raw_docs:
            return []

        signals = self.extract_signals(question)
        candidate_docs = self._apply_strict_post_filter(raw_docs, signals)

        scored_docs: list[tuple[Document, float]] = []
        for doc in candidate_docs:
            final_score = self.score_document(doc, signals)

            if doc.metadata is None:
                doc.metadata = {}

            first_date, last_date = self._doc_dates(doc)
            doc.metadata["final_score"] = float(final_score)
            doc.metadata["sort_recency_date"] = str(
                self._recency_anchor_date(first_date, last_date)
            )
            doc.metadata["duration_days"] = self._doc_duration_days(doc)

            scored_docs.append((doc, final_score))

        scored_docs.sort(
            key=lambda item: self._sort_key(item[0], item[1], signals),
            reverse=True,
        )

        return self._apply_diversification(
            scored_docs=scored_docs,
            signals=signals,
            top_k=top_k,
        )

    def rank_documents_with_scores(
        self,
        question: str,
        raw_docs: list[Document],
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Variante de debug du ranking.

        Retourne les documents avec leurs scores et plusieurs signaux utiles
        à l'analyse du classement.
        """
        # Version explicable / debug du service.
        # Très utile pour comprendre le comportement du ranking en soutenance.
        if not raw_docs:
            return []

        signals = self.extract_signals(question)
        candidate_docs = self._apply_strict_post_filter(raw_docs, signals)

        rows: list[dict[str, Any]] = []
        for doc in candidate_docs:
            md = doc.metadata or {}
            score = self.score_document(doc, signals)

            first_date, last_date = self._doc_dates(doc)
            recency_date = self._recency_anchor_date(first_date, last_date)
            duration_days = self._doc_duration_days(doc)
            temporal_distance = self._temporal_distance_days(doc, signals)

            rows.append(
                {
                    "title": md.get("title", ""),
                    "location_name": md.get("location_name", ""),
                    "city": md.get("city", ""),
                    "region": md.get("region", ""),
                    "first_date": md.get("first_date", ""),
                    "last_date": md.get("last_date", ""),
                    "event_type": md.get("canonical_event_type", "") or md.get("event_type", ""),
                    "music_genre": md.get("music_genre", ""),
                    "price_info": md.get("price_info", ""),
                    "is_free": md.get("is_free"),
                    "keywords_title": md.get("keywords_title", []),
                    "derived_event_terms": md.get("derived_event_terms", []),
                    "derived_music_terms": md.get("derived_music_terms", []),
                    "content_quality": md.get("content_quality"),
                    "has_long_description": md.get("has_long_description"),
                    "is_single_day": md.get("is_single_day"),
                    "vector_score": md.get("vector_score"),
                    "final_score": score,
                    "recency_date": str(recency_date),
                    "temporal_distance_days": temporal_distance,
                    "duration_days": duration_days,
                    "matched_title_keywords": md.get("matched_title_keywords", 0),
                    "missing_title_keywords": md.get("missing_title_keywords", 0),
                    "detected_city_signal": md.get("detected_city_signal"),
                    "detected_event_type_signal": md.get("detected_event_type_signal"),
                    "detected_music_genre_signal": md.get("detected_music_genre_signal"),
                    "detected_price_signal": md.get("detected_price_signal"),
                    "detected_date_start_signal": md.get("detected_date_start_signal"),
                    "detected_date_end_signal": md.get("detected_date_end_signal"),
                    "detected_time_mode": md.get("detected_time_mode"),
                    "detected_is_cultural_query": md.get("detected_is_cultural_query"),
                    "detected_is_broad_activity_query": md.get("detected_is_broad_activity_query"),
                    "is_strong_cultural_candidate": md.get("is_strong_cultural_candidate", False),
                    "is_weak_cultural_candidate": md.get("is_weak_cultural_candidate", False),
                    "has_market_signal": md.get("has_market_signal", False),
                    "has_repair_signal": md.get("has_repair_signal", False),
                    "has_business_signal": md.get("has_business_signal", False),
                    "url": md.get("source_url", "") or md.get("url", ""),
                }
            )

        rows = sorted(
            rows,
            key=lambda row: (
                row["final_score"],
                -row["temporal_distance_days"],
                datetime.strptime(row["recency_date"], "%Y-%m-%d").date().toordinal()
                if row["recency_date"] and row["recency_date"] != "0001-01-01"
                else date.min.toordinal(),
                -row["duration_days"],
            ),
            reverse=True,
        )
        return rows[:top_k]