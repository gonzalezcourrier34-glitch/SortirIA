"""Tests unitaires de `app/lexical_service.py`.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `tests/test_lexical_service.py` (commit 18f7ce1).
"""

from __future__ import annotations

import pytest

from app.lexical_service import LexicalService


@pytest.fixture
def service() -> LexicalService:
    return LexicalService()


# -------------------------------------------------------------------
# Constantes / initialisation
# -------------------------------------------------------------------


def test_lexical_service_has_expected_constant_sets(service: LexicalService):
    assert isinstance(service.STOPWORDS, set)
    assert isinstance(service.EVENT_TYPE_TERMS, dict)
    assert isinstance(service.MUSIC_GENRE_TERMS, dict)
    assert isinstance(service.AUDIENCE_TERMS, dict)
    assert isinstance(service.FREE_MARKERS, list)
    assert isinstance(service.PAID_MARKERS, list)
    assert isinstance(service.CULTURAL_EVENT_TYPES, set)
    assert isinstance(service.MUSICAL_EVENT_TYPES, set)
    assert isinstance(service.STRONG_CULTURAL_TERMS, list)
    assert isinstance(service.MEDIUM_CULTURAL_TERMS, list)
    assert isinstance(service.WEAK_ACTIVITY_TERMS, list)
    assert isinstance(service.MARKET_TERMS, list)
    assert isinstance(service.REPAIR_TERMS, list)
    assert isinstance(service.RELIGIOUS_TERMS, list)
    assert isinstance(service.BUSINESS_TERMS, list)
    assert isinstance(service.KNOWN_CITY_TERMS, list)

    assert "concert" in service.EVENT_TYPE_TERMS
    assert "rock" in service.MUSIC_GENRE_TERMS
    assert "famille" in service.AUDIENCE_TERMS
    assert "exposition" in service.CULTURAL_EVENT_TYPES
    assert "concert" in service.MUSICAL_EVENT_TYPES


def test_init_builds_normalized_internal_structures(service: LexicalService):
    assert "concert" in service._event_type_terms_norm
    assert "rock" in service._music_genre_terms_norm
    assert "famille" in service._audience_terms_norm

    assert "entree libre" in service._free_markers_norm
    assert "reservation obligatoire" in service._paid_markers_norm
    assert "que faire" in service._weak_activity_terms_norm
    assert "repair cafe" in service._repair_terms_norm
    assert "paroisse" in service._religious_terms_norm
    assert "business" in service._business_terms_norm
    assert "montpellier" in service._known_city_terms_norm


# -------------------------------------------------------------------
# Helpers internes
# -------------------------------------------------------------------


def test_normalize_terms_deduplicates_and_sorts(service: LexicalService):
    result = service._normalize_terms(["Éxpo", "expo", " Expo ", "", "vernissage"])
    assert result == ["expo", "vernissage"]


def test_normalize_term_mapping_normalizes_values(service: LexicalService):
    mapping = {"cat": ["Éxpo", "expo", " Vernissage "]}
    result = service._normalize_term_mapping(mapping)

    assert "cat" in result
    assert result["cat"] == ["expo", "vernissage"]


# -------------------------------------------------------------------
# safe / clean / extract / normalize
# -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("abc", "abc"),
        (123, "123"),
    ],
)
def test_safe(service: LexicalService, value, expected):
    assert service.safe(value) == expected


def test_clean_text_removes_newlines_tabs_and_extra_spaces(service: LexicalService):
    text = " Bonjour \n\t le   monde \r test "
    assert service.clean_text(text) == "Bonjour le monde test"


def test_extract_text_returns_fr_priority(service: LexicalService):
    value = {"en": "Hello", "fr": "Bonjour"}
    assert service.extract_text(value) == "Bonjour"


def test_extract_text_returns_en_if_fr_missing(service: LexicalService):
    value = {"en": "Hello", "es": "Hola"}
    assert service.extract_text(value) == "Hello"


def test_extract_text_returns_first_non_empty_value(service: LexicalService):
    value = {"x": "", "y": "Salut", "z": "Hola"}
    assert service.extract_text(value) == "Salut"


def test_extract_text_returns_empty_for_empty_dict(service: LexicalService):
    assert service.extract_text({}) == ""


def test_extract_text_handles_non_dict_value(service: LexicalService):
    assert service.extract_text(" Bonjour \n test ") == "Bonjour test"


def test_normalize_text_lowercases_removes_accents_and_punctuation(service: LexicalService):
    text = "Événement : Théâtre, Café !"
    assert service.normalize_text(text) == "evenement theatre cafe"


