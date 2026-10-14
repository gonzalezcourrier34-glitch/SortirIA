"""Tests unitaires de `app/filter_service.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_filter_service.py` (commit 18f7ce1).
"""

from __future__ import annotations

from datetime import date

import pytest
from langchain_core.documents import Document

from app.filter_service import FilterService


@pytest.fixture
def service() -> FilterService:
    return FilterService()


def make_doc(
    *,
    title: str = "Doc",
    city: str = "Montpellier",
    city_norm: str | None = None,
    canonical_event_type: str = "",
    canonical_event_type_norm: str | None = None,
    event_type: str = "",
    event_type_norm: str | None = None,
    music_genre: str = "",
    music_genre_norm: str | None = None,
    first_date: str = "",
    last_date: str = "",
    is_free: bool | None = None,
    duration_days: int | str | None = None,
    is_single_day: bool | None = None,
    audience_terms: list[str] | None = None,
    derived_event_terms: list[str] | None = None,
    derived_music_terms: list[str] | None = None,
    keywords_title: list[str] | None = None,
    is_strong_cultural_candidate: bool = False,
    is_weak_cultural_candidate: bool = False,
    has_market_signal: bool = False,
    has_repair_signal: bool = False,
    has_business_signal: bool = False,
    search_text: str = "",
    description: str = "",
    location_name: str = "",
    price_info: str = "",
    page_content: str = "",
    source_url: str = "",
    url: str = "",
) -> Document:
    return Document(
        page_content=page_content,
        metadata={
            "title": title,
            "city": city,
            "city_norm": city_norm,
            "canonical_event_type": canonical_event_type,
            "canonical_event_type_norm": canonical_event_type_norm,
            "event_type": event_type,
            "event_type_norm": event_type_norm,
            "music_genre": music_genre,
            "music_genre_norm": music_genre_norm,
            "first_date": first_date,
            "last_date": last_date,
            "is_free": is_free,
            "duration_days": duration_days,
            "is_single_day": is_single_day,
            "audience_terms": audience_terms or [],
            "derived_event_terms": derived_event_terms or [],
            "derived_music_terms": derived_music_terms or [],
            "keywords_title": keywords_title or [],
            "is_strong_cultural_candidate": is_strong_cultural_candidate,
            "is_weak_cultural_candidate": is_weak_cultural_candidate,
            "has_market_signal": has_market_signal,
            "has_repair_signal": has_repair_signal,
            "has_business_signal": has_business_signal,
            "search_text": search_text,
            "description": description,
            "location_name": location_name,
            "price_info": price_info,
            "source_url": source_url,
            "url": url,
        },
    )


# -------------------------------------------------------------------
# Utilitaires de base
# -------------------------------------------------------------------


def test_safe(service: FilterService):
    assert service._safe(None) == ""
    assert service._safe("abc") == "abc"
    assert service._safe(123) == "123"


def test_normalize_text_delegates_to_lexical_service(service: FilterService):
    assert service.normalize_text("Événement à Sète !") == "evenement a sete"


def test_parse_iso_date_valid(service: FilterService):
    assert service.parse_iso_date("2026-03-27") == date(2026, 3, 27)


def test_parse_iso_date_valid_with_time(service: FilterService):
    assert service.parse_iso_date("2026-03-27T10:20:30") == date(2026, 3, 27)


def test_parse_iso_date_invalid(service: FilterService):
    assert service.parse_iso_date("") is None
    assert service.parse_iso_date(None) is None
    assert service.parse_iso_date("bad-date") is None


def test_build_date_valid(service: FilterService):
    assert service._build_date(2026, 3, 27) == date(2026, 3, 27)


def test_build_date_invalid(service: FilterService):
    assert service._build_date(2026, 2, 31) is None


# -------------------------------------------------------------------
# Lecture métadonnées document
# -------------------------------------------------------------------


def test_metadata_returns_dict(service: FilterService):
    doc = make_doc(title="Test")
    assert service._metadata(doc)["title"] == "Test"


def test_doc_dates_with_start_and_end(service: FilterService):
    doc = make_doc(first_date="2026-03-01", last_date="2026-03-05")
    assert service._doc_dates(doc) == (date(2026, 3, 1), date(2026, 3, 5))


def test_doc_dates_with_only_start(service: FilterService):
    doc = make_doc(first_date="2026-03-01", last_date="")
    assert service._doc_dates(doc) == (date(2026, 3, 1), date(2026, 3, 1))


def test_doc_dates_without_dates(service: FilterService):
    doc = make_doc(first_date="", last_date="")
    assert service._doc_dates(doc) == (None, None)


