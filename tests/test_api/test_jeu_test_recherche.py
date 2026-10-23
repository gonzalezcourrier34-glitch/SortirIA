"""Base de test de la recherche pour la CI (`scripts/jeu_test_recherche.py`, 16/10/2026).

Les tests d'aller-retour (export puis chargement) ne tournent que si
SORTIRIA_TEST_DATABASE_URL vise une base jetable dont le nom contient « test ».
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.search_filters import PARIS
from app.search_service import SearchServices
from scripts import eval_recherche
from scripts import jeu_test_recherche as dataset

TEST_URL = os.environ.get("SORTIRIA_TEST_DATABASE_URL", "")


def test_loading_is_refused_outside_a_test_database() -> None:
    assert dataset.is_test_database("postgresql://u:p@localhost:5432/sortiria_test")
    assert not dataset.is_test_database("postgresql://u:p@sortiria-db.postgres.database.azure.com/sortiria")
    assert not dataset.is_test_database("postgresql://u@/sortiria?host=/tmp/pgtest")


def test_threshold_says_what_is_below() -> None:
    report = {"precision": 0.93, "comprehension": 1.0}
    assert eval_recherche.below_threshold(report, None) == ""
    assert eval_recherche.below_threshold(report, 0.95) == "précision 93.0% < 95%"


@pytest.fixture()
def db() -> Any:
    if not dataset.is_test_database(TEST_URL):
        pytest.skip("SORTIRIA_TEST_DATABASE_URL absente (ou base dont le nom ne contient pas « test »)")
    psycopg2 = pytest.importorskip("psycopg2")
    connection = psycopg2.connect(TEST_URL)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS sortiria CASCADE")
    from scripts.init_db import apply_schema

    apply_schema(connection)
    yield connection
    connection.close()


def insert_event(db: Any, event_id: str, start: datetime, **values: Any) -> None:
    row = {"event_id": event_id, "source": "openagenda", "title": "Concert de jazz", "category_norm": "concert",
           "city": "Sète", "start_at": start.isoformat(), "url": f"https://agenda.fr/{event_id}",
           "is_free": True, **values}
    with db.cursor() as cursor:
        cursor.execute(f"INSERT INTO sortiria.events ({', '.join(row)}) VALUES "
                       f"({', '.join(f'%({key})s' for key in row)})", row)


def test_export_then_load_then_evaluate_at_the_snapshot_time(db: Any, tmp_path: Path) -> None:
    now = datetime.now(PARIS)
    insert_event(db, "futur", (now + timedelta(days=5)).replace(hour=20, minute=30, second=0, microsecond=0))
    insert_event(db, "passe", now - timedelta(days=5), title="Concert passé")
    path = tmp_path / "jeu.json.gz"
    counts = dataset.export_dataset(db, path)
    assert counts["events"] == 1                     # seulement les sorties à venir
    assert "embedding" not in dataset.read_dataset(path)["tables"]["events"]["colonnes"]

    loaded = dataset.load_dataset(db, path)          # schéma recréé, puis les lignes
    assert loaded["events"] == 1
    # Comptes : les lignes insérées, pas les lignes lues (les sources déclarées
    # par le schéma existent déjà, ON CONFLICT DO NOTHING les écarte).
    read = len(dataset.read_dataset(path)["tables"]["sources"]["lignes"])
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM sortiria.sources")
        total = cursor.fetchone()[0]
    assert loaded["sources"] < read <= total
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM sortiria.title_vocabulary WHERE lexeme = 'jazz'")
        assert cursor.fetchone()[0] == 1

    frozen = dataset.snapshot_time(path).astimezone(PARIS)
    services = SearchServices(embed=None, write=None, now=lambda: frozen, log=False, cache=False)
    report = eval_recherche.evaluate(db, [{"question": "un concert gratuit à Sète",
                                           "compris": {"communes": ["sete"], "gratuit": True,
                                                       "types": ["concert"]}}], services)
    assert report["comprehension"] == 1.0 and report["precision"] == 1.0
    assert report["detail"][0]["titres"] == ["Concert de jazz"]


def test_upcoming_view_follows_the_frozen_clock(db: Any) -> None:
    """23/10/2026 : la vue lisait l'heure réelle (now()) alors que l'évaluation se
    place à l'instant de l'export : chaque jour, des sorties de la base figée
    disparaissaient. Elle suit maintenant le réglage `sortiria.maintenant`."""

    now = datetime.now(PARIS)
    insert_event(db, "hier", (now - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0))

    def visible() -> list[str]:
        with db.cursor() as cursor:
            cursor.execute("SELECT event_id FROM sortiria.v_upcoming_events ORDER BY event_id")
            return [row[0] for row in cursor.fetchall()]

    assert visible() == []                                   # réglage absent : l'heure réelle
    frozen = now - timedelta(days=2)
    dataset.freeze_clock(db, frozen)
    with db.cursor() as cursor:
        cursor.execute("SELECT sortiria.reference_now()")
        assert cursor.fetchone()[0] == frozen
    assert visible() == ["hier"]                             # avant-hier, la sortie était à venir
    with db.cursor() as cursor:
        cursor.execute("RESET sortiria.maintenant")
    assert visible() == []                                   # réglage retiré : comme en production
