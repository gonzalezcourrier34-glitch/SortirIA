"""Cache des réponses : une même demande, le même jour, sur la même base.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

Clé de la demande (`cache_key`), expiration (6 h, jamais après minuit),
lecture et écriture dans `sortiria.query_cache`, numéro de la dernière
collecte (`index_version`). Une erreur de cache ne bloque jamais la recherche.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.query_understanding import QueryPlan, plain
from app.search_answer import PROMPT_VERSION
from app.search_filters import PARIS, SearchFilters
from app.search_log import plan_summary
from app.search_query import fetch_rows

if TYPE_CHECKING:
    from app.search_service import SearchResult

# Cache des réponses (bloc 3, 15/10/2026) : une même demande, le même jour,
# sur la même base, reçoit la même réponse sans nouvel appel à Mistral.
# La clé contient la date (« ce soir », « demain » changent de sens chaque
# jour) et le numéro de la dernière collecte (index_version) : une nouvelle
# nuit rend les anciennes réponses inutilisables. Les entrées expirent à
# minuit au plus tard ; la purge de la nuit les supprime (06_retention.sql).
CACHE_HOURS = 6
INDEX_VERSION_SQL = "SELECT COALESCE(max(run_id)::text, '') AS version FROM sortiria.collect_runs"
CACHE_GET_SQL = """
UPDATE sortiria.query_cache SET hits = hits + 1
WHERE cache_key = %(key)s AND expires_at > now()
RETURNING response"""
CACHE_PUT_SQL = """
INSERT INTO sortiria.query_cache
    (cache_key, question_normalized, constraints, response, event_ids, index_version, expires_at)
VALUES (%(key)s, %(question)s, %(constraints)s, %(response)s, %(event_ids)s, %(version)s,
        %(expires_at)s)
ON CONFLICT (cache_key) DO UPDATE SET
    response = EXCLUDED.response, event_ids = EXCLUDED.event_ids,
    expires_at = EXCLUDED.expires_at, created_at = now()"""


def cache_key(question: str, context: str, filters: SearchFilters, now: datetime, version: str,
              follow_up: bool = False) -> str:
    """Calcule l'empreinte de la demande, clé du cache des réponses.

    Empreinte de la demande : question et contexte normalisés, cases cochées,
    jour, version du prompt et de la base, synonymes appris (un synonyme
    validé sert dès la question suivante, sans attendre la fin du cache).

    Parameters
    ----------
    question : str
        La question du visiteur.
    context : str
        La demande précédente de la conversation.
    filters : SearchFilters
        Les cases cochées.
    now : datetime
        Heure de référence (seul le jour, à Paris, compte).
    version : str
        Numéro de la dernière collecte (`index_version`).
    follow_up : bool
        Vrai si la question est une relance cliquée. Par défaut faux.

    Returns
    -------
    str
        L'empreinte SHA-256 en hexadécimal.
    """

    import hashlib

    from app.query_understanding import learned_synonyms

    parts = [PROMPT_VERSION, version, now.astimezone(PARIS).date().isoformat(), plain(question),
             plain(context), json.dumps(asdict(filters), sort_keys=True),
             json.dumps(learned_synonyms(), sort_keys=True)]
    if follow_up:
        parts.append("relance")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def cache_expiry(now: datetime) -> datetime:
    """Fin de validité : 6 h plus tard, et jamais après minuit (heure de Paris).

    Parameters
    ----------
    now : datetime
        L'heure d'écriture dans le cache.

    Returns
    -------
    datetime
        La plus proche de `now` + `CACHE_HOURS` et du minuit suivant.
    """

    local = now.astimezone(PARIS)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return min(local + timedelta(hours=CACHE_HOURS), midnight)


def read_cache(connection: Any, key: str) -> dict[str, Any] | None:
    """Réponse gardée pour cette demande, ou None. Jamais bloquant.

    Une lecture compte aussi un usage de l'entrée (`hits`).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (validée après la lecture, annulée en cas d'erreur).
    key : str
        L'empreinte de la demande (`cache_key`).

    Returns
    -------
    dict[str, Any] | None
        Le résultat gardé (avec au moins `answer` et `events`), ou None s'il est
        absent, expiré ou illisible.
    """

    # Lire l'entrée encore valide (une erreur de base vaut « pas de cache »)
    try:
        rows = fetch_rows(connection, CACHE_GET_SQL, {"key": key})
        connection.commit()
    except Exception:  # noqa: BLE001 (sans cache, la recherche se fait normalement)
        connection.rollback()
        return None
    # Décoder la réponse gardée et vérifier qu'elle est complète
    response = rows[0].get("response") if rows else None
    if not response:
        return None
    try:
        cached = json.loads(response) if isinstance(response, str) else dict(response)
    except (TypeError, ValueError):
        return None
    return cached if "answer" in cached and "events" in cached else None


def write_cache(connection: Any, key: str, question: str, plan: QueryPlan, result: SearchResult,
                version: str, now: datetime) -> None:
    """Garde une réponse complète et sourcée. Jamais bloquant.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (validée après l'écriture, annulée en cas d'erreur).
    key : str
        L'empreinte de la demande (`cache_key`).
    question : str
        La question, gardée sous forme simplifiée.
    plan : QueryPlan
        Le plan appliqué, gardé en JSON (`plan_summary`).
    result : SearchResult
        Le résultat à garder.
    version : str
        Numéro de la dernière collecte.
    now : datetime
        Heure d'écriture, pour calculer l'expiration.
    """

    try:
        fetch_rows(connection, CACHE_PUT_SQL + " RETURNING cache_key", {
            "key": key, "question": plain(question),
            "constraints": json.dumps(plan_summary(plan), ensure_ascii=False),
            "response": json.dumps(asdict(result), ensure_ascii=False, default=str),
            "event_ids": [card["event_id"] for card in result.events],
            "version": version, "expires_at": cache_expiry(now),
        })
        connection.commit()
    except Exception:  # noqa: BLE001
        connection.rollback()


def index_version(connection: Any) -> str:
    """Numéro de la dernière collecte : change chaque nuit.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL (annulée en cas d'erreur).

    Returns
    -------
    str
        Le numéro, ou une chaîne vide s'il n'est pas lisible.
    """

    try:
        rows = fetch_rows(connection, INDEX_VERSION_SQL, {})
        return str(rows[0]["version"]) if rows else ""
    except Exception:  # noqa: BLE001
        connection.rollback()
        return ""
