"""Tests du lecteur de programmes PDF (sans réseau ni vrai Mistral)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

import src.extractors.documents.document_extractor as module
from config.constants import EXTRACTOR_STATUS_SUCCESS
from src.event.event_normalizer import PARIS
from src.event.event_pipeline import event_from_article

SOURCE: dict[str, Any] = {
    "name": "Agenda Culture test", "source_id": "agenda_test", "url": "https://agglopole.fr/agenda.pdf",
    "credit": "Sète agglopôle", "period": {"debut": "2026-09-01", "fin": "2026-12-31"},
    "city": "Sète", "pages_per_chunk": 2, "days_forward": 60, "max_articles": 50,
    "filters": {"require_title": True, "require_url": True, "min_title_length": 3,
                "min_text_length": 0, "min_total_text_length": 3, "remove_duplicates": True},
}
PAGES = [
    "ROMANTISME FANTAISIE ET IMPRESSIONNISME\nConservatoire Manitas de Plata, Sète\nVendredi 23 octobre, 19h. Gratuit.",
    "CHAUSSONS !\nMédiathèque Olympe de Gouges, Balaruc-les-Bains\nSamedi 5 décembre, 10h30. Dès 3 ans.",
    "Coups de cœur des communes",
]
LLM_ANSWERS = {
    1: {"evenements": [
        {"titre": "Romantisme, fantaisie et impressionnisme", "date_debut": "2026-10-23", "heure_debut": "19:00",
         "lieu": "Conservatoire Manitas de Plata", "commune": "Sète", "gratuit": True, "categorie": "Concert"},
        {"titre": "Chaussons !", "date_debut": "2026-12-05", "heure_debut": "10:30", "commune": "Balaruc-les-Bains",
         "age_min": 3, "categorie": "Spectacle"},
        {"titre": "Grand bal de la Saint-Sylvestre", "date_debut": "2026-12-31"},   # inventé
        {"titre": "Chaussons !", "date_debut": "2025-12-05"},                     # mauvaise année
        {"titre": "Romantisme", "date_debut": "le 23"},                           # date illisible
    ]},
    3: {"evenements": []},
}


class FakeLlm:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, prompt: str, text: str) -> tuple[str, dict[str, int]]:
        self.calls += 1
        assert "du 2026-09-01 au 2026-12-31" in prompt
        first_page = int(text.split("]")[0].replace("[Page ", ""))
        return json.dumps(LLM_ANSWERS[first_page]), {"input_tokens": 1000, "output_tokens": 200}


class _FixedDatetime(module.datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 14, 12, 0, tzinfo=tz or PARIS)


@pytest.fixture
def fake_document(monkeypatch: pytest.MonkeyPatch, tmp_path) -> FakeLlm:
    llm = FakeLlm()
    monkeypatch.setattr(module, "fetch_document", lambda url: b"%PDF-1.4 faux document")
    monkeypatch.setattr(module, "extract_pages_text", lambda pdf: PAGES)
    monkeypatch.setattr(module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(module, "call_mistral", llm)
    monkeypatch.setattr(module, "datetime", _FixedDatetime)
    monkeypatch.setenv("DOCUMENT_CALL_PAUSE_S", "0")
    return llm


def test_guard_rails_and_pages(fake_document: FakeLlm) -> None:
    items = module.iter_document_items(SOURCE)
    kept = [item for item in items if "event" in item]
    rejected = sorted(item["rejected"] for item in items if "rejected" in item)
    assert [(item["event"].titre, item["page"]) for item in kept] == [
        ("Romantisme, fantaisie et impressionnisme", 1), ("Chaussons !", 2),
    ]
    assert rejected == ["champs_llm_invalides", "hors_periode", "titre_absent_du_texte"]
    assert fake_document.calls == 2   # 3 pages par paquets de 2


def test_unchanged_pdf_does_not_call_mistral_again(fake_document: FakeLlm) -> None:
    module.iter_document_items(SOURCE)
    module.iter_document_items(SOURCE)
    assert fake_document.calls == 2   # le second passage lit le cache


def test_engine_end_to_end(fake_document: FakeLlm) -> None:
    result = module.extract_articles_from_sources([SOURCE])
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    events = [event_from_article(article) for article in result.articles]
    assert [(e.title, e.city, e.is_free) for e in events] == [
        ("Romantisme, fantaisie et impressionnisme", "Sète", True),
        ("Chaussons !", "Balaruc-les-Bains", None),
    ]
    assert events[0].url == "https://agglopole.fr/agenda.pdf#page=1"
    assert result.rejection_reasons == {"champs_llm_invalides": 1, "hors_periode": 1, "titre_absent_du_texte": 1}


def test_chunks_mark_their_pages() -> None:
    """Pages vides sautées, chaque page marquée, recouvrement d'une page."""

    pages = ["a", "", "c", "d", "e"]
    assert module.chunk_pages(pages, 2) == [(1, "[Page 1]\na\n\n[Page 3]\nc"), (4, "[Page 4]\nd\n\n[Page 5]\ne")]
    assert [first for first, _ in module.chunk_pages(pages, 3, overlap=1)] == [1, 4]
    assert module.chunk_pages(pages, 3, overlap=1)[1][1].startswith("[Page 4]")


