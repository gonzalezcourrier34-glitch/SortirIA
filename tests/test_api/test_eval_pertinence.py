"""Tests de la mesure de pertinence (sans base ni Mistral)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import eval_pertinence as pertinence


def test_questions_keep_their_family() -> None:
    items = pertinence.load_questions()
    assert len(items) == 50
    assert items[0]["famille"] == "Le type de sortie"
    assert all(item["famille"] for item in items)


def test_grid_round_trip_and_score(tmp_path: Path) -> None:
    item = {"question": "un concert", "filtres": {"public": "famille"}, "famille": "Le type"}
    cards = [{"title": f"Concert {n}", "date": "sam. 17 oct.", "place": "Sète", "category": "concert",
              "summary": "Jazz."} for n in range(5)]
    rows = pertinence.grid_rows(1, item, cards) + pertinence.grid_rows(2, {**item, "filtres": {}}, [])
    assert [row["rang"] for row in rows] == [1, 2, 3, 0]
    path = tmp_path / "grille.csv"
    pertinence.write_grid(rows, path)
    read = pertinence.read_grid(path)
    assert json.loads(read[0]["filtres"]) == {"public": "famille"}
    read[0]["pertinent"], read[1]["pertinent"], read[2]["pertinent"] = "o", "Non", ""
    report = pertinence.score(read)
    # Question 1 : 1 pertinente sur 2 jugées ; question 2 : aucune proposition = 0 sur 3.
    assert report["cartes_jugees"] == 2 and report["cartes_non_annotees"] == 1
    assert report["pertinence_at_3"] == round(1 / 5, 3)
    assert report["questions_satisfaites"] == 0.5


def test_agreement_and_kappa() -> None:
    rows = [{"pertinent": h, "juge": j} for h, j in
            [("o", "o"), ("o", "o"), ("n", "n"), ("n", "o"), ("o", "o"), ("n", "n")]]
    result = pertinence.agreement(rows)
    assert result["cartes"] == 6 and result["accord"] == round(5 / 6, 3)
    assert 0.6 < result["kappa"] < 0.7


def test_judge_fills_its_column_and_survives_a_bad_answer() -> None:
    rows = [{"num": "1", "rang": "1", "question": "q", "filtres": "", "titre": "A", "date": "", "lieu": "",
             "categorie": "", "resume": ""},
            {"num": "1", "rang": "2", "question": "q", "filtres": "", "titre": "B", "date": "", "lieu": "",
             "categorie": "", "resume": ""}]
    answer = ('{"verdicts": [{"id": "c0", "raison": "bon sujet", "pertinent": true},'
              ' {"id": "c1", "pertinent": "non"}]}')
    answers = iter([answer])
    judged = pertinence.judge_rows(rows, call=lambda prompt, text: next(answers))
    assert judged[0]["juge"] == "o" and judged[0]["raison_juge"] == "bon sujet"
    assert "juge" not in judged[1]  # verdict mal formé : la carte reste sans avis


def test_judge_sees_one_question_per_call() -> None:
    """v2 jugeait par lots de 15 cartes mêlant 5 questions : 55 % d'accord."""

    texts: list[str] = []

    def call(prompt: str, text: str) -> str:
        texts.append(text)
        return "pas du JSON"

    rows = [{"num": str(num), "rang": str(rank), "question": f"q{num}", "filtres": "", "titre": "T", "date": "",
             "lieu": "", "categorie": "", "resume": ""} for num in (1, 2) for rank in (1, 2, 3)]
    pertinence.judge_rows(rows, call=call)
    assert len(texts) == 2 and texts[0].count("id: c") == 3 and "q2" not in texts[0]


def test_grid_saved_by_openoffice_in_windows_1252(tmp_path: Path) -> None:
    path = tmp_path / "grille.csv"
    path.write_bytes("num;famille;question;rang;titre;pertinent\n1;Où;théâtre à Sète;1;Pièce;o\n".encode("cp1252"))
    rows = pertinence.read_grid(path)
    assert rows[0]["question"] == "théâtre à Sète" and pertinence.verdict(rows[0]["pertinent"]) is True


