# MLOps, monitoring et suivi

Ce qui est surveillé, comment, et le code d'origine à adapter.

| Indicateur | Outil | Code d'origine |
| --- | --- | --- |
| Requêtes, latence P50 / P95, erreurs de l'API | Middleware Prometheus + Grafana | `reference/p14_observabilite/main.py`, `monitoring/` |
| Journal de chaque requête : question, contraintes extraites, événements renvoyés, latence, tokens, coût | Table `rag_requests` (PostgreSQL) | `reference/p14_observabilite/src/api/services/audit.py`, `reference/p5_monitoring/app/services/prediction_logging_service.py` |
| Collecte nocturne : événements ajoutés, rejetés, expirés, échecs par source, durée | Table `collect_runs` | `dags/checkit_quality_dag.py`, nœud `H_report` de l'agent |
| Fraîcheur : âge de la dernière collecte réussie, nombre d'événements à venir | Requête SQL + panneau Grafana | nouveau |
| Dérive des questions (thèmes, quartiers, budgets demandés) | Evidently, rapport hebdomadaire | `reference/p5_monitoring/app/services/analysis_services/evidently_service.py` |
| Qualité des réponses : respect des contraintes, pertinence des 3 premiers résultats | Jeu de 50 questions rejoué en CI + RAGAS | RAG OpenAgenda (évaluation), `tests/rag/` |
| Santé et version du service | Routes `/health` et `/version` | `reference/p14_observabilite/src/api/routes/` |
| Tableau de bord | Grafana (technique) + page Streamlit « Suivi » | `monitoring/grafana/`, `reference/p5_monitoring/dashboard/` |

## Alertes (Azure Monitor en production)

- Collecte nocturne en échec ou aucun événement ajouté.
- Latence P95 > 5 s sur une heure.
- Coût LLM journalier au-dessus du budget fixé.

## CI/CD

- `.github/workflows/ci.yml` (repris de Chess Agent) : ruff, pyright, pytest avec seuil de couverture. À adapter : chemins `backend/` → racine, ajout du rejeu de l'évaluation.
- `.github/workflows/cd.yml` (repris du triage médical) : image Docker publiée sur GHCR. À adapter : nom de l'image, puis déploiement Azure Container Apps.
- `docker/Dockerfile.api` (repris du triage médical) : à adapter au point d'entrée `app.main:app`.
- `monitoring/grafana/provisioning/dashboards/medical-triage.json` : tableau de bord du triage, à renommer et à adapter aux indicateurs ci-dessus.
