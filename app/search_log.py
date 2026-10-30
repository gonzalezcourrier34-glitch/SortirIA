"""Suivi de la recherche : journal des demandes, pouces, santé de la collecte.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

Chaque demande est tracée dans `sortiria.rag_requests` (contraintes, cartes,
tokens, coût, version de la consigne) ; le pouce haut ou bas d'un visiteur
va dans `sortiria.feedback` ; `collect_health` donne l'état de la dernière
collecte à la route de santé. Le suivi ne bloque jamais une réponse.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from agent.watch import DEFAULT_PRICE, PRICES_USD_PER_MILLION
from app.query_understanding import QueryPlan
from app.search_answer import PROMPT_VERSION
from app.search_filters import PARIS, SearchFilters
from app.search_query import fetch_rows

if TYPE_CHECKING:
    from app.search_service import SearchResult

LOG_SQL = """
INSERT INTO sortiria.rag_requests
    (question, constraints, event_ids, status, error, latency_ms,
     prompt_tokens, completion_tokens, cost_usd, llm_model, prompt_version, answer, degraded,
     from_cache, visitor_key)
VALUES (%(question)s, %(constraints)s, %(event_ids)s, %(status)s, %(error)s, %(latency_ms)s,
        %(prompt_tokens)s, %(completion_tokens)s, %(cost_usd)s, %(llm_model)s, %(prompt_version)s,
        %(answer)s, %(degraded)s, %(from_cache)s, %(visitor_key)s)
