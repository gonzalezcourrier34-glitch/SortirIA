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
    """Au plus `limit` appels par `window_s` secondes et par clé (fenêtre glissante).

    Chaque clé (adresse d'un visiteur, ou « tous » pour le plafond global) garde
    l'heure de ses appels récents ; les plus anciennes sortent de la fenêtre au fil
    du temps. Le compteur est protégé par un verrou : FastAPI exécute les
    dépendances synchrones dans plusieurs fils.

    Parameters
    ----------
    limit : int
        Nombre d'appels permis dans la fenêtre ; 0 ou moins désactive la limite.
    window_s : float
        Durée de la fenêtre glissante, en secondes.
    clock : Callable[[], float]
        L'horloge utilisée ; par défaut `time.monotonic` (remplaçable dans les tests).
    """

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        """Prépare un compteur vide (paramètres : voir la classe)."""
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """Compte un appel. Renvoie 0 s'il est accepté, sinon les secondes à attendre.

        Un appel refusé n'est pas compté. Au-delà de `MAX_TRACKED_KEYS` clés, les
        moins récemment vues sont oubliées pour borner la mémoire.

        Parameters
        ----------
        key : str
            La clé à compter (adresse du visiteur, ou « tous »).

        Returns
        -------
        float
            0 si l'appel est accepté ; sinon les secondes à attendre (au moins 1).
        """

        if self.limit <= 0:
            return 0.0
        now = self.clock()
        with self._lock:
            # Retirer de la fenêtre les appels trop anciens
            calls = self._calls.pop(key, None) or deque()
            while calls and calls[0] <= now - self.window_s:
                calls.popleft()
            # Refuser si la limite est atteinte, sans compter l'appel
            if len(calls) >= self.limit:
                self._calls[key] = calls
                return max(calls[0] + self.window_s - now, 1.0)
            # Accepter : noter l'appel, puis oublier les clés les plus anciennes
            calls.append(now)
            self._calls[key] = calls            # remis en fin : les plus récents restent
            while len(self._calls) > MAX_TRACKED_KEYS:
                self._calls.pop(next(iter(self._calls)))
            return 0.0

    def reset(self) -> None:
        """Oublie tous les appels comptés (utile entre deux tests)."""
        with self._lock:
            self._calls.clear()


def env_int(name: str, default: int) -> int:
    """Lit un entier dans une variable d'environnement.

    Parameters
    ----------
    name : str
        Nom de la variable (par exemple `LIMITE_RECHERCHES_MINUTE`).
    default : int
        Valeur utilisée si la variable est absente ou n'est pas un entier.

    Returns
    -------
    int
        L'entier lu, ou `default`.
    """
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
    """Adresse du visiteur : première de `X-Forwarded-For`, sinon celle de la connexion.

    Parameters
    ----------
    request : Request
        La requête FastAPI reçue.

    Returns
    -------
    str
        L'adresse IP du visiteur, ou « inconnu » si elle n'est pas connue.
    """

    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "inconnu"


def refuse(wait_s: float, message: str) -> HTTPException:
    """Construit la réponse 429 avec l'en-tête `Retry-After` (secondes arrondies au-dessus).

    Parameters
    ----------
    wait_s : float
        Secondes à attendre avant de réessayer.
    message : str
        Le message affiché au visiteur.

    Returns
    -------
    HTTPException
        L'exception 429, à lever par l'appelant.
    """
    return HTTPException(status_code=429, detail=message, headers={"Retry-After": str(int(wait_s + 0.999))})


def limit_search(request: Request) -> None:
    """Dépendance FastAPI de la recherche : par visiteur, puis pour tout le service.

    Parameters
    ----------
    request : Request
        La requête FastAPI reçue.

    Raises
    ------
    HTTPException
        Code 429 si le visiteur ou le service a dépassé sa limite.
    """

    key = client_key(request)
    for limiter in (SEARCH_PER_MINUTE, SEARCH_PER_HOUR):
        wait = limiter.hit(key)
        if wait:
            raise refuse(wait, "Trop de recherches d'affilée : réessayez dans un instant.")
    wait = SEARCH_GLOBAL_PER_HOUR.hit("tous")
    if wait:
        raise refuse(wait, "SortirIA reçoit beaucoup de questions en ce moment : réessayez un peu plus tard.")


def limit_writes(request: Request) -> None:
    """Dépendance FastAPI des écritures (avis et pouces) : limite par visiteur.

    Parameters
    ----------
    request : Request
        La requête FastAPI reçue.

    Raises
    ------
    HTTPException
        Code 429 si le visiteur a dépassé sa limite.
    """

    wait = WRITES_PER_MINUTE.hit(client_key(request))
    if wait:
        raise refuse(wait, "Trop d'envois d'affilée : réessayez dans un instant.")


def limit_reads(request: Request) -> None:
    """Dépendance FastAPI des lectures (détail, sources, partenaires) : par visiteur, plafond large.

    Parameters
    ----------
    request : Request
        La requête FastAPI reçue.

    Raises
    ------
    HTTPException
        Code 429 si le visiteur a dépassé sa limite.
    """

    wait = READS_PER_MINUTE.hit(client_key(request))
    if wait:
        raise refuse(wait, "Trop de demandes d'affilée : réessayez dans un instant.")
