"""
Gestion de la sécurité de l'API.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/security.py` (commit 18f7ce1).

Ce module implémente un mécanisme simple d'authentification
par clé API pour protéger certains endpoints de l'application.

La clé API attendue est définie dans la variable d'environnement
`API_KEY` et doit être envoyée par le client dans le header HTTP :

    x-api-key: <votre_token>

Cette vérification est utilisée dans FastAPI via le système
de dépendances (`Depends` / `Security`).
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import API_KEY

api_key_header = APIKeyHeader(
    name="x-api-key",
    scheme_name="API Key",
    description="Token personnel requis pour accéder à l'API",
    auto_error=False,
)


def require_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """
    Vérifie la validité de la clé API envoyée par le client.

    Cette fonction est utilisée comme dépendance FastAPI afin
    de protéger certains endpoints. Elle compare la clé reçue
    dans le header `x-api-key` avec la clé définie dans les
    variables d'environnement du serveur.

    La comparaison utilise `secrets.compare_digest` afin
    d'éviter les attaques par timing.

    Parameters
    ----------
    api_key : str | None
        Clé API transmise par le client dans le header HTTP `x-api-key`,
        injectée par FastAPI ; `None` si le header est absent.

    Returns
    -------
    str
        La clé API validée.

    Raises
    ------
    HTTPException
        500 Internal Server Error si la clé du serveur (`API_KEY`) n'est pas
        configurée ; 401 Unauthorized si la clé API est absente ou invalide.
    """
    
    # Refuser tout si le serveur n'a pas de clé : mieux vaut une erreur qu'une API ouverte
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La clé API du serveur n'est pas configurée.",
        )
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token manquant.",
        )

    if not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide.",
        )

    return api_key