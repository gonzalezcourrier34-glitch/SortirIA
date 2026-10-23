"""Tests de l'apprentissage des recherches ratées (agent/search_learning.py, 15/10/2026).

Sans réseau ni Mistral : la lecture des mots, la base et OpenAgenda sont simulées.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from agent import nodes
from agent.proposals import add_synonym_to_config, apply_proposal, describe_action
from agent.report import build_report_email
from agent.search_learning import (
    LearningResult,
    WordReading,
    learn_from_searches,
    parse_readings,
    words_to_learn,
)
from agent.services import CollectionServices
from app import query_understanding
from src.event.event_normalizer import PARIS

MONDAY = date(2026, 10, 19)
GAPS = {"days": 7, "questions": 30, "not_exact": 9, "no_answer": 3,
        "unknown_words": [("zumba", 4), ("cirqe", 3), ("escalade", 2), ("romantique", 2), ("kitesurf", 2),
                          ("tango", 1)],
        "unanswered": [("de la zumba", 3)]}


def reader(readings: dict[str, WordReading]):
    def read(words):
        read.asked = list(words)
        return {word: readings[word] for word in words if word in readings}, {"mistral-small-latest": {
            "input": 120, "output": 40}}
    return read


READINGS = {
    "zumba": WordReading(mot="zumba", nature="synonyme", equivalents=["aerobic", "fitness", "danse"]),
    "cirqe": WordReading(mot="cirqe", nature="faute", equivalents=["cirque"]),
    "escalade": WordReading(mot="escalade", nature="sujet", equivalents=["grimpe"]),
    "romantique": WordReading(mot="romantique", nature="ambiance"),
    "kitesurf": WordReading(mot="kitesurf", nature="sujet", equivalents=["glisse"]),
}


def test_only_words_searched_several_times_are_learned() -> None:
    assert [word for word, _ in words_to_learn(GAPS)] == ["zumba", "cirqe", "escalade", "romantique", "kitesurf"]
    assert words_to_learn(GAPS, limit=2) == [("zumba", 4), ("cirqe", 3)]
    assert words_to_learn({}) == []


def test_words_of_the_sentence_are_not_learned() -> None:
    """Bilan du 19/10/2026 : « bonjour » -> « salut », « voire » -> « ou-encore »."""

    gaps = {"unknown_words": [("bonjour", 3), ("peinture", 3), ("propositions", 3), ("voire", 3), ("sont", 9)]}
    assert words_to_learn(gaps) == [("peinture", 3)]
    answer = json.dumps({"mots": [{"mot": "peinture", "nature": "sujet", "equivalents": ["ou-encore", "peintre"]}]})
    assert parse_readings(answer, ["peinture"])["peinture"].equivalents == ["peintre"]


def test_mistral_answer_is_checked() -> None:
    answer = json.dumps({"mots": [
        {"mot": "Zumba", "nature": "synonyme", "equivalents": ["Fitness", "danse sportive", "zumba", "fitness"]},
        {"mot": "inconnu", "nature": "faute", "equivalents": ["x"]},          # pas demandé
        {"mot": "cirqe", "nature": "devinette", "equivalents": ["cirque"]},   # nature inconnue
        {"nature": "faute"},                                                  # illisible
    ]})
    readings = parse_readings(answer, ["zumba", "cirqe"])
    assert list(readings) == ["zumba"] and readings["zumba"].equivalents == ["fitness"]


def test_week_of_failed_searches_becomes_proposals() -> None:
    known = {"fitness", "danse", "cirque"}                     # dans les titres de la base
    agendas = {"escalade": {"4242": {"events": 3, "examples": ["Bloc en famille", "Grimpe libre"]}}}
    read = reader(READINGS)
    result = learn_from_searches(GAPS, MONDAY, read_words=read,
                                 known_words=lambda words: [w for w in words if w in known],
                                 find_agendas=lambda words: {w: agendas.get(w, {}) for w in words})
    assert read.asked == ["zumba", "cirqe", "escalade", "romantique", "kitesurf"]
    actions = [proposal["action"] for proposal in result.proposals]
    # « aerobic » n'est pas dans la base : le premier équivalent connu est retenu.
    assert actions[0] == {"type": "ajouter_synonyme", "word": "zumba", "target": "fitness", "times": 4,
                          "nature": "synonyme", "new": "zumba -> fitness", "found_on": "2026-10-19"}
    assert actions[1]["target"] == "cirque" and actions[1]["nature"] == "faute"
    assert actions[2]["type"] == "ajouter_agenda" and actions[2]["new"] == "4242" and actions[2]["word"] == "escalade"
    assert result.proposals[2]["kind"] == "sujet_demande" and "« Bloc en famille »" in result.proposals[2]["diagnosis"]
    assert any("romantique" in note and "ambiance" in note for note in result.notes)
    assert any("kitesurf" in note and "partenaires" in note for note in result.notes)
    assert result.usage == {"mistral-small-latest": {"input": 120, "output": 40}}


def test_synonym_unknown_to_the_base_is_never_proposed() -> None:
    read = reader({"zumba": WordReading(mot="zumba", nature="synonyme", equivalents=["aerobic"])})
    result = learn_from_searches({"unknown_words": [("zumba", 4)]}, MONDAY, read_words=read,
                                 known_words=lambda words: [], find_agendas=None)
    assert result.proposals == [] and "partenaires" in result.notes[0]


def test_mistral_or_openagenda_down_never_stops_the_report() -> None:
    def broken(words):
        raise RuntimeError("429")

    result = learn_from_searches(GAPS, MONDAY, read_words=broken, known_words=lambda words: [])
    assert result.proposals == [] and "impossible" in result.notes[0]
    result = learn_from_searches(GAPS, MONDAY, read_words=reader(READINGS), known_words=lambda words: [],
                                 find_agendas=broken)
    assert any("OpenAgenda impossible" in note for note in result.notes)


def test_accepted_synonym_is_written_and_used(tmp_path: Path) -> None:
    path = tmp_path / "synonymes.yaml"
    path.write_text("# Synonymes appris\nsynonymes:\n", encoding="utf-8")
    assert add_synonym_to_config("Zumba", "fitness", 4, "2026-10-19", path)[0] is True
    assert add_synonym_to_config("zumba", "danse", 1, "2026-10-20", path)[0] is False      # déjà appris
    assert add_synonym_to_config("?!", "fitness", 1, "?", path)[0] is False               # illisible
    synonyms = query_understanding.learned_synonyms(path)
    assert synonyms == {"zumba": "fitness"}
    assert query_understanding.with_synonyms("de la Zumba ce soir", synonyms) == "de la Zumba ce soir fitness"
    assert query_understanding.with_synonyms("du fitness", synonyms) == "du fitness"


def test_understanding_uses_learned_synonyms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_understanding, "learned_synonyms", lambda path=None: {"cirqe": "cirque"})
    understood = query_understanding.understand("du cirqe", datetime(2026, 10, 15, tzinfo=PARIS))
    assert "spectacle" in understood.categories and "cirque" in understood.keywords


def test_apply_synonym_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "synonymes.yaml"
    path.write_text("synonymes:\n", encoding="utf-8")
    monkeypatch.setattr(query_understanding, "SYNONYMS_FILE", path)
    proposal = {"action": json.dumps({"type": "ajouter_synonyme", "word": "standup", "target": "humour",
                                      "times": 2, "found_on": "2026-10-19"})}
    applied, explanation = apply_proposal(proposal)
    assert applied and "standup" in path.read_text(encoding="utf-8") and "humour" in explanation


def test_proposals_and_notes_in_the_monday_mail() -> None:
    synonym = {"source_id": "recherche", "action": {"type": "ajouter_synonyme", "word": "zumba",
                                                    "target": "fitness", "times": 4, "nature": "synonyme"}}
    agenda = {"source_id": "openagenda", "action": {"type": "ajouter_agenda", "new": "4242", "events": 3,
                                                    "word": "escalade", "url": "https://openagenda.com/agendas/4242"}}
    assert describe_action(synonym) == ("recherche : « zumba » cherchera aussi « fitness » "
                                        "(synonyme, cherché 4 fois sans réponse)")
    assert "cherché : « escalade »" in describe_action(agenda)
    _, body = build_report_email({"status": "success", "proposals": [synonym, agenda],
                                  "search_report": GAPS, "search_learning": ["« kitesurf » (2) : piste"]})
    assert "Ce que l'agent en tire" in body and "« kitesurf » (2) : piste" in body
    assert "« zumba » cherchera aussi « fitness »" in body


def services_on(day: date, learn: Any) -> CollectionServices:
    return CollectionServices(extract=lambda names: None, connect=lambda: None, embed=lambda texts: [],
                              now=lambda: datetime(day.year, day.month, day.day, 3, tzinfo=PARIS),
                              learn_searches=learn)


def test_agent_learns_on_monday_only() -> None:
    calls: list[Any] = []

    def learn(gaps, today):
        calls.append((gaps, today))
        return LearningResult(proposals=[{"source_id": "recherche", "kind": "synonyme", "diagnosis": "d",
                                          "action": {"type": "ajouter_synonyme", "new": "zumba -> fitness"}}],
                              notes=["« kitesurf » (2) : piste"],
                              usage={"mistral-small-latest": {"input": 1000, "output": 200}})

    state = {"search_gaps": GAPS, "events": [], "llm_usage": {}}
    monday = nodes.watch_collection(state, {"configurable": {"services": services_on(MONDAY, learn)}})
    assert calls == [(GAPS, MONDAY)]
    assert monday["proposals"][-1]["kind"] == "synonyme" and monday["search_learning"] == ["« kitesurf » (2) : piste"]
    assert monday["llm_usage"]["mistral-small-latest"]["input"] == 1000 and monday["cost_usd"] > 0
    thursday = nodes.watch_collection(state, {"configurable": {"services": services_on(date(2026, 10, 15), learn)}})
    assert len(calls) == 1 and thursday["search_report"] == {} and thursday["search_learning"] == []
    essai = nodes.watch_collection({**state, "dry_run": True},
                                   {"configurable": {"services": services_on(MONDAY, learn)}})
    assert len(calls) == 1 and essai["search_learning"] == []


def test_learning_failure_never_stops_the_night() -> None:
    def broken(gaps, today):
        raise RuntimeError("base indisponible")

    result = nodes.watch_collection({"search_gaps": GAPS, "events": []},
                                    {"configurable": {"services": services_on(MONDAY, broken)}})
    assert "impossible" in result["search_learning"][0]


def test_a_new_synonym_renews_the_cached_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import search_cache
    from app.search_filters import SearchFilters

    now = datetime(2026, 10, 19, 10, tzinfo=PARIS)
    monkeypatch.setattr(query_understanding, "learned_synonyms", lambda path=None: {})
    before = search_cache.cache_key("de la zumba", "", SearchFilters(), now, "12")
    monkeypatch.setattr(query_understanding, "learned_synonyms", lambda path=None: {"zumba": "fitness"})
    assert search_cache.cache_key("de la zumba", "", SearchFilters(), now, "12") != before


def test_apply_synonym_proposal_writes_to_the_given_file(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    default, chosen = tmp_path / "par_defaut.yaml", tmp_path / "choisi.yaml"
    default.write_text("synonymes:\n", encoding="utf-8")
    chosen.write_text("synonymes:\n", encoding="utf-8")
    monkeypatch.setattr(query_understanding, "SYNONYMS_FILE", default)
    action = {"type": "ajouter_synonyme", "word": "slam", "target": "poesie", "times": 3}
    proposal = {"action": action}
    applied, _ = apply_proposal(proposal, synonyms_path=chosen)
    assert applied and "slam" in chosen.read_text(encoding="utf-8")
    assert default.read_text(encoding="utf-8") == "synonymes:\n"
