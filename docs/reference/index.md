# Référence du code

Ces pages sont générées automatiquement à partir des docstrings des modules.
Chaque module se termine par une mention **Provenance** qui indique le projet
et le fichier d'origine, et ce qui a été modifié pour SortirIA le cas échéant.

| Page | Contenu | Origine |
| --- | --- | --- |
| [Recherche et API](rag.md) | `app/` | Nouveau (point de départ : Assistant RAG OpenAgenda, projet 6) |
| [Configuration](config.md) | `config/` | CheckIt.AI (projet 12) |
| [Collecte - extracteurs](extracteurs.md) | `src/extractors/`, `src/robot/` | CheckIt.AI (projet 12) |
| [Collecte - traitement](traitement.md) | `src/event/` (nouveau), `src/article/`, `src/cleaner/` | CheckIt.AI (projet 12) |
| [Collecte - utilitaires](stockage.md) | `src/utils/`, `src/logger.py` | CheckIt.AI (projet 12) |
| [Agent de collecte](agent.md) | `agent/` | Nouveau (organisation reprise de Chess Agent, projet 13) |
| [Orchestration Airflow](dags.md) | `dags/` | Nouveau (DAG nocturne qui lance l'agent) |
