"""Ce que l'agent tire des pouces bas de la semaine (16/10/2026).

**Provenance** : nouveau module écrit pour SortirIA.

Un pouce bas dit qu'une réponse n'a pas plu ; le commentaire « ce qui
manquait », la question et les cartes proposées disent souvent pourquoi.
Chaque lundi, avec les recherches ratées (`agent/search_learning.py`),
Mistral range chaque pouce bas dans une cause :

| Cause | Ce que l'agent en fait |
| --- | --- |
| `sujet_absent` : la base n'a rien sur ce sujet | le mot cherché rejoint les mots inconnus de la semaine : synonyme ou agenda **proposé** (pull request) |
| `mal_compris` : un mot de la question n'a pas été compris | idem : un synonyme ou une correction est proposé |
| `date_ou_lieu` : les cartes ne respectent pas le moment ou le lieu demandés | signalé dans le mail (à rejouer avec `scripts/eval_recherche.py`) |
| `redaction` : bonnes cartes, réponse mal écrite (trop longue, ton, erreur) | signalé dans le mail (consigne de rédaction) |
| `autre` | signalé dans le mail |

Garde-fous : seuls les avis demandés sont gardés, avec une cause connue ; le
mot retenu est un seul mot ; un pouce bas compte comme une recherche ratée
« confirmée » (`MIN_TIMES` fois), puisqu'un visiteur a pris la peine de le
dire. Rien n'est appliqué : les propositions suivent le circuit habituel.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.search_learning import MIN_TIMES
from src.logger import get_logger

logger = get_logger(__name__)

CAUSES = ("sujet_absent", "mal_compris", "date_ou_lieu", "redaction", "autre")
CAUSE_LABELS = {
    "sujet_absent": "sujet absent de la base",
    "mal_compris": "question mal comprise",
    "date_ou_lieu": "date ou lieu non respectés",
    "redaction": "réponse mal rédigée",
    "autre": "autre",
}
MAX_REVIEWS = 20

# Version de la consigne (26/10/2026) : à changer quand le texte change ; le
# manifeste config/versions_consignes.json et un test de la CI y veillent.
FEEDBACK_PROMPT_VERSION = "v1"

FEEDBACK_PROMPT = """Tu aides SortirIA, un assistant de sorties (Montpellier, bassin de Thau).
Des visiteurs ont mis un pouce bas à une réponse. Pour chaque avis, on te donne la
question, le commentaire du visiteur (« ce qui manquait », parfois vide), les titres
des sorties proposées et le début de la réponse rédigée.
Range chaque avis dans UNE cause :
- "sujet_absent" : le visiteur cherchait un sujet que les sorties proposées ne
  traitent pas du tout (rien sur ce sujet) ;
- "mal_compris" : un mot de la question n'a pas été compris (faute, mot rare,
  synonyme) et les sorties proposées parlent d'autre chose ;
