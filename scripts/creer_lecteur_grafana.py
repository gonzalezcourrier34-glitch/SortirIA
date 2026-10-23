"""Crée le compte en lecture seule qu'utilise Grafana sur la base de production.

**Provenance** : écrit pour SortirIA (16/10/2026).

Grafana tourne sur le poste (docker compose) ; pour suivre la production, il
lit la base Azure avec un compte qui ne peut **rien modifier** : il voit les
tables et les vues du schéma `sortiria`, en `SELECT` seulement.

À lancer une fois, avec la connexion d'administration de la base Azure dans
`DATABASE_URL` (comme pour `scripts.init_db`) ; le mot de passe du nouveau
compte est demandé au clavier, jamais affiché ni écrit dans un fichier :

    $env:DATABASE_URL = "<connexion d'administration de la base Azure>"
    uv run python -m scripts.creer_lecteur_grafana

Puis reporter ce mot de passe dans `AZURE_PG_PASSWORD` du `.env` et
redémarrer Grafana (`docker compose up -d grafana`). Relancer le script
change le mot de passe et remet les droits, sans rien casser.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets
from typing import Any

ROLE = "grafana_lecture"
SCHEMA = "sortiria"


def grant_statements(database: str) -> list[str]:
    """Droits du compte : se connecter, lire le schéma, et rien d'autre.

    Parameters
    ----------
    database : str
        Le nom de la base sur laquelle accorder la connexion.

    Returns
    -------
    list[str]
        Les requêtes SQL à exécuter : connexion, lecture du schéma (y compris
        les tables futures), délai maximal de 30 s et transactions en lecture seule.
    """

    return [
        f'GRANT CONNECT ON DATABASE "{database}" TO {ROLE}',
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {ROLE}",
        # Tables et vues créées plus tard par l'administrateur (init_db).
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES TO {ROLE}",
        # Garde-fou : une requête de tableau ne doit jamais bloquer la base.
        f"ALTER ROLE {ROLE} SET statement_timeout = '30s'",
        f"ALTER ROLE {ROLE} SET default_transaction_read_only = on",
    ]


def scram_sha256(password: str, iterations: int = 4096, salt: bytes | None = None) -> str:
    """Empreinte SCRAM-SHA-256 du mot de passe, au format de PostgreSQL.

    Le mot de passe en clair n'est jamais envoyé au serveur : seule cette
    empreinte part dans `ALTER ROLE … PASSWORD`, et c'est elle qui apparaîtrait
    dans un journal des requêtes (calcul de la RFC 5802, comme `psql \\password`).

    Parameters
    ----------
    password : str
        Le mot de passe en clair.
    iterations : int
        Le nombre d'itérations PBKDF2. Par défaut 4096, comme PostgreSQL.
    salt : bytes | None
        Le sel ; par défaut, 16 octets aléatoires (le fixer sert aux tests).

    Returns
    -------
    str
        L'empreinte « SCRAM-SHA-256$itérations:sel$StoredKey:ServerKey ».
    """

    # Dériver la clé salée, puis les clés client, stockée et serveur
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(data: bytes) -> str:
        """Encode des octets en base64 ASCII."""
        return base64.b64encode(data).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def create_reader(connection: Any, password: str) -> None:
    """Crée le compte s'il manque, fixe son mot de passe et ses droits.

    Peut être relancé sans risque : le compte existant est gardé, seuls son
    mot de passe et ses droits sont remis.

    Parameters
    ----------
    connection : Any
        Une connexion psycopg2 d'administration, en mode autocommit.
    password : str
        Le mot de passe en clair ; seule son empreinte SCRAM est envoyée.
    """

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
        if cursor.fetchone() is None:
            cursor.execute(f"CREATE ROLE {ROLE} LOGIN")
        cursor.execute(f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD %s", (scram_sha256(password),))
        cursor.execute("SELECT current_database()")
        database = cursor.fetchone()[0]
        for statement in grant_statements(database):
            cursor.execute(statement)


def main() -> None:
    """Demande le mot de passe au clavier puis crée ou met à jour le compte.

    Raises
    ------
    SystemExit
        Si les deux saisies diffèrent ou si le mot de passe a moins de
        12 caractères.
    """
    # Charger l'environnement (DATABASE_URL d'administration)
    from config.environment import load_environment

    load_environment()
    import psycopg2

    # Saisir deux fois le mot de passe, sans écho
    password = getpass.getpass(f"Mot de passe du compte {ROLE} : ")
    if len(password) < 12 or password != getpass.getpass("Encore une fois : "):
        raise SystemExit("Mots de passe différents, ou moins de 12 caractères : rien n'a été fait.")
    # Créer le compte et ses droits, puis fermer la connexion quoi qu'il arrive
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        connection.autocommit = True
        create_reader(connection, password)
    finally:
        connection.close()
    print(f"Compte {ROLE} prêt, en lecture seule sur le schéma {SCHEMA}. "
          "Reporter le mot de passe dans AZURE_PG_PASSWORD du .env, puis : docker compose up -d grafana")


if __name__ == "__main__":
    main()
