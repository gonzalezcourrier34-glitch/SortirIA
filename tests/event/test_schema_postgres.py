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
            "sortiria.collect_runs, sortiria.partner_imports, sortiria.partners, sortiria.deleted_event_stats, "
            "sortiria.agent_proposals, sortiria.event_ratings, sortiria.title_vocabulary RESTART IDENTITY CASCADE"
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
    # Dans 10 jours : la vue ne propose que les 3 prochains mois (14/10/2026).
    soon = (datetime.now(PARIS) + timedelta(days=10)).replace(hour=20, minute=0, second=0, microsecond=0)
    row = {"event_id": "e1", "source": "openagenda", "title": "T", "start_at": soon.isoformat(),
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
    {"end_at": "2020-12-31T20:00+01:00"},      # fin avant le début
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
        {"source_id": "document", "status": "partiel", "attempts": 1, "events_kept": 120},   # un PDF sur deux lu
    ])
    assert scalar(db, "SELECT sum(events_kept) FROM sortiria.collect_run_sources WHERE run_id = %s", (run_id,)) == 184
    # Une nuit « partielle » ne sert pas de référence pour détecter une baisse.
    assert repository.fetch_previous_source_counts(db) == {"openagenda": 64}


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
    request_id = scalar(db, "INSERT INTO sortiria.rag_requests (question, latency_ms, cost_usd, answer, degraded) "
                            "VALUES ('q', 1200, 0.002, 'Allez au Jam [1].', TRUE) RETURNING request_id")
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.feedback (request_id, rating) VALUES (%s, 1)", (request_id,))
        cursor.execute("SELECT requests, ratings, thumbs_up_rate, degraded_rate, cost_usd FROM sortiria.v_daily_quality")
        requests, ratings, rate, degraded, cost = cursor.fetchone()
    assert (requests, ratings, float(rate), float(degraded), float(cost)) == (1, 1, 1.0, 1.0, 0.002)


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


def test_previous_source_counts_and_purge_from_python(db: Any) -> None:
    first = repository.record_collect_run(db, {"status": "success"})
    repository.record_run_sources(db, first, [{"source_id": "openagenda", "status": "ok", "events_kept": 80}])
    second = repository.record_collect_run(db, {"status": "partial"})
    repository.record_run_sources(db, second, [
        {"source_id": "openagenda", "status": "echec", "events_kept": 0},     # échec : ignoré
        {"source_id": "datatourisme", "status": "ok", "events_kept": 986},
    ])
    assert repository.fetch_previous_source_counts(db) == {"openagenda": 80, "datatourisme": 986}
    assert repository.purge_old_data(db) == {"requests": 0, "cache": 0, "runs": 0}


def test_partner_files_are_recorded(db: Any) -> None:
    """Partenaire créé, événement relié et mis en avant, bilan du fichier noté une fois."""

    from src.event.event_schema import Event

    info = {"name": "Ludothèque du Port", "sector": "enfants", "website": "https://ludo.example",
            "sponsored_until": "2026-12-31", "file_name": "programme.csv", "file_sha256": "a" * 64,
            "rows_total": 3, "rows_loaded": 2, "rows_rejected": 1, "rejection_reasons": {"date_illisible": 1}}
    start = (datetime.now(PARIS) + timedelta(days=5)).replace(microsecond=0).isoformat()
    events = [
        Event(event_id=f"p{i}", source="partenaire", source_event_id=f"ludo:p{i}", title=f"Atelier {i}",
              start_at=start, city="Sète", url="https://ludo.example", is_sponsored=True, partner_info=info)
        for i in range(2)
    ]
    with db.cursor():
        assert repository.attach_partners(db, events) == 2
        assert repository.upsert_events(db, events) == 2
        repository.attach_partners(db, events)   # nuit suivante : même fichier
    assert scalar(db, "SELECT count(*) FROM sortiria.partners") == 1
    assert scalar(db, "SELECT count(*) FROM sortiria.events WHERE is_sponsored AND partner_id IS NOT NULL") == 2
    assert scalar(db, "SELECT status || ':' || rows_loaded || ':' || (rejection_reasons->>'date_illisible') "
                      "FROM sortiria.partner_imports") == "partiel:2:1"


def test_collect_run_keeps_the_agent_monitoring(db: Any) -> None:
    """Coût, tokens, couverture, durées, alertes et lien MLflow sont gardés, et lisibles par Grafana."""

    run_id = repository.record_collect_run(db, {
        "status": "success", "events_extracted": 10, "events_embedded": 4, "cost_usd": 0.0412,
        "llm_usage": {"mistral-embed": {"input": 500, "output": 0}},
        "coverage": {"montpellier": {"famille": 12, "soiree": 30}, "bassin_de_thau": {"famille": 6, "soiree": 2}},
        "node_durations": {"extract_sources": 90.5}, "alerts": ["Couverture faible"],
        "prompt_version": "2026-10-14.2", "mlflow_run_id": "abc123",
    })
    row = scalar(db, "SELECT cost_usd || '|' || alerts_count || '|' || thau_soiree || '|' || mlflow_run_id "
                     "FROM sortiria.v_collect_health WHERE run_id = %s", (run_id,))
    assert row == "0.0412|1|2|abc123"
    assert scalar(db, "SELECT node_durations ->> 'extract_sources' FROM sortiria.collect_runs WHERE run_id = %s",
                  (run_id,)) == "90.5"


