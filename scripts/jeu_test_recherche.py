"""Base de test de la recherche, pour rejouer l'évaluation en CI.

**Provenance** : nouveau script écrit pour SortirIA (16/10/2026).

L'évaluation des contraintes (`scripts/eval_recherche.py`, objectif 95 %)
tournait à la main sur la vraie base. Pour la rejouer à chaque push, la CI a
besoin d'une base **figée** : les mêmes événements, vus au même instant.

1. **Exporter** (sur le poste, une fois de temps en temps) : les sorties à
   venir de la base, leurs sources et les partenaires, dans
   `tests/data/jeu_recherche.json.gz`, avec l'instant de l'export :

       uv run python -m scripts.jeu_test_recherche --exporter

2. **Charger** (en CI) : schéma créé dans une base PostgreSQL vide dont le
   nom contient « test », puis les lignes de l'export :

       DATABASE_URL=postgresql://.../sortiria_test uv run python -m scripts.jeu_test_recherche --charger

3. **Évaluer** à l'instant de l'export, sans appel à Mistral :

       uv run python -m scripts.eval_recherche --jeu-test --seuil 0.95

Ce qui n'est **pas** exporté :
- les embeddings (la CI cherche sans vecteur : mots, types et filtres SQL ;
  c'est ce qui décide du respect des contraintes) ;
- les événements lus dans le programme PDF de Sète agglopôle (source
  `document`) : le jeu de test est publié avec le code, on n'y garde que des
  données sous licence ouverte (OpenAgenda, DATAtourisme) et les partenaires
  d'exemple, fictifs ; chaque ligne garde son attribution ;
- les journaux (questions, collectes, avis).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests" / "data" / "jeu_recherche.json.gz"
EXCLUDED_SOURCES = ("document",)
# Colonnes laissées de côté : l'embedding (gros, et la CI cherche sans
# vecteur) et le lien vers la collecte (table non exportée).
SKIPPED_COLUMNS = {"embedding", "run_id"}
FORMAT_VERSION = 1


def plain_value(value: Any) -> Any:
    """Valeur JSON : dates en ISO, décimaux en flottants.

    Parameters
    ----------
    value : Any
        Une valeur lue en base.

    Returns
    -------
    Any
        La même valeur, convertie si besoin pour être écrite en JSON.
    """

    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def fetch_table(connection: Any, sql: str, params: Any = None) -> dict[str, Any]:
    """Exécute une requête et renvoie ses colonnes et ses lignes, prêtes pour le JSON.

    Les colonnes de SKIPPED_COLUMNS (embedding, run_id) sont retirées.

    Parameters
    ----------
    connection : Any
        Connexion psycopg2 ouverte.
    sql : str
        Requête SELECT à exécuter.
    params : Any
        Paramètres de la requête. Par défaut None.

    Returns
    -------
    dict[str, Any]
        Les noms de colonnes sous `colonnes` et les lignes converties sous `lignes`.
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        keep = [index for index, name in enumerate(columns) if name not in SKIPPED_COLUMNS]
        rows = [[plain_value(row[index]) for index in keep] for row in cursor.fetchall()]
    return {"colonnes": [columns[index] for index in keep], "lignes": rows}


