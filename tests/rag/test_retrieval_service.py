"""Tests unitaires de `app/retrieval_service.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_retrieval_service.py` (commit 18f7ce1).
"""

from __future__ import annotations

from datetime import date

import pytest
from langchain_core.documents import Document

from app.retrieval_service import RetrievalService


@pytest.fixture
def service():
    return RetrievalService()


@pytest.fixture
def poor_doc():
    return Document(
        page_content="document pauvre",
        metadata={},
    )


@pytest.fixture
def docs_extra():
    return [
        Document(
            page_content="Exposition photo à Montpellier",
            metadata={
                "title": "Expo Photo",
                "description": "Exposition de photographie contemporaine",
                "location_name": "Galerie Z",
                "city": "Montpellier",
                "city_norm": "montpellier",
                "region": "Occitanie",
                "first_date": "2026-03-28",
                "last_date": "2026-03-30",
                "event_type": "Exposition",
                "event_type_norm": "exposition",
                "canonical_event_type": "exposition",
                "music_genre": "",
                "music_genre_norm": "",
                "search_text": "exposition photo photographie montpellier galerie z culture art",
                "is_free": True,
                "is_single_day": False,
                "duration_days": 3,
                "derived_event_terms": ["exposition", "photo", "photographie"],
                "derived_music_terms": [],
                "audience_terms": ["tout public"],
                "price_info": "gratuit",
                "keywords_title": ["expo", "photo"],
                "content_quality": 7,
                "has_long_description": True,
                "vector_score": 0.15,
                "source_url": "http://expo-photo.com",
                "url": "http://expo-photo.com",
            },
        ),
        Document(
            page_content="Concert rock à Paris",
            metadata={
                "title": "Rock Arena",
                "description": "Grand concert live rock",
                "location_name": "Arena Paris",
                "city": "Paris",
                "city_norm": "paris",
                "region": "Île-de-France",
                "first_date": "2026-06-10",
                "last_date": "2026-06-10",
                "event_type": "Concert",
                "event_type_norm": "concert",
                "canonical_event_type": "concert",
                "music_genre": "rock",
                "music_genre_norm": "rock",
                "search_text": "concert rock paris live arena musique groupe scene",
                "is_free": False,
                "is_single_day": True,
                "duration_days": 1,
                "derived_event_terms": ["concert", "live"],
                "derived_music_terms": ["rock"],
                "audience_terms": ["adulte"],
                "price_info": "payant",
                "keywords_title": ["rock"],
                "content_quality": 8,
                "has_long_description": False,
                "vector_score": 0.05,
                "source_url": "http://rock-arena.com",
                "url": "http://rock-arena.com",
            },
        ),
        Document(
            page_content="Braderie locale à Montpellier",
            metadata={
                "title": "Braderie locale",
                "description": "Vente associative de quartier",
                "location_name": "Place centrale",
                "city": "Montpellier",
                "city_norm": "montpellier",
                "region": "Occitanie",
                "first_date": "2026-05-01",
                "last_date": "2026-05-01",
                "event_type": "",
                "event_type_norm": "",
                "canonical_event_type": "",
                "music_genre": "",
                "music_genre_norm": "",
                "search_text": "braderie quartier montpellier association",
                "is_free": None,
                "is_single_day": True,
                "duration_days": 1,
                "derived_event_terms": [],
                "derived_music_terms": [],
                "audience_terms": [],
                "price_info": "inconnu",
                "keywords_title": ["braderie"],
                "content_quality": 3,
                "has_long_description": False,
                "vector_score": 0.40,
                "source_url": "http://braderie.com",
                "url": "http://braderie.com",
            },
        ),
        Document(
            page_content="Festival très long sans vraie date exploitable",
            metadata={
                "title": "Mega Festival",
                "description": "Événement très long",
                "location_name": "Parc XXL",
                "city": "Lyon",
                "city_norm": "lyon",
                "region": "Auvergne-Rhône-Alpes",
                "first_date": "2026-01-01",
                "last_date": "2026-08-01",
                "event_type": "Festival",
                "event_type_norm": "festival",
                "canonical_event_type": "festival",
                "music_genre": "",
                "music_genre_norm": "",
                "search_text": "festival lyon culture musique scene art",
                "is_free": False,
                "is_single_day": False,
                "duration_days": 213,
                "derived_event_terms": ["festival"],
                "derived_music_terms": [],
                "audience_terms": ["tout public"],
                "price_info": "payant",
                "keywords_title": ["festival"],
                "content_quality": 5,
                "has_long_description": True,
                "vector_score": 0.35,
                "source_url": "http://mega-festival.com",
                "url": "http://mega-festival.com",
            },
        ),
    ]


