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

## Suivi de la recherche (fait le 16/10/2026)

Tableau Grafana **« SortirIA - Recherche »**
(`monitoring/grafana/provisioning/dashboards/sortiria-recherche.json`, chargé
avec le premier), lu dans `rag_requests` (une ligne par question) et
`feedback` (pouce haut / pouce bas) :

| Partie | Panneaux |
| --- | --- |
| Usage et satisfaction | questions posées, part des pouces haut, latence P95 hors cache, coût Mistral moyen par question |
| Qualité des réponses | par jour : réponses exactes, élargies (une contrainte relâchée), idées voisines, sujet introuvable ; pouces haut et bas ; latence médiane et P95 ; part du cache, des réponses dégradées et des erreurs ; coût ; questions par heure |
| Ce qui manque | mots cherchés absents de la base, questions sans réponse exacte les plus fréquentes, derniers pouces bas avec « ce qui manquait » |

Les comptes sont vérifiés par un test sur un vrai PostgreSQL
(`test_search_dashboard_queries_run_and_count`).

## Modèles, coûts et avis (fait le 16/10/2026)

Tableau Grafana **« SortirIA - Modèles et coûts »**
(`sortiria-modeles-couts.json`, généré par
`monitoring/grafana/construire_tableau_modeles.py` : les requêtes y sont
lisibles et testées sur un vrai PostgreSQL, `tests/test_api/test_monitoring.py`).

| Partie | Panneaux | Données |
| --- | --- | --- |
| Coûts Mistral | coût du mois (collecte + recherche), projection de fin de mois, coût moyen d'une nuit et d'une question, coût par jour empilé | `collect_runs.cost_usd`, `rag_requests.cost_usd` (tarifs de `agent/watch.py`) |
| Modèles | tokens par modèle et par jour la nuit (enrichissement, embeddings, juge, PDF) et à la question (rédaction), tableau des modèles utilisés | `collect_runs.llm_usage`, `rag_requests.llm_model` et tokens |
| Versions et fiabilité | versions de la consigne de rédaction en service, avec latence, part sans Mistral et satisfaction de chacune ; réponses sans Mistral et erreurs ; score du juge de qualité | `rag_requests.prompt_version`, `degraded`, `status`, `feedback`, `collect_runs.quality_score` |
| Avis des visiteurs | avis sur les réponses et part des pouces haut, notes des sorties, commentaires à modérer, pouces par jour | `feedback`, `event_ratings` |

