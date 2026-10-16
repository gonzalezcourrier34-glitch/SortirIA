"""Tableaux Grafana (16/10/2026) : requêtes du tableau « Modèles et coûts »
exécutées sur un vrai PostgreSQL, et choix de la base sur les trois tableaux."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from monitoring.grafana import construire_tableau_modeles as tableau

DASHBOARDS = Path(__file__).resolve().parents[2] / "monitoring" / "grafana" / "provisioning" / "dashboards"
TEST_URL = os.environ.get("SORTIRIA_TEST_DATABASE_URL", "")


def test_every_panel_reads_the_chosen_database() -> None:
    for path in DASHBOARDS.glob("sortiria-*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        assert dashboard["templating"]["list"][0]["name"] == "source", path.name
        uids = set(re.findall(r'"uid": "([^"]+)"', json.dumps(dashboard)))
        assert uids <= {"${source}", dashboard["uid"]}, (path.name, uids)


def test_generated_dashboard_matches_the_builder() -> None:
    built = tableau.build()
    saved = json.loads((DASHBOARDS / "sortiria-modeles-couts.json").read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(built)), "relancer : uv run python -m monitoring.grafana.construire_tableau_modeles"
    titles = [panel["title"] for panel in built["panels"]]
    assert "Coût Mistral du mois" in titles and "Tokens par modèle, collecte de la nuit" in titles


@pytest.fixture()
def db() -> Any:
    from scripts.jeu_test_recherche import is_test_database

    if not is_test_database(TEST_URL):
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


def test_every_query_runs_on_postgresql(db: Any) -> None:
    with db.cursor() as cursor:
        cursor.execute("""INSERT INTO sortiria.collect_runs (status, cost_usd, llm_usage, quality_score)
                          VALUES ('success', 0.005, '{"mistral-small-latest": {"input": 1000, "output": 200},
                                                     "mistral-embed": {"input": 5000}}', 0.9)""")
        cursor.execute("""INSERT INTO sortiria.rag_requests (question, llm_model, prompt_tokens, completion_tokens,
                          cost_usd, prompt_version, latency_ms) VALUES ('un concert', 'mistral-small-latest',
                          900, 150, 0.000118, '2026-10-16.2', 3000) RETURNING request_id""")
        request_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO sortiria.feedback (request_id, rating) VALUES (%s, 1)", (request_id,))
        results = {}
        for name, sql in tableau.QUERIES.items():
            cursor.execute(re.sub(r"\$__timeFilter\(([^)]+)\)", r"(\1 IS NOT NULL)", sql))
            results[name] = cursor.fetchall()
    assert float(results["MONTH_COST"][0][0]) == pytest.approx(0.005118)
    assert {row[1] for row in results["NIGHT_TOKENS"]} == {"mistral-small-latest", "mistral-embed"}
    assert results["PROMPT_VERSIONS"][0][0] == "2026-10-16.2" and float(results["PROMPT_VERSIONS"][0][6]) == 1.0
    assert len(results["MODELS_TABLE"]) == 3


def test_grafana_reader_can_read_but_not_write(db: Any) -> None:
    """Le compte de Grafana sur la production ne peut rien modifier."""

    from scripts.creer_lecteur_grafana import ROLE, create_reader

    psycopg2 = pytest.importorskip("psycopg2")
    create_reader(db, "un-mot-de-passe-de-test-assez-long")
    create_reader(db, "un-mot-de-passe-de-test-assez-long")       # relancer ne casse rien
    with db.cursor() as cursor:
        # Le serveur ne reçoit que l'empreinte SCRAM, jamais le mot de passe en clair.
        cursor.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (ROLE,))
        stored = cursor.fetchone()[0]
        assert stored.startswith("SCRAM-SHA-256$4096:") and "un-mot-de-passe" not in stored
        cursor.execute(f"SET ROLE {ROLE}")
        try:
            cursor.execute("SELECT count(*) FROM sortiria.rag_requests")
            cursor.execute("SELECT count(*) FROM sortiria.v_upcoming_events")
            with pytest.raises(psycopg2.Error):
                cursor.execute("INSERT INTO sortiria.rag_requests (question) VALUES ('x')")
        finally:
            cursor.execute("RESET ROLE")


def test_production_is_the_default_database() -> None:
    for path in DASHBOARDS.glob("sortiria-*.json"):
        variable = json.loads(path.read_text(encoding="utf-8"))["templating"]["list"][0]
        assert variable["current"]["value"] == "sortiria-prod", path.name