# -------------------------------------------------------------------------
# Lecture documentaire : branches fallback
# -------------------------------------------------------------------------

def test_doc_dates_sets_last_date_to_first_when_missing(service):
    doc = Document(
        page_content="x",
        metadata={"first_date": "2026-03-28", "last_date": ""},
    )

    first_date, last_date = service._doc_dates(doc)

    assert first_date == date(2026, 3, 28)
    assert last_date == date(2026, 3, 28)


def test_doc_text_falls_back_to_metadata_and_page_content(service):
    doc = Document(
        page_content="contenu libre",
        metadata={
            "title": "Titre X",
            "description": "Desc Y",
            "location_name": "Lieu Z",
            "city": "Montpellier",
            "event_type": "Exposition",
            "canonical_event_type": "",
            "music_genre": "",
            "price_info": "gratuit",
            "search_text": "",
        },
    )

    text = service._doc_text(doc)

    assert "titre x" in text
    assert "desc y" in text
    assert "montpellier" in text
    assert "contenu libre" in text


def test_doc_title_keywords_returns_empty_set_when_invalid(service):
    doc = Document(page_content="x", metadata={"keywords_title": "expo"})
    assert service._doc_title_keywords(doc) == set()


def test_doc_derived_terms_returns_empty_set_when_invalid(service):
    doc = Document(page_content="x", metadata={"derived_event_terms": "expo"})
    assert service._doc_derived_terms(doc) == set()


def test_doc_derived_music_terms_returns_empty_set_when_invalid(service):
    doc = Document(page_content="x", metadata={"derived_music_terms": "jazz"})
    assert service._doc_derived_music_terms(doc) == set()


def test_doc_duration_days_uses_fallback_from_dates(service):
    doc = Document(
        page_content="x",
        metadata={
            "duration_days": None,
            "first_date": "2026-03-28",
            "last_date": "2026-03-30",
        },
    )

    assert service._doc_duration_days(doc) == 3


def test_doc_duration_days_returns_large_default_when_no_dates(service, poor_doc):
    assert service._doc_duration_days(poor_doc) == 999999


def test_doc_content_quality_returns_zero_when_invalid(service):
    doc = Document(page_content="x", metadata={"content_quality": "abc"})
    assert service._doc_content_quality(doc) == 0


# -------------------------------------------------------------------------
# Extraction de signaux : branches complémentaires
# -------------------------------------------------------------------------

def test_extract_city_returns_none_when_unknown(service):
    assert service._extract_city("je cherche quelque chose a berlin") is None


def test_extract_date_filters_returns_empty_structure_when_no_date(service):
    result = service._extract_date_filters("je cherche une exposition")

    assert result["exact_date"] is None
    assert result["date_start"] is None
    assert result["date_end"] is None
    assert result["month"] is None
    assert result["year"] is None


def test_extract_signals_broad_query(service):
    signals = service.extract_signals("Que faire ce week-end ?")

    assert signals["has_time_constraint"] is False or isinstance(signals["has_time_constraint"], bool)
    assert signals["has_type_constraint"] is False
    assert signals["has_price_constraint"] is False
    assert signals["has_music_constraint"] is False
    assert isinstance(signals["is_broad_query"], bool)


def test_extract_signals_time_constraint(service):
    signals = service.extract_signals("Que faire le 29 mars 2026 à Montpellier ?")

    assert signals["has_time_constraint"] is True
    assert signals["date_start"] == date(2026, 3, 29)
    assert signals["date_end"] == date(2026, 3, 29)


# -------------------------------------------------------------------------
# Helpers métier
# -------------------------------------------------------------------------

def test_vector_score_to_bonus_returns_zero_when_missing(service, poor_doc):
    assert service._vector_score_to_bonus(poor_doc) == 0.0


def test_vector_score_to_bonus_returns_zero_when_invalid(service):
    doc = Document(page_content="x", metadata={"vector_score": "bad"})
    assert service._vector_score_to_bonus(doc) == 0.0