def test_grafana_dashboard_queries_run(db: Any) -> None:
    """Chaque requête du tableau de bord Grafana s'exécute sur le schéma (garde-fou)."""

    import json
    import re
    from pathlib import Path

    run_id = repository.record_collect_run(db, {"status": "success", "events_extracted": 5,
                                                "coverage": {"montpellier": {"famille": 1, "soiree": 2}}})
    repository.record_run_sources(db, run_id, [{"source_id": "openagenda", "status": "ok", "events_kept": 5}])
    dashboard = json.loads(Path("monitoring/grafana/provisioning/dashboards/sortiria-collecte.json").read_text("utf-8"))
    queries = [target["rawSql"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
    assert len(queries) >= 12
    with db.cursor() as cursor:
        for sql in queries:
            cursor.execute(re.sub(r"\$__timeFilter\([^)]*\)", "TRUE", sql))
            cursor.fetchall()


def test_search_dashboard_queries_run_and_count(db: Any) -> None:
    """Tableau « SortirIA - Recherche » (16/10/2026) : chaque requête s'exécute,
    et les comptes exactes / élargies / satisfaction sont justes."""

    import json
    import re
    from pathlib import Path

    def ask(question: str, recherche: dict, latency: int, cost: float | None, cache: bool = False) -> int:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sortiria.rag_requests (question, constraints, latency_ms, cost_usd, from_cache) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING request_id",
                (question, json.dumps({"recherche": recherche}), latency, cost, cache))
            return cursor.fetchone()[0]

    exact = ask("concert jazz", {"exact": True, "elargi": [], "mots_inconnus": []}, 3000, 0.0004)
    widened = ask("karaoké", {"exact": False, "elargi": ["pour le public « famille »"], "mots_inconnus": []}, 5000, 0.0005)
    ask("zumba", {"exact": False, "elargi": [], "mots_inconnus": ["zumba"], "sujet_introuvable": True}, 2000, None)
    ask("concert jazz", {"exact": True}, 40, None, cache=True)
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.feedback (request_id, rating) VALUES (%s, 1), (%s, -1)", (exact, widened))

    dashboard = json.loads(Path("monitoring/grafana/provisioning/dashboards/sortiria-recherche.json").read_text("utf-8"))
    results = {}
    with db.cursor() as cursor:
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                cursor.execute(re.sub(r"\$__timeFilter\(([^)]*)\)", "TRUE", target["rawSql"]))
                results[panel["title"]] = cursor.fetchall()
    assert len(results) >= 12
    assert results["Questions posées"] == [(4,)]
    assert float(results["Satisfaction (pouces haut)"][0][0]) == 0.5
    [(_, exactes, elargies, voisines, introuvables)] = results[
        "Réponses par jour : exactes, élargies, idées voisines, sujet introuvable"]
    assert (exactes, elargies, voisines, introuvables) == (2, 1, 0, 1)
    assert results["Mots cherchés absents de la base"] == [("zumba", 1)]
    assert [row[1] for row in results["Derniers pouces bas"]] == ["karaoké"]


def test_mistral_audiences_survive_the_next_night(db: Any) -> None:
    """L'enrichissement ajoute ses publics ; la collecte suivante ne les efface pas."""

    from src.event.event_schema import Event

    start = (datetime.now(PARIS) + timedelta(days=3)).replace(microsecond=0).isoformat()
    event = Event(event_id="en1", source="openagenda", source_event_id="1", title="Atelier BD",
                  description="Atelier pour les 12-17 ans.", start_at=start, city="Sète",
                  url="https://exemple.fr/bd", audiences=["famille"])
    repository.upsert_events(db, [event])
    rows = repository.fetch_events_to_enrich(db, "v1")
    assert [row["event_id"] for row in rows] == ["en1"]
    repository.update_enrichments(db, [{"event_id": "en1", "category_norm": "atelier", "summary": "Atelier BD ados.",
                                        "price_band": "inconnu", "audiences_llm": ["ados"], "is_outing": True}], "v1")
    assert scalar(db, "SELECT array_to_string(audiences, ',') FROM sortiria.events WHERE event_id = 'en1'") == "ados,famille"
    repository.upsert_events(db, [event])                                   # nuit suivante, même texte
    assert scalar(db, "SELECT array_to_string(audiences, ',') FROM sortiria.events WHERE event_id = 'en1'") == "ados,famille"
    assert repository.fetch_events_to_enrich(db, "v1") == []                # rien à refaire
    assert repository.fetch_events_to_enrich(db, "v2") != []                # nouvelle consigne : à refaire
    event.description = "Atelier BD, nouveau programme."
    repository.upsert_events(db, [event])                                   # texte modifié
    assert repository.fetch_events_to_enrich(db, "v1") != []


def test_agent_proposals_wait_for_a_human(db: Any) -> None:
    """Une réparation n'est proposée qu'une fois tant qu'elle attend ; on peut ensuite la refuser."""

    proposal = {"source_id": "document", "kind": "document_deplace", "diagnosis": "PDF déplacé",
                "action": {"type": "remplacer_url", "old": "https://a.fr/08/x.pdf", "new": "https://a.fr/09/x.pdf"}}
    repository.record_proposals(db, None, [proposal])
    repository.record_proposals(db, None, [proposal])                  # nuit suivante : même panne
    pending = repository.fetch_proposals(db)
    assert len(pending) == 1 and pending[0]["action"]["new"] == "https://a.fr/09/x.pdf"
    assert repository.set_proposal_status(db, pending[0]["proposal_id"], "refusee") == 1
    assert repository.fetch_proposals(db) == []
    assert len(repository.fetch_proposals(db, "refusee")) == 1


def test_deletions_keep_statistics_not_events(db: Any) -> None:
    """Passés, disparus, plafond : la fiche part, ses comptes restent (deleted_event_stats)."""

    from src.event.event_schema import Event

    now = datetime.now(PARIS)

    def event(event_id: str, days: int, **values: Any) -> Event:
        start = (now + timedelta(days=days)).replace(microsecond=0).isoformat()
        return Event(event_id=event_id, source="openagenda", source_event_id=event_id, title=f"Concert {event_id}",
                     start_at=start, city="Sète", url="https://exemple.fr", audiences=["soiree"], **values)

    repository.upsert_events(db, [event("passe", -10), event("proche", 5), event("loin1", 60), event("loin2", 80)])
    assert repository.delete_past_events(db, now) == 1
    assert repository.enforce_event_cap(db, maximum=2) == 1                 # le plus lointain part
    assert repository.count_events(db) == 2
    assert scalar(db, "SELECT string_agg(event_id, ',' ORDER BY event_id) FROM sortiria.events") == "loin1,proche"
    stats = scalar(db, "SELECT string_agg(reason || ':' || zone || ':' || events || ':' || night, ',' ORDER BY reason) "
                       "FROM sortiria.deleted_event_stats")
    assert stats == "passe:bassin_de_thau:1:1,plafond:bassin_de_thau:1:1"
    assert repository.enforce_event_cap(db, maximum=10) == 0                 # sous le plafond : rien


def test_only_the_next_three_months_are_proposed(db: Any) -> None:
    insert_event(db, event_id="soon")
    insert_event(db, event_id="far", start_at=(datetime.now(PARIS) + timedelta(days=120)).isoformat())
    assert scalar(db, "SELECT string_agg(event_id, ',') FROM sortiria.v_upcoming_events") == "soon"


def test_event_zone_uses_the_city_then_gps(db: Any) -> None:
    assert scalar(db, "SELECT sortiria.event_zone(NULL, NULL, 'Castelnau-le-Lez')") == "montpellier"
    assert scalar(db, "SELECT sortiria.event_zone(NULL, NULL, 'MÈZE')") == "bassin_de_thau"
    assert scalar(db, "SELECT sortiria.event_zone(43.40, 3.69, '')") == "bassin_de_thau"
    assert scalar(db, "SELECT sortiria.event_zone(NULL, NULL, 'Paris')") == "sans_gps"


# --- Notes des clients ---------------------------------------------------------------------

def _rated_event(db: Any, event_id: str, start_days: float, **values: Any) -> None:
    start = datetime.now(PARIS) + timedelta(days=start_days)
    insert_event(db, event_id=event_id, start_at=start.isoformat(), venue_name="Le Jam", city="Montpellier",
                 **values)


def test_rating_window_and_one_rating_per_client(db: Any) -> None:
    from src.event.event_ratings import submit_rating

    _rated_event(db, "futur", 3)
    _rated_event(db, "hier", -1)
    _rated_event(db, "ancien", -10)
    no_moderation = lambda text: ("publie", {})
    assert submit_rating(db, "futur", "session-aaaa", 5, moderate=no_moderation)["status"] == "hors_delai"
    assert submit_rating(db, "ancien", "session-aaaa", 5, moderate=no_moderation)["status"] == "hors_delai"
    assert submit_rating(db, "absent", "session-aaaa", 5, moderate=no_moderation)["status"] == "hors_delai"
    first = submit_rating(db, "hier", "session-aaaa", 2, "Bof", moderate=no_moderation)
    again = submit_rating(db, "hier", "session-aaaa", 4, "Finalement bien", moderate=no_moderation)
    assert first["status"] == again["status"] == "enregistree" and first["rating_id"] == again["rating_id"]
    assert scalar(db, "SELECT stars || ':' || comment || ':' || zone FROM sortiria.event_ratings") == \
        "4:Finalement bien:montpellier"
    assert scalar(db, "SELECT count(*) FROM sortiria.event_ratings WHERE user_key LIKE '%session%'") == 0


def test_ratings_outlive_the_event_and_rate_the_venue(db: Any) -> None:
    from src.event.event_ratings import submit_rating

    _rated_event(db, "concert", -1)
    for number, stars in enumerate((5, 4, 4)):
        submit_rating(db, "concert", f"session-{number:04d}", stars, moderate=lambda text: ("aucun", {}))
    assert scalar(db, "SELECT average_stars::TEXT || '/' || ratings FROM sortiria.v_venue_ratings") == "4.3/3"
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM sortiria.events")
    insert_event(db, event_id="prochain", venue_name="LE JAM ", city="montpellier")   # même lieu, autre soir
    assert scalar(db, "SELECT venue_rating::TEXT FROM sortiria.v_upcoming_events") == "4.3"
    assert scalar(db, "SELECT count(*) FROM sortiria.event_ratings") == 3


def test_past_events_stay_seven_days_for_rating(db: Any) -> None:
    _rated_event(db, "il_y_a_3_jours", -3)
    _rated_event(db, "il_y_a_9_jours", -9)
    assert repository.delete_past_events(db) == 1
    assert scalar(db, "SELECT string_agg(event_id, ',') FROM sortiria.events") == "il_y_a_3_jours"
    assert scalar(db, "SELECT count(*) FROM sortiria.v_upcoming_events") == 0     # gardé, mais plus proposé


def test_low_rated_partner_and_pending_comments(db: Any) -> None:
    from agent.nodes import review_ratings
    from agent.services import CollectionServices
    from src.event.event_ratings import submit_rating

    partner_id = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Bar Test', 'soiree') RETURNING partner_id")
    _rated_event(db, "soiree", -1, source="partenaire", partner_id=partner_id)
    for number in range(5):
        submit_rating(db, "soiree", f"session-{number:04d}", 2, "Trop de bruit",
                      moderate=lambda text: ("en_attente", {}))                     # Mistral injoignable
    services = CollectionServices(extract=None, connect=None, embed=None,
                                  moderate=lambda text: ("publie", {"flagged": []}))
    summary = review_ratings(db, services)
    assert summary["moderated"] == 5 and summary["total"] == 5 and summary["new_24h"] == 5
    assert summary["low_rated_partners"] == [{"partner": "Bar Test", "average": 2.0, "ratings": 5}]
    assert scalar(db, "SELECT count(*) FROM sortiria.event_ratings WHERE comment_status = 'publie'") == 5


# --- Interface : recherche, détail, partenaires (app/search_service.py) ----
def test_interface_search_queries_run(db: Any) -> None:
    from app import search_service as service
    from app.search_service import SearchFilters

    partner_id = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Ludo', 'enfants') RETURNING partner_id")
    insert_event(db, event_id="famille", city="Sète", audiences=["famille"], is_free=True,
                 partner_id=partner_id, embedding="[" + ",".join(["0.1"] * 1024) + "]")
    insert_event(db, event_id="soiree", city="Montpellier", audiences=["soiree"], price_text="12 €")
    now = datetime.now(PARIS)
    for vector in (None, "[" + ",".join(["0.2"] * 1024) + "]"):
        sql, params = service.build_search_query(SearchFilters(public="famille", zone="thau", free_only=True),
                                                 vector, now)
        assert [row["event_id"] for row in service.fetch_rows(db, sql, params)] == ["famille"]
    sql, params = service.build_search_query(SearchFilters(), "[" + ",".join(["0.2"] * 1024) + "]", now)
    assert [row["event_id"] for row in service.fetch_rows(db, sql, params)] == ["famille", "soiree"]
    highlights = service.partner_highlights(db)
    assert [card["event_id"] for card in highlights] == ["famille"] and highlights[0]["partner_name"] == "Ludo"
    assert [source["events"] for source in service.active_sources(db)] == [2]


def test_interface_detail_shows_published_venue_comments(db: Any) -> None:
    from app import search_service as service
    from src.event.event_ratings import submit_rating

    _rated_event(db, "hier", -1)
    submit_rating(db, "hier", "session-aaaa", 5, "Super lieu", moderate=lambda text: ("publie", {}))
    submit_rating(db, "hier", "session-bbbb", 1, "Insulte", moderate=lambda text: ("refuse", {}))
    insert_event(db, event_id="demain", venue_name="le jam", city="MONTPELLIER")
    detail = service.event_detail(db, "demain")
    assert [(c["stars"], c["text"], c["event_title"]) for c in detail["comments"]] == [(5, "Super lieu", "T")]
    assert detail["comments"][0]["event_date"]        # date de l'événement noté, pas celle de l'avis
    assert service.event_detail(db, "hier")["can_rate"]       # passé hier : encore affiché, pour être noté


def test_interface_detail_lists_other_dates_of_the_same_event(db: Any) -> None:
    from app import search_service as service

    soon = (datetime.now(PARIS) + timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)
    for index, title in enumerate(("PAUSE JEUX", "Pause  jeux", "PAUSE JEUX")):
        insert_event(db, event_id=f"pause{index}", title=title, venue_name="La Fabrique", city="Marseillan",
                     start_at=(soon + timedelta(days=7 * index)).isoformat())
    insert_event(db, event_id="ailleurs", title="PAUSE JEUX", venue_name="Autre", city="Marseillan")
    detail = service.event_detail(db, "pause0")
    assert [d["event_id"] for d in detail["other_dates"]] == ["pause1", "pause2"]


def test_interface_search_is_logged(db: Any) -> None:
    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    insert_event(db, event_id="e1", audiences=["famille"])
    events = list(service.run_search(db, "sortie", SearchFilters(public="famille"),
                                     SearchServices(embed=None, write=None)))
    result = events[-1]["result"]
    assert [card["event_id"] for card in result["events"]] == ["e1"] and result["request_id"] == 1
    assert scalar(db, "SELECT status || ':' || array_to_string(event_ids, ',') FROM sortiria.rag_requests") == "ok:e1"


# --- Revue du 15/10/2026 : balayage, référence de baisse, couverture, schéma --
def test_sweep_never_removes_a_past_event_kept_for_rating(db: Any) -> None:
    """Un concert d'hier n'est plus renvoyé par sa source : il reste 7 jours pour la notation."""

    insert_event(db, event_id="hier", start_at=(datetime.now(PARIS) - timedelta(days=1)).isoformat())
    insert_event(db, event_id="a_venir_disparu", start_at=(datetime.now(PARIS) + timedelta(days=2)).isoformat())
    with db.cursor() as cursor:
        cursor.execute("UPDATE sortiria.events SET last_seen_at = now() - interval '1 day'")
    assert repository.delete_unseen_events(db, ["openagenda"]) == 1
    assert scalar(db, "SELECT string_agg(event_id, ',') FROM sortiria.events") == "hier"
    assert scalar(db, "SELECT sum(events) FROM sortiria.deleted_event_stats WHERE reason = 'disparu'") == 1


def test_drop_reference_is_the_best_of_the_last_seven_nights(db: Any) -> None:
    """Nuit 1 : 1 000 ; nuit 2 : 184 (baisse). La nuit 3 se compare encore à 1 000, pas à 184."""

    for kept in (1000, 184):
        run_id = repository.record_collect_run(db, {"status": "partial"})
        repository.record_run_sources(db, run_id, [{"source_id": "datatourisme", "status": "ok", "events_kept": kept}])
    run_id = repository.record_collect_run(db, {"status": "failed"})
    repository.record_run_sources(db, run_id, [{"source_id": "datatourisme", "status": "echec", "events_kept": 0}])
    assert repository.fetch_previous_source_counts(db) == {"datatourisme": 1000}
    # Au-delà de 7 bilans, l'ancien record est oublié : une baisse durable devient la normale.
    for _ in range(7):
        run_id = repository.record_collect_run(db, {"status": "success"})
        repository.record_run_sources(db, run_id, [{"source_id": "datatourisme", "status": "ok", "events_kept": 190}])
    assert repository.fetch_previous_source_counts(db) == {"datatourisme": 190}


def test_weekly_coverage_from_the_database(db: Any) -> None:
    soon = (datetime.now(PARIS) + timedelta(days=2)).isoformat()
    far = (datetime.now(PARIS) + timedelta(days=30)).isoformat()
    insert_event(db, event_id="s1", city="Sète", audiences=["famille", "ados"], start_at=soon)
    insert_event(db, event_id="s2", city="Montpellier", audiences=["soiree"], start_at=soon)
    insert_event(db, event_id="loin", city="Montpellier", audiences=["soiree"], start_at=far)
    insert_event(db, event_id="sans_public", city="Montpellier", audiences=[], start_at=soon)
    assert repository.fetch_weekly_coverage(db, 7) == {
        "montpellier": {"famille": 0, "soiree": 1, "ados": 0, "seniors": 0},
        "bassin_de_thau": {"famille": 1, "soiree": 0, "ados": 1, "seniors": 0},
    }


def test_init_db_checks_every_table_and_view(db: Any) -> None:
    from scripts.init_db import EXPECTED_TABLES, EXPECTED_VIEWS

    found = list_schema_objects(db)
    assert missing_objects(found) == []
    assert {"event_ratings", "agent_proposals"} <= set(EXPECTED_TABLES)
    assert {"v_venue_ratings", "v_partner_ratings"} <= set(EXPECTED_VIEWS)


# --- Une nuit complète de l'agent sur la vraie base (15/10/2026) -------------
def _night_services(db: Any, articles: list, stats: list, failed: list) -> Any:
    """Services de l'agent branchés sur la base de test, Mistral et l'extraction simulés."""

    from contextlib import contextmanager

    from agent.services import CollectionServices, ExtractionOutput
    from src.event.event_enrichment import Enrichment
    from src.event.quality_judge import Verdict

    @contextmanager
    def connect():
        db.autocommit = False
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.autocommit = True

    def enrich(rows):
        return ({str(r["event_id"]): Enrichment(id=str(r["event_id"]), categorie="concert", publics=["soiree"],
                                                 resume="Un concert.") for r in rows},
                {"mistral-small-latest": {"input": 100_000, "output": 20_000}})

    def judge(rows):
        return ({str(r["event_id"]): Verdict(id=str(r["event_id"]), coherent=True, est_une_sortie=True,
                                             publics_corrects=True, categorie_correcte=False, probleme="catégorie")
                 for r in rows}, {"mistral-medium-latest": {"input": 10, "output": 5}})

    return CollectionServices(
        extract=lambda sources: ExtractionOutput(articles=articles, failed_sources=failed, extractor_stats=stats),
        connect=connect, embed=lambda texts: [[0.01] * 1024 for _ in texts],
        now=lambda: datetime.now(PARIS), default_sources=("openagenda", "document", "partenaire"),
        enrich=enrich, judge=judge, probe=lambda url: url.endswith("/2026/09/agenda.pdf"),
        ratings_enabled=True, moderate=lambda text: ("publie", {"flagged": []}),
        watch_inputs=lambda: {
            "documents": [{"name": "Agenda Culture", "end": datetime.now(PARIS).date() + timedelta(days=5),
                           "source_id": "agglopole", "url": "https://x.fr/uploads/2026/08/agenda.pdf"}],
            "partner_files": [{"name": "Ludothèque du Port", "file_name": "ludo.csv",
                               "updated": datetime.now(PARIS).date()}],
        },
    )


def test_a_full_night_touches_every_table(db: Any) -> None:
    """L'agent écrit ou lit chaque table : la preuve que le schéma et le code sont branchés."""

    from agent.graph import run_nightly_collection
    from src.event.event_ratings import submit_rating
    from src.event.event_schema import Event
    from src.event.openagenda_mapper import map_openagenda_event

    from .conftest import make_openagenda_item

    # Avant la nuit : un événement d'hier noté (gardé 7 jours), un vieux (à retirer), une note en attente.
    _rated_event(db, "hier", -1)
    _rated_event(db, "vieux", -12)
    submit_rating(db, "hier", "session-aaaa", 4, "Bien mais bruyant", moderate=lambda text: ("en_attente", {}))

    start = (datetime.now(PARIS) + timedelta(days=3)).replace(hour=20, minute=30, second=0, microsecond=0)
    articles = []
    for uid, title in ((1, "Concert Jazz"), (2, "Théâtre d'impro")):
        item = make_openagenda_item(timings=[{"start": start.isoformat(), "end": (start + timedelta(hours=2)).isoformat()}],
                                    uid=uid, title={"fr": title})
        articles.append({"title": title, "metadata": {"event": map_openagenda_event(item).to_dict()}})
    info = {"name": "Ludothèque du Port", "sector": "enfants", "website": "https://ludo.example",
            "sponsored_until": "2026-12-31", "file_name": "ludo.csv", "file_sha256": "b" * 64,
            "rows_total": 1, "rows_loaded": 1, "rows_rejected": 0, "rejection_reasons": {}}
    partner_event = Event(event_id="ludo1", source="partenaire", source_event_id="ludo:1", title="Atelier jeux",
                          start_at=start.isoformat(), city="Sète", venue_name="Ludothèque", url="https://ludo.example",
                          is_sponsored=True, audiences=["famille"], partner_info=info)
    articles.append({"title": "Atelier jeux", "metadata": {"event": partner_event.to_dict()}})
    stats = [
        {"source_id": "openagenda", "name": "OpenAgenda", "status": "success", "errors": []},
        {"source_id": "partenaire", "name": "Fichiers des partenaires", "status": "success", "errors": []},
        {"source_id": "document", "name": "Programmes PDF", "status": "failed",
         "errors": ["Agenda Culture : PDF introuvable (404) : https://x.fr/uploads/2026/08/agenda.pdf"]},
    ]
    services = _night_services(db, articles, stats, failed=["document"])

    state = run_nightly_collection(services, max_attempts=1)
    assert state["status"] == "partial_success" and state["run_id"]

    # events : les 3 nouveaux sont enregistrés, enrichis et vectorisés ; « vieux » est parti, « hier » reste.
    assert scalar(db, "SELECT count(*) FROM sortiria.events") == 4
    assert scalar(db, "SELECT count(*) FROM sortiria.events WHERE category_norm = 'concert' AND embedding IS NOT NULL "
                      "AND 'soiree' = ANY (audiences) AND enrichment_version <> ''") == 3
    assert scalar(db, "SELECT count(*) FROM sortiria.v_upcoming_events") == 3
    # partners, partner_imports, events.partner_id
    assert scalar(db, "SELECT count(*) FROM sortiria.partners WHERE name = 'Ludothèque du Port'") == 1
    assert scalar(db, "SELECT status FROM sortiria.partner_imports") == "importe"
    assert scalar(db, "SELECT count(*) FROM sortiria.events e JOIN sortiria.partners p USING (partner_id) WHERE e.is_sponsored") == 1
    # deleted_event_stats : « vieux » retiré, motif passé.
    assert scalar(db, "SELECT sum(events) FROM sortiria.deleted_event_stats WHERE reason = 'passe'") == 1
    # event_ratings : le commentaire en attente a été modéré pendant la nuit.
    assert scalar(db, "SELECT comment_status FROM sortiria.event_ratings") == "publie"
    # collect_runs : tous les champs de suivi sont remplis.
    row = db.cursor()
    row.execute("SELECT status, events_upserted, events_expired, events_embedded, cost_usd, llm_usage, coverage, "
                "node_durations, quality_score, quality, events_total, cardinality(alerts), failed_sources "
                "FROM sortiria.collect_runs WHERE run_id = %s", (state["run_id"],))
    (status, upserted, expired, embedded, cost, usage, coverage, durations, score, quality, total, alerts, failed) = row.fetchone()
    assert (status, upserted, expired, embedded, total, failed) == ("partial_success", 3, 1, 3, 4, ["document"])
    assert cost > 0 and "mistral-small-latest" in usage and "mistral-embed" in usage
    assert coverage["montpellier"]["soiree"] >= 2 and set(durations) >= {"extract_sources", "load_events", "watch"}
    assert float(score) == 0.75 and quality["checked"] == 3 and alerts >= 3
    # collect_run_sources : un statut par source, ok / echec.
    assert scalar(db, "SELECT string_agg(source_id || '=' || status, ',' ORDER BY source_id) "
                      "FROM sortiria.collect_run_sources WHERE run_id = %s", (state["run_id"],)) == \
        "document=echec,openagenda=ok,partenaire=ok"
    # agent_proposals : le PDF déplacé a sa nouvelle adresse, en attente d'un humain.
    assert scalar(db, "SELECT action ->> 'new' FROM sortiria.agent_proposals WHERE status = 'en_attente'") == \
        "https://x.fr/uploads/2026/09/agenda.pdf"
    # Les vues de suivi répondent.
    assert scalar(db, "SELECT events_total FROM sortiria.v_collect_health LIMIT 1") == 4
    assert scalar(db, "SELECT count(*) FROM sortiria.v_coverage") >= 1


def test_search_and_feedback_write_their_tables(db: Any) -> None:
    """La recherche de l'interface trace sa demande ; un avis s'y rattache ; la vue qualité les lit."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    insert_event(db, event_id="e1", audiences=["famille"], embedding="[" + ",".join(["0.1"] * 1024) + "]")
    events = list(service.run_search(
        db, "sortie en famille", SearchFilters(public="famille"),
        SearchServices(embed=lambda q: "[" + ",".join(["0.1"] * 1024) + "]",
                       write=lambda q, f, c: ('{"reponse": "Allez voir T [1].", "suggestions": ["Et dimanche ?"]}',
                                              {"input": 50, "output": 20}, "mistral-small-latest"))))
    request_id = events[-1]["result"]["request_id"]
    assert scalar(db, "SELECT answer || '|' || degraded::TEXT || '|' || (cost_usd > 0)::TEXT || '|' || llm_model "
                      "FROM sortiria.rag_requests WHERE request_id = %s", (request_id,)) == \
        "Allez voir T [1].|false|true|mistral-small-latest"
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.feedback (request_id, event_id, rating) VALUES (%s, 'e1', 1)", (request_id,))
    assert scalar(db, "SELECT requests || ':' || ratings || ':' || degraded_rate FROM sortiria.v_daily_quality") == "1:1:0.000"


def test_answer_feedback_is_recorded_replaced_and_limited_to_a_day(db: Any) -> None:
    """Pouce haut / pouce bas (16/10/2026) : un avis par réponse, le second
    remplace le premier, rien pour une réponse inconnue ou de plus d'un jour."""

    from app import search_service as service

    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.rag_requests (question, status) VALUES ('un concert', 'ok') "
                       "RETURNING request_id")
        request_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sortiria.rag_requests (question, status, asked_at) "
                       "VALUES ('vieille', 'ok', now() - interval '2 days') RETURNING request_id")
        old_id = cursor.fetchone()[0]
    assert service.record_feedback(db, request_id, 1) is True
    assert service.record_feedback(db, request_id, -1, "  Rien près de Sète.  ") is True
    assert scalar(db, "SELECT count(*) || ':' || min(rating) || ':' || min(comment) FROM sortiria.feedback") == \
        "1:-1:Rien près de Sète."
    assert service.record_feedback(db, old_id, 1) is False
    assert service.record_feedback(db, 999999, 1) is False
    with pytest.raises(ValueError):
        service.record_feedback(db, request_id, 0)


