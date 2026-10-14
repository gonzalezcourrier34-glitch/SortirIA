"""
Tests unitaires du module document_service.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_document_service.py` (commit 18f7ce1).

Objectifs :
- valider les helpers internes
- valider la construction des métadonnées documentaires
- valider la transformation d'un événement OpenAgenda en Document
- valider le chargement complet avec déduplication et filtrage zone/scope
"""

from __future__ import annotations

from datetime import datetime

import pytest
from langchain_core.documents import Document

import app.document_service as ds


# -------------------------------------------------------------------------
# Helpers de test
# -------------------------------------------------------------------------

@pytest.fixture
def sample_event() -> dict:
    return {
        "uid": "evt_001",
        "agenda": {"uid": "agenda_123"},
        "title": {"fr": "Exposition peinture moderne"},
        "description": {"fr": "Une belle exposition d'art contemporain gratuite."},
        "longDescription": {
            "fr": (
                "Venez découvrir plusieurs artistes locaux, leurs univers visuels, "
                "leurs techniques, leurs inspirations et les démarches créatives qui "
                "structurent cette exposition. Cette présentation propose un parcours "
                "riche, détaillé et commenté autour des œuvres, du lieu, des matériaux "
                "utilisés et de la scène artistique contemporaine locale."
            )
        },
        "location": {
            "name": "Musée Fabre",
            "city": "Montpellier",
            "region": "Occitanie",
        },
        "firstDate": "2025-09-21T18:30:00Z",
        "lastDate": "2025-09-23T20:00:00Z",
        "eventType": "Exposition",
        "canonicalUrl": "https://openagenda.com/event/evt_001",
        "conditions": "Entrée libre",
        "pricing": "",
        "attendanceMode": "",
    }


# -------------------------------------------------------------------------
# _nested_get
# -------------------------------------------------------------------------

def test_nested_get_returns_nested_value():
    data = {"a": {"b": {"c": "ok"}}}
    assert ds._nested_get(data, "a", "b", "c") == "ok"


def test_nested_get_returns_none_when_path_missing():
    data = {"a": {"b": {}}}
    assert ds._nested_get(data, "a", "b", "c") is None


def test_nested_get_returns_none_when_intermediate_is_not_dict():
    data = {"a": "not_a_dict"}
    assert ds._nested_get(data, "a", "b") is None


# -------------------------------------------------------------------------
# _first_non_empty
# -------------------------------------------------------------------------

def test_first_non_empty_returns_first_textual_value():
    assert ds._first_non_empty(None, "", "bonjour", "salut") == "bonjour"


def test_first_non_empty_returns_empty_string_when_all_empty():
    assert ds._first_non_empty(None, "", "   ") == ""


# -------------------------------------------------------------------------
# _extract_event_field
# -------------------------------------------------------------------------

def test_extract_event_field_returns_first_matching_field():
    event = {
        "title": "",
        "alt": {"fr": "Titre alternatif"},
    }
    value = ds._extract_event_field(event, ("title",), ("alt", "fr"))
    assert value == "Titre alternatif"


def test_extract_event_field_returns_empty_string_when_no_match():
    event = {}
    value = ds._extract_event_field(event, ("title",), ("description", "fr"))
    assert value == ""


# -------------------------------------------------------------------------
# Dates
# -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("2025-09-21", datetime(2025, 9, 21)),
        ("2025-09-21T18:30:00", datetime(2025, 9, 21, 18, 30, 0)),
    ],
)
def test_parse_iso_date_valid_formats(value, expected):
    parsed = ds._parse_iso_date(value)
    assert parsed is not None
    assert parsed.year == expected.year
    assert parsed.month == expected.month
    assert parsed.day == expected.day


def test_parse_iso_date_accepts_z_suffix():
    parsed = ds._parse_iso_date("2025-09-21T18:30:00Z")
    assert parsed is not None
    assert parsed.year == 2025
    assert parsed.month == 9
    assert parsed.day == 21


def test_parse_iso_date_returns_none_for_invalid_value():
    assert ds._parse_iso_date("not-a-date") is None
    assert ds._parse_iso_date(None) is None


def test_normalize_iso_day_returns_yyyy_mm_dd():
    assert ds._normalize_iso_day("2025-09-21T18:30:00Z") == "2025-09-21"


def test_normalize_iso_day_returns_empty_string_when_invalid():
    assert ds._normalize_iso_day("bad-date") == ""


def test_build_duration_label_no_dates():
    assert ds._build_duration_label("", "") == "durée non précisée"


