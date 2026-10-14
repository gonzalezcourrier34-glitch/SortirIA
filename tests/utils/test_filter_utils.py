"""Tests des fonctions utilitaires liées aux filtres d'extraction.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/utils/test_filter_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from src.utils.filter_utils import (
    ACQUISITION_FILTER_SECTION,
    BOOLEAN_FILTER_FIELDS,
    DATASET_FILTER_SECTION,
    FALLBACK_FILTERS,
    get_default_filter_section,
    get_fallback_filters,
    get_filter_configuration,
    load_default_filters,
    normalize_allowed_labels,
    normalize_filter_configuration
)


# Sélection du profil

@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        [],
        "source",
        42,
        {"type": "rss"},
        {"type": "api"},
        {"type": "social"},
        {"type": "scraper"},
        {"role": "acquisition"},
        {"role": "social_reference"}
    ]
)
def test_get_default_filter_section_returns_acquisition_section(
    source: Any
) -> None:
    """Une source classique doit utiliser les filtres d'acquisition."""

    assert (
        get_default_filter_section(source)
        == ACQUISITION_FILTER_SECTION
    )


@pytest.mark.parametrize(
    "source",
    [
        {"type": "dataset"},
        {"type": " DATASET "},
        {"role": "dataset"},
        {"role": "labeled_reference"},
        {"role": "multimodal_reference"},
        {"role": "fact_check_reference"},
        {"role": "claim_reference"},
        {"dataset_role": "labeled_reference"},
        {"dataset_role": "multimodal_reference"},
        {
            "role": "",
            "dataset_role": "claim_reference"
        }
    ]
)
def test_get_default_filter_section_returns_dataset_section(
    source: Mapping[str, Any]
) -> None:
    """Une source de dataset doit utiliser les filtres de dataset."""

    assert (
        get_default_filter_section(source)
        == DATASET_FILTER_SECTION
    )


def test_get_default_filter_section_prioritizes_role_over_dataset_role() -> None:
    """Le rôle principal doit être utilisé lorsqu'il est renseigné."""

    source = {
        "role": "acquisition",
        "dataset_role": "labeled_reference"
    }

    assert (
        get_default_filter_section(source)
        == ACQUISITION_FILTER_SECTION
    )


# Filtres de repli

def test_get_fallback_filters_returns_acquisition_copy() -> None:
    """Les filtres d'acquisition doivent être retournés sous forme de copie."""

    result = get_fallback_filters({"type": "rss"})

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]
    assert result is not FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


def test_get_fallback_filters_returns_dataset_copy() -> None:
    """Les filtres de dataset doivent être retournés sous forme de copie."""

    result = get_fallback_filters({"type": "dataset"})

    assert result == FALLBACK_FILTERS[DATASET_FILTER_SECTION]
    assert result is not FALLBACK_FILTERS[DATASET_FILTER_SECTION]


def test_get_fallback_filters_does_not_modify_global_configuration() -> None:
    """La modification du résultat ne doit pas altérer les constantes."""

    result = get_fallback_filters({"type": "rss"})
    result["require_title"] = False

    assert (
        FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]["require_title"]
        is True
    )


# Chargement des filtres YAML

def test_load_default_filters_returns_acquisition_filters(
    tmp_path: Path
) -> None:
    """Les filtres d'acquisition doivent être chargés depuis le YAML."""

    file_path = tmp_path / "sources.yaml"
    file_path.write_text(
        """
acquisition_filters:
  require_title: false
  require_url: true
  min_title_length: 15

dataset_filters:
  require_label: true
""",
        encoding="utf-8"
    )

    result = load_default_filters(
        source={"type": "rss"},
        file_path=file_path
    )

    assert result == {
        "require_title": False,
        "require_url": True,
        "min_title_length": 15
    }


