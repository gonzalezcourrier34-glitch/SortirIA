"""Documents des partenaires (PDF, Word) lus par Mistral et validés par un humain (sans réseau)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from src.extractors.datasets.source import partner_documents as documents
from src.extractors.datasets.source import partner_extractor as extractor

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path: Path, paragraphs: list[str], table: list[list[str]] | None = None) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
                       + "</w:tr>" for row in table)
        body += f"<w:tbl>{rows}</w:tbl>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    path.write_bytes(buffer.getvalue())
    return path


ANSWER = {"evenements": [
    {"titre": "Soirée blind test", "date": "06/11/2026", "heure_debut": "21:00", "lieu": "", "commune": "",
     "prix": "Gratuit", "gratuit": "oui", "public": "adultes", "categorie": "Soirée", "description": "Quiz musical.",
     "lien": ""},
    {"titre": "Concert de Mozart inventé", "date": "07/11/2026", "lieu": "Bar", "commune": "Sète"},
]}


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setattr(documents, "CACHE_DIR", folder)
    return folder


def test_word_text_includes_tables(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "prog.docx", ["Programme de novembre", "Soirée blind test le 6/11"],
                     table=[["Date", "Événement"], ["7/11", "Karaoké"]])
    assert documents.docx_paragraphs(path.read_bytes()) == [
        "Programme de novembre", "Soirée blind test le 6/11", "Date | Événement", "7/11 | Karaoké"]


def test_mistral_rows_are_checked_against_the_text_and_cached(tmp_path: Path, cache: Path) -> None:
    path = make_docx(tmp_path / "bar.docx", ["Programme du bar", "Jeudi 6 novembre : soirée blind test, gratuit"])
    calls: list[str] = []

    def llm(prompt: str, text: str):
        calls.append(text)
        assert "06/11/2026" not in prompt and "16/10/2026" in prompt
        return json.dumps(ANSWER), {"input_tokens": 100, "output_tokens": 50}

    rows = documents.read_document_rows(path, llm=llm, today=date(2026, 10, 16))
    assert [row.get("rejet_llm", "") for row in rows] == ["", "titre_absent_du_texte"]   # Mozart inventé
    assert documents.read_document_rows(path, llm=llm, today=date(2026, 10, 16)) == rows
    assert len(calls) == 1                                                               # cache
    assert documents.cached_rows(path) == rows


def test_document_is_only_imported_after_approval(tmp_path: Path, cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_docx(tmp_path / "bar_du_port.docx", ["Jeudi 6 novembre : soirée blind test, gratuit"])
    monkeypatch.setattr(documents, "default_llm", lambda prompt, text: (json.dumps(ANSWER), {}))
    partner = {**extractor.partner_for_file(path, {}), "venue": "Bar du Port", "city": "Sète",
               "website": "https://bardu.port"}

    [pending] = extractor.read_partner_file(path, partner, today=date(2026, 10, 16))
    assert pending["rejected"] == "document_a_valider" and "1 séance" in pending["detail"]
    assert [event.title for event in pending["preview"]] == ["Soirée blind test"]

    sha = documents.file_sha256(path)
    assert documents.approve_document(tmp_path, path.name, sha)[0] is True
    assert documents.is_approved(path)
    items = extractor.read_partner_file(path, partner, today=date(2026, 10, 16))
    [event] = [item["event"] for item in items if "event" in item]
    # Lieu et commune absents de la plaquette : ceux du partenaire.
    assert (event.venue_name, event.city, event.is_free) == ("Bar du Port", "Sète", True)
    assert [item["rejected"] for item in items if "rejected" in item] == ["titre_absent_du_texte"]

    path.write_bytes(path.read_bytes() + b" ")                  # document modifié : nouvel accord
    assert not documents.is_approved(path)
    assert documents.approve_document(tmp_path, path.name, sha)[0] is False


def test_documents_are_found_with_the_spreadsheets(tmp_path: Path) -> None:
    make_docx(tmp_path / "bar.docx", ["x"])
    (tmp_path / "salle.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "_documents_valides.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert [path.name for path in extractor.find_partner_files(tmp_path, {})] == ["bar.docx", "salle.pdf"]


def test_agent_proposes_the_import_and_the_human_applies_it(tmp_path: Path, cache: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import nodes, proposals
    from agent.proposals import describe_action
    from agent.services import pending_partner_document

    path = make_docx(tmp_path / "bar.docx", ["Jeudi 6 novembre : soirée blind test, gratuit"])
    partner = {**extractor.partner_for_file(path, {}), "venue": "Bar", "city": "Sète", "website": "https://bar.fr"}
    assert pending_partner_document(path, partner) is None          # pas encore lu cette nuit
    monkeypatch.setattr(documents, "default_llm", lambda prompt, text: (json.dumps(ANSWER), {}))
    extractor.read_partner_file(path, partner)                       # la collecte de la nuit le lit
    pending = pending_partner_document(path, partner)
    assert pending is not None and pending["events"] == 1 and "Soirée blind test" in pending["preview"][0]

    [proposal] = nodes.partner_document_proposals([pending], date(2026, 10, 16))
    assert proposal["action"]["type"] == "valider_document_partenaire"
    assert "importer bar.docx (1 séance(s) lue(s) par Mistral)" in describe_action(proposal)

    monkeypatch.setattr(proposals, "partner_directory", lambda: tmp_path)
    applied, explanation = proposals.apply_proposal(proposal)
    assert applied and "_documents_valides.json" in explanation
    assert pending_partner_document(path, partner) is None           # validé : plus de proposition


def test_programme_link_written_once_serves_every_session(tmp_path: Path, cache: Path) -> None:
    """Essai réel du 16/10/2026 : le lien du programme n'est écrit qu'une fois, en bas."""

    path = make_docx(tmp_path / "bar.docx", ["Jeudi 6 novembre : soirée blind test, gratuit",
                                             "Programme et réservations : https://example.org/bar."])
    rows = documents.read_document_rows(path, llm=lambda prompt, text: (json.dumps(ANSWER), {}))
    assert rows[0]["lien"] == "https://example.org/bar"
    assert "lien" not in rows[1] or not rows[1]["lien"]          # ligne écartée : laissée telle quelle
    assert documents.read_document_rows(path)[0]["lien"] == "https://example.org/bar"   # depuis le cache