def test_page_selection() -> None:
    assert module.parse_page_selection("4-6, 9", 8) == [4, 5, 6]
    assert module.parse_page_selection("", 3) == [1, 2, 3]
    assert module.parse_page_selection("2-3,63", 64) == [2, 3, 63]


def test_overlap_duplicates_and_weekly_activity(fake_document: FakeLlm, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un événement lu dans deux paquets n'est gardé qu'une fois ; « tous les jeudis » est déroulé."""

    pages = [
        "VISITES\nVILLA LOUPIAN\nVisites guidées : tous les jeudis à 15h",
        "CHAUSSONS !\nSamedi 24 octobre, 10h30.",
    ]
    answers = {
        1: {"evenements": [
            {"titre": "Visites guidées", "date_debut": "2026-09-01", "date_fin": "2026-12-31", "heure_debut": "15:00",
             "lieu": "Musée gallo-romain Villa Loupian", "jours_semaine": ["jeudi"], "hors_vacances_scolaires": True},
            {"titre": "Chaussons !", "date_debut": "2026-10-24", "heure_debut": "10:30"},
        ]},
        2: {"evenements": [{"titre": "Chaussons !", "date_debut": "2026-10-24", "heure_debut": "10:30"}]},
    }
    monkeypatch.setattr(module, "extract_pages_text", lambda pdf: pages)
    monkeypatch.setattr(module, "call_mistral", lambda prompt, text: (
        json.dumps(answers[int(text.split("]")[0].replace("[Page ", ""))]), {}))
    source = {**SOURCE, "pages_per_chunk": 2, "pages_overlap": 1, "days_forward": 30,
              "venues": {"Villa Loupian": "Loupian"},
              "school_holidays": [{"debut": "2026-10-17", "fin": "2026-11-01"}]}
    items = [item for item in module.iter_document_items(source) if "event" in item]
    visits = [item["event"].date_debut.isoformat() for item in items if item["event"].titre == "Visites guidées"]
    assert visits == ["2026-10-15", "2026-11-05", "2026-11-12"]   # jeudis 22 et 29/10 : vacances
    assert sum(1 for item in items if item["event"].titre == "Chaussons !") == 1
    article = module.build_document_article(items[0], source)
    assert article["metadata"]["event"]["city"] == "Loupian"


def test_real_pdf_text_extraction() -> None:
    """Un vrai petit PDF (écrit à la main) : pypdf en extrait bien le texte."""

    pytest.importorskip("pypdf")
    content = b"BT /F1 18 Tf 72 720 Td (CONCERT DE JAZZ) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    assert module.extract_pages_text(pdf) == ["CONCERT DE JAZZ"]


def test_occurrence_url_keeps_page_fragment() -> None:
    """Le `?occurrence=` passe avant le `#page=N` (sinon : faux doublons)."""

    from src.event.event_pipeline import occurrence_article_url
    from src.event.event_schema import Event

    event = Event(event_id="abc123", source="document", source_event_id="x", title="Chaussons !",
                  start_at="2026-12-05T10:30:00+01:00", url="https://agglopole.fr/agenda.pdf#page=2")
    assert occurrence_article_url(event) == "https://agglopole.fr/agenda.pdf?occurrence=abc123#page=2"


def test_moved_pdf_gives_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un PDF déplacé (404) donne un message qui dit quoi corriger."""

    import httpx

    request = httpx.Request("GET", "https://x.fr/a.pdf")
    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: httpx.Response(404, request=request))
    with pytest.raises(ValueError, match="config/sources.yaml"):
        module.fetch_document("https://x.fr/a.pdf")


