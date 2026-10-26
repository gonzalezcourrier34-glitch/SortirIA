"""L'agent apprend de ce que les gens cherchent sans trouver.

**Provenance** : nouveau module écrit pour SortirIA (15/10/2026).

Chaque lundi, après le bilan de la recherche (`fetch_search_gaps`), l'agent
prend les mots cherchés plusieurs fois et absents de tous les titres de la
base (« zumba (4) », « cirqe (2) »), et pour chacun :

1. **Mistral dit ce que c'est** : une faute de frappe, un synonyme d'un sujet
   que la base connaît peut-être, un sujet à part entière, ou une ambiance.
   Il propose quelques mots équivalents ; seul un mot vraiment présent dans
   les titres de la base est retenu (pas de synonyme inventé).
2. **Faute ou synonyme** -> proposition « ajouter_synonyme » : une fois
   acceptée, « zumba » cherche aussi « fitness » (config/synonymes.yaml).
3. **Sujet absent de la base** -> l'agent cherche ce sujet dans les agendas
   OpenAgenda de la zone qu'on ne lit pas encore. S'il en trouve, proposition
   « ajouter_agenda » (le même circuit que la découverte des agendas) ;
   sinon, une piste dans le mail : en parler aux partenaires.
4. **Ambiance** (« romantique ») : rien à faire, la recherche par le sens
   s'en charge.

Comme partout dans l'agent : il propose, un humain décide
(`uv run python -m agent.proposals`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.logger import get_logger

logger = get_logger(__name__)

MIN_TIMES = 2          # un mot cherché une seule fois peut être un accident
MAX_WORDS = 5          # par semaine : le bilan reste lisible, la recherche OpenAgenda courte
MAX_CANDIDATES = 4     # mots équivalents proposés par Mistral pour un mot
NATURES = ("faute", "synonyme", "sujet", "ambiance")

# Version de la consigne (26/10/2026) : à changer quand le texte change ; le
# manifeste config/versions_consignes.json et un test de la CI y veillent.
LEARNING_PROMPT_VERSION = "v1"

LEARNING_PROMPT = """Tu aides SortirIA, un assistant de sorties (Montpellier, bassin de Thau).
Des utilisateurs ont cherché des mots qu'aucun titre d'événement de la base ne contient.
Pour chaque mot, dis ce que c'est :
- "faute" : une faute de frappe d'un mot courant (« cirqe » pour « cirque ») ;
- "synonyme" : un autre nom d'une sortie courante (« stand-up » pour « humour »,
  « ciné » pour « cinéma », « zumba » pour « fitness » ou « danse ») ;