def test_normalize_text_preserves_hyphen_as_token_separator_when_relevant(service: LexicalService):
    text = "pop-up market"
    assert service.normalize_text(text) == "pop-up market"


def test_tokenize_text_returns_min_len_3_tokens(service: LexicalService):
    text = "Le DJ set au JAM en 2026 !"
    assert service.tokenize_text(text) == ["set", "jam", "2026"]


def test_join_texts_concatenates_non_empty_values(service: LexicalService):
    result = service.join_texts("Bonjour", None, "le monde", "", "test")
    assert result == "Bonjour le monde test"


# -------------------------------------------------------------------
# Matching lexical
# -------------------------------------------------------------------


def test_contains_term_word(service: LexicalService):
    assert service.contains_term("Concert de rock à Montpellier", "rock") is True


def test_contains_term_expression(service: LexicalService):
    assert service.contains_term("Entrée libre ce soir", "entrée libre") is True


def test_contains_term_returns_false_for_empty_inputs(service: LexicalService):
    assert service.contains_term("", "rock") is False
    assert service.contains_term("concert", "") is False
    assert service.contains_term(None, None) is False


def test_contains_term_respects_word_boundaries(service: LexicalService):
    assert service.contains_term("brocante", "rock") is False


def test_contains_any_term_true(service: LexicalService):
    assert service.contains_any_term(
        "Concert jazz à Montpellier",
        ["rock", "jazz"],
    ) is True


def test_contains_any_term_false(service: LexicalService):
    assert service.contains_any_term(
        "Concert jazz à Montpellier",
        ["rock", "metal"],
    ) is False


def test_contains_any_term_false_for_empty_text_or_terms(service: LexicalService):
    assert service.contains_any_term("", ["rock"]) is False
    assert service.contains_any_term("concert", []) is False


def test_count_matching_terms_counts_distinct_matches(service: LexicalService):
    result = service.count_matching_terms(
        "Concert jazz et blues à Montpellier",
        ["jazz", "blues", "rock"],
    )
    assert result == 2


def test_count_matching_terms_zero_when_empty(service: LexicalService):
    assert service.count_matching_terms("", ["jazz"]) == 0
    assert service.count_matching_terms("concert", []) == 0


# -------------------------------------------------------------------
# Keywords
# -------------------------------------------------------------------


def test_extract_keywords_removes_stopwords_and_years(service: LexicalService):
    text = "Quels événements culturels ont lieu à Montpellier en 2026 pour la famille"
    result = service.extract_keywords(text)

    assert "montpellier" in result
    assert "famille" in result
    assert "2026" not in result
    assert "quels" not in result
    assert "culturels" not in result


def test_extract_keywords_without_stopwords_filter(service: LexicalService):
    result = service.extract_keywords("Quels concerts à Montpellier", remove_stopwords=False)
    assert "quels" in result
    assert "concerts" in result
    assert "montpellier" in result


def test_extract_title_keywords(service: LexicalService):
    result = service.extract_title_keywords("Exposition photo à Montpellier")
    assert "exposition" in result
    assert "photo" in result
    assert "montpellier" in result


# -------------------------------------------------------------------
# Détection type / genre / audience
# -------------------------------------------------------------------


def test_extract_event_type_from_text(service: LexicalService):
    assert service.extract_event_type("Je cherche une exposition à Montpellier") == "exposition"


def test_extract_event_type_returns_none_when_no_match(service: LexicalService):
    assert service.extract_event_type("Je cherche quelque chose à faire") is None


def test_extract_music_genre_from_text(service: LexicalService):
    assert service.extract_music_genre("Y a-t-il des concerts de rock ?") == "rock"


def test_extract_music_genre_returns_none_when_no_match(service: LexicalService):
    assert service.extract_music_genre("Je cherche un événement culturel") is None


def test_extract_audience_terms_detects_multiple_audiences(service: LexicalService):
    result = service.extract_audience_terms("Un atelier tout public pour enfants et famille")
    assert result == ["enfant", "famille"]


def test_extract_audience_terms_returns_empty(service: LexicalService):
    assert service.extract_audience_terms("Concert de rock") == []


# -------------------------------------------------------------------
# Price info
# -------------------------------------------------------------------


def test_extract_price_info_free(service: LexicalService):
    label, flag = service.extract_price_info("Entrée libre et gratuit")
    assert label == "gratuit"
    assert flag is True


def test_extract_price_info_paid(service: LexicalService):
    label, flag = service.extract_price_info("Tarif 10 euros, réservation obligatoire")
    assert label == "payant"
    assert flag is False


def test_extract_price_info_conflicting_markers_returns_unknown(service: LexicalService):
    label, flag = service.extract_price_info("Entrée libre mais tarifs sur place")
    assert label == "inconnu"
    assert flag is None