def test_web_page_instead_of_pdf_says_access_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une page web à la place du PDF (Azure, 15/10/2026) : le message le dit."""

    import httpx

    request = httpx.Request("GET", "https://x.fr/a.pdf")
    page = httpx.Response(200, request=request, headers={"content-type": "text/html; charset=UTF-8"},
                          content=b"<html>Just a moment...</html>")
    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: page)
    with pytest.raises(ValueError, match="page web reçue .*Accès bloqué"):
        module.fetch_document("https://x.fr/a.pdf")


def test_rate_limit_is_retried_with_growing_waits() -> None:
    answers = iter([RuntimeError("Error response 429: Rate limit exceeded"), RuntimeError("429"), ("{}", {})])
    waits: list[float] = []

    def llm(prompt: str, text: str):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert module.call_with_rate_limit(llm, "p", "t", sleep=waits.append) == ("{}", {})
    assert waits == [10.0, 20.0]


def test_failed_chunks_are_not_cached(fake_document: FakeLlm, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un paquet en échec n'entre pas en cache : seul lui est redemandé ensuite."""

    calls: list[int] = []

    def flaky(prompt: str, text: str):
        first_page = int(text.split("]")[0].replace("[Page ", ""))
        calls.append(first_page)
        if first_page == 3 and calls.count(3) == 1:
            raise ValueError("coupure réseau")
        return json.dumps(LLM_ANSWERS[first_page]), {}

    monkeypatch.setattr(module, "call_mistral", flaky)
    module.iter_document_items(SOURCE)
    module.iter_document_items(SOURCE)
    assert calls == [1, 3, 3]   # le paquet réussi (pages 1-2) vient du cache au 2e passage


def test_all_chunks_failing_is_a_source_failure(fake_document: FakeLlm, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quota épuisé partout : la source est en échec (et rien n'est mis en cache)."""

    def refused(prompt: str, text: str):
        raise ValueError("Error response 401: Unauthorized")

    monkeypatch.setattr(module, "call_mistral", refused)
    result = module.extract_articles_from_sources([SOURCE])
    assert result.status != EXTRACTOR_STATUS_SUCCESS and not result.articles
    assert not list((module.CACHE_DIR).glob("*.json")) if module.CACHE_DIR.exists() else True


# --- Revue du 15/10/2026 : cache -------------------------------------------
def test_cache_depends_on_period_and_chunking(fake_document: FakeLlm, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changer la période ou le découpage dans sources.yaml relit le PDF au lieu de réutiliser l'ancien cache."""

    prompts: list[str] = []

    def lenient_llm(prompt: str, text: str) -> tuple[str, dict[str, int]]:
        prompts.append(prompt)
        first_page = int(text.split("]")[0].replace("[Page ", ""))
        return json.dumps(LLM_ANSWERS.get(first_page, {"evenements": []})), {"input_tokens": 10, "output_tokens": 2}

    monkeypatch.setattr(module, "call_mistral", lenient_llm)
    module.iter_document_items(SOURCE)
    assert len(prompts) == 2
    module.iter_document_items(SOURCE)
    assert len(prompts) == 2                                           # PDF et réglages inchangés : cache
    module.iter_document_items({**SOURCE, "period": {**SOURCE["period"], "fin": "2027-01-31"}})
    assert len(prompts) == 4 and "2027-01-31" in prompts[-1]          # autre période : relu
    module.iter_document_items({**SOURCE, "pages_per_chunk": 1})
    assert len(prompts) == 4 + len(PAGES)                              # autre découpage : relu


def test_unreadable_cache_is_ignored_and_rewritten(fake_document: FakeLlm, tmp_path) -> None:
    """Une écriture coupée laisse un JSON tronqué : il ne doit pas bloquer la source pour toujours."""

    module.iter_document_items(SOURCE)
    (cache_file,) = list((tmp_path / "cache").glob("*.json"))
    cache_file.write_text('[{"first_page": 1, "te', encoding="utf-8")
    module.iter_document_items(SOURCE)
    assert fake_document.calls == 4                       # relu en entier
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1 and not list((tmp_path / "cache").glob("*.tmp"))
