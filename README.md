# SortirIA

Assistant qui recommande des sorties à Montpellier, dans ses alentours et sur le bassin de Thau (Sète, Frontignan, Mèze, Marseillan...) à partir d'une demande en langage naturel
(« un concert pas trop cher samedi soir, en extérieur »).

Projet personnel de fin de formation Ingénieur IA (OpenClassrooms), livré le 6 novembre 2026.
Il industrialise deux projets précédents :

- **CheckIt.AI** (projet 12) : moteur de collecte multi-sources réutilisable → `src/`, `config/`, `dags/`, `sql/`
  (seuls les moteurs génériques sont gardés : tout ce qui concernait les fake news a été retiré)
- **Assistant RAG OpenAgenda** (projet 6) : point de départ de l'API (`app/`) ; son RAG FAISS et son interface Streamlit ont été remplacés (recherche PostgreSQL + pgvector, interface Angular `frontend/`)

Le détail de ce qui a été repris, et de ce qui reste à adapter, est dans [PROVENANCE.md](PROVENANCE.md).

## Architecture cible (MVP)

1. Collecte quotidienne des événements (CheckIt.AI) : extraction, normalisation, déduplication, score de qualité.
2. Retrait des événements passés, embeddings des nouveaux (Mistral), stockage PostgreSQL + pgvector.
3. Question utilisateur → extraction des contraintes (date, lieu, budget, type) → filtre SQL → recherche sémantique.
4. Réponse générée par Mistral, avec le lien source de chaque événement.
5. Journalisation : requête, résultats, latence, coût en tokens.

Déploiement : API FastAPI et interface Angular sur Azure Container Apps, base Azure Database for PostgreSQL,
CI/CD GitHub Actions.

## Démarrage local

```bash
cp .env.example .env          # puis renseigner les clés
uv sync
docker compose up -d db       # PostgreSQL + pgvector
uv run python -m scripts.init_db --verifier   # schéma créé au premier démarrage
uv run pytest
uv run mkdocs serve           # documentation sur http://127.0.0.1:8000
```

## Structure

| Dossier | Origine | Rôle |
| --- | --- | --- |
| `src/` | CheckIt.AI | Extracteurs, normalisation, validation, déduplication, écriture des événements |
| `config/` | CheckIt.AI | Paramètres et contrat des sources (`sources.yaml`) |
| `dags/` | SortirIA | DAG Airflow nocturne qui lance l'agent de collecte |
| `sql/sortiria/` | SortirIA | Schéma PostgreSQL + pgvector (voir `docs/base_de_donnees.md`) |
| `app/` | RAG OpenAgenda | API FastAPI, filtres, recherche, génération |
| `frontend/` | Chess Agent (projet 13) | Interface Angular |
| `tests/` | les deux | Tests de la collecte, de l'agent (`tests/test_agent_collecte/`) et de l'API (`tests/test_api/`) |
