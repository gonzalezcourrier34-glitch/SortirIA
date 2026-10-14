"""Traçage JSONL des appels du pipeline RAG (une ligne par requête).

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/trace_service.py` (commit 18f7ce1).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


class TraceService:
    """
    Service simple de traçage JSONL pour le pipeline RAG.

    Chaque appel écrit une ligne JSON indépendante dans un fichier .jsonl.
    Ce format est pratique pour :
    - accumuler les traces au fil du temps
    - relire une exécution précise
    - parser ensuite le fichier facilement
    """

    def __init__(
        self,
        trace_file: str | Path = "artifacts/rag_trace.jsonl",
        enabled: bool = True,
    ) -> None:
        self.trace_file = Path(trace_file)
        self.enabled = enabled

    def _json_default(self, obj: Any) -> Any:
        """
        Convertit les objets non sérialisables nativement en JSON.

        Cas gérés :
        - date / datetime -> ISO string
        - Path -> string
        - fallback générique -> string
        """
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()

        if isinstance(obj, Path):
            return str(obj)

        return str(obj)

    def write_trace(self, payload: dict[str, Any]) -> None:
        """
        Ajoute une trace au fichier JSONL.

        Si le service est désactivé, aucune écriture n'est effectuée.
        """
        if not self.enabled:
            return

        row = {
            "timestamp_utc": datetime.utcnow().isoformat(),
            **payload,
        }

        self.trace_file.parent.mkdir(parents=True, exist_ok=True)

        with self.trace_file.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=self._json_default,
                )
                + "\n"
            )