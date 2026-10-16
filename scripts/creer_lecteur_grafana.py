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

import getpass
import os
from typing import Any

ROLE = "grafana_lecture"
SCHEMA = "sortiria"


def grant_statements(database: str) -> list[str]:
    """Droits du compte : se connecter, lire le schéma, et rien d'autre."""

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


def create_reader(connection: Any, password: str) -> None:
    """Crée le compte s'il manque, fixe son mot de passe et ses droits."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
        if cursor.fetchone() is None:
            cursor.execute(f"CREATE ROLE {ROLE} LOGIN")
        cursor.execute(f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD %s", (password,))
        cursor.execute("SELECT current_database()")
        database = cursor.fetchone()[0]
        for statement in grant_statements(database):
            cursor.execute(statement)


def main() -> None:
    from config.environment import load_environment

    load_environment()
    import psycopg2

    password = getpass.getpass(f"Mot de passe du compte {ROLE} : ")
    if len(password) < 12 or password != getpass.getpass("Encore une fois : "):
        raise SystemExit("Mots de passe différents, ou moins de 12 caractères : rien n'a été fait.")
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
