"""Tests de l'agent de collecte nocturne, avec de faux services.

**Provenance** : nouveau fichier écrit pour SortirIA (13/10/2026). Aucun
réseau, aucune base, aucun appel Mistral : tout est simulé.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Self

import pytest

from agent.graph import run_nightly_collection
from agent.nodes import decide_status
from agent.routing import ROUTE_PREPARE, ROUTE_REPORT, ROUTE_RETRY, route_after_extraction
from agent.services import CollectionServices, ExtractionOutput
from agent.state import STATUS_PARTIAL
from src.event.event_normalizer import PARIS
from src.event.openagenda_mapper import map_openagenda_event

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=PARIS)


def make_article(uid: int, title: str) -> dict[str, Any]:
    item = {
        "uid": uid,
        "title": {"fr": title},
        "canonicalUrl": f"https://openagenda.com/e/{uid}",
        "location": {"name": "Le Jam", "city": "Montpellier", "latitude": 43.6, "longitude": 3.86},
        "timings": [{"start": "2026-10-24T18:30:00Z", "end": "2026-10-24T21:00:00Z"}],
    }
    event = map_openagenda_event(item)
    return {"title": title, "metadata": {"event": event.to_dict()}}


class FakeCursor:
    def __init__(self, db: FakeDatabase) -> None:
        self.db = db
        self.rowcount = 1
        self.description = [("event_id",), ("title",), ("description",), ("category",), ("venue_name",), ("city",)]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.db.calls.append(("execute", sql.split()[0], params))
        self.last_sql = sql

    def executemany(self, sql: str, rows: list) -> None:
        self.db.calls.append(("executemany", sql.split()[0], list(rows)))

    def fetchall(self) -> list[tuple]:
        if "collect_run_sources" in getattr(self, "last_sql", ""):
            return list(self.db.previous_counts.items())
        if "unnest(audiences)" in getattr(self, "last_sql", ""):   # couverture de la semaine en base
            return [("montpellier", "soiree", 12), ("bassin_de_thau", "famille", 3)]
        return [("e1", "Concert", "", "", "Le Jam", "Montpellier"), ("e2", "Théâtre", "", "", "", "Montpellier")]

    def fetchone(self) -> tuple:
        if "purge_old_data" in getattr(self, "last_sql", ""):
            return (3, 1, 0)
        return (7,)


class FakeDatabase:
    def __init__(self, previous_counts: dict[str, int] | None = None) -> None:
        self.calls: list[tuple] = []
        self.connections = 0
        self.previous_counts = previous_counts or {}

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @contextmanager
    def connect(self):
        self.connections += 1
        yield self


def make_services(extract, db: FakeDatabase | None = None, embedded: list | None = None) -> CollectionServices:
    db = db or FakeDatabase()

    def embed(texts: list[str]) -> list[list[float]]:
        if embedded is not None:
            embedded.extend(texts)
        return [[0.1, 0.2] for _ in texts]

    # Une seule source dans ces tests : la logique de l'agent ne dépend pas
    # de la liste réelle des sources (voir test_default_sources).
    return CollectionServices(
        extract=extract, connect=db.connect, embed=embed, now=lambda: NOW, default_sources=("openagenda",)
    )


def test_full_night_success() -> None:
    db = FakeDatabase()
    embedded: list[str] = []
    articles = [make_article(1, "Concert Jazz"), make_article(2, "Théâtre d'impro")]
    services = make_services(lambda sources: ExtractionOutput(articles=articles), db, embedded)

    state = run_nightly_collection(services)

    assert state["status"] == "success"
    assert state["sources"] == ["openagenda"]
    assert state["events_upserted"] == 2
    assert state["events_embedded"] == 2
    assert state["run_id"] == 7
    assert embedded[0].startswith("Concert. Lieu : Le Jam, Montpellier")
    # Le détail par source est bien écrit, avec les 2 événements retenus.
    detail = [call[2] for call in db.calls if call[0] == "executemany" and call[2] and "source_id" in call[2][0]]
    assert detail == [[{"run_id": 7, "source_id": "openagenda", "status": "ok",
                        "attempts": 1, "events_kept": 2, "error": ""}]]
    kinds = [call[1] for call in db.calls]
    # comptes de la veille, upsert, ménage des disparus, purge des passés,
    # embeddings (lecture + écriture), purge RGPD, bilan, détail par source
    # Suppressions (disparus, passés, plafond) : requêtes « WITH … DELETE » qui
    # gardent leurs statistiques ; puis taille de la base, embeddings, purge, bilan.
    # ... puis le SELECT de la couverture de la semaine (surveillance), le
    # vocabulaire des titres et le bilan de la recherche (transaction à part,
    # la fausse base ne sait pas y répondre : la collecte continue), le rapport
    # de dérive (transaction à part, arrêté à la 2e requête par la fausse base :
    # la collecte continue aussi), le bilan.
    assert kinds == ["SELECT", "INSERT", "WITH", "WITH", "WITH", "SELECT", "SELECT", "UPDATE", "SELECT", "SELECT",
                     "SELECT", "WITH", "WITH", "WITH", "INSERT", "INSERT"]
    assert state["purged"] == {"requests": 3, "cache": 1, "runs": 0}


def test_retry_after_a_failed_extraction() -> None:
    calls = {"n": 0}

    def flaky(sources):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("OpenAgenda ne répond pas")
        return ExtractionOutput(articles=[make_article(1, "Concert")])

    state = run_nightly_collection(make_services(flaky))

    assert state["attempts"] == 2
    assert state["status"] == "partial_success"  # réussie, mais avec une erreur à signaler
    assert "essai 1" in state["errors"][0]


def test_total_failure_still_writes_a_report() -> None:
    db = FakeDatabase()

    def broken(sources):
        raise ConnectionError("réseau coupé")

    state = run_nightly_collection(make_services(broken, db), max_attempts=2)

    assert state["status"] == "failed"
    assert state["attempts"] == 2
    assert "Aucun événement collecté cette nuit." in state["alerts"]
    # Seuls le bilan et son détail par source sont écrits.
    assert [call[1] for call in db.calls] == ["INSERT", "INSERT"]
    detail = db.calls[1][2]
    assert detail == [{"run_id": 7, "source_id": "openagenda", "status": "echec",
                       "attempts": 2, "events_kept": 0, "error": ""}]


def test_dry_run_writes_nothing() -> None:
    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), db)

    state = run_nightly_collection(services, dry_run=True)

    assert state["status"] == "success"
    assert db.connections == 0


def test_requested_sources_are_used() -> None:
    seen: list = []
    services = make_services(lambda sources: seen.append(list(sources)) or ExtractionOutput(articles=[]))
    run_nightly_collection(services, sources=["rss"], dry_run=True, max_attempts=1)
    assert seen == [["rss"]]


@pytest.mark.parametrize(
    ("state", "route"),
    [
        ({"articles": [{}], "attempts": 1}, ROUTE_PREPARE),
        ({"articles": [], "attempts": 1, "max_attempts": 2}, ROUTE_RETRY),
        ({"articles": [], "attempts": 2, "max_attempts": 2}, ROUTE_REPORT),
    ],
)
def test_route_after_extraction(state, route) -> None:
    assert route_after_extraction(state) == route


def test_high_rejection_rate_raises_an_alert() -> None:
    status, alerts = decide_status({
        "preparation": {"events_extracted": 10, "events_rejected": 5},
        "events": [{}] * 5,
        "failed_sources": ["openagenda"],
    })
    assert status == "partial_success"
    assert any("rejeté" in alert for alert in alerts)
    assert any("openagenda" in alert for alert in alerts)


def test_source_rows_match_failed_source_by_full_name() -> None:
    from agent.nodes import build_source_rows

    state = {
        "sources": ["openagenda", "datatourisme"],
        "failed_sources": ["OpenAgenda - Montpellier et bassin de Thau"],
        "attempts": 1,
        # Comme dans le vrai graphe : des dictionnaires (bug du 14/10 corrigé).
        "events": [{"source": "datatourisme"}, {"source": "datatourisme"}],
    }
    rows = build_source_rows(state)
    assert [(row["source_id"], row["status"], row["events_kept"]) for row in rows] == [
        ("openagenda", "echec", 0),
        ("datatourisme", "ok", 2),
    ]


def test_sweep_only_sources_read_without_failure_and_with_events() -> None:
    from agent.nodes import sources_safe_to_sweep

    state = {
        "sources": ["openagenda", "datatourisme", "partenaire"],
        "failed_sources": ["datatourisme"],
        "events": [{"source": "openagenda"}, {"source": "datatourisme"}],
    }
    # datatourisme a échoué ; partenaire n'a rien renvoyé : on garde leurs événements.
    assert sources_safe_to_sweep(state) == ["openagenda"]


def test_default_sources() -> None:
    assert CollectionServices(extract=None, connect=None, embed=None).default_sources == (
        "openagenda", "datatourisme", "rss", "ical", "document", "partenaire", "scraper",
    )


def test_sources_without_configuration_are_skipped() -> None:
    db = FakeDatabase()
    asked: list[list[str]] = []

    def extract(sources):
        asked.append(list(sources))
        return ExtractionOutput(articles=[make_article(1, "Concert Jazz")])

    services = CollectionServices(
        extract=extract, connect=db.connect, embed=lambda texts: [[0.1] for _ in texts], now=lambda: NOW,
        default_sources=("openagenda", "datatourisme", "ical"),
        is_configured=lambda name: name != "ical",   # aucun calendrier déclaré
    )
    state = run_nightly_collection(services)
    assert asked == [["openagenda", "datatourisme"]]
    assert state["sources"] == ["openagenda", "datatourisme"]


def test_disabled_sources_are_not_launched_nor_swept(monkeypatch) -> None:
    """SOURCES_DESACTIVEES (Azure, 15/10/2026) : la source n'est ni lancée ni
    balayée, et le rapport la signale."""

    monkeypatch.setenv("SOURCES_DESACTIVEES", " document , rss")
    db = FakeDatabase()
    asked: list[list[str]] = []

    def extract(sources):
        asked.append(list(sources))
        return ExtractionOutput(articles=[make_article(1, "Concert Jazz")])

    services = CollectionServices(
        extract=extract, connect=db.connect, embed=lambda texts: [[0.1] for _ in texts], now=lambda: NOW,
        default_sources=("openagenda", "document"), is_configured=lambda name: True,
    )
    state = run_nightly_collection(services)
    assert asked == [["openagenda"]]
    assert state["sources"] == ["openagenda"]
    assert state["disabled_sources"] == ["document"]
    sweeps = [call for call in db.calls if call[1] == "DELETE" and call[2] and "document" in str(call[2])]
    assert sweeps == []


def test_disabled_sources_do_not_block_an_explicit_request(monkeypatch) -> None:
    """Sur le PC, `--source document` lit la source même si elle est coupée ailleurs."""

    monkeypatch.setenv("SOURCES_DESACTIVEES", "document")
    asked: list[list[str]] = []

    def extract(sources):
        asked.append(list(sources))
        return ExtractionOutput(articles=[make_article(1, "Concert Jazz")])

    services = CollectionServices(
        extract=extract, connect=FakeDatabase().connect, embed=lambda texts: [[0.1] for _ in texts],
        now=lambda: NOW, default_sources=("openagenda", "document"),
    )
    run_nightly_collection(services, sources=["document"])
    assert asked == [["document"]]


def test_sharp_drop_keeps_yesterday_events_and_alerts() -> None:
    # La veille, openagenda avait apporté 80 événements ; ce soir, 2.
    db = FakeDatabase(previous_counts={"openagenda": 80})
    articles = [make_article(1, "Concert Jazz"), make_article(2, "Théâtre d'impro")]
    state = run_nightly_collection(make_services(lambda sources: ExtractionOutput(articles=articles), db))

    assert state["source_drops"] == {"openagenda": (80, 2)}
    assert state["status"] == STATUS_PARTIAL
    assert any("Baisse anormale pour openagenda" in alert for alert in state["alerts"])
    # Aucun ménage : la requête DELETE des disparus n'a pas été envoyée pour openagenda.
    sweeps = [call for call in db.calls if call[1] == "DELETE" and call[2] and "sources" in call[2]]
    assert sweeps == []


def test_small_source_variation_is_not_a_drop() -> None:
    from agent.nodes import detect_source_drops

    state = {"sources": ["openagenda", "ical"], "events": [{"source": "openagenda"}] * 50 + [{"source": "ical"}]}
    # 50 contre 60 : normal ; ical apportait trop peu pour juger.
    assert detect_source_drops(state, {"openagenda": 60, "ical": 10}) == {}


def test_report_is_sent_after_the_collection() -> None:
    db = FakeDatabase()
    sent: list[dict] = []
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert Jazz")]), db)
    services.notify = sent.append
    run_nightly_collection(services)
    (report,) = sent
    assert report["status"] == "success" and report["run_id"] == 7
    assert report["source_rows"][0]["source_id"] == "openagenda"


def test_no_mail_in_dry_run_and_mail_failure_is_harmless() -> None:
    sent: list[dict] = []
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert Jazz")]))
    services.notify = sent.append
    run_nightly_collection(services, dry_run=True)
    assert sent == []

    def broken(report):
        raise OSError("SMTP en panne")

    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert Jazz")]), FakeDatabase())
    services.notify = broken
    assert run_nightly_collection(services)["status"] == "success"


def test_a_database_error_still_ends_with_a_failed_report() -> None:
    """17/10/2026 : une contrainte violée à l'écriture avait arrêté la nuit sans
    bilan ni mail. Désormais : statut « échec », alerte, bilan enregistré."""

    class RefusingCursor(FakeCursor):
        def executemany(self, sql: str, rows: list) -> None:
            if "INTO sortiria.events" in sql:
                raise RuntimeError('new row for relation "events" violates check constraint "events_price_order"')
            super().executemany(sql, rows)

    class RefusingDatabase(FakeDatabase):
        def cursor(self) -> FakeCursor:
            return RefusingCursor(self)

    db = RefusingDatabase()
    sent: list[dict] = []
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert Jazz")]), db)
    services.notify = lambda report: sent.append(dict(report))

    state = run_nightly_collection(services)

    assert state["status"] == "failed" and state["events_upserted"] == 0
    assert any("Écriture en base impossible" in alert and "events_price_order" in alert for alert in state["alerts"])
    assert sent and sent[0]["status"] == "failed"                  # le mail part quand même


def test_embeddings_stop_only_after_consecutive_failed_batches(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Un lot réussi remet le compte à zéro ; trois échecs de suite arrêtent la nuit."""
    from agent import nodes

    rows = [{"event_id": str(number), "title": f"Événement {number}"} for number in range(10)]
    saved: list[str] = []
    monkeypatch.setattr(nodes, "EMBEDDING_BATCH_SIZE", 1)
    monkeypatch.setattr(nodes, "build_embedding_text", lambda row: row["event_id"])
    monkeypatch.setattr(nodes.repository, "fetch_events_missing_embedding",
                        lambda connection, limit: rows)

    def update(connection: Any, pairs: Any, model: Any) -> int:
        keys = [key for key, _ in pairs]
        saved.extend(keys)
        return len(keys)

    monkeypatch.setattr(nodes.repository, "update_embeddings", update)
    # Lots 0 et 1 en échec, 2 réussi, 3 et 4 en échec, 5 réussi, puis 6, 7, 8 en échec.
    failing = {"0", "1", "3", "4", "6", "7", "8"}

    def embed(texts: list[str]) -> list[list[float]]:
        if texts[0] in failing:
            raise RuntimeError("503")
        return [[0.1] for _ in texts]

    services = CollectionServices(extract=None, connect=None, embed=embed)
    total, _ = nodes.embed_missing(None, services)
    assert saved == ["2", "5"] and total == 2   # le lot 9 n'est pas tenté