def test_refused_proposal_is_not_proposed_again(db: Any) -> None:
    from src.event import event_repository as repository

    proposal = {"source_id": "openagenda", "kind": "nouvel_agenda", "diagnosis": "Agenda 123",
                "action": {"type": "ajouter_agenda", "new": "123", "events": 7}}
    repository.record_proposals(db, None, [proposal])
    repository.record_proposals(db, None, [proposal])            # en attente : une seule fois
    [pending] = repository.fetch_proposals(db)
    repository.set_proposal_status(db, pending["proposal_id"], "refusee")
    repository.record_proposals(db, None, [proposal])            # refusé : pas reproposé
    assert repository.fetch_proposals(db) == []
    other = {**proposal, "action": {**proposal["action"], "new": "456"}}
    repository.record_proposals(db, None, [other])
    assert [p["action"]["new"] for p in repository.fetch_proposals(db)] == ["456"]


def test_hybrid_search_finds_names_dates_places_and_widens(db: Any) -> None:
    """Recherche hybride (15/10/2026) sur la vraie base : les mots-clés retrouvent
    ce que le sens seul rate, les contraintes écrites filtrent, et une question
    trop stricte est élargie au lieu de ne rien rendre."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    same = "[" + ",".join(["0.1"] * 1024) + "]"          # même vecteur pour tous : le sens ne départage pas
    soon = (datetime.now(PARIS) + timedelta(days=10)).replace(hour=15, minute=0, second=0, microsecond=0)
    insert_event(db, event_id="echecs", title="Tournoi d'échecs du Lido", city="Mèze", category_norm="jeux",
                 start_at=soon.isoformat(), embedding=same)
    insert_event(db, event_id="jazz", title="Soirée Blue Note", description="Quartet de jazz manouche.",
                 city="Montpellier", category_norm="concert",
                 start_at=soon.replace(hour=21).isoformat(), embedding=same)
    insert_event(db, event_id="poterie", title="Atelier poterie", city="Sète", category_norm="atelier",
                 start_at=soon.isoformat(), embedding=same, audiences=["famille"], is_free=True)

    services = SearchServices(embed=lambda text: same, write=None, now=lambda: datetime.now(PARIS))

    def search(question: str, **filters: Any) -> tuple[list[str], list[dict]]:
        events = list(service.run_search(db, question, SearchFilters(**filters), services))
        return [card["event_id"] for card in events[-1]["result"]["events"]], events

    assert search("un tournoi d'echecs")[0][0] == "echecs"          # mot-clé, sans accent
    assert search("du jazz")[0][0] == "jazz"                         # mot de la description
    assert search("quelque chose à Mèze")[0] == ["echecs"]          # commune écrite : filtre strict
    assert search("un concert le soir")[0] == ["jazz"]               # 21 h, pas 15 h
    assert search("pour les enfants, gratuit")[0] == ["poterie"]
    ids, events = search("un concert à Frontignan")                 # rien à Frontignan : élargi
    assert ids[0] == "jazz"
    assert any("recherche élargie" in (e.get("message") or "") for e in events)


def test_search_evaluation_runs_on_the_database(db: Any) -> None:
    """scripts/eval_recherche.py : mesures calculées, rien d'écrit dans rag_requests."""

    from app.search_service import SearchServices
    from scripts.eval_recherche import evaluate

    same = "[" + ",".join(["0.1"] * 1024) + "]"
    soon = (datetime.now(PARIS) + timedelta(days=10)).replace(hour=20, minute=0, second=0, microsecond=0)
    insert_event(db, event_id="jazz", title="Concert de jazz", city="Sète", category_norm="concert",
                 start_at=soon.isoformat(), embedding=same)
    insert_event(db, event_id="expo", title="Exposition photo", city="Sète", category_norm="exposition",
                 start_at=soon.isoformat(), embedding=same)
    services = SearchServices(embed=lambda text: same, write=None, now=lambda: datetime.now(PARIS), log=False)
    report = evaluate(db, [{"question": "un concert à Sète", "compris": {"communes": ["sete"], "types": ["concert"]}},
                           {"question": "du jazz", "mots": ["jazz"]}], services)
    assert report["comprehension"] == 1.0
    first = report["detail"][0]
    # Seul le concert est proposé : l'exposition n'est pas ajoutée pour faire nombre.
    assert first["titres"] == ["Concert de jazz"] and first["correctes"] == 1 and first["exact"]
    assert report["detail"][1]["titres"] == ["Concert de jazz"]          # mot précis « jazz »
    assert report["precision"] == 1.0 and report["sans_resultat"] == 0 and report["idees_voisines"] == 0
    neighbours = evaluate(db, [{"question": "du théâtre à Sète", "compris": {"types": ["theatre"]}}], services)
    assert neighbours["idees_voisines"] == 1 and not neighbours["detail"][0]["exact"]
    assert scalar(db, "SELECT count(*) FROM sortiria.rag_requests") == 0