def test_load_default_filters_returns_dataset_filters(
    tmp_path: Path
) -> None:
    """Les filtres de dataset doivent être chargés depuis le YAML."""

    file_path = tmp_path / "sources.yaml"
    file_path.write_text(
        """
acquisition_filters:
  require_url: true

dataset_filters:
  require_label: true
  require_url: false
  allowed_labels:
    - fake
    - real
""",
        encoding="utf-8"
    )

    result = load_default_filters(
        source={"role": "labeled_reference"},
        file_path=file_path
    )

    assert result == {
        "require_label": True,
        "require_url": False,
        "allowed_labels": [
            "fake",
            "real"
        ]
    }


def test_load_default_filters_returns_empty_dict_for_missing_file(
    tmp_path: Path
) -> None:
    """Un fichier absent doit produire une configuration vide."""

    result = load_default_filters(
        source={"type": "rss"},
        file_path=tmp_path / "missing.yaml"
    )

    assert result == {}


def test_load_default_filters_returns_empty_dict_for_empty_file(
    tmp_path: Path
) -> None:
    """Un fichier YAML vide doit produire une configuration vide."""

    file_path = tmp_path / "sources.yaml"
    file_path.write_text("", encoding="utf-8")

    result = load_default_filters(
        source={"type": "rss"},
        file_path=file_path
    )

    assert result == {}


def test_load_default_filters_returns_empty_dict_for_missing_section(
    tmp_path: Path
) -> None:
    """Une section absente doit produire un dictionnaire vide."""

    file_path = tmp_path / "sources.yaml"
    file_path.write_text(
        """
other_section:
  enabled: true
""",
        encoding="utf-8"
    )

    result = load_default_filters(
        source={"type": "rss"},
        file_path=file_path
    )

    assert result == {}


@pytest.mark.parametrize(
    "section_value",
    [
        "invalid",
        42,
        True,
        [],
        ["require_title"]
    ]
)
def test_load_default_filters_rejects_invalid_section(
    tmp_path: Path,
    section_value: Any
) -> None:
    """Une section qui n'est pas un dictionnaire doit être rejetée."""

    file_path = tmp_path / "sources.yaml"

    if isinstance(section_value, str):
        yaml_value = f'"{section_value}"'
    elif section_value is True:
        yaml_value = "true"
    elif isinstance(section_value, list):
        yaml_value = "- require_title"
    else:
        yaml_value = str(section_value)

    file_path.write_text(
        f"acquisition_filters:\n  {yaml_value}\n",
        encoding="utf-8"
    )

    result = load_default_filters(
        source={"type": "rss"},
        file_path=file_path
    )

    assert result == {}


def test_load_default_filters_handles_loader_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une erreur du chargeur YAML doit être interceptée."""

    def raise_error(file_path: str | Path) -> dict[str, Any]:
        raise RuntimeError("Erreur simulée")

    monkeypatch.setattr(
        "src.utils.filter_utils.load_yaml_file",
        raise_error
    )

    result = load_default_filters(
        source={"type": "rss"},
        file_path="sources.yaml"
    )

    assert result == {}


def test_load_default_filters_rejects_non_mapping_configuration(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une configuration racine invalide doit être rejetée."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_yaml_file",
        lambda file_path: ["invalid"]
    )

    result = load_default_filters(
        source={"type": "rss"},
        file_path="sources.yaml"
    )

    assert result == {}


# Normalisation des labels autorisés

@pytest.mark.parametrize(
    "labels",
    [
        None,
        {},
        {"fake": True},
        object()
    ]
)
def test_normalize_allowed_labels_returns_empty_list_for_invalid_values(
    labels: Any
) -> None:
    """Une valeur non exploitable doit produire une liste vide."""

    assert normalize_allowed_labels(labels) == []


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ("fake", ["fake"]),
        (" FAKE ", ["fake"]),
        ("real", ["real"]),
        (1, ["fake"]),
        (0, ["real"]),
        (True, ["true"]),
        (False, ["false"]),
        ("unknown", ["unknown"]),
        ("", [])
    ]
)

def test_normalize_allowed_labels_accepts_scalar_values(
    labels: Any,
    expected: list[str]
) -> None:
    """Une valeur simple doit devenir une liste contenant un label."""

    assert normalize_allowed_labels(labels) == expected


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (
            ["fake", "real"],
            ["fake", "real"]
        ),
        (
            [" FAKE ", "fake", "REAL", "real"],
            ["fake", "real"]
        ),
        (
            ["fake", None, "", "real"],
            ["fake", "real"]
        ),
        (
            ("fake", "real", "fake"),
            ["fake", "real"]
        ),
        (
            iter(["fake", "real", "fake"]),
            ["fake", "real"]
        ),
        (
            [0, 1],
            ["real", "fake"]
        )
    ]
)
def test_normalize_allowed_labels_normalizes_iterables(
    labels: Any,
    expected: list[str]
) -> None:
    """Les labels d'un itérable doivent être normalisés et dédupliqués."""

    assert normalize_allowed_labels(labels) == expected