def test_doc_city_prefers_city_norm(service: FilterService):
    doc = make_doc(city="Montpellier", city_norm="montpellier")
    assert service._doc_city(doc) == "montpellier"


def test_doc_city_fallback_normalizes_city(service: FilterService):
    doc = make_doc(city="Sète", city_norm=None)
    assert service._doc_city(doc) == "sete"


def test_doc_event_type_prefers_canonical_norm(service: FilterService):
    doc = make_doc(
        canonical_event_type="Exposition",
        canonical_event_type_norm="exposition",
        event_type="Concert",
        event_type_norm="concert",
    )
    assert service._doc_event_type(doc) == "exposition"


def test_doc_event_type_fallback_chain(service: FilterService):
    doc = make_doc(
        canonical_event_type="Atelier",
        canonical_event_type_norm=None,
        event_type="Concert",
        event_type_norm=None,
    )
    assert service._doc_event_type(doc) == "atelier"


def test_doc_music_genre_prefers_norm(service: FilterService):
    doc = make_doc(music_genre="Rock", music_genre_norm="rock")
    assert service._doc_music_genre(doc) == "rock"


def test_doc_music_genre_fallback_normalization(service: FilterService):
    doc = make_doc(music_genre="Électro", music_genre_norm=None)
    assert service._doc_music_genre(doc) == "electro"


def test_doc_search_text_prefers_search_text(service: FilterService):
    doc = make_doc(search_text="Concert rock à Montpellier")
    assert service._doc_search_text(doc) == "concert rock a montpellier"


def test_doc_search_text_builds_fallback_text(service: FilterService):
    doc = make_doc(
        title="Expo Archi",
        description="Description test",
        location_name="Musée X",
        city="Montpellier",
        event_type="Exposition",
        canonical_event_type="Exposition",
        music_genre="",
        page_content="Contenu page",
        search_text="",
    )
    text = service._doc_search_text(doc)
    assert "expo archi" in text
    assert "description test" in text
    assert "musee x" in text
    assert "montpellier" in text
    assert "contenu page" in text


def test_doc_is_free(service: FilterService):
    assert service._doc_is_free(make_doc(is_free=True)) is True
    assert service._doc_is_free(make_doc(is_free=False)) is False
    assert service._doc_is_free(make_doc(is_free=None)) is None


def test_doc_is_single_day(service: FilterService):
    assert service._doc_is_single_day(make_doc(is_single_day=True)) is True
    assert service._doc_is_single_day(make_doc(is_single_day=False)) is False
    assert service._doc_is_single_day(make_doc(is_single_day="yes")) is None


def test_doc_duration_days_from_metadata(service: FilterService):
    assert service._doc_duration_days(make_doc(duration_days=5)) == 5


def test_doc_duration_days_from_metadata_invalid_then_compute(service: FilterService):
    doc = make_doc(duration_days="bad", first_date="2026-03-01", last_date="2026-03-03")
    assert service._doc_duration_days(doc) == 3


def test_doc_duration_days_from_dates_single_day(service: FilterService):
    doc = make_doc(duration_days="bad", first_date="2026-03-01", last_date="")
    assert service._doc_duration_days(doc) == 1


def test_doc_duration_days_none(service: FilterService):
    doc = make_doc(first_date="", last_date="")
    assert service._doc_duration_days(doc) is None


def test_doc_derived_event_terms(service: FilterService):
    doc = make_doc(derived_event_terms=["Exposition", "Photo"])
    assert service._doc_derived_event_terms(doc) == {"exposition", "photo"}


def test_doc_derived_event_terms_non_list(service: FilterService):
    doc = make_doc(derived_event_terms="bad")  # type: ignore[arg-type]
    assert service._doc_derived_event_terms(doc) == set()


def test_doc_derived_music_terms(service: FilterService):
    doc = make_doc(derived_music_terms=["Rock", "Garage"])
    assert service._doc_derived_music_terms(doc) == {"rock", "garage"}


def test_doc_derived_music_terms_non_list(service: FilterService):
    doc = make_doc(derived_music_terms="bad")  # type: ignore[arg-type]
    assert service._doc_derived_music_terms(doc) == set()


def test_doc_audience_terms(service: FilterService):
    doc = make_doc(audience_terms=["Famille", "Enfant"])
    assert service._doc_audience_terms(doc) == {"famille", "enfant"}


def test_doc_audience_terms_non_list(service: FilterService):
    doc = make_doc(audience_terms="bad")  # type: ignore[arg-type]
    assert service._doc_audience_terms(doc) == set()


