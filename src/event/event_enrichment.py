"""Enrichissement des événements par Mistral : catégorie unifiée, publics, résumé.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), première
décision « LLM » de l'agent de collecte. Chaque source a ses catégories
(« Spectacle vivant », « Théâtre et Humour », « Concert », « Jeu de rôle »...)
et les publics « famille » / « soirée » étaient devinés par mots-clés : le
RAG du bloc 2 a besoin d'un vocabulaire commun et d'un court résumé.

Pour chaque **nouvel** événement (ou dont le texte a changé), Mistral rend :

- `categorie` : une valeur de `CATEGORIES` (liste fermée, validée) ;
- `publics` : parmi `PUBLICS` (« famille », « soiree », « ados », « seniors ») ;
- `resume` : une phrase de 160 caractères au plus, tirée du texte ;
- `est_une_sortie` : faux pour une offre d'emploi, une collecte, une
  réunion administrative... (signalé, jamais supprimé automatiquement).

La tranche de prix (`price_band`) est calculée sans LLM, à partir des prix
déjà extraits : inutile de payer un modèle pour une comparaison.

Garde-fous : sortie JSON validée par Pydantic, catégorie et publics hors
liste rejetés, un lot illisible est ignoré (réessayé la nuit suivante) ;
l'enrichissement n'efface jamais les données des sources.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from src.logger import get_logger

logger = get_logger(__name__)

# À changer quand la consigne change : les événements seront ré-enrichis.
ENRICHMENT_VERSION = "2026-10-15.1"   # 15/10 : définitions des catégories, résumés contrôlés
DEFAULT_MODEL = "mistral-small-latest"

CATEGORIES = (
    "concert", "spectacle", "theatre", "danse", "cinema", "exposition", "atelier", "conference",
    "visite", "festival", "marche", "sport", "jeux", "soiree", "lecture", "nature", "fete", "autre",
)
PUBLICS = ("famille", "soiree", "ados", "seniors")

ENRICHMENT_PROMPT = f"""Tu ranges des événements d'un agenda de sorties (Montpellier, bassin de Thau).
Pour chaque événement fourni (identifiant, titre, catégorie d'origine, lieu, âges, texte), rends :
- "categorie" : UNE valeur parmi {", ".join(CATEGORIES)}, d'après ce qu'on y FAIT :
  atelier = on pratique soi-même (stage, initiation, atelier créatif, cours) ;
  jeux = jeux de société, échecs, jeux vidéo, escape game, quiz ;
  visite = visite guidée ou commentée d'un lieu ; exposition = œuvres à voir ;
  conference = conférence, rencontre, débat, table ronde ; lecture = lecture, conte, club de lecture ;
  spectacle = cirque, magie, humour, spectacle pluridisciplinaire ; theatre = pièce de théâtre ;
  concert = musique jouée en direct ; soiree = fête de nuit, bar, club ; nature = balade, sortie nature ;
  fete = fête locale, carnaval, Halloween ; festival = festival sur plusieurs jours ou lieux ;
- "publics" : liste (éventuellement vide) parmi {", ".join(PUBLICS)} ;
  famille = adapté aux enfants avec leurs parents ; soiree = sortie du soir entre adultes
  (concert, bar, club, spectacle tardif) ; ados = 12-17 ans ; seniors = public âgé ;
- "resume" : une phrase de 160 caractères au plus, fidèle au texte. N'y mets AUCUN nom
  propre (équipe, artiste, lieu, ville, œuvre), date, heure ni prix qui ne figure pas
  mot pour mot dans le titre ou le texte ; dans le doute, reste général ;
- "est_une_sortie" : false seulement si ce n'est pas une sortie de loisir (offre
  d'emploi, collecte de sang, réunion administrative, aide aux devoirs, fermeture d'un lieu...).

