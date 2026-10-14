"""Middlewares HTTP de Chess Agent.

Ce module centralise les traitements transversaux appliqués aux
requêtes HTTP.

Il est responsable de créer un identifiant de corrélation unique pour
chaque requête afin de faciliter le rapprochement entre les réponses
API et les journaux applicatifs.

Il ne contient aucune logique métier.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.constants import REQUEST_ID_FIELD

# Middleware


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ajoute un identifiant de corrélation à chaque requête HTTP."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Ajoute le request_id au contexte de la requête."""

        request_id = str(uuid4())

        setattr(request.state, REQUEST_ID_FIELD, request_id)

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id

        return response
