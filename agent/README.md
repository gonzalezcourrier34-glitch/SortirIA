# Agent de collecte nocturne

Chaque nuit à 2 h (heure de Paris), l'agent met à jour la base d'événements pour les recherches du lendemain.
Il est déclenché par le DAG Airflow `checkit_master_pipeline` en local, et par une tâche planifiée
Azure Container Apps Job en production (même cron : `0 2 * * *`).

Organisation reprise du graphe LangGraph de Chess Agent (`reference/p13_agent/backend/app/agent/`) :

| Fichier | Rôle |
| --- | --- |
| `state.py` | État partagé entre les nœuds |
| `services.py` | Services injectés : extraction (moteur CheckIt.AI), PostgreSQL, embeddings Mistral |
| `nodes.py` | Les étapes : planification, extraction, préparation, chargement, bilan |
| `routing.py` | Les décisions : continuer, réessayer, ou aller au bilan |
| `graph.py` | L'enchaînement des nœuds et `run_nightly_collection()` |
| `__main__.py` | Lancement manuel : `uv run python -m agent` (`--essai` : sans écrire en base) |

Planification : `dags/sortiria_nightly_dag.py` (Airflow, 2 h chaque nuit).

## État au 13/10/2026

Implémenté et testé (`tests/test_agent_collecte/`) : planification, extraction avec nouvel essai,
préparation, chargement, retrait des événements passés, embeddings Mistral, bilan et alertes.
À venir : le nœud d'extraction par Mistral des pages d'agenda sans données structurées
(utile dès qu'une source de type « scraper » sera branchée).

## Pourquoi un agent et pas un simple pipeline

Les sources structurées (API OpenAgenda, open data) passent par les extracteurs déterministes de CheckIt.AI.
L'agent n'intervient que là où un pipeline fixe ne suffit pas :

- une page d'agenda sans données structurées : le LLM extrait les événements au format du schéma `Event`,
  et la sortie est validée par Pydantic avant d'entrer en base ;
- une source en échec : nouvel essai, puis source suivante, et alerte si l'échec dure deux nuits ;
- un rapport de fin de collecte lisible, écrit à partir des métriques de la nuit.

## Graphe cible

| Nœud | Rôle | Réutilise |
| --- | --- | --- |
| A_plan_sources | Lit `config/sources.yaml` et l'état des dernières collectes, choisit les sources à traiter | `config/source_config.py` |
| B_extract | Lance l'extracteur de chaque source | `src/extractors/` |
| C_route | Données structurées → E ; page brute → D ; échec → nouvel essai ou source suivante | `reference/p13_agent/.../routing.py` |
| D_llm_extract | Mistral transforme une page brute en liste d'`Event` (JSON validé) | `app/` (client Mistral du RAG) |
| E_validate_dedupe | Validation, normalisation, déduplication titre + date + lieu | `src/article/` → `src/event/` |
| F_expire | Retire les événements passés | nouveau |
| G_embed_load | Embeddings des nouveaux événements, chargement PostgreSQL + pgvector | `src/storage/postgres/` |
| H_report | Écrit la ligne de la nuit dans `collect_runs`, alerte si besoin | `reference/p5_monitoring/` |

## Garde-fous

- Le LLM ne crée jamais un événement sans URL source ni date ; tout événement rejeté est compté.
- Budget de tokens par nuit plafonné et journalisé.
- robots.txt et CGU respectés (`src/robot/robots_utils.py`).
