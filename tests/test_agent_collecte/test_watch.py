"""Tests des décisions de surveillance de l'agent (agent/watch.py et nœud watch).

**Provenance** : nouveau fichier écrit pour SortirIA (14/10/2026). Aucun
réseau, aucune base, aucun appel Mistral.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agent import watch
from agent.graph import run_nightly_collection
from agent.report import build_report_email
from agent.services import ExtractionOutput

from .test_agent import NOW, FakeDatabase, make_article, make_services

TODAY = date(2026, 10, 20)


def event(city: str, start: str, audiences: list[str], end: str = "", **extra: Any) -> dict[str, Any]:
    return {"city": city, "start_at": start, "end_at": end, "audiences": audiences, **extra}


# --- 1. Programmes PDF -------------------------------------------------------

def test_document_expiry() -> None:
    documents = [
        {"name": "Agenda Culture sept.-déc.", "end": date(2026, 11, 5)},    # dans 16 jours
        {"name": "Programme d'été", "end": date(2026, 8, 31)},              # terminé
        {"name": "Saison 2026-2027", "end": date(2027, 6, 30)},             # loin
        {"name": "Sans période", "end": None},
    ]
    alerts = watch.check_documents(documents, TODAY)
    assert len(alerts) == 2
    assert "se termine le 05/11/2026 (dans 16 jour(s))" in alerts[0]
    assert "terminé depuis le 31/08/2026" in alerts[1]


# --- 2. Partenaires ------------------------------------------------------------

def test_inactive_partners() -> None:
    files = [
        {"name": "Ludothèque", "file_name": "ludo.xlsx", "updated": date(2026, 10, 10)},     # récent, des séances
        {"name": "Bar", "file_name": "bar.csv", "updated": date(2026, 10, 1)},               # plus de séance
        {"name": "Festival", "file_name": "festival.json", "updated": date(2026, 8, 1)},     # trop ancien
    ]
    events = [
        {"partner_info": {"file_name": "ludo.xlsx"}},
        {"partner_info": {"file_name": "festival.json"}},
        {"source": "openagenda"},
    ]
    alerts = watch.check_partners(files, events, TODAY)
    assert alerts == [
        "Partenaire « Bar » : plus aucune séance à venir dans bar.csv, le relancer.",
        "Partenaire « Festival » : festival.json non mis à jour depuis 80 jours, demander son nouveau programme.",
    ]


# --- 3. Couverture -------------------------------------------------------------

def test_zone_from_city_then_gps() -> None:
    assert watch.zone_of({"city": "Castelnau-le-Lez"}) == "montpellier"
    assert watch.zone_of({"city": "MÈZE"}) == "bassin_de_thau"
    assert watch.zone_of({"city": "", "latitude": 43.40, "longitude": 3.69}) == "bassin_de_thau"   # Sète
    assert watch.zone_of({"city": "Nîmes", "latitude": 43.84, "longitude": 4.36}) == "hors_zone"


def test_coverage_counts_the_coming_week() -> None:
    events = [
        event("Montpellier", "2026-10-22T20:00:00+02:00", ["soiree"]),
        event("Montpellier", "2026-10-21T10:00:00+02:00", ["famille", "soiree"]),
        event("Sète", "2026-10-01T00:00:00+02:00", ["famille"], end="2026-11-30T00:00:00+01:00"),  # expo en cours
        event("Sète", "2026-11-15T00:00:00+01:00", ["famille"]),        # trop loin
        event("Paris", "2026-10-22T20:00:00+02:00", ["soiree"]),        # hors zone
    ]
    counts = watch.coverage(events, TODAY)
    assert counts == {"montpellier": {"famille": 1, "soiree": 2}, "bassin_de_thau": {"famille": 1, "soiree": 0}}
    alerts = watch.check_coverage(counts, minimum=2)
    assert alerts == [
        "Couverture faible : 1 sortie(s) « famille » à Montpellier dans les 7 prochains jours (minimum souhaité : 2).",
        "Couverture faible : 1 sortie(s) « famille » à bassin de Thau dans les 7 prochains jours (minimum souhaité : 2).",
        "Couverture faible : 0 sortie(s) « soirée » à bassin de Thau dans les 7 prochains jours (minimum souhaité : 2).",
    ]


# --- 4. Coût -------------------------------------------------------------------

def test_cost_and_budget() -> None:
    usage = watch.merge_usage(
        {"mistral-small-latest": {"input": 60_000, "output": 100_000}},
        {"mistral-embed": {"input": 50_000, "output": 0}},
    )
    cost = watch.nightly_cost(usage)
    assert cost == round(60_000 * 0.10 / 1e6 + 100_000 * 0.30 / 1e6 + 50_000 * 0.10 / 1e6, 4)   # 0,041 $
    assert watch.check_cost(cost, budget=0.50) == []
    assert "au-delà du budget de 0.01 $" in watch.check_cost(cost, budget=0.01)[0]
    assert watch.nightly_cost({"modele-inconnu": {"input": 1_000_000, "output": 0}}) == 0.40   # prudence
    assert watch.estimate_tokens(["a" * 400, "b" * 40]) == 110


# --- L'agent complet -------------------------------------------------------------

def test_only_failed_sources_are_retried() -> None:
    calls: list[list[str]] = []

    def extract(sources):
        calls.append(list(sources))
        if len(calls) == 1:   # 1re nuit : datatourisme en panne, openagenda va bien
            return ExtractionOutput(articles=[make_article(1, "Concert")], failed_sources=["DATAtourisme - Montpellier"])
        return ExtractionOutput(articles=[make_article(2, "Théâtre")],
                                usage={"mistral-small-latest": {"input": 10, "output": 5}})

    services = make_services(extract, FakeDatabase())
    services.default_sources = ("openagenda", "datatourisme")
    state = run_nightly_collection(services, max_attempts=2)
    assert calls == [["openagenda", "datatourisme"], ["datatourisme"]]
    assert {e["title"] for e in state["events"]} == {"Concert", "Théâtre"}   # les deux nuits additionnées
    assert state["source_attempts"] == {"openagenda": 1, "datatourisme": 2}
    assert state["status"] == "success" and not state.get("failed_sources")
    assert state["llm_usage"]["mistral-small-latest"] == {"input": 10, "output": 5}


def test_watch_alerts_reach_the_report_without_changing_the_status() -> None:
    sent: list[dict] = []
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert Jazz")]), FakeDatabase())
    services.notify = sent.append
    services.watch_inputs = lambda: {
        "documents": [{"name": "Agenda Culture", "end": date(2026, 10, 31)}],
        "partner_files": [{"name": "Bar Exemple", "file_name": "bar.csv", "updated": date(2026, 10, 1)}],
    }
    # Les partenaires ne sont contrôlés que si leurs fichiers ont été lus cette nuit.
    state = run_nightly_collection(services, sources=["openagenda", "partenaire"])
    assert state["status"] == "success"                         # rappels, pas pannes
    assert any("Agenda Culture" in alert for alert in state["alerts"])
    assert any("Bar Exemple" in alert for alert in state["alerts"])
    assert any("Couverture faible" in alert for alert in state["alerts"])
    report = sent[0]
    # La couverture vient de la base (fausse base : 12 soirées à Montpellier), pas des seuls événements de la nuit.
    assert report["coverage"]["montpellier"]["soiree"] == 12 and "cost_usd" in report
    assert not any("Couverture faible : 12" in alert for alert in state["alerts"])

    # Source partenaire non lancée : aucun rappel « relancer le partenaire » à tort.
    sent.clear()
    state = run_nightly_collection(services, sources=["openagenda"])
    assert not any("Bar Exemple" in alert for alert in state["alerts"])
    _subject, body = build_report_email(report)
    assert "Sorties des 7 prochains jours (famille / soirée)" in body and "Coût Mistral estimé" in body
    assert NOW.date() == TODAY


def test_watch_runs_even_after_a_total_failure() -> None:
    def broken(sources):
        raise RuntimeError("réseau coupé")

    services = make_services(broken, FakeDatabase())
    services.watch_inputs = lambda: {"documents": [{"name": "Agenda Culture", "end": date(2026, 10, 31)}]}
    state = run_nightly_collection(services, max_attempts=1)
    assert state["status"] == "failed"
    assert any("Agenda Culture" in alert for alert in state["alerts"])


# --- Enrichissement dans l'agent ---------------------------------------------------

def test_enrichment_runs_before_embeddings_and_is_counted() -> None:
    from src.event.event_enrichment import Enrichment

    calls: list[int] = []

    def fake_enrich(rows):
        calls.append(len(rows))
        results = {row["event_id"]: Enrichment(id=row["event_id"], categorie="concert", publics=["soiree"],
                                               resume="Un concert.") for row in rows[:1]}   # le 2e est « refusé »
        return results, {"mistral-small-latest": {"input": 300, "output": 60}}

    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), db)
    services.enrich = fake_enrich
    state = run_nightly_collection(services)
    assert calls == [2]                                    # la fausse base renvoie 2 événements à enrichir
    assert state["events_enriched"] == 1 and state["enrichment"]["invalid"] == 1
    assert state["llm_usage"]["mistral-small-latest"] == {"input": 300, "output": 60}
    written = [rows for kind, sql, rows in db.calls if kind == "executemany" and sql == "UPDATE"
               and rows and "category_norm" in rows[0]]
    assert written == [[{"event_id": "e1", "category_norm": "concert", "summary": "Un concert.", "price_band": "inconnu",
                         "audiences_llm": ["soiree"], "is_outing": True, "version": written[0][0]["version"]}]]


def test_enrichment_failure_never_breaks_the_night() -> None:
    def broken(rows):
        raise RuntimeError("quota Mistral")

    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), FakeDatabase())
    services.enrich = broken
    state = run_nightly_collection(services)
    assert state["status"] == "success" and state["enrichment"]["failed_batches"] == 1


# --- Juge de qualité dans l'agent ----------------------------------------------------

def test_quality_judge_in_the_night() -> None:
    from src.event.quality_judge import Verdict

    sent: list[dict] = []

    def fake_judge(rows):
        verdicts = {row["event_id"]: Verdict(id=row["event_id"], coherent=True, est_une_sortie=False,
                                             publics_corrects=True, categorie_correcte=True, probleme="pas une sortie")
                    for row in rows}
        return verdicts, {"mistral-medium-latest": {"input": 2000, "output": 300}}

    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), db)
    services.judge = fake_judge
    services.notify = sent.append
    state = run_nightly_collection(services)
    assert state["quality"]["score"] == 0.75 and state["quality"]["checked"] == 2
    assert any("Qualité en baisse : score 75%" in alert for alert in state["alerts"])
    assert state["status"] == "success"
    assert "Qualité (juge Mistral, 2 événements tirés au hasard) : 75%" in build_report_email(sent[0])[1]
    assert state["llm_usage"]["mistral-medium-latest"]["input"] == 2000


def test_judge_failure_is_harmless() -> None:
    def broken(rows):
        raise RuntimeError("quota")

    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), FakeDatabase())
    services.judge = broken
    state = run_nightly_collection(services)
    assert state["status"] == "success" and "error" in state["quality"]


# --- Plafond de la base ----------------------------------------------------------------

def test_capacity_alerts() -> None:
    assert watch.check_capacity(5_000, 0, maximum=10_000) == []
    assert "presque pleine" in watch.check_capacity(9_500, 0, maximum=10_000)[0]
    assert "Plafond de 10000 événements atteint : 120" in watch.check_capacity(10_000, 120, maximum=10_000)[0]


# --- Revue du 15/10/2026 : statuts par source, embeddings, configuration ----

PDF_STAT_FAILED = {"source_id": "document", "name": "Programmes PDF", "type": "document", "status": "failed",
                   "errors": ["Agenda Culture : PDF introuvable (404) : https://x.fr/uploads/2026/08/agenda.pdf"]}


def test_failed_document_source_is_recognized_and_retried() -> None:
    """Avant : l'agent cherchait « document » dans « Programmes PDF » et ne voyait pas l'échec."""

    calls: list[list[str]] = []

    def extract(sources):
        calls.append(list(sources))
        if len(calls) == 1:
            # Le nom affiché (« Programmes PDF ») ne contient pas « document » : seul le bilan le dit.
            return ExtractionOutput(articles=[make_article(1, "Concert")], failed_sources=["Programmes PDF"],
                                    extractor_stats=[PDF_STAT_FAILED])
        return ExtractionOutput(articles=[make_article(2, "Visite")],
                                extractor_stats=[{**PDF_STAT_FAILED, "status": "success", "errors": []}])

    services = make_services(extract, FakeDatabase())
    state = run_nightly_collection(services, sources=["openagenda", "document"], max_attempts=2)
    assert calls == [["openagenda", "document"], ["document"]]
    assert state["status"] == "success" and state["source_attempts"] == {"openagenda": 1, "document": 2}


def test_partial_extractor_is_not_swept_but_counted_ok() -> None:
    """Un PDF sur deux en 404 : ses événements ne sont pas pris pour disparus, la nuit est « partielle »."""

    from agent.nodes import build_source_rows, sources_safe_to_sweep
    from agent.routing import source_statuses

    state = {
        "sources": ["openagenda", "document"],
        "failed_sources": [],
        "extractor_stats": [{"source_id": "openagenda", "status": "success"},
                            {"source_id": "document", "status": "partial_success"}],
        "events": [{"source": "openagenda"}, {"source": "document"}],
    }
    assert source_statuses(state) == {"openagenda": "ok", "document": "partiel"}
    assert sources_safe_to_sweep(state) == ["openagenda"]
    assert [row["status"] for row in build_source_rows(state)] == ["ok", "partiel"]


def test_source_statuses_fall_back_to_the_displayed_names() -> None:
    from agent.routing import source_statuses

    state = {"sources": ["openagenda", "ical"], "failed_sources": ["Calendriers iCal"], "extractor_stats": []}
    assert source_statuses(state) == {"openagenda": "ok", "ical": "echec"}


def test_embedding_failure_never_breaks_the_night() -> None:
    """Mistral absent pendant les embeddings : la nuit continue, les événements restent écrits."""

    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), db)

    def broken(texts):
        raise RuntimeError("401 Unauthorized")

    services.embed = broken
    state = run_nightly_collection(services)
    assert state["status"] == "success" and state["events_embedded"] == 0 and state["run_id"] == 7
    assert any(call[1] == "INSERT" for call in db.calls)    # l'upsert a bien eu lieu


def test_invalid_source_configuration_leaves_a_trace() -> None:
    def broken(name):
        raise ValueError("source_id en double : agglopole")

    services = make_services(lambda sources: ExtractionOutput(articles=[]), FakeDatabase())
    services.is_configured = broken
    state = run_nightly_collection(services, max_attempts=1)
    assert state["status"] == "failed" and state["sources"] == []
    assert any("configuration des sources" in error for error in state["errors"])
    assert state["run_id"] == 7   # le bilan est écrit


def test_extraction_output_carries_source_ids(monkeypatch) -> None:
    """Les bilans portent l'identifiant de source, dans l'ordre des extracteurs demandés."""

    from types import SimpleNamespace

    import src.extractor_execution_service as execution
    from agent.services import extract_with_checkit

    def fake_extract_all_sources(names, **kwargs):
        runs = [SimpleNamespace(extractor_name="OpenAgenda - Montpellier", source_type="api", status="success",
                                analyzed_count=3, article_count=3, rejected_count=0, requests_count=1,
                                duration_seconds=1.0, rejection_reasons={}, message="", errors=[]),
                SimpleNamespace(extractor_name="Programmes PDF", source_type="document", status="failed",
                                analyzed_count=0, article_count=0, rejected_count=0, requests_count=1,
                                duration_seconds=0.5, rejection_reasons={}, message="404", errors=["x : 404"])]
        return SimpleNamespace(articles=[{"title": "a"}], extractor_results=runs)

    monkeypatch.setattr(execution, "extract_all_sources", fake_extract_all_sources)
    output = extract_with_checkit(["OpenAgenda", "document"])
    assert output.failed_sources == ["document"]
    assert [stat["source_id"] for stat in output.extractor_stats] == ["openagenda", "document"]


def test_embedding_client_is_created_once(monkeypatch):
    """Le client (et son tokenizer Hugging Face) n'est créé qu'une fois."""

    import sys
    import types

    from agent import services

    created = []

    class FakeEmbeddings:
        def __init__(self, model):
            created.append(model)

        def embed_documents(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "langchain_mistralai",
                        types.SimpleNamespace(MistralAIEmbeddings=FakeEmbeddings))
    monkeypatch.setenv("EMBEDDING_MODEL", "faux-modele")
    services.embedding_client.cache_clear()
    try:
        services.embed_with_mistral(["a"])
        services.embed_with_mistral(["b", "c"])
    finally:
        services.embedding_client.cache_clear()
    assert created == ["faux-modele"]