def test_build_duration_label_single_day():
    assert ds._build_duration_label("2025-09-21", "2025-09-21") == "événement sur une journée"


def test_build_duration_label_multiple_days():
    assert ds._build_duration_label("2025-09-21", "2025-09-23") == "événement sur plusieurs jours"


def test_compute_duration_days_none_when_no_dates():
    assert ds._compute_duration_days("", "") is None


def test_compute_duration_days_one_when_only_first_date():
    assert ds._compute_duration_days("2025-09-21", "") == 1


def test_compute_duration_days_multiple_days():
    assert ds._compute_duration_days("2025-09-21", "2025-09-23") == 3


# -------------------------------------------------------------------------
# Qualité / enrichissement
# -------------------------------------------------------------------------

def test_compute_content_quality_counts_present_fields():
    score = ds._compute_content_quality(
        title="Titre",
        description="Desc",
        long_description="Long",
        location_name="Lieu",
        city="Montpellier",
        region="Occitanie",
        first_date="2025-09-21",
        last_date="2025-09-22",
        source_url="https://example.com",
        price_info="gratuit",
        event_type="expo",
        music_genre="",
        canonical_event_type="exposition",
    )
    assert score == 11


def test_build_cultural_tags_for_exhibition():
    tags = ds._build_cultural_tags(
        canonical_event_type="exposition",
        derived_terms=["expo", "vernissage"],
    )
    assert "culture" in tags
    assert "artistique" in tags
    assert "exposition" in tags


def test_build_search_text_contains_core_fields():
    text = ds._build_search_text(
        title="Concert jazz",
        description_search="Une soirée musicale",
        long_description_search="Avec plusieurs artistes",
        location_name="Salle Victoire 2",
        city="Montpellier",
        region="Occitanie",
        event_type="Concert",
        canonical_event_type="concert",
        canonical_music_genre="jazz",
        audience_terms=["tout public"],
        first_date="2025-09-21",
        last_date="2025-09-21",
        price_info="gratuit",
        access_label="gratuit",
        duration_label="événement sur une journée",
        title_keywords=["concert", "jazz"],
        derived_terms=["concert", "live"],
        derived_music_terms=["jazz"],
        cultural_tags=["musique", "concert"],
    )

    assert "concert jazz" in text.lower()
    assert "montpellier" in text.lower()
    assert "gratuit" in text.lower()
    assert "musique" in text.lower()


# -------------------------------------------------------------------------
# Cohérence zone / scope
# -------------------------------------------------------------------------

def test_matches_zone_scope_city_true():
    doc = Document(page_content="x", metadata={"city": "Montpellier"})
    assert ds._matches_zone_scope(doc, zone="Montpellier", scope="city") is True


def test_matches_zone_scope_city_false():
    doc = Document(page_content="x", metadata={"city": "Sète"})
    assert ds._matches_zone_scope(doc, zone="Montpellier", scope="city") is False


def test_matches_zone_scope_non_city_returns_true():
    doc = Document(page_content="x", metadata={"city": ""})
    assert ds._matches_zone_scope(doc, zone="Montpellier", scope="region") is True


# -------------------------------------------------------------------------
# Fenêtre de dates
# -------------------------------------------------------------------------

def test_get_default_date_window_returns_two_iso_dates():
    date_from, date_to = ds.get_default_date_window()
    assert isinstance(date_from, str)
    assert isinstance(date_to, str)
    assert len(date_from) == 10
    assert len(date_to) == 10
    assert date_from < date_to


# -------------------------------------------------------------------------
# build_event_document
# -------------------------------------------------------------------------

def test_build_event_document_returns_langchain_document(sample_event):
    doc = ds.build_event_document(sample_event)

    assert isinstance(doc, Document)
    assert "Titre : Exposition peinture moderne" in doc.page_content
    assert doc.metadata["event_uid"] == "evt_001"
    assert doc.metadata["agenda_uid"] == "agenda_123"
    assert doc.metadata["title"] == "Exposition peinture moderne"
    assert doc.metadata["location_name"] == "Musée Fabre"
    assert doc.metadata["city"] == "Montpellier"
    assert doc.metadata["region"] == "Occitanie"
    assert doc.metadata["first_date"] == "2025-09-21"
    assert doc.metadata["last_date"] == "2025-09-23"
    assert doc.metadata["source"] == "openagenda"
    assert doc.metadata["url"] == "https://openagenda.com/event/evt_001"
    assert doc.metadata["doc_id"] == "openagenda_evt_001"


