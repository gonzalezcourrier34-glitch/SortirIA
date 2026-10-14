"""Tests du gestionnaire commun des configurations d'extracteurs.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/core/test_extractor_configuration.py` (commit 93632c5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import src.extractors.core.extractor_configuration as module
from config.source_config import SourceConfigurationError


# Objets de test

@dataclass
class DataclassConfiguration:
    """Configuration de test exposée sous forme de dataclass."""

    source_id: str
    type: str
    language: str = "fr"


class ToDictConfiguration:
    """Configuration de test exposant to_dict()."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def to_dict(self) -> Any:
        """Retourne la valeur configurée."""

        return self.value


class ModelDumpConfiguration:
    """Configuration de test exposant model_dump()."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def model_dump(self) -> Any:
        """Retourne la valeur configurée."""

        return self.value


class InvalidConfiguration:
    """Objet ne représentant pas une configuration exploitable."""


class FakeSourceRegistry:
    """Registre minimal utilisé pour isoler les tests du vrai registre."""

    def __init__(self, sources: list[Any]) -> None:
        self.sources = sources


# Conversion en dictionnaire

def test_configuration_to_dict_copies_mapping() -> None:
    source = {
        "source_id": "newsapi",
        "type": "api"
    }

    result = module.configuration_to_dict(source)

    assert result == source
    assert result is not source


def test_configuration_to_dict_uses_to_dict() -> None:
    source = ToDictConfiguration({
        "source_id": "gnews",
        "type": "api"
    })

    assert module.configuration_to_dict(source) == {
        "source_id": "gnews",
        "type": "api"
    }


def test_configuration_to_dict_rejects_invalid_to_dict_result() -> None:
    source = ToDictConfiguration([
        "invalid"
    ])

    assert module.configuration_to_dict(source) is None


def test_configuration_to_dict_uses_model_dump() -> None:
    source = ModelDumpConfiguration({
        "source_id": "guardian_api",
        "type": "api"
    })

    assert module.configuration_to_dict(source) == {
        "source_id": "guardian_api",
        "type": "api"
    }


def test_configuration_to_dict_rejects_invalid_model_dump_result() -> None:
    source = ModelDumpConfiguration("invalid")

    assert module.configuration_to_dict(source) is None


def test_configuration_to_dict_uses_dataclass() -> None:
    source = DataclassConfiguration(
        source_id="currents",
        type="api"
    )

    assert module.configuration_to_dict(source) == {
        "source_id": "currents",
        "type": "api",
        "language": "fr"
    }


def test_configuration_to_dict_rejects_dataclass_type() -> None:
    assert module.configuration_to_dict(
        DataclassConfiguration
    ) is None


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        42,
        [],
        InvalidConfiguration()
    ]
)
def test_configuration_to_dict_returns_none_for_unsupported_value(
    source: Any
) -> None:
    assert module.configuration_to_dict(source) is None


def test_configuration_to_dict_prioritizes_to_dict_over_model_dump() -> None:
    class MultipleFormats:
        def to_dict(self) -> dict[str, Any]:
            return {
                "format": "to_dict"
            }

        def model_dump(self) -> dict[str, Any]:
            return {
                "format": "model_dump"
            }

    assert module.configuration_to_dict(
        MultipleFormats()
    ) == {
        "format": "to_dict"
    }


# Normalisation

def test_normalize_source_configuration_returns_independent_dict() -> None:
    source = {
        "source_id": "newsdata",
        "type": "api"
    }

    result = module.normalize_source_configuration(
        source,
        "NewsData.io"
    )

    assert result == source
    assert result is not source


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        ToDictConfiguration({}),
        ModelDumpConfiguration({}),
        InvalidConfiguration()
    ]
)
def test_normalize_source_configuration_rejects_empty_or_invalid_values(
    source: Any
) -> None:
    assert module.normalize_source_configuration(
        source,
        "extracteur_test"
    ) is None


def test_normalize_source_configuration_logs_invalid_value(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: captured.append(args)
    )

    result = module.normalize_source_configuration(
        InvalidConfiguration(),
        "extracteur_test"
    )

    assert result is None
    assert captured
    assert captured[0][1] == "extracteur_test"
    assert captured[0][2] == "InvalidConfiguration"


# Détection d'une configuration

@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            {
                "source_id": "newsapi"
            },
            True
        ),
        (
            {},
            True
        ),
        (
            ToDictConfiguration({
                "source_id": "gnews"
            }),
            True
        ),
        (
            ModelDumpConfiguration({
                "source_id": "guardian_api"
            }),
            True
        ),
        (
            DataclassConfiguration(
                source_id="currents",
                type="api"
            ),
            True
        ),
        (
            InvalidConfiguration(),
            False
        ),
        (
            None,
            False
        ),
        (
            [],
            False
        )
    ]
)
def test_is_configuration_item(
    source: Any,
    expected: bool
) -> None:
    assert module.is_configuration_item(source) is expected


# Métadonnées

def test_build_source_metadata_extracts_all_supported_fields() -> None:
    source = {
        "source_id": "newsapi",
        "type": "api",
        "language": "fr",
        "country": "fr",
        "category": "technology",
        "role": "acquisition",
        "max_articles": 100
    }

    assert module.build_source_metadata(source) == {
        "source_id": "newsapi",
        "source_type": "api",
        "language": "fr",
        "country": "fr",
        "category": "technology",
        "role": "acquisition",
        "max_articles": 100
    }


def test_build_source_metadata_uses_id_fallback() -> None:
    source = {
        "id": "guardian_api",
        "source_type": "api"
    }

    assert module.build_source_metadata(source) == {
        "source_id": "guardian_api",
        "source_type": "api"
    }


def test_build_source_metadata_prefers_type() -> None:
    source = {
        "source_id": "newsdata",
        "type": "api",
        "source_type": "legacy"
    }

    assert module.build_source_metadata(
        source,
        source_type="fallback"
    ) == {
        "source_id": "newsdata",
        "source_type": "api"
    }


def test_build_source_metadata_uses_source_type_field_fallback() -> None:
    source = {
        "source_id": "reddit",
        "source_type": "social"
    }

    assert module.build_source_metadata(
        source,
        source_type="fallback"
    ) == {
        "source_id": "reddit",
        "source_type": "social"
    }


def test_build_source_metadata_uses_argument_fallback() -> None:
    source = {
        "source_id": "rss"
    }

    assert module.build_source_metadata(
        source,
        source_type="rss"
    ) == {
        "source_id": "rss",
        "source_type": "rss"
    }


def test_build_source_metadata_removes_none_values() -> None:
    source = {
        "source_id": "gnews",
        "type": "api",
        "language": None,
        "country": None,
        "category": None,
        "role": None,
        "max_articles": None
    }

    assert module.build_source_metadata(source) == {
        "source_id": "gnews",
        "source_type": "api"
    }


def test_build_source_metadata_keeps_falsey_non_none_values() -> None:
    source = {
        "source_id": "",
        "type": "api",
        "max_articles": 0
    }

    assert module.build_source_metadata(source) == {
        "source_type": "api",
        "max_articles": 0
    }


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        InvalidConfiguration()
    ]
)
def test_build_source_metadata_rejects_invalid_source(
    source: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="source doit être une configuration non vide"
    ):
        module.build_source_metadata(source)


# Résultat d'échec

def test_build_configuration_failure_delegates_to_failed_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    captured: dict[str, Any] = {}

    def fake_build_failed_result(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        module,
        "build_failed_result",
        fake_build_failed_result
    )

    configuration, result = module.build_configuration_failure(
        extractor_name="NewsAPI",
        source_type="api",
        message="Configuration invalide"
    )

    assert configuration is None
    assert result is expected
    assert captured == {
        "name": "NewsAPI",
        "source_type": "api",
        "message": "Configuration invalide"
    }


# Validation du chargement

def test_validate_loaded_configuration_accepts_mapping() -> None:
    source = {
        "source_id": "newsapi",
        "type": "api"
    }

    assert module.validate_loaded_configuration(source) is source


def test_validate_loaded_configuration_accepts_to_dict_object() -> None:
    source = ToDictConfiguration({
        "source_id": "gnews"
    })

    assert module.validate_loaded_configuration(source) is source


def test_validate_loaded_configuration_accepts_model_dump_object() -> None:
    source = ModelDumpConfiguration({
        "source_id": "guardian_api"
    })

    assert module.validate_loaded_configuration(source) is source


def test_validate_loaded_configuration_accepts_dataclass() -> None:
    source = DataclassConfiguration(
        source_id="currents",
        type="api"
    )

    assert module.validate_loaded_configuration(source) is source


def test_validate_loaded_configuration_accepts_sequence() -> None:
    sources = [
        {
            "source_id": "newsapi"
        },
        ToDictConfiguration({
            "source_id": "gnews"
        }),
        DataclassConfiguration(
            source_id="currents",
            type="api"
        )
    ]

    assert module.validate_loaded_configuration(sources) is sources


def test_validate_loaded_configuration_accepts_tuple() -> None:
    sources = (
        {
            "source_id": "newsapi"
        },
        {
            "source_id": "gnews"
        }
    )

    assert module.validate_loaded_configuration(sources) is sources


def test_validate_loaded_configuration_accepts_source_registry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "SourceRegistry",
        FakeSourceRegistry
    )

    registry = FakeSourceRegistry([
        {
            "source_id": "newsapi"
        }
    ])

    assert module.validate_loaded_configuration(registry) is registry


def test_validate_loaded_configuration_rejects_none() -> None:
    with pytest.raises(
        ValueError,
        match="configuration vide"
    ):
        module.validate_loaded_configuration(None)


def test_validate_loaded_configuration_rejects_empty_registry(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "SourceRegistry",
        FakeSourceRegistry
    )

    with pytest.raises(
        ValueError,
        match="registre de sources vide"
    ):
        module.validate_loaded_configuration(
            FakeSourceRegistry([])
        )


@pytest.mark.parametrize(
    "configuration",
    [
        [],
        ()
    ]
)
def test_validate_loaded_configuration_rejects_empty_sequence(
    configuration: Any
) -> None:
    with pytest.raises(
        ValueError,
        match="collection de sources vide"
    ):
        module.validate_loaded_configuration(configuration)


def test_validate_loaded_configuration_lists_invalid_types() -> None:
    configuration = [
        {
            "source_id": "newsapi"
        },
        42,
        "invalid",
        None
    ]

    with pytest.raises(
        TypeError,
        match=(
            "La collection contient des configurations invalides : "
            "NoneType, int, str"
        )
    ):
        module.validate_loaded_configuration(configuration)


@pytest.mark.parametrize(
    "configuration",
    [
        "newsapi",
        b"newsapi",
        42,
        3.14,
        object()
    ]
)
def test_validate_loaded_configuration_rejects_unexpected_type(
    configuration: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="type de configuration inattendu"
    ):
        module.validate_loaded_configuration(configuration)


# Chargement de configuration

def test_load_extractor_configuration_returns_valid_configuration() -> None:
    configuration = {
        "source_id": "newsapi",
        "type": "api"
    }

    loaded, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=lambda: configuration
    )

    assert loaded is configuration
    assert failure is None


def test_load_extractor_configuration_validates_sequence() -> None:
    configuration = [
        {
            "source_id": "newsapi"
        },
        {
            "source_id": "gnews"
        }
    ]

    loaded, failure = module.load_extractor_configuration(
        extractor_name="APIs",
        source_type="api",
        configuration_loader=lambda: configuration
    )

    assert loaded is configuration
    assert failure is None


def test_load_extractor_configuration_rejects_non_callable_loader(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    captured: dict[str, Any] = {}

    def fake_failure(
        extractor_name: str,
        source_type: str,
        message: str
    ) -> tuple[None, Any]:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "message": message
        })
        return None, expected

    monkeypatch.setattr(
        module,
        "build_configuration_failure",
        fake_failure
    )

    configuration, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=None
    )

    assert configuration is None
    assert failure is expected
    assert captured["extractor_name"] == "NewsAPI"
    assert captured["source_type"] == "api"
    assert (
        "Le chargeur de configuration doit être appelable"
        in captured["message"]
    )


def test_load_extractor_configuration_handles_source_configuration_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    captured: dict[str, Any] = {}

    def fake_failure(
        extractor_name: str,
        source_type: str,
        message: str
    ) -> tuple[None, Any]:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "message": message
        })
        return None, expected

    def loader() -> Any:
        raise SourceConfigurationError([
            "Source obligatoire absente"
        ])

    monkeypatch.setattr(
        module,
        "build_configuration_failure",
        fake_failure
    )

    configuration, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=loader
    )

    assert configuration is None
    assert failure is expected
    assert captured["extractor_name"] == "NewsAPI"
    assert captured["source_type"] == "api"
    assert captured["message"].startswith(
        "Configuration des sources invalide :"
    )
    assert "Source obligatoire absente" in captured["message"]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            TypeError("Type invalide"),
            "Chargement de la configuration impossible : Type invalide"
        ),
        (
            ValueError("Valeur invalide"),
            "Chargement de la configuration impossible : Valeur invalide"
        )
    ]
)
def test_load_extractor_configuration_handles_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str
) -> None:
    expected = object()
    captured: dict[str, Any] = {}

    def loader() -> Any:
        raise error

    def fake_failure(
        extractor_name: str,
        source_type: str,
        failure_message: str
    ) -> tuple[None, Any]:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "message": failure_message
        })
        return None, expected

    monkeypatch.setattr(
        module,
        "build_configuration_failure",
        fake_failure
    )

    configuration, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=loader
    )

    assert configuration is None
    assert failure is expected
    assert captured == {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "message": message
    }


def test_load_extractor_configuration_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    captured: dict[str, Any] = {}

    def loader() -> Any:
        raise RuntimeError(
            "Erreur inattendue"
        )

    def fake_failure(
        extractor_name: str,
        source_type: str,
        message: str
    ) -> tuple[None, Any]:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "message": message
        })
        return None, expected

    monkeypatch.setattr(
        module,
        "build_configuration_failure",
        fake_failure
    )

    configuration, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=loader
    )

    assert configuration is None
    assert failure is expected
    assert captured == {
        "extractor_name": "NewsAPI",
        "source_type": "api",
        "message": (
            "Chargement de la configuration impossible : "
            "Erreur inattendue"
        )
    }


def test_load_extractor_configuration_handles_invalid_loader_result(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = object()
    captured: dict[str, Any] = {}

    def fake_failure(
        extractor_name: str,
        source_type: str,
        message: str
    ) -> tuple[None, Any]:
        captured.update({
            "extractor_name": extractor_name,
            "source_type": source_type,
            "message": message
        })
        return None, expected

    monkeypatch.setattr(
        module,
        "build_configuration_failure",
        fake_failure
    )

    configuration, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=lambda: []
    )

    assert configuration is None
    assert failure is expected
    assert captured["message"] == (
        "Chargement de la configuration impossible : "
        "Le chargeur a retourné une collection de sources vide."
    )


def test_load_extractor_configuration_calls_loader_once() -> None:
    calls = 0

    def loader() -> dict[str, Any]:
        nonlocal calls
        calls += 1

        return {
            "source_id": "newsapi"
        }

    configuration, failure = module.load_extractor_configuration(
        extractor_name="NewsAPI",
        source_type="api",
        configuration_loader=loader
    )

    assert configuration == {
        "source_id": "newsapi"
    }
    assert failure is None
    assert calls == 1