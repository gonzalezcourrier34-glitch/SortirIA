"""Juge de qualité : Mistral contrôle un échantillon d'événements chaque nuit.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Méthode
« LLM comme juge », courante pour évaluer un système qui produit du texte : on
ne relit pas 1 200 fiches à la main, on en tire 20 au hasard et un modèle les
note selon une grille fixe. Le score, suivi nuit après nuit (MLflow, Grafana),
montre si une source ou une consigne dégrade la base.

Grille, par événement (quatre questions fermées, plus simples à fiabiliser
qu'une note libre) :

1. `coherent` : le titre, la catégorie et le résumé décrivent-ils bien le texte ?
2. `est_une_sortie` : est-ce une sortie de loisir proposable ?
3. `publics_corrects` : les publics (famille, soirée...) sont-ils justes ?
4. `categorie_correcte` : la catégorie est-elle la bonne ?

Le juge est, par défaut, un autre modèle que celui de l'enrichissement
(`mistral-medium-latest` contre `mistral-small-latest`) : un modèle ne note
pas son propre travail.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

JUDGE_VERSION = "2026-10-14.1"
DEFAULT_JUDGE_MODEL = "mistral-medium-latest"
SAMPLE_SIZE = 20
CHECKS = ("coherent", "est_une_sortie", "publics_corrects", "categorie_correcte")

JUDGE_PROMPT = """Tu contrôles la qualité d'un agenda de sorties (Montpellier, bassin de Thau).
Pour chaque fiche fournie, réponds par vrai ou faux à quatre questions :
- "coherent" : le titre, la catégorie et le résumé décrivent fidèlement le texte ;
- "est_une_sortie" : c'est une sortie de loisir qu'on peut proposer au public
  (faux pour une offre d'emploi, une réunion administrative, une fermeture...) ;
- "publics_corrects" : les publics indiqués sont justes (famille = adapté aux
  enfants ; soiree = sortie du soir entre adultes ; ados ; seniors ; liste vide
  acceptable pour un tout public) ;
- "categorie_correcte" : la catégorie est la plus juste.
Si une réponse est fausse, explique en une courte phrase dans "probleme".

Réponds uniquement avec un objet JSON :
{"verdicts": [{"id": "...", "coherent": true, "est_une_sortie": true,
"publics_corrects": true, "categorie_correcte": true, "probleme": ""}]}"""


class Verdict(BaseModel):
    """Réponse du juge pour une fiche."""

    id: str
    coherent: bool
    est_une_sortie: bool
    publics_corrects: bool
    categorie_correcte: bool
    probleme: str = ""

    @property
    def score(self) -> float:
        return sum(bool(getattr(self, check)) for check in CHECKS) / len(CHECKS)


def build_judge_text(rows: Sequence[Mapping[str, Any]]) -> str:
    """Fiches envoyées au juge : ce que l'utilisateur verrait, plus le texte d'origine."""

    blocks = []
    for row in rows:
        audiences = ", ".join(row.get("audiences") or []) or "-"
        blocks.append(
            f"id: {row['event_id']}\nTitre : {row.get('title', '')}\n"
            f"Catégorie : {row.get('category_norm') or row.get('category') or '-'}\n"
            f"Publics : {audiences}\nRésumé : {row.get('summary') or '-'}\n"
            f"Lieu : {row.get('venue_name') or '-'}, {row.get('city') or '-'}\n"
            f"Texte d'origine : {str(row.get('description') or '')[:600]}"
        )
    return "\n\n".join(blocks)


def parse_verdicts(answer: str, expected_ids: Sequence[str]) -> dict[str, Verdict]:
    """Verdicts valides d'une réponse, par identifiant."""

    match = re.search(r"\{.*\}", answer or "", re.DOTALL)
    if not match:
        return {}
    try:
        items = json.loads(match.group(0)).get("verdicts", [])
    except (json.JSONDecodeError, AttributeError):
        return {}
    verdicts = {}
    for item in items if isinstance(items, list) else []:
        try:
            verdict = Verdict.model_validate(item)
        except ValidationError:
            continue
        if verdict.id in expected_ids:
            verdicts[verdict.id] = verdict
    return verdicts


def summarize(verdicts: Mapping[str, Verdict], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Bilan d'une nuit : score global, taux par question, exemples de problèmes."""

    if not verdicts:
        return {"checked": 0, "score": None}
    titles = {str(row["event_id"]): row.get("title", "") for row in rows}
    values = list(verdicts.values())
    result: dict[str, Any] = {
        "checked": len(values),
        "score": round(sum(verdict.score for verdict in values) / len(values), 3),
    }
    for check in CHECKS:
        result[check] = round(sum(bool(getattr(verdict, check)) for verdict in values) / len(values), 3)
    result["problems"] = [
        f"{titles.get(verdict.id, verdict.id)} : {verdict.probleme}"
        for verdict in values if verdict.score < 1 and verdict.probleme
    ][:5]
    return result


def judge_model() -> str:
    """Modèle du juge (`JUDGE_MODEL` dans .env)."""

    return os.getenv("JUDGE_MODEL", DEFAULT_JUDGE_MODEL)


def judge_with_mistral(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Verdict], dict[str, dict[str, int]]]:
    """Fait juger un échantillon. Renvoie ({event_id: Verdict}, consommation {modèle: tokens})."""

    from src.event.event_enrichment import call_mistral_json

    model = judge_model()
    answer, usage = call_mistral_json(JUDGE_PROMPT, build_judge_text(rows), model)
    return parse_verdicts(answer, [str(row["event_id"]) for row in rows]), {model: usage}
