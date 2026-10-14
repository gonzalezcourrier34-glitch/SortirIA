"""Journal d'audit des interactions de triage.

Une ligne JSON par appel à /triage (succès ou moteur indisponible), pour
rejouer et contrôler a posteriori une décision : quel modèle, quel prompt,
quelle priorité, quand, en combien de temps.

Minimisation des données (RGPD) : le texte du patient n'est jamais écrit.
Seule son empreinte SHA-256 est conservée ; elle permet de vérifier qu'une
entrée donnée a bien produit une décision donnée, sans stocker le contenu.

Configuration :
- AUDIT_LOG_ENABLED : "true" (défaut) ou "false" ;
- AUDIT_LOG_PATH    : fichier JSONL, défaut logs/audit.jsonl.

Une erreur d'écriture du journal n'interrompt jamais la réponse au patient :
elle est signalée dans les logs applicatifs.
"""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from src.api.schemas import TriageRequest

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _enabled() -> bool:
    return os.getenv("AUDIT_LOG_ENABLED", "true").lower() not in {"0", "false", "no"}


def _path() -> Path:
    return Path(os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl"))


def input_fingerprint(request: TriageRequest) -> str:
    """Empreinte SHA-256 de l'entrée complète (langue + champs texte)."""
    canonical = json.dumps(
        request.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record(
    *,
    request_id: str,
    timestamp: datetime,
    request: TriageRequest,
    backend: str,
    model_version: str,
    prompt_version: str,
    api_version: str,
    status: str,
    latency_ms: float,
    priority: str | None = None,
) -> None:
    """Ajoute une entrée au journal d'audit (append-only)."""
    if not _enabled():
        return

    entry = {
        "request_id": request_id,
        "timestamp": timestamp.isoformat(),
        "api_version": api_version,
        "backend": backend,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "language": request.language,
        "input_sha256": input_fingerprint(request),
        "input_chars": len(request.question) + len(request.context) + len(request.answer),
        "priority": priority,
        "status": status,
        "latency_ms": latency_ms,
    }

    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        logger.exception("Écriture du journal d'audit impossible (request_id=%s)", request_id)