def test_text_stat_panels_name_their_column() -> None:
    """Un panneau « stat » ignore les colonnes texte si on ne les nomme pas (« No data »)."""

    panel = next(panel for panel in json.loads((DASHBOARDS / "sortiria-collecte.json").read_text(encoding="utf-8"))
                 ["panels"] if panel["title"] == "Dernière collecte : statut")
    assert panel["options"]["reduceOptions"]["fields"] == "/^status$/"


def test_collect_health_on_postgresql(db: Any) -> None:
    """Route /api/v1/sante : âge de la dernière collecte réussie, statut de la dernière."""

    from app.search_service import collect_health

    assert collect_health(db) == {"statut": None, "heures_depuis_reussite": None}
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO sortiria.collect_runs (status, started_at) VALUES "
                       "('success', now() - interval '30 hours'), ('failed', now() - interval '2 hours')")
    health = collect_health(db)
    assert health["statut"] == "failed" and health["heures_depuis_reussite"] == pytest.approx(30, abs=0.1)


def test_proposals_with_pull_requests_on_postgresql(db: Any) -> None:
    """Colonnes de la PR, décision notée « github », panneaux Grafana des propositions."""

    from src.event import event_repository as repository

    repository.record_proposals(db, None, [
        {"source_id": "recherche", "kind": "synonyme", "diagnosis": "zumba cherché 4 fois",
         "action": {"type": "ajouter_synonyme", "word": "zumba", "target": "danse", "new": "zumba -> danse"}},
        {"source_id": "openagenda", "kind": "agenda", "diagnosis": "agenda utile",
         "action": {"type": "ajouter_agenda", "new": "123"}}])
    first, second = repository.fetch_proposals(db)
    assert first["pr_number"] is None and first["pr_url"] == ""
    repository.set_proposal_pull_request(db, first["proposal_id"], 7, "https://github.com/x/pull/7")
    repository.set_proposal_status(db, second["proposal_id"], "refusee", decided_by="github")
    [pending] = repository.fetch_proposals(db)
    assert pending["pr_number"] == 7 and pending["pr_url"].endswith("/7")
    with db.cursor() as cursor:
        cursor.execute("SELECT decided_by FROM sortiria.agent_proposals WHERE proposal_id = %s",
                       (second["proposal_id"],))
        assert cursor.fetchone()[0] == "github"
        rows = {}
        for name in ("PROPOSALS_COUNTS", "PROPOSALS_PENDING", "PROPOSALS_DECIDED"):
            cursor.execute(re.sub(r"\$__timeFilter\(([^)]+)\)", r"(\1 IS NOT NULL)", tableau.QUERIES[name]))
            rows[name] = cursor.fetchall()
    assert rows["PROPOSALS_COUNTS"] == [(1, 0, 1)]
    assert rows["PROPOSALS_PENDING"][0][-1].endswith("/pull/7")
    assert rows["PROPOSALS_DECIDED"][0][3:5] == ("refusee", "github")


def test_scram_verifier_has_the_postgresql_format() -> None:
    from scripts.creer_lecteur_grafana import scram_sha256

    verifier = scram_sha256("secret", salt=bytes(16))
    assert verifier == scram_sha256("secret", salt=bytes(16))          # déterministe à sel égal
    assert verifier != scram_sha256("autre", salt=bytes(16))
    method, rest = verifier.split("$", 1)
    params, keys = rest.split("$")
    assert method == "SCRAM-SHA-256" and params == "4096:AAAAAAAAAAAAAAAAAAAAAA=="
    assert all(len(key) == 44 for key in keys.split(":"))               # 32 octets en base64


def test_scram_verifier_matches_postgresql(db: Any) -> None:
    """Même empreinte que celle que PostgreSQL calcule lui-même, à sel égal."""

    import base64

    from scripts.creer_lecteur_grafana import scram_sha256

    with db.cursor() as cursor:
        cursor.execute("SET password_encryption = 'scram-sha-256'")
        cursor.execute("DROP ROLE IF EXISTS essai_scram")
        cursor.execute("CREATE ROLE essai_scram PASSWORD 'mot-de-passe-123'")
        cursor.execute("SELECT rolpassword FROM pg_authid WHERE rolname = 'essai_scram'")
        computed_by_postgresql = cursor.fetchone()[0]
        cursor.execute("DROP ROLE essai_scram")
    salt = base64.b64decode(computed_by_postgresql.split("$")[1].split(":")[1])
    assert scram_sha256("mot-de-passe-123", salt=salt) == computed_by_postgresql