def test_doc_candidate_flags_and_negative_signals(service: FilterService):
    doc = make_doc(
        is_strong_cultural_candidate=True,
        is_weak_cultural_candidate=True,
        has_market_signal=True,
        has_repair_signal=True,
        has_business_signal=True,
    )
    assert service._doc_is_strong_cultural_candidate(doc) is True
    assert service._doc_is_weak_cultural_candidate(doc) is True
    assert service._doc_has_market_signal(doc) is True
    assert service._doc_has_repair_signal(doc) is True
    assert service._doc_has_business_signal(doc) is True


# -------------------------------------------------------------------
# Extraction contraintes question
# -------------------------------------------------------------------


def test_extract_city(service: FilterService):
    assert service._extract_city("concert a montpellier") == "montpellier"
    assert service._extract_city("concert a paris") == "paris"
    assert service._extract_city("concert ailleurs") is None


def test_extract_duration_filter(service: FilterService):
    assert service._extract_duration_filter("sur une journee") == "single_day"
    assert service._extract_duration_filter("plusieurs jours") == "multi_day"
    assert service._extract_duration_filter("concert a montpellier") is None


def test_get_month_bounds(service: FilterService):
    start, end = service._get_month_bounds(2026, 3)
    assert start == date(2026, 3, 1)
    assert end == date(2026, 4, 1)


def test_get_month_bounds_december(service: FilterService):
    start, end = service._get_month_bounds(2026, 12)
    assert start == date(2026, 12, 1)
    assert end == date(2027, 1, 1)


def test_get_year_bounds(service: FilterService):
    assert service._get_year_bounds(2026) == (date(2026, 1, 1), date(2027, 1, 1))


def test_get_weekend_range_from_weekday(service: FilterService):
    saturday, sunday = service._get_weekend_range(date(2026, 3, 27), next_weekend=False)
    assert saturday == date(2026, 3, 28)
    assert sunday == date(2026, 3, 29)


def test_get_weekend_range_from_saturday(service: FilterService):
    saturday, sunday = service._get_weekend_range(date(2026, 3, 28), next_weekend=False)
    assert saturday == date(2026, 3, 28)
    assert sunday == date(2026, 3, 29)


def test_get_weekend_range_from_sunday(service: FilterService):
    saturday, sunday = service._get_weekend_range(date(2026, 3, 29), next_weekend=False)
    assert saturday == date(2026, 3, 28)
    assert sunday == date(2026, 3, 29)


def test_get_weekend_range_next_weekend(service: FilterService):
    saturday, sunday = service._get_weekend_range(date(2026, 3, 27), next_weekend=True)
    assert saturday == date(2026, 4, 4)
    assert sunday == date(2026, 4, 5)


def test_extract_explicit_weekend_range_two_days(service: FilterService):
    start, end = service._extract_explicit_weekend_range(
        "quels evenements le week-end du 28 au 29 mars 2026"
    )
    assert start == date(2026, 3, 28)
    assert end == date(2026, 3, 29)


def test_extract_explicit_weekend_range_single_saturday(service: FilterService):
    start, end = service._extract_explicit_weekend_range(
        "quels evenements le week-end du 28 mars 2026"
    )
    assert start == date(2026, 3, 28)
    assert end == date(2026, 3, 29)


def test_extract_explicit_weekend_range_single_sunday(service: FilterService):
    start, end = service._extract_explicit_weekend_range(
        "quels evenements le week-end du 29 mars 2026"
    )
    assert start == date(2026, 3, 28)
    assert end == date(2026, 3, 29)


def test_extract_explicit_weekend_range_none(service: FilterService):
    start, end = service._extract_explicit_weekend_range("concert en mars 2026")
    assert start is None
    assert end is None


def test_extract_date_filters_weekend_explicit(service: FilterService):
    result = service._extract_date_filters("week-end du 28 au 29 mars 2026")
    assert result["time_mode"] == "weekend_explicit"
    assert result["start_date"] == date(2026, 3, 28)
    assert result["end_date"] == date(2026, 3, 29)
    assert result["month"] == 3
    assert result["year"] == 2026


def test_extract_date_filters_this_weekend(service: FilterService, monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 27)

    monkeypatch.setattr("app.filter_service.date", FakeDate)
    result = service._extract_date_filters("que faire ce week-end")
    assert result["time_mode"] == "weekend_this"
    assert result["start_date"] == FakeDate(2026, 3, 28)
    assert result["end_date"] == FakeDate(2026, 3, 29)


