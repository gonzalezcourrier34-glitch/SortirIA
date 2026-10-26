"""Versionnage des consignes, des modèles et des jeux de données dans MLflow (26/10/2026).

**Provenance** : nouveau script écrit pour SortirIA.

SortirIA n'entraîne aucun modèle : ce qui change d'une version à l'autre, ce
sont les **consignes** envoyées à Mistral, les **modèles** Mistral choisis et
les **jeux de données** qui servent à mesurer. Ce script les enregistre dans
les registres de MLflow (le même serveur que les évaluations) :

- **Prompt Registry** : les 8 consignes, une version MLflow par texte différent,
  avec l'étiquette du code (`PROMPT_VERSION`, `JUDGE_VERSION`...), le modèle qui
  la lit et son empreinte ; l'alias `production` désigne la version en service ;
- **jeux de données** : les jeux d'évaluation (questions, base figée de la CI,
  grilles de pertinence) et la configuration des sources, chacun avec son
  empreinte SHA-256 et son lien vers GitHub au commit courant ;
- **Model Registry** : la « chaîne SortirIA » (`sortiria-chaine`), un modèle
  externe qui réunit les modèles Mistral, les versions des consignes et les
  empreintes des jeux. Une nouvelle version n'est créée que si cette empreinte
  change ; l'alias `production` passe dessus, `precedente` garde la version
  d'avant, pour savoir ce qui tournait et revenir en arrière (`git revert`).

**Garde-fou en CI** : `config/versions_consignes.json` garde, pour chaque
consigne, sa version et l'empreinte de son texte. Un test échoue si un texte
change sans que sa version change, ou si le manifeste n'est pas à jour.

Usage :

    uv run python -m scripts.versionnage --verifier    # comparer au manifeste (sans MLflow)
    uv run python -m scripts.versionnage --manifeste   # réécrire le manifeste après un changement
    uv run python -m scripts.versionnage               # enregistrer dans MLflow
    uv run python -m scripts.versionnage --evaluation eval_recherche.json

Sans `MLFLOW_TRACKING_URI`, l'enregistrement va dans la base SQLite locale du
suivi de l'agent (`data/mlflow/mlflow.db`) : les registres demandent une base,
pas de simples fichiers.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FILE = ROOT / "config" / "versions_consignes.json"
REPOSITORY_URL = "https://github.com/gonzalezcourrier34-glitch/SortirIA"
EXPERIMENT = "sortiria-versionnage"
CHAIN_MODEL = "sortiria-chaine"
PRODUCTION = "production"
PREVIOUS = "precedente"
TEXT_SUFFIXES = {".yaml", ".yml", ".csv", ".json", ".md", ".txt"}


@dataclass(frozen=True)
class PromptSource:
    """Où trouver une consigne dans le code, et quel modèle la lit.

    Attributes
    ----------
    name : str
        Nom dans le Prompt Registry (`sortiria-...`).
    module : str
        Module Python qui définit la consigne.
    template : str
        Nom de la constante qui contient le texte.
    version : str
        Nom de la constante qui contient la version.
    model_env : str
        Variable d'environnement qui choisit le modèle.
    model_default : str
        Modèle par défaut (celui du code).
    usage : str
        À quoi sert la consigne, en quelques mots.
    """

    name: str
    module: str
    template: str
    version: str
    model_env: str
    model_default: str
    usage: str


PROMPT_SOURCES: tuple[PromptSource, ...] = (
    PromptSource("sortiria-reponse", "app.search_answer", "WRITE_PROMPT", "PROMPT_VERSION",
                 "SEARCH_MODEL", "mistral-small-latest", "rédiger la réponse au visiteur"),
    PromptSource("sortiria-lecture-programme", "src.event.document_mapper", "EXTRACTION_PROMPT",
                 "PROMPT_VERSION", "DOCUMENT_EXTRACTION_MODEL", "mistral-small-latest",
                 "lire le programme PDF de Sète agglopôle"),
    PromptSource("sortiria-documents-partenaires",
                 "src.extractors.datasets.source.partner_documents", "EXTRACTION_PROMPT",
                 "PROMPT_VERSION", "DOCUMENT_EXTRACTION_MODEL", "mistral-small-latest",
                 "lire les documents des partenaires"),
    PromptSource("sortiria-enrichissement", "src.event.event_enrichment", "ENRICHMENT_PROMPT",
                 "ENRICHMENT_VERSION", "ENRICHMENT_MODEL", "mistral-small-latest",
                 "ranger et résumer les événements la nuit"),
    PromptSource("sortiria-juge-qualite", "src.event.quality_judge", "JUDGE_PROMPT",
                 "JUDGE_VERSION", "JUDGE_MODEL", "mistral-medium-latest",
                 "contrôler un échantillon de fiches chaque nuit"),
    PromptSource("sortiria-juge-pertinence", "scripts.eval_pertinence", "JUDGE_PROMPT",
                 "JUDGE_PROMPT_VERSION", "PERTINENCE_JUDGE_MODEL", "mistral-large-latest",
                 "juger la pertinence des 3 premières cartes"),
    PromptSource("sortiria-apprentissage-recherches", "agent.search_learning", "LEARNING_PROMPT",
                 "LEARNING_PROMPT_VERSION", "LEARNING_MODEL", "mistral-small-latest",
                 "lire les recherches ratées (agent du lundi)"),
    PromptSource("sortiria-apprentissage-avis", "agent.feedback_learning", "FEEDBACK_PROMPT",
                 "FEEDBACK_PROMPT_VERSION", "LEARNING_MODEL", "mistral-small-latest",
                 "lire les pouces bas (agent du lundi)"),
)

# Jeux de données suivis : nom dans MLflow -> fichier (chemin depuis la racine)
DATASETS: dict[str, str] = {
    "questions-recherche": "config/eval_recherche.yaml",
    "recherches-courtes": "config/eval_mots.yaml",
    "recherches-pratique": "config/eval_pratique.yaml",
    "recherches-moments": "config/eval_moments.yaml",
    "base-figee-ci": "tests/data/jeu_recherche.json.gz",
    "grille-pertinence-calibrage": "benchmarks/pertinence/grille_2026-10-16_09h50_juge.csv",
    "grille-pertinence-questions": "benchmarks/pertinence/grille_2026-10-16_12h52_juge.csv",
    "grille-pertinence-mots": "benchmarks/pertinence/grille_2026-10-16_12h50_mots_juge.csv",
    "configuration-sources": "config/sources.yaml",
    "synonymes": "config/synonymes.yaml",
}

# Modèles de la chaîne qui n'ont pas de consigne propre : variable -> défaut
OTHER_MODELS: dict[str, tuple[str, str]] = {
    "modele_secours": ("SEARCH_FALLBACK_MODEL", "mistral-medium-latest"),
    "modele_vecteurs": ("EMBEDDING_MODEL", "mistral-embed"),
    "modele_moderation": ("MODERATION_MODEL", "mistral-moderation-latest"),
}


def fingerprint(data: str | bytes) -> str:
    """Empreinte courte (16 caractères hexadécimaux de SHA-256).

    Parameters
    ----------
    data : str | bytes
        Texte (encodé en UTF-8) ou octets.

    Returns
    -------
    str
        L'empreinte.
    """

    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()[:16]


def file_fingerprint(path: Path) -> str:
    """Empreinte d'un fichier, identique sous Windows et sous Linux.

    Les fichiers texte sont lus avec des fins de ligne `\\n` (Git peut les
    écrire en `\\r\\n` sous Windows) ; un `.gz` est décompressé, car deux
    compressions du même contenu peuvent différer.

    Parameters
    ----------
    path : Path
        Le fichier.

    Returns
    -------
    str
        L'empreinte du contenu.
    """

    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    elif path.suffix in TEXT_SUFFIXES:
        raw = raw.replace(b"\r\n", b"\n")
    return fingerprint(raw)


def model_for(source: PromptSource, environ: Mapping[str, str] | None = None) -> str:
    """Modèle qui lit une consigne : la variable d'environnement, sinon le défaut.

    Parameters
    ----------
    source : PromptSource
        La consigne.
    environ : Mapping[str, str] | None
        Variables d'environnement ; par défaut `os.environ`.

    Returns
    -------
    str
        Le nom du modèle Mistral.
    """

    environ = os.environ if environ is None else environ
    return (environ.get(source.model_env) or "").strip() or source.model_default


def load_prompts(sources: Sequence[PromptSource] = PROMPT_SOURCES,
                 environ: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """Lit chaque consigne dans le code : texte, version, empreinte, modèle.

    Parameters
    ----------
    sources : Sequence[PromptSource]
        Les consignes à lire ; par défaut les 8 de SortirIA.
    environ : Mapping[str, str] | None
        Variables d'environnement pour le choix des modèles.

    Returns
    -------
    list[dict[str, str]]
        Une entrée par consigne : `name`, `version`, `template`, `empreinte`,
        `modele`, `usage`, `source` (module et constante).
    """

    prompts = []
    for source in sources:
        module = importlib.import_module(source.module)
        template = str(getattr(module, source.template))
        prompts.append({
            "name": source.name,
            "version": str(getattr(module, source.version)),
            "template": template,
            "empreinte": fingerprint(template),
            "modele": model_for(source, environ),
            "usage": source.usage,
            "source": f"{source.module}.{source.template}",
        })
    return prompts


def build_manifest(prompts: Sequence[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    """Manifeste des consignes : version et empreinte de chacune.

    Parameters
    ----------
    prompts : Sequence[Mapping[str, str]]
        Les consignes lues par `load_prompts`.

    Returns
    -------
    dict[str, dict[str, str]]
        `{nom: {"version": ..., "empreinte": ..., "source": ...}}`, trié par nom.
    """

    return {prompt["name"]: {"version": prompt["version"], "empreinte": prompt["empreinte"],
                             "source": prompt["source"]}
            for prompt in sorted(prompts, key=lambda item: item["name"])}


def check_manifest(prompts: Sequence[Mapping[str, str]],
                   manifest: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Compare les consignes du code au manifeste ; renvoie les problèmes trouvés.

    Le cas grave est un texte modifié sans changement de version : deux
    réponses rédigées avec des consignes différentes porteraient la même
    version, et on ne saurait plus laquelle a produit quoi.

    Parameters
    ----------
    prompts : Sequence[Mapping[str, str]]
        Les consignes lues par `load_prompts`.
    manifest : Mapping[str, Mapping[str, str]]
        Le manifeste enregistré (`config/versions_consignes.json`).

    Returns
    -------
    list[str]
        Un message par problème ; vide si tout concorde.
    """

    problems = []
    names = {prompt["name"] for prompt in prompts}
    for prompt in prompts:
        known = manifest.get(prompt["name"])
        same_text = known is not None and known.get("empreinte") == prompt["empreinte"]
        same_version = known is not None and known.get("version") == prompt["version"]
        if known is None:
            problems.append(f"{prompt['name']} : consigne absente du manifeste.")
        elif not same_text and same_version:
            problems.append(f"{prompt['name']} : texte modifié sans changer sa version "
                            f"({prompt['source']} est toujours en {prompt['version']}).")
        elif not (same_text and same_version):
            problems.append(f"{prompt['name']} : manifeste à mettre à jour "
                            f"({known.get('version')} -> {prompt['version']}).")
    problems += [f"{name} : dans le manifeste mais plus dans le code." for name in manifest
                 if name not in names]
    return problems