def test_extract_price_info_unknown_when_empty(service: LexicalService):
    label, flag = service.extract_price_info("")
    assert label == "inconnu"
    assert flag is None


# -------------------------------------------------------------------
# Signaux métier simples
# -------------------------------------------------------------------


def test_has_market_signal(service: LexicalService):
    assert service.has_market_signal("Braderie et pop-up market ce dimanche") is True


def test_has_repair_signal(service: LexicalService):
    assert service.has_repair_signal("Repair café et atelier de réparation") is True


def test_has_religious_signal(service: LexicalService):
    assert service.has_religious_signal("Messe à la paroisse Saint Jean") is True


def test_has_business_signal(service: LexicalService):
    assert service.has_business_signal("Salon business et networking entrepreneur") is True


# -------------------------------------------------------------------
# Requêtes culturelles / larges / prix
# -------------------------------------------------------------------


def test_is_cultural_query_true_for_event_type(service: LexicalService):
    assert service.is_cultural_query("Je cherche une exposition à Montpellier") is True


def test_is_cultural_query_true_for_cultural_expression(service: LexicalService):
    assert service.is_cultural_query("Quels événements culturels ont lieu à Montpellier ?") is True


def test_is_cultural_query_true_for_strong_term(service: LexicalService):
    assert service.is_cultural_query("Y a-t-il du théâtre à Montpellier ?") is True


def test_is_cultural_query_false_for_broad_activity_query(service: LexicalService):
    assert service.is_cultural_query("Que faire à Montpellier ce week-end ?") is False


def test_is_cultural_query_false_when_empty(service: LexicalService):
    assert service.is_cultural_query("") is False


def test_is_broad_activity_query_true(service: LexicalService):
    assert service.is_broad_activity_query("Que faire à Montpellier ce week-end ?") is True


def test_is_broad_activity_query_false(service: LexicalService):
    assert service.is_broad_activity_query("Je cherche une exposition photo") is False


def test_extract_explicit_price_filter_free(service: LexicalService):
    assert service.extract_explicit_price_filter("Quels événements gratuits ?") == "gratuit"


def test_extract_explicit_price_filter_paid(service: LexicalService):
    assert service.extract_explicit_price_filter("Quels concerts payants à Montpellier ?") == "payant"


def test_extract_explicit_price_filter_none(service: LexicalService):
    assert service.extract_explicit_price_filter("Quels concerts à Montpellier ?") is None


# -------------------------------------------------------------------
# Dérivation de termes métier
# -------------------------------------------------------------------


def test_derive_event_terms_exposition(service: LexicalService):
    result = service.derive_event_terms(
        title="Exposition photo",
        description="Une galerie photo d'artistes locaux",
        event_type="",
    )
    assert "exposition" in result
    assert "expo" in result
    assert "vernissage" in result
    assert "photo" in result
    assert "photographie" in result


def test_derive_event_terms_concert(service: LexicalService):
    result = service.derive_event_terms(
        title="Concert live",
        description="Un groupe sur scène avec plusieurs musiciens",
        event_type="",
    )
    assert "concert" in result
    assert "musique" in result


def test_derive_event_terms_projection(service: LexicalService):
    result = service.derive_event_terms(
        title="Projection spéciale",
        description="Projection d'un film documentaire au cinéma",
        event_type="",
    )
    assert "projection" in result
    assert "film" in result
    assert "cinema" in result


def test_derive_event_terms_other_categories(service: LexicalService):
    result = service.derive_event_terms(
        title="Festival de lecture et visite guidée",
        description="Atelier participatif, conte, marché, spectacle",
        event_type="conference",
    )

    assert "festival" in result
    assert "lecture" in result
    assert "visite" in result
    assert "conference" in result
    assert "atelier" not in result


def test_derive_event_terms_vinyl(service: LexicalService):
    result = service.derive_event_terms(
        title="Vinyl Pop-Up",
        description="Marché de vinyles à Montpellier",
        event_type="marche",
    )
    assert "vinyl" in result


def test_derive_music_terms_detects_canonical_and_variants(service: LexicalService):
    result = service.derive_music_terms(
        title="Concert rock garage",
        description="Une soirée punk et noise",
        event_type="concert",
    )
    assert "rock" in result
    assert "garage" in result
    assert "punk" in result
    assert "noise" in result


def test_derive_music_terms_empty_when_no_signal(service: LexicalService):
    result = service.derive_music_terms(
        title="Exposition photo",
        description="Galerie d'art",
        event_type="exposition",
    )
    assert result == []


# -------------------------------------------------------------------
# Inférence canonique
# -------------------------------------------------------------------