def test_extract_date_filters_next_weekend(service: FilterService, monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 27)

    monkeypatch.setattr("app.filter_service.date", FakeDate)
    result = service._extract_date_filters("week-end prochain")
    assert result["time_mode"] == "weekend_next"
    assert result["start_date"] == FakeDate(2026, 4, 4)
    assert result["end_date"] == FakeDate(2026, 4, 5)


def test_extract_date_filters_next_month(service: FilterService, monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 3, 27)

    monkeypatch.setattr("app.filter_service.date", FakeDate)
    result = service._extract_date_filters("mois prochain")
    assert result["time_mode"] == "next_month"
    assert result["month"] == 4
    assert result["year"] == 2026


def test_extract_date_filters_next_month_december(service: FilterService, monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 12, 20)

    monkeypatch.setattr("app.filter_service.date", FakeDate)
    result = service._extract_date_filters("mois prochain")
    assert result["time_mode"] == "next_month"
    assert result["month"] == 1
    assert result["year"] == 2027


def test_extract_date_filters_date_range(service: FilterService):
    result = service._extract_date_filters("du 20 au 21 septembre 2025")
    assert result["time_mode"] == "date_range"
    assert result["start_date"] == date(2025, 9, 20)
    assert result["end_date"] == date(2025, 9, 21)
    assert result["month"] == 9
    assert result["year"] == 2025


def test_extract_date_filters_date_range_reversed(service: FilterService):
    result = service._extract_date_filters("du 21 au 20 septembre 2025")
    assert result["time_mode"] == "date_range"
    assert result["start_date"] == date(2025, 9, 20)
    assert result["end_date"] == date(2025, 9, 21)


def test_extract_date_filters_exact_date(service: FilterService):
    result = service._extract_date_filters("le 20 septembre 2025")
    assert result["time_mode"] == "exact_date"
    assert result["exact_date"] == date(2025, 9, 20)
    assert result["month"] == 9
    assert result["year"] == 2025


def test_extract_date_filters_month_year(service: FilterService):
    result = service._extract_date_filters("en septembre 2025")
    assert result["time_mode"] == "month_year"
    assert result["month"] == 9
    assert result["year"] == 2025


def test_extract_date_filters_year_only(service: FilterService):
    result = service._extract_date_filters("en 2025")
    assert result["time_mode"] == "year"
    assert result["year"] == 2025


def test_extract_date_filters_none(service: FilterService):
    result = service._extract_date_filters("concert a montpellier")
    assert result["time_mode"] is None
    assert result["exact_date"] is None
    assert result["month"] is None
    assert result["year"] is None


def test_extract_filters_with_explicit_city(service: FilterService):
    filters = service.extract_filters(
        question="Quels concerts de rock gratuits pour enfants à Montpellier en septembre 2025 ?",
        default_city="Paris",
    )
    assert filters["city"] == "montpellier"
    assert filters["explicit_city"] == "montpellier"
    assert filters["event_type"] == "concert"
    assert filters["music_genre"] == "rock"
    assert filters["price_filter"] == "gratuit"
    assert filters["audience_terms"] == ["enfant"]
    assert filters["month"] == 9
    assert filters["year"] == 2025


def test_extract_filters_with_default_city(service: FilterService):
    filters = service.extract_filters(
        question="Quels concerts ?",
        default_city="Montpellier",
    )
    assert filters["city"] == "montpellier"
    assert filters["explicit_city"] is None


# -------------------------------------------------------------------
# Matching document / filtres
# -------------------------------------------------------------------


def test_matches_city(service: FilterService):
    filters = {"city": "montpellier"}
    assert service.matches_city(make_doc(city="Montpellier"), filters) is True
    assert service.matches_city(make_doc(city="Paris"), filters) is False
    assert service.matches_city(make_doc(city="Paris"), {"city": None}) is True


def test_supports_any_variant(service: FilterService):
    doc = make_doc(search_text="Vernissage et exposition photo")
    assert service._supports_any_variant(doc, ["vernissage", "concert"]) is True
    assert service._supports_any_variant(doc, []) is False


def test_event_type_match_level_exact(service: FilterService):
    doc = make_doc(canonical_event_type_norm="exposition")
    assert service._event_type_match_level(doc, "exposition") == "exact"


def test_event_type_match_level_variant_by_search_text(service: FilterService):
    doc = make_doc(
        event_type="",
        search_text="vernissage d une expo photo",
        derived_event_terms=[],
    )
    assert service._event_type_match_level(doc, "exposition") == "variant"


def test_event_type_match_level_variant_by_derived_terms(service: FilterService):
    doc = make_doc(
        event_type="",
        search_text="evenement divers",
        derived_event_terms=["exposition"],
    )
    assert service._event_type_match_level(doc, "exposition") == "variant"


