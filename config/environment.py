"""
Gestion centralisée de l'environnement et des secrets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `config/environment.py` (commit 93632c5).

Ce module charge le fichier .env, lit et convertit les variables
d'environnement, puis résout les Docker Secrets montés dans /run/secrets.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from dotenv import load_dotenv

from config.paths import BASE_DIR, DOCKER_SECRETS_DIR, LOCAL_SECRETS_DIR


# Configuration
ENV_FILE = BASE_DIR / ".env"
SECRET_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

PLACEHOLDER_VALUES: frozenset[str] = frozenset({
    "", "change_me", "example", "none", "null",
    "ta_cle_api", "xxx", "your_api_key"
})


# Exceptions
class SecretError(RuntimeError):
    """Erreur générique liée à la résolution d'un secret."""


class SecretNotFoundError(SecretError):
    """Signale qu'un secret requis est absent."""


class SecretEmptyError(SecretError):
    """Signale qu'un secret existe mais ne contient aucune valeur valide."""


class SecretNameError(SecretError):
    """Signale qu'un nom de Docker Secret est invalide."""


# Chargement de l'environnement
def load_environment() -> None:
    """Charge le fichier .env sans écraser les variables existantes."""

    load_dotenv(dotenv_path=ENV_FILE, override=False)


load_environment()


# Validation commune
def is_valid_secret(value: str | None) -> bool:
    """Vérifie qu'une valeur sensible est présente et non factice."""

    return bool(value and value.strip().lower() not in PLACEHOLDER_VALUES)


def validate_secret_name(secret_name: str) -> str:
    """Valide et retourne un nom de Docker Secret normalisé."""

    normalized_name = str(secret_name or "").strip()
    if not SECRET_NAME_PATTERN.fullmatch(normalized_name):
        raise SecretNameError(
            f"Nom de Docker Secret invalide : {normalized_name!r}. "
            "Le format attendu est snake_case en minuscules."
        )
    return normalized_name


# Variables d'environnement
def get_environment_variable(name: str, default: str | None = None) -> str | None:
    """Retourne une variable d'environnement ou sa valeur par défaut."""

    return os.getenv(name, default)


def get_integer_environment_variable(name: str, default: int) -> int:
    """Retourne une variable d'environnement convertie en entier."""

    value = get_environment_variable(name, str(default))
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"La variable '{name}' doit contenir un entier.") from error


def get_float_environment_variable(name: str, default: float) -> float:
    """Retourne une variable d'environnement convertie en nombre."""

    value = get_environment_variable(name, str(default))
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"La variable '{name}' doit contenir un nombre.") from error


def get_boolean_environment_variable(name: str, default: bool) -> bool:
    """Retourne une variable d'environnement convertie en booléen."""

    value = (get_environment_variable(name, str(default)) or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"La variable '{name}' doit contenir un booléen.")


def require_environment_variables(variable_names: Iterable[str]) -> None:
    """Vérifie que toutes les variables d'environnement demandées sont valides."""

    missing = sorted({
        name for name in variable_names
        if not is_valid_secret(get_environment_variable(name))
    })
    if missing:
        raise RuntimeError(
            "Variables d'environnement manquantes ou invalides : "
            + ", ".join(missing)
        )


# Secrets
def get_secret_directories(
    secrets_directory: str | Path | None = None
) -> tuple[Path, ...]:
    """Retourne les répertoires de secrets dans leur ordre de priorité."""

    if secrets_directory is not None:
        return (Path(secrets_directory),)

    directories = (
        DOCKER_SECRETS_DIR,
        LOCAL_SECRETS_DIR
    )
    return tuple(dict.fromkeys(directories))


def get_secret_path(
    secret_name: str,
    *,
    secrets_directory: str | Path
) -> Path:
    """Construit le chemin sécurisé d'un secret."""

    normalized_name = validate_secret_name(secret_name)
    directory = Path(secrets_directory)
    secret_path = directory / normalized_name

    try:
        resolved_directory = directory.resolve(strict=False)
        resolved_path = secret_path.resolve(strict=False)
        resolved_path.relative_to(resolved_directory)
    except (OSError, ValueError) as error:
        raise SecretNameError(
            f"Chemin de secret invalide : {secret_path}."
        ) from error

    return secret_path


def read_secret_file(secret_path: Path) -> str | None:
    """Lit et valide un fichier secret."""

    if not secret_path.is_file():
        return None

    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    return value if is_valid_secret(value) else None


def get_secret(
    secret_name: str | None,
    default: str | None = None,
    *,
    required: bool = False,
    secrets_directory: str | Path | None = None
) -> str | None:
    """Résout un secret depuis Docker Secrets puis le dossier local."""

    if not secret_name:
        if required:
            raise SecretNotFoundError(
                "Aucun nom de secret n'a été fourni."
            )
        return default

    normalized_name = validate_secret_name(secret_name)
    searched_paths: list[Path] = []

    for directory in get_secret_directories(secrets_directory):
        secret_path = get_secret_path(
            normalized_name,
            secrets_directory=directory
        )
        searched_paths.append(secret_path)

        if value := read_secret_file(secret_path):
            return value

    if required:
        searched_locations = ", ".join(
            str(path) for path in searched_paths
        )
        raise SecretNotFoundError(
            f"Secret introuvable : {normalized_name}. "
            f"Emplacements vérifiés : {searched_locations}."
        )

    return default


def require_secrets(
    secret_names: Iterable[str],
    *,
    secrets_directory: str | Path | None = None
) -> None:
    """Vérifie que tous les secrets demandés sont disponibles."""

    missing: list[str] = []

    for secret_name in sorted(set(secret_names)):
        try:
            get_secret(
                secret_name,
                required=True,
                secrets_directory=secrets_directory
            )
        except SecretError:
            missing.append(secret_name)

    if missing:
        raise SecretNotFoundError(
            "Secrets manquants ou invalides : "
            + ", ".join(missing)
        )


# Résolution unifiée
def get_secret_or_environment(
    *,
    secret_name: str | None = None,
    environment_variable: str | None = None,
    default: str | None = None,
    required: bool = False,
    secrets_directory: str | Path | None = None
) -> str | None:
    """
    Résout une valeur sensible.

    Priorité : Docker Secret, secret local, variable d'environnement,
    valeur par défaut.
    """

    value = get_secret(
        secret_name,
        required=False,
        secrets_directory=secrets_directory
    )
    if is_valid_secret(value):
        return value

    value = (
        get_environment_variable(environment_variable)
        if environment_variable else None
    )
    if is_valid_secret(value):
        return value

    if is_valid_secret(default):
        return default

    if required:
        references = [
            reference
            for reference in (
                f"secret={secret_name}" if secret_name else None,
                f"env={environment_variable}" if environment_variable else None
            )
            if reference
        ]
        raise SecretNotFoundError(
            "Secret requis introuvable"
            + (f" ({', '.join(references)})." if references else ".")
        )

    return default