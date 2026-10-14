"""Tests des utilitaires de fichiers pour les datasets CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/extractors/datasets/test_dataset_file_utils.py` (commit 93632c5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import src.extractors.datasets.dataset_file_utils as module


# Doubles de test

class FakeDataFrame:
    """DataFrame minimal pour tester la normalisation des colonnes."""

    def __init__(
        self,
        columns: list[Any]
    ) -> None:
        self.columns = columns
        self.copy_called = False

    def copy(self) -> FakeDataFrame:
        copied = FakeDataFrame(list(self.columns))
        self.copy_called = True
        return copied


class FakeAdapter:
    """Adaptateur minimal utilisé pour tester la découverte."""

    def __init__(
        self,
        discovered_files: Any,
        supported_extensions: frozenset[str] | None = None
    ) -> None:
        self.discovered_files = discovered_files
        self.supported_extensions = (
            supported_extensions
            or frozenset({".csv", ".json"})
        )
        self.received_directory: Path | None = None
        self.received_source: Any = None

    def find_files(
        self,
        dataset_directory: Path,
        source: Any
    ) -> Any:
        self.received_directory = dataset_directory
        self.received_source = source
        return self.discovered_files


# normalize_dataframe_columns

def test_normalize_dataframe_columns_returns_copy() -> None:
    dataframe = FakeDataFrame(
        [
            "Title",
            " Text ",
            42
        ]
    )

    result = module.normalize_dataframe_columns(dataframe)

    assert result is not dataframe
    assert dataframe.copy_called is True


def test_normalize_dataframe_columns_normalizes_column_names() -> None:
    dataframe = FakeDataFrame(
        [
            "Title",
            " Text ",
            "IMAGE_URL",
            42,
            None
        ]
    )

    result = module.normalize_dataframe_columns(dataframe)

    assert result.columns == [
        "title",
        "text",
        "image_url",
        "42",
        "none"
    ]


def test_normalize_dataframe_columns_does_not_modify_original() -> None:
    dataframe = FakeDataFrame(
        [
            "Title",
            " TEXT "
        ]
    )
    original_columns = list(dataframe.columns)

    result = module.normalize_dataframe_columns(dataframe)

    assert dataframe.columns == original_columns
    assert result.columns == [
        "title",
        "text"
    ]


def test_normalize_dataframe_columns_accepts_empty_columns() -> None:
    dataframe = FakeDataFrame([])

    result = module.normalize_dataframe_columns(dataframe)

    assert result.columns == []


@pytest.mark.parametrize(
    "invalid_dataframe",
    [
        None,
        {},
        [],
        (),
        "dataframe",
        42,
        object()
    ]
)
def test_normalize_dataframe_columns_rejects_invalid_values(
    invalid_dataframe: Any
) -> None:
    with pytest.raises(
        TypeError,
        match="Un DataFrame valide est attendu"
    ):
        module.normalize_dataframe_columns(invalid_dataframe)


def test_normalize_dataframe_columns_rejects_object_without_columns() -> None:
    class CopyOnly:
        def copy(self) -> CopyOnly:
            return self

    with pytest.raises(
        TypeError,
        match="Un DataFrame valide est attendu"
    ):
        module.normalize_dataframe_columns(CopyOnly())


def test_normalize_dataframe_columns_rejects_object_without_copy() -> None:
    class ColumnsOnly:
        columns = [
            "Title"
        ]

    with pytest.raises(
        TypeError,
        match="Un DataFrame valide est attendu"
    ):
        module.normalize_dataframe_columns(ColumnsOnly())


# resolve_dataset_directory

def test_resolve_dataset_directory_returns_absolute_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: str(value)
    )

    result = module.resolve_dataset_directory(
        {
            "path": str(tmp_path)
        },
        "Fakeddit"
    )

    assert result == tmp_path.resolve()


def test_resolve_dataset_directory_resolves_relative_path_from_base_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_directory = tmp_path / "datasets" / "fakeddit"
    dataset_directory.mkdir(parents=True)

    monkeypatch.setattr(
        module,
        "BASE_DIR",
        tmp_path
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    result = module.resolve_dataset_directory(
        {
            "path": "datasets/fakeddit"
        },
        "Fakeddit"
    )

    assert result == dataset_directory.resolve()


def test_resolve_dataset_directory_expands_user_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    home_directory = tmp_path / "home"
    dataset_directory = home_directory / "dataset"
    dataset_directory.mkdir(parents=True)

    monkeypatch.setattr(
        Path,
        "expanduser",
        lambda path: Path(
            str(path).replace(
                "~",
                str(home_directory),
                1
            )
        )
    )
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    result = module.resolve_dataset_directory(
        {
            "path": "~/dataset"
        },
        "Dataset"
    )

    assert result == dataset_directory.resolve()


@pytest.mark.parametrize(
    "raw_path",
    [
        None,
        "",
        "   "
    ]
)
def test_resolve_dataset_directory_rejects_missing_path(
    raw_path: Any,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
    )

    with pytest.raises(
        ValueError,
        match="Aucun chemin configuré pour le dataset Fakeddit"
    ):
        module.resolve_dataset_directory(
            {
                "path": raw_path
            },
            "Fakeddit"
        )


def test_resolve_dataset_directory_rejects_missing_path_key(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
    )

    with pytest.raises(
        ValueError,
        match="Aucun chemin configuré pour le dataset ISOT"
    ):
        module.resolve_dataset_directory(
            {},
            "ISOT"
        )


def test_resolve_dataset_directory_rejects_missing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_directory = tmp_path / "missing"

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    with pytest.raises(
        FileNotFoundError,
        match="Dossier du dataset Fakeddit introuvable"
    ):
        module.resolve_dataset_directory(
            {
                "path": str(missing_directory)
            },
            "Fakeddit"
        )


def test_resolve_dataset_directory_rejects_file_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset.csv"
    file_path.write_text(
        "title,text",
        encoding="utf-8"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    with pytest.raises(
        NotADirectoryError,
        match="n'est pas un dossier"
    ):
        module.resolve_dataset_directory(
            {
                "path": str(file_path)
            },
            "Dataset"
        )


@pytest.mark.parametrize(
    "exception",
    [
        TypeError("type invalide"),
        ValueError("valeur invalide"),
        OSError("erreur système"),
        RuntimeError("erreur interne")
    ]
)
def test_resolve_dataset_directory_wraps_resolution_errors(
    exception: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: "dataset"
    )

    def fake_expanduser(path: Path) -> Path:
        raise exception

    monkeypatch.setattr(
        Path,
        "expanduser",
        fake_expanduser
    )

    with pytest.raises(
        ValueError,
        match="Impossible de résoudre le chemin du dataset Dataset"
    ) as error_info:
        module.resolve_dataset_directory(
            {
                "path": "dataset"
            },
            "Dataset"
        )

    assert error_info.value.__cause__ is exception


# is_path_inside_directory

def test_is_path_inside_directory_returns_true_for_direct_child(
    tmp_path: Path
) -> None:
    file_path = tmp_path / "article.csv"

    assert module.is_path_inside_directory(
        file_path,
        tmp_path
    ) is True


def test_is_path_inside_directory_returns_true_for_nested_child(
    tmp_path: Path
) -> None:
    file_path = tmp_path / "nested" / "article.csv"

    assert module.is_path_inside_directory(
        file_path,
        tmp_path
    ) is True


def test_is_path_inside_directory_returns_true_for_directory_itself(
    tmp_path: Path
) -> None:
    assert module.is_path_inside_directory(
        tmp_path,
        tmp_path
    ) is True


def test_is_path_inside_directory_returns_false_for_external_path(
    tmp_path: Path
) -> None:
    dataset_directory = tmp_path / "dataset"
    external_file = tmp_path / "outside.csv"

    assert module.is_path_inside_directory(
        external_file,
        dataset_directory
    ) is False


def test_is_path_inside_directory_handles_parent_traversal(
    tmp_path: Path
) -> None:
    dataset_directory = tmp_path / "dataset"
    file_path = dataset_directory / ".." / "outside.csv"

    assert module.is_path_inside_directory(
        file_path,
        dataset_directory
    ) is False


@pytest.mark.parametrize(
    "exception",
    [
        ValueError("erreur"),
        OSError("erreur"),
        RuntimeError("erreur")
    ]
)
def test_is_path_inside_directory_returns_false_on_resolution_error(
    exception: Exception,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_resolve(
        path: Path,
        strict: bool = False
    ) -> Path:
        raise exception

    monkeypatch.setattr(
        Path,
        "resolve",
        fake_resolve
    )

    assert module.is_path_inside_directory(
        Path("article.csv"),
        Path("dataset")
    ) is False


# resolve_configured_dataset_file

def test_resolve_configured_dataset_file_returns_valid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset.csv"
    file_path.write_text(
        "title,text",
        encoding="utf-8"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "dataset.csv",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == file_path.resolve()


def test_resolve_configured_dataset_file_accepts_uppercase_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset.CSV"
    file_path.write_text(
        "title,text",
        encoding="utf-8"
    )

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "dataset.CSV",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == file_path.resolve()


@pytest.mark.parametrize(
    "configured_filename",
    [
        None,
        "",
        "   "
    ]
)
def test_resolve_configured_dataset_file_returns_none_without_filename(
    configured_filename: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: ""
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        configured_filename,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None


def test_resolve_configured_dataset_file_rejects_path_outside_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_directory = tmp_path / "dataset"
    dataset_directory.mkdir()

    external_file = tmp_path / "outside.csv"
    external_file.write_text(
        "title,text",
        encoding="utf-8"
    )

    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = module.resolve_configured_dataset_file(
        dataset_directory,
        "../outside.csv",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None
    assert len(errors) == 1
    assert errors[0][0] == (
        "Le fichier configuré sort du dossier de %s : %s"
    )
    assert errors[0][1:] == (
        "Dataset",
        "../outside.csv"
    )


def test_resolve_configured_dataset_file_rejects_unsupported_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset.txt"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )
    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "dataset.txt",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None
    assert errors[0][0] == (
        "Extension non prise en charge pour %s : %s"
    )
    assert errors[0][1:] == (
        "Dataset",
        ".txt"
    )


def test_resolve_configured_dataset_file_reports_missing_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )
    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "dataset",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None
    assert errors[0][2] == "sans extension"


def test_resolve_configured_dataset_file_rejects_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "missing.csv",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None
    assert errors[0][0] == (
        "Fichier configuré introuvable pour %s : %s"
    )
    assert errors[0][1] == "Dataset"


def test_resolve_configured_dataset_file_rejects_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "dataset.csv"
    directory.mkdir()
    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "dataset.csv",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None
    assert errors[0][0] == (
        "Fichier configuré introuvable pour %s : %s"
    )


@pytest.mark.parametrize(
    "exception",
    [
        TypeError("type invalide"),
        ValueError("valeur invalide"),
        OSError("erreur système"),
        RuntimeError("erreur interne")
    ]
)
def test_resolve_configured_dataset_file_handles_resolution_errors(
    exception: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module,
        "normalize_value",
        lambda value: value
    )
    monkeypatch.setattr(
        module.logger,
        "error",
        lambda *args: errors.append(args)
    )

    def fake_resolve(
        path: Path,
        strict: bool = False
    ) -> Path:
        raise exception

    monkeypatch.setattr(
        Path,
        "resolve",
        fake_resolve
    )

    result = module.resolve_configured_dataset_file(
        tmp_path,
        "dataset.csv",
        frozenset({".csv"}),
        "Dataset"
    )

    assert result is None
    assert errors[0][0] == (
        "Impossible de résoudre le fichier configuré pour %s : %s"
    )
    assert errors[0][1:] == (
        "Dataset",
        exception
    )


# validate_dataset_files

def test_validate_dataset_files_returns_valid_files(
    tmp_path: Path
) -> None:
    first_file = tmp_path / "first.csv"
    second_file = tmp_path / "second.json"

    first_file.write_text(
        "title,text",
        encoding="utf-8"
    )
    second_file.write_text(
        "{}",
        encoding="utf-8"
    )

    result = module.validate_dataset_files(
        [
            first_file,
            second_file
        ],
        tmp_path,
        frozenset({".csv", ".json"}),
        "Dataset"
    )

    assert result == [
        first_file.resolve(),
        second_file.resolve()
    ]


def test_validate_dataset_files_preserves_input_order(
    tmp_path: Path
) -> None:
    first_file = tmp_path / "first.csv"
    second_file = tmp_path / "second.csv"

    first_file.write_text(
        "first",
        encoding="utf-8"
    )
    second_file.write_text(
        "second",
        encoding="utf-8"
    )

    result = module.validate_dataset_files(
        [
            second_file,
            first_file
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == [
        second_file.resolve(),
        first_file.resolve()
    ]


@pytest.mark.parametrize(
    "invalid_path",
    [
        None,
        "dataset.csv",
        42,
        [],
        {}
    ]
)
def test_validate_dataset_files_ignores_non_path_values(
    invalid_path: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_dataset_files(
        [
            invalid_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][0] == (
        "Chemin invalide ignoré pour %s : %s."
    )
    assert warnings[0][1:] == (
        "Dataset",
        type(invalid_path).__name__
    )


@pytest.mark.parametrize(
    "exception",
    [
        OSError("erreur système"),
        RuntimeError("erreur interne")
    ]
)
def test_validate_dataset_files_ignores_resolution_errors(
    exception: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[Any, ...]] = []
    file_path = tmp_path / "dataset.csv"

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    original_resolve = Path.resolve

    def fake_resolve(
        path: Path,
        strict: bool = False
    ) -> Path:
        if path == file_path:
            raise exception

        return original_resolve(
            path,
            strict=strict
        )

    monkeypatch.setattr(
        Path,
        "resolve",
        fake_resolve
    )

    result = module.validate_dataset_files(
        [
            file_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][0] == (
        "Chemin impossible à résoudre pour %s : %s (%s)"
    )
    assert warnings[0][1:] == (
        "Dataset",
        file_path,
        exception
    )


def test_validate_dataset_files_ignores_external_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_directory = tmp_path / "dataset"
    dataset_directory.mkdir()

    external_file = tmp_path / "outside.csv"
    external_file.write_text(
        "contenu",
        encoding="utf-8"
    )
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_dataset_files(
        [
            external_file
        ],
        dataset_directory,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][0] == (
        "Fichier situé hors du dossier de %s : %s"
    )


def test_validate_dataset_files_ignores_missing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_file = tmp_path / "missing.csv"
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_dataset_files(
        [
            missing_file
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][0] == (
        "Fichier absent ou invalide pour %s : %s"
    )


def test_validate_dataset_files_ignores_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "dataset.csv"
    directory.mkdir()
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_dataset_files(
        [
            directory
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][0] == (
        "Fichier absent ou invalide pour %s : %s"
    )


def test_validate_dataset_files_ignores_unsupported_extensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset.txt"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_dataset_files(
        [
            file_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][0] == (
        "Extension non prise en charge pour %s : %s"
    )
    assert warnings[0][1:] == (
        "Dataset",
        ".txt"
    )


def test_validate_dataset_files_reports_missing_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )
    warnings: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "warning",
        lambda *args: warnings.append(args)
    )

    result = module.validate_dataset_files(
        [
            file_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []
    assert warnings[0][2] == "sans extension"


def test_validate_dataset_files_accepts_uppercase_extension(
    tmp_path: Path
) -> None:
    file_path = tmp_path / "dataset.CSV"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )

    result = module.validate_dataset_files(
        [
            file_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == [
        file_path.resolve()
    ]


def test_validate_dataset_files_removes_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "dataset.csv"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )
    debug_messages: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda *args: debug_messages.append(args)
    )

    result = module.validate_dataset_files(
        [
            file_path,
            file_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == [
        file_path.resolve()
    ]
    assert debug_messages[0][0] == (
        "Fichier déjà sélectionné pour %s : %s"
    )


def test_validate_dataset_files_detects_equivalent_resolved_paths(
    tmp_path: Path
) -> None:
    file_path = tmp_path / "dataset.csv"
    equivalent_path = tmp_path / "." / "dataset.csv"

    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )

    result = module.validate_dataset_files(
        [
            file_path,
            equivalent_path
        ],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == [
        file_path.resolve()
    ]


def test_validate_dataset_files_accepts_generator(
    tmp_path: Path
) -> None:
    first_file = tmp_path / "first.csv"
    second_file = tmp_path / "second.csv"

    first_file.write_text(
        "first",
        encoding="utf-8"
    )
    second_file.write_text(
        "second",
        encoding="utf-8"
    )

    files = (
        file_path
        for file_path in [
            first_file,
            second_file
        ]
    )

    result = module.validate_dataset_files(
        files,
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == [
        first_file.resolve(),
        second_file.resolve()
    ]


def test_validate_dataset_files_returns_empty_list_for_empty_iterable(
    tmp_path: Path
) -> None:
    result = module.validate_dataset_files(
        [],
        tmp_path,
        frozenset({".csv"}),
        "Dataset"
    )

    assert result == []


# get_dataset_files

def test_get_dataset_files_returns_validated_files(
    tmp_path: Path
) -> None:
    file_path = tmp_path / "dataset.csv"
    file_path.write_text(
        "contenu",
        encoding="utf-8"
    )

    adapter = FakeAdapter(
        [
            file_path
        ],
        frozenset({".csv"})
    )
    source = {
        "path": str(tmp_path)
    }

    result = module.get_dataset_files(
        adapter,
        tmp_path,
        source,
        "Dataset"
    )

    assert result == [
        file_path.resolve()
    ]
    assert adapter.received_directory == tmp_path
    assert adapter.received_source is source


def test_get_dataset_files_passes_supported_extensions_to_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = FakeAdapter(
        [],
        frozenset({".csv", ".jsonl"})
    )
    source = {
        "path": str(tmp_path)
    }
    received: dict[str, Any] = {}
    expected_files = [
        tmp_path / "dataset.csv"
    ]

    def fake_validate_dataset_files(
        files: Any,
        dataset_directory: Path,
        supported_extensions: frozenset[str],
        source_name: str
    ) -> list[Path]:
        received["files"] = files
        received["dataset_directory"] = dataset_directory
        received["supported_extensions"] = supported_extensions
        received["source_name"] = source_name
        return expected_files

    monkeypatch.setattr(
        module,
        "validate_dataset_files",
        fake_validate_dataset_files
    )

    result = module.get_dataset_files(
        adapter,
        tmp_path,
        source,
        "Dataset"
    )

    assert result is expected_files
    assert received == {
        "files": [],
        "dataset_directory": tmp_path,
        "supported_extensions": frozenset({
            ".csv",
            ".jsonl"
        }),
        "source_name": "Dataset"
    }


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        (),
        {},
        "dataset.csv",
        Path("dataset.csv"),
        {
            Path("dataset.csv")
        }
    ]
)
def test_get_dataset_files_rejects_non_list_results(
    invalid_result: Any,
    tmp_path: Path
) -> None:
    adapter = FakeAdapter(invalid_result)

    with pytest.raises(
        TypeError,
        match=(
            "La recherche de fichiers de Dataset "
            "doit retourner une liste"
        )
    ):
        module.get_dataset_files(
            adapter,
            tmp_path,
            {},
            "Dataset"
        )


def test_get_dataset_files_rejects_empty_validated_result(
    tmp_path: Path
) -> None:
    adapter = FakeAdapter([])

    with pytest.raises(
        FileNotFoundError,
        match="Aucun fichier exploitable trouvé pour Dataset"
    ):
        module.get_dataset_files(
            adapter,
            tmp_path,
            {},
            "Dataset"
        )


def test_get_dataset_files_rejects_discovered_but_invalid_files(
    tmp_path: Path
) -> None:
    invalid_file = tmp_path / "dataset.txt"
    invalid_file.write_text(
        "contenu",
        encoding="utf-8"
    )

    adapter = FakeAdapter(
        [
            invalid_file
        ],
        frozenset({".csv"})
    )

    with pytest.raises(
        FileNotFoundError,
        match="Aucun fichier exploitable trouvé pour Dataset"
    ):
        module.get_dataset_files(
            adapter,
            tmp_path,
            {},
            "Dataset"
        )


def test_get_dataset_files_propagates_adapter_error(
    tmp_path: Path
) -> None:
    expected_error = RuntimeError("Erreur de découverte")

    class FailingAdapter:
        supported_extensions = frozenset({".csv"})

        def find_files(
            self,
            dataset_directory: Path,
            source: Any
        ) -> list[Path]:
            raise expected_error

    with pytest.raises(RuntimeError) as error_info:
        module.get_dataset_files(
            FailingAdapter(),
            tmp_path,
            {},
            "Dataset"
        )

    assert error_info.value is expected_error


def test_get_dataset_files_propagates_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = FakeAdapter([])
    expected_error = RuntimeError("Erreur de validation")

    def fake_validate_dataset_files(
        *args: Any,
        **kwargs: Any
    ) -> list[Path]:
        raise expected_error

    monkeypatch.setattr(
        module,
        "validate_dataset_files",
        fake_validate_dataset_files
    )

    with pytest.raises(RuntimeError) as error_info:
        module.get_dataset_files(
            adapter,
            tmp_path,
            {},
            "Dataset"
        )

    assert error_info.value is expected_error