# SortirIA

Assistant qui recommande des sorties à Montpellier, dans ses alentours et sur le bassin de Thau (Sète, Frontignan, Mèze, Marseillan...) à partir d'une demande en langage naturel
(« un concert pas trop cher samedi soir, en extérieur »).

Projet personnel de fin de formation Ingénieur IA (OpenClassrooms), livré le 6 novembre 2026.
Il industrialise deux projets précédents :

- **CheckIt.AI** (projet 12) : moteur de collecte multi-sources réutilisable → `src/`, `config/`, `dags/`, `sql/`
  (seuls les moteurs génériques sont gardés : tout ce qui concernait les fake news a été retiré)
- **Assistant RAG OpenAgenda** (projet 6) : point de départ de l'API (`app/`) ; son RAG FAISS et son interface Streamlit ont été remplacés (recherche hybride PostgreSQL + pgvector, interface Angular `frontend/`)

Le détail de ce qui a été repris, et de ce qui reste à adapter, est dans [PROVENANCE.md](PROVENANCE.md).

## Architecture (MVP)

1. Collecte chaque nuit par un agent LangGraph (`agent/`) qui pilote le moteur CheckIt.AI : extraction, normalisation, déduplication, enrichissement et contrôle de qualité par Mistral, surveillance et propositions de réparation validées par un humain.
2. Retrait des événements passés, embeddings des nouveaux (Mistral), stockage PostgreSQL + pgvector, vocabulaire des titres.
3. Question utilisateur → compréhension (date, lieu, public, budget, type, mots-clés) → filtres SQL stricts → recherche hybride (sens + mots, fusion RRF) → élargissement au sujet si rien ne répond.
4. Réponse rédigée par Mistral, qui cite chaque événement ; offres partenaires qui répondent en tête.
5. Journalisation de chaque requête et de ce qui a manqué ; chaque lundi, l'agent en tire des propositions (synonymes, agendas à lire).

Déploiement : API FastAPI et interface Angular sur Azure Container Apps, base Azure Database for PostgreSQL,
CI/CD GitHub Actions.

## Démarrage local

```bash
cp .env.example .env          # puis renseigner les clés
uv sync
docker compose up -d db       # PostgreSQL + pgvector
uv run python -m scripts.init_db --verifier   # schéma créé au premier démarrage
uv run pytest
uv run mkdocs serve -a 127.0.0.1:8001   # documentation sur http://127.0.0.1:8001
```

## Structure

| Dossier | Origine | Rôle |
| --- | --- | --- |
| `src/` | CheckIt.AI | Extracteurs, normalisation, validation, déduplication, écriture des événements |
| `config/` | CheckIt.AI | Paramètres et contrat des sources (`sources.yaml`) |
| `dags/` | SortirIA | DAG Airflow nocturne qui lance l'agent de collecte |
| `sql/sortiria/` | SortirIA | Schéma PostgreSQL + pgvector (voir `docs/base_de_donnees.md`) |
| `agent/` | SortirIA (organisation de Chess Agent) | Agent de collecte nocturne et apprentissage des recherches ratées |
| `app/` | SortirIA (départ : RAG OpenAgenda) | API FastAPI, compréhension des questions, recherche, rédaction, avis |
| `frontend/` | Chess Agent (projet 13) | Interface Angular |
| `tests/` | les deux | Tests de la collecte, de l'agent (`tests/test_agent_collecte/`) et de l'API (`tests/test_api/`) |