Le tableau se régénère avec
`uv run python -m monitoring.grafana.construire_tableau_modeles` (le test
échoue si le JSON n'est plus à jour).

## Suivre la production (16/10/2026)

Grafana tourne sur le poste (docker compose) et lit **deux bases** : la base
locale et la base de production Azure. La **production est la base par
défaut** ; en haut de chaque tableau, le choix **« Base »** passe à la base
locale (développement).

La production est lue par un compte **en lecture seule** (`grafana_lecture` :
`SELECT` sur le schéma `sortiria`, transactions en lecture seule, requêtes
coupées à 30 s). Mise en place, une fois :

1. avec la connexion d'administration Azure dans `DATABASE_URL` :
   `uv run python -m scripts.creer_lecteur_grafana` (le mot de passe du
   compte est demandé au clavier) ;
2. dans le `.env` : `AZURE_PG_HOST`, `AZURE_PG_DB`, `AZURE_PG_USER`,
   `AZURE_PG_PASSWORD` (voir `.env.example`) ;
3. `docker compose up -d grafana`, puis choisir « SortirIA production (Azure) »
   en haut d'un tableau.

Le pare-feu de la base n'accepte que l'adresse IP du poste : si elle change,
Grafana ne lit plus la production. Lister les règles, puis mettre à jour celle
du poste (pour les règles, le serveur se donne avec `--server-name`, et
`--name` désigne la règle) :

```powershell
Invoke-RestMethod https://api.ipify.org
az postgres flexible-server firewall-rule list --resource-group sortiria-rg --server-name sortiria-db-gonza -o table
az postgres flexible-server firewall-rule update --resource-group sortiria-rg --server-name sortiria-db-gonza --name <règle du poste> --start-ip-address <nouvelle adresse> --end-ip-address <nouvelle adresse>
```

Le mot de passe de `grafana_lecture` : lettres et chiffres seulement (un `$`
ou un `#` dans le `.env` serait mal lu par docker compose). Relancer
`scripts.creer_lecteur_grafana` le change sans rien casser.

## Coût Azure : budget avec alerte (fait le 16/10/2026)

Le coût Azure n'est pas dans la base : il se suit dans Azure. Réglé une
fois dans le portail : recherche `sortiria-rg` → **Gestion des coûts →
Budgets → Ajouter** (par Cost Management, l'étendue proposée est le compte de
facturation : passer par le groupe de ressources), budget `sortiria-mensuel` sur le
groupe de ressources `sortiria-rg`, budget mensuel de 20 €, alertes par mail
à 50 %, 80 % et 100 % du coût réel, et à 100 % du coût prévu. Le coût attendu
est d'environ 10 $ par mois ([Coûts](couts.md)).

## Alertes : ce qui existe vraiment

| Signal | Comment on est prévenu |
| --- | --- |
| Collecte en échec, source en panne, baisse anormale, couverture faible, coût de la nuit au-dessus du budget (`MISTRAL_BUDGET_NUIT_USD`), qualité sous 80 % | **mail de bilan** de chaque nuit, statut dans le sujet (`agent/report.py`) |
| Site ou API en panne, base injoignable, pas de collecte réussie depuis 26 h | **workflow « Surveillance »** de GitHub toutes les 3 h (`.github/workflows/surveillance.yml`, route `/api/v1/sante`) : en échec, GitHub envoie un mail |
| Pas de mail le matin | la nuit n'a pas abouti : lire l'exécution du job (`docs/dossier/procedures.html`, incidents) |
| Facture Azure | budget Azure ci-dessus |
| Recherche : latence, réponses sans Mistral, erreurs, pouces bas | tableaux Grafana (consultés, pas d'alerte envoyée) |
| Régression de la recherche | la CI échoue sous 95 % et bloque le déploiement |

**Pas fait, assumé** : alertes Azure Monitor sur l'API (latence, erreurs),
métriques Prometheus (retirées le 16/10/2026 : aucun service ne les produisait),
rapports de dérive Evidently. Le journal `rag_requests`
et Grafana couvrent le besoin d'un service à faible trafic
([MVP](mvp.md)).

## MLflow : expériences et évaluations

MLflow tourne sur le poste (http://localhost:5000) ; il est **coupé sur
Azure** : la production se suit dans la base et Grafana. Il garde tout ce qui
compare des modèles ou des réglages :

| Expérience | Une exécution par | Contenu |
| --- | --- | --- |
| `sortiria-collecte` | collecte lancée sur le poste | paramètres (modèles, seuils, consigne PDF), métriques par source, coût, tokens, trace LangGraph et appels Mistral |
| `sortiria-evaluation-recherche` | `scripts.eval_recherche` | compréhension, précision, idées voisines, latences, version de la consigne |
| `sortiria-evaluation-pertinence` | `scripts.eval_pertinence --calculer` | pertinence@3 humaine ; avec `--juge` : modèle du juge, version de sa consigne (v7), accord, kappa, AC1, rejets retrouvés ; grilles en pièces jointes |

Les deux évaluations passent par `scripts/suivi_mlflow.py` : MLflow absent
ou injoignable, l'évaluation continue (historique CSV dans `benchmarks/`).

## Ce qui vient des projets précédents

| Indicateur | Outil | Code d'origine |
| --- | --- | --- |
| Journal de chaque requête : question, contraintes, événements renvoyés, latence, tokens, coût | Table `rag_requests` (PostgreSQL) | `reference/p14_observabilite/src/api/services/audit.py`, `reference/p5_monitoring/app/services/prediction_logging_service.py` |
| Collecte nocturne : ajoutés, rejetés, expirés, échecs par source, durée | Table `collect_runs` | nœud `H_report` de Chess Agent |
| Tableaux de bord | Grafana | `reference/p14_observabilite/grafana-medical-triage.json` (modèle) |
| Métriques Prometheus de l'API | non repris (voir ci-dessus) | `reference/p14_observabilite/main.py` |
| Dérive (Evidently) | non repris | `reference/p5_monitoring/app/services/analysis_services/evidently_service.py` |

## CI/CD

- `.github/workflows/ci.yml` : Ruff, tests avec un vrai PostgreSQL (pgvector), image Docker, documentation MkDocs, et **évaluation de la recherche** sur une base figée (échec sous 95 %).
- `.github/workflows/surveillance.yml` : toutes les 3 h, le site, l'API, la base et la fraîcheur de la collecte, vus de l'extérieur (pas plus souvent : chaque appel réveille l'API en veille).
- `.github/workflows/cd.yml` : image Docker publiée sur GHCR, puis déploiement sur Azure à chaque push sur `main` (API, collecte de la nuit, interface ; connexion OpenID Connect sans mot de passe), voir `docs/deploiement.md`.
- `docker/Dockerfile.api` : point d'entrée `app.main:app` ; la même image sert à la collecte de la nuit (`python agent`).
