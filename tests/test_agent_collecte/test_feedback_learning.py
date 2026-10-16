"""Les avis des visiteurs lus par l'agent (16/10/2026) : pouces bas rangés par
cause, commentaires refusés à revoir, sorties mal notées. Mistral est simulé."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from agent import feedback_learning as fl
from agent.commentaires import describe
from agent.report import build_report_email, feedback_lines
from agent.search_learning import MIN_TIMES, LearningResult
from agent.watch import check_ratings

REVIEWS = [
    {"question": "cours de zumba", "comment": "rien sur la zumba", "titles": ["Atelier poterie"]},
    {"question": "concert samedi", "comment": "c'était dimanche", "titles": ["Jazz au parc"]},
    {"question": "expo à Sète", "comment": "", "titles": ["Expo photo"], "answer": "Bonjour..."},
]


def answer(*items: dict[str, Any]) -> str:
    return json.dumps({"avis": list(items)})


def test_mistral_answer_is_checked() -> None:
    readings = fl.parse_feedback(answer(
        {"id": 0, "cause": "sujet_absent", "mot": "Zumba", "explication": "rien en base"},
        {"id": 1, "cause": "date_ou_lieu", "mot": "samedi"},            # pas de mot pour cette cause
        {"id": 2, "cause": "inventee"},                                # cause inconnue
        {"id": 7, "cause": "autre"},                                   # avis non demandé
        {"id": "x"},                                                   # illisible
    ), 3)
    assert set(readings) == {0, 1}
    assert readings[0].mot == "zumba" and readings[1].mot == ""
    long = fl.parse_feedback(answer({"id": 0, "cause": "mal_compris", "mot": "deux mots"}), 1)
    assert long[0].mot == ""


def test_thumbs_down_are_grouped_by_cause_and_feed_the_words() -> None:
    def read(reviews: Any) -> Any:
        assert len(reviews) == 3
        return fl.parse_feedback(answer(
            {"id": 0, "cause": "sujet_absent", "mot": "zumba", "explication": "aucune sortie de danse"},
            {"id": 1, "cause": "date_ou_lieu", "explication": "dimanche au lieu de samedi"}), 3), \
            {"mistral-small-latest": {"input": 900, "output": 120}}

    result = fl.learn_from_thumbs_down(REVIEWS, read)
    assert result.words == {"zumba": MIN_TIMES}
    assert result.causes == {"sujet_absent": 1, "date_ou_lieu": 1, "autre": 1}
    assert result.notes[0].startswith("3 pouce(s) bas cette semaine : ")
    assert "« cours de zumba » (« rien sur la zumba ») → sujet absent de la base : aucune sortie de danse" in result.notes
    assert result.notes[-1] == "Mots repris dans l'apprentissage de la recherche : zumba."
    assert result.usage == {"mistral-small-latest": {"input": 900, "output": 120}}


def test_a_mistral_failure_still_reports_the_count() -> None:
    def broken(reviews: Any) -> Any:
        raise RuntimeError("quota")

    result = fl.learn_from_thumbs_down(REVIEWS, broken)
    assert result.words == {} and "3 pouce(s) bas" in result.notes[0] and "quota" in result.notes[0]
    assert fl.learn_from_thumbs_down([], broken).notes == []


def test_words_join_the_week_and_the_usual_learning_runs() -> None:
    gaps = {"unknown_words": [("pilates", 3), ("zumba", 1)],
            "thumbs_down": REVIEWS[:1]}
    seen: dict[str, Any] = {}

    def learn(merged: Any, today: Any) -> LearningResult:
        seen["words"] = merged["unknown_words"]
        return LearningResult(notes=["note"], usage={"mistral-small-latest": {"input": 10, "output": 1}})

    def read(reviews: Any) -> Any:
        return fl.parse_feedback(answer({"id": 0, "cause": "mal_compris", "mot": "zumba"}), 1), \
            {"mistral-small-latest": {"input": 5, "output": 2}}

    result = fl.learn_with_feedback(gaps, date(2026, 10, 19), learn=learn, read_feedback=read)
    assert dict(seen["words"]) == {"zumba": 1 + MIN_TIMES, "pilates": 3}
    assert result.notes == ["note"] and result.feedback_notes[0].startswith("1 pouce(s) bas")
    assert result.usage == {"mistral-small-latest": {"input": 15, "output": 3}}


def test_monday_mail_lists_thumbs_down_and_refused_comments() -> None:
    report = {"status": "success",
              "feedback_learning": ["2 pouce(s) bas cette semaine : 1 sujet absent de la base, 1 autre.",
                                    "« cours de zumba » → sujet absent de la base"],
              "refused_comments": [{"rating_id": 12, "title": "Concert", "stars": 4,
                                    "comment": "super soirée de dingue", "flagged": ["violence"]}]}
    text = "\n".join(feedback_lines(report))
    assert "Pouces bas de la semaine" in text and "  « cours de zumba »" in text
    assert "- n° 12 « Concert », 4/5 : « super soirée de dingue » (signalé : violence)" in text
    assert "agent.commentaires --publier N" in text
    assert "Pouces bas de la semaine" in build_report_email(report)[1]
    assert feedback_lines({"status": "success"}) == []


def test_low_rated_events_are_signalled() -> None:
    alerts = check_ratings({"low_rated_events": [
        {"title": "Soirée ratée", "venue": "Le Quai", "city": "Sète", "source": "openagenda",
         "average": 1.7, "ratings": 3}]})
    assert alerts == [("Sortie mal notée : « Soirée ratée » (Le Quai, Sète), source openagenda : 1.7/5 sur 3 notes. "
                       "Vérifier la fiche (date, lieu, description) et la fiabilité de la source.")]


def test_comment_line_for_the_command() -> None:
    line = describe({"rating_id": 12, "updated_at": "2026-10-19 10:00:00", "title": "Concert", "city": "Sète",
                     "stars": 4, "comment": "super", "moderation": {"flagged": ["violence"],
                                                                    "revue_humaine": {"le": "2026-10-19 11:00"}}})
    assert line == ("n° 12 (2026-10-19 10:00) « Concert », Sète, 4/5 : « super » "
                    "(signalé : violence ; revu le 2026-10-19 11:00)")
