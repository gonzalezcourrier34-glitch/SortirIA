from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """
    Formatter JSON simple pour produire des logs techniques structurés.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
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

        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    *,
    level: int = logging.INFO,
    write_file: bool = False,
    log_file_path: str = "logs/app.jsonl",
) -> None:
    """
    Configure le logging global de l'application.

    Parameters
    ----------
    level : int, default=logging.INFO
        Niveau racine de logging.
    write_file : bool, default=False
        Si True, écrit également les logs dans un fichier JSONL.
    log_file_path : str, default="logs/app.jsonl"
        Chemin du fichier de logs.
    """
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JsonFormatter())
    root_logger.addHandler(console_handler)

    if write_file:
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(file_handler)