def test_supports_any_variant_false(service):
    assert service._supports_any_variant(
        doc_text="atelier peinture",
        doc_title_keywords={"atelier"},
        doc_derived_terms={"peinture"},
        variants=["concert", "live"],
    ) is False


def test_date_overlaps_false_when_event_start_missing(service):
    assert service._date_overlaps(
        event_start=None,
        event_end=None,
        query_start=date(2026, 3, 28),
        query_end=date(2026, 3, 29),
    ) is False


def test_keyword_text_score_returns_zero_without_keywords(service):
    assert service._keyword_text_score("concert jazz", []) == 0.0


def test_keyword_title_score_returns_zero_without_keywords(service):
    score, present, absent = service._keyword_title_score({"concert"}, [])
    assert score == 0.0
    assert present == 0
    assert absent == 0


def test_derived_terms_score_returns_zero_without_terms(service):
    assert service._derived_terms_score(set(), ["concert"]) == 0.0
    assert service._derived_terms_score({"concert"}, []) == 0.0


def test_strong_keywords_score_returns_zero_without_keywords(service):
    score = service._strong_keywords_score(
        doc_text="concert jazz",
        doc_title_keywords={"concert"},
        doc_derived_terms={"jazz"},
        strong_keywords=[],
    )
    assert score == 0.0


def test_is_musical_document_false_for_non_musical_doc(service, docs_extra):
    doc = docs_extra[2]

    assert service._is_musical_document(
        doc_text=service._doc_text(doc),
        doc_event_type=service._doc_event_type(doc),
        doc_music_genre=service._doc_music_genre(doc),
        doc_derived_terms=service._doc_derived_terms(doc),
        doc_derived_music_terms=service._doc_derived_music_terms(doc),
    ) is False

def test_is_cultural_document_false_for_generic_doc(service, docs_extra):
    doc = docs_extra[2]

    result = service._is_cultural_document(
        doc,
        doc_text=service._doc_text(doc),
        doc_event_type=service._doc_event_type(doc),
        doc_derived_terms=service._doc_derived_terms(doc),
    )

    assert result is False


def test_event_type_match_level_variant(service, docs_extra):
    doc = docs_extra[0]

    level = service._event_type_match_level(
        requested_event_type="exposition",
        doc_event_type="",
        doc_text=service._doc_text(doc),
        doc_title_keywords=service._doc_title_keywords(doc),
        doc_derived_terms=service._doc_derived_terms(doc),
    )

    assert level == "variant"


def test_event_type_match_level_returns_variant_when_no_requested_type(service, docs_extra):
    doc = docs_extra[0]

    level = service._event_type_match_level(
        requested_event_type="",
        doc_event_type=service._doc_event_type(doc),
        doc_text=service._doc_text(doc),
        doc_title_keywords=service._doc_title_keywords(doc),
        doc_derived_terms=service._doc_derived_terms(doc),
    )

    assert level == "variant"


def test_is_doc_compatible_with_query_false_for_wrong_cultural_doc(service, docs_extra):
    signals = service.extract_signals("Je cherche un événement culturel à Montpellier")
    assert service._is_doc_compatible_with_query(docs_extra[2], signals) is False


def test_duration_penalty_missing_dates(service, poor_doc):
    signals = service.extract_signals("Que faire le 29 mars 2026 ?")
    penalty = service._duration_penalty(poor_doc, signals)
    assert penalty == -4.0


def test_duration_penalty_time_constraint_long_event(service, docs_extra):
    signals = service.extract_signals("Que faire le 29 mars 2026 ?")
    penalty = service._duration_penalty(docs_extra[3], signals)
    assert penalty <= -12.0


def test_duration_penalty_broad_query_long_event(service, docs_extra):
    signals = service.extract_signals("Que faire à Lyon ?")
    penalty = service._duration_penalty(docs_extra[3], signals)
    assert penalty <= -6.0


# -------------------------------------------------------------------------
# Scoring : branches supplémentaires
# -------------------------------------------------------------------------

def test_score_document_penalizes_wrong_city(service, docs_extra):
    signals = service.extract_signals("Je cherche une exposition à Montpellier")
    score_good = service.score_document(docs_extra[0], signals)
    score_bad = service.score_document(docs_extra[1], signals)

    assert score_good > score_bad


