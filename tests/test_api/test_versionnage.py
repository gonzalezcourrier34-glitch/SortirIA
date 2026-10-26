"""Versionnage des consignes, des modèles et des jeux de données (scripts/versionnage.py)."""

from __future__ import annotations

import gzip
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from scripts import versionnage
from scripts.versionnage import (
    PROMPT_SOURCES,
    build_manifest,
    chain_fingerprint,
    chain_params,
    check_manifest,
    file_fingerprint,
    load_datasets,
    load_prompts,
    read_manifest,
    register_chain,
    version_model_id,
)

NO_ENV: dict[str, str] = {}


# Garde-fou de la CI : le manifeste suit le code


def test_manifest_matches_the_code() -> None:
    """Une consigne modifiée sans changer sa version fait échouer la CI."""

    problems = check_manifest(load_prompts(environ=NO_ENV), read_manifest())
    assert problems == [], "\n".join(problems) + (
        "\nChangez la version de la consigne, puis : python -m scripts.versionnage --manifeste")


def test_every_prompt_has_a_version_and_a_model() -> None:
    prompts = load_prompts(environ=NO_ENV)
    assert len(prompts) == len(PROMPT_SOURCES) == 8
    assert all(prompt["version"] and prompt["modele"].startswith(("mistral", "ministral"))
               for prompt in prompts)
    assert len({prompt["name"] for prompt in prompts}) == 8


def test_tracked_datasets_exist() -> None:
    datasets = load_datasets()
    assert {dataset["name"] for dataset in datasets} >= {"questions-recherche", "base-figee-ci"}
    assert all(len(dataset["empreinte"]) == 16 for dataset in datasets)


# Contrôle du manifeste


def prompt(name: str = "sortiria-x", version: str = "v1", text: str = "Consigne") -> dict[str, str]:
    return {"name": name, "version": version, "template": text,
            "empreinte": versionnage.fingerprint(text), "modele": "mistral-small-latest",
            "usage": "essai", "source": f"module.{name}"}


def test_text_changed_without_new_version_is_refused() -> None:
    manifest = build_manifest([prompt()])
    problems = check_manifest([prompt(text="Consigne retouchée")], manifest)
    assert len(problems) == 1 and "sans changer sa version" in problems[0]


def test_new_version_asks_to_update_the_manifest() -> None:
    manifest = build_manifest([prompt()])
    problems = check_manifest([prompt(version="v2", text="Consigne retouchée")], manifest)
    assert len(problems) == 1 and "v1 -> v2" in problems[0]


def test_missing_and_removed_prompts_are_reported() -> None:
    manifest = build_manifest([prompt("sortiria-ancienne")])
    problems = check_manifest([prompt("sortiria-nouvelle")], manifest)
    assert any("absente du manifeste" in problem for problem in problems)
    assert any("plus dans le code" in problem for problem in problems)


# Empreintes


def test_file_fingerprint_ignores_windows_line_endings_and_gzip(tmp_path: Path) -> None:
    unix, windows = tmp_path / "a.yaml", tmp_path / "b.yaml"
    unix.write_bytes(b"a: 1\nb: 2\n")
    windows.write_bytes(b"a: 1\r\nb: 2\r\n")
    assert file_fingerprint(unix) == file_fingerprint(windows)
    packed = tmp_path / "c.json.gz"
    packed.write_bytes(gzip.compress(b'{"x": 1}', mtime=0))
    assert file_fingerprint(packed) == versionnage.fingerprint(b'{"x": 1}')


def test_chain_fingerprint_changes_with_a_model_a_prompt_or_a_dataset() -> None:
    datasets = [{"name": "jeu", "path": "x", "empreinte": "aaaa"}]
    base = chain_fingerprint(chain_params([prompt()], datasets, NO_ENV))
    assert base == chain_fingerprint(chain_params([prompt()], datasets, NO_ENV))
    assert base != chain_fingerprint(chain_params([prompt()], datasets,
                                                  {"EMBEDDING_MODEL": "autre-embed"}))
    changed = [prompt(version="v2", text="B")]
    assert base != chain_fingerprint(chain_params(changed, datasets, NO_ENV))
    assert base != chain_fingerprint(chain_params([prompt()], [dict(datasets[0], empreinte="b")],
                                                  NO_ENV))


def test_version_model_id_reads_the_source_when_needed() -> None:
    assert version_model_id(types.SimpleNamespace(model_id="m-1", source="")) == "m-1"
    assert version_model_id(types.SimpleNamespace(model_id=None, source="models:/m-2")) == "m-2"
    assert version_model_id(types.SimpleNamespace(source="runs:/x/model")) == ""


# Enregistrement, avec un MLflow factice


@dataclass
class FakeRegistry:
    prompts: dict[str, list[str]] = field(default_factory=dict)
    prompt_alias: dict[str, int] = field(default_factory=dict)
    versions: list[types.SimpleNamespace] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)