Réponds uniquement avec un objet JSON :
{{"evenements": [{{"id": "...", "categorie": "...", "publics": [], "resume": "...", "est_une_sortie": true}}]}}"""

# Variantes fréquentes dans les réponses du modèle, ramenées à la liste fermée.
CATEGORY_SYNONYMS = {
    "expo": "exposition", "visite guidee": "visite", "marches": "marche", "jeu": "jeux",
    "film": "cinema", "musique": "concert", "balade": "nature", "randonnee": "nature",
}
PUBLIC_SYNONYMS = {
    "enfants": "famille", "enfant": "famille", "familles": "famille", "adolescents": "ados",
    "jeunes": "ados", "senior": "seniors", "personnes agees": "seniors",
}


def plain(value: str) -> str:
    """Minuscules, sans accents ni espaces superflus : « Théâtre » -> « theatre »."""

    decomposed = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).split())


PRICE_BANDS = ("gratuit", "moins_10", "10_25", "plus_25", "inconnu")
MAX_TEXT_CHARS = 800


class Enrichment(BaseModel):
    """Ce que Mistral doit rendre pour un événement (validé par Pydantic)."""

    id: str
    categorie: str
    publics: list[str] = []
    resume: str = ""
    est_une_sortie: bool = True

    @field_validator("categorie")
    @classmethod
    def known_category(cls, value: str) -> str:
        # « Théâtre », « marché », « fête » : le modèle met souvent les accents
        # que la liste n'a pas (collecte du 14/10 : plus de la moitié des lots perdus).
        value = plain(value)
        value = CATEGORY_SYNONYMS.get(value, value)
        if value not in CATEGORIES:
            # Une catégorie hors liste (« evenement », « musique classique ») ne
            # fait plus perdre tout l'enrichissement (publics, résumé) : elle
            # devient « autre », et la nuit le signale dans le journal.
            # Collectes du 15/10 : 8 à 10 événements refusés chaque nuit.
            logger.info("Catégorie hors liste rangée en « autre » : %s.", value)
            return "autre"
        return value

    @field_validator("publics")
    @classmethod
    def known_publics(cls, values: list[str]) -> list[str]:
        # Un public hors liste est ignoré (pas tout l'événement) : « enfants »
        # devient « famille », « soirée » devient « soiree ».
        cleaned = {PUBLIC_SYNONYMS.get(plain(value), plain(value)) for value in values}
        return sorted(value for value in cleaned if value in PUBLICS)

    @field_validator("resume")
    @classmethod
    def short_summary(cls, value: str) -> str:
        return " ".join(value.split())[:200]


def price_band(is_free: Any, price_min: Any, price_max: Any) -> str:
    """Tranche de prix, sans LLM : gratuit, moins de 10 €, 10 à 25 €, plus de 25 €."""

    if is_free is True:
        return "gratuit"
    price = price_min if price_min is not None else price_max
    if price is None:
        return "inconnu"
    price = float(price)
    if price == 0:
        return "gratuit"
    if price < 10:
        return "moins_10"
    return "10_25" if price <= 25 else "plus_25"


def short_id(index: int) -> str:
    """Identifiant court d'un événement dans un lot (« e1 », « e2 »...)."""

    return f"e{index + 1}"


def build_batch_text(rows: Sequence[Mapping[str, Any]]) -> str:
    """Texte envoyé à Mistral pour un lot d'événements.

    Les événements y sont numérotés e1, e2... : un identifiant long
    (adresse DATAtourisme, uid OpenAgenda) est souvent mal recopié par le modèle.
    """

    blocks = []
    for index, row in enumerate(rows):
        ages = ""
        if row.get("age_min") is not None or row.get("age_max") is not None:
            ages = f"Âges : {row.get('age_min') or '?'}-{row.get('age_max') or '?'}\n"
        blocks.append(
            f"id: {short_id(index)}\nTitre : {row.get('title', '')}\n"
            f"Catégorie d'origine : {row.get('category') or '-'}\n"
            f"Lieu : {row.get('venue_name') or '-'}, {row.get('city') or '-'}\n{ages}"
            f"Texte : {str(row.get('description') or '')[:MAX_TEXT_CHARS]}"
        )
    return "\n\n".join(blocks)


def parse_enrichments(answer: str, expected_ids: Sequence[str]) -> tuple[dict[str, Enrichment], int]:
    """Enrichissements valides d'une réponse, par identifiant ; et nombre de rejets."""

    match = re.search(r"\{.*\}", answer or "", re.DOTALL)
    if not match:
        return {}, len(expected_ids)
    try:
        items = json.loads(match.group(0)).get("evenements", [])
    except (json.JSONDecodeError, AttributeError):
        return {}, len(expected_ids)
    valid: dict[str, Enrichment] = {}
    for item in items if isinstance(items, list) else []:
        try:
            enrichment = Enrichment.model_validate(item)
        except ValidationError as error:
            # Le motif est journalisé : un refus qui revient chaque nuit se corrige à la source.
            reasons = "; ".join(f"{'.'.join(map(str, e['loc']))} : {e['msg']}" for e in error.errors())
            logger.warning("Enrichissement refusé (%s) : %s.", item.get("id", "?") if isinstance(item, dict) else "?",
                           reasons)
            continue
        if enrichment.id in expected_ids:
            valid[enrichment.id] = enrichment
    return valid, len(expected_ids) - len(valid)


def call_mistral_json(system_prompt: str, text: str, model: str) -> tuple[str, dict[str, int]]:
    """Appel Mistral en mode JSON. Renvoie (réponse, tokens consommés)."""

    from langchain_mistralai import ChatMistralAI

    llm = ChatMistralAI(model=model, temperature=0).bind(response_format={"type": "json_object"})
    message = llm.invoke([("system", system_prompt), ("human", text)])
    usage = getattr(message, "usage_metadata", None) or {}
    return str(message.content), {"input": int(usage.get("input_tokens", 0)),
                                  "output": int(usage.get("output_tokens", 0))}


def enrichment_model() -> str:
    """Modèle d'enrichissement (`ENRICHMENT_MODEL` dans .env)."""

    return os.getenv("ENRICHMENT_MODEL", DEFAULT_MODEL)


