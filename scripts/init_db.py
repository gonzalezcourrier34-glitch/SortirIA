"""Crée ou met à jour le schéma PostgreSQL de SortirIA.

**Provenance** : nouveau script écrit pour SortirIA (14/10/2026).

Exécute dans l'ordre les fichiers `sql/sortiria/*.sql`. Ils sont réexécutables :
on peut relancer ce script après chaque modification du schéma, les données
déjà en base sont conservées.

Au premier démarrage d'une base vide, docker compose exécute déjà ces fichiers
(ils sont montés dans `/docker-entrypoint-initdb.d`) : ce script sert pour une
base existante, ou pour une base hébergée (Azure).

Utilisation :

    uv run python -m scripts.init_db                 # DATABASE_URL du .env
    uv run python -m scripts.init_db --verifier      # liste les tables et vues
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "sortiria"

EXPECTED_TABLES = (
    "sources", "partners", "partner_imports", "events", "collect_runs",
    "collect_run_sources", "rag_requests", "feedback", "query_cache",
)
EXPECTED_VIEWS = ("v_upcoming_events", "v_coverage", "v_collect_health", "v_daily_quality")


def wait_for_database(url: str, timeout_s: float = 90.0, pause_s: float = 2.0) -> Any:
    """Ouvre une connexion, en réessayant tant que la base démarre.

    Juste après `docker compose up`, le conteneur est « Started » mais
    PostgreSQL n'est pas encore prêt : au premier démarrage, il crée la base,
    exécute les fichiers de `sql/sortiria/` puis redémarre. Pendant ce temps,
    le port 5432 accepte la connexion puis la ferme (« server closed the
    connection unexpectedly »). On réessaie donc jusqu'à `timeout_s` secondes.
    """

    import psycopg2

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return psycopg2.connect(url)
        except psycopg2.OperationalError as error:
            if time.monotonic() >= deadline:
                raise
            print(f"Base pas encore prête, nouvel essai dans {pause_s:.0f} s ({str(error).splitlines()[0]})")
            time.sleep(pause_s)


def schema_files(sql_dir: Path = SQL_DIR) -> list[Path]:
    """Fichiers SQL du schéma, dans l'ordre de leur numéro."""

    return sorted(sql_dir.glob("[0-9][0-9]_*.sql"))


def apply_schema(connection: Any, sql_dir: Path = SQL_DIR) -> list[str]:
    """Exécute chaque fichier. Renvoie les noms des fichiers appliqués.

    La connexion doit être en autocommit : chaque fichier gère lui-même sa
    transaction (BEGIN ... COMMIT), si bien qu'une erreur annule le fichier
    en cours sans toucher aux précédents.
    """

    applied = []
    with connection.cursor() as cursor:
        for path in schema_files(sql_dir):
            cursor.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    return applied


def list_schema_objects(connection: Any) -> dict[str, list[str]]:
    """Tables et vues présentes dans le schéma `sortiria`."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'sortiria' ORDER BY table_name"
        )
        rows = cursor.fetchall()
    return {
        "tables": [name for name, kind in rows if kind == "BASE TABLE"],
        "views": [name for name, kind in rows if kind == "VIEW"],
    }


def missing_objects(found: dict[str, list[str]]) -> list[str]:
    """Objets attendus mais absents (liste vide = schéma complet)."""

    return [name for name in EXPECTED_TABLES if name not in found["tables"]] + [
        name for name in EXPECTED_VIEWS if name not in found["views"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verifier", action="store_true", help="liste les tables et vues sans rien modifier")
    args = parser.parse_args()

    load_dotenv()
    connection = wait_for_database(os.environ["DATABASE_URL"])
    connection.autocommit = True
    try:
        if not args.verifier:
            for name in apply_schema(connection):
                print(f"appliqué : {name}")
        found = list_schema_objects(connection)
    finally:
        connection.close()

    print(f"tables : {', '.join(found['tables'])}")
    print(f"vues   : {', '.join(found['views'])}")
    missing = missing_objects(found)
    if missing:
        print(f"MANQUANT : {', '.join(missing)}")
        return 1
    print("Schéma complet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
