"""
Service lexical partagé pour le pipeline RAG OpenAgenda.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/lexical_service.py` (commit 18f7ce1).

Ce module centralise les opérations lexicales réutilisables dans
plusieurs services applicatifs, notamment :

- `document_service`
- `filter_service`
- `retrieval_service`

Il permet de :

- normaliser les textes
- extraire des tokens et des mots-clés
- détecter des types d'événements
- détecter des genres musicaux
- détecter quelques signaux métier simples dans une question
- dériver des termes métier utiles pour l'indexation et le retrieval
- fournir des helpers communs de matching lexical
- détecter quelques signaux négatifs utiles pour éviter certains faux positifs

Ce service ne gère pas :

- le parsing des dates
- le scoring métier
- la recherche vectorielle
- la génération de réponse

Philosophie
-----------
Le service lexical doit rester :

- cohérent entre documents et questions
- explicable
- prudent
- peu coûteux à exécuter
- simple à maintenir

Il vaut mieux renvoyer un signal vide qu'un faux signal métier.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


class LexicalService:
    """
    Service lexical partagé.

    Cette classe fournit un ensemble d'utilitaires de normalisation,
    de matching lexical et de détection de signaux métier simples
    pour l'ensemble du pipeline RAG.

    Le service est volontairement conservateur :

    - il privilégie la robustesse
    - il limite les déductions agressives
    - il sert de base commune à plusieurs composants
    """
    # Cette classe joue le rôle de couche lexicale commune.
    # Elle évite que chaque service réimplémente sa propre logique
    # de normalisation, de matching et de détection de signaux métier.

    STOPWORDS = {
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "un",
        "une",
        "et",
        "a",
        "au",
        "aux",
        "en",
        "pour",
        "avec",
        "sur",
        "dans",
        "par",
        "chez",
        "d",
        "l",
        "quel",
        "quels",
        "quelle",
        "quelles",
        "que",
        "qui",
        "ou",
        "où",
        "est",
        "sont",
        "ont",
        "lieu",
        "faire",
        "propose",
        "proposes",
        "proposé",
        "proposés",
        "proposée",
        "proposées",
        "evenement",
        "evenements",
        "événement",
        "événements",
        "culturel",
        "culturels",
        "culturelle",
        "culturelles",
        "ces",
        "cet",
        "cette",
        "aujourd",
        "hui",
        "demain",
        "week",
        "end",
        "weekend",
        "weekends",
        "mois",
        "annee",
        "année",
        "jour",
        "jours",
    }
    # Mots trop fréquents ou peu discriminants pour l'extraction de keywords.

    EVENT_TYPE_TERMS = {
        "exposition": [
            "exposition",
            "expositions",
            "expo",
            "expos",
            "vernissage",
            "vernissages",
            "installation",
            "installations",
            "retrospective",
            "retrospectives",
            "rétrospective",
            "rétrospectives",
        ],
        "concert": [
            "concert",
            "concerts",
            "live",
            "showcase",
            "showcases",
            "dj set",
            "dj sets",
            "performance musicale",
            "performances musicales",
            "session live",
            "sessions live",
            "soiree concert",
            "soirée concert",
            "soirees concert",
            "soirées concert",
        ],
        "visite": [
            "visite",
            "visites",
            "visite guidee",
            "visites guidees",
            "visite guidée",
            "visites guidées",
            "parcours guide",
            "parcours guides",
            "parcours guidé",
            "parcours guidés",
            "parcours commente",
            "parcours commentes",
            "parcours commenté",
            "parcours commentés",
        ],
        "conference": [
            "conference",
            "conferences",
            "conférence",
            "conférences",
            "debat",
            "debats",
            "débat",
            "débats",
            "table ronde",
            "tables rondes",
            "masterclass",
        ],
        "atelier": [
            "atelier",
            "ateliers",
            "initiation",
            "initiations",
            "workshop",
            "workshops",
            "atelier participatif",
            "ateliers participatifs",
        ],
        "conte": [
            "conte",
            "contes",
            "conteur",
            "conteurs",
            "conteuse",
            "conteuses",
            "lecture contee",
            "lectures contees",
            "lecture contée",
            "lectures contées",
        ],
        "projection": [
            "projection",
            "projections",
            "film",
            "films",
            "cinema",
            "cinéma",
            "seance",
            "seances",
            "séance",
            "séances",
            "cine debat",
            "ciné débat",
            "cine-debat",
            "ciné-débat",
            "documentaire",
            "documentaires",
        ],
        "festival": [
            "festival",
            "festivals",
            "micro festival",
            "micro festivals",
            "micro-festival",
            "micro-festivals",
            "biennale",
            "biennales",
        ],
        "marche": [
            "marche",
            "marches",
            "marché",
            "marchés",
            "braderie",
            "braderies",
            "brocante",
            "brocantes",
            "vide grenier",
            "vide greniers",
            "vide-grenier",
            "vide-greniers",
            "pop up market",
            "pop up markets",
            "pop-up market",
            "pop-up markets",
        ],
        "spectacle": [
            "spectacle",
            "spectacles",
            "theatre",
            "théâtre",
            "representation",
            "representations",
            "représentation",
            "représentations",
            "one man show",
            "one man shows",
            "one-man-show",
            "one-man-shows",
        ],
        "lecture": [
            "lecture",
            "lectures",
            "lecture publique",
            "lectures publiques",
            "lecture musicale",
            "lectures musicales",
            "lecture performee",
            "lectures performees",
            "lecture performée",
            "lectures performées",
        ],
    }
    # Dictionnaire principal de correspondance
    # type canonique -> variantes lexicales reconnues.

    MUSIC_GENRE_TERMS = {
        "rock": ["rock", "garage", "punk", "noise", "metal", "grunge", "hardcore"],
        "jazz": ["jazz", "swing", "bebop"],
        "blues": ["blues"],
        "electro": ["electro", "électro", "techno", "house"],
        "folk": ["folk"],
        "rap": ["rap", "hip hop", "hip-hop"],
        "classique": ["classique", "baroque", "symphonique", "orchestre"],
    }
    # Référentiel des genres musicaux canoniques et de leurs variantes.

    AUDIENCE_TERMS = {
        "enfant": ["enfant", "enfants", "jeune public"],
        "famille": ["famille", "familial", "familiale", "tout public"],
    }
    # Référentiel simple de signaux de public cible.

    FREE_MARKERS = [
        "gratuit",
        "gratuite",
        "gratuits",
        "gratuites",
        "entree libre",
        "entrée libre",
        "acces libre",
        "accès libre",
        "libre participation",
        "sans frais",
        "free",
    ]
    # Marqueurs de gratuité détectables dans les textes.

    PAID_MARKERS = [
        "payant",
        "payante",
        "payants",
        "payantes",
        "billetterie",
        "tarif",
        "tarifs",
        "prix",
        "reservation obligatoire",
        "réservation obligatoire",
        "sur reservation",
        "sur réservation",
        "€",
        "euro",
        "euros",
    ]
    # Marqueurs de paiement ou de billetterie.

    CULTURAL_EVENT_TYPES = {
        "exposition",
        "concert",
        "festival",
        "projection",
        "spectacle",
        "conte",
        "lecture",
    }
    # Types considérés comme culturels forts.

    MUSICAL_EVENT_TYPES = {
        "concert",
        "festival",
        "spectacle",
    }
    # Types ayant une dimension musicale suffisamment forte
    # pour servir dans certains garde-fous.

    STRONG_CULTURAL_TERMS = [
        "exposition",
        "expo",
        "vernissage",
        "concert",
        "festival",
        "projection",
        "cinema",
        "cinéma",
        "spectacle",
        "theatre",
        "théâtre",
        "conte",
        "lecture",
        "danse",
        "poesie",
        "poésie",
        "musee",
        "musée",
        "galerie",
    ]
    # Vocabulaire culturel explicite et fort.

    MEDIUM_CULTURAL_TERMS = [
        "conference",
        "conférence",
        "debat",
        "débat",
        "visite",
        "atelier",
        "patrimoine",
        "architecture",
        "photographie",
        "photo",
    ]
    # Vocabulaire culturel utile mais moins fort,
    # donc à manier avec prudence.

    # Attention :
    # ces termes faibles ne doivent PAS activer un filtre culturel fort.
    WEAK_ACTIVITY_TERMS = [
        "que faire",
        "quoi faire",
        "sortie",
        "sorties",
        "activite",
        "activité",
        "activites",
        "activités",
        "agenda",
        "programme",
    ]
    # Vocabulaire d'exploration large, non suffisant
    # pour conclure à une vraie contrainte culturelle.

    MARKET_TERMS = [
        "braderie",
        "destockage",
        "déstockage",
        "brocante",
        "vide grenier",
        "vide-grenier",
        "market",
        "pop up market",
        "pop-up market",
        "vinyl pop up",
        "vinyl pop-up",
    ]
    # Signaux de marché / vente / brocante.

    REPAIR_TERMS = [
        "repair cafe",
        "repair café",
        "reparation",
        "réparation",
        "reparer",
        "réparer",
        "atelier de reparation",
        "atelier de réparation",
    ]
    # Signaux de réparation ou repair café.

    RELIGIOUS_TERMS = [
        "messe",
        "veillee",
        "veillée",
        "priere",
        "prière",
        "prieure",
        "prieuré",
        "saint",
        "paroisse",
        "patronage",
    ]
    # Signaux religieux simples.

    BUSINESS_TERMS = [
        "franchise",
        "networking",
        "investisseur",
        "entrepreneur",
        "concours",
        "business",
        "salon professionnel",
    ]
    # Signaux business ou professionnels,
    # utiles pour éviter des faux positifs culturels.

    KNOWN_CITY_TERMS = [
        "montpellier",
        "paris",
        "sete",
        "sète",
        "toulouse",
        "lyon",
        "marseille",
        "bordeaux",
        "lille",
        "nantes",
    ]
    # Réserve de villes connues, potentiellement utile
    # pour certains helpers géographiques futurs.

    def __init__(self) -> None:
        """
        Initialise le service lexical.

        Les tables lexicales normalisées sont pré-calculées une fois afin de :

        - réduire les appels répétés à `normalize_text`
        - accélérer les matching fréquents
        - garder une logique homogène dans tout le pipeline
        """
        # Prépare à l'avance des versions normalisées des référentiels
        # pour éviter de refaire le même travail à chaque appel.
        self._event_type_terms_norm = self._normalize_term_mapping(self.EVENT_TYPE_TERMS)
        self._music_genre_terms_norm = self._normalize_term_mapping(self.MUSIC_GENRE_TERMS)
        self._audience_terms_norm = self._normalize_term_mapping(self.AUDIENCE_TERMS)

        self._free_markers_norm = self._normalize_terms(self.FREE_MARKERS)
        self._paid_markers_norm = self._normalize_terms(self.PAID_MARKERS)

        self._strong_cultural_terms_norm = self._normalize_terms(self.STRONG_CULTURAL_TERMS)
        self._medium_cultural_terms_norm = self._normalize_terms(self.MEDIUM_CULTURAL_TERMS)
        self._weak_activity_terms_norm = self._normalize_terms(self.WEAK_ACTIVITY_TERMS)

        self._market_terms_norm = self._normalize_terms(self.MARKET_TERMS)
        self._repair_terms_norm = self._normalize_terms(self.REPAIR_TERMS)
        self._religious_terms_norm = self._normalize_terms(self.RELIGIOUS_TERMS)
        self._business_terms_norm = self._normalize_terms(self.BUSINESS_TERMS)
        self._known_city_terms_norm = self._normalize_terms(self.KNOWN_CITY_TERMS)

    # ------------------------------------------------------------------
    # Utilitaires internes de normalisation
    # ------------------------------------------------------------------

    def _normalize_terms(self, terms: list[str] | set[str] | tuple[str, ...]) -> list[str]:
        """
        Normalise une collection de termes et supprime les doublons.

        Parameters
        ----------
        terms : list[str] | set[str] | tuple[str, ...]
            Liste brute de termes.

        Returns
        -------
        list[str]
            Liste triée de termes normalisés non vides.
        """
        # Sert à homogénéiser une liste de termes métier :
        # minuscules, accents retirés, espaces nettoyés, doublons supprimés.
        normalized = {
            self.normalize_text(term)
            for term in terms
            if self.normalize_text(term)
        }
        return sorted(normalized)

    def _normalize_term_mapping(self, mapping: dict[str, list[str]]) -> dict[str, list[str]]:
        """
        Normalise les variantes lexicales d'un dictionnaire canonique.

        Parameters
        ----------
        mapping : dict[str, list[str]]
            Dictionnaire de type canonique -> variantes textuelles.

        Returns
        -------
        dict[str, list[str]]
            Dictionnaire équivalent avec variantes normalisées.
        """
        # Applique la normalisation à tout un dictionnaire
        # de variantes lexicales afin de le rendre exploitable
        # par les fonctions de matching.
        normalized_mapping: dict[str, list[str]] = {}

        for key, values in mapping.items():
            normalized_mapping[key] = self._normalize_terms(values)

        return normalized_mapping

    # ------------------------------------------------------------------
    # Utilitaires texte de base
    # ------------------------------------------------------------------

    def safe(self, value: object) -> str:
        """
        Convertit une valeur potentiellement nulle en chaîne.

        Parameters
        ----------
        value : object
            Valeur arbitraire.

        Returns
        -------
        str
            Chaîne vide si la valeur est nulle, sinon conversion en texte.
        """
        # Petit garde-fou utilitaire pour éviter les None
        # dans la chaîne de traitement textuel.
        return "" if value is None else str(value)

    def clean_text(self, value: object) -> str:
        """
        Nettoie un texte sans en modifier le sens métier.

        Le nettoyage applique :

        - suppression des retours ligne
        - suppression des tabulations
        - réduction des espaces multiples

        Parameters
        ----------
        value : object
            Valeur textuelle brute.

        Returns
        -------
        str
            Texte nettoyé.
        """
        # Produit une version lisible et stable du texte brut
        # avant toute normalisation plus poussée.
        text = self.safe(value)
        text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def extract_text(self, value: object) -> str:
        """
        Extrait un texte propre depuis une valeur arbitraire.

        La valeur peut être :

        - une chaîne simple
        - un dictionnaire multilingue
        - toute autre valeur convertible en texte

        En cas de dictionnaire, la priorité est donnée à :

        1. `fr`
        2. `en`
        3. premier champ non vide

        Parameters
        ----------
        value : object
            Valeur source.

        Returns
        -------
        str
            Texte extrait.
        """
        # Uniformise des formats d'entrée variés.
        # Très utile pour OpenAgenda où certains champs sont multilingues.
        if isinstance(value, dict):
            for key in ("fr", "en"):
                if key in value and value[key]:
                    return self.clean_text(value[key])

            for candidate in value.values():
                text = self.clean_text(candidate)
                if text:
                    return text

            return ""

        return self.clean_text(value)

    def normalize_text(self, value: object) -> str:
        """
        Normalise un texte pour les comparaisons lexicales simples.

        Transformations appliquées :

        - minuscules
        - suppression des accents
        - suppression de la ponctuation non utile
        - compactage des espaces

        Parameters
        ----------
        value : object
            Valeur source.

        Returns
        -------
        str
            Texte normalisé.
        """
        # C'est la base de toute comparaison lexicale du service.
        # Deux textes visuellement différents deviennent comparables.
        text = self.extract_text(value).lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if not unicodedata.combining(char))
        text = re.sub(r"[^\w\s-]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def tokenize_text(self, value: object) -> list[str]:
        """
        Découpe un texte normalisé en tokens simples.

        Parameters
        ----------
        value : object
            Texte source.

        Returns
        -------
        list[str]
            Liste de tokens alphanumériques de longueur >= 3.
        """
        # Version légère de tokenisation,
        # suffisante pour l'extraction de mots-clés simples.
        text = self.normalize_text(value)
        return re.findall(r"\b[a-z0-9]{3,}\b", text)

    def join_texts(self, *values: object) -> str:
        """
        Concatène proprement plusieurs morceaux de texte.

        Parameters
        ----------
        *values : object
            Morceaux de texte à concaténer.

        Returns
        -------
        str
            Texte final nettoyé.
        """
        # Assemble plusieurs champs textuels en une seule chaîne propre,
        # sans laisser traîner de bruit de formatage.
        return self.clean_text(
            " ".join(self.extract_text(value) for value in values if value)
        )

    # ------------------------------------------------------------------
    # Matching lexical simple
    # ------------------------------------------------------------------

    def contains_term(self, text: object, term: object) -> bool:
        """
        Vérifie la présence d'un terme complet dans un texte normalisé.

        Cette fonction fonctionne pour :

        - un mot simple
        - une expression multi-mots

        Parameters
        ----------
        text : object
            Texte de recherche.
        term : object
            Terme recherché.

        Returns
        -------
        bool
            True si le terme est présent, sinon False.
        """
        # Vérifie une présence lexicale avec borne de mot.
        # C'est plus sûr qu'un simple "in", surtout pour éviter
        # des matches parasites au milieu d'autres mots.
        normalized_text = self.normalize_text(text)
        normalized_term = self.normalize_text(term)

        if not normalized_text or not normalized_term:
            return False

        return re.search(
            rf"\b{re.escape(normalized_term)}\b",
            normalized_text,
        ) is not None

    def contains_any_term(self, text: object, terms: list[str]) -> bool:
        """
        Vérifie si au moins un terme complet est présent dans un texte.

        Parameters
        ----------
        text : object
            Texte inspecté.
        terms : list[str]
            Liste de termes candidats.

        Returns
        -------
        bool
            True si au moins un terme est trouvé.
        """
        # Helper pratique pour tester un ensemble de variantes lexicales.
        normalized_text = self.normalize_text(text)
        if not normalized_text or not terms:
            return False

        normalized_terms = self._normalize_terms(terms)

        for term in normalized_terms:
            if re.search(rf"\b{re.escape(term)}\b", normalized_text):
                return True

        return False

    def count_matching_terms(self, text: object, terms: list[str]) -> int:
        """
        Compte combien de termes distincts sont présents dans un texte.

        Parameters
        ----------
        text : object
            Texte inspecté.
        terms : list[str]
            Liste de termes candidats.

        Returns
        -------
        int
            Nombre de termes distincts trouvés.
        """
        # Mesure la densité d'un signal lexical.
        # Utile lorsqu'un seul terme isolé est trop faible
        # pour conclure à un vrai contexte métier.
        normalized_text = self.normalize_text(text)
        if not normalized_text or not terms:
            return 0

        normalized_terms = self._normalize_terms(terms)

        return sum(
            1
            for term in normalized_terms
            if re.search(rf"\b{re.escape(term)}\b", normalized_text)
        )

    # ------------------------------------------------------------------
    # Extraction de mots-clés
    # ------------------------------------------------------------------

    def extract_keywords(
        self,
        text: object,
        *,
        min_len: int = 3,
        remove_stopwords: bool = True,
    ) -> list[str]:
        """
        Extrait des mots-clés simples et uniques depuis un texte.

        Règles appliquées :

        - longueur minimale configurable
        - suppression optionnelle des stopwords
        - exclusion des années au format `20xx`

        Parameters
        ----------
        text : object
            Texte source.
        min_len : int, default=3
            Longueur minimale d'un mot-clé.
        remove_stopwords : bool, default=True
            Indique si les stopwords doivent être supprimés.

        Returns
        -------
        list[str]
            Liste triée de mots-clés.
        """
        # Extraction volontairement simple :
        # pas de NLP lourd, juste une base robuste pour enrichir les textes.
        tokens = self.tokenize_text(text)

        keywords = {
            token
            for token in tokens
            if len(token) >= min_len
            and not re.fullmatch(r"20\d{2}", token)
            and (not remove_stopwords or token not in self.STOPWORDS)
        }

        return sorted(keywords)

    def extract_title_keywords(self, title: str) -> list[str]:
        """
        Extrait des mots-clés simples depuis un titre.

        Parameters
        ----------
        title : str
            Titre documentaire.

        Returns
        -------
        list[str]
            Liste triée de mots-clés du titre.
        """
        # Spécialisation légère pour le titre,
        # souvent plus dense et plus discriminant que le reste.
        return self.extract_keywords(title, min_len=3, remove_stopwords=True)

    # ------------------------------------------------------------------
    # Détection de signaux métier
    # ------------------------------------------------------------------

    def extract_event_type(self, text: object) -> str | None:
        """
        Détecte un type d'événement explicite dans un texte.

        Cette fonction sert surtout à analyser une question utilisateur
        ou un texte documentaire court.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        str | None
            Type canonique détecté, ou None.
        """
        # Détection directe d'un type métier explicite.
        # Très utile côté requête utilisateur.
        text_norm = self.normalize_text(text)
        if not text_norm:
            return None

        for canonical_type, variants in self._event_type_terms_norm.items():
            if self.contains_any_term(text_norm, variants):
                return canonical_type

        return None

    def extract_music_genre(self, text: object) -> str | None:
        """
        Détecte un genre musical explicite dans un texte.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        str | None
            Genre canonique détecté, ou None.
        """
        # Détection directe d'un genre musical explicitement formulé.
        text_norm = self.normalize_text(text)
        if not text_norm:
            return None

        for canonical_genre, variants in self._music_genre_terms_norm.items():
            if self.contains_any_term(text_norm, variants):
                return canonical_genre

        return None

    def extract_audience_terms(self, text: object) -> list[str]:
        """
        Extrait des signaux simples de public cible.

        Important :
        cette extraction ne doit servir au filtrage de question
        que si le public est explicitement formulé dans la question.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        list[str]
            Liste triée de signaux de public détectés.
        """
        # Détecte des indices simples de public,
        # sans extrapoler au-delà de ce qui est explicitement dit.
        text_norm = self.normalize_text(text)
        if not text_norm:
            return []

        terms: set[str] = set()

        for canonical_audience, variants in self._audience_terms_norm.items():
            if self.contains_any_term(text_norm, variants):
                terms.add(canonical_audience)

        return sorted(terms)

    def extract_price_info(self, *values: object) -> tuple[str, bool | None]:
        """
        Déduit une information simple de tarification à partir de plusieurs textes.

        Retourne :

        - `("gratuit", True)`
        - `("payant", False)`
        - `("inconnu", None)`

        Parameters
        ----------
        *values : object
            Champs textuels à agréger.

        Returns
        -------
        tuple[str, bool | None]
            Libellé tarifaire et booléen de gratuité.
        """
        # Agrège plusieurs champs textuels pour construire
        # un signal tarifaire simple mais utile au pipeline.
        text = self.join_texts(*values)
        text_norm = self.normalize_text(text)

        if not text_norm:
            return "inconnu", None

        has_free = self.contains_any_term(text_norm, self._free_markers_norm)
        has_paid = self.contains_any_term(text_norm, self._paid_markers_norm)

        if has_free and not has_paid:
            return "gratuit", True

        if has_paid and not has_free:
            return "payant", False

        if has_free and has_paid:
            return "inconnu", None

        return "inconnu", None

    def has_market_signal(self, text: object) -> bool:
        """
        Indique si un texte contient un signal de marché ou de braderie.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        bool
            True si un signal de marché est détecté.
        """
        # Sert à repérer des événements de vente ou de brocante,
        # potentiellement peu pertinents dans certains contextes culturels.
        return self.contains_any_term(text, self._market_terms_norm)

    def has_repair_signal(self, text: object) -> bool:
        """
        Indique si un texte contient un signal de réparation.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        bool
            True si un signal de réparation est détecté.
        """
        # Sert à distinguer des événements de type réparation / repair café.
        return self.contains_any_term(text, self._repair_terms_norm)

    def has_religious_signal(self, text: object) -> bool:
        """
        Indique si un texte contient un signal religieux.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        bool
            True si un signal religieux est détecté.
        """
        # Petit détecteur de signaux religieux,
        # utile pour éviter certains faux positifs.
        return self.contains_any_term(text, self._religious_terms_norm)

    def has_business_signal(self, text: object) -> bool:
        """
        Indique si un texte contient un signal business ou événementiel non culturel.

        Parameters
        ----------
        text : object
            Texte à analyser.

        Returns
        -------
        bool
            True si un signal business est détecté.
        """
        # Détecte un vocabulaire business / pro / networking
        # qui peut orienter le document hors du périmètre culturel recherché.
        return self.contains_any_term(text, self._business_terms_norm)

    def is_cultural_query(self, text: object) -> bool:
        """
        Détecte si une question contient une contrainte culturelle explicite.

        Règle importante :
        ce booléen ne doit être activé que si la dimension culturelle
        est explicitement présente dans la question.

        Active `True` si la question contient par exemple :
        - un type culturel explicite : exposition, concert, festival...
        - l'expression "événement culturel" / "sortie culturelle"
        - un vocabulaire culturel fort explicite

        N'active PAS `True` pour :
        - "que faire"
        - "quoi faire"
        - "sorties"
        - formulations larges d'exploration sans contrainte culturelle explicite

        Parameters
        ----------
        text : object
            Question utilisateur.

        Returns
        -------
        bool
            True si la requête contient une contrainte culturelle explicite.
        """
        # Fonction clé pour ne pas activer à tort
        # un filtre culturel fort sur des requêtes trop vagues.
        text_norm = self.normalize_text(text)
        if not text_norm:
            return False

        event_type = self.extract_event_type(text_norm)
        if event_type in self.CULTURAL_EVENT_TYPES:
            return True

        if self.contains_any_term(
            text_norm,
            [
                "evenement culturel",
                "evenements culturels",
                "sortie culturelle",
                "sorties culturelles",
                "activite culturelle",
                "activites culturelles",
            ],
        ):
            return True

        if self.contains_any_term(text_norm, self._strong_cultural_terms_norm):
            return True

        return False

    def is_broad_activity_query(self, text: object) -> bool:
        """
        Détecte une requête large d'exploration d'activités.

        Cette information ne doit pas activer un filtre fort.
        Elle sert seulement à distinguer les questions du type :

        - "que faire à Montpellier ?"
        - "quoi faire ce week-end ?"
        - "sorties à Montpellier"

        d'une vraie contrainte culturelle explicite.

        Parameters
        ----------
        text : object
            Question utilisateur.

        Returns
        -------
        bool
            True si la question est une demande large d'activités / sorties.
        """
        # Détecte un besoin d'exploration large,
        # utile pour adapter la logique de filtrage sans surcontraindre.
        text_norm = self.normalize_text(text)
        if not text_norm:
            return False

        return self.contains_any_term(text_norm, self._weak_activity_terms_norm)

    def extract_explicit_price_filter(self, text: object) -> str | None:
        """
        Extrait un filtre tarifaire uniquement s'il est explicite dans la question.

        Parameters
        ----------
        text : object
            Question utilisateur.

        Returns
        -------
        str | None
            "gratuit", "payant" ou None.
        """
        # Respecte le principe métier :
        # on ne filtre fortement que sur ce que l'utilisateur a vraiment demandé.
        text_norm = self.normalize_text(text)
        if not text_norm:
            return None

        if self.contains_any_term(text_norm, self._free_markers_norm):
            return "gratuit"

        if self.contains_any_term(text_norm, self._paid_markers_norm):
            return "payant"

        return None

    # ------------------------------------------------------------------
    # Dérivation de termes métier pour les documents
    # ------------------------------------------------------------------

    def derive_event_terms(self, title: str, description: str, event_type: str) -> list[str]:
        """
        Déduit quelques termes métier utiles pour enrichir le texte indexé.

        Cette fonction est un peu plus généreuse que l'inférence canonique,
        mais reste volontairement resserrée pour éviter les enrichissements
        trop faibles ou trop ambigus.

        Parameters
        ----------
        title : str
            Titre du document.
        description : str
            Description consolidée du document.
        event_type : str
            Type brut du document.

        Returns
        -------
        list[str]
            Liste triée de termes métier dérivés.
        """
        # Produit des termes supplémentaires utiles à l'embedding,
        # au matching léger et au ranking.
        title_norm = self.normalize_text(title)
        desc_norm = self.normalize_text(description)
        event_type_norm = self.normalize_text(event_type)

        terms: set[str] = set()

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["exposition"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["exposition"])
            or (
                self.contains_any_term(desc_norm, self._event_type_terms_norm["exposition"])
                and self.contains_any_term(
                    desc_norm,
                    ["art", "artiste", "galerie", "photo", "photographie"],
                )
            )
        ):
            terms.update(["exposition", "expo", "vernissage"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["concert"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["concert"])
            or (
                self.contains_any_term(
                    desc_norm,
                    ["concert", "concerts", "musique", "musical", "live", "dj set"],
                )
                and self.contains_any_term(
                    desc_norm,
                    ["scene", "scène", "artiste", "groupe", "chanteur", "musicien", "duo"],
                )
            )
        ):
            terms.update(["concert", "musique"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["atelier"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["atelier"])
        ):
            terms.update(["atelier"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["conte"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["conte"])
        ):
            terms.update(["conte"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["conference"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["conference"])
        ):
            terms.update(["conference"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["visite"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["visite"])
        ):
            terms.update(["visite"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["projection"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["projection"])
        ):
            terms.update(["projection", "film", "cinema"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["festival"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["festival"])
        ):
            terms.update(["festival"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["marche"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["marche"])
        ):
            terms.update(["marche"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["spectacle"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["spectacle"])
        ):
            terms.update(["spectacle", "theatre"])

        if (
            self.contains_any_term(title_norm, self._event_type_terms_norm["lecture"])
            or self.contains_any_term(event_type_norm, self._event_type_terms_norm["lecture"])
        ):
            terms.update(["lecture"])

        if (
            self.contains_any_term(title_norm, ["photo", "photographie"])
            or self.contains_any_term(desc_norm, ["photo", "photographie"])
        ):
            terms.update(["photo", "photographie"])

        if (
            self.contains_any_term(title_norm, ["vinyl"])
            or self.contains_any_term(desc_norm, ["vinyl"])
        ):
            terms.update(["vinyl"])

        return sorted(terms)

    def derive_music_terms(self, title: str, description: str, event_type: str) -> list[str]:
        """
        Déduit quelques termes de genre musical utiles pour le ranking.

        La logique reste prudente :

        - aucun genre n'est inféré sur des indices trop faibles
        - le terme canonique est conservé
        - les variantes réellement présentes peuvent être ajoutées

        Parameters
        ----------
        title : str
            Titre du document.
        description : str
            Description consolidée.
        event_type : str
            Type brut.

        Returns
        -------
        list[str]
            Liste triée de termes musicaux.
        """
        # Enrichissement musical plus fin que le seul genre canonique.
        # On conserve à la fois la catégorie et certaines variantes réellement vues.
        title_norm = self.normalize_text(title)
        desc_norm = self.normalize_text(description)
        event_type_norm = self.normalize_text(event_type)

        support_text = f"{title_norm} {desc_norm} {event_type_norm}".strip()
        if not support_text:
            return []

        terms: set[str] = set()

        for canonical_genre, variants in self._music_genre_terms_norm.items():
            if self.contains_any_term(support_text, variants):
                terms.add(canonical_genre)

                for variant in variants:
                    if self.contains_term(support_text, variant):
                        terms.add(variant)

        return sorted(terms)

    # ------------------------------------------------------------------
    # Inférence canonique prudente pour les documents
    # ------------------------------------------------------------------

    def infer_canonical_event_type(
        self,
        title: str,
        description: str,
        event_type: str,
    ) -> str:
        """
        Déduit un type d'événement canonique simple.

        Logique prudente :

        1. priorité au titre
        2. puis au champ `event_type` brut
        3. puis à quelques signaux forts dans la description
        4. sinon chaîne vide

        On préfère renvoyer une chaîne vide plutôt qu'un faux type.

        Parameters
        ----------
        title : str
            Titre documentaire.
        description : str
            Description consolidée.
        event_type : str
            Type brut.

        Returns
        -------
        str
            Type canonique inféré ou chaîne vide.
        """
        # Essaie d'homogénéiser le type documentaire
        # tout en restant prudent pour éviter les faux positifs.
        title_norm = self.normalize_text(title)
        desc_norm = self.normalize_text(description)
        event_type_norm = self.normalize_text(event_type)

        strong_rules = [
            ("exposition", self._event_type_terms_norm["exposition"]),
            ("concert", self._event_type_terms_norm["concert"]),
            ("visite", self._event_type_terms_norm["visite"]),
            ("conference", self._event_type_terms_norm["conference"]),
            ("atelier", self._event_type_terms_norm["atelier"]),
            ("conte", self._event_type_terms_norm["conte"]),
            ("projection", self._event_type_terms_norm["projection"]),
            ("festival", self._event_type_terms_norm["festival"]),
            ("marche", self._event_type_terms_norm["marche"]),
            ("spectacle", self._event_type_terms_norm["spectacle"]),
            ("lecture", self._event_type_terms_norm["lecture"]),
        ]

        for canonical_type, variants in strong_rules:
            if self.contains_any_term(title_norm, variants):
                return canonical_type

        for canonical_type, variants in strong_rules:
            if self.contains_any_term(event_type_norm, variants):
                return canonical_type

        if (
            self.contains_any_term(desc_norm, self._event_type_terms_norm["exposition"])
            and self.contains_any_term(
                desc_norm,
                ["art", "artiste", "galerie", "photo", "photographie"],
            )
        ):
            return "exposition"

        if (
            self.contains_any_term(desc_norm, ["concert", "concerts", "musique", "live", "dj set"])
            and self.contains_any_term(
                desc_norm,
                ["scene", "scène", "groupe", "musicien", "chanteur", "duo"],
            )
        ):
            return "concert"

        if (
            self.contains_any_term(desc_norm, self._event_type_terms_norm["projection"])
            and self.contains_any_term(desc_norm, ["film", "projection", "cinema", "cinéma"])
        ):
            return "projection"

        if (
            self.contains_any_term(desc_norm, self._event_type_terms_norm["conference"])
            and self.contains_any_term(desc_norm, ["conference", "conférence", "debat", "débat", "table ronde"])
        ):
            return "conference"

        if self.contains_any_term(desc_norm, self._event_type_terms_norm["atelier"]):
            return "atelier"

        if self.contains_any_term(desc_norm, self._event_type_terms_norm["conte"]):
            return "conte"

        if self.contains_any_term(desc_norm, self._event_type_terms_norm["festival"]):
            return "festival"

        if self.contains_any_term(desc_norm, self._event_type_terms_norm["marche"]):
            return "marche"

        if self.contains_any_term(desc_norm, self._event_type_terms_norm["spectacle"]):
            return "spectacle"

        if self.contains_any_term(desc_norm, self._event_type_terms_norm["lecture"]):
            return "lecture"

        if event_type_norm in {
            "exposition",
            "concert",
            "visite",
            "conference",
            "atelier",
            "conte",
            "projection",
            "festival",
            "marche",
            "spectacle",
            "lecture",
        }:
            return event_type_norm

        return ""

    def infer_canonical_music_genre(
        self,
        title: str,
        description: str,
        event_type: str,
    ) -> str:
        """
        Déduit un genre musical canonique simple lorsqu'il est identifiable.

        Logique très prudente :

        - priorité au titre
        - puis à la description
        - aucun genre n'est inféré sur indice faible
        - sinon chaîne vide

        Parameters
        ----------
        title : str
            Titre documentaire.
        description : str
            Description consolidée.
        event_type : str
            Type brut.

        Returns
        -------
        str
            Genre musical canonique ou chaîne vide.
        """
        # Déduit un genre musical stable,
        # mais seulement lorsqu'il est vraiment identifiable.
        title_norm = self.normalize_text(title)
        desc_norm = self.normalize_text(description)
        _ = self.normalize_text(event_type)

        for canonical_genre, variants in self._music_genre_terms_norm.items():
            if self.contains_any_term(title_norm, variants):
                return canonical_genre

        for canonical_genre, variants in self._music_genre_terms_norm.items():
            if self.contains_any_term(desc_norm, variants):
                return canonical_genre

        return ""

    # ------------------------------------------------------------------
    # Helpers plus structurés
    # ------------------------------------------------------------------

    def extract_question_signals(self, question: str) -> dict[str, Any]:
        """
        Extrait quelques signaux lexicaux simples depuis une question.

        Ce helper sert de base commune à `filter_service`
        et `retrieval_service`.

        Principe important :
        les filtres ne doivent s'activer que si la contrainte
        est explicitement présente dans la question.

        Signaux renvoyés :

        - texte normalisé
        - mots-clés
        - type d'événement explicite
        - genre musical explicite
        - filtre tarifaire explicite
        - signaux de public explicites
        - indicateur de contrainte culturelle explicite
        - indicateur de requête large d'activité
        - signaux négatifs métier simples

        Parameters
        ----------
        question : str
            Question utilisateur brute.

        Returns
        -------
        dict[str, Any]
            Dictionnaire structuré de signaux.
        """
        # Fonction de synthèse pour l'analyse lexicale des questions.
        # Elle centralise les signaux utiles au filtrage et au ranking.
        question_norm = self.normalize_text(question)

        return {
            "question_norm": question_norm,
            "keywords": self.extract_keywords(question_norm),
            "event_type": self.extract_event_type(question_norm),
            "music_genre": self.extract_music_genre(question_norm),
            "price_filter": self.extract_explicit_price_filter(question_norm),
            "audience_terms": self.extract_audience_terms(question_norm),
            "is_cultural_query": self.is_cultural_query(question_norm),
            "is_broad_activity_query": self.is_broad_activity_query(question_norm),
            "has_market_signal": self.has_market_signal(question_norm),
            "has_repair_signal": self.has_repair_signal(question_norm),
            "has_religious_signal": self.has_religious_signal(question_norm),
            "has_business_signal": self.has_business_signal(question_norm),
        }

    def build_document_lexical_profile(
        self,
        *,
        title: str,
        description: str,
        long_description: str,
        event_type: str,
    ) -> dict[str, Any]:
        """
        Construit un petit profil lexical de document réutilisable.

        Ce helper est destiné à réduire la duplication dans
        `document_service`.

        Parameters
        ----------
        title : str
            Titre du document.
        description : str
            Description courte ou standard.
        long_description : str
            Description longue éventuelle.
        event_type : str
            Type brut.

        Returns
        -------
        dict[str, Any]
            Profil lexical structuré du document.
        """
        # Version document de la logique de synthèse lexicale.
        # Elle consolide plusieurs signaux réutilisables
        # sans disperser la logique dans document_service.
        full_description = self.join_texts(description, long_description)
        support_text = self.join_texts(title, full_description, event_type)

        keywords_title = self.extract_title_keywords(title)
        derived_event_terms = self.derive_event_terms(
            title=title,
            description=full_description,
            event_type=event_type,
        )
        derived_music_terms = self.derive_music_terms(
            title=title,
            description=full_description,
            event_type=event_type,
        )
        audience_terms = self.extract_audience_terms(
            self.join_texts(title, full_description)
        )
        canonical_event_type = self.infer_canonical_event_type(
            title=title,
            description=full_description,
            event_type=event_type,
        )
        music_genre = self.infer_canonical_music_genre(
            title=title,
            description=full_description,
            event_type=event_type,
        )

        return {
            "keywords_title": keywords_title,
            "derived_event_terms": derived_event_terms,
            "derived_music_terms": derived_music_terms,
            "audience_terms": audience_terms,
            "canonical_event_type": canonical_event_type,
            "music_genre": music_genre,
            "has_market_signal": self.has_market_signal(support_text),
            "has_repair_signal": self.has_repair_signal(support_text),
            "has_religious_signal": self.has_religious_signal(support_text),
            "has_business_signal": self.has_business_signal(support_text),
        }