def test_rating_from_the_interface(db: Any) -> None:
    """Bouton « Donner mon avis » (15/10/2026) : fenêtre d'avis dans le détail,
    événement d'hier encore affichable pour être noté, « Vos sorties à noter »."""

    from app import search_service as service
    from src.event.event_ratings import submit_rating

    _rated_event(db, "futur", 3)
    _rated_event(db, "hier", -1)
    _rated_event(db, "ancien", -10)
    future = service.event_detail(db, "futur")
    assert future["can_rate"] is False and future["rate_from"]          # « Vous pourrez noter à partir du … »
    yesterday = service.event_detail(db, "hier")                        # plus proposé, mais notable
    assert yesterday is not None and yesterday["can_rate"] is True
    assert service.event_detail(db, "ancien") is None                   # plus de 7 jours : fini

    session = "session-de-test-0123456789"
    seen = ["futur", "hier", "ancien", "inconnu"]
    assert [item["event_id"] for item in service.events_to_rate(db, seen, session)] == ["hier"]
    assert yesterday["my_rating"] is None and yesterday["venue_ratings"] == 0
    submit_rating(db, "hier", session, 4, "Très bien", moderate=lambda text: ("en_attente", {}))
    assert service.events_to_rate(db, seen, session) == []              # déjà noté : plus proposé
    # Le visiteur revoit son avis et son état (15/10/2026), même avant la
    # moyenne du lieu (affichée à partir de 3 notes) et avant la modération.
    from src.event.event_ratings import user_key

    mine = service.event_detail(db, "hier", visitor_key=user_key(session))
    assert mine["my_rating"]["stars"] == 4 and mine["my_rating"]["comment_status"] == "en_attente"
    assert mine["venue_ratings"] == 1 and mine["venue_rating"] is None and mine["comments"] == []
    assert service.event_detail(db, "hier", visitor_key=user_key("autre-visiteur-0123456789"))["my_rating"] is None
    assert [i["event_id"] for i in service.events_to_rate(db, seen, "autre-visiteur-0123456789")] == ["hier"]