def test_event_type_match_level_mismatch_due_to_other_doc_type(service: FilterService):
    doc = make_doc(
        canonical_event_type_norm="concert",
        search_text="vernissage d expo",
        derived_event_terms=["exposition"],
    )
    assert service._event_type_match_level(doc, "exposition") == "variant"


def test_matches_event_type(service: FilterService):
    doc = make_doc(canonical_event_type_norm="concert")
    assert service.matches_event_type(doc, {"event_type": "concert"}) is True
    assert service.matches_event_type(doc, {"event_type": "exposition"}) is False
    assert service.matches_event_type(doc, {"event_type": None}) is True


def test_is_musical_document_by_event_type(service: FilterService):
    assert service._is_musical_document(make_doc(canonical_event_type_norm="concert")) is True


def test_is_musical_document_by_music_genre(service: FilterService):
    assert service._is_musical_document(make_doc(music_genre_norm="rock")) is True


def test_is_musical_document_by_derived_music(service: FilterService):
    assert service._is_musical_document(make_doc(derived_music_terms=["rock"])) is True


def test_is_musical_document_by_text_hits(service: FilterService):
    doc = make_doc(search_text="concert live avec groupe sur scene")
    assert service._is_musical_document(doc) is True


def test_is_musical_document_false(service: FilterService):
    doc = make_doc(search_text="exposition photo en galerie")
    assert service._is_musical_document(doc) is False


def test_is_cultural_document_false_for_business(service: FilterService):
    assert service._is_cultural_document(make_doc(has_business_signal=True)) is False


def test_is_cultural_document_false_for_market(service: FilterService):
    assert service._is_cultural_document(make_doc(has_market_signal=True)) is False


def test_is_cultural_document_false_for_repair(service: FilterService):
    assert service._is_cultural_document(make_doc(has_repair_signal=True)) is False


def test_is_cultural_document_true_for_strong_candidate_flag(service: FilterService):
    assert service._is_cultural_document(make_doc(is_strong_cultural_candidate=True)) is True


def test_is_cultural_document_true_for_strong_event_type(service: FilterService):
    assert service._is_cultural_document(make_doc(canonical_event_type_norm="exposition")) is True


def test_is_cultural_document_true_for_weak_candidate_with_strong_terms(service: FilterService):
    doc = make_doc(
        is_weak_cultural_candidate=True,
        search_text="atelier autour du theatre et de la poesie",
    )
    assert service._is_cultural_document(doc) is True


def test_is_cultural_document_true_for_derived_terms(service: FilterService):
    doc = make_doc(derived_event_terms=["concert"])
    assert service._is_cultural_document(doc) is True


def test_is_cultural_document_true_for_search_text(service: FilterService):
    doc = make_doc(search_text="projection de cinema et theatre")
    assert service._is_cultural_document(doc) is True


def test_is_cultural_document_false_for_generic_doc(service: FilterService):
    doc = make_doc(search_text="rencontre conviviale et pratique")
    assert service._is_cultural_document(doc) is False


def test_matches_music_genre_exact(service: FilterService):
    doc = make_doc(
        music_genre_norm="rock",
        canonical_event_type_norm="concert",
    )
    assert service.matches_music_genre(doc, {"music_genre": "rock"}) is True


def test_matches_music_genre_from_derived_terms(service: FilterService):
    doc = make_doc(
        derived_music_terms=["rock"],
        search_text="concert rock",
        canonical_event_type_norm="concert",
    )
    assert service.matches_music_genre(doc, {"music_genre": "rock"}) is True


def test_matches_music_genre_from_variant(service: FilterService):
    doc = make_doc(
        music_genre_norm="",
        search_text="concert punk garage live",
        canonical_event_type_norm="concert",
    )
    assert service.matches_music_genre(doc, {"music_genre": "rock"}) is True


def test_matches_music_genre_false_if_not_musical(service: FilterService):
    doc = make_doc(
        music_genre_norm="",
        search_text="atelier punk garage",
        canonical_event_type_norm="atelier",
    )
    assert service.matches_music_genre(doc, {"music_genre": "rock"}) is False


def test_matches_music_genre_false_if_other_genre_explicit(service: FilterService):
    doc = make_doc(
        music_genre_norm="jazz",
        search_text="concert rock et jazz",
        canonical_event_type_norm="concert",
    )
    assert service.matches_music_genre(doc, {"music_genre": "rock"}) is False


def test_matches_music_genre_false_no_match(service: FilterService):
    doc = make_doc(search_text="concert jazz", canonical_event_type_norm="concert")
    assert service.matches_music_genre(doc, {"music_genre": "rock"}) is False


