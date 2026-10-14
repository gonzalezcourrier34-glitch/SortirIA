"""Tests du pipeline de transformation CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/pipelines/test_transformation_pipeline.py` (commit 93632c5).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import src.pipelines.transformation_pipeline as module
from src.article.preparation.article_preparation_models import (
    ArticlePreparationReport
)
from src.pipelines.transformation_pipeline import (
    PREPARATION_POLICIES,
    TRANSFORMATION_VERSION,
    build_transformation_report,
    export_transformed_articles,
    get_export_columns,
    prepare_articles_by_role,
    rollback_exported_files,
    run_transformation_pipeline,
    validate_transformed_articles,
    write_csv_file,
    write_json_file,
    write_transformation_report
)


# Utilitaires

def make_preparation_report(
    *,
    received: int = 0,
    normalized: int = 0,
    normalization_ignored: int = 0,
    cleaned: int = 0,
    cleaning_ignored: int = 0,
    duplicates: int = 0,
    invalid: int = 0,
    kept: int = 0,
    rejection_reasons: dict[str, int] | None = None
) -> ArticlePreparationReport:
    """Construit un rapport de préparation pour les tests."""

    report = ArticlePreparationReport()
    report.received = received
    report.normalized = normalized
    report.normalization_ignored = normalization_ignored
    report.cleaned = cleaned
    report.cleaning_ignored = cleaning_ignored
    report.duplicates = duplicates
    report.invalid = invalid
    report.kept = kept
    report.rejection_reasons.update(rejection_reasons or {})

    return report


def make_preparation_result(
    articles: list[dict[str, Any]],
    report: ArticlePreparationReport | None = None
) -> SimpleNamespace:
    """Construit un résultat simulé de préparation."""

    return SimpleNamespace(
        articles=articles,
        report=report or make_preparation_report(
            received=len(articles),
            normalized=len(articles),
            cleaned=len(articles),
            kept=len(articles)
        )
    )


# Configuration

def test_transformation_configuration() -> None:
    assert TRANSFORMATION_VERSION == "1.0.0"
    assert PREPARATION_POLICIES == (
        "acquisition",
        "labeled_reference",
        "multimodal_reference"
    )


# Préparation par rôle

def test_prepare_articles_by_role_groups_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "a1",
            "dataset_role": "acquisition"
        },
        {
            "id": "l1",
            "dataset_role": "labeled_reference"
        },
        {
            "id": "m1",
            "dataset_role": "multimodal_reference"
        },
        {
            "id": "a2",
            "dataset_role": "unknown"
        },
        "invalid"
    ]

    calls: list[dict[str, Any]] = []

    def fake_prepare_articles(
        policy_articles: list[Any],
        *,
        policy_name: str,
        remove_duplicates: bool
    ) -> SimpleNamespace:
        calls.append({
            "articles": policy_articles,
            "policy_name": policy_name,
            "remove_duplicates": remove_duplicates
        })

        prepared_articles = [
            {
                "id": (
                    article.get("id", "invalid")
                    if isinstance(article, dict)
                    else "invalid"
                ),
                "policy": policy_name
            }
            for article in policy_articles
        ]

        report = make_preparation_report(
            received=len(policy_articles),
            normalized=len(policy_articles),
            normalization_ignored=1,
            cleaned=len(policy_articles),
            cleaning_ignored=2,
            duplicates=1,
            invalid=1,
            kept=len(prepared_articles),
            rejection_reasons={"raison_test": 1}
        )

        return make_preparation_result(
            prepared_articles,
            report
        )

    monkeypatch.setattr(
        module,
        "prepare_articles",
        fake_prepare_articles
    )

    prepared, report = prepare_articles_by_role(
        articles,
        remove_duplicates=False
    )

    assert prepared == [
        {
            "id": "a1",
            "policy": "acquisition"
        },
        {
            "id": "a2",
            "policy": "acquisition"
        },
        {
            "id": "invalid",
            "policy": "acquisition"
        },
        {
            "id": "l1",
            "policy": "labeled_reference"
        },
        {
            "id": "m1",
            "policy": "multimodal_reference"
        }
    ]

    assert calls == [
        {
            "articles": [
                {
                    "id": "a1",
                    "dataset_role": "acquisition"
                },
                {
                    "id": "a2",
                    "dataset_role": "unknown"
                },
                "invalid"
            ],
            "policy_name": "acquisition",
            "remove_duplicates": False
        },
        {
            "articles": [
                {
                    "id": "l1",
                    "dataset_role": "labeled_reference"
                }
            ],
            "policy_name": "labeled_reference",
            "remove_duplicates": False
        },
        {
            "articles": [
                {
                    "id": "m1",
                    "dataset_role": "multimodal_reference"
                }
            ],
            "policy_name": "multimodal_reference",
            "remove_duplicates": False
        }
    ]

    assert report.received == 5
    assert report.normalized == 5
    assert report.normalization_ignored == 3
    assert report.cleaned == 5
    assert report.cleaning_ignored == 6
    assert report.duplicates == 3
    assert report.invalid == 3
    assert report.kept == 5
    assert report.rejection_reasons == Counter({
        "raison_test": 3
    })


def test_prepare_articles_by_role_returns_empty_result() -> None:
    prepared, report = prepare_articles_by_role([])

    assert prepared == []
    assert report.received == 0
    assert report.kept == 0
    assert report.rejection_reasons == Counter()


def test_prepare_articles_by_role_normalizes_unknown_role(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    received_policies: list[str] = []

    def fake_prepare_articles(
        articles: list[Any],
        *,
        policy_name: str,
        remove_duplicates: bool
    ) -> SimpleNamespace:
        received_policies.append(policy_name)
        return make_preparation_result([
            {
                "id": "1",
                "dataset_role": policy_name
            }
        ])

    monkeypatch.setattr(
        module,
        "prepare_articles",
        fake_prepare_articles
    )

    prepared, _ = prepare_articles_by_role([
        {
            "id": "1",
            "dataset_role": "other"
        }
    ])

    assert prepared == [
        {
            "id": "1",
            "dataset_role": "acquisition"
        }
    ]
    assert received_policies == ["acquisition"]


# Validation finale

def test_validate_transformed_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [
        {
            "id": "valid"
        },
        {
            "id": "invalid"
        },
        {
            "id": "unknown"
        }
    ]

    received_policies: list[dict[str, Any]] = []

    def fake_validate(
        article: dict[str, Any],
        policy: dict[str, Any]
    ) -> tuple[bool, str]:
        received_policies.append(policy)

        if article["id"] == "valid":
            return True, ""

        if article["id"] == "invalid":
            return False, "titre_absent"

        return False, ""

    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        fake_validate
    )
    monkeypatch.setattr(
        module,
        "get_article_identifier",
        lambda article: article["id"]
    )

    valid_articles, rejection_stats = validate_transformed_articles(
        articles,
        require_image=True
    )

    assert valid_articles == [
        {
            "id": "valid",
            "data_quality_status": "valid",
            "rejection_reason": ""
        }
    ]
    assert rejection_stats == Counter({
        "titre_absent": 1,
        "raison_inconnue": 1
    })
    assert received_policies == [
        {
            "require_image": True
        },
        {
            "require_image": True
        },
        {
            "require_image": True
        }
    ]


def test_validate_transformed_articles_preserves_original(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    article = {
        "id": "1"
    }

    monkeypatch.setattr(
        module,
        "validate_article_with_reason",
        lambda article, policy: (True, "")
    )

    validated, _ = validate_transformed_articles([article])

    assert article == {
        "id": "1"
    }
    assert validated[0] is not article


def test_validate_transformed_articles_returns_empty_result() -> None:
    valid_articles, rejection_stats = validate_transformed_articles([])

    assert valid_articles == []
    assert rejection_stats == Counter()


# Colonnes d'export

def test_get_export_columns_places_standard_fields_first(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "id",
            "title",
            "text"
        )
    )

    articles = [
        {
            "id": "1",
            "title": "Titre",
            "custom_z": "z"
        },
        {
            "id": "2",
            "text": "Texte",
            "custom_a": "a"
        }
    ]

    columns = get_export_columns(articles)

    assert columns == [
        "id",
        "title",
        "text",
        "custom_a",
        "custom_z"
    ]


def test_get_export_columns_without_articles(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "id",
            "title"
        )
    )

    assert get_export_columns([]) == [
        "id",
        "title"
    ]


# Écriture JSON

def test_write_json_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "exports" / "articles.json"
    temporary_path = tmp_path / "articles.tmp"

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: temporary_path
    )

    write_json_file(
        [
            {
                "title": "Article français",
                "published_at": datetime(2026, 7, 27, 12, 0)
            }
        ],
        output_path
    )

    assert output_path.exists()
    assert not temporary_path.exists()

    content = json.loads(
        output_path.read_text(encoding="utf-8")
    )

    assert content == [
        {
            "title": "Article français",
            "published_at": "2026-07-27 12:00:00"
        }
    ]


def test_write_json_file_removes_temporary_file_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "articles.json"
    temporary_path = tmp_path / "articles.tmp"
    temporary_path.write_text("temporary", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: temporary_path
    )
    monkeypatch.setattr(
        module,
        "atomic_replace",
        lambda source, target: (
            _ for _ in ()
        ).throw(OSError("remplacement impossible"))
    )

    with pytest.raises(
        OSError,
        match="remplacement impossible"
    ):
        write_json_file(
            [{"id": "1"}],
            output_path
        )

    assert not temporary_path.exists()


# Écriture CSV

def test_write_csv_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "exports" / "articles.csv"
    temporary_path = tmp_path / "articles.tmp"

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: temporary_path
    )
    monkeypatch.setattr(
        module,
        "STANDARD_ARTICLE_FIELDS",
        (
            "id",
            "title"
        )
    )

    write_csv_file(
        [
            {
                "id": "1",
                "title": "Premier",
                "score": 0.9
            },
            {
                "id": "2",
                "title": "Second",
                "score": 0.8
            }
        ],
        output_path
    )

    assert output_path.exists()
    assert not temporary_path.exists()

    dataframe = pd.read_csv(
        output_path,
        encoding="utf-8-sig"
    )

    assert list(dataframe.columns) == [
        "id",
        "title",
        "score"
    ]
    assert dataframe["title"].tolist() == [
        "Premier",
        "Second"
    ]


def test_write_csv_file_removes_temporary_file_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "articles.csv"
    temporary_path = tmp_path / "articles.tmp"

    monkeypatch.setattr(
        module,
        "create_temporary_path",
        lambda path: temporary_path
    )
    monkeypatch.setattr(
        pd.DataFrame,
        "to_csv",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(OSError("écriture impossible"))
    )

    with pytest.raises(
        OSError,
        match="écriture impossible"
    ):
        write_csv_file(
            [{"id": "1"}],
            output_path
        )

    assert not temporary_path.exists()


# Rollback

def test_rollback_exported_files(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    removed_paths: list[Path] = []

    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: removed_paths.append(path)
    )

    exported_files = {
        "json": Path("/tmp/articles.json"),
        "csv": Path("/tmp/articles.csv")
    }

    rollback_exported_files(exported_files)

    assert removed_paths == [
        Path("/tmp/articles.json"),
        Path("/tmp/articles.csv")
    ]


def test_rollback_exported_files_continues_after_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    attempted_paths: list[Path] = []

    def fake_remove(path: Path) -> None:
        attempted_paths.append(path)

        if path.suffix == ".json":
            raise OSError("suppression impossible")

    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        fake_remove
    )

    rollback_exported_files({
        "json": Path("/tmp/articles.json"),
        "csv": Path("/tmp/articles.csv")
    })

    assert attempted_paths == [
        Path("/tmp/articles.json"),
        Path("/tmp/articles.csv")
    ]


def test_rollback_exported_files_ignores_empty_mapping(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "remove_file_if_exists",
        lambda path: calls.append(path)
    )

    rollback_exported_files({})

    assert calls == []


# Export transactionnel

def test_export_transformed_articles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TRANSFORMED_DATA_DIR",
        tmp_path
    )

    json_calls: list[tuple[Any, Path]] = []
    csv_calls: list[tuple[Any, Path]] = []

    monkeypatch.setattr(
        module,
        "write_json_file",
        lambda data, path: json_calls.append((data, path))
    )
    monkeypatch.setattr(
        module,
        "write_csv_file",
        lambda data, path: csv_calls.append((data, path))
    )

    articles = [{"id": "1"}]

    exported = export_transformed_articles(
        articles,
        "20260727_120000"
    )

    assert exported == {
        "json": (
            tmp_path
            / "articles_transformed_20260727_120000.json"
        ),
        "csv": (
            tmp_path
            / "articles_transformed_20260727_120000.csv"
        )
    }
    assert json_calls == [
        (
            articles,
            tmp_path / "articles_transformed_20260727_120000.json"
        )
    ]
    assert csv_calls == [
        (
            articles,
            tmp_path / "articles_transformed_20260727_120000.csv"
        )
    ]


def test_export_transformed_articles_respects_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TRANSFORMED_DATA_DIR",
        tmp_path
    )

    json_calls: list[Path] = []
    csv_calls: list[Path] = []

    monkeypatch.setattr(
        module,
        "write_json_file",
        lambda data, path: json_calls.append(path)
    )
    monkeypatch.setattr(
        module,
        "write_csv_file",
        lambda data, path: csv_calls.append(path)
    )

    exported = export_transformed_articles(
        [{"id": "1"}],
        "date",
        export_json=False,
        export_csv=True
    )

    assert exported == {
        "csv": tmp_path / "articles_transformed_date.csv"
    }
    assert json_calls == []
    assert csv_calls == [
        tmp_path / "articles_transformed_date.csv"
    ]


def test_export_transformed_articles_returns_empty_mapping() -> None:
    assert export_transformed_articles(
        [],
        "date"
    ) == {}


def test_export_transformed_articles_rolls_back_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TRANSFORMED_DATA_DIR",
        tmp_path
    )

    rolled_back: list[dict[str, Path]] = []

    monkeypatch.setattr(
        module,
        "write_json_file",
        lambda data, path: None
    )
    monkeypatch.setattr(
        module,
        "write_csv_file",
        lambda data, path: (
            _ for _ in ()
        ).throw(OSError("CSV impossible"))
    )
    monkeypatch.setattr(
        module,
        "rollback_exported_files",
        lambda files: rolled_back.append(dict(files))
    )

    with pytest.raises(
        OSError,
        match="CSV impossible"
    ):
        export_transformed_articles(
            [{"id": "1"}],
            "date"
        )

    assert rolled_back == [
        {
            "json": tmp_path / "articles_transformed_date.json"
        }
    ]


# Rapport

def test_write_transformation_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "TRANSFORMATION_REPORTS_DIR",
        tmp_path
    )

    calls: list[tuple[Any, Path]] = []

    monkeypatch.setattr(
        module,
        "write_json_file",
        lambda data, path: calls.append((data, path))
    )

    report = {
        "status": "success"
    }

    result = write_transformation_report(
        report,
        "20260727_120000"
    )

    expected_path = (
        tmp_path
        / "transformation_report_20260727_120000.json"
    )

    assert result == expected_path
    assert calls == [
        (
            report,
            expected_path
        )
    ]


def test_build_transformation_report(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    started_at = datetime(2026, 7, 27, 12, 0, 0)
    finished_at = started_at + timedelta(seconds=12.5)

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: finished_at
    )
    monkeypatch.setattr(
        module,
        "get_extraction_date",
        lambda: "2026-07-27T12:00:12.500000"
    )

    preparation_report = make_preparation_report(
        received=5,
        normalized=4,
        normalization_ignored=1,
        cleaned=3,
        cleaning_ignored=1,
        duplicates=1,
        invalid=1,
        kept=3,
        rejection_reasons={
            "titre_absent": 1
        }
    )

    report = build_transformation_report(
        status="success",
        started_at=started_at,
        transformation_version="2.0.0",
        source="rss",
        require_image=True,
        raw_articles=[1, 2, 3, 4, 5],
        prepared_articles=[1, 2, 3],
        transformed_articles=[1, 2, 3],
        enriched_articles=[1, 2, 3],
        valid_articles=[1, 2],
        preparation_report=preparation_report,
        rejection_stats={
            "image_absente": 1
        },
        exported_files={
            "json": Path("/tmp/articles.json")
        }
    )

    assert report == {
        "pipeline": "transformation",
        "status": "success",
        "transformation_version": "2.0.0",
        "started_at": "2026-07-27T12:00:00",
        "finished_at": "2026-07-27T12:00:12.500000",
        "duration_seconds": 12.5,
        "source_filter": "rss",
        "require_image": True,
        "raw_articles": 5,
        "normalized_articles": 4,
        "normalization_ignored": 1,
        "cleaned_articles": 3,
        "cleaning_ignored": 1,
        "duplicate_articles": 1,
        "prepared_articles": 3,
        "transformed_articles": 3,
        "enriched_articles": 3,
        "valid_articles": 2,
        "preparation_invalid_articles": 1,
        "final_invalid_articles": 1,
        "rejected_articles": 2,
        "rejection_reasons": {
            "titre_absent": 1,
            "image_absente": 1
        },
        "exported_files": {
            "json": str(Path("/tmp/articles.json"))
        },
        "error": ""
    }


def test_build_transformation_report_uses_defaults(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    started_at = datetime(2026, 7, 27, 12, 0, 0)

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: started_at
    )
    monkeypatch.setattr(
        module,
        "get_extraction_date",
        lambda: "2026-07-27T12:00:00"
    )

    report = build_transformation_report(
        status="failed",
        started_at=started_at,
        transformation_version="1.0.0",
        source=None,
        require_image=False
    )

    assert report["source_filter"] == "all"
    assert report["raw_articles"] == 0
    assert report["rejected_articles"] == 0
    assert report["rejection_reasons"] == {}
    assert report["exported_files"] == {}


# Pipeline principal

def test_run_transformation_pipeline_success(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    started_at = datetime(2026, 7, 27, 12, 0, 0)

    raw_articles = [{"id": "raw"}]
    prepared_articles = [{"id": "prepared"}]
    transformed_articles = [{"id": "transformed"}]
    enriched_articles = [{"id": "enriched"}]
    valid_articles = [{"id": "valid"}]

    preparation_report = make_preparation_report(
        received=1,
        normalized=1,
        cleaned=1,
        kept=1
    )

    calls: list[tuple[str, Any]] = []
    written_reports: list[dict[str, Any]] = []

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: started_at
    )
    monkeypatch.setattr(
        module,
        "get_extraction_date",
        lambda: "2026-07-27T12:00:00"
    )

    monkeypatch.setattr(
        module,
        "load_articles",
        lambda source=None: raw_articles
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_by_role",
        lambda articles, remove_duplicates=True: (
            prepared_articles,
            preparation_report
        )
    )
    monkeypatch.setattr(
        module,
        "transform_articles",
        lambda articles: transformed_articles
    )

    def fake_enrich_articles(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(("enrich", kwargs))
        return enriched_articles

    monkeypatch.setattr(
        module,
        "enrich_articles",
        fake_enrich_articles
    )
    monkeypatch.setattr(
        module,
        "validate_transformed_articles",
        lambda articles, require_image=False: (
            valid_articles,
            Counter()
        )
    )
    monkeypatch.setattr(
        module,
        "build_transformation_report",
        lambda **kwargs: {
            "status": kwargs["status"]
        }
    )

    def fake_write_report(
        report: dict[str, Any],
        execution_date: str
    ) -> Path:
        written_reports.append(report)
        return Path(f"/tmp/{execution_date}.json")

    monkeypatch.setattr(
        module,
        "write_transformation_report",
        fake_write_report
    )
    monkeypatch.setattr(
        module,
        "export_transformed_articles",
        lambda *args, **kwargs: {
            "json": Path("/tmp/articles.json")
        }
    )

    result = run_transformation_pipeline(
        source="rss",
        require_image=True,
        export_json=True,
        export_csv=False,
        transformation_version="2.0.0"
    )

    assert result is valid_articles
    assert written_reports == [
        {
            "status": "export_pending"
        },
        {
            "status": "success"
        }
    ]

    assert calls == [
        (
            "enrich",
            {
                "articles": transformed_articles,
                "transformation_date": started_at.isoformat(),
                "transformation_version": "2.0.0"
            }
        )
    ]


@pytest.mark.parametrize(
    ("stage", "expected_warning"),
    [
        (
            "load",
            "Pipeline arrêté : aucune donnée brute disponible."
        ),
        (
            "prepare",
            "Pipeline arrêté : aucun article après préparation."
        ),
        (
            "transform",
            "Pipeline arrêté : aucun article après transformation métier."
        ),
        (
            "enrich",
            "Pipeline arrêté : aucun article après enrichissement."
        ),
        (
            "validate",
            "Pipeline arrêté : aucun article transformé valide."
        )
    ]
)
def test_run_transformation_pipeline_stops_on_empty_stage(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    expected_warning: str
) -> None:
    articles = [{"id": "1"}]
    warnings: list[str] = []
    report = make_preparation_report(
        received=1,
        kept=1
    )

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: datetime(2026, 7, 27, 12, 0, 0)
    )

    monkeypatch.setattr(
        module,
        "load_articles",
        lambda source=None: (
            []
            if stage == "load"
            else articles
        )
    )
    monkeypatch.setattr(
        module,
        "prepare_articles_by_role",
        lambda received, remove_duplicates=True: (
            [],
            report
        )
        if stage == "prepare"
        else (
            articles,
            report
        )
    )
    monkeypatch.setattr(
        module,
        "transform_articles",
        lambda received: (
            []
            if stage == "transform"
            else articles
        )
    )
    monkeypatch.setattr(
        module,
        "enrich_articles",
        lambda **kwargs: (
            []
            if stage == "enrich"
            else articles
        )
    )
    monkeypatch.setattr(
        module,
        "validate_transformed_articles",
        lambda received, require_image=False: (
            [],
            Counter()
        )
        if stage == "validate"
        else (
            articles,
            Counter()
        )
    )
    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda message: warnings.append(message)
    )

    result = run_transformation_pipeline()

    assert result == []
    assert expected_warning in warnings


def test_run_transformation_pipeline_handles_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    started_at = datetime(2026, 7, 27, 12, 0, 0)
    failed_reports: list[dict[str, Any]] = []
    rollback_calls: list[dict[str, Path]] = []

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: started_at
    )
    monkeypatch.setattr(
        module,
        "get_extraction_date",
        lambda: "2026-07-27T12:00:00"
    )
    monkeypatch.setattr(
        module,
        "load_articles",
        lambda source=None: (
            _ for _ in ()
        ).throw(RuntimeError("chargement impossible"))
    )
    monkeypatch.setattr(
        module,
        "rollback_exported_files",
        lambda files: rollback_calls.append(dict(files))
    )

    def fake_build_report(**kwargs: Any) -> dict[str, Any]:
        report = {
            "status": kwargs["status"],
            "error": kwargs.get("error", "")
        }
        failed_reports.append(report)
        return report

    monkeypatch.setattr(
        module,
        "build_transformation_report",
        fake_build_report
    )
    monkeypatch.setattr(
        module,
        "write_transformation_report",
        lambda report, date: Path("/tmp/report.json")
    )

    result = run_transformation_pipeline()

    assert result == []
    assert rollback_calls == [{}]
    assert failed_reports == [
        {
            "status": "failed",
            "error": "RuntimeError: chargement impossible"
        }
    ]


def test_run_transformation_pipeline_handles_report_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    started_at = datetime(2026, 7, 27, 12, 0, 0)
    logged_exceptions: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: started_at
    )
    monkeypatch.setattr(
        module,
        "load_articles",
        lambda source=None: (
            _ for _ in ()
        ).throw(RuntimeError("pipeline impossible"))
    )
    monkeypatch.setattr(
        module,
        "build_transformation_report",
        lambda **kwargs: {
            "status": "failed"
        }
    )
    monkeypatch.setattr(
        module,
        "write_transformation_report",
        lambda report, date: (
            _ for _ in ()
        ).throw(OSError("rapport impossible"))
    )
    monkeypatch.setattr(
        module.logger,
        "exception",
        lambda *args: logged_exceptions.append(args)
    )

    result = run_transformation_pipeline()

    assert result == []
    assert len(logged_exceptions) == 2
    assert logged_exceptions[0][0] == (
        "Échec inattendu du pipeline de transformation : %s"
    )
    assert logged_exceptions[1][0] == (
        "Impossible d'écrire le rapport d'échec : %s"
    )


def test_run_transformation_pipeline_default_arguments(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    load_calls: list[Any] = []

    monkeypatch.setattr(
        module,
        "get_current_datetime",
        lambda: datetime(2026, 7, 27, 12, 0, 0)
    )

    def fake_load_articles(source: str | None = None) -> list[Any]:
        load_calls.append(source)
        return []

    monkeypatch.setattr(
        module,
        "load_articles",
        fake_load_articles
    )

    result = run_transformation_pipeline()

    assert result == []
    assert load_calls == [None]