def test_same_question_same_day_is_answered_from_the_cache(db: Any) -> None:
    """Cache des réponses (15/10/2026) : pas de second appel à Mistral pour la même
    demande le même jour ; une nouvelle collecte ou un autre jour l'invalident."""

    import json as json_module

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    same = "[" + ",".join(["0.1"] * 1024) + "]"
    insert_event(db, event_id="jazz", title="Concert de jazz", city="Sète", category_norm="concert", embedding=same)
    calls: list[str] = []

    def write(question, plan, cards):
        calls.append(question)
        return json_module.dumps({"reponse": "Allez voir [1].", "suggestions": ["Et demain ?"]}), \
            {"input": 100, "output": 20}, "mistral-small-latest"

    today = datetime.now(PARIS)
    services = SearchServices(embed=lambda text: same, write=write, now=lambda: today)

    def ask(question: str = "un concert de jazz", **filters: Any) -> list[dict]:
        return list(service.run_search(db, question, SearchFilters(**filters), services))

    first = ask()
    second = ask("  Un CONCERT de jazz  ")                               # même demande, écrite autrement
    assert len(calls) == 1 and second[-1]["result"]["answer"] == first[-1]["result"]["answer"]
    assert any("cache" in (event.get("message") or "") for event in second)
    assert scalar(db, "SELECT count(*) FROM sortiria.rag_requests WHERE from_cache") == 1
    assert scalar(db, "SELECT hits FROM sortiria.query_cache") == 1
    ask(zone="thau")                                                     # autres cases cochées : nouvelle réponse
    assert len(calls) == 2
    with db.cursor() as cursor:                                          # une nouvelle nuit de collecte
        cursor.execute("INSERT INTO sortiria.collect_runs (status) VALUES ('ok')")
    ask()
    assert len(calls) == 3
    services.now = lambda: today + timedelta(days=1)                    # le lendemain
    ask()
    assert len(calls) == 4


