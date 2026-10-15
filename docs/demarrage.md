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

## Tests

```bash
uv run pytest
```

Les tests n'ont besoin d'aucune clé d'API : `tests/conftest.py` crée des secrets factices
et tous les appels réseau sont simulés.

!!! note "Tests vérifiés au 15/10/2026"
    3 421 tests passent, dont ceux du schéma sur un vrai PostgreSQL. Les tests RSS
    (`feedparser`) n'ont pas pu être lancés dans l'environnement de vérification ;
    ils doivent être relancés avec `uv run pytest` après `uv sync`.

## API et interface

```bash
uv run uvicorn app.main:app --reload            # API sur http://127.0.0.1:8000/docs
cd frontend && npm start                       # interface (http://localhost:4210)
```

## Image Docker de l'API

```bash
docker build -f docker/Dockerfile.api -t sortiria-api .
```

## Documentation

```bash
uv sync --group docs
uv run mkdocs serve       # http://127.0.0.1:8000
```