def test_build_event_document_sets_duration_fields(sample_event):
    doc = ds.build_event_document(sample_event)

    assert doc.metadata["duration_label"] == "événement sur plusieurs jours"
    assert doc.metadata["duration_days"] == 3
    assert doc.metadata["is_single_day"] is False


def test_build_event_document_sets_has_long_description(sample_event):
    doc = ds.build_event_document(sample_event)
    assert doc.metadata["has_long_description"] is True


def test_build_event_document_keeps_empty_music_genre_without_musical_context(sample_event):
    doc = ds.build_event_document(sample_event)
    assert doc.metadata["music_genre"] == ""
    assert doc.metadata["derived_music_terms"] == []


def test_build_event_document_builds_normalized_fields(sample_event):
    doc = ds.build_event_document(sample_event)

    assert doc.metadata["title_norm"]
    assert doc.metadata["city_norm"] == "montpellier"
    assert doc.metadata["region_norm"] == "occitanie"


def test_build_event_document_handles_missing_optional_fields():
    event = {
        "uid": "evt_002",
        "title": {"fr": "Atelier créatif"},
        "location": {"city": "Montpellier"},
        "firstDate": "2025-10-10",
        "lastDate": "2025-10-10",
    }

    doc = ds.build_event_document(event)

    assert doc.metadata["event_uid"] == "evt_002"
    assert doc.metadata["title"] == "Atelier créatif"
    assert doc.metadata["city"] == "Montpellier"
    assert doc.metadata["duration_days"] == 1
    assert doc.metadata["is_single_day"] is True
    assert doc.metadata["source_url"] == ""


# -------------------------------------------------------------------------
# search_agendas_for_zone
# -------------------------------------------------------------------------

def test_search_agendas_for_zone_raises_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(ds, "OPENAGENDA_API_KEY", "")
    with pytest.raises(ValueError, match="OPENAGENDA_API_KEY manquante"):
        ds.search_agendas_for_zone("Montpellier")


def test_search_agendas_for_zone_calls_api(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"agendas": [{"uid": "ag_1"}, {"uid": "ag_2"}]}

    def fake_get(url, headers=None, params=None, timeout=None):
        assert "agendas" in url
        assert headers == {"key": "fake-key"}
        assert params["search"] == "Montpellier"
        assert params["official"] == 1
        return FakeResponse()

    monkeypatch.setattr(ds, "OPENAGENDA_API_KEY", "fake-key")
    monkeypatch.setattr(ds.requests, "get", fake_get)

    agendas = ds.search_agendas_for_zone("Montpellier")
    assert len(agendas) == 2
    assert agendas[0]["uid"] == "ag_1"


# -------------------------------------------------------------------------
# fetch_openagenda_events
# -------------------------------------------------------------------------

def test_fetch_openagenda_events_raises_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(ds, "OPENAGENDA_API_KEY", "")
    with pytest.raises(ValueError, match="OPENAGENDA_API_KEY manquante"):
        ds.fetch_openagenda_events("agenda_1", "Montpellier")


def test_fetch_openagenda_events_paginates_until_empty(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params=None, timeout=None):
        calls.append(params["offset"])
        if params["offset"] == 0:
            return FakeResponse({"events": [{"uid": "e1"}, {"uid": "e2"}]})
        if params["offset"] == 200:
            return FakeResponse({"events": [{"uid": "e3"}]})
        return FakeResponse({"events": []})

    monkeypatch.setattr(ds, "OPENAGENDA_API_KEY", "fake-key")
    monkeypatch.setattr(ds.requests, "get", fake_get)
    monkeypatch.setattr(ds, "get_default_date_window", lambda: ("2025-01-01", "2025-12-31"))

    events = ds.fetch_openagenda_events(
        agenda_uid="agenda_1",
        zone="Montpellier",
        scope="city",
        limit=200,
        max_pages=10,
    )

    assert [e["uid"] for e in events] == ["e1", "e2", "e3"]
    assert calls == [0, 200, 400]


def test_fetch_openagenda_events_respects_max_pages(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"events": [{"uid": "e1"}]}

    def fake_get(url, params=None, timeout=None):
        return FakeResponse()

    monkeypatch.setattr(ds, "OPENAGENDA_API_KEY", "fake-key")
    monkeypatch.setattr(ds.requests, "get", fake_get)
    monkeypatch.setattr(ds, "get_default_date_window", lambda: ("2025-01-01", "2025-12-31"))

    events = ds.fetch_openagenda_events(
        agenda_uid="agenda_1",
        zone="Montpellier",
        scope="city",
        limit=1,
        max_pages=2,
    )

    assert len(events) == 2


