# MLOps, monitoring et suivi

Ce qui est surveillé, comment, et le code d'origine à adapter.

## Suivi de l'agent de collecte (fait le 14/10/2026)

Trois couches, de la plus simple à la plus détaillée :

| Couche | Ce qu'on y voit | Où |
| --- | --- | --- |
| **Mail de bilan** | Statut, chiffres, alertes, couverture de la semaine, coût de la nuit ; le lundi, recherches ratées de la semaine et propositions de l'agent (synonymes, agendas) | `agent/report.py` (`REPORT_EMAIL_TO`) |
| **Historique en base** | Une ligne par nuit (`collect_runs` : coût, tokens, couverture, durée de chaque nœud, alertes, version de la consigne PDF, lien MLflow) et une par source (`collect_run_sources`) ; vue `v_collect_health` | `sql/sortiria/03_collect.sql`, `05_views.sql` |
| **Tableau de bord Grafana** | « SortirIA - Collecte nocturne » : statut et fraîcheur, événements par source, échecs et réessais, taux de rejet, coût, durée des étapes, sorties famille / soirée par zone, journal et alertes | http://localhost:3001, `monitoring/grafana/provisioning/` |
| **MLflow** | Une exécution par nuit : paramètres (modèles, seuils, version de consigne), métriques (par source et par extracteur, qualité de la lecture des PDF, coût, tokens, couverture, durées), bilan complet (`bilan.json`) et **trace** du graphe LangGraph et de chaque appel Mistral | http://localhost:5000, expérience `sortiria-collecte`, `agent/tracking.py` |

Lancement : `docker compose up -d` démarre la base, MLflow et Grafana ; dans
le `.env`, `MLFLOW_TRACKING_URI=http://localhost:5000` et
`GRAFANA_ADMIN_PASSWORD`. Sans serveur MLflow, l'agent écrit dans
`data/mlflow/mlflow.db` ; si le serveur ne répond pas en 3 s, la nuit se fait
sans suivi (jamais d'échec de collecte à cause du suivi).

Exemples d'usage « ingénieur IA » :

- comparer deux versions de la consigne de lecture des PDF
  (`document_prompt_version`) sur `pdf/taux_rejet_llm` et
  `pdf/inventions_rattrapees` ;
- repérer une source qui se dégrade (`source/<id>/events`, échecs, réessais)
  avant qu'elle ne tombe ;
- suivre le coût par nuit et par modèle (`tokens/<modèle>/input|output`) ;
- vérifier que la promesse du service tient (`couverture/<zone>/<public>`).

Chaque requête du tableau de bord est rejouée par les tests sur un vrai
PostgreSQL (`tests/event/test_schema_postgres.py`).

| Indicateur | Outil | Code d'origine |
| --- | --- | --- |
| Requêtes, latence P50 / P95, erreurs de l'API | Middleware Prometheus + Grafana | `reference/p14_observabilite/main.py`, `monitoring/` |
| Journal de chaque requête : question, contraintes extraites, événements renvoyés, latence, tokens, coût | Table `rag_requests` (PostgreSQL) | `reference/p14_observabilite/src/api/services/audit.py`, `reference/p5_monitoring/app/services/prediction_logging_service.py` |
| Collecte nocturne : événements ajoutés, rejetés, expirés, échecs par source, durée | Table `collect_runs` | nœud `H_report` de l'agent |
| Fraîcheur : âge de la dernière collecte réussie, nombre d'événements à venir | Requête SQL + panneau Grafana | nouveau |
| Recherches ratées : sans réponse exacte, mots inconnus de la base, sujets introuvables | `rag_requests.constraints -> 'recherche'`, bilan du lundi (`fetch_search_gaps`), `uv run python -m agent --bilan-recherche` | nouveau (15/10/2026) |
| Dérive des questions (thèmes, quartiers, budgets demandés) | Evidently, rapport hebdomadaire | `reference/p5_monitoring/app/services/analysis_services/evidently_service.py` |
| Qualité des réponses : respect des contraintes, pertinence des 3 premiers résultats | Jeu de 50 questions rejoué en CI + RAGAS | `scripts/eval_recherche.py`, historique dans `benchmarks/recherche/` |
| Santé et version du service | Routes `/health` et `/version` | `reference/p14_observabilite/src/api/routes/` |
| Tableau de bord | Grafana | `monitoring/grafana/` |

## Alertes (Azure Monitor en production)

- Collecte nocturne en échec ou aucun événement ajouté.
- Latence P95 > 5 s sur une heure.
- Coût LLM journalier au-dessus du budget fixé.

## CI/CD

- `.github/workflows/ci.yml` (repris de Chess Agent) : ruff, pyright, pytest avec seuil de couverture. À adapter : chemins `backend/` → racine, ajout du rejeu de l'évaluation.
- `.github/workflows/cd.yml` (repris du triage médical) : image Docker publiée sur GHCR. À adapter : nom de l'image, puis déploiement Azure Container Apps.
- `docker/Dockerfile.api` (repris du triage médical) : à adapter au point d'entrée `app.main:app`.
- Tableau de bord du triage médical : archivé dans `reference/p14_observabilite/grafana-medical-triage.json` (modèle pour le futur tableau de l'API).
