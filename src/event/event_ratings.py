"""Notes des clients sur les événements : étoiles, commentaire modéré, anonymat.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Il sert
l'API (`app/ratings_api.py`) et l'agent de collecte (modération des
commentaires en attente). La table et les règles sont dans
`sql/sortiria/04_rag.sql` (`event_ratings`) ; les moyennes par lieu et par
partenaire dans `05_views.sql`.

Règles :

- 1 à 5 étoiles, commentaire facultatif de 300 caractères au plus ;
- notation ouverte du début de l'événement à 7 jours après sa fin (vérifié
  dans la requête SQL elle-même : impossible de noter hors délai) ;
- une note par client et par événement, modifiable ;
- **anonymat** : le client est représenté par une empreinte SHA-256 salée de
  son identifiant de session (`RATING_SALT` dans .env) ; aucun nom, aucun
  courriel n'est stocké (RGPD : minimisation) ;
- **modération** : le commentaire passe par Mistral Moderation avant d'être
  publié ; refusé s'il est signalé ; en attente si le service ne répond pas
  (l'agent de collecte le reprend la nuit suivante).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_COMMENT_CHARS = 300
STATUS_NONE = "aucun"
STATUS_PENDING = "en_attente"
STATUS_PUBLISHED = "publie"
STATUS_REFUSED = "refuse"
DEFAULT_MODERATION_MODEL = "mistral-moderation-latest"
MODERATION_URL = "https://api.mistral.ai/v1/moderations"

# Résultat de la modération : (statut du commentaire, détail des catégories).
Moderation = tuple[str, dict[str, Any]]


class RatingInput(BaseModel):
    """Note envoyée par un client (validée avant tout accès à la base)."""

    stars: int = Field(ge=1, le=5)
    comment: str = ""

    @field_validator("comment")
    @classmethod
    def short_comment(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) > MAX_COMMENT_CHARS:
            raise ValueError(f"commentaire de {MAX_COMMENT_CHARS} caractères au plus")
        return value


def user_key(raw_identifier: str, salt: str | None = None) -> str:
    """Empreinte anonyme d'un client : SHA-256(sel + identifiant de session).

    Le même client garde la même empreinte (une note par événement), mais on
    ne peut pas remonter à l'identifiant sans le sel, qui reste dans le .env.
    """

    identifier = str(raw_identifier or "").strip()
    if not identifier:
        raise ValueError("identifiant de client manquant")
    salt = salt if salt is not None else os.getenv("RATING_SALT", "")
    if not salt:
        # Sans sel, l'empreinte se recalcule depuis l'identifiant de session :
        # l'anonymat promis n'est plus garanti. On le dit, une fois.
        _warn_missing_salt()
    return hashlib.sha256(f"{salt}:{identifier}".encode()).hexdigest()


_SALT_WARNED = False


def _warn_missing_salt() -> None:
    global _SALT_WARNED  # un seul avertissement par processus
    if not _SALT_WARNED:
        _SALT_WARNED = True
        import logging

        logging.getLogger(__name__).warning("RATING_SALT absent du .env : les empreintes des clients ne sont pas salées.")


def moderate_with_mistral(text: str) -> Moderation:
    """Modère un commentaire avec Mistral Moderation.

    Publié si aucune catégorie n'est signalée, refusé sinon. Si le service ne
    répond pas : en attente (repris par l'agent la nuit suivante).
    """

    if not text:
        return STATUS_NONE, {}
    import httpx

    try:
        response = httpx.post(
            MODERATION_URL,
            headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
            json={"model": os.getenv("MODERATION_MODEL", DEFAULT_MODERATION_MODEL), "input": [text]},
            timeout=10,
        )
        response.raise_for_status()
        categories = response.json()["results"][0]["categories"]
    except (KeyError, IndexError, ValueError, httpx.HTTPError):
        return STATUS_PENDING, {}
    flagged = sorted(name for name, value in categories.items() if value)
    return (STATUS_REFUSED if flagged else STATUS_PUBLISHED), {"flagged": flagged}


def submit_rating(
    connection: Any,
    event_id: str,
    raw_user: str,
    stars: int,
    comment: str = "",
    moderate: Callable[[str], Moderation] = moderate_with_mistral,
) -> dict[str, Any]:
    """Enregistre (ou met à jour) la note d'un client. Renvoie le résultat.

    `status` vaut « enregistree », ou « hors_delai » si l'événement n'existe
    pas ou n'est pas notable (pas commencé, ou fini depuis plus de 7 jours).
    """

    from src.event import event_repository as repository

    rating = RatingInput(stars=stars, comment=comment)
    comment_status, details = moderate(rating.comment) if rating.comment else (STATUS_NONE, {})
    rating_id = repository.upsert_rating(connection, {
        "event_id": event_id,
        "user_key": user_key(raw_user),
        "stars": rating.stars,
        "comment": rating.comment,
        "comment_status": comment_status,
        "moderation": json.dumps(details, ensure_ascii=False),
    })
    if rating_id is None:
        return {"status": "hors_delai", "event_id": event_id}
    return {"status": "enregistree", "event_id": event_id, "rating_id": rating_id, "comment_status": comment_status}


def summarize_ratings(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Moyenne et commentaires publiés d'un événement (pour l'API)."""

    if not rows:
        return {"ratings": 0, "average_stars": None, "comments": []}
    return {
        "ratings": len(rows),
        "average_stars": round(sum(int(row["stars"]) for row in rows) / len(rows), 1),
        "comments": [
            {"stars": int(row["stars"]), "comment": row["comment"], "date": str(row["updated_at"])[:10]}
            for row in rows if row.get("comment_status") == STATUS_PUBLISHED and row.get("comment")
        ],
    }
