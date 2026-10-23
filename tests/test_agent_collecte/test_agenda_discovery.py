"""Tests de la découverte hebdomadaire des agendas OpenAgenda.

**Provenance** : nouveau fichier écrit pour SortirIA (15/10/2026). La nuit ne
lit que les agendas de `agenda_uids` ; une fois par semaine, l'agent refait la
recherche complète et propose les nouveaux agendas, sans toucher seul à la
configuration.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agent.graph import run_nightly_collection
from agent.proposals import add_agenda_to_config, apply_proposal, describe_action
from agent.report import build_report_email
from agent.services import ExtractionOutput

from .test_agent import NOW, FakeDatabase, make_article, make_services


def night(discover, weekday: int | None, *, forced: bool = False, sent: list | None = None):
    db = FakeDatabase()
    services = make_services(lambda sources: ExtractionOutput(articles=[make_article(1, "Concert")]), db)
    services.discover_agendas = discover
    services.watch_inputs = lambda: {"discovery_weekday": weekday}
    if sent is not None:
        services.notify = sent.append
    return run_nightly_collection(services, max_attempts=1, discover_agendas=forced), db


def test_discovery_runs_only_on_its_day() -> None:
    calls: list[int] = []

    def discover():
        calls.append(1)
        return {"123": 7, "456": 2}

    state, _ = night(discover, (NOW.weekday() + 1) % 7)       # pas le bon jour
    assert calls == [] and state["proposals"] == []
    state, db = night(discover, NOW.weekday())                 # le bon jour
    assert calls == [1]
    assert [p["action"]["new"] for p in state["proposals"]] == ["123", "456"]   # les plus riches d'abord
    assert state["proposals"][0]["action"]["events"] == 7
    recorded = [rows for kind, sql, rows in db.calls if kind == "executemany" and sql == "INSERT"
                and rows and "diagnosis" in rows[0]]
    assert recorded and recorded[0][0]["kind"] == "nouvel_agenda"


def test_discovery_can_be_forced_and_never_breaks_the_night() -> None:
    sent: list[dict] = []
    state, _ = night(lambda: {"789": 3}, None, forced=True, sent=sent)
    assert state["proposals"][0]["action"]["new"] == "789"
    body = build_report_email(sent[0])[1]
    assert "ajouter l'agenda 789 (3 événement(s) dans la zone)" in body

    def broken():
        raise RuntimeError("HTTP 429")

    state, _ = night(broken, NOW.weekday())
    assert state["status"] == "success"
    assert any("Découverte des agendas OpenAgenda impossible" in alert for alert in state["alerts"])


def test_no_discovery_without_the_service() -> None:
    state, _ = night(None, NOW.weekday(), forced=True)
    assert state["proposals"] == []


CONFIG = (
    'api_sources:\r\n'
    '  - name: "OpenAgenda"\r\n'
    '    agenda_uids:\r\n'
    '      - "9969608"    # 30 événements retenus le 14/10\r\n'
    '      - "67871262"   # 1\r\n'
    '    max_agendas: 50\r\n'
)


def test_accepting_adds_the_agenda_at_the_end_of_the_list(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_bytes(CONFIG.encode())
    proposal = {"source_id": "openagenda",
                "action": {"type": "ajouter_agenda", "new": "123", "events": 7, "found_on": "2026-10-18"}}
    applied, why = apply_proposal(proposal, config)
    assert applied, why
    text = config.read_bytes().decode()
    assert '      - "67871262"   # 1\r\n      - "123"        # 7 (découverte du 2026-10-18)\r\n    max_agendas' in text
    assert "\n" not in text.replace("\r\n", "")                       # fins de ligne gardées
    applied, why = apply_proposal(proposal, config)
    assert not applied and "déjà" in why                               # jamais ajouté deux fois


def test_unusual_config_is_left_to_a_human(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_bytes(b"api_sources:\n  - name: OpenAgenda\n    agenda_uids: []\n")
    applied, why = add_agenda_to_config("123", 7, "2026-10-18", config)
    assert not applied and "à la main" in why
    assert config.read_bytes() == b"api_sources:\n  - name: OpenAgenda\n    agenda_uids: []\n"


def test_describe_both_kinds_of_proposal() -> None:
    assert "remplacer a par b" in describe_action({"source_id": "x", "action": {"type": "remplacer_url",
                                                                                 "old": "a", "new": "b"}})
    assert "ajouter l'agenda 1" in describe_action({"source_id": "openagenda",
                                                     "action": '{"type": "ajouter_agenda", "new": "1", "events": 2}'})


def test_an_agenda_in_error_is_logged_and_skipped(monkeypatch: pytest.MonkeyPatch,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    from agent import services
    from src.extractors.apis.source import openagenda_extractor as openagenda

    def page(source: dict, uid: str, api_key: str, offset: int, size: int) -> list[dict]:
        if uid == "111":
            raise RuntimeError("403 agenda privé")
        return [{"title": {"fr": "Bal folk"}}]

    monkeypatch.setattr(openagenda, "get_api_key", lambda: "clé")
    monkeypatch.setattr(openagenda, "search_agenda_uids", lambda source, api_key: ["111", "222"])
    monkeypatch.setattr(openagenda, "fetch_agenda_page", page)
    monkeypatch.setattr(openagenda, "is_in_area", lambda item, source: True)
    monkeypatch.setattr(services.logger, "propagate", True)
    with caplog.at_level(logging.WARNING):
        found = services.find_agendas_for_words(["folk"])
    assert found == {"folk": {"222": {"events": 1, "examples": ["Bal folk"]}}}
    assert any("111" in record.getMessage() for record in caplog.records)
