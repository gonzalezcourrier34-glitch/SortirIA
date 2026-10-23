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
    """Minuscules, sans accents ni espaces superflus : « Théâtre » -> « theatre ».

    Parameters
    ----------
    value : str
        Texte à simplifier (`None` est accepté et donne une chaîne vide).

    Returns
    -------
    str
        Le texte en minuscules, sans accents, espaces multiples réduits à un seul.
    """

    decomposed = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).split())


MAX_TEXT_CHARS = 800


class Enrichment(BaseModel):
    """Ce que Mistral doit rendre pour un événement (validé par Pydantic).

    Les validateurs ramènent la catégorie et les publics aux listes fermées
    `CATEGORIES` et `PUBLICS`, et raccourcissent le résumé.

    Attributes
    ----------
    id : str
        Identifiant court de l'événement dans le lot (« e1 », « e2 »...).
    categorie : str
        Catégorie unifiée, une valeur de `CATEGORIES` (« autre » si hors liste).
    publics : list[str]
        Publics visés, parmi `PUBLICS` ; vide par défaut.
    resume : str
        Résumé d'une phrase (200 caractères au plus après nettoyage) ; vide par défaut.
    est_une_sortie : bool
        Faux si ce n'est pas une sortie de loisir (offre d'emploi, collecte...).
        Vrai par défaut.
    """

    id: str
    categorie: str
    publics: list[str] = []
    resume: str = ""
    est_une_sortie: bool = True

    @field_validator("categorie")
    @classmethod
    def known_category(cls, value: str) -> str:
        """Ramener la catégorie rendue par le modèle à une valeur de `CATEGORIES`.

        Accents retirés, synonymes traduits ; une catégorie inconnue devient
        « autre » au lieu de faire rejeter tout l'enrichissement.

        Parameters
        ----------
        value : str
            Catégorie telle que rendue par Mistral.

        Returns
        -------
        str
            Une valeur de `CATEGORIES`.
        """
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
        """Ramener les publics rendus à la liste `PUBLICS`, sans doublons, triés.

        Parameters
        ----------
        values : list[str]
            Publics tels que rendus par Mistral.

        Returns
        -------
        list[str]
            Les publics reconnus (synonymes traduits), triés ; les autres sont ignorés.
        """
        # Un public hors liste est ignoré (pas tout l'événement) : « enfants »
        # devient « famille », « soirée » devient « soiree ».
        cleaned = {PUBLIC_SYNONYMS.get(plain(value), plain(value)) for value in values}
        return sorted(value for value in cleaned if value in PUBLICS)

    @field_validator("resume")
    @classmethod
    def short_summary(cls, value: str) -> str:
        """Réduire les espaces du résumé et le couper à 200 caractères.

        Parameters
        ----------
        value : str
            Résumé tel que rendu par Mistral.

        Returns
        -------
        str
            Le résumé nettoyé, 200 caractères au plus.
        """
        return " ".join(value.split())[:200]


def price_band(is_free: Any, price_min: Any, price_max: Any) -> str:
    """Tranche de prix, sans LLM : gratuit, moins de 10 €, 10 à 25 €, plus de 25 €.

    Le prix minimal sert de référence ; à défaut, le prix maximal.

    Parameters
    ----------
    is_free : Any
        Vrai (`True` strictement) si la source annonce l'événement gratuit.
    price_min : Any
        Prix le plus bas connu, ou None.
    price_max : Any
        Prix le plus haut connu, ou None.

    Returns
    -------
    str
        « gratuit », « moins_10 », « 10_25 », « plus_25 », ou « inconnu » si
        aucun prix n'est connu.
    """

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
    """Identifiant court d'un événement dans un lot (« e1 », « e2 »...).

    Parameters
    ----------
    index : int
        Position de l'événement dans le lot, à partir de 0.

    Returns
    -------
    str
        L'identifiant « e » suivi de la position comptée à partir de 1.
    """

    return f"e{index + 1}"