def test_normalize_allowed_labels_preserves_order() -> None:
    """L'ordre de première apparition des labels doit être conservé."""

    labels = [
        "real",
        "fake",
        "unknown",
        "real",
        "fake"
    ]

    assert normalize_allowed_labels(labels) == [
        "real",
        "fake",
        "unknown"
    ]


# Normalisation de la configuration

def test_normalize_filter_configuration_normalizes_all_fields() -> None:
    """Tous les champs connus doivent être normalisés."""

    filters = {
        "require_title": "false",
        "require_text": "true",
        "require_image": 1,
        "require_url": 0,
        "require_label": "oui",
        "remove_deleted_content": "non",
        "remove_duplicates": "yes",
        "validate_urls": "off",
        "min_title_length": "15",
        "min_text_length": "20.0",
        "min_total_text_length": 50,
        "allowed_labels": [
            "fake",
            "REAL",
            "fake"
        ],
        "custom_field": "preserved"
    }

    fallback_filters = FALLBACK_FILTERS[
        ACQUISITION_FILTER_SECTION
    ]

    result = normalize_filter_configuration(
        filters,
        fallback_filters
    )

    assert result == {
        "require_title": False,
        "require_text": True,
        "require_image": True,
        "require_url": False,
        "require_label": True,
        "remove_deleted_content": False,
        "remove_duplicates": True,
        "validate_urls": False,
        "min_title_length": 15,
        "min_text_length": 20,
        "min_total_text_length": 50,
        "allowed_labels": [
            "fake",
            "real"
        ],
        "custom_field": "preserved"
    }


def test_normalize_filter_configuration_uses_boolean_fallbacks() -> None:
    """Les booléens invalides doivent utiliser les valeurs de repli."""

    filters = {
        field: "invalid"
        for field in BOOLEAN_FILTER_FIELDS
    }

    fallback_filters = {
        field: index % 2 == 0
        for index, field in enumerate(BOOLEAN_FILTER_FIELDS)
    }

    result = normalize_filter_configuration(
        filters,
        fallback_filters
    )

    for field in BOOLEAN_FILTER_FIELDS:
        assert result[field] is fallback_filters[field]


def test_normalize_filter_configuration_uses_integer_defaults() -> None:
    """Les entiers invalides doivent utiliser les constantes par défaut."""

    result = normalize_filter_configuration(
        filters={
            "min_title_length": -1,
            "min_text_length": "invalid",
            "min_total_text_length": 1.5
        },
        fallback_filters={}
    )

    assert result["min_title_length"] >= 0
    assert result["min_text_length"] >= 0
    assert result["min_total_text_length"] >= 0


def test_normalize_filter_configuration_does_not_modify_input() -> None:
    """La configuration d'origine ne doit pas être modifiée."""

    filters = {
        "require_title": "false",
        "allowed_labels": ["fake"]
    }
    original_filters = {
        "require_title": "false",
        "allowed_labels": ["fake"]
    }

    normalize_filter_configuration(
        filters,
        FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]
    )

    assert filters == original_filters


# Configuration publique