def test_cache_expires_at_midnight_at_the_latest() -> None:
    from app.search_service import cache_expiry

    evening = datetime(2026, 10, 15, 22, 0, tzinfo=PARIS)
    assert cache_expiry(evening) == datetime(2026, 10, 16, 0, 0, tzinfo=PARIS)
    morning = datetime(2026, 10, 15, 9, 0, tzinfo=PARIS)
    assert cache_expiry(morning) == datetime(2026, 10, 15, 15, 0, tzinfo=PARIS)


def test_partner_offers_when_nothing_matches_exactly(db: Any) -> None:
    """Pas de réponse exacte : les offres partenaires les plus proches (15/10/2026)."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    near = "[" + ",".join(["0.1"] * 1024) + "]"
    far = "[" + ",".join(["-0.1"] * 512 + ["0.1"] * 512) + "]"
    ludo = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Ludo', 'enfants') RETURNING partner_id")
    insert_event(db, event_id="expo", title="Exposition photo", city="Sète", category_norm="exposition", embedding=near)
    # Les offres partenaires sont à Mèze : rien au même endroit, on élargit à toute la zone.
    insert_event(db, event_id="jeux", title="Après-midi jeux", city="Mèze", category_norm="jeux", embedding=near,
                 partner_id=ludo)
    insert_event(db, event_id="loin", title="Atelier poterie", city="Mèze", category_norm="atelier", embedding=far,
                 partner_id=ludo)
    services = SearchServices(embed=lambda text: near, write=None, now=lambda: datetime.now(PARIS), cache=False)

    def result(question: str) -> dict:
        return list(service.run_search(db, question, SearchFilters(), services))[-1]["result"]

    # Un sujet absent partout (théâtre) : ni carte ni offre sans rapport (15/10/2026).
    theatre = result("du théâtre à Sète")
    assert theatre["exact"] is False and theatre["events"] == [] and theatre["partner_offers"] == []
    assert "théâtre" in theatre["answer"]
    # Sans sujet, rien à Frontignan : idées voisines, et les offres partenaires
    # les plus proches à part (hors des cartes).
    nothing = result("une sortie à Frontignan")
    assert nothing["exact"] is False and [card["event_id"] for card in nothing["events"]] == ["expo"]
    assert [offer["event_id"] for offer in nothing["partner_offers"]][:2] == ["jeux", "loin"]   # la plus proche d'abord
    assert all(offer["partner"] for offer in nothing["partner_offers"])
    # Le sujet existe ailleurs : il est proposé (poterie à Mèze pour « poterie à Sète »).
    elsewhere = result("de la poterie à Sète")
    assert [card["event_id"] for card in elsewhere["events"]] == ["loin"] and elsewhere["events"][0]["partner"]
    exact = result("une exposition à Sète")
    assert exact["exact"] is True and exact["partner_offers"] == []
    shown = result("des jeux à Mèze")                             # déjà parmi les propositions : pas en double
    assert "jeux" not in [offer["event_id"] for offer in shown["partner_offers"]]


def test_budget_setting_distance_and_diversity_on_the_database(db: Any) -> None:
    """Budget, pluie, distance et diversité des lieux (15/10/2026), sur la vraie base."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    same = "[" + ",".join(["0.1"] * 1024) + "]"
    sete, lunel = (43.40, 3.69), (43.68, 4.13)                         # Lunel : hors de la zone, ~40 km
    insert_event(db, event_id="sete", title="Concert au port", city="Sète", latitude=sete[0], longitude=sete[1],
                 category_norm="concert", price_min=12, embedding=same)
    insert_event(db, event_id="balaruc", title="Concert au lac", city="Balaruc-les-Bains", latitude=43.44,
                 longitude=3.68, category_norm="concert", price_min=30, embedding=same)
    insert_event(db, event_id="loin", title="Concert lointain", city="Lunel", latitude=lunel[0], longitude=lunel[1],
                 category_norm="concert", is_free=True, embedding=same)
    insert_event(db, event_id="balade", title="Balade nature sur le lido", city="Sète", category_norm="nature",
                 is_free=True, embedding=same)
    services = SearchServices(embed=lambda text: same, write=None, now=lambda: datetime.now(PARIS), cache=False)

    def ids(question: str) -> list[str]:
        result = list(service.run_search(db, question, SearchFilters(), services))[-1]["result"]
        return sorted(card["event_id"] for card in result["events"])

    assert ids("un concert près de Sète") == ["balaruc", "sete"]          # Balaruc à ~5 km, Lunel trop loin
    assert ids("un concert à moins de 15 €") == ["loin", "sete"]          # gratuit ou 12 €, pas 30 €
    assert "balade" not in ids("que faire s'il pleut")
    assert ids("une sortie en plein air") == ["balade"]

    venue = "[" + ",".join(["0.1"] * 1024) + "]"
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM sortiria.events")
    for index in range(5):
        insert_event(db, event_id=f"media{index}", title=f"Atelier numéro {index}", venue_name="Médiathèque",
                     city="Sète", category_norm="atelier", embedding=venue)
    insert_event(db, event_id="autre", title="Atelier au musée", venue_name="Musée", city="Sète",
                 category_norm="atelier", embedding=venue)
    result = list(service.run_search(db, "un atelier à Sète", SearchFilters(), services))[-1]["result"]
    ordered = [card["event_id"] for card in result["events"]]
    # Deux ateliers de la médiathèque, puis le musée, puis on complète avec la médiathèque.
    assert ordered.index("autre") == 2 and len(ordered) == 6