@pytest.fixture
def fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeRegistry]:
    registry = FakeRegistry()
    module = types.ModuleType("mlflow")

    def load_prompt(uri: str) -> Any:
        name = uri.removeprefix("prompts:/").split("@")[0]
        if name not in registry.prompt_alias:
            raise LookupError(name)
        version = registry.prompt_alias[name]
        return types.SimpleNamespace(version=version, template=registry.prompts[name][version - 1])

    def register_prompt(name: str, template: str, **kwargs: Any) -> Any:
        registry.prompts.setdefault(name, []).append(template)
        registry.calls.append(("prompt", name))
        return types.SimpleNamespace(version=len(registry.prompts[name]))

    def set_prompt_alias(name: str, alias: str, version: int) -> None:
        registry.prompt_alias[name] = version

    module.genai = types.SimpleNamespace(load_prompt=load_prompt, register_prompt=register_prompt,
                                         set_prompt_alias=set_prompt_alias)

    class Client:
        def get_model_version_by_alias(self, name: str, alias: str) -> Any:
            return registry.versions[int(registry.aliases[alias]) - 1]

        def set_model_version_tag(self, name: str, version: str, key: str, value: str) -> None:
            registry.versions[int(version) - 1].tags[key] = value

        def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
            registry.aliases[alias] = str(version)

    module.MlflowClient = Client
    module.set_experiment = lambda name: registry.calls.append(("experience", name))

    @contextmanager
    def start_run(run_name: str):
        registry.calls.append(("run", run_name))
        yield

    module.start_run = start_run
    module.create_external_model = lambda **kwargs: types.SimpleNamespace(
        model_id=f"m-{len(registry.versions) + 1}", params=kwargs["params"])
    module.log_input = lambda dataset, context, model: registry.calls.append(("jeu", dataset))
    module.log_metrics = lambda metrics, model_id: registry.calls.append(("mesures", model_id))
    module.log_dict = lambda data, name: registry.calls.append(("fichier", name))

    def register_model(uri: str, name: str) -> Any:
        version = types.SimpleNamespace(version=str(len(registry.versions) + 1), tags={},
                                        source=uri, model_id=None)
        registry.versions.append(version)
        return version

    module.register_model = register_model
    entities = types.ModuleType("mlflow.entities")
    entities.LoggedModelInput = lambda model_id: model_id
    http = types.ModuleType("mlflow.data.http_dataset_source")
    http.HTTPDatasetSource = lambda url: url
    meta = types.ModuleType("mlflow.data.meta_dataset")
    meta.MetaDataset = lambda source, name, digest: (name, digest, source)
    modules = {"mlflow": module, "mlflow.entities": entities,
               "mlflow.data": types.ModuleType("mlflow.data"),
               "mlflow.data.http_dataset_source": http, "mlflow.data.meta_dataset": meta}
    for key, value in modules.items():
        monkeypatch.setitem(sys.modules, key, value)
    return module, registry


DATASETS = [{"name": "questions", "path": "config/q.yaml", "empreinte": "aaaa"}]


def test_first_registration_creates_prompts_chain_and_production_alias(fake_mlflow: tuple) -> None:
    mlflow, registry = fake_mlflow
    report = register_chain(mlflow, [prompt("sortiria-a"), prompt("sortiria-b")], DATASETS,
                            commit="abc", environ=NO_ENV)
    assert report["nouvelle"] and report["version"] == 1 and report["precedente"] is None
    assert report["consignes"] == {"sortiria-a": 1, "sortiria-b": 1}
    assert registry.aliases == {"production": "1"}
    assert registry.versions[0].tags["commit"] == "abc"
    jeux = [value for kind, value in registry.calls if kind == "jeu"]
    url = "https://github.com/gonzalezcourrier34-glitch/SortirIA/blob/abc/config/q.yaml"
    assert jeux == [("questions", "aaaa", url)]


def test_nothing_changes_when_nothing_changed(fake_mlflow: tuple) -> None:
    mlflow, registry = fake_mlflow
    register_chain(mlflow, [prompt()], DATASETS, environ=NO_ENV)
    calls = len(registry.calls)
    report = register_chain(mlflow, [prompt()], DATASETS, environ=NO_ENV)
    assert not report["nouvelle"] and report["version"] == 1
    assert len(registry.calls) == calls and len(registry.versions) == 1


def test_changed_prompt_creates_new_versions_and_keeps_the_previous(fake_mlflow: tuple) -> None:
    mlflow, registry = fake_mlflow
    register_chain(mlflow, [prompt()], DATASETS, environ=NO_ENV)
    report = register_chain(mlflow, [prompt(version="v2", text="Nouvelle consigne")], DATASETS,
                            environ=NO_ENV)
    assert report["consignes"] == {"sortiria-x": 2} and report["version"] == 2
    assert registry.aliases == {"production": "2", "precedente": "1"}


def test_measures_attach_to_the_unchanged_version(fake_mlflow: tuple) -> None:
    mlflow, registry = fake_mlflow
    register_chain(mlflow, [prompt()], DATASETS, environ=NO_ENV)
    report = register_chain(mlflow, [prompt()], DATASETS, {"precision": 0.987}, environ=NO_ENV)
    assert not report["nouvelle"] and report["mesures"]
    assert ("mesures", "m-1") in registry.calls and len(registry.versions) == 1


# Avec le vrai MLflow (installé par uv sync, donc en CI)


def test_real_mlflow_registries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.chdir(tmp_path)
    mlflow.set_tracking_uri(f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    try:
        prompts = [prompt("sortiria-reponse", text="Réponds en {n} phrases.")]
        first = register_chain(mlflow, prompts, DATASETS, {"precision": 0.987}, "abc", NO_ENV)
        again = register_chain(mlflow, prompts, DATASETS, environ=NO_ENV)
        changed = register_chain(mlflow, prompts, DATASETS, environ={"SEARCH_FALLBACK_MODEL": "x"})
        client = mlflow.MlflowClient()
        production = client.get_model_version_by_alias("sortiria-chaine", "production")
        model = mlflow.get_logged_model(version_model_id(production))
        loaded = mlflow.genai.load_prompt("prompts:/sortiria-reponse@production")
    finally:
        mlflow.set_tracking_uri("")
    assert (first["version"], again["nouvelle"], changed["version"]) == (1, False, 2)
    assert changed["precedente"] == 1
    assert model.params["modele_secours"] == "x"
    assert model.params["uri.reponse"] == "prompts:/sortiria-reponse/1"
    assert loaded.template == "Réponds en {n} phrases." and loaded.tags["version"] == "v1"