def test_get_filter_configuration_returns_acquisition_fallbacks(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une source classique doit recevoir le profil d'acquisition."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    result = get_filter_configuration(
        source={"type": "rss"}
    )

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


def test_get_filter_configuration_returns_dataset_fallbacks(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un dataset doit recevoir le profil de dataset."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    result = get_filter_configuration(
        source={"type": "dataset"}
    )

    assert result == FALLBACK_FILTERS[DATASET_FILTER_SECTION]


def test_get_filter_configuration_respects_priority(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La priorité doit être source, argument, YAML puis repli."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {
            "require_title": False,
            "require_text": True,
            "min_title_length": 10,
            "min_text_length": 10
        }
    )

    default_filters = {
        "require_title": True,
        "min_title_length": 20,
        "require_image": True
    }

    source = {
        "type": "rss",
        "filters": {
            "require_title": False,
            "min_title_length": 30,
            "require_url": False
        }
    }

    result = get_filter_configuration(
        source=source,
        default_filters=default_filters
    )

    assert result["require_title"] is False
    assert result["require_text"] is True
    assert result["require_image"] is True
    assert result["require_url"] is False
    assert result["min_title_length"] == 30
    assert result["min_text_length"] == 10


def test_get_filter_configuration_normalizes_merged_values(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les valeurs fusionnées doivent être normalisées."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {
            "require_title": "false",
            "min_title_length": "25",
            "allowed_labels": [
                "FAKE",
                "real",
                "fake"
            ]
        }
    )

    result = get_filter_configuration(
        source={"type": "rss"}
    )

    assert result["require_title"] is False
    assert result["min_title_length"] == 25
    assert result["allowed_labels"] == [
        "fake",
        "real"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "invalid",
        [],
        42,
        True
    ]
)
def test_get_filter_configuration_accepts_invalid_source(
    monkeypatch: pytest.MonkeyPatch,
    source: Any
) -> None:
    """Une source invalide doit être remplacée par un dictionnaire vide."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    result = get_filter_configuration(source)

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


def test_get_filter_configuration_accepts_none_source(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une source absente doit utiliser les filtres d'acquisition."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    result = get_filter_configuration(None)

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


def test_get_filter_configuration_ignores_invalid_default_filters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Des filtres transmis invalides doivent être ignorés."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    result = get_filter_configuration(
        source={"type": "rss"},
        default_filters=["invalid"]
    )

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


@pytest.mark.parametrize(
    "source_filters",
    [
        "invalid",
        [],
        42,
        True
    ]
)
def test_get_filter_configuration_ignores_invalid_source_filters(
    monkeypatch: pytest.MonkeyPatch,
    source_filters: Any
) -> None:
    """Des filtres de source invalides doivent être ignorés."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    source = {
        "name": "Test source",
        "type": "rss",
        "filters": source_filters
    }

    result = get_filter_configuration(source)

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


def test_get_filter_configuration_accepts_none_source_filters(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Des filtres de source à None doivent être ignorés."""

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        lambda source, file_path: {}
    )

    source = {
        "type": "rss",
        "filters": None
    }

    result = get_filter_configuration(source)

    assert result == FALLBACK_FILTERS[ACQUISITION_FILTER_SECTION]


def test_get_filter_configuration_passes_defaults_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    """Le chemin YAML transmis doit être envoyé au chargeur."""

    defaults_file = tmp_path / "custom_sources.yaml"
    received: dict[str, Any] = {}

    def fake_load_default_filters(
        source: Mapping[str, Any] | None,
        file_path: str | Path
    ) -> dict[str, Any]:
        received["source"] = source
        received["file_path"] = file_path
        return {}

    monkeypatch.setattr(
        "src.utils.filter_utils.load_default_filters",
        fake_load_default_filters
    )

    source = {"type": "rss"}

    get_filter_configuration(
        source=source,
        defaults_file=defaults_file
    )

    assert received["source"] == source
    assert received["file_path"] == defaults_file