def test_infer_canonical_event_type_from_title(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="Exposition photo",
        description="",
        event_type="",
    ) == "exposition"


def test_infer_canonical_event_type_from_event_type_field(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="",
        description="",
        event_type="concert",
    ) == "concert"


def test_infer_canonical_event_type_from_description_exposition(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="",
        description="Une exposition d'art dans une galerie photo",
        event_type="",
    ) == "exposition"


def test_infer_canonical_event_type_from_description_concert(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="",
        description="Concert live avec groupe sur scène et musiciens",
        event_type="",
    ) == "concert"


def test_infer_canonical_event_type_from_description_projection(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="",
        description="Projection de film au cinéma",
        event_type="",
    ) == "projection"


def test_infer_canonical_event_type_from_description_conference(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="",
        description="Conférence et débat en table ronde",
        event_type="",
    ) == "conference"


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Atelier participatif", "atelier"),
        ("Soirée de conte pour enfants", "conte"),
        ("Grand festival d'été", "festival"),
        ("Braderie et marché du dimanche", "marche"),
        ("Spectacle au théâtre", "spectacle"),
        ("Lecture publique", "lecture"),
    ],
)
def test_infer_canonical_event_type_from_description_simple_cases(
    service: LexicalService,
    description: str,
    expected: str,
):
    assert service.infer_canonical_event_type(
        title="",
        description=description,
        event_type="",
    ) == expected


def test_infer_canonical_event_type_returns_empty_when_unknown(service: LexicalService):
    assert service.infer_canonical_event_type(
        title="Rencontre diverse",
        description="Moment convivial sans signal net",
        event_type="",
    ) == ""


def test_infer_canonical_music_genre_from_title(service: LexicalService):
    assert service.infer_canonical_music_genre(
        title="Concert rock garage",
        description="",
        event_type="concert",
    ) == "rock"


def test_infer_canonical_music_genre_from_description(service: LexicalService):
    assert service.infer_canonical_music_genre(
        title="Concert",
        description="Une soirée jazz et swing",
        event_type="concert",
    ) == "jazz"


def test_infer_canonical_music_genre_returns_empty(service: LexicalService):
    assert service.infer_canonical_music_genre(
        title="Exposition",
        description="Galerie de peinture",
        event_type="exposition",
    ) == ""


# -------------------------------------------------------------------
# Question signals
# -------------------------------------------------------------------


def test_extract_question_signals_full(service: LexicalService):
    result = service.extract_question_signals(
        "Quels concerts de rock gratuits pour enfants à Montpellier ?"
    )

    assert result["question_norm"] == (
        "quels concerts de rock gratuits pour enfants a montpellier"
    )
    assert result["event_type"] == "concert"
    assert result["music_genre"] == "rock"
    assert result["price_filter"] == "gratuit"
    assert result["audience_terms"] == ["enfant"]
    assert result["is_cultural_query"] is True
    assert result["is_broad_activity_query"] is False
    assert result["has_market_signal"] is False
    assert result["has_repair_signal"] is False
    assert result["has_religious_signal"] is False
    assert result["has_business_signal"] is False
    assert "rock" in result["keywords"]
    assert "montpellier" in result["keywords"]


def test_extract_question_signals_negative_business(service: LexicalService):
    result = service.extract_question_signals(
        "Y a-t-il un concours business à Paris ?"
    )

    assert result["event_type"] is None
    assert result["music_genre"] is None
    assert result["price_filter"] is None
    assert result["is_cultural_query"] is False
    assert result["has_business_signal"] is True


def test_extract_question_signals_market_and_repair_and_religious(service: LexicalService):
    result = service.extract_question_signals(
        "Braderie, repair café et messe à Montpellier ?"
    )

    assert result["has_market_signal"] is True
    assert result["has_repair_signal"] is True
    assert result["has_religious_signal"] is True


# -------------------------------------------------------------------
# Profil lexical document
# -------------------------------------------------------------------


def test_build_document_lexical_profile(service: LexicalService):
    result = service.build_document_lexical_profile(
        title="Exposition photo rock",
        description="Galerie d'art avec concert live",
        long_description="Un atelier famille avec entrée libre",
        event_type="exposition",
    )

    assert "keywords_title" in result
    assert "derived_event_terms" in result
    assert "derived_music_terms" in result
    assert "audience_terms" in result
    assert "canonical_event_type" in result
    assert "music_genre" in result
    assert "has_market_signal" in result
    assert "has_repair_signal" in result
    assert "has_religious_signal" in result
    assert "has_business_signal" in result

    assert "exposition" in result["keywords_title"]
    assert "photo" in result["keywords_title"]
    assert "exposition" in result["derived_event_terms"]