# ----------------------------------------------------------------------------
# Contrôles après Mistral (15/10/2026)
# ----------------------------------------------------------------------------

# Catégorie sûre d'après le titre : le juge de qualité relevait 25 % de
# catégories discutables (« PAUSE JEUX : Les échecs » en atelier, une visite
# guidée en exposition). Quand le titre le dit clairement, il l'emporte.
# L'ordre compte : la première règle qui correspond gagne.
TITLE_CATEGORY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(pattern)) for category, pattern in (
        ("visite", r"\bvisites? (guidees?|commentees?|decouverte|theatralisees?)\b"),
        ("jeux", r"\b(pause jeux|soiree jeux|jeux? de societe|echecs|ludotheque|escape game|jeu de role|blind test)\b"),
        ("atelier", r"\b(atelier|ateliers|stage|stages|initiation)\b"),
        ("conference", r"\b(conference|conferences|table ronde|colloque)\b"),
        ("cinema", r"\b(cine|cinema|projection|projections)\b"),
        ("concert", r"\b(concert|concerts|recital)\b"),
        ("exposition", r"\b(exposition|expositions|expo|vernissage)\b"),
        ("lecture", r"\b(heure du conte|lecture|lectures|contes)\b"),
        ("marche", r"\b(brocante|vide greniers?|marche aux puces|marches? (traditionnel|nocturne|de noel|paysan|artisanal|gourmand))\b"),
        ("theatre", r"\btheatre\b"),
    )
)

# Noms de la région que le résumé peut citer sans qu'ils soient dans le texte.
REGION_NAMES = frozenset({"montpellier", "sete", "thau", "herault", "occitanie", "france", "mediterranee"})
SENTENCE_START = re.compile(r"(^|[.!?…]\s+|[«\"(]\s*)$")


def category_from_title(title: str) -> str | None:
    """Catégorie évidente d'après le titre, ou None."""

    text = re.sub(r"[^a-z0-9]+", " ", plain(title))
    for category, pattern in TITLE_CATEGORY_RULES:
        if pattern.search(text):
            return category
    return None


def unsupported_names(summary: str, row: Mapping[str, Any]) -> list[str]:
    """Noms propres et nombres du résumé absents du titre, du lieu et du texte.

    Cas réel (15/10/2026) : « MHSC vs Nantes » alors que la source disait Metz.
    Un mot à majuscule en début de phrase n'est pas compté (« Concert de… »).
    """

    source = " " + re.sub(r"[^a-z0-9]+", " ", plain(" ".join(str(row.get(key) or "") for key in (
        "title", "category", "venue_name", "city", "description")))) + " "
    found = []
    for match in re.finditer(r"[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ'’-]+|\d+", summary):
        word = match.group(0)
        if not word[0].isdigit() and SENTENCE_START.search(summary[:match.start()]):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", plain(word)).strip()
        if not key or key in REGION_NAMES:
            continue
        if f" {key} " not in source and not all(f" {part} " in source for part in key.split()):
            found.append(word)
    return found


def check_enrichment(enrichment: Enrichment, row: Mapping[str, Any]) -> tuple[Enrichment, list[str]]:
    """Catégorie d'après le titre quand elle est évidente ; résumé écarté s'il
    cite un nom absent de la source. Renvoie (enrichissement corrigé, noms inventés)."""

    updates: dict[str, Any] = {}
    category = category_from_title(str(row.get("title") or ""))
    if category and category != enrichment.categorie:
        updates["categorie"] = category
    invented = unsupported_names(enrichment.resume, row) if enrichment.resume else []
    if invented:
        updates["resume"] = ""
    return (enrichment.model_copy(update=updates) if updates else enrichment), invented


def enrich_with_mistral(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Enrichment], dict[str, dict[str, int]]]:
    """Enrichit un lot. Renvoie ({event_id: Enrichment}, consommation {modèle: tokens})."""

    model = enrichment_model()
    answer, usage = call_mistral_json(ENRICHMENT_PROMPT, build_batch_text(rows), model)
    by_short_id = {short_id(index): str(row["event_id"]) for index, row in enumerate(rows)}
    valid, _ = parse_enrichments(answer, list(by_short_id))
    missing = [str(row.get("title") or row["event_id"]) for index, row in enumerate(rows)
               if short_id(index) not in valid]
    if missing:
        logger.warning("Enrichissement absent ou refusé pour %s événement(s) : %s.", len(missing), missing[:10])
    checked: dict[str, Enrichment] = {}
    for index, row in enumerate(rows):
        key = short_id(index)
        if key in valid:
            checked[key], invented = check_enrichment(valid[key], row)
            if invented:
                logger.warning("Résumé écarté (noms absents de la source : %s) : %s.", ", ".join(invented),
                               row.get("title"))
    return {by_short_id[key]: value for key, value in checked.items()}, {model: usage}