def test_matches_music_genre_true_without_filter(service: FilterService):
    assert service.matches_music_genre(make_doc(), {"music_genre": None}) is True


def test_matches_cultural_scope(service: FilterService):
    cultural_doc = make_doc(canonical_event_type_norm="concert")
    non_cultural_doc = make_doc(has_market_signal=True)

    assert service.matches_cultural_scope(cultural_doc, {"is_cultural_query": True}) is True
    assert service.matches_cultural_scope(non_cultural_doc, {"is_cultural_query": True}) is False
    assert service.matches_cultural_scope(non_cultural_doc, {"is_cultural_query": False}) is True


def test_matches_duration_single_day(service: FilterService):
    assert service.matches_duration(
        make_doc(is_single_day=True),
        {"duration_filter": "single_day"},
    ) is True
    assert service.matches_duration(
        make_doc(is_single_day=False),
        {"duration_filter": "single_day"},
    ) is False


def test_matches_duration_single_day_from_duration_days(service: FilterService):
    assert service.matches_duration(
        make_doc(is_single_day=None, duration_days=1),
        {"duration_filter": "single_day"},
    ) is True
    assert service.matches_duration(
        make_doc(is_single_day=None, duration_days=3),
        {"duration_filter": "single_day"},
    ) is False


def test_matches_duration_single_day_unknown_is_soft(service: FilterService):
    assert service.matches_duration(
        make_doc(is_single_day=None, duration_days=None),
        {"duration_filter": "single_day"},
    ) is True


def test_matches_duration_multi_day(service: FilterService):
    assert service.matches_duration(
        make_doc(is_single_day=False),
        {"duration_filter": "multi_day"},
    ) is True
    assert service.matches_duration(
        make_doc(is_single_day=True),
        {"duration_filter": "multi_day"},
    ) is False


def test_matches_duration_multi_day_from_duration_days(service: FilterService):
    assert service.matches_duration(
        make_doc(is_single_day=None, duration_days=2),
        {"duration_filter": "multi_day"},
    ) is True
    assert service.matches_duration(
        make_doc(is_single_day=None, duration_days=1),
        {"duration_filter": "multi_day"},
    ) is False


def test_matches_duration_no_filter(service: FilterService):
    assert service.matches_duration(make_doc(), {"duration_filter": None}) is True


def test_matches_weekend_with_max_span(service: FilterService):
    doc = make_doc(first_date="2025-09-20", last_date="2025-09-21")
    assert service._matches_weekend_with_max_span(
        doc,
        start_date=date(2025, 9, 20),
        end_date=date(2025, 9, 21),
        max_span_days=3,
    ) is True


def test_matches_weekend_with_max_span_false_on_long_event(service: FilterService):
    doc = make_doc(first_date="2025-09-18", last_date="2025-09-25")
    assert service._matches_weekend_with_max_span(
        doc,
        start_date=date(2025, 9, 20),
        end_date=date(2025, 9, 21),
        max_span_days=3,
    ) is False


def test_matches_weekend_with_max_span_false_on_no_overlap(service: FilterService):
    doc = make_doc(first_date="2025-09-10", last_date="2025-09-11")
    assert service._matches_weekend_with_max_span(
        doc,
        start_date=date(2025, 9, 20),
        end_date=date(2025, 9, 21),
        max_span_days=3,
    ) is False


def test_matches_weekend_with_max_span_false_on_missing_date(service: FilterService):
    doc = make_doc(first_date="", last_date="")
    assert service._matches_weekend_with_max_span(
        doc,
        start_date=date(2025, 9, 20),
        end_date=date(2025, 9, 21),
        max_span_days=3,
    ) is False


def test_matches_date_without_filters(service: FilterService):
    assert service.matches_date(make_doc(), {}) is True


def test_matches_date_false_on_missing_doc_date_when_filter_present(service: FilterService):
    assert service.matches_date(make_doc(first_date=""), {"year": 2025}) is False


def test_matches_date_with_range_non_weekend(service: FilterService):
    doc = make_doc(first_date="2025-09-20", last_date="2025-09-21")
    filters = {
        "start_date": date(2025, 9, 20),
        "end_date": date(2025, 9, 21),
        "time_mode": "date_range",
    }
    assert service.matches_date(doc, filters) is True


def test_matches_date_with_exact_date(service: FilterService):
    doc = make_doc(first_date="2025-09-20", last_date="2025-09-21")
    assert service.matches_date(doc, {"exact_date": date(2025, 9, 20)}) is True
    assert service.matches_date(doc, {"exact_date": date(2025, 9, 22)}) is False