- "date_ou_lieu" : les sorties ne sont pas au moment ou à l'endroit demandés ;
- "redaction" : les sorties conviennent mais la réponse écrite déplaît ;
- "autre" : rien de tout cela, ou pas assez d'indices.
Pour "sujet_absent" et "mal_compris", donne le mot cherché (un seul mot, en
minuscules, sans accents, tel que le visiteur l'a écrit). Sinon, mot vide.
Donne une explication de quelques mots.
Réponds uniquement en JSON :
{"avis": [{"id": 0, "cause": "...", "mot": "", "explication": "..."}]}"""


class FeedbackReading(BaseModel):
    """Ce que Mistral dit d'un pouce bas.

    Attributes
    ----------
    id : int
        Numéro de l'avis dans la liste envoyée à Mistral.
    cause : str
        Cause retenue (une des `CAUSES`).
    mot : str
        Mot cherché, pour `sujet_absent` et `mal_compris` ; vide sinon.
    explication : str
        Explication de quelques mots donnée par Mistral.
    """

    id: int
    cause: str
    mot: str = ""
    explication: str = ""


@dataclass
class FeedbackLearning:
    """Mots à apprendre (ajoutés aux recherches ratées) et lignes pour le mail.

    Attributes
    ----------
    words : dict[str, int]
        Mots à apprendre, avec leur poids (chaque pouce bas compte `MIN_TIMES` fois).
    causes : dict[str, int]
        Nombre de pouces bas par cause.
    notes : list[str]
        Lignes destinées au mail du lundi.
    usage : dict[str, dict[str, int]]
        Jetons consommés, par modèle Mistral.
    """

    words: dict[str, int] = field(default_factory=dict)
    causes: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)


def describe_review(index: int, review: Mapping[str, Any]) -> str:
    """Rédiger un pouce bas en quelques lignes, pour le message envoyé à Mistral.

    Parameters
    ----------
    index : int
        Numéro de l'avis, que Mistral renvoie dans son champ `id`.
    review : Mapping[str, Any]
        Le pouce bas (`question`, `comment`, `titles`, `card_title`, `answer`).

    Returns
    -------
    str
        Le texte de l'avis : question, commentaire, sorties proposées, début de la réponse.
    """
    titles = [str(title) for title in review.get("titles") or [] if title]
    card = review.get("card_title")
    lines = [f"Avis {index}",
             f"Question : {review.get('question', '')}",
             f"Commentaire : {review.get('comment') or '(aucun)'}",
             f"Sorties proposées : {' ; '.join(titles) if titles else '(aucune)'}"]
    if card:
        lines.append(f"Pouce bas sur la carte : {card}")
    if review.get("answer"):
        lines.append(f"Début de la réponse : {str(review['answer'])[:300]}")
    return "\n".join(lines)


def parse_feedback(answer: str, count: int) -> dict[int, FeedbackReading]:
    """Réponse de Mistral contrôlée : avis demandés, cause connue, mot d'un seul tenant.

    Les avis mal formés, hors liste ou de cause inconnue sont ignorés ; le mot
    est vidé s'il ne concerne pas `sujet_absent` / `mal_compris` ou n'est pas
    un seul mot de 3 à 30 caractères.

    Parameters
    ----------
    answer : str
        Réponse JSON brute de Mistral.
    count : int
        Nombre d'avis envoyés (les `id` valides vont de 0 à `count - 1`).

    Returns
    -------
    dict[int, FeedbackReading]
        Les lectures retenues, indexées par numéro d'avis.

    Raises
    ------
    json.JSONDecodeError
        Si la réponse n'est pas du JSON (laissée remonter jusqu'à l'appelant).
    """

    data = json.loads(answer)
    readings: dict[int, FeedbackReading] = {}
    for item in data.get("avis") or []:
        try:
            reading = FeedbackReading.model_validate(item)
        except ValidationError:
            continue
        if not 0 <= reading.id < count or reading.cause not in CAUSES:
            continue
        word = reading.mot.strip().lower()
        if reading.cause not in ("sujet_absent", "mal_compris") or not re.fullmatch(r"[a-z0-9-]{3,30}", word):
            word = ""
        readings[reading.id] = reading.model_copy(update={"mot": word})
    return readings


def read_feedback_with_mistral(reviews: Sequence[Mapping[str, Any]]) -> tuple[dict[int, FeedbackReading],
                                                                                dict[str, dict[str, int]]]:
    """Lecture réelle par Mistral (`LEARNING_MODEL`, mistral-small-latest par défaut).

    Parameters
    ----------
    reviews : Sequence[Mapping[str, Any]]
        Les pouces bas à classer.

    Returns
    -------
    tuple[dict[int, FeedbackReading], dict[str, dict[str, int]]]
        Les lectures contrôlées par `parse_feedback`, et les jetons consommés par modèle.
    """

    import os

    from src.event.event_enrichment import call_mistral_json

    model = os.getenv("LEARNING_MODEL", "mistral-small-latest")
    text = "\n\n".join(describe_review(index, review) for index, review in enumerate(reviews))
    answer, tokens = call_mistral_json(FEEDBACK_PROMPT, text, model)
    return parse_feedback(answer, len(reviews)), {model: tokens}


def learn_from_thumbs_down(
    reviews: Sequence[Mapping[str, Any]],
    read: Callable[[Sequence[Mapping[str, Any]]], tuple[Mapping[int, FeedbackReading], Mapping[str, Mapping[str, int]]]],
) -> FeedbackLearning:
    """Range les pouces bas de la semaine par cause ; les mots à apprendre en sortent.

    Au plus `MAX_REVIEWS` avis sont lus. Si la lecture échoue, le bilan part
    quand même, avec une simple ligne qui le signale.

    Parameters
    ----------
    reviews : Sequence[Mapping[str, Any]]
        Les pouces bas de la semaine.
    read : Callable
        Classe les avis et renvoie (lectures par numéro, jetons par modèle) ;
        en production `read_feedback_with_mistral`.

    Returns
    -------
    FeedbackLearning
        Les mots à apprendre, le compte par cause, les lignes du mail et les jetons.
    """

    result = FeedbackLearning()
    reviews = list(reviews)[:MAX_REVIEWS]
    if not reviews:
        return result
    # Faire classer les avis par Mistral ; en cas d'échec, une ligne dans le mail suffit
    try:
        readings, usage = read(reviews)
    except Exception as error:  # le bilan part quand même, sans le classement
        logger.exception("Lecture des pouces bas par Mistral impossible.")
        result.notes.append(f"{len(reviews)} pouce(s) bas cette semaine ; lecture par Mistral impossible : {error}")
        return result
    result.usage = {model: dict(tokens) for model, tokens in usage.items()}
    # Compter les causes, garder les mots à apprendre, détailler chaque avis
    details: list[str] = []
    for index, review in enumerate(reviews):
        reading = readings.get(index)
        cause = reading.cause if reading else "autre"
        result.causes[cause] = result.causes.get(cause, 0) + 1
        if reading and reading.mot:
            result.words[reading.mot] = result.words.get(reading.mot, 0) + MIN_TIMES
        why = f" : {reading.explication}" if reading and reading.explication else ""
        comment = f" (« {review['comment']} »)" if review.get("comment") else ""
        details.append(f"« {review.get('question', '')} »{comment} → {CAUSE_LABELS[cause]}{why}")
    # Rédiger le résumé par cause, puis le détail et les mots retenus
    summary = ", ".join(f"{count} {CAUSE_LABELS[cause]}" for cause, count in
                        sorted(result.causes.items(), key=lambda item: -item[1]))
    result.notes = [f"{len(reviews)} pouce(s) bas cette semaine : {summary}.", *details]
    if result.words:
        result.notes.append("Mots repris dans l'apprentissage de la recherche : "
                            + ", ".join(sorted(result.words)) + ".")
    return result


def merge_words(gaps: Mapping[str, Any], words: Mapping[str, int]) -> dict[str, Any]:
    """Les mots des pouces bas rejoignent les mots inconnus de la semaine.

    Parameters
    ----------
    gaps : Mapping[str, Any]
        Bilan de la recherche, dont `unknown_words` : liste de (mot, nombre de fois).
    words : Mapping[str, int]
        Mots tirés des pouces bas, avec leur poids.

    Returns
    -------
    dict[str, Any]
        Une copie du bilan où `unknown_words` cumule les deux, du plus cherché
        au moins cherché (puis par ordre alphabétique).
    """

    merged: dict[str, int] = {str(word): int(times) for word, times in gaps.get("unknown_words") or []}
    for word, times in words.items():
        merged[word] = merged.get(word, 0) + int(times)
    ordered = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    return {**gaps, "unknown_words": ordered}


def learn_with_feedback(gaps: Mapping[str, Any], today: Any, *, learn: Callable[[Mapping[str, Any], Any], Any],
                        read_feedback: Callable[..., Any]) -> Any:
    """Apprendre des pouces bas puis des recherches ratées de la semaine.

    Le lundi : pouces bas d'abord (leurs mots rejoignent les recherches
    ratées), puis l'apprentissage habituel (`learn`, qui renvoie un
    `LearningResult`) ; le classement des pouces bas va dans le mail.

    Parameters
    ----------
    gaps : Mapping[str, Any]
        Bilan de la recherche de la semaine, avec les pouces bas sous `thumbs_down`.
    today : Any
        Date du jour, transmise à `learn`.
    learn : Callable[[Mapping[str, Any], Any], Any]
        Apprentissage habituel des recherches ratées (renvoie un `LearningResult`).
    read_feedback : Callable[..., Any]
        Classe les pouces bas (voir `learn_from_thumbs_down`).

    Returns
    -------
    Any
        Le `LearningResult` de `learn`, complété de `feedback_notes` et des
        jetons des deux lectures.
    """

    from agent.watch import merge_usage

    feedback = learn_from_thumbs_down(gaps.get("thumbs_down") or [], read_feedback)
    result = learn(merge_words(gaps, feedback.words), today)
    result.feedback_notes = feedback.notes
    result.usage = merge_usage(result.usage, feedback.usage)
    return result