def test_highlights_show_every_partner_first(db: Any) -> None:
    """« À ne pas manquer » : une offre par partenaire d'abord, mises en avant en tête,
    et jusqu'à 3 mois (15/10/2026 : avant, 30 jours seulement)."""

    from app import search_service as service

    bar = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Bar', 'soiree') RETURNING partner_id")
    ludo = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Ludo', 'enfants') RETURNING partner_id")
    soon = datetime.now(PARIS) + timedelta(days=2)
    for index in range(3):
        insert_event(db, event_id=f"bar{index}", title=f"Soirée {index}", partner_id=bar,
                     start_at=(soon + timedelta(days=index)).isoformat())
    insert_event(db, event_id="ludo", title="Jeux en famille", partner_id=ludo,
                 start_at=(soon + timedelta(days=50)).isoformat())                  # au-delà de 30 jours
    insert_event(db, event_id="vedette", title="Soirée mise en avant", partner_id=bar, is_sponsored=True,
                 start_at=(soon + timedelta(days=20)).isoformat())
    assert [card["event_id"] for card in service.partner_highlights(db, 3)] == ["vedette", "ludo", "bar0"]


def test_card_counts_every_date_like_the_detail(db: Any) -> None:
    """Cas réels (15/10/2026) : « NOCTURNE » affichait « + 1 date » alors que le
    détail en listait 3 ; « LA CRYPTE » montrait le 12 déc. au lieu du 17 oct."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    near = "[" + ",".join(["0.1"] * 1024) + "]"
    far = "[" + ",".join(["-0.1"] * 1024) + "]"
    soon = (datetime.now(PARIS) + timedelta(days=3)).replace(hour=21, minute=0, second=0, microsecond=0)
    # Seule la séance la plus lointaine ressemble à la question (vecteur proche) ;
    # les autres ne sont pas trouvées par la recherche elle-même.
    for index, (days, vector) in enumerate(((50, near), (0, far), (30, far))):
        insert_event(db, event_id=f"crypte{index}", title="LA CRYPTE", venue_name="", city="Montpellier",
                     category_norm="spectacle", start_at=(soon + timedelta(days=days)).isoformat(), embedding=vector)
    services = SearchServices(embed=lambda text: near, write=None, now=lambda: datetime.now(PARIS), cache=False)
    service.POOL, saved = 1, service.POOL                                 # la recherche ne voit qu'une séance
    try:
        result = list(service.run_search(db, "un spectacle", SearchFilters(), services))[-1]["result"]
    finally:
        service.POOL = saved
    card = next(card for card in result["events"] if card["title"] == "LA CRYPTE")
    assert card["event_id"] == "crypte1"                                  # la prochaine séance, pas la lointaine
    assert [d["event_id"] for d in card["other_dates"]] == ["crypte2", "crypte0"]
    detail = service.event_detail(db, card["event_id"])
    assert len(detail["other_dates"]) == len(card["other_dates"])         # même compte que le détail


def test_title_vocabulary_tells_subjects_from_moods(db: Any) -> None:
    """Sujets reconnus d'après les titres de la base, sans liste écrite à la main (15/10/2026)."""

    from app import search_service as service
    from src.event import event_repository as repository

    insert_event(db, event_id="expo", title="Exposition de photographies", city="Bouzigues")
    insert_event(db, event_id="poterie", title="Atelier poterie", city="Sète")
    insert_event(db, event_id="offre", title="Cirque d'emploi", is_outing=False)   # pas une sortie : ignoré
    words = ["photo", "poterie", "romantique", "cirque"]
    # Vocabulaire pas encore calculé : les titres sont lus directement.
    assert service.known_subjects(db, words) == {"photo", "poterie"}
    assert repository.refresh_title_vocabulary(db) >= 4
    assert scalar(db, "SELECT events FROM sortiria.title_vocabulary WHERE lexeme = 'poter'") == 1
    assert service.known_subjects(db, words) == {"photo", "poterie"}
    # « photo » trouve « photographies » (préfixe) ; « art » (court) ne trouve pas « atelier ».
    assert service.known_subjects(db, ["art"]) == set()


def test_search_gaps_report_what_users_did_not_find(db: Any) -> None:
    """Bilan de la recherche : questions sans réponse exacte et mots inconnus de la base (15/10/2026)."""

    import json

    from src.event import event_repository as repository

    def ask(question: str, status: str = "ok", **recherche: Any) -> None:
        with db.cursor() as cursor:
            cursor.execute("INSERT INTO sortiria.rag_requests (question, status, constraints) VALUES (%s, %s, %s)",
                           (question, status, json.dumps({"recherche": recherche})))

    ask("du jazz", exact=True)
    ask("de la zumba", exact=False, mots_inconnus=["zumba"])
    ask("De la zumba ", exact=False, mots_inconnus=["zumba"])
    ask("du cirque", status="aucun_resultat", exact=False, sujet_introuvable=True)
    ask("(filtres seuls)", exact=False)                                   # pas une question
    with db.cursor() as cursor:                                           # trop ancienne
        cursor.execute("INSERT INTO sortiria.rag_requests (question, asked_at, constraints) "
                       "VALUES ('vieux', now() - interval '10 days', '{\"recherche\": {\"exact\": false}}')")
    gaps = repository.fetch_search_gaps(db, days=7)
    assert (gaps["questions"], gaps["not_exact"], gaps["no_answer"]) == (4, 3, 1)
    assert gaps["unknown_words"] == [("zumba", 2)]
    assert gaps["unanswered"] == [("de la zumba", 2), ("du cirque", 1)]


def test_search_finds_a_known_subject_beyond_the_ticked_boxes(db: Any) -> None:
    """« photo » avec Famille coché : l'expo (tout public, non marquée famille)
    est proposée plutôt que des ateliers sans rapport ; « romantique », écrit
    dans aucun titre, garde les idées proches par le sens (15/10/2026)."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices
    from src.event import event_repository as repository

    near = "[" + ",".join(["0.1"] * 1024) + "]"
    insert_event(db, event_id="expo", title="Exposition photo de l'étang", city="Bouzigues", is_free=True,
                 category_norm="exposition", embedding=near)
    insert_event(db, event_id="jeux", title="Après-midi jeux", city="Sète", is_free=True, audiences=["famille"],
                 category_norm="jeux", embedding=near)
    repository.refresh_title_vocabulary(db)
    services = SearchServices(embed=lambda text: near, write=None, now=lambda: datetime.now(PARIS), cache=False)

    def result(question: str) -> dict:
        filters = SearchFilters(public="famille", period="3mois", free_only=True)
        return list(service.run_search(db, question, filters, services))[-1]["result"]

    photo = result("photo")
    assert [card["event_id"] for card in photo["events"]] == ["expo"] and photo["exact"] is False
    mood = result("un endroit romantique")
    assert [card["event_id"] for card in mood["events"]] == ["jeux"]
    logged = scalar(db, "SELECT constraints -> 'recherche' FROM sortiria.rag_requests ORDER BY request_id DESC LIMIT 1")
    assert logged["mots_inconnus"] == ["romantique"] and logged["exact"] is False


def test_partner_workshop_named_after_the_type_comes_first(db: Any) -> None:
    """Capture du 15/10/2026 : « jeux » ; l'« Atelier jeux » du partenaire (rangé
    en atelier) était relégué en bas. Le type écrit dans le titre compte."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    near = "[" + ",".join(["0.1"] * 1024) + "]"
    ludo = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Ludo', 'enfants') RETURNING partner_id")
    insert_event(db, event_id="pause", title="Pause jeux", city="Balaruc-les-Bains", category_norm="jeux",
                 audiences=["famille"], embedding=near)
    insert_event(db, event_id="ludo", title="Atelier jeux en famille", city="Sète", category_norm="atelier",
                 audiences=["famille"], partner_id=ludo, embedding=near)
    insert_event(db, event_id="poterie", title="Atelier poterie", city="Sète", category_norm="atelier",
                 audiences=["famille"], embedding=near)
    services = SearchServices(embed=lambda text: near, write=None, now=lambda: datetime.now(PARIS), cache=False)
    result = list(service.run_search(db, "jeux", SearchFilters(public="famille", period="3mois"), services))[-1]
    cards = result["result"]["events"]
    assert [card["event_id"] for card in cards] == ["ludo", "pause"] and cards[0]["partner"]
    assert result["result"]["exact"] is True and result["result"]["partner_offers"] == []