# -------------------------------------------------------------------------
# load_documents
# -------------------------------------------------------------------------

def test_load_documents_returns_deduplicated_documents(monkeypatch):
    agendas = [
        {"uid": "agenda_1"},
        {"uid": "agenda_2"},
    ]

    events_by_agenda = {
        "agenda_1": [
            {
                "uid": "evt_1",
                "title": {"fr": "Expo A"},
                "location": {"city": "Montpellier"},
                "firstDate": "2025-09-21",
                "lastDate": "2025-09-21",
            },
            {
                "uid": "evt_2",
                "title": {"fr": "Expo B"},
                "location": {"city": "Montpellier"},
                "firstDate": "2025-09-22",
                "lastDate": "2025-09-22",
            },
        ],
        "agenda_2": [
            {
                "uid": "evt_2",
                "title": {"fr": "Expo B dupliquée"},
                "location": {"city": "Montpellier"},
                "firstDate": "2025-09-22",
                "lastDate": "2025-09-22",
            },
            {
                "uid": "evt_3",
                "title": {"fr": "Expo C"},
                "location": {"city": "Montpellier"},
                "firstDate": "2025-09-23",
                "lastDate": "2025-09-23",
            },
        ],
    }

    monkeypatch.setattr(ds, "search_agendas_for_zone", lambda zone: agendas)
    monkeypatch.setattr(
        ds,
        "fetch_openagenda_events",
        lambda agenda_uid, zone, scope: events_by_agenda[agenda_uid],
    )

    docs = ds.load_documents(zone="Montpellier", scope="city")

    assert len(docs) == 3
    event_uids = [doc.metadata["event_uid"] for doc in docs]
    assert event_uids == ["evt_1", "evt_2", "evt_3"]


def test_load_documents_filters_on_zone_scope(monkeypatch):
    agendas = [{"uid": "agenda_1"}]

    events = [
        {
            "uid": "evt_1",
            "title": {"fr": "Expo Montpellier"},
            "location": {"city": "Montpellier"},
            "firstDate": "2025-09-21",
            "lastDate": "2025-09-21",
        },
        {
            "uid": "evt_2",
            "title": {"fr": "Expo Sète"},
            "location": {"city": "Sète"},
            "firstDate": "2025-09-22",
            "lastDate": "2025-09-22",
        },
    ]

    monkeypatch.setattr(ds, "search_agendas_for_zone", lambda zone: agendas)
    monkeypatch.setattr(ds, "fetch_openagenda_events", lambda agenda_uid, zone, scope: events)

    docs = ds.load_documents(zone="Montpellier", scope="city")

    assert len(docs) == 1
    assert docs[0].metadata["event_uid"] == "evt_1"


def test_load_documents_ignores_agenda_without_uid(monkeypatch):
    monkeypatch.setattr(ds, "search_agendas_for_zone", lambda zone: [{"uid": ""}, {}])

    docs = ds.load_documents(zone="Montpellier", scope="city")
    assert docs == []


def test_load_documents_skips_empty_event_lists(monkeypatch):
    monkeypatch.setattr(ds, "search_agendas_for_zone", lambda zone: [{"uid": "agenda_1"}])
    monkeypatch.setattr(ds, "fetch_openagenda_events", lambda agenda_uid, zone, scope: [])

    docs = ds.load_documents(zone="Montpellier", scope="city")
    assert docs == []


def test_load_documents_skips_events_without_uid(monkeypatch):
    monkeypatch.setattr(ds, "search_agendas_for_zone", lambda zone: [{"uid": "agenda_1"}])
    monkeypatch.setattr(
        ds,
        "fetch_openagenda_events",
        lambda agenda_uid, zone, scope: [
            {
                "uid": "",
                "title": {"fr": "Sans uid"},
                "location": {"city": "Montpellier"},
                "firstDate": "2025-09-21",
                "lastDate": "2025-09-21",
            },
            {
                "uid": "evt_ok",
                "title": {"fr": "Avec uid"},
                "location": {"city": "Montpellier"},
                "firstDate": "2025-09-21",
                "lastDate": "2025-09-21",
            },
        ],
    )

    docs = ds.load_documents(zone="Montpellier", scope="city")

    assert len(docs) == 1
    assert docs[0].metadata["event_uid"] == "evt_ok"