def build_batch_text(rows: Sequence[Mapping[str, Any]]) -> str:
    """Texte envoyé à Mistral pour un lot d'événements.

    Les événements y sont numérotés e1, e2... : un identifiant long
    (adresse DATAtourisme, uid OpenAgenda) est souvent mal recopié par le modèle.
    Le texte de chaque événement est coupé à `MAX_TEXT_CHARS` caractères.

    Parameters
    ----------
    rows : Sequence[Mapping[str, Any]]
        Les événements du lot (titre, catégorie, lieu, ville, âges, description).

    Returns
    -------
    str
        Un bloc de texte par événement, séparés par une ligne vide.
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
    """Enrichissements valides d'une réponse, par identifiant ; et nombre de rejets.

    Le premier objet JSON de la réponse est lu ; chaque élément est validé par
    `Enrichment`, et ceux dont l'identifiant n'était pas attendu sont ignorés.

    Parameters
    ----------
    answer : str
        Réponse brute de Mistral.
    expected_ids : Sequence[str]
        Identifiants courts envoyés dans le lot (« e1 », « e2 »...).

    Returns
    -------
    tuple[dict[str, Enrichment], int]
        Les enrichissements valides par identifiant court, et le nombre
        d'événements attendus sans enrichissement valide.
    """

    # Extraire l'objet JSON de la réponse ; illisible = tout le lot rejeté
    match = re.search(r"\{.*\}", answer or "", re.DOTALL)
    if not match:
        return {}, len(expected_ids)
    try:
        items = json.loads(match.group(0)).get("evenements", [])
    except (json.JSONDecodeError, AttributeError):
        return {}, len(expected_ids)
    # Valider chaque élément et ne garder que les identifiants attendus
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
    """Appel Mistral en mode JSON. Renvoie (réponse, tokens consommés).

    Température 0 et format de réponse `json_object` imposé.

    Parameters
    ----------
    system_prompt : str
        Consigne système (par exemple `ENRICHMENT_PROMPT`).
    text : str
        Message utilisateur : le texte du lot.
    model : str
        Nom du modèle Mistral.

    Returns
    -------
    tuple[str, dict[str, int]]
        Le contenu de la réponse, et les tokens consommés
        (`{"input": ..., "output": ...}`, 0 si inconnus).
    """

    from langchain_mistralai import ChatMistralAI

    llm = ChatMistralAI(model=model, temperature=0).bind(response_format={"type": "json_object"})
    message = llm.invoke([("system", system_prompt), ("human", text)])
    usage = getattr(message, "usage_metadata", None) or {}
    return str(message.content), {"input": int(usage.get("input_tokens", 0)),
                                  "output": int(usage.get("output_tokens", 0))}


def enrichment_model() -> str:
    """Modèle d'enrichissement (`ENRICHMENT_MODEL` dans .env).

    Returns
    -------
    str
        Le nom du modèle ; `DEFAULT_MODEL` si la variable n'est pas définie.
    """

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
# Mots courants écrits avec une majuscule, qui ne sont pas des noms propres
# (collecte du 15/10/2026 : 7 résumés écartés à tort pour « DJ » ou « DJs »).
COMMON_CAPITALIZED = frozenset({"dj", "djs", "vj", "mc", "bd", "ia", "vr"})
# Siècles en chiffres romains (« XIXe », « XXe siècle ») : pas des noms propres
# (15/10/2026 : « Castelnau, de la Révolution à la Première Guerre mondiale »
# a perdu son résumé pour « XIXe »).
ROMAN_CENTURY = re.compile(r"^[IVXL]+(e|er|ème|eme)$")
SENTENCE_START = re.compile(r"(^|[.!?…]\s+|[«\"(]\s*)$")


def category_from_title(title: str) -> str | None:
    """Catégorie évidente d'après le titre, ou None.

    Les règles de `TITLE_CATEGORY_RULES` sont essayées dans l'ordre ; la
    première qui correspond gagne.

    Parameters
    ----------
    title : str
        Titre de l'événement.

    Returns
    -------
    str or None
        La catégorie déduite du titre, ou None si le titre ne la dit pas clairement.
    """

    text = re.sub(r"[^a-z0-9]+", " ", plain(title))
    for category, pattern in TITLE_CATEGORY_RULES:
        if pattern.search(text):
            return category
    return None


def unsupported_names(summary: str, row: Mapping[str, Any]) -> list[str]:
    """Noms propres et nombres du résumé absents du titre, du lieu et du texte.

    Cas réel (15/10/2026) : « MHSC vs Nantes » alors que la source disait Metz.
    Un mot à majuscule en début de phrase n'est pas compté (« Concert de… »).
    Les noms de la région, les mots courants à majuscule (« DJ ») et les siècles
    en chiffres romains sont aussi tolérés.

    Parameters
    ----------
    summary : str
        Résumé rendu par Mistral.
    row : Mapping[str, Any]
        L'événement source (titre, catégorie, lieu, ville, description, âges, prix).

    Returns
    -------
    list[str]
        Les mots du résumé introuvables dans la source (vide si tout est justifié).
    """

    # Âges et prix : envoyés à Mistral avec le texte (« de 4 à 12 ans »), ils ne
    # sont pas inventés (15/10/2026 : « 4, 12 » refusés pour un atelier en famille).
    source = " " + re.sub(r"[^a-z0-9]+", " ", plain(" ".join(str(row.get(key) or "") for key in (
        "title", "category", "venue_name", "city", "description",
        "age_min", "age_max", "price_min", "price_max")))) + " "
    # Chercher chaque mot à majuscule ou nombre du résumé dans la source
    found = []
    for match in re.finditer(r"[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ'’-]+|\d+", summary):
        word = match.group(0)
        if not word[0].isdigit() and SENTENCE_START.search(summary[:match.start()]):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", plain(word)).strip()
        if not key or key in REGION_NAMES or key in COMMON_CAPITALIZED or ROMAN_CENTURY.match(word):
            continue
        if f" {key} " not in source and not all(f" {part} " in source for part in key.split()):
            found.append(word)
    return found


def check_enrichment(enrichment: Enrichment, row: Mapping[str, Any]) -> tuple[Enrichment, list[str]]:
    """Corriger un enrichissement validé à l'aide de la source.

    Catégorie d'après le titre quand elle est évidente ; résumé écarté s'il
    cite un nom absent de la source. Renvoie (enrichissement corrigé, noms inventés).

    Parameters
    ----------
    enrichment : Enrichment
        L'enrichissement rendu par Mistral et déjà validé.
    row : Mapping[str, Any]
        L'événement source correspondant.

    Returns
    -------
    tuple[Enrichment, list[str]]
        L'enrichissement corrigé (une copie si quelque chose a changé), et les
        noms inventés qui ont fait écarter le résumé.
    """

    updates: dict[str, Any] = {}
    category = category_from_title(str(row.get("title") or ""))
    if category and category != enrichment.categorie:
        updates["categorie"] = category
    invented = unsupported_names(enrichment.resume, row) if enrichment.resume else []
    if invented:
        updates["resume"] = ""
    return (enrichment.model_copy(update=updates) if updates else enrichment), invented


def enrich_with_mistral(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Enrichment], dict[str, dict[str, int]]]:
    """Enrichit un lot. Renvoie ({event_id: Enrichment}, consommation {modèle: tokens}).

    Un seul appel Mistral pour tout le lot ; les enrichissements valides sont
    ensuite contrôlés (`check_enrichment`) et rendus sous l'identifiant réel.

    Parameters
    ----------
    rows : Sequence[Mapping[str, Any]]
        Les événements du lot ; chacun doit avoir un `event_id`.

    Returns
    -------
    tuple[dict[str, Enrichment], dict[str, dict[str, int]]]
        Les enrichissements par `event_id` (les événements refusés en sont
        absents), et les tokens consommés par modèle.
    """

    # Appeler Mistral sur le lot, avec des identifiants courts
    model = enrichment_model()
    answer, usage = call_mistral_json(ENRICHMENT_PROMPT, build_batch_text(rows), model)
    by_short_id = {short_id(index): str(row["event_id"]) for index, row in enumerate(rows)}
    # Lire la réponse et signaler les événements sans enrichissement
    valid, _ = parse_enrichments(answer, list(by_short_id))
    missing = [str(row.get("title") or row["event_id"]) for index, row in enumerate(rows)
               if short_id(index) not in valid]
    if missing:
        logger.warning("Enrichissement absent ou refusé pour %s événement(s) : %s.", len(missing), missing[:10])
    # Contrôler chaque enrichissement contre sa source (catégorie du titre, noms inventés)
    checked: dict[str, Enrichment] = {}
    for index, row in enumerate(rows):
        key = short_id(index)
        if key in valid:
            checked[key], invented = check_enrichment(valid[key], row)
            if invented:
                logger.warning("Résumé écarté (noms absents de la source : %s) : %s.", ", ".join(invented),
                               row.get("title"))
    # Revenir aux identifiants réels des événements
    return {by_short_id[key]: value for key, value in checked.items()}, {model: usage}