def test_unclassified_events_keep_their_source_category(db: Any) -> None:
    """Audit du 15/10/2026 : tant que Mistral n'a pas classé un événement,
    category_norm vaut '' ; la catégorie de la source doit compter (« Lecture »
    d'un partenaire pour « lecture », « Soirée DJ » d'un bar pour « soirée »)."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    insert_event(db, event_id="contes", title="Contes au jardin", category="Lecture", city="Balaruc-les-Bains")
    insert_event(db, event_id="dj", title="Nuit électro", category="Soirée DJ", city="Montpellier",
                 audiences=["soiree"])
    insert_event(db, event_id="jeudi", title="Les jeudis du port", category="Concert", city="Sète")
    services = SearchServices(embed=None, write=None, now=lambda: datetime.now(PARIS), cache=False, log=False)

    def ids(question: str) -> list[str]:
        result = list(service.run_search(db, question, SearchFilters(period="3mois"), services))[-1]["result"]
        return [card["event_id"] for card in result["events"]] if result["exact"] else []

    assert ids("une lecture") == ["contes"]
    assert ids("une soirée") == ["dj"]
    assert "jeudi" not in ids("des jeux")             # racine exacte : « jeux » ne trouve pas « jeudis »


def test_learned_synonym_answers_the_failed_search(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """La boucle complète (15/10/2026) : « zumba » ne trouve rien ; une fois le
    synonyme accepté (« zumba » -> « fitness »), la même question trouve le cours."""

    from app import query_understanding
    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    insert_event(db, event_id="fitness", title="Cours de fitness en plein air", city="Sète")
    services = SearchServices(embed=None, write=None, now=lambda: datetime.now(PARIS), cache=False, log=False)

    def ask() -> dict:
        return list(service.run_search(db, "de la zumba", SearchFilters(period="3mois"), services))[-1]["result"]

    monkeypatch.setattr(query_understanding, "learned_synonyms", lambda path=None: {})
    before = ask()
    assert before["exact"] is False and before["gaps"]["mots_inconnus"] == ["zumba"]
    monkeypatch.setattr(query_understanding, "learned_synonyms", lambda path=None: {"zumba": "fitness"})
    after = ask()
    assert after["exact"] is True and [card["event_id"] for card in after["events"]] == ["fitness"]


def test_partner_offer_beyond_the_first_candidates_still_comes_first(db: Any) -> None:
    """Capture du 15/10/2026 : cases « Soirée » et « 3 mois » sans question ; le
    karaoké partenaire du 30/10 tombait après les 80 premières séances (triées
    par date) et n'apparaissait pas. Les séances partenaires sont toujours
    examinées ; le type s'affiche « soirée »."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    bar = scalar(db, "INSERT INTO sortiria.partners (name, sector) VALUES ('Bar', 'soiree') RETURNING partner_id")
    soon = datetime.now(PARIS).replace(hour=21, minute=0, second=0, microsecond=0)
    for index in range(service.CANDIDATES + 10):
        insert_event(db, event_id=f"s{index}", title=f"Soirée {index}", city="Montpellier", audiences=["soiree"],
                     start_at=(soon + timedelta(days=1, minutes=index)).isoformat(), venue_name=f"Lieu {index}")
    insert_event(db, event_id="karaoke", title="Karaoké géant", city="Montpellier", audiences=["soiree"],
                 category_norm="soiree", partner_id=bar, start_at=(soon + timedelta(days=15)).isoformat())
    services = SearchServices(embed=None, write=None, now=lambda: datetime.now(PARIS), cache=False, log=False)
    result = list(service.run_search(db, "", SearchFilters(public="soiree", period="3mois"), services))[-1]["result"]
    first = result["events"][0]
    assert first["event_id"] == "karaoke" and first["partner"] is True
    # Le type s'affiche « soirée » ; le public reste dans les données (filtres,
    # évaluation) et l'interface ne le répète pas à côté du type.
    assert first["category_label"] == "soirée" and first["audiences"] == ["soirée"]


def test_several_precise_words_keep_the_events_that_have_them_all(db: Any) -> None:
    """Capture du 15/10/2026 : « blind test » gardait 6 intrus qui contiennent
    « tester » (une dictée, des dégustations). Seuls les événements qui ont le
    plus de mots précis de la question sont gardés."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    insert_event(db, event_id="blind", title="Blind test géant", city="Montpellier", category_norm="jeux")
    insert_event(db, event_id="vin", title="Le blind test du vin", city="Montpellier", category_norm="jeux")
    insert_event(db, event_id="dictee", title="Dictée", city="Sète", category_norm="lecture",
                 description="Venez tester votre orthographe.")
    insert_event(db, event_id="fromage", title="Vins et fromages", city="Sète", category_norm="atelier",
                 description="Testez les accords parfaits.")
    services = SearchServices(embed=None, write=None, now=lambda: datetime.now(PARIS), cache=False, log=False)

    def ids(question: str) -> set[str]:
        result = list(service.run_search(db, question, SearchFilters(period="3mois"), services))[-1]["result"]
        return {card["event_id"] for card in result["events"]}

    assert ids("un blind test") == {"blind", "vin"}
    assert ids("une dictée") == {"dictee"}                   # un seul mot : comme avant


def test_words_seen_on_the_card_come_before_words_hidden_in_the_description(db: Any) -> None:
    """Évaluation des 100 recherches (15/10/2026) : « karaoké » ramenait un concert
    acoustique dont seule la longue description cite un karaoké. Quand des
    événements montrent le mot (titre, résumé, lieu), seuls eux sont gardés."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    insert_event(db, event_id="karaoke", title="Karaoké géant", city="Montpellier")
    insert_event(db, event_id="concert", title="Concert acoustique", city="Montpellier",
                 description="Après le concert, la soirée se termine par un karaoké.")
    insert_event(db, event_id="tango", title="Quatuor jazz", city="Sète", description="Un détour par le tango.")
    services = SearchServices(embed=None, write=None, now=lambda: datetime.now(PARIS), cache=False, log=False)

    def ids(question: str) -> list[str]:
        result = list(service.run_search(db, question, SearchFilters(period="3mois"), services))[-1]["result"]
        return [card["event_id"] for card in result["events"]]

    assert ids("karaoké") == ["karaoke"]
    assert ids("tango") == ["tango"]           # le mot n'est que dans une description : on la garde


def test_the_card_shows_the_session_that_answers(db: Any) -> None:
    """Évaluation des 100 recherches (15/10/2026) : « Halloween » trouvait la
    séance du 31/10 de l'atelier jeux, mais la carte montrait celle du 24/10
    (sans Halloween). La carte montre la séance trouvée ; « bière » cité dans la
    seule description donne une idée voisine ; « feu d'artifice » n'est pas
    trouvé avec « feu » seulement."""

    from app import search_service as service
    from app.search_service import SearchFilters, SearchServices

    first = (datetime.now(PARIS) + timedelta(days=9)).replace(hour=14, minute=30, second=0, microsecond=0)
    insert_event(db, event_id="j24", title="Atelier jeux en famille", venue_name="Ludothèque", city="Sète",
                 start_at=first.isoformat(), summary="Jeux de société pour petits et grands.")
    insert_event(db, event_id="j31", title="Atelier jeux en famille", venue_name="Ludothèque", city="Sète",
                 start_at=(first + timedelta(days=7)).isoformat(), summary="Spécial Halloween : jeux d'enquête.")
    insert_event(db, event_id="bar", title="Programmation du Réservoir", city="Montpellier",
                 description="Concerts, tournois et dégustation de bière artisanale.")
    insert_event(db, event_id="feu", title="Contes au coin du feu", city="Mèze")
    services = SearchServices(embed=None, write=None, now=lambda: datetime.now(PARIS), cache=False, log=False)

    def search(question: str) -> dict:
        return list(service.run_search(db, question, SearchFilters(period="3mois"), services))[-1]["result"]

    halloween = search("Halloween")
    assert [card["event_id"] for card in halloween["events"]] == ["j31"] and halloween["exact"] is True
    assert [other["event_id"] for other in halloween["events"][0]["other_dates"]] == ["j24"]
    beer = search("de la bière")
    assert [card["event_id"] for card in beer["events"]] == ["bar"] and beer["exact"] is False
    fireworks = search("un feu d'artifice")
    assert fireworks["exact"] is False
