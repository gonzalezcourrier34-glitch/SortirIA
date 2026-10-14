"""Agent de collecte nocturne de SortirIA.

**Provenance** : nouveau package écrit pour SortirIA (13/10/2026). Son
organisation (état, nœuds, routage, graphe, services injectés) reprend celle
de l'agent LangGraph de Chess Agent (projet 13, `backend/app/agent/`).

Chaque nuit à 2 h, l'agent :

1. choisit les sources à collecter ;
2. lance les extracteurs (moteur CheckIt.AI), avec un nouvel essai en cas d'échec ;
3. prépare les événements (validation, déduplication) ;
4. les enregistre dans PostgreSQL ;
5. retire les événements passés ;
6. calcule les embeddings Mistral des nouveaux événements ;
7. écrit le bilan de la nuit et lève des alertes si besoin.

Lancement manuel : `uv run python -m agent` (voir `agent/__main__.py`).
"""