def test_matches_date_with_month_year(service: FilterService):
    doc = make_doc(first_date="2025-09-20", last_date="2025-09-21")
    assert service.matches_date(doc, {"month": 9, "year": 2025}) is True
    assert service.matches_date(doc, {"month": 10, "year": 2025}) is False


def test_matches_date_with_year(service: FilterService):
    doc = make_doc(first_date="2025-09-20", last_date="2025-09-21")
    assert service.matches_date(doc, {"year": 2025}) is True
    assert service.matches_date(doc, {"year": 2026}) is False


def test_matches_date_weekend_mode(service: FilterService):
    doc = make_doc(first_date="2025-09-20", last_date="2025-09-21")
    filters = {
        "start_date": date(2025, 9, 20),
        "end_date": date(2025, 9, 21),
        "time_mode": "weekend_explicit",
    }
    assert service.matches_date(doc, filters) is True


def test_matches_price(service: FilterService):
    assert service.matches_price(make_doc(is_free=True), {"price_filter": "gratuit"}) is True
    assert service.matches_price(make_doc(is_free=False), {"price_filter": "gratuit"}) is False
    assert service.matches_price(make_doc(is_free=None), {"price_filter": "gratuit"}) is True

    assert service.matches_price(make_doc(is_free=False), {"price_filter": "payant"}) is True
    assert service.matches_price(make_doc(is_free=True), {"price_filter": "payant"}) is False
    assert service.matches_price(make_doc(is_free=None), {"price_filter": "payant"}) is True

    assert service.matches_price(make_doc(), {"price_filter": None}) is True


def test_matches_audience(service: FilterService):
    doc = make_doc(audience_terms=["famille"])
    assert service.matches_audience(doc, {"audience_terms": ["famille"]}) is True
    assert service.matches_audience(doc, {"audience_terms": ["enfant"]}) is False
    assert service.matches_audience(make_doc(audience_terms=[]), {"audience_terms": ["famille"]}) is True
    assert service.matches_audience(doc, {"audience_terms": []}) is True


# -------------------------------------------------------------------
# Pilotage global
# -------------------------------------------------------------------


def test_has_strong_filters(service: FilterService):
    assert service.has_strong_filters({"explicit_city": "montpellier"}) is True
    assert service.has_strong_filters({"event_type": "concert"}) is True
    assert service.has_strong_filters({"music_genre": "rock"}) is True
    assert service.has_strong_filters({"is_cultural_query": True}) is True
    assert service.has_strong_filters({"duration_filter": "single_day"}) is True
    assert service.has_strong_filters({"audience_terms": ["famille"]}) is True
    assert service.has_strong_filters({"exact_date": date(2025, 9, 20)}) is True
    assert service.has_strong_filters({"month": 9}) is True
    assert service.has_strong_filters({"year": 2025}) is True
    assert service.has_strong_filters({"start_date": date(2025, 9, 20)}) is True
    assert service.has_strong_filters({"end_date": date(2025, 9, 21)}) is True
    assert service.has_strong_filters({"price_filter": "gratuit"}) is True
    assert service.has_strong_filters({}) is False


def test_empty_pipeline_result(service: FilterService):
    doc = make_doc(title="A")
    result = service._empty_pipeline_result([doc])
    assert result["input"] == [doc]
    assert result["after_city"] == []
    assert result["after_price"] == []


def test_run_filter_pipeline_empty_after_city(service: FilterService):
    docs = [make_doc(city="Paris")]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_city"] == []
    assert result["after_price"] == []


def test_run_filter_pipeline_empty_after_date(service: FilterService):
    docs = [make_doc(city="Montpellier", first_date="2025-09-20", last_date="2025-09-20")]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": date(2025, 9, 21),
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert len(result["after_city"]) == 1
    assert result["after_date"] == []
    assert result["after_price"] == []


def test_run_filter_pipeline_stops_after_date_when_no_other_explicit_filters(service: FilterService):
    docs = [make_doc(city="Montpellier", first_date="2025-09-20", last_date="2025-09-20")]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": date(2025, 9, 20),
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_date"] == docs
    assert result["after_type"] == docs
    assert result["after_price"] == docs


