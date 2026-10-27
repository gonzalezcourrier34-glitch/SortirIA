# Démarrage

## Installation

Le projet utilise [uv](https://docs.astral.sh/uv/) et Python 3.12.

```bash
cp .env.example .env      # puis renseigner MISTRAL_API_KEY, OPENAGENDA_API_KEY...
uv sync                   # crée .venv et installe les dépendances
uv lock                   # génère uv.lock : à commiter pour figer les versions
```

## Base de données

```bash
docker compose up -d db   # PostgreSQL 16 avec l'extension pgvector
uv run python -m scripts.init_db --verifier   # schéma créé au premier démarrage
```

Après une mise à jour du schéma (nouveau fichier dans `sql/sortiria/`, comme
`07_vocabulary.sql` le 15/10/2026) : `uv run python -m scripts.init_db`. Les
fichiers sont réexécutables.

## Tests

```bash
uv run pytest
```

Les tests n'ont besoin d'aucune clé d'API : `tests/conftest.py` crée des secrets factices
et tous les appels réseau sont simulés.

!!! note "Tests au 27/10/2026"
    3 892 tests (3 877 vérifiés le 26/10, et 15 ajoutés le 27/10 : visiteurs distincts,
    rapport Evidently mensuel). Le 26/10, 3 799 passaient sur le poste ; les 78 autres (schéma, requêtes Grafana,
    dérive) demandent un vrai PostgreSQL et passent en CI, ou en local avec
    `SORTIRIA_TEST_DATABASE_URL` (une base dont le nom contient « test »).

## API et interface

```bash
uv run uvicorn app.main:app --reload --port 8010   # API sur http://127.0.0.1:8010/docs (8000 : triage médical)
cd frontend && npm start                       # interface (http://localhost:4210)
```

## Agent de collecte

```bash
uv run python -m agent                     # collecte complète (en production : chaque nuit à 1 h UTC, 3 h à Paris en été)
uv run python -m agent --essai             # sans écrire en base
uv run python -m agent --bilan-recherche   # ce que les gens ont cherché sans trouver (7 jours)
uv run python -m agent --bilan-recherche --apprendre   # + propositions de l'agent
uv run python -m agent.proposals           # propositions en attente (--accepter N, --refuser N)
uv run python -m scripts.eval_recherche    # évaluation de la recherche (50 questions)
```

## Image Docker de l'API

```bash
docker build -f docker/Dockerfile.api -t sortiria-api .
```

## Documentation

```bash
uv sync --group docs
uv run mkdocs serve -a 127.0.0.1:8001   # http://127.0.0.1:8001 (8000 : triage médical)
```
