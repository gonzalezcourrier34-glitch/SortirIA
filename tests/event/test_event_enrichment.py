"""Tests de l'enrichissement des événements par Mistral (sans appel réel)."""

from __future__ import annotations

import json

import pytest

from src.event import event_enrichment as enrichment


@pytest.mark.parametrize(("is_free", "low", "high", "band"), [
    (True, None, None, "gratuit"), (None, 0, 0, "gratuit"), (None, 8, 12, "moins_10"),
    (None, 15, None, "10_25"), (None, None, 40, "plus_25"), (None, None, None, "inconnu"),
])
def test_price_band_needs_no_llm(is_free, low, high, band) -> None:
    assert enrichment.price_band(is_free, low, high) == band


def test_answers_are_validated() -> None:
    answer = json.dumps({"evenements": [
        {"id": "a", "categorie": "Concert", "publics": ["soiree"], "resume": "Trois groupes  rock.", "est_une_sortie": True},
        {"id": "b", "categorie": "karaoké", "publics": ["famille"]},         # catégorie hors liste : « autre »
        {"id": "c", "categorie": "Théâtre", "publics": ["Soirée", "enfants", "touristes"]},  # accents, synonymes
        {"id": "d", "categorie": "autre", "publics": [], "est_une_sortie": False},
        {"id": "zzz", "categorie": "autre"},                                 # identifiant inconnu
        {"id": "e", "publics": ["famille"]},                                 # catégorie absente : refusé
    ]})
    valid, rejected = enrichment.parse_enrichments(f"```json\n{answer}\n```", ["a", "b", "c", "d", "e"])
    assert sorted(valid) == ["a", "b", "c", "d"] and rejected == 1
    # Une catégorie hors liste ne fait plus perdre les publics et le résumé (15/10/2026).
    assert (valid["b"].categorie, valid["b"].publics) == ("autre", ["famille"])
    assert (valid["c"].categorie, valid["c"].publics) == ("theatre", ["famille", "soiree"])
    assert (valid["a"].categorie, valid["a"].resume) == ("concert", "Trois groupes rock.")
    assert valid["d"].est_une_sortie is False
    assert enrichment.parse_enrichments("pas du JSON", ["a"]) == ({}, 1)


def test_batch_text_and_prompt() -> None:
    text = enrichment.build_batch_text([{"event_id": "e1", "title": "Atelier jeux", "category": "Atelier",
                                         "venue_name": "Ludothèque", "city": "Sète", "age_min": 4, "age_max": 12,
                                         "description": "x" * 2000}])
    assert "id: e1" in text and "Âges : 4-12" in text and len(text) < 1000


def test_long_identifiers_are_replaced_by_short_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"event_id": "https://data.datatourisme.fr/10/abc-123", "title": "Marché"},
            {"event_id": "openagenda:9969608:42", "title": "Concert"}]
    sent: dict[str, str] = {}

    def fake_call(prompt, text, model):
        sent["text"] = text
        return json.dumps({"evenements": [{"id": "e1", "categorie": "Marché"}, {"id": "e2", "categorie": "concert"}]}), \
            {"input": 10, "output": 5}

    monkeypatch.setattr(enrichment, "call_mistral_json", fake_call)
    valid, _ = enrichment.enrich_with_mistral(rows)
    assert "datatourisme" not in sent["text"] and "id: e2" in sent["text"]
    assert {key: value.categorie for key, value in valid.items()} == {
        "https://data.datatourisme.fr/10/abc-123": "marche", "openagenda:9969608:42": "concert"}
    assert all(category in enrichment.ENRICHMENT_PROMPT for category in enrichment.CATEGORIES)


# --- Contrôles après Mistral (15/10/2026) ---------------------------------

@pytest.mark.parametrize(("title", "expected"), [
    ("PAUSE JEUX : Les échecs", "jeux"),                  # relevé par le juge : rangé en atelier
    ("VISITE GUIDÉE : SETH - EN TRANSIT", "visite"),     # relevé par le juge : rangé en exposition
    ("ATELIER INITIATION THÉÂTRE ADOS", "atelier"),
    ("CINÉ-ADOS", "cinema"),
    ("MARCHÉ TRADITIONNEL DE FRONTIGNAN", "marche"),
    ("Marche nordique", None),                            # une marche à pied n'est pas un marché
    ("Soirée Blue Note", None),                           # rien d'évident : Mistral décide
])
def test_category_from_title(title: str, expected) -> None:
    assert enrichment.category_from_title(title) == expected


def test_summary_with_an_invented_name_is_dropped() -> None:
    row = {"title": "FOOTBALL : MHSC VS METZ", "description": "Match de Ligue 1 au stade de la Mosson.",
           "city": "Montpellier", "venue_name": "Stade de la Mosson"}
    invented = enrichment.Enrichment(id="e1", categorie="sport", resume="Le MHSC reçoit Nantes à la Mosson.")
    checked, names = enrichment.check_enrichment(invented, row)
    assert names == ["Nantes"] and checked.resume == "" and checked.categorie == "sport"
    faithful = enrichment.Enrichment(id="e1", categorie="sport", resume="Le MHSC reçoit Metz à Montpellier.")
    assert enrichment.check_enrichment(faithful, row) == (faithful, [])
    games = enrichment.Enrichment(id="e1", categorie="atelier", resume="Partie d'échecs.")
    assert enrichment.check_enrichment(games, {"title": "PAUSE JEUX : Les échecs"})[0].categorie == "jeux"


@pytest.mark.parametrize("summary, row", [
    # Collecte du 15/10/2026 : résumés écartés à tort.
    ("Visite d'un hôtel particulier du XIXe siècle.", {"title": "L'HÔTEL DE LUNAS", "description": "Visite guidée."}),
    ("Soirée électro avec deux DJs.", {"title": "Hard Night", "description": "Techno toute la nuit."}),
    ("Un DJ set au bord de l'eau.", {"title": "Dub Striker", "description": "Soirée dub."}),
    ("Jeux en famille, de 4 à 12 ans.", {"title": "Atelier jeux en famille", "description": "Jeux de société.",
                                          "age_min": 4, "age_max": 12}),
    ("Entrée à 12 euros.", {"title": "Concert", "description": "Concert.", "price_min": 12.0}),
])
def test_common_words_centuries_ages_and_prices_are_not_invented_names(summary: str, row: dict) -> None:
    result = enrichment.Enrichment(id="e1", categorie="visite", resume=summary)
    assert enrichment.unsupported_names(summary, row) == []
    assert enrichment.check_enrichment(result, row)[0].resume == summary


def test_real_invented_names_are_still_caught() -> None:
    row = {"title": "CYRANA", "description": "Spectacle de théâtre."}
    assert enrichment.unsupported_names("Une relecture de Cyrano de Bergerac.", row) == ["Cyrano", "Bergerac"]
    row = {"title": "GREASE IS THE WORD", "description": "Comédie musicale."}
    assert enrichment.unsupported_names("Le spectacle culte de 1978 revient.", row) == ["1978"]