def test_run_filter_pipeline_empty_after_type(service: FilterService):
    docs = [make_doc(city="Montpellier", canonical_event_type_norm="concert")]
    filters = {
        "city": "montpellier",
        "event_type": "exposition",
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_type"] == []


def test_run_filter_pipeline_empty_after_music(service: FilterService):
    docs = [make_doc(city="Montpellier", canonical_event_type_norm="concert", music_genre_norm="jazz")]
    filters = {
        "city": "montpellier",
        "event_type": "concert",
        "music_genre": "rock",
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_music"] == []


def test_run_filter_pipeline_empty_after_cultural(service: FilterService):
    docs = [make_doc(city="Montpellier", has_market_signal=True)]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": True,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_cultural"] == []


def test_run_filter_pipeline_empty_after_audience(service: FilterService):
    docs = [make_doc(city="Montpellier", audience_terms=["famille"], canonical_event_type_norm="concert")]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": ["enfant"],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_audience"] == []


def test_run_filter_pipeline_empty_after_duration(service: FilterService):
    docs = [make_doc(city="Montpellier", is_single_day=False)]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": "single_day",
        "audience_terms": [],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": None,
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_duration"] == []


def test_run_filter_pipeline_empty_after_price(service: FilterService):
    docs = [make_doc(city="Montpellier", is_free=False)]
    filters = {
        "city": "montpellier",
        "event_type": None,
        "music_genre": None,
        "is_cultural_query": False,
        "duration_filter": None,
        "audience_terms": [],
        "exact_date": None,
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": "gratuit",
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_price"] == []


def test_run_filter_pipeline_full_success(service: FilterService):
    docs = [
        make_doc(
            title="Concert Rock",
            city="Montpellier",
            canonical_event_type_norm="concert",
            music_genre_norm="rock",
            first_date="2025-09-20",
            last_date="2025-09-20",
            is_free=True,
            is_single_day=True,
            audience_terms=["famille"],
            is_strong_cultural_candidate=True,
            search_text="concert rock famille montpellier",
        )
    ]
    filters = {
        "city": "montpellier",
        "event_type": "concert",
        "music_genre": "rock",
        "is_cultural_query": True,
        "duration_filter": "single_day",
        "audience_terms": ["famille"],
        "exact_date": date(2025, 9, 20),
        "month": None,
        "year": None,
        "start_date": None,
        "end_date": None,
        "price_filter": "gratuit",
        "explicit_city": "montpellier",
    }
    result = service._run_filter_pipeline(filters, docs)
    assert result["after_price"] == docs


def test_filter_documents(service: FilterService):
    docs = [
        make_doc(title="A", city="Montpellier", canonical_event_type_norm="concert"),
        make_doc(title="B", city="Paris", canonical_event_type_norm="concert"),
    ]
    result = service.filter_documents("Quels concerts à Montpellier ?", docs)
    assert len(result) == 1
    assert result[0].metadata["title"] == "A"


# -------------------------------------------------------------------
# Debug
# -------------------------------------------------------------------


def test_doc_to_debug_row(service: FilterService):
    doc = make_doc(
        title="Expo",
        city="Montpellier",
        canonical_event_type="Exposition",
        event_type="Exposition",
        music_genre="",
        first_date="2026-03-01",
        last_date="2026-03-10",
        duration_days=10,
        is_single_day=False,
        price_info="gratuit",
        is_free=True,
        keywords_title=[],
        derived_event_terms=["exposition"],
        derived_music_terms=[],
        audience_terms=["famille"],
        is_strong_cultural_candidate=True,
        is_weak_cultural_candidate=False,
        has_market_signal=False,
        has_repair_signal=False,
        has_business_signal=False,
        source_url="http://source.test",
    )
    row = service._doc_to_debug_row(doc)
    assert row["title"] == "Expo"
    assert row["city"] == "Montpellier"
    assert row["canonical_event_type"] == "Exposition"
    assert row["url"] == "http://source.test"


def test_doc_to_debug_row_uses_url_fallback(service: FilterService):
    doc = make_doc(title="Expo", url="http://url.test")
    row = service._doc_to_debug_row(doc)
    assert row["url"] == "http://url.test"


def test_filter_documents_with_debug(service: FilterService):
    docs = [
        make_doc(
            title="Concert Rock",
            city="Montpellier",
            canonical_event_type_norm="concert",
            music_genre_norm="rock",
            first_date="2025-09-20",
            last_date="2025-09-20",
            is_free=True,
            is_single_day=True,
            audience_terms=["famille"],
            is_strong_cultural_candidate=True,
            search_text="concert rock famille montpellier",
        )
    ]
    result = service.filter_documents_with_debug(
        question="Quels concerts de rock gratuits pour famille à Montpellier le 20 septembre 2025 ?",
        docs=docs,
    )
    assert "filters" in result
    assert result["n_input_docs"] == 1
    assert result["n_after_price"] == 1
    assert len(result["docs"]) == 1
    assert len(result["docs_debug"]) == 1
    assert result["docs_debug"][0]["title"] == "Concert Rock"