def test_judge_sees_neither_date_nor_place_nor_price() -> None:
    """Moment, lieu et budget sont vérifiés par le code : le juge v5 inventait un
    « délai implicite », v6 un lieu « Montpellier » non demandé (16/10/2026)."""

    seen: dict[str, str] = {}

    def call(prompt: str, text: str) -> str:
        seen["prompt"], seen["text"] = prompt, text
        return '{"verdicts": [{"id": "c0", "pertinent": true}]}'

    rows = [{"num": "1", "rang": "1", "question": "un concert à moins de 15 €",
             "filtres": '{"free_only": true, "period": "weekend", "zone": "thau", "public": "famille"}',
             "titre": "Jazz",
             "date": "sam. 17 oct.", "lieu": "Sète", "categorie": "concert", "prix": "8 €", "public": "Soirée",
             "resume": "Trio de jazz"}]
    pertinence.judge_rows(rows, call=call)
    assert "sam. 17 oct." not in seen["text"] and "8 €" not in seen["text"] and "Sète" not in seen["text"]
    assert "public : Soirée" in seen["text"] and "Trio de jazz" in seen["text"]
    assert "cases cochées : public : famille" in seen["text"]
    assert "gratuit" not in seen["text"] and "thau" not in seen["text"]
    assert "déjà été vérifiés" in seen["prompt"]


def test_agreement_gives_ac1_when_almost_everything_is_relevant() -> None:
    """16/10/2026 : 137 cartes pertinentes sur 139, le kappa ne dit plus rien."""

    rows = ([{"pertinent": "o", "juge": "o"}] * 95 + [{"pertinent": "o", "juge": "n"}] * 3
            + [{"pertinent": "n", "juge": "n"}, {"pertinent": "n", "juge": "o"}])
    result = pertinence.agreement(rows)
    assert result["accord"] == 0.96 and result["ac1"] > 0.9
    assert result["kappa"] < 0.4 and (result["rejets_humains"], result["rejets_retrouves"]) == (2, 1)


def test_second_grid_sample_is_stable_and_annotations_carry_over() -> None:
    """16/10/2026 : seconde grille sur des recherches nouvelles, ou sur les mêmes
    questions sans réannoter les cartes déjà vues."""

    questions = [{"question": f"q{index}"} for index in range(125)]
    first = pertinence.sample_questions(questions, 50)
    assert len(first) == 50 and first == pertinence.sample_questions(questions, 50)
    assert pertinence.sample_questions(questions, None) == questions
    rows = [{"question": "q1", "rang": "1", "titre": "A", "pertinent": "", "commentaire": ""},
            {"question": "q1", "rang": "2", "titre": "B", "pertinent": "", "commentaire": ""}]
    previous = [{"question": "q1", "titre": "A", "pertinent": "n", "commentaire": "pas de la danse"},
                {"question": "q1", "titre": "C", "pertinent": "o", "commentaire": ""}]
    assert pertinence.carry_over(rows, previous) == 1
    assert rows[0]["pertinent"] == "n" and rows[0]["commentaire"] == "pas de la danse"
    assert rows[1]["pertinent"] == ""


def test_history_keeps_judge_version_in_last_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ancien historique, sans la colonne de la consigne du juge
    monkeypatch.setattr(pertinence, "BENCH_DIR", tmp_path)
    old_header = pertinence.HISTORY_HEADER[:-1]
    (tmp_path / "historique.csv").write_text(
        ",".join(old_header) + "\r\n" + "2026-10-16T10:25:43+02:00,g.csv,50,139,0.986,1.0,,,,v1,\r\n",
        encoding="utf-8", newline="")  # sinon Windows double les \r
    report = {"questions": 50, "cartes_jugees": 139, "pertinence_at_3": 0.9, "questions_satisfaites": 1.0}
    history = pertinence.save_history(report, Path("grille.csv"), note="essai")
    with history.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == pertinence.HISTORY_HEADER
    assert rows[0]["juge_consigne"] == "" and rows[0]["prompt_version"] == "v1"
    assert rows[1]["juge_consigne"] == pertinence.JUDGE_PROMPT_VERSION
    assert rows[1]["prompt_version"] == pertinence.PROMPT_VERSION
    assert rows[1]["note"] == "essai"


def test_describe_filters_ignores_zone() -> None:
    raw = json.dumps({"zone": "Sète", "public": "famille", "outdoor": True})
    assert pertinence.describe_filters(raw) == "public : famille, en extérieur : oui"
