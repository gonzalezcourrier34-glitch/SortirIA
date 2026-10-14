"""Tests du service centralisé de stockage CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/storage/files/test_storage_service.py` (commit 93632c5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import src.storage.files.storage_service as module
from src.storage.files.storage_service import (
    StorageFormatResult,
    StorageServiceReport,
    filter_article_dictionaries,
    finalize_storage_report,
    save_articles,
    save_csv_articles,
    save_json_articles,
    save_with_storage
)


# Objets de test

@dataclass
class DummyStorageReport:
    success: bool
    errors: list[str]


class InvalidStorageReport:
    pass


# Dataclasses

def test_storage_format_result_defaults() -> None:
    result = StorageFormatResult(format="json")

    assert result.format == "json"
    assert result.requested is False
    assert result.success is False
    assert result.report is None
    assert result.errors == []


def test_storage_service_report_defaults() -> None:
    report = StorageServiceReport()

    assert report.status == "failed"
    assert report.success is False
    assert report.partial_success is False
    assert report.received == 0
    assert report.valid == 0
    assert report.ignored == 0
    assert report.json.format == "json"
    assert report.csv.format == "csv"
    assert report.errors == []


def test_storage_service_report_boolean_value() -> None:
    assert bool(StorageServiceReport(success=True)) is True
    assert bool(StorageServiceReport(success=False)) is False


@pytest.mark.parametrize(
    ("requested", "success", "expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False)
    ]
)
def test_json_success_property(
    requested: bool,
    success: bool,
    expected: bool
) -> None:
    report = StorageServiceReport(
        json=StorageFormatResult(
            format="json",
            requested=requested,
            success=success
        )
    )

    assert report.json_success is expected


@pytest.mark.parametrize(
    ("requested", "success", "expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False)
    ]
)
def test_csv_success_property(
    requested: bool,
    success: bool,
    expected: bool
) -> None:
    report = StorageServiceReport(
        csv=StorageFormatResult(
            format="csv",
            requested=requested,
            success=success
        )
    )

    assert report.csv_success is expected


# Filtrage

def test_filter_article_dictionaries_rejects_non_list(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result, ignored = filter_article_dictionaries("invalid")

    assert result == []
    assert ignored == 0
    assert warnings == [
        (
            "Collection invalide fournie au stockage : %s.",
            "str"
        )
    ]


def test_filter_article_dictionaries_keeps_mappings(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    source_mapping = {"id": "1", "source": "Reuters"}

    valid, ignored = filter_article_dictionaries([
        source_mapping,
        None,
        "invalid",
        {"id": "2"}
    ])

    assert valid == [
        {"id": "1", "source": "Reuters"},
        {"id": "2"}
    ]
    assert valid[0] is not source_mapping
    assert ignored == 2
    assert warnings == [
        (
            "%s élément(s) ignoré(s) avant le stockage.",
            2
        )
    ]


def test_filter_article_dictionaries_without_ignored_items(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    valid, ignored = filter_article_dictionaries([
        {"id": "1"},
        {"id": "2"}
    ])

    assert valid == [{"id": "1"}, {"id": "2"}]
    assert ignored == 0
    assert warnings == []


# Exécution indépendante

def test_save_with_storage_rejects_empty_articles() -> None:
    result = save_with_storage([], "json", lambda articles: None)

    assert result == StorageFormatResult(
        format="json",
        requested=True,
        errors=["Aucun article fourni pour la sauvegarde JSON."]
    )


def test_save_with_storage_rejects_non_callable_function() -> None:
    result = save_with_storage(
        [{"id": "1"}],
        "csv",
        None
    )

    assert result == StorageFormatResult(
        format="csv",
        requested=True,
        errors=["Fonction de stockage CSV invalide."]
    )


def test_save_with_storage_passes_article_copies(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    original_articles = [
        {"id": "1", "nested": {"value": 1}},
        {"id": "2"}
    ]
    received: list[list[dict[str, Any]]] = []

    def storage_function(
        articles: list[dict[str, Any]]
    ) -> DummyStorageReport:
        received.append(articles)
        articles[0]["id"] = "modified"
        return DummyStorageReport(success=True, errors=[])

    monkeypatch.setattr(module.logger, "info", lambda *args: None)

    result = save_with_storage(
        original_articles,
        "json",
        storage_function
    )

    assert result.success is True
    assert original_articles[0]["id"] == "1"
    assert received[0] is not original_articles
    assert received[0][0] is not original_articles[0]
    assert received[0][0]["nested"] is original_articles[0]["nested"]


def test_save_with_storage_handles_exception(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    exceptions: list[tuple[Any, ...]] = []

    monkeypatch.setattr(module.logger, "info", lambda *args: None)
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args: exceptions.append(args)
    )

    def failing_storage(
        articles: list[dict[str, Any]]
    ) -> DummyStorageReport:
        raise RuntimeError("Erreur disque")

    result = save_with_storage(
        [{"id": "1"}],
        "json",
        failing_storage
    )

    assert result.requested is True
    assert result.success is False
    assert result.report is None
    assert result.errors == ["RuntimeError: Erreur disque"]
    assert exceptions == [
        ("Erreur pendant la sauvegarde %s.", "JSON")
    ]


@pytest.mark.parametrize(
    "storage_report",
    [
        InvalidStorageReport(),
        object(),
        None,
        {"success": True}
    ]
)
def test_save_with_storage_rejects_report_without_success_attribute(
    monkeypatch: pytest.MonkeyPatch,
    storage_report: Any
) -> None:
    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(module.logger, "info", lambda *args: None)
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = save_with_storage(
        [{"id": "1"}],
        "csv",
        lambda articles: storage_report
    )

    assert result.success is False
    assert result.report is None
    assert result.errors == [
        (
            "Le stockage CSV a retourné un rapport invalide : "
            f"{type(storage_report).__name__}."
        )
    ]
    assert errors == [("%s", result.errors[0])]


@pytest.mark.parametrize(
    "invalid_success",
    [1, 0, "true", None, [], {}]
)
def test_save_with_storage_rejects_non_boolean_success(
    monkeypatch: pytest.MonkeyPatch,
    invalid_success: Any
) -> None:
    class Report:
        success = invalid_success
        errors: list[str] = []

    monkeypatch.setattr(module.logger, "info", lambda *args: None)
    monkeypatch.setattr(module.logger, "error", lambda *args: None)

    result = save_with_storage(
        [{"id": "1"}],
        "json",
        lambda articles: Report()
    )

    assert result.success is False
    assert result.report is None
    assert result.errors == [
        "Le stockage JSON a retourné un rapport invalide : Report."
    ]


def test_save_with_storage_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    infos: list[tuple[Any, ...]] = []
    storage_report = DummyStorageReport(
        success=True,
        errors=[]
    )

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: infos.append(args)
    )

    result = save_with_storage(
        [{"id": "1"}, {"id": "2"}],
        "json",
        lambda articles: storage_report
    )

    assert result.format == "json"
    assert result.requested is True
    assert result.success is True
    assert result.report is storage_report
    assert result.errors == []
    assert infos == [
        (
            "Début de la sauvegarde %s de %s article(s).",
            "JSON",
            2
        ),
        (
            "Sauvegarde %s terminée avec succès.",
            "JSON"
        )
    ]


def test_save_with_storage_failure_collects_clean_errors(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[Any, ...]] = []
    storage_report = DummyStorageReport(
        success=False,
        errors=[
            " Erreur disque ",
            "",
            "   ",
            42
        ]
    )

    monkeypatch.setattr(module.logger, "info", lambda *args: None)
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = save_with_storage(
        [{"id": "1"}],
        "csv",
        lambda articles: storage_report
    )

    assert result.success is False
    assert result.report is storage_report
    assert result.errors == ["Erreur disque", "42"]
    assert errors == [
        (
            "La sauvegarde %s a échoué.",
            "CSV"
        )
    ]


def test_save_with_storage_accepts_report_without_errors_attribute(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class Report:
        success = True

    monkeypatch.setattr(module.logger, "info", lambda *args: None)

    result = save_with_storage(
        [{"id": "1"}],
        "json",
        lambda articles: Report()
    )

    assert result.success is True
    assert result.errors == []


# Fonctions spécialisées

def test_save_json_articles_delegates(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = StorageFormatResult(
        format="json",
        requested=True,
        success=True
    )
    calls: list[tuple[Any, ...]] = []

    def fake_save_with_storage(
        articles: list[dict[str, Any]],
        storage_format: str,
        storage_function: Any
    ) -> StorageFormatResult:
        calls.append((articles, storage_format, storage_function))
        return expected

    monkeypatch.setattr(
        module,
        "save_with_storage",
        fake_save_with_storage
    )

    articles = [{"id": "1"}]

    result = save_json_articles(articles)

    assert result is expected
    assert calls == [
        (
            articles,
            "json",
            module.save_articles_to_json
        )
    ]


def test_save_csv_articles_delegates(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = StorageFormatResult(
        format="csv",
        requested=True,
        success=True
    )
    calls: list[tuple[Any, ...]] = []

    def fake_save_with_storage(
        articles: list[dict[str, Any]],
        storage_format: str,
        storage_function: Any
    ) -> StorageFormatResult:
        calls.append((articles, storage_format, storage_function))
        return expected

    monkeypatch.setattr(
        module,
        "save_with_storage",
        fake_save_with_storage
    )

    articles = [{"id": "1"}]

    result = save_csv_articles(articles)

    assert result is expected
    assert calls == [
        (
            articles,
            "csv",
            module.save_articles_to_csv
        )
    ]


# Rapport global

def test_finalize_storage_report_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    infos: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: infos.append(args)
    )

    report = StorageServiceReport(
        valid=5,
        ignored=1,
        json=StorageFormatResult(
            format="json",
            requested=True,
            success=True
        ),
        csv=StorageFormatResult(
            format="csv",
            requested=True,
            success=True
        )
    )

    result = finalize_storage_report(report)

    assert result is report
    assert report.status == "success"
    assert report.success is True
    assert report.partial_success is False
    assert report.errors == []
    assert infos == [
        (
            "Stockage terminé : statut=%s, JSON=%s, CSV=%s, "
            "valides=%s, ignorés=%s.",
            "success",
            "réussi",
            "réussi",
            5,
            1
        )
    ]


def test_finalize_storage_report_partial_success() -> None:
    report = StorageServiceReport(
        json=StorageFormatResult(
            format="json",
            requested=True,
            success=True
        ),
        csv=StorageFormatResult(
            format="csv",
            requested=True,
            success=False,
            errors=["Erreur CSV"]
        )
    )

    result = finalize_storage_report(report)

    assert result.status == "partial_success"
    assert result.success is False
    assert result.partial_success is True
    assert result.errors == ["CSV: Erreur CSV"]


def test_finalize_storage_report_failed() -> None:
    report = StorageServiceReport(
        json=StorageFormatResult(
            format="json",
            requested=True,
            success=False,
            errors=["Erreur JSON"]
        ),
        csv=StorageFormatResult(
            format="csv",
            requested=True,
            success=False,
            errors=["Erreur CSV 1", "Erreur CSV 2"]
        )
    )

    result = finalize_storage_report(report)

    assert result.status == "failed"
    assert result.success is False
    assert result.partial_success is False
    assert result.errors == [
        "JSON: Erreur JSON",
        "CSV: Erreur CSV 1",
        "CSV: Erreur CSV 2"
    ]


def test_finalize_storage_report_without_requested_formats() -> None:
    report = StorageServiceReport()

    result = finalize_storage_report(report)

    assert result.status == "failed"
    assert result.success is False
    assert result.partial_success is False
    assert result.errors == []


def test_finalize_storage_report_one_requested_success() -> None:
    report = StorageServiceReport(
        json=StorageFormatResult(
            format="json",
            requested=True,
            success=True
        )
    )

    result = finalize_storage_report(report)

    assert result.status == "success"
    assert result.success is True
    assert result.partial_success is False


def test_finalize_storage_report_logs_unrequested_formats(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    infos: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "info",
        lambda *args: infos.append(args)
    )

    report = StorageServiceReport(
        valid=2,
        ignored=0,
        json=StorageFormatResult(format="json"),
        csv=StorageFormatResult(
            format="csv",
            requested=True,
            success=False
        )
    )

    finalize_storage_report(report)

    assert infos[0][2] == "non demandé"
    assert infos[0][3] == "non réussi"


# Service principal

@pytest.mark.parametrize(
    "articles",
    [
        None,
        {},
        (),
        "article",
        42
    ]
)
def test_save_articles_rejects_non_list(articles: Any) -> None:
    report = save_articles(articles)

    assert report.status == "failed"
    assert report.success is False
    assert report.received == 0
    assert report.errors == [
        (
            "Collection invalide fournie au service : "
            f"{type(articles).__name__}."
        )
    ]


def test_save_articles_rejects_empty_list() -> None:
    report = save_articles([])

    assert report.received == 0
    assert report.errors == [
        "Aucun article fourni au service de stockage."
    ]


def test_save_articles_rejects_no_selected_format() -> None:
    report = save_articles(
        [{"id": "1"}],
        save_json=False,
        save_csv=False
    )

    assert report.received == 1
    assert report.valid == 0
    assert report.errors == [
        "Aucun format de stockage sélectionné."
    ]


def test_save_articles_rejects_no_valid_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "filter_article_dictionaries",
        lambda articles: ([], 3)
    )

    report = save_articles([None, "invalid", 42])

    assert report.received == 3
    assert report.valid == 0
    assert report.ignored == 3
    assert report.errors == [
        "Aucun article exploitable à sauvegarder."
    ]


def test_save_articles_json_only_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_articles = [{"id": "1"}]
    json_result = StorageFormatResult(
        format="json",
        requested=True,
        success=True
    )

    monkeypatch.setattr(
        module,
        "filter_article_dictionaries",
        lambda articles: (valid_articles, 1)
    )
    monkeypatch.setattr(
        module,
        "save_json_articles",
        lambda articles: json_result
    )

    report = save_articles(
        [{"id": "1"}, "invalid"],
        save_json=True,
        save_csv=False
    )

    assert report.received == 2
    assert report.valid == 1
    assert report.ignored == 1
    assert report.json is json_result
    assert report.csv.requested is False
    assert report.status == "success"
    assert report.success is True


def test_save_articles_csv_only_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_articles = [{"id": "1"}]
    csv_result = StorageFormatResult(
        format="csv",
        requested=True,
        success=False,
        errors=["Erreur disque"]
    )

    monkeypatch.setattr(
        module,
        "filter_article_dictionaries",
        lambda articles: (valid_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "save_csv_articles",
        lambda articles: csv_result
    )

    report = save_articles(
        [{"id": "1"}],
        save_json=False,
        save_csv=True
    )

    assert report.json.requested is False
    assert report.csv is csv_result
    assert report.status == "failed"
    assert report.success is False
    assert report.errors == ["CSV: Erreur disque"]


def test_save_articles_both_formats_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_articles = [{"id": "1"}, {"id": "2"}]
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    monkeypatch.setattr(
        module,
        "filter_article_dictionaries",
        lambda articles: (valid_articles, 0)
    )

    def fake_json(
        articles: list[dict[str, Any]]
    ) -> StorageFormatResult:
        calls.append(("json", articles))
        return StorageFormatResult(
            format="json",
            requested=True,
            success=True
        )

    def fake_csv(
        articles: list[dict[str, Any]]
    ) -> StorageFormatResult:
        calls.append(("csv", articles))
        return StorageFormatResult(
            format="csv",
            requested=True,
            success=True
        )

    monkeypatch.setattr(module, "save_json_articles", fake_json)
    monkeypatch.setattr(module, "save_csv_articles", fake_csv)

    report = save_articles(valid_articles)

    assert calls == [
        ("json", valid_articles),
        ("csv", valid_articles)
    ]
    assert report.status == "success"
    assert report.success is True
    assert report.partial_success is False


def test_save_articles_partial_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_articles = [{"id": "1"}]

    monkeypatch.setattr(
        module,
        "filter_article_dictionaries",
        lambda articles: (valid_articles, 0)
    )
    monkeypatch.setattr(
        module,
        "save_json_articles",
        lambda articles: StorageFormatResult(
            format="json",
            requested=True,
            success=True
        )
    )
    monkeypatch.setattr(
        module,
        "save_csv_articles",
        lambda articles: StorageFormatResult(
            format="csv",
            requested=True,
            success=False,
            errors=["Écriture impossible"]
        )
    )

    report = save_articles(valid_articles)

    assert report.status == "partial_success"
    assert report.success is False
    assert report.partial_success is True
    assert report.json_success is True
    assert report.csv_success is False
    assert report.errors == ["CSV: Écriture impossible"]


def test_save_articles_calls_finalize_storage_report(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = StorageServiceReport(status="success", success=True)
    captured: list[StorageServiceReport] = []

    monkeypatch.setattr(
        module,
        "filter_article_dictionaries",
        lambda articles: ([{"id": "1"}], 0)
    )
    monkeypatch.setattr(
        module,
        "save_json_articles",
        lambda articles: StorageFormatResult(
            format="json",
            requested=True,
            success=True
        )
    )
    monkeypatch.setattr(
        module,
        "finalize_storage_report",
        lambda report: captured.append(report) or marker
    )

    result = save_articles(
        [{"id": "1"}],
        save_json=True,
        save_csv=False
    )

    assert result is marker
    assert len(captured) == 1
    assert captured[0].received == 1
    assert captured[0].valid == 1