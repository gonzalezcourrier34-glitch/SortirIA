"""Tests du diagnostic des pannes et des propositions validées par un humain.

**Provenance** : nouveau fichier écrit pour SortirIA (14/10/2026). Le cas
principal est la panne réelle du jour : le PDF de l'Agglopôle passé du dossier
2026/08 au dossier 2026/09.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agent import diagnosis
from agent.graph import run_nightly_collection
from agent.proposals import apply_proposal
from agent.report import build_report_email
from agent.services import ExtractionOutput

from .test_agent import FakeDatabase, make_article, make_services

OLD = "https://www.agglopole.fr/wp-content/uploads/2026/08/Agenda-Culture-2026.pdf"
NEW = "https://www.agglopole.fr/wp-content/uploads/2026/09/Agenda-Culture-2026.pdf"
TODAY = date(2026, 10, 14)
PDF_FAILURE = {"name": "Programmes PDF", "type": "document", "status": "failed", "message": "Échec des 1 source(s).",
               "errors": [f"Agenda Culture : PDF introuvable (404) : {OLD}. Le document a sans doute été déplacé."]}
DOCUMENTS = [{"name": "Agenda Culture", "source_id": "agglopole", "url": OLD, "end": date(2026, 12, 31)}]


def test_failures_are_classified() -> None:
    assert diagnosis.classify("PDF introuvable (404) : https://x.fr/a.pdf") == diagnosis.KIND_MOVED
    assert diagnosis.classify("Error response 401: Unauthorized") == diagnosis.KIND_ACCESS
    assert diagnosis.classify("Error response 429 : Rate limit exceeded") == diagnosis.KIND_QUOTA
    assert diagnosis.classify("Délai dépassé pendant le téléchargement.") == diagnosis.KIND_NETWORK
    assert diagnosis.classify("Le document téléchargé n'est pas un PDF.") == diagnosis.KIND_FORMAT
    # Azure, 15/10/2026 : page web servie aux centres de données à la place du PDF.
    assert diagnosis.classify(
        "Le document téléchargé n'est pas un PDF : page web reçue (text/html, HTTP 200). Accès bloqué par le site"
    ) == diagnosis.KIND_BLOCKED
    assert diagnosis.classify("bizarre") == diagnosis.KIND_UNKNOWN


def test_candidate_urls_try_neighbouring_months_first() -> None:
    candidates = diagnosis.candidate_urls(OLD, TODAY)
    assert candidates[0] == "https://www.agglopole.fr/wp-content/uploads/2026/10/Agenda-Culture-2026.pdf"
    assert NEW in candidates[:3] and OLD not in candidates
    assert diagnosis.candidate_urls("https://exemple.fr/agenda.pdf", TODAY) == []   # pas une adresse WordPress


def test_moved_document_is_found_and_proposed_not_applied() -> None:
    probed: list[str] = []

    def probe(url: str) -> bool:
        probed.append(url)
        return url == NEW

    [found] = diagnosis.diagnose([PDF_FAILURE], DOCUMENTS, probe, TODAY)
    assert (found.source_name, found.kind) == ("Agenda Culture", diagnosis.KIND_MOVED)
    assert found.proposal == {"type": "remplacer_url", "source_id": "agglopole", "old": OLD, "new": NEW}
    assert "à valider" in found.as_alert()
    assert len(probed) <= 3                         # trouvé dans les mois voisins, sans tout essayer


def test_nothing_found_means_advice_only() -> None:
    [found] = diagnosis.diagnose([PDF_FAILURE], DOCUMENTS, lambda url: False, TODAY)
    assert found.proposal == {} and "chercher la nouvelle" in found.advice
    [access] = diagnosis.diagnose([{"name": "OpenAgenda", "status": "failed", "message": "HTTP 401", "errors": []}],
                                  [], None, TODAY)
    assert access.kind == diagnosis.KIND_ACCESS and "clé API" in access.advice


def test_the_night_proposes_the_repair_and_waits_for_a_human() -> None:
    sent: list[dict] = []
    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(
        articles=[make_article(1, "Concert")], failed_sources=["Programmes PDF"], extractor_stats=[PDF_FAILURE]), db)
    services.default_sources = ("openagenda", "document")
    services.probe = lambda url: url == NEW
    services.watch_inputs = lambda: {"documents": DOCUMENTS}
    services.notify = sent.append
    state = run_nightly_collection(services, max_attempts=1)
    assert state["proposals"][0]["action"]["new"] == NEW
    assert any("Proposition : remplacer l'adresse" in alert for alert in state["alerts"])
    recorded = [rows for kind, sql, rows in db.calls if kind == "executemany" and sql == "INSERT"
                and rows and "diagnosis" in rows[0]]
    assert recorded and recorded[0][0]["source_id"] == "agglopole"
    assert "uv run python -m agent.proposals" in build_report_email(sent[0])[1]


def test_accepting_rewrites_only_that_url(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_bytes(f'document_sources:\r\n  - name: "Agenda"\r\n    url: "{OLD}"   # commentaire\r\n'.encode())
    applied, _ = apply_proposal({"action": {"type": "remplacer_url", "old": OLD, "new": NEW}}, config)
    assert applied and config.read_bytes() == (
        f'document_sources:\r\n  - name: "Agenda"\r\n    url: "{NEW}"   # commentaire\r\n'.encode())
    applied, why = apply_proposal({"action": {"type": "remplacer_url", "old": OLD, "new": NEW}}, config)
    assert not applied and "0 fois" in why                      # déjà fait : rien n'est touché
