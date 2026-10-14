"""Tests du schéma `sql/sortiria/` sur un vrai PostgreSQL avec pgvector.

**Provenance** : nouveau fichier écrit pour SortirIA (14/10/2026).

Ces tests ne tournent que si la variable SORTIRIA_TEST_DATABASE_URL pointe vers
une base jetable dont le nom contient « test » : le schéma `sortiria` y est
supprimé puis recréé. Exemple avec la base de docker compose :

    docker compose exec db createdb -U sortiria sortiria_test
    SORTIRIA_TEST_DATABASE_URL=postgresql://sortiria:...@localhost:5432/sortiria_test \\
        uv run pytest tests/event/test_schema_postgres.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import pytest

from scripts.init_db import apply_schema, list_schema_objects, missing_objects
from src.event import event_repository as repository
from src.event.event_normalizer import PARIS
from src.event.openagenda_mapper import map_openagenda_event

from .conftest import make_openagenda_item

TEST_URL = os.environ.get("SORTIRIA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_URL or "test" not in TEST_URL.rsplit("/", 1)[-1],
    reason="SORTIRIA_TEST_DATABASE_URL absente (ou base dont le nom ne contient pas « test »)",
)


@pytest.fixture(scope="module")
def db() -> Any:
    psycopg2 = pytest.importorskip("psycopg2")
    connection = psycopg2.connect(TEST_URL)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS sortiria CASCADE")
    apply_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean(db: Any) -> None:
    with db.cursor() as cursor:
        cursor.execute(
            "TRUNCATE sortiria.events, sortiria.rag_requests, sortiria.query_cache, "
            "sortiria.collect_runs, sortiria.partner_imports, sortiria.partners RESTART IDENTITY CASCADE"
        )


def scalar(db: Any, sql: str, params: Any = None) -> Any:
    with db.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def future_event(**overrides: Any):
    start = (datetime.now(PARIS) + timedelta(days=3)).replace(hour=20, minute=30, second=0, microsecond=0)
    item = make_openagenda_item(timings=[{"start": start.isoformat(), "end": (start + timedelta(hours=2)).isoformat()}],
                                **overrides)
    return map_openagenda_event(item)


# --- Le schéma lui-même ---------------------------------------------------

def test_schema_is_complete_and_rerunnable(db: Any) -> None:
    apply_schema(db)  # deuxième passage : aucune erreur
    assert missing_objects(list_schema_objects(db)) == []


def test_sources_are_declared(db: Any) -> None:
    assert scalar(db, "SELECT count(*) FROM sortiria.sources WHERE source_id IN ('openagenda','datatourisme','partenaire')") == 3


def test_distance_montpellier_sete(db: Any) -> None:
    km = scalar(db, "SELECT sortiria.distance_km(43.6108, 3.8767, 43.4028, 3.6969)")
    assert 26 < km < 30


# --- Règles de cohérence --------------------------------------------------

def insert_event(db: Any, **values: Any) -> None:
    row = {"event_id": "e1", "source": "openagenda", "title": "T", "start_at": "2030-01-01T20:00+01:00",
           "url": "https://x.fr", **values}
    columns = ", ".join(row)
    placeholders = ", ".join(f"%({key})s" for key in row)
    with db.cursor() as cursor:
        cursor.execute(f"INSERT INTO sortiria.events ({columns}) VALUES ({placeholders})", row)


@pytest.mark.parametrize("values", [
    {"source": "inconnue"},                    # source non déclarée
    {"is_sponsored": True},                    # mis en avant sans partenaire
    {"audiences": ["adultes"]},                # public inconnu
    {"status": "peut-etre"},                   # statut inconnu
    {"age_min": 12, "age_max": 6},             # âges inversés
    {"end_at": "2029-12-31T20:00+01:00"},      # fin avant le début
])
def test_invalid_events_are_refused(db: Any, values: dict) -> None:
    psycopg2 = pytest.importorskip("psycopg2")
    with pytest.raises(psycopg2.errors.IntegrityError):
        insert_event(db, **values)


def test_sponsored_event_with_partner_is_accepted(db: Any) -> None:
    partner_id = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Le Club', 'soiree') RETURNING partner_id")
    insert_event(db, source="partenaire", partner_id=partner_id, is_sponsored=True, audiences=["soiree"])
    assert scalar(db, "SELECT is_sponsored FROM sortiria.v_upcoming_events") is True


# --- Le dépôt Python contre la vraie base ---------------------------------

def test_repository_round_trip(db: Any) -> None:
    event = future_event(age={"min": 3, "max": 8}, accessibility=["mi"], title={"fr": "Spectacle jeune public"})
    db.autocommit = False
    assert repository.upsert_events(db, [event]) == 1
    db.commit()
    db.autocommit = True
    row = scalar(db, "SELECT row(audiences, accessibility, age_min, status)::text FROM sortiria.events")
    assert row == '({famille},{moteur},3,programme)'
    assert repository.fetch_events_missing_embedding(db)[0]["event_id"] == event.event_id
    assert repository.update_embeddings(db, [(event.event_id, [0.1] * 1024)], "mistral-embed") == 1
    assert repository.fetch_events_missing_embedding(db) == []


def test_upsert_resets_embedding_when_text_changes(db: Any) -> None:
    event = future_event()
    repository.upsert_events(db, [event])
    repository.update_embeddings(db, [(event.event_id, [0.1] * 1024)], "mistral-embed")
    event.description = "Nouvelle description"
    repository.upsert_events(db, [event])
    assert scalar(db, "SELECT embedding IS NULL FROM sortiria.events") is True


def test_view_hides_cancelled_online_and_past_events(db: Any) -> None:
    insert_event(db, event_id="ok", start_at=datetime.now(PARIS) + timedelta(days=1))
    insert_event(db, event_id="annule", status="annule", start_at=datetime.now(PARIS) + timedelta(days=1))
    insert_event(db, event_id="enligne", attendance="en_ligne", start_at=datetime.now(PARIS) + timedelta(days=1))
    insert_event(db, event_id="passe", start_at=datetime.now(PARIS) - timedelta(days=1))
    # « Jour seulement », aujourd'hui à minuit : encore visible jusqu'à ce soir.
    today = datetime.now(PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
    insert_event(db, event_id="aujourdhui", start_at=today, time_known=False)
    with db.cursor() as cursor:
        cursor.execute("SELECT event_id FROM sortiria.v_upcoming_events ORDER BY event_id")
        assert [row[0] for row in cursor.fetchall()] == ["aujourdhui", "ok"]


def test_coverage_by_zone(db: Any) -> None:
    start = datetime.now(PARIS) + timedelta(days=1)
    insert_event(db, event_id="mtp", start_at=start, latitude=43.61, longitude=3.877)
    insert_event(db, event_id="sete", start_at=start, latitude=43.403, longitude=3.697)
    insert_event(db, event_id="beziers", start_at=start, latitude=43.344, longitude=3.215)
    with db.cursor() as cursor:
        cursor.execute("SELECT zone, events FROM sortiria.v_coverage ORDER BY zone")
        assert cursor.fetchall() == [("bassin_de_thau", 1), ("hors_zone", 1), ("montpellier", 1)]


def test_collect_run_with_source_detail(db: Any) -> None:
    run_id = repository.record_collect_run(db, {"status": "partial", "failed_sources": ["datatourisme"]})
    repository.record_run_sources(db, run_id, [
        {"source_id": "openagenda", "status": "ok", "attempts": 1, "events_kept": 64},
        {"source_id": "datatourisme", "status": "echec", "attempts": 2, "error": "délai dépassé"},
    ])
    assert scalar(db, "SELECT sum(events_kept) FROM sortiria.collect_run_sources WHERE run_id = %s", (run_id,)) == 64


def test_feedback_rules_and_purge(db: Any) -> None:
    psycopg2 = pytest.importorskip("psycopg2")
    old = scalar(db, "INSERT INTO sortiria.rag_requests (question, asked_at) VALUES ('ancienne', now() - interval '100 days') RETURNING request_id")
    new = scalar(db, "INSERT INTO sortiria.rag_requests (question) VALUES ('récente') RETURNING request_id")
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.feedback (request_id, event_id, rating) VALUES (%s, 'e1', 1), (%s, 'e1', -1)", (old, new))
        with pytest.raises(psycopg2.errors.IntegrityError):
            cursor.execute("INSERT INTO sortiria.feedback (request_id, event_id, rating) VALUES (%s, 'e1', 1)", (new,))
        with pytest.raises(psycopg2.errors.CheckViolation):
            cursor.execute("INSERT INTO sortiria.feedback (request_id, event_id, rating) VALUES (%s, 'e2', 0)", (new,))
        cursor.execute("SELECT * FROM sortiria.purge_old_data()")
        assert cursor.fetchone() == (1, 0, 0)
    assert scalar(db, "SELECT count(*) FROM sortiria.feedback") == 1  # l'avis ancien est parti avec sa question


def test_daily_quality_view(db: Any) -> None:
    request_id = scalar(db, "INSERT INTO sortiria.rag_requests (question, latency_ms, cost_eur) VALUES ('q', 1200, 0.002) RETURNING request_id")
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.feedback (request_id, rating) VALUES (%s, 1)", (request_id,))
        cursor.execute("SELECT requests, ratings, thumbs_up_rate FROM sortiria.v_daily_quality")
        requests, ratings, rate = cursor.fetchone()
    assert (requests, ratings, float(rate)) == (1, 1, 1.0)


def test_unseen_events_are_removed_only_for_given_sources(db: Any) -> None:
    start = datetime.now(PARIS) + timedelta(days=1)
    insert_event(db, event_id="revu", start_at=start)
    insert_event(db, event_id="disparu", start_at=start)
    insert_event(db, event_id="autre_source", source="datatourisme", start_at=start)
    with db.cursor() as cursor:
        cursor.execute("UPDATE sortiria.events SET last_seen_at = now() - interval '1 day'")
    # Collecte suivante, dans une seule transaction : « revu » est revu, pas « disparu ».
    db.autocommit = False
    repository.upsert_events(db, [])
    with db.cursor() as cursor:
        cursor.execute("UPDATE sortiria.events SET last_seen_at = now() WHERE event_id = 'revu'")
    assert repository.delete_unseen_events(db, ["openagenda"]) == 1
    db.commit()
    db.autocommit = True
    with db.cursor() as cursor:
        cursor.execute("SELECT event_id FROM sortiria.events ORDER BY event_id")
        assert [row[0] for row in cursor.fetchall()] == ["autre_source", "revu"]


def test_collect_run_duration(db: Any) -> None:
    started = (datetime.now(PARIS) - timedelta(seconds=78)).isoformat()
    run_id = repository.record_collect_run(db, {"status": "success", "started_at": started, "events_removed": 5})
    with db.cursor() as cursor:
        cursor.execute("SELECT duration_s, events_removed FROM sortiria.v_collect_health WHERE run_id = %s", (run_id,))
        duration, removed = cursor.fetchone()
    assert 77 <= duration <= 80 and removed == 5


def test_attribution_text_cites_producer_and_update_date(db: Any) -> None:
    insert_event(db, source="datatourisme", start_at=datetime.now(PARIS) + timedelta(days=1),
                 source_credit="34 - ADT34", source_updated_at="2026-10-06")
    assert scalar(db, "SELECT source_attribution FROM sortiria.v_upcoming_events") == (
        "Source : DATAtourisme (34 - ADT34), mise à jour le 06/10/2026"
    )