def read_manifest(path: Path = MANIFEST_FILE) -> dict[str, dict[str, str]]:
    """Lit le manifeste ; vide s'il n'existe pas encore.

    Parameters
    ----------
    path : Path
        Le fichier du manifeste.

    Returns
    -------
    dict[str, dict[str, str]]
        Le manifeste.
    """

    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(prompts: Sequence[Mapping[str, str]], path: Path = MANIFEST_FILE) -> None:
    """Écrit le manifeste (JSON indenté, fin de ligne finale).

    Parameters
    ----------
    prompts : Sequence[Mapping[str, str]]
        Les consignes lues par `load_prompts`.
    path : Path
        Le fichier du manifeste.
    """

    text = json.dumps(build_manifest(prompts), ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def load_datasets(root: Path = ROOT,
                  datasets: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """Empreinte de chaque jeu de données suivi.

    Parameters
    ----------
    root : Path
        Racine du projet.
    datasets : Mapping[str, str] | None
        `{nom: chemin}` ; par défaut `DATASETS`.

    Returns
    -------
    list[dict[str, str]]
        Une entrée par jeu : `name`, `path`, `empreinte`.

    Raises
    ------
    FileNotFoundError
        Si un jeu suivi a disparu : la chaîne ne serait plus mesurable.
    """

    datasets = DATASETS if datasets is None else datasets
    return [{"name": name, "path": path, "empreinte": file_fingerprint(root / path)}
            for name, path in datasets.items()]


def chain_params(prompts: Sequence[Mapping[str, str]], datasets: Sequence[Mapping[str, str]],
                 environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Paramètres de la chaîne : modèles, versions des consignes, empreintes des jeux.

    Parameters
    ----------
    prompts : Sequence[Mapping[str, str]]
        Les consignes lues par `load_prompts`.
    datasets : Sequence[Mapping[str, str]]
        Les jeux lus par `load_datasets`.
    environ : Mapping[str, str] | None
        Variables d'environnement pour les modèles sans consigne.

    Returns
    -------
    dict[str, str]
        Paramètres triés : `consigne.<nom>` (version et empreinte),
        `modele.<nom>` (modèle de chaque consigne), `modele_*` (secours,
        vecteurs, modération), `jeu.<nom>` (empreinte).
    """

    environ = os.environ if environ is None else environ
    params = {}
    for prompt in prompts:
        short = prompt["name"].removeprefix("sortiria-")
        params[f"consigne.{short}"] = f"{prompt['version']} ({prompt['empreinte'][:8]})"
        params[f"modele.{short}"] = prompt["modele"]
    for key, (variable, default) in OTHER_MODELS.items():
        params[key] = (environ.get(variable) or "").strip() or default
    for dataset in datasets:
        params[f"jeu.{dataset['name']}"] = dataset["empreinte"]
    return dict(sorted(params.items()))


def chain_fingerprint(params: Mapping[str, str]) -> str:
    """Empreinte de la chaîne : change dès qu'un modèle, une consigne ou un jeu change.

    Parameters
    ----------
    params : Mapping[str, str]
        Les paramètres de `chain_params`.

    Returns
    -------
    str
        L'empreinte.
    """

    return fingerprint(json.dumps(dict(sorted(params.items())), ensure_ascii=False))


def git_commit(root: Path = ROOT) -> str:
    """Commit courant, ou chaîne vide hors d'un dépôt Git.

    Parameters
    ----------
    root : Path
        Racine du projet.

    Returns
    -------
    str
        L'identifiant du commit.
    """

    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def evaluation_metrics(path: Path) -> dict[str, float]:
    """Métriques d'un rapport d'évaluation (`scripts.eval_recherche --json`).

    Parameters
    ----------
    path : Path
        Le rapport JSON.

    Returns
    -------
    dict[str, float]
        Les métriques numériques présentes (précision, compréhension, latence...).
    """

    report = json.loads(path.read_text(encoding="utf-8"))
    keys = ("precision", "comprehension", "latence_mediane_s", "latence_p95_s", "questions",
            "sans_resultat", "idees_voisines")
    return {key: float(report[key]) for key in keys if isinstance(report.get(key), int | float)}


def register_prompts(mlflow: Any, prompts: Sequence[Mapping[str, str]]) -> dict[str, int]:
    """Enregistre chaque consigne dont le texte a changé ; alias `production` à jour.

    Parameters
    ----------
    mlflow : Any
        Le module `mlflow` (remplaçable dans les tests).
    prompts : Sequence[Mapping[str, str]]
        Les consignes lues par `load_prompts`.

    Returns
    -------
    dict[str, int]
        `{nom: numéro de version MLflow en production}`.
    """

    versions = {}
    for prompt in prompts:
        name = prompt["name"]
        try:
            current = mlflow.genai.load_prompt(f"prompts:/{name}@{PRODUCTION}")
        except Exception:  # noqa: BLE001 (consigne jamais enregistrée)
            current = None
        if current is not None and current.template == prompt["template"]:
            versions[name] = int(current.version)
            continue
        registered = mlflow.genai.register_prompt(
            name=name,
            template=prompt["template"],
            commit_message=f"{prompt['version']} : {prompt['usage']}",
            tags={"version": prompt["version"], "empreinte": prompt["empreinte"],
                  "modele": prompt["modele"], "source": prompt["source"]},
            model_config={"provider": "mistral", "model_name": prompt["modele"]},
        )
        mlflow.genai.set_prompt_alias(name, alias=PRODUCTION, version=int(registered.version))
        versions[name] = int(registered.version)
    return versions


def dataset_input(mlflow: Any, dataset: Mapping[str, str], commit: str) -> Any:
    """Jeu de données MLflow : empreinte et lien vers le fichier sur GitHub.

    Parameters
    ----------
    mlflow : Any
        Le module `mlflow`.
    dataset : Mapping[str, str]
        Un jeu lu par `load_datasets`.
    commit : str
        Commit courant (lien figé) ; vide : branche `main`.

    Returns
    -------
    Any
        Un `MetaDataset` prêt pour `mlflow.log_input`.
    """

    from mlflow.data.http_dataset_source import HTTPDatasetSource
    from mlflow.data.meta_dataset import MetaDataset

    url = f"{REPOSITORY_URL}/blob/{commit or 'main'}/{dataset['path']}"
    return MetaDataset(source=HTTPDatasetSource(url=url), name=dataset["name"],
                       digest=dataset["empreinte"])


def production_version(client: Any) -> Any:
    """Version de la chaîne qui porte l'alias `production`, ou None.

    Parameters
    ----------
    client : Any
        Un `MlflowClient`.

    Returns
    -------
    Any
        La version enregistrée, ou None si la chaîne n'a jamais été enregistrée.
    """

    try:
        return client.get_model_version_by_alias(CHAIN_MODEL, PRODUCTION)
    except Exception:  # noqa: BLE001 (modèle ou alias absent)
        return None


def version_model_id(version: Any) -> str:
    """Identifiant du modèle externe derrière une version enregistrée.

    Parameters
    ----------
    version : Any
        Une version du Model Registry.

    Returns
    -------
    str
        L'identifiant (`m-...`), lu dans `model_id` ou dans la source
        `models:/m-...` ; vide s'il est introuvable.
    """

    model_id = getattr(version, "model_id", None)
    if model_id:
        return str(model_id)
    source = str(getattr(version, "source", "") or "")
    return source.removeprefix("models:/").split("/")[0] if source.startswith("models:/") else ""


def register_chain(mlflow: Any, prompts: Sequence[Mapping[str, str]],
                   datasets: Sequence[Mapping[str, str]],
                   metrics: Mapping[str, float] | None = None,
                   commit: str = "", environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Enregistre consignes, jeux de données et chaîne ; ne crée rien si rien n'a changé.

    Parameters
    ----------
    mlflow : Any
        Le module `mlflow` (remplaçable dans les tests).
    prompts : Sequence[Mapping[str, str]]
        Les consignes lues par `load_prompts`.
    datasets : Sequence[Mapping[str, str]]
        Les jeux lus par `load_datasets`.
    metrics : Mapping[str, float] | None
        Résultats d'une évaluation à rattacher à la version (facultatif).
    commit : str
        Commit courant.
    environ : Mapping[str, str] | None
        Variables d'environnement pour les modèles.

    Returns
    -------
    dict[str, Any]
        `consignes` (versions MLflow), `empreinte`, `version` de la chaîne,
        `nouvelle` (True si une version a été créée), `precedente`.
    """

    prompt_versions = register_prompts(mlflow, prompts)
    params = chain_params(prompts, datasets, environ)
    for prompt in prompts:
        short = prompt["name"].removeprefix("sortiria-")
        params[f"uri.{short}"] = f"prompts:/{prompt['name']}/{prompt_versions[prompt['name']]}"
    chain_print = chain_fingerprint(params)
    client = mlflow.MlflowClient()
    current = production_version(client)
    report: dict[str, Any] = {"consignes": prompt_versions, "empreinte": chain_print,
                              "nouvelle": False, "precedente": None}
    if current is not None and current.tags.get("empreinte") == chain_print:
        # Chaîne inchangée : on rattache seulement les nouvelles mesures
        model_id = version_model_id(current)
        if metrics and model_id:
            mlflow.set_experiment(EXPERIMENT)
            with mlflow.start_run(run_name=f"mesures chaine {chain_print[:8]}"):
                mlflow.log_metrics(dict(metrics), model_id=model_id)
            report["mesures"] = True
        report["version"] = int(current.version)
        return report

    # Nouvelle version : un lancement qui porte le modèle externe et ses jeux
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"chaine {chain_print[:8]}"):
        model = mlflow.create_external_model(
            name=CHAIN_MODEL, params=params, model_type="chaine-rag",
            tags={"empreinte": chain_print, "commit": commit},
        )
        from mlflow.entities import LoggedModelInput

        for dataset in datasets:
            mlflow.log_input(dataset_input(mlflow, dataset, commit), context="evaluation",
                             model=LoggedModelInput(model.model_id))
        if metrics:
            mlflow.log_metrics(dict(metrics), model_id=model.model_id)
        mlflow.log_dict(build_manifest(prompts), "versions_consignes.json")
    version = mlflow.register_model(f"models:/{model.model_id}", CHAIN_MODEL)
    for key, value in {"empreinte": chain_print, "commit": commit}.items():
        client.set_model_version_tag(CHAIN_MODEL, version.version, key, value)
    if current is not None and str(current.version) != str(version.version):
        client.set_registered_model_alias(CHAIN_MODEL, PREVIOUS, current.version)
        report["precedente"] = int(current.version)
    client.set_registered_model_alias(CHAIN_MODEL, PRODUCTION, version.version)
    report |= {"version": int(version.version), "nouvelle": True}
    return report


def connect(mlflow: Any) -> None:
    """Serveur MLflow de `.env`, sinon la base SQLite locale du suivi de l'agent.

    En local, l'expérience range ses fichiers dans `data/mlflow/artifacts`
    (comme `agent/tracking.py`), pas dans un dossier `mlruns/` du répertoire
    courant.

    Parameters
    ----------
    mlflow : Any
        Le module `mlflow`.
    """

    if os.getenv("MLFLOW_TRACKING_URI"):
        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        return
    from config.paths import DATA_DIR

    folder = DATA_DIR / "mlflow"
    folder.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{(folder / 'mlflow.db').as_posix()}")
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=(folder / "artifacts").as_uri())


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : vérifier, réécrire le manifeste, ou enregistrer.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments de la ligne de commande.

    Returns
    -------
    int
        0 si tout va bien, 1 si le manifeste ne concorde pas.
    """

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--verifier", action="store_true",
                        help="comparer au manifeste, sans MLflow")
    parser.add_argument("--manifeste", action="store_true", help="réécrire le manifeste")
    parser.add_argument("--evaluation", type=Path, help="rapport JSON d'évaluation à rattacher")
    args = parser.parse_args(argv)

    from config.environment import load_environment

    load_environment()
    prompts = load_prompts()
    if args.manifeste:
        write_manifest(prompts)
        print(f"Manifeste écrit : {MANIFEST_FILE.relative_to(ROOT)} ({len(prompts)} consignes).")
        return 0
    problems = check_manifest(prompts, read_manifest())
    for problem in problems:
        print(problem)
    if problems:
        print("Changez la version de la consigne, puis : python -m scripts.versionnage --manifeste")
        return 1
    if args.verifier:
        print(f"{len(prompts)} consignes conformes au manifeste.")
        return 0

    import mlflow

    connect(mlflow)
    metrics = evaluation_metrics(args.evaluation) if args.evaluation else None
    report = register_chain(mlflow, prompts, load_datasets(), metrics, git_commit())
    state = "nouvelle version" if report["nouvelle"] else "inchangée"
    print(f"Chaîne {CHAIN_MODEL} : version {report['version']} ({state}, "
          f"empreinte {report['empreinte']}), alias {PRODUCTION}.")
    for name, version in report["consignes"].items():
        print(f"  {name} : version MLflow {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
