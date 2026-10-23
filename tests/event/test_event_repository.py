"""Tests unitaires de `src/event/event_repository.py`, avec une base simulée.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026).
Aucune vraie base n'est nécessaire : `FakeConnection` enregistre les requêtes.
"""

from __future__ import annotations

from typing import Any, Self

from src.event import event_repository as repo
from src.event.openagenda_mapper import map_openagenda_event


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.rowcount = connection.rowcount
        self.description = [("event_id",), ("title",)]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.calls.append(("execute", sql, params))

    def executemany(self, sql: str, rows: list[dict]) -> None:
        self.connection.calls.append(("executemany", sql, rows))

    def fetchall(self) -> list[tuple]:
        return [("abc", "Concert")]

    def fetchone(self) -> tuple:
        return (42,)


class FakeConnection:
    def __init__(self, rowcount: int = 0) -> None:
        self.calls: list[tuple] = []
        self.rowcount = rowcount

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def test_upsert_sends_one_row_per_event(openagenda_item) -> None:
    connection = FakeConnection()
    event = map_openagenda_event(openagenda_item)

    assert repo.upsert_events(connection, [event, event]) == 2
    kind, sql, rows = connection.calls[0]
    assert kind == "executemany"
    assert "ON CONFLICT (event_id) DO UPDATE" in sql
    assert rows[0]["event_id"] == event.event_id
    assert set(rows[0]) == set(repo.EVENT_COLUMNS)


def test_upsert_without_events_does_nothing() -> None:
    connection = FakeConnection()
    assert repo.upsert_events(connection, []) == 0
    assert connection.calls == []


def test_empty_dates_become_null(openagenda_item) -> None:
    event = map_openagenda_event(openagenda_item)
    event.end_at = ""
    assert repo.event_to_row(event)["end_at"] is None


def test_delete_past_events_counts_and_keeps_statistics(now) -> None:
    connection = FakeConnection(rowcount=3)
    assert repo.delete_past_events(connection, now) == 42          # compte rendu par la requête (fetchone)
    _, sql, params = connection.calls[0]
    assert "DELETE FROM sortiria.events" in sql and "deleted_event_stats" in sql
    assert params["today_start"].hour == 0 and params["reason"] == "passe"


def test_fetch_events_missing_embedding() -> None:
    rows = repo.fetch_events_missing_embedding(FakeConnection(), limit=10)
    assert rows == [{"event_id": "abc", "title": "Concert"}]


def test_record_collect_run() -> None:
    connection = FakeConnection()
    run_id = repo.record_collect_run(connection, {"events_extracted": 5, "rejection_reasons": {"date_absente": 1}})
    assert run_id == 42
    params = connection.calls[0][2]
    assert params["events_extracted"] == 5
    assert params["rejection_reasons"] == '{"date_absente": 1}'
