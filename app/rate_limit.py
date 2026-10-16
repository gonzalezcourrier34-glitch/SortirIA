"""Limite de débit des routes publiques (16/10/2026).

**Provenance** : nouveau module écrit pour SortirIA.

Les routes de l'interface sont ouvertes à tous, sans clé : sans limite, un
script pourrait poser des milliers de questions, chacune payée à Mistral. Deux
garde-fous, en mémoire (l'API tourne en une seule copie, `--max-replicas 1`) :

- **par visiteur** (son adresse IP) : un humain ne pose pas 12 questions en
  une minute ;
- **pour tout le service**, sur la recherche seulement : un plafond horaire
  qui borne le coût Mistral quoi qu'il arrive, même si un robot change
  d'adresse à chaque question.

Au-delà : réponse 429 avec l'en-tête `Retry-After` (secondes à attendre).

L'adresse du visiteur : en production, la requête passe par Static Web Apps
puis par l'entrée de Container Apps, qui l'ajoutent à `X-Forwarded-For`. On
prend la première adresse de cet en-tête, sinon celle de la connexion. Un
robot peut écrire un faux en-tête : c'est pour cela que le plafond global
existe.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request

MAX_TRACKED_KEYS = 10_000   # au-delà, les visiteurs les plus anciens sont oubliés


class SlidingWindowLimiter:
    """Au plus `limit` appels par `window_s` secondes et par clé (fenêtre glissante)."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """Compte un appel. Renvoie 0 s'il est accepté, sinon les secondes à attendre."""

        if self.limit <= 0:
            return 0.0
        now = self.clock()
        with self._lock:
            calls = self._calls.pop(key, None) or deque()
            while calls and calls[0] <= now - self.window_s:
                calls.popleft()
            if len(calls) >= self.limit:
                self._calls[key] = calls
                return max(calls[0] + self.window_s - now, 1.0)
            calls.append(now)
            self._calls[key] = calls            # remis en fin : les plus récents restent
            while len(self._calls) > MAX_TRACKED_KEYS:
                self._calls.pop(next(iter(self._calls)))
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Réglages (`.env` ou secrets Container Apps) ; 0 désactive une limite.
SEARCH_PER_MINUTE = SlidingWindowLimiter(env_int("LIMITE_RECHERCHES_MINUTE", 12), 60)
SEARCH_PER_HOUR = SlidingWindowLimiter(env_int("LIMITE_RECHERCHES_HEURE", 120), 3600)
SEARCH_GLOBAL_PER_HOUR = SlidingWindowLimiter(env_int("LIMITE_RECHERCHES_HEURE_TOTAL", 1000), 3600)
WRITES_PER_MINUTE = SlidingWindowLimiter(env_int("LIMITE_ECRITURES_MINUTE", 30), 60)
READS_PER_MINUTE = SlidingWindowLimiter(env_int("LIMITE_LECTURES_MINUTE", 120), 60)

ALL_LIMITERS = (SEARCH_PER_MINUTE, SEARCH_PER_HOUR, SEARCH_GLOBAL_PER_HOUR, WRITES_PER_MINUTE, READS_PER_MINUTE)


def client_key(request: Request) -> str:
    """Adresse du visiteur : première de `X-Forwarded-For`, sinon celle de la connexion."""

    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "inconnu"


def refuse(wait_s: float, message: str) -> HTTPException:
    return HTTPException(status_code=429, detail=message, headers={"Retry-After": str(int(wait_s + 0.999))})


def limit_search(request: Request) -> None:
    """Dépendance FastAPI de la recherche : par visiteur, puis pour tout le service."""

    key = client_key(request)
    for limiter in (SEARCH_PER_MINUTE, SEARCH_PER_HOUR):
        wait = limiter.hit(key)
        if wait:
            raise refuse(wait, "Trop de recherches d'affilée : réessayez dans un instant.")
    wait = SEARCH_GLOBAL_PER_HOUR.hit("tous")
    if wait:
        raise refuse(wait, "SortirIA reçoit beaucoup de questions en ce moment : réessayez un peu plus tard.")


def limit_writes(request: Request) -> None:
    """Avis et pouces : par visiteur."""

    wait = WRITES_PER_MINUTE.hit(client_key(request))
    if wait:
        raise refuse(wait, "Trop d'envois d'affilée : réessayez dans un instant.")


def limit_reads(request: Request) -> None:
    """Lectures (détail, sources, partenaires) : par visiteur, plafond large."""

    wait = READS_PER_MINUTE.hit(client_key(request))
    if wait:
        raise refuse(wait, "Trop de demandes d'affilée : réessayez dans un instant.")