- "sujet" : une activité précise, à part entière (« escalade », « poterie ») ;
- "ambiance" : une envie ou une qualité, pas une activité (« romantique », « insolite »).
Pour "faute" et "synonyme", donne jusqu'à 4 mots équivalents, du plus proche au
plus large, en français courant, un seul mot chacun, sans accents.
Pour "sujet", donne jusqu'à 4 mots proches qui pourraient figurer dans un titre.
Pour "ambiance", aucune liste.
Réponds uniquement en JSON :
{"mots": [{"mot": "...", "nature": "faute|synonyme|sujet|ambiance", "equivalents": ["..."]}]}"""


class WordReading(BaseModel):
    """Ce que Mistral dit d'un mot cherché.

    Attributes
    ----------
    mot : str
        Le mot cherché.
    nature : str
        Sa nature (une des `NATURES` : faute, synonyme, sujet, ambiance).
    equivalents : list[str]
        Mots équivalents ou proches proposés par Mistral.
    """

    mot: str
    nature: str
    equivalents: list[str] = Field(default_factory=list)


@dataclass
class LearningResult:
    """Propositions pour la table `agent_proposals` et lignes pour le mail.

    Attributes
    ----------
    proposals : list[dict[str, Any]]
        Propositions (`ajouter_synonyme`, `ajouter_agenda`) en attente de validation.
    notes : list[str]
        Lignes destinées au mail du lundi.
    usage : dict[str, dict[str, int]]
        Jetons consommés, par modèle Mistral.
    feedback_notes : list[str]
        Lignes sur les pouces bas de la semaine rangés par cause.
    """

    proposals: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    # Pouces bas de la semaine rangés par cause (agent/feedback_learning.py).
    feedback_notes: list[str] = field(default_factory=list)


def subject_words(words: Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
    """Retirer les mots vides de la liste des mots cherchés.

    Sans les mots de la phrase (« bonjour », « sont », « propositions ») :
    les recherches d'avant le 19/10/2026 les gardaient comme sujets, et l'agent
    proposait « bonjour » -> « salut » (bilan du 19/10).

    Parameters
    ----------
    words : Iterable[tuple[str, int]]
        Couples (mot, nombre de fois cherché).

    Returns
    -------
    list[tuple[str, int]]
        Les mêmes couples, sans les mots de `STOPWORDS`.
    """

    from app.query_understanding import STOPWORDS

    return [(str(word), int(times)) for word, times in words if str(word) not in STOPWORDS]


def words_to_learn(gaps: Mapping[str, Any], minimum: int = MIN_TIMES, limit: int = MAX_WORDS) -> list[tuple[str, int]]:
    """Mots inconnus de la base, cherchés au moins `minimum` fois (les plus cherchés d'abord).

    L'ordre est celui de `unknown_words`, déjà trié du plus cherché au moins cherché.

    Parameters
    ----------
    gaps : Mapping[str, Any]
        Bilan de la recherche, dont `unknown_words` : liste de (mot, nombre de fois).
    minimum : int
        Nombre minimal de recherches. Par défaut `MIN_TIMES` (2).
    limit : int
        Nombre maximal de mots gardés. Par défaut `MAX_WORDS` (5).

    Returns
    -------
    list[tuple[str, int]]
        Les mots retenus, avec leur nombre de recherches.
    """

    words = [(word, times) for word, times in subject_words(gaps.get("unknown_words") or []) if times >= minimum]
    return words[:limit]


def parse_readings(answer: str, words: Sequence[str]) -> dict[str, WordReading]:
    """Lire et contrôler la réponse de Mistral sur les mots cherchés.

    Réponse de Mistral lue et contrôlée : seulement les mots demandés, une
    nature connue, des équivalents d'un seul mot, différents du mot cherché.

    Parameters
    ----------
    answer : str
        Réponse JSON brute de Mistral.
    words : Sequence[str]
        Les mots demandés ; les autres sont ignorés.

    Returns
    -------
    dict[str, WordReading]
        Les lectures retenues, indexées par mot (au plus `MAX_CANDIDATES` équivalents).

    Raises
    ------
    json.JSONDecodeError
        Si la réponse n'est pas du JSON (laissée remonter jusqu'à l'appelant).
    """

    data = json.loads(answer)
    asked = set(words)
    readings: dict[str, WordReading] = {}
    for item in data.get("mots") or []:
        try:
            reading = WordReading.model_validate(item)
        except ValidationError:
            continue
        word = reading.mot.strip().lower()
        if word not in asked or reading.nature not in NATURES:
            continue
        equivalents = []
        for candidate in reading.equivalents:
            candidate = candidate.strip().lower()
            # Un mot simple : pas « ou-encore » (bilan du 19/10/2026).
            if re.fullmatch(r"[a-z]+", candidate) and candidate != word and candidate not in equivalents:
                equivalents.append(candidate)
        readings[word] = WordReading(mot=word, nature=reading.nature, equivalents=equivalents[:MAX_CANDIDATES])
    return readings


def read_words_with_mistral(words: Sequence[str]) -> tuple[dict[str, WordReading], dict[str, dict[str, int]]]:
    """Lecture réelle par Mistral (`LEARNING_MODEL`, mistral-small-latest par défaut).

    Parameters
    ----------
    words : Sequence[str]
        Les mots à classer.

    Returns
    -------
    tuple[dict[str, WordReading], dict[str, dict[str, int]]]
        Les lectures contrôlées par `parse_readings`, et les jetons consommés par modèle.
    """

    import os

    from src.event.event_enrichment import call_mistral_json

    model = os.getenv("LEARNING_MODEL", "mistral-small-latest")
    answer, tokens = call_mistral_json(LEARNING_PROMPT, "\n".join(f"- {word}" for word in words), model)
    return parse_readings(answer, words), {model: tokens}


def learn_from_searches(
    gaps: Mapping[str, Any],
    today: date,
    *,
    read_words: Callable[[Sequence[str]], tuple[Mapping[str, WordReading], Mapping[str, Mapping[str, int]]]],
    known_words: Callable[[Sequence[str]], Iterable[str]],
    find_agendas: Callable[[Sequence[str]], Mapping[str, Mapping[str, Mapping[str, Any]]]] | None = None,
) -> LearningResult:
    """Transforme les recherches ratées de la semaine en propositions.

    - `read_words` : Mistral classe les mots (faute, synonyme, sujet, ambiance) ;
    - `known_words` : les mots présents dans les titres de la base (garde-fou :
      un synonyme proposé doit exister dans la base, sinon il ne sert à rien) ;
    - `find_agendas` : {mot: {agenda: {"events": n, "examples": [...]}}} pour
      les agendas OpenAgenda de la zone qu'on ne lit pas encore (None : pas de
      recherche, par exemple sans clé OpenAgenda).

    Parameters
    ----------
    gaps : Mapping[str, Any]
        Bilan de la recherche de la semaine (`unknown_words`).
    today : date
        Date du jour, notée dans chaque proposition (`found_on`).
    read_words : Callable
        Classe les mots et renvoie (lectures par mot, jetons par modèle) ;
        en production `read_words_with_mistral`.
    known_words : Callable[[Sequence[str]], Iterable[str]]
        Renvoie, parmi les mots donnés, ceux présents dans les titres de la base.
    find_agendas : Callable | None
        Cherche les sujets dans les agendas OpenAgenda non lus ; `None` par
        défaut (pas de recherche).

    Returns
    -------
    LearningResult
        Les propositions, les lignes du mail et les jetons consommés.
    """

    # Choisir les mots à apprendre et les faire classer par Mistral
    result = LearningResult()
    words = words_to_learn(gaps)
    if not words:
        return result
    times = dict(words)
    try:
        readings, usage = read_words([word for word, _ in words])
    except Exception as error:  # le bilan part quand même, sans les pistes
        logger.exception("Lecture des mots cherchés par Mistral impossible.")
        result.notes.append(f"Lecture des mots cherchés impossible : {error}")
        return result
    result.usage = {model: dict(tokens) for model, tokens in usage.items()}

    # Faute ou synonyme d'un mot connu de la base : proposer un synonyme ; sujet : à chercher
    to_search: list[str] = []
    for word, count in words:
        reading = readings.get(word)
        if reading is None:
            result.notes.append(f"« {word} » ({count}) : pas de lecture de Mistral.")
            continue
        if reading.nature == "ambiance":
            result.notes.append(f"« {word} » ({count}) : une ambiance, la recherche par le sens s'en charge.")
            continue
        known = set(known_words(reading.equivalents)) if reading.equivalents else set()
        target = next((candidate for candidate in reading.equivalents if candidate in known), None)
        if reading.nature in ("faute", "synonyme") and target:
            label = "correction" if reading.nature == "faute" else "synonyme"
            result.proposals.append({
                "source_id": "recherche", "kind": "synonyme",
                "diagnosis": (f"« {word} » cherché {count} fois sans réponse, absent de tous les titres ; "
                              f"Mistral y voit un(e) {label} de « {target} », que la base connaît."),
                "action": {"type": "ajouter_synonyme", "word": word, "target": target, "times": count,
                           "nature": reading.nature, "new": f"{word} -> {target}", "found_on": today.isoformat()},
            })
            continue
        to_search.append(word)

    # Sujets absents : sans recherche OpenAgenda, une simple piste dans le mail
    if not to_search:
        return result
    if find_agendas is None:
        result.notes += [f"« {word} » ({times[word]}) : absent de la base ; piste à proposer aux partenaires."
                         for word in to_search]
        return result
    # Chercher les sujets dans les agendas OpenAgenda non lus
    try:
        found = find_agendas(to_search)
    except Exception as error:
        logger.exception("Recherche des sujets dans OpenAgenda impossible.")
        result.notes.append(f"Recherche dans OpenAgenda impossible : {error}")
        return result
    # Proposer chaque agenda trouvé (le plus fourni d'abord), sinon une piste partenaires
    for word in to_search:
        agendas = found.get(word) or {}
        if not agendas:
            result.notes.append(f"« {word} » ({times[word]}) : absent de la base et des agendas OpenAgenda "
                                "de la zone ; piste à proposer aux partenaires.")
            continue
        for uid, info in sorted(agendas.items(), key=lambda item: -int(item[1].get("events", 0))):
            examples = ", ".join(f"« {title} »" for title in list(info.get("examples") or [])[:2])
            result.proposals.append({
                "source_id": "openagenda", "kind": "sujet_demande",
                "diagnosis": (f"« {word} » cherché {times[word]} fois sans réponse : l'agenda OpenAgenda {uid}, "
                              f"pas encore lu, en propose {info.get('events', 0)} dans la zone"
                              + (f" ({examples})" if examples else "") + "."),
                "action": {"type": "ajouter_agenda", "new": uid, "events": info.get("events", 0),
                           "word": word, "found_on": today.isoformat(),
                           "url": f"https://openagenda.com/agendas/{uid}"},
            })
    return result
