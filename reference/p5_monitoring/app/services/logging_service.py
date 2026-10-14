"""
Logging applicatif structuré + middleware HTTP.

Ce module contient :
- un formatter JSON pour les logs structurés
- une configuration globale du logging
- un middleware HTTP avec request_id et latence
- une exclusion des routes de healthcheck pour éviter le bruit en Docker
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import BENCHMARK_MODE


# =============================================================================
# Constantes
# =============================================================================

IGNORED_PATHS = {
    "/health",
    "/predict/health",
}


# =============================================================================
# Formatter JSON
# =============================================================================

class JsonFormatter(logging.Formatter):
    """
    Formatter JSON pour produire des logs structurés.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Convertit un LogRecord Python en chaîne JSON.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        extra_data = getattr(record, "extra_data", None)
        if isinstance(extra_data, dict):
            payload.update(extra_data)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# =============================================================================
# Configuration globale du logging
# =============================================================================

def setup_logging(
    *,
    level: int = logging.INFO,
    write_file: bool = False,
    log_file_path: str = "logs/app.jsonl",
    quiet_libraries: bool = True,
) -> None:
    """
    Configure le logging global de l'application.
    """
    root_logger = logging.getLogger()

    root_logger.handlers.clear()
    root_logger.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(JsonFormatter())
    root_logger.addHandler(console_handler)

    if write_file:
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(file_handler)

    if quiet_libraries:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("numexpr").setLevel(logging.WARNING)


# =============================================================================
# Middleware HTTP
# =============================================================================

logger = logging.getLogger("app.http")


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware de logging HTTP technique.

    En mode normal :
    - log début de requête
    - log fin de requête
    - ajoute X-Request-ID

    En mode benchmark :
    - ne log pas les requêtes réussies
    - log uniquement les exceptions

    Les routes de healthcheck sont toujours ignorées pour éviter le bruit Docker.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        """
        Intercepte une requête HTTP, mesure sa latence et ajoute X-Request-ID.
        """
        request_id = str(uuid.uuid4())
        start = time.perf_counter()

        method = request.method
        path = request.url.path
        query_params = str(request.query_params)
        client_host = request.client.host if request.client else None

        # Healthchecks Docker : pas de logs applicatifs.
        if path in IGNORED_PATHS:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        should_log_success = not BENCHMARK_MODE

        if should_log_success:
            logger.info(
                "HTTP request started",
                extra={
                    "extra_data": {
                        "event": "http_request_start",
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "query_params": query_params,
                        "client_host": client_host,
                    }
                },
            )

        try:
            response = await call_next(request)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)

            if should_log_success:
                logger.info(
                    "HTTP request completed",
                    extra={
                        "extra_data": {
                            "event": "http_request_success",
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "query_params": query_params,
                            "status_code": response.status_code,
                            "latency_ms": latency_ms,
                            "client_host": client_host,
                        }
                    },
                )

            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)

            logger.exception(
                "Unhandled HTTP error",
                extra={
                    "extra_data": {
                        "event": "http_request_exception",
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "query_params": query_params,
                        "latency_ms": latency_ms,
                        "client_host": client_host,
                        "error": str(exc),
                    }
                },
            )

            raise