def export_dataset(connection: Any, path: Path = DATASET,
                   excluded: Sequence[str] = EXCLUDED_SOURCES) -> dict[str, int]:
    """Écrit les sorties à venir, leurs sources et les partenaires.

    Seuls les événements marqués comme sorties et pas encore terminés sont gardés,
    hors sources exclues ; on n'exporte que les sources réellement utilisées.

    Parameters
    ----------
    connection : Any
        Connexion psycopg2 à la base à exporter.
    path : Path
        Fichier `.json.gz` à écrire. Par défaut `tests/data/jeu_recherche.json.gz`.
    excluded : Sequence[str]
        Sources à ne pas exporter. Par défaut la source `document`.

    Returns
    -------
    dict[str, int]
        Le nombre de lignes exportées par table.
    """

    # Noter l'instant de l'export : l'évaluation se placera à ce moment-là
    with connection.cursor() as cursor:
        cursor.execute("SELECT now()")
        now = cursor.fetchone()[0]
    # Lire les sorties à venir, puis leurs sources et les partenaires
    events = fetch_table(connection, """
        SELECT * FROM sortiria.events
        WHERE is_outing AND COALESCE(end_at, start_at) >= now() AND source <> ALL (%(excluded)s)
        ORDER BY start_at, event_id""", {"excluded": list(excluded)})
    source_index = events["colonnes"].index("source")
    used = sorted({row[source_index] for row in events["lignes"]})
    sources = fetch_table(connection, "SELECT * FROM sortiria.sources WHERE source_id = ANY (%(used)s) "
                                      "ORDER BY source_id", {"used": used})
    partners = fetch_table(connection, "SELECT * FROM sortiria.partners ORDER BY partner_id")
    # Écrire le tout dans un JSON compressé
    dataset = {"version": FORMAT_VERSION, "maintenant": plain_value(now),
               "tables": {"sources": sources, "partners": partners, "events": events}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(dataset, handle, ensure_ascii=False, separators=(",", ":"))
    return {name: len(table["lignes"]) for name, table in dataset["tables"].items()}


def read_dataset(path: Path = DATASET) -> dict[str, Any]:
    """Lit le jeu de test compressé.

    Parameters
    ----------
    path : Path
        Fichier `.json.gz`. Par défaut `tests/data/jeu_recherche.json.gz`.

    Returns
    -------
    dict[str, Any]
        Le contenu : `version`, `maintenant` et `tables`.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def snapshot_time(path: Path = DATASET) -> datetime:
    """Instant de l'export : l'évaluation se place à ce moment-là.

    Parameters
    ----------
    path : Path
        Fichier du jeu de test. Par défaut `tests/data/jeu_recherche.json.gz`.

    Returns
    -------
    datetime
        L'instant `maintenant` enregistré lors de l'export.
    """

    return datetime.fromisoformat(read_dataset(path)["maintenant"])


def is_test_database(url: str) -> bool:
    """Garde-fou : le chargement efface le schéma ; seulement une base « test ».

    Parameters
    ----------
    url : str
        Adresse de connexion PostgreSQL.

    Returns
    -------
    bool
        True si le nom de la base (dernier segment du chemin) contient « test ».
    """

    from urllib.parse import urlsplit

    return "test" in urlsplit(url).path.rsplit("/", 1)[-1]


def load_dataset(connection: Any, path: Path = DATASET) -> dict[str, int]:
    """Recrée le schéma et charge l'export (base de test vide).

    Le schéma `sortiria` est supprimé puis recréé : à n'appeler que sur une base
    de test (voir `is_test_database`). Les colonnes `jsonb` sont enveloppées pour
    psycopg2, et le vocabulaire des titres est recalculé à la fin.

    Parameters
    ----------
    connection : Any
        Connexion psycopg2 à la base de test ; passée en autocommit.
    path : Path
        Fichier du jeu de test. Par défaut `tests/data/jeu_recherche.json.gz`.

    Returns
    -------
    dict[str, int]
        Le nombre de lignes lues dans l'export, par table.
    """

    from psycopg2.extras import Json

    from scripts.init_db import apply_schema

    # Repartir d'un schéma neuf
    dataset = read_dataset(path)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS sortiria CASCADE")
    apply_schema(connection)
    # Insérer les lignes table par table, dans l'ordre des clés étrangères
    counts = {}
    with connection.cursor() as cursor:
        for name in ("sources", "partners", "events"):
            table = dataset["tables"][name]
            cursor.execute("SELECT column_name FROM information_schema.columns "
                           "WHERE table_schema = 'sortiria' AND table_name = %s AND data_type = 'jsonb'", (name,))
            json_columns = {row[0] for row in cursor.fetchall()}
            columns = table["colonnes"]
            placeholders = ", ".join(["%s"] * len(columns))
            # Les sources déclarées par le schéma existent déjà : gardées telles quelles.
            sql = (f"INSERT INTO sortiria.{name} ({', '.join(columns)}) VALUES ({placeholders}) "
                   "ON CONFLICT DO NOTHING")
            for row in table["lignes"]:
                cursor.execute(sql, [Json(value) if column in json_columns and value is not None else value
                                     for column, value in zip(columns, row, strict=True)])
            counts[name] = len(table["lignes"])
        # Recalculer le vocabulaire des titres à partir des événements chargés
        cursor.execute("SELECT sortiria.refresh_title_vocabulary()")
    return counts


def main() -> None:
    """Exporte la base vers le jeu de test, ou charge le jeu de test dans une base « test ».

    Raises
    ------
    SystemExit
        Si `--charger` est demandé alors que DATABASE_URL ne vise pas une base « test ».
    """
    # Lire les arguments : exporter ou charger
    parser = argparse.ArgumentParser(description="Base de test de la recherche (export, chargement en CI).")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--exporter", action="store_true", help="Exporte la base (DATABASE_URL) vers le jeu de test.")
    action.add_argument("--charger", action="store_true", help="Charge le jeu de test dans une base « test » vide.")
    parser.add_argument("--fichier", type=Path, default=DATASET, help="Fichier du jeu de test.")
    args = parser.parse_args()

    # Charger l'environnement et vérifier le garde-fou avant de se connecter
    from config.environment import load_environment

    load_environment()
    import psycopg2

    url = os.environ["DATABASE_URL"]
    if args.charger and not is_test_database(url):
        raise SystemExit("Refusé : le chargement efface le schéma ; DATABASE_URL doit viser une base « test ».")
    # Exécuter l'action demandée, puis fermer la connexion dans tous les cas
    connection = psycopg2.connect(url)
    try:
        if args.exporter:
            counts = export_dataset(connection, args.fichier)
            connection.commit()
            size = args.fichier.stat().st_size / 1_000_000
            print(f"Jeu de test écrit : {args.fichier.relative_to(ROOT)} ({size:.1f} Mo) : {counts}")
        else:
            counts = load_dataset(connection, args.fichier)
            print(f"Jeu de test chargé (instant {snapshot_time(args.fichier):%d/%m/%Y %H:%M}) : {counts}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
