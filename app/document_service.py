"""
Service documentaire pour le pipeline RAG OpenAgenda.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/document_service.py` (commit 18f7ce1).

Ce module est responsable de :

- interroger l'API OpenAgenda
- rechercher les agendas officiels pertinents pour une zone
- récupérer les événements correspondant à une zone et à un scope
- transformer chaque événement en `Document` LangChain
- construire des métadonnées simples, cohérentes et fiables

Les documents produits sont utilisés pour :

- l'indexation vectorielle dans FAISS
- le filtrage structuré dans `filter_service`
- le ranking métier dans `retrieval_service`
- la génération de réponse dans `rag_service`

Ce module ne contient aucune logique de ranking ni de génération.

Philosophie documentaire
------------------------
Deux représentations textuelles coexistent volontairement :

- `page_content` :
  texte court, structuré, lisible, destiné au contexte LLM
- `search_text` :
  texte plus riche, optimisé pour l'embedding, le matching lexical
  léger et le ranking métier

Le contrôle de longueur est centralisé ici afin d'éviter de polluer
`rag_service` avec des considérations de préparation documentaire.

Principes de conception
-----------------------
La construction documentaire suit quelques règles simples :

- mieux vaut une métadonnée vide qu'une métadonnée fausse
- la préparation du texte utile doit être centralisée ici
- les signaux implicites doivent rester explicables
- les contenus trop longs doivent être plafonnés
- les faux positifs culturels doivent être limités autant que possible
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import requests
from langchain_core.documents import Document

from app.config import OPENAGENDA_API_KEY
from app.lexical_service import LexicalService


LEX = LexicalService()


# -------------------------------------------------------------------------
# Paramètres documentaires
# -------------------------------------------------------------------------
#Limite la taille de la description courte destinée au page_content déstiné au LLM.
MAX_PAGE_DESCRIPTION_CHARS = 320
#Limite la description utilisée dans search_text déstiné à l'embedding.
MAX_SEARCH_DESCRIPTION_CHARS = 420
#Limite la version longue de la description dans search_text.
MAX_SEARCH_LONG_DESCRIPTION_CHARS = 520
#Longueur maximale du texte final search_text.
MAX_SEARCH_TEXT_CHARS = 900

# Seuil minimal pour considérer qu'une longue description est réellement utile
MIN_LONG_DESCRIPTION_CHARS = 120


# -------------------------------------------------------------------------
# Helpers généraux
# -------------------------------------------------------------------------

def _nested_get(data: dict[str, Any], *keys: str) -> Any:
    """
    Accède en sécurité à des clés imbriquées d'un dictionnaire.
    """
    # Parcourt progressivement les clés demandées.
    # Si à un moment la structure n'est plus un dictionnaire,
    # on renvoie None au lieu de lever une erreur.
    current: Any = data

    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current


def _first_non_empty(*values: object) -> str:
    """
    Retourne la première valeur textuelle non vide.
    """
    # Sert de fallback générique :
    # on essaie plusieurs valeurs possibles
    # et on garde la première réellement exploitable.
    for value in values:
        text = LEX.extract_text(value)
        if text:
            return text

    return ""


def _extract_event_field(event: dict[str, Any], *paths: tuple[str, ...]) -> str:
    """
    Extrait le premier champ non vide parmi plusieurs chemins possibles.
    """
    # Centralise la logique de fallback sur les champs OpenAgenda.
    # Exemple :
    # - ("title",)
    # - ("title", "fr")
    # Cela évite de répéter des if/else partout dans le code.
    candidates: list[object] = []

    for path in paths:
        if len(path) == 1:
            candidates.append(event.get(path[0]))
        else:
            candidates.append(_nested_get(event, *path))

    return _first_non_empty(*candidates)


def _normalize_whitespace(value: object) -> str:
    """
    Normalise les espaces et les retours ligne d'un texte.
    """
    # Nettoie un texte pour obtenir une version homogène :
    # pas de doubles espaces, pas de retours ligne parasites,
    # pas de tabulations inutiles.
    text = LEX.extract_text(value)
    if not text:
        return ""

    return " ".join(text.split())


def _truncate_text(value: object, max_chars: int) -> str:
    """
    Tronque un texte proprement sans couper brutalement au milieu d'un mot.
    """
    # Utilisé pour plafonner les descriptions
    # afin d'éviter des textes trop longs dans le contexte LLM
    # ou dans le texte d'embedding.
    text = _normalize_whitespace(value)
    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    # Si possible, on coupe au dernier espace
    # pour éviter un rendu visuellement sale.
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]

    return truncated.rstrip(" ,;:.-") + "..."


def _join_non_empty(parts: list[str], sep: str = " ") -> str:
    """
    Concatène uniquement les éléments non vides.
    """
    # Assemble proprement une liste de fragments textuels
    # sans injecter d'éléments vides dans le résultat final.
    return sep.join(part for part in parts if part)


# -------------------------------------------------------------------------
# Dates et durée
# -------------------------------------------------------------------------

def _parse_iso_date(value: object) -> datetime | None:
    """
    Parse une date ISO.
    """
    # Tente d'abord un parsing ISO complet
    # puis un fallback plus simple sur YYYY-MM-DD.
    raw = LEX.extract_text(value)
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass

    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _normalize_iso_day(value: object) -> str:
    """
    Ramène une date potentielle au format `YYYY-MM-DD`.
    """
    # Uniformise les dates pour simplifier les comparaisons,
    # le filtrage, et le stockage dans les métadonnées.
    parsed = _parse_iso_date(value)
    if parsed is None:
        return ""

    return parsed.date().isoformat()


def _build_duration_label(first_date: str, last_date: str) -> str:
    """
    Construit un libellé métier simple décrivant la durée de l'événement.
    """
    # Produit un libellé textuel simple,
    # utile à la fois pour l'interprétation métier
    # et comme signal textuel dans le search_text.
    dt_start = _parse_iso_date(first_date)
    dt_end = _parse_iso_date(last_date)

    if not dt_start and not dt_end:
        return "durée non précisée"

    if dt_start and not dt_end:
        return "événement sur une journée"

    if not dt_start and dt_end:
        return "durée non précisée"

    if dt_start is None or dt_end is None:
        return "durée non précisée"

    if dt_start.date() == dt_end.date():
        return "événement sur une journée"

    return "événement sur plusieurs jours"


def _compute_duration_days(first_date: str, last_date: str) -> int | None:
    """
    Calcule la durée de l'événement en jours.
    """
    # Version structurée de la durée.
    # Ce signal peut être utilisé plus tard
    # dans les filtres, le ranking ou l'analyse.
    dt_start = _parse_iso_date(first_date)
    dt_end = _parse_iso_date(last_date)

    if not dt_start and not dt_end:
        return None

    if dt_start and not dt_end:
        return 1

    if not dt_start and dt_end:
        return None

    if dt_start is None or dt_end is None:
        return None

    return max(1, (dt_end.date() - dt_start.date()).days + 1)


# -------------------------------------------------------------------------
# Qualité documentaire et enrichissement
# -------------------------------------------------------------------------

def _compute_content_quality(
    *,
    title: str,
    description: str,
    long_description: str,
    location_name: str,
    city: str,
    region: str,
    first_date: str,
    last_date: str,
    source_url: str,
    price_info: str,
    event_type: str,
    music_genre: str,
    canonical_event_type: str,
) -> int:
    """
    Calcule un score simple de qualité documentaire.
    """
    # Score additif volontairement simple et explicable.
    # L'idée n'est pas de produire une vérité absolue,
    # mais un indicateur pratique de richesse documentaire.
    score = 0
    score += int(bool(title))
    score += int(bool(description))
    score += int(len(long_description) >= MIN_LONG_DESCRIPTION_CHARS)
    score += int(bool(location_name))
    score += int(bool(city))
    score += int(bool(region))
    score += int(bool(first_date))
    score += int(bool(last_date))
    score += int(bool(source_url))
    score += int(price_info != "inconnu")
    score += int(bool(event_type))
    score += int(bool(canonical_event_type))
    score += int(bool(music_genre))

    return score


def _build_cultural_tags(
    *,
    canonical_event_type: str,
    derived_terms: list[str],
) -> list[str]:
    """
    Déduit quelques tags culturels simples pour enrichir le `search_text`.
    """
    # Ajoute des mots utiles à la recherche sémantique
    # et au matching lexical léger.
    # Le but est d'aider la récupération sans surcharger le document.
    tags: set[str] = set()

    if canonical_event_type in {
        "exposition",
        "concert",
        "festival",
        "projection",
        "spectacle",
        "conte",
        "lecture",
    }:
        tags.update(["culture", "culturel", "evenement culturel"])

    if any(term in derived_terms for term in ["exposition", "expo", "vernissage"]):
        tags.update(["art", "artistique", "exposition"])

    if any(term in derived_terms for term in ["concert"]):
        tags.update(["musique", "musical", "concert"])

    if any(term in derived_terms for term in ["projection", "film", "cinema"]):
        tags.update(["cinema", "projection"])

    if any(term in derived_terms for term in ["spectacle", "theatre"]):
        tags.update(["spectacle", "scene"])

    if any(term in derived_terms for term in ["conte", "lecture"]):
        tags.update(["lecture", "oralite"])

    return sorted(tags)


def _build_implicit_relevance_flags(
    *,
    title: str,
    description: str,
    long_description: str,
    canonical_event_type: str,
    derived_terms: list[str],
    lexical_profile: dict[str, Any],
) -> dict[str, bool]:
    """
    Déduit quelques drapeaux implicites de pertinence métier.
    """
    # Ces drapeaux servent à enrichir le document avec des signaux métier
    # simples, lisibles et explicables.
    # Ils sont utiles pour limiter certains faux positifs.
    support_text = LEX.normalize_text(
        f"{title} {description} {long_description} {' '.join(derived_terms)} {canonical_event_type}"
    )

    strong_cultural_types = {
        "exposition",
        "concert",
        "festival",
        "projection",
        "spectacle",
        "conte",
        "lecture",
    }
    weak_cultural_types = {"atelier", "conference", "visite"}

    return {
        "is_strong_cultural_candidate": canonical_event_type in strong_cultural_types,
        "is_weak_cultural_candidate": canonical_event_type in weak_cultural_types,
        "has_market_signal": bool(lexical_profile.get("has_market_signal", False)),
        "has_repair_signal": bool(lexical_profile.get("has_repair_signal", False)),
        "has_religious_signal": bool(lexical_profile.get("has_religious_signal", False)),
        "has_business_signal": bool(lexical_profile.get("has_business_signal", False)),
        "has_strong_cultural_terms": LEX.contains_any_term(
            support_text,
            LEX.STRONG_CULTURAL_TERMS,
        ),
    }


def _build_page_content(
    *,
    title: str,
    description_short: str,
    location_name: str,
    city: str,
    region: str,
    first_date: str,
    last_date: str,
    event_type_label: str,
    canonical_music_genre: str,
    price_info: str,
    source_url: str,
) -> str:
    """
    Construit la fiche courte destinée au LLM.
    """
    # Ce texte est pensé pour être lu directement par le LLM.
    # Il doit rester compact, lisible et structuré.
    page_lines = [
        f"Titre : {title}",
        f"Description : {description_short}",
        f"Lieu : {location_name}",
        f"Ville : {city}",
        f"Région : {region}",
        f"Date de début : {first_date}",
        f"Date de fin : {last_date}",
        f"Type : {event_type_label}",
    ]

    if canonical_music_genre:
        page_lines.append(f"Genre musical : {canonical_music_genre}")

    page_lines.extend(
        [
            f"Tarification : {price_info}",
            f"URL : {source_url}",
        ]
    )

    return "\n".join(
        line
        for line in page_lines
        if line.split(" : ", 1)[-1].strip()
    )


def _build_search_text(
    *,
    title: str,
    description_search: str,
    long_description_search: str,
    location_name: str,
    city: str,
    region: str,
    event_type: str,
    canonical_event_type: str,
    canonical_music_genre: str,
    audience_terms: list[str],
    first_date: str,
    last_date: str,
    price_info: str,
    access_label: str,
    duration_label: str,
    title_keywords: list[str],
    derived_terms: list[str],
    derived_music_terms: list[str],
    cultural_tags: list[str],
    max_chars: int = MAX_SEARCH_TEXT_CHARS,
) -> str:
    """
    Construit le texte riche destiné à l'embedding, au matching lexical léger
    et au ranking métier.
    """
    # Ce texte est plus dense que page_content.
    # Il embarque des signaux utiles pour la récupération :
    # sémantique, mots-clés, tags culturels, prix, durée, audience, etc.
    parts = [
        title,
        canonical_event_type,
        canonical_music_genre,
        event_type,
        location_name,
        city,
        region,
        first_date,
        last_date,
        description_search,
        long_description_search,
        " ".join(title_keywords),
        " ".join(derived_terms),
        " ".join(derived_music_terms),
        " ".join(cultural_tags),
        " ".join(audience_terms),
        price_info,
        access_label,
        duration_label,
    ]

    text = LEX.clean_text(_join_non_empty(parts, sep=" "))

    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]

    return truncated.rstrip(" ,;:.-") + "..."


# -------------------------------------------------------------------------
# Cohérence zone / scope
# -------------------------------------------------------------------------

def _matches_zone_scope(doc: Document, zone: str, scope: str) -> bool:
    """
    Vérifie qu'un document correspond réellement à la zone demandée.

    Pour le scope `city`, la ville documentaire doit correspondre
    à la zone demandée une fois normalisée.

    La logique est légèrement souple pour tolérer certaines variantes
    documentaires simples.
    """
    # Même si OpenAgenda filtre déjà,
    # on ajoute ici une vérification locale de cohérence géographique.
    # Cela limite certains faux positifs.
    zone_norm = LEX.normalize_text(zone)
    md = doc.metadata or {}

    if scope == "city":
        city_norm = LEX.normalize_text(md.get("city", ""))
        if not city_norm:
            return False

        return (
            city_norm == zone_norm
            or zone_norm in city_norm
            or city_norm in zone_norm
        )

    return True


# -------------------------------------------------------------------------
# API OpenAgenda
# -------------------------------------------------------------------------

def get_default_date_window(
    days_back: int = 365,
    days_forward: int = 365,
) -> tuple[str, str]:
    """
    Calcule la fenêtre temporelle utilisée pour récupérer les événements.
    """
    # Définit une fenêtre raisonnable autour de la date du jour
    # pour ne pas interroger des événements trop anciens
    # ou trop éloignés dans le futur.
    today = date.today()
    date_from = (today - timedelta(days=days_back)).isoformat()
    date_to = (today + timedelta(days=days_forward)).isoformat()
    return date_from, date_to


def search_agendas_for_zone(zone: str, size: int = 30) -> list[dict[str, Any]]:
    """
    Recherche les agendas OpenAgenda officiels susceptibles de concerner une zone.
    """
    # Première étape de collecte :
    # on cherche les agendas pertinents avant de récupérer leurs événements.
    if not OPENAGENDA_API_KEY:
        raise ValueError("OPENAGENDA_API_KEY manquante")

    url = "https://api.openagenda.com/v2/agendas"

    response = requests.get(
        url,
        headers={"key": OPENAGENDA_API_KEY},
        params={
            "search": zone,
            "size": size,
            "official": 1,
        },
        timeout=30,
    )
    response.raise_for_status()

    return response.json().get("agendas", [])


def fetch_openagenda_events(
    agenda_uid: str,
    zone: str,
    scope: str = "city",
    limit: int = 200,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """
    Récupère les événements d'un agenda OpenAgenda.
    """
    # Récupération paginée des événements.
    # On accumule les pages jusqu'à épuisement ou atteinte du plafond max_pages.
    if not OPENAGENDA_API_KEY:
        raise ValueError("OPENAGENDA_API_KEY manquante")

    date_from, date_to = get_default_date_window()

    events_all: list[dict[str, Any]] = []
    offset = 0

    for _ in range(max_pages):
        url = f"https://openagenda.com/agendas/{agenda_uid}/events.json"

        response = requests.get(
            url,
            params={
                "key": OPENAGENDA_API_KEY,
                "limit": limit,
                "offset": offset,
                "oaq[from]": date_from,
                "oaq[to]": date_to,
                "oaq[what]": zone,
                "oaq[scope]": scope,
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        events = data.get("events", [])

        if not events:
            break

        events_all.extend(events)
        offset += limit

    return events_all


# -------------------------------------------------------------------------
# Construction des documents
# -------------------------------------------------------------------------

def build_event_document(event: dict[str, Any]) -> Document:
    """
    Transforme un événement OpenAgenda en `Document` LangChain.
    """
    # Fonction centrale du module.
    # Elle extrait, nettoie, enrichit, structure
    # puis transforme un événement brut en Document prêt pour le pipeline.
    event_uid = _extract_event_field(event, ("uid",))
    agenda_uid = _extract_event_field(event, ("agenda", "uid"))

    title = _extract_event_field(event, ("title",), ("title", "fr"))
    description = _extract_event_field(event, ("description",), ("description", "fr"))
    long_description = _extract_event_field(
        event,
        ("longDescription",),
        ("longDescription", "fr"),
    )

    location_name = _extract_event_field(
        event,
        ("location_name",),
        ("location", "name"),
    )
    city = _extract_event_field(event, ("city",), ("location", "city"))
    region = _extract_event_field(event, ("region",), ("location", "region"))

    first_date = _normalize_iso_day(
        _extract_event_field(event, ("firstDate",), ("first_date",))
    )
    last_date = _normalize_iso_day(
        _extract_event_field(event, ("lastDate",), ("last_date",))
    )

    event_type = _extract_event_field(
        event,
        ("eventType",),
        ("type",),
        ("schema",),
    )

    source_url = _extract_event_field(
        event,
        ("canonicalUrl",),
        ("url",),
    )

    # Nettoyage homogène de tous les champs textuels
    # avant enrichissement.
    title = _normalize_whitespace(title)
    description = _normalize_whitespace(description)
    long_description = _normalize_whitespace(long_description)
    location_name = _normalize_whitespace(location_name)
    city = _normalize_whitespace(city)
    region = _normalize_whitespace(region)
    event_type = _normalize_whitespace(event_type)
    source_url = _normalize_whitespace(source_url)

    # Détection du prix / gratuité.
    # C'est un signal important pour le filtrage métier.
    price_info, is_free = LEX.extract_price_info(
        title,
        description,
        long_description,
        event.get("conditions"),
        event.get("pricing"),
        event.get("attendanceMode"),
    )

    # Construction du profil lexical documentaire.
    # C'est ici que l'on dérive le type canonique,
    # les mots-clés, l'audience, le genre musical, etc.
    lexical_profile = LEX.build_document_lexical_profile(
        title=title,
        description=description,
        long_description=long_description,
        event_type=event_type,
    )

    title_keywords = lexical_profile["keywords_title"]
    derived_terms = lexical_profile["derived_event_terms"]
    derived_music_terms = lexical_profile["derived_music_terms"]
    audience_terms = lexical_profile["audience_terms"]
    canonical_event_type = lexical_profile["canonical_event_type"]
    canonical_music_genre = lexical_profile["music_genre"]

    # ------------------------------------------------------------------
    # Garde-fou musical
    # ------------------------------------------------------------------

    # On évite ici les faux positifs musicaux.
    # Un genre musical détecté lexicalement n'est conservé
    # que s'il existe aussi un vrai contexte musical dans le document.
    if canonical_music_genre:
        support_text = LEX.normalize_text(
            f"{title} {description} {long_description} {event_type} {canonical_event_type}"
        )

        musical_terms = [
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
        ]

        musical_hits = LEX.count_matching_terms(support_text, musical_terms)

        has_strong_musical_context = (
            canonical_event_type in LEX.MUSICAL_EVENT_TYPES
            or musical_hits >= 2
        )

        if not has_strong_musical_context:
            canonical_music_genre = ""
            derived_music_terms = []

    # Calculs de durée et de libellés d'accès.
    duration_label = _build_duration_label(first_date, last_date)
    duration_days = _compute_duration_days(first_date, last_date)

    if is_free is True:
        access_label = "gratuit"
    elif is_free is False:
        access_label = "payant"
    else:
        access_label = "tarification inconnue"

    is_single_day = duration_days == 1 if duration_days is not None else None
    has_long_description = len(long_description) >= MIN_LONG_DESCRIPTION_CHARS

    # Tags culturels simples
    # pour enrichir le texte de recherche.
    cultural_tags = _build_cultural_tags(
        canonical_event_type=canonical_event_type,
        derived_terms=derived_terms,
    )

    # Signaux implicites utiles pour le ranking ou l'analyse.
    implicit_flags = _build_implicit_relevance_flags(
        title=title,
        description=description,
        long_description=long_description,
        canonical_event_type=canonical_event_type,
        derived_terms=derived_terms,
        lexical_profile=lexical_profile,
    )

    # Score global de richesse documentaire.
    content_quality = _compute_content_quality(
        title=title,
        description=description,
        long_description=long_description,
        location_name=location_name,
        city=city,
        region=region,
        first_date=first_date,
        last_date=last_date,
        source_url=source_url,
        price_info=price_info,
        event_type=event_type,
        music_genre=canonical_music_genre,
        canonical_event_type=canonical_event_type,
    )

    # Préparation de versions tronquées adaptées
    # à leurs usages respectifs.
    description_short = _truncate_text(description, MAX_PAGE_DESCRIPTION_CHARS)
    description_search = _truncate_text(description, MAX_SEARCH_DESCRIPTION_CHARS)
    long_description_search = _truncate_text(
        long_description,
        MAX_SEARCH_LONG_DESCRIPTION_CHARS,
    )

    event_type_label = canonical_event_type or event_type or "type non precise"

    # Texte court lisible pour le LLM.
    page_content = _build_page_content(
        title=title,
        description_short=description_short,
        location_name=location_name,
        city=city,
        region=region,
        first_date=first_date,
        last_date=last_date,
        event_type_label=event_type_label,
        canonical_music_genre=canonical_music_genre,
        price_info=price_info,
        source_url=source_url,
    )

    # Texte plus riche pour embedding / retrieval / ranking.
    search_text = _build_search_text(
        title=title,
        description_search=description_search,
        long_description_search=long_description_search,
        location_name=location_name,
        city=city,
        region=region,
        event_type=event_type,
        canonical_event_type=canonical_event_type,
        canonical_music_genre=canonical_music_genre,
        audience_terms=audience_terms,
        first_date=first_date,
        last_date=last_date,
        price_info=price_info,
        access_label=access_label,
        duration_label=duration_label,
        title_keywords=title_keywords,
        derived_terms=derived_terms,
        derived_music_terms=derived_music_terms,
        cultural_tags=cultural_tags,
    )

    # Métadonnées structurées consommées ensuite
    # par les autres services du pipeline.
    metadata = {
        "doc_id": f"openagenda_{event_uid}" if event_uid else "",
        "event_uid": event_uid,
        "agenda_uid": agenda_uid,
        "source": "openagenda",
        "title": title,
        "description": description,
        "long_description": long_description,
        "description_short": description_short,
        "description_search": description_search,
        "long_description_search": long_description_search,
        "location_name": location_name,
        "city": city,
        "region": region,
        "first_date": first_date,
        "last_date": last_date,
        "event_type": event_type,
        "canonical_event_type": canonical_event_type,
        "music_genre": canonical_music_genre,
        "source_url": source_url,
        "url": source_url,
        "price_info": price_info,
        "is_free": is_free,
        "keywords_title": title_keywords,
        "derived_event_terms": derived_terms,
        "derived_music_terms": derived_music_terms,
        "audience_terms": audience_terms,
        "cultural_tags": cultural_tags,
        "search_text": search_text,
        "title_norm": LEX.normalize_text(title),
        "location_name_norm": LEX.normalize_text(location_name),
        "city_norm": LEX.normalize_text(city),
        "region_norm": LEX.normalize_text(region),
        "event_type_norm": LEX.normalize_text(event_type),
        "canonical_event_type_norm": LEX.normalize_text(canonical_event_type),
        "music_genre_norm": LEX.normalize_text(canonical_music_genre),
        "duration_label": duration_label,
        "duration_days": duration_days,
        "is_single_day": is_single_day,
        "has_long_description": has_long_description,
        "content_quality": content_quality,
        **implicit_flags,
    }

    return Document(page_content=page_content, metadata=metadata)


def load_documents(
    zone: str = "Montpellier",
    scope: str = "city",
) -> list[Document]:
    """
    Charge les documents OpenAgenda prêts à être indexés.
    """
    # Fonction d'orchestration documentaire.
    # Elle cherche les agendas, récupère leurs événements,
    # construit les documents, déduplique,
    # puis filtre les incohérences géographiques résiduelles.
    agendas = search_agendas_for_zone(zone)

    documents: list[Document] = []
    seen_event_uids: set[str] = set()

    for agenda in agendas:
        agenda_uid = LEX.extract_text(agenda.get("uid"))
        if not agenda_uid:
            continue

        events = fetch_openagenda_events(
            agenda_uid=agenda_uid,
            zone=zone,
            scope=scope,
        )

        if not events:
            continue

        for event in events:
            event_uid = LEX.extract_text(event.get("uid"))

            # Déduplication par identifiant événement.
            if not event_uid or event_uid in seen_event_uids:
                continue

            doc = build_event_document(event)

            # Vérification finale locale sur la zone demandée.
            if not _matches_zone_scope(doc, zone=zone, scope=scope):
                continue

            documents.append(doc)
            seen_event_uids.add(event_uid)

    return documents