RETURNING request_id"""


def request_cost(model: str, tokens: Mapping[str, int]) -> float:
    """Coût d'une réponse en dollars (grille de agent/watch.py), gardé au millionième.

    Une seule réponse coûte de l'ordre de 0,00001 $, l'arrondi de la nuit
    (4 décimales) le perdrait.

    Parameters
    ----------
    model : str
        Le modèle de rédaction (prix par défaut s'il n'est pas dans la grille).
    tokens : Mapping[str, int]
        Les tokens consommés (`input`, `output`).

    Returns
    -------
    float
        Le coût en dollars, arrondi à 6 décimales.
    """

    price_in, price_out = PRICES_USD_PER_MILLION.get(model, DEFAULT_PRICE)
    cost = tokens.get("input", 0) * price_in / 1e6 + tokens.get("output", 0) * price_out / 1e6
    return round(cost, 6)


def plan_summary(plan: QueryPlan) -> dict[str, Any]:
    """Le plan en JSON pour `rag_requests.constraints`.

    Parameters
    ----------
    plan : QueryPlan
        Le plan de recherche.

    Returns
    -------
    dict[str, Any]
        Période, public, zone, communes, gratuit, soir, moment, types, mots-clés,
        et la liste des contraintes lues dans la question.
    """

    return {"periode": [plan.start.isoformat(), plan.end.isoformat()], "public": plan.public,
            "zone": plan.zone, "communes": list(plan.cities), "gratuit": plan.free_only,
            "soir": plan.evening, "moment": plan.moment,
            "categories": list(plan.categories), "mots_cles": list(plan.keywords),
            "lus_dans_la_question": sorted(plan.from_question)}


def log_request(connection: Any, question: str, filters: SearchFilters, result: SearchResult,
                model: str, latency_ms: int, plan: QueryPlan | None = None,
                from_cache: bool = False, visitor_key: str | None = None) -> int | None:
    """Trace la demande dans `rag_requests` (suivi Grafana). Jamais bloquant.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (validée après l'écriture, annulée en cas d'erreur).
    question : str
        La question (« (filtres seuls) » si elle est vide).
    filters : SearchFilters
        Les cases cochées.
    result : SearchResult
        Le résultat envoyé.
    model : str
        Le modèle de rédaction, vide si Mistral n'a pas servi.
    latency_ms : int
        Durée de la recherche, en millisecondes.
    plan : QueryPlan | None
        Le plan appliqué, s'il est connu.
    from_cache : bool
        Vrai si la réponse vient du cache. Par défaut faux.
    visitor_key : str | None
        Empreinte salée du navigateur (27/10/2026), pour compter les visiteurs
        distincts ; None si l'interface n'a pas envoyé d'identifiant.

    Returns
    -------
    int | None
        Le numéro de la demande, ou None si la trace n'a pas pu être écrite.
    """

    try:
        rows = fetch_rows(connection, LOG_SQL, {
            "question": question or "(filtres seuls)",
            # Filtres cochés et contraintes comprises : la base du suivi de précision.
            # « recherche » : réponse exacte ou non, et ce qui a manqué (bilan
            # hebdomadaire de l'agent, `fetch_search_gaps`).
            "constraints": json.dumps({**asdict(filters),
                                       **({"compris": plan_summary(plan)} if plan else {}),
                                       "recherche": {"exact": result.exact, **result.gaps}},
                                      ensure_ascii=False),
            "event_ids": [card["event_id"] for card in result.events],
            "status": "ok" if result.events else "aucun_resultat",
            "error": "service Mistral indisponible" if result.degraded else "",
            "latency_ms": latency_ms,
            # Réponse du cache : aucun token consommé (30/10/2026 : ceux de la
            # réponse d'origine étaient recopiés et gonflaient les totaux).
            "prompt_tokens": None if from_cache else result.tokens.get("input"),
            "completion_tokens": None if from_cache else result.tokens.get("output"),
            # Même grille de prix que l'agent (agent/watch.py) : l'embedding de la
            # question n'est pas compté (quelques dizaines de tokens).
            "cost_usd": request_cost(model, result.tokens) if model and result.tokens else None,
            "llm_model": model or None,
            "prompt_version": PROMPT_VERSION,
            "answer": result.answer,
            "degraded": result.degraded,
            "from_cache": from_cache,
            "visitor_key": visitor_key,
        })
        connection.commit()
        return int(rows[0]["request_id"]) if rows else None
    except Exception:  # noqa: BLE001 (le suivi ne doit jamais bloquer une réponse)
        connection.rollback()
        return None


# Pouce haut / pouce bas sur une réponse (16/10/2026). Seulement pour une
# réponse de moins d'un jour : au-delà, le visiteur ne l'a plus sous les yeux.
# Un second clic remplace le premier (contrainte feedback_once). Le
# commentaire n'est jamais publié : il sert au bilan et, plus tard, de donnée
# d'entraînement (docs/evolutions.md).
FEEDBACK_WINDOW = timedelta(days=1)
RECORD_FEEDBACK_SQL = """
INSERT INTO sortiria.feedback (request_id, event_id, rating, comment)
SELECT r.request_id, '', %(rating)s, %(comment)s
FROM sortiria.rag_requests AS r
WHERE r.request_id = %(request_id)s AND r.asked_at >= %(since)s
ON CONFLICT (request_id, event_id) DO UPDATE
SET rating = EXCLUDED.rating, comment = EXCLUDED.comment, created_at = now()
RETURNING feedback_id"""


def record_feedback(connection: Any, request_id: int, rating: int, comment: str = "",
                    now: datetime | None = None) -> bool:
    """Enregistre l'avis sur une réponse (1 : utile, -1 : pas utile).

    Faux si la réponse n'existe pas ou date de plus d'un jour. Un second avis
    remplace le premier.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (validée après l'écriture).
    request_id : int
        Numéro de la réponse dans `rag_requests`.
    rating : int
        1 (utile) ou -1 (pas utile).
    comment : str
        Mot facultatif (300 caractères gardés).
    now : datetime | None
        Heure de référence ; par défaut maintenant.

    Returns
    -------
    bool
        Vrai si l'avis est enregistré.

    Raises
    ------
    ValueError
        Si `rating` ne vaut ni 1 ni -1.
    """

    if rating not in (-1, 1):
        raise ValueError("rating vaut 1 ou -1")
    since = (now or datetime.now(PARIS)) - FEEDBACK_WINDOW
    rows = fetch_rows(connection, RECORD_FEEDBACK_SQL, {
        "request_id": request_id, "rating": rating, "comment": comment.strip()[:300],
        "since": since})
    connection.commit()
    return bool(rows)


# Santé du service (16/10/2026) : lue par la surveillance GitHub
# (.github/workflows/surveillance.yml) via /api/v1/sante. Aucune donnée
# sensible : un statut et un âge en heures.
COLLECT_HEALTH_SQL = """
SELECT
    (SELECT status FROM sortiria.collect_runs ORDER BY started_at DESC LIMIT 1) AS statut,
    (SELECT round((EXTRACT(EPOCH FROM now() - max(started_at)) / 3600)::NUMERIC, 1)
       FROM sortiria.collect_runs WHERE status NOT IN ('failed', 'running')) AS heures
"""


def database_status(connection: Any) -> str:
    """La base répond-elle à une vraie requête (`SELECT 1`) ?

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.

    Returns
    -------
    str
        « ok » si la base renvoie 1, « erreur » sinon.

    Raises
    ------
    Exception
        L'erreur du pilote si la requête échoue (laissée remonter : la route
        de santé répond alors 500, ce que la surveillance détecte).
    """

    rows = fetch_rows(connection, "SELECT 1 AS ok", {})
    return "ok" if rows and rows[0].get("ok") == 1 else "erreur"


def collect_health(connection: Any) -> dict[str, Any]:
    """Statut de la dernière collecte et heures depuis la dernière réussie.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.

    Returns
    -------
    dict[str, Any]
        `statut` de la dernière collecte, et `heures_depuis_reussite` (None si
        aucune collecte n'a réussi).
    """

    row = fetch_rows(connection, COLLECT_HEALTH_SQL, {})[0]
    hours = row["heures"]
    return {"statut": row["statut"],
            "heures_depuis_reussite": None if hours is None else float(hours)}