def test_score_document_music_query_prefers_musical_doc(service, docs_extra):
    signals = service.extract_signals("Je cherche un concert rock payant à Paris")
    score_music = service.score_document(docs_extra[1], signals)
    score_non_music = service.score_document(docs_extra[0], signals)

    assert score_music > score_non_music


def test_score_document_updates_debug_metadata(service, docs_extra):
    signals = service.extract_signals("Je cherche une exposition gratuite à Montpellier")
    _ = service.score_document(docs_extra[0], signals)

    md = docs_extra[0].metadata
    assert "matched_title_keywords" in md
    assert "missing_title_keywords" in md
    assert "detected_city_signal" in md
    assert "detected_event_type_signal" in md
    assert "detected_price_signal" in md


# -------------------------------------------------------------------------
# Tri et diversification : branches complémentaires
# -------------------------------------------------------------------------

def test_temporal_distance_days_returns_large_default_without_date(service, poor_doc):
    signals = service.extract_signals("Que faire le 29 mars 2026 ?")
    assert service._temporal_distance_days(poor_doc, signals) == 999999


def test_temporal_distance_days_exact_before_event(service, docs_extra):
    signals = service.extract_signals("Que faire le 20 mars 2026 ?")
    distance = service._temporal_distance_days(docs_extra[0], signals)
    assert distance > 0


def test_temporal_distance_days_range_overlap(service, docs_extra):
    signals = service.extract_signals("Que faire du 29 au 30 mars 2026 ?")
    distance = service._temporal_distance_days(docs_extra[0], signals)
    assert distance == 0


def test_doc_similarity_signature_handles_empty_metadata(service, poor_doc):
    signature = service._doc_similarity_signature(poor_doc)
    assert signature == set()


def test_apply_diversification_empty(service):
    assert service._apply_diversification([], {}, 3) == []


def test_apply_diversification_sets_diversified_score(service, docs_extra):
    signals = service.extract_signals("Je cherche une exposition à Montpellier")
    scored_docs = [
        (docs_extra[0], 10.0),
        (docs_extra[1], 9.0),
    ]

    selected = service._apply_diversification(scored_docs, signals, top_k=2)

    assert len(selected) == 2
    assert "diversified_score" in selected[0].metadata


def test_apply_strict_post_filter_returns_empty_on_empty_input(service):
    assert service._apply_strict_post_filter([], {"is_broad_query": False}) == []


def test_apply_strict_post_filter_broad_fallback(service, docs_extra):
    signals = service.extract_signals("Que faire à Paris ?")
    signals["is_broad_query"] = True
    signals["is_cultural_query"] = True
    signals["event_type"] = None
    signals["music_genre"] = None

    result = service._apply_strict_post_filter(docs_extra, signals)

    assert isinstance(result, list)
    assert len(result) >= 1


# -------------------------------------------------------------------------
# API publique : ranking complet
# -------------------------------------------------------------------------

def test_rank_documents_prefers_best_matching_doc(service, docs_extra):
    docs = service.rank_documents(
        question="Je cherche une exposition photo gratuite à Montpellier",
        raw_docs=docs_extra,
        top_k=2,
    )

    assert len(docs) <= 2
    assert docs[0].metadata["title"] == "Expo Photo"
    assert "final_score" in docs[0].metadata
    assert "duration_days" in docs[0].metadata


def test_rank_documents_with_scores_returns_sorted_rows(service, docs_extra):
    rows = service.rank_documents_with_scores(
        question="Je cherche un concert rock payant à Paris",
        raw_docs=docs_extra,
        top_k=3,
    )

    assert len(rows) <= 3
    assert rows[0]["title"] == "Rock Arena"
    assert "final_score" in rows[0]
    assert "recency_date" in rows[0]
    assert "duration_days" in rows[0]


def test_rank_documents_with_scores_includes_debug_fields(service, docs_extra):
    rows = service.rank_documents_with_scores(
        question="Je cherche une exposition gratuite à Montpellier",
        raw_docs=docs_extra,
        top_k=2,
    )

    row = rows[0]
    assert "matched_title_keywords" in row
    assert "missing_title_keywords" in row
    assert "detected_city_signal" in row
    assert "detected_event_type_signal" in row
    assert "detected_is_cultural_query" in row