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
| Propositions de l'agent | en attente, appliquées, refusées ; liste à décider avec le lien de la pull request ; décisions prises (par qui, en combien de temps) | `agent_proposals` |

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
| Recherche : latence, réponses sans Mistral, erreurs | tableaux Grafana (consultés, pas d'alerte envoyée) |
| Pouces bas, commentaires refusés | **mail du lundi** : pouces bas rangés par cause par Mistral (leurs mots nourrissent les propositions de l'agent), commentaires refusés à revoir (`agent.commentaires`) |
| Sortie ou partenaire mal noté | alerte dans le mail de la nuit (2/5 ou moins sur 3 notes pour une sortie, 2,5/5 sur 5 pour un partenaire) |
| Régression de la recherche | la CI échoue sous 95 % et bloque le déploiement |
| Dérive des questions, des réponses ou de la collecte | **mail du lundi** (section « Dérive ») ; une dérive forte devient une alerte ; tableau Grafana « Modèles et coûts », partie 06 |

**Pas fait, assumé** : alertes Azure Monitor sur l'API (latence, erreurs),
métriques Prometheus (retirées le 16/10/2026 : aucun service ne les produisait).
Le journal `rag_requests` et Grafana couvrent le besoin d'un service à faible
trafic ([MVP](mvp.md)).

## Dérive (fait le 19/10/2026)

SortirIA n'entraîne pas de modèle : il appelle Mistral. Ce qui peut dériver,
c'est ce qu'on lui **demande**, ce qu'il **répond** et ce que les sources lui
**apportent**. `agent/drift.py` compare chaque nuit les **7 derniers jours**
aux **28 précédents** :

| Famille | Indicateur | Ce que ça détecte |
| --- | --- | --- |
| Questions (*data drift*) | zone, public, type de sortie, moment de la journée, gratuit | les visiteurs ne demandent plus la même chose (saison, nouveau public, autre ville) |
| Réponses (*prediction drift*) | type de réponse : exacte, élargie, idées voisines, introuvable, erreur | la base répond moins bien aux demandes |
| Réponses (performance) | latence P95 hors cache, part sans Mistral, part des pouces haut | service plus lent, Mistral moins disponible, visiteurs moins satisfaits |
| Collecte (données) | part de chaque source, motifs de rejet | une source change de format ou de volume |
| Collecte (qualité) | taux de rejet, score du juge de qualité | l'enrichissement par Mistral se dégrade (*concept drift*) |

**Méthode** : PSI (Population Stability Index) pour les distributions, la
méthode qu'Evidently applique par défaut aux variables catégorielles (code du
projet 5 : `reference/p5_monitoring/.../evidently_service.py`). Elle est
recodée en quelques lignes plutôt que d'ajouter Evidently à l'image de
production. Moins de 0,10 : stable ; 0,10 à 0,25 : modérée ; au-delà : forte.
Pour les mesures, seuils fixés à l'avance (`MEASURE_RULES`) : latence P95
+25 % / +50 %, part sans Mistral +5 / +10 points, pouces haut −8 / −15
points, taux de rejet +5 / +10 points, juge −5 / −10 points.

**Faible trafic** : sous 30 questions dans la semaine (`DERIVE_MIN_QUESTIONS`)
ou 20 avis, l'indicateur est marqué « pas assez de données » au lieu de
déclencher une fausse alerte. La collecte, elle, est jugée dès 3 nuits.

**Où le voir** :

- chaque nuit : `collect_runs.drift` (rapport complet en JSON) et métriques
  MLflow `derive/<famille>/<indicateur>/psi` ;
- le lundi : section « Dérive » du mail de bilan ; une dérive **forte**
  devient une alerte ;
- Grafana, « SortirIA - Modèles et coûts », partie 06 : état, PSI de chaque
  indicateur dans le temps, dernier rapport ;
- à la demande : `uv run python -m agent.drift` (`--json` pour le rapport
  complet, `--jours-courant`, `--jours-reference`, `--minimum`).

**Que faire** (rien n'est changé automatiquement, comme pour les autres
propositions de l'agent) :

| Dérive | Réaction |
| --- | --- |
| Questions vers une zone, un public ou un type de sortie | vérifier la couverture de cette demande (mail, partie « Sorties des 7 prochains jours ») ; chercher une source ou un partenaire ; les propositions du lundi (agendas, synonymes) vont dans ce sens |
| Plus de réponses élargies, voisines ou introuvables | lire les questions sans réponse exacte (mail du lundi, Grafana « Recherche ») ; ajouter les questions au jeu d'évaluation (`config/eval_*.yaml`) puis corriger la compréhension ou les synonymes ; la CI vérifie qu'on ne recule pas |
| Latence ou part sans Mistral | état de Mistral, cache, taille de la base (`events_total`) |
| Pouces haut en baisse | pouces bas rangés par cause (mail du lundi) ; comparer les versions de consigne (Grafana, partie 03) |
| Part d'une source, motifs de rejet | lire le bilan de la source (mail de la nuit, MLflow `source/...`) : format changé, quota, document déplacé |
| Juge de qualité, taux de rejet | comparer les versions de consigne (`document_prompt_version`) dans MLflow, relire des fiches |

## MLflow : expériences et évaluations

MLflow tourne sur le poste (http://localhost:5000) ; il est **coupé sur
Azure** : la production se suit dans la base et Grafana. Il garde tout ce qui
compare des modèles ou des réglages :

| Expérience | Une exécution par | Contenu |
| --- | --- | --- |
| `sortiria-collecte` | collecte lancée sur le poste | paramètres (modèles, seuils, consigne PDF), métriques par source, coût, tokens, trace LangGraph et appels Mistral |
| `sortiria-evaluation-recherche` | `scripts.eval_recherche` | compréhension, précision, idées voisines, latences, version de la consigne |
| `sortiria-evaluation-pertinence` | `scripts.eval_pertinence --calculer` | pertinence@3 humaine ; avec `--juge` : modèle du juge, version de sa consigne (v7), accord, kappa, AC1, rejets retrouvés ; grilles en pièces jointes |
| `sortiria-versionnage` | nouvelle version de la chaîne (`scripts.versionnage`) | modèle externe `sortiria-chaine` : modèles Mistral, versions et liens des 8 consignes, jeux d'évaluation (empreinte, lien GitHub au commit), mesures rattachées |

Les deux évaluations passent par `scripts/suivi_mlflow.py` : MLflow absent
ou injoignable, l'évaluation continue (historique CSV dans `benchmarks/`).

## Versionnage : consignes, chaîne et jeux de données (fait le 26/10/2026)

SortirIA n'entraîne pas de modèle : ce qui change d'une version à l'autre,
ce sont les **consignes** envoyées à Mistral, les **modèles** choisis et les
**jeux de données** de mesure. `scripts/versionnage.py` les enregistre dans
les registres de MLflow :

| Registre | Contenu | Alias |
| --- | --- | --- |
| Prompt Registry | les 8 consignes (réponse, lecture du programme PDF, documents partenaires, enrichissement, juge de qualité, juge de pertinence v7, apprentissage des recherches et des avis) ; une version MLflow par texte différent, étiquetée avec la version du code, le modèle qui la lit et l'empreinte du texte | `production` |
| Model Registry | `sortiria-chaine`, modèle externe : modèles Mistral (rédaction, secours, vecteurs, modération, juges), version et lien `prompts:/…` de chaque consigne, empreinte de chaque jeu ; nouvelle version seulement si l'un d'eux change | `production`, `precedente` |
| Jeux de données | 10 jeux rattachés à chaque version de la chaîne : les 4 jeux de questions, la base figée de la CI, les 3 grilles de pertinence, `sources.yaml`, `synonymes.yaml` (empreinte SHA-256, lien GitHub au commit) | |

**Garde-fou en CI** : `config/versions_consignes.json` garde la version et
l'empreinte de chaque consigne ; `tests/test_api/test_versionnage.py` échoue
si un texte change sans que sa version change. Chaque réponse enregistrée
dans `rag_requests` garde aussi sa version de consigne et son modèle.

Commandes : `--verifier` (sans MLflow), `--manifeste` (après un changement de
consigne), sans option (enregistrer après un déploiement), `--evaluation
rapport.json` (rattacher une mesure à la version en service). Procédure :
dossier, procédures, 6.5.

**Pourquoi pas DVC** : les jeux pèsent environ 1 Mo et sont dans Git ; MLflow
garde leur empreinte et le lien vers le fichier au commit exact. DVC servira
quand un jeu ne tiendra plus dans Git, par exemple les avis réels pour un
fine-tuning.

## Ce qui vient des projets précédents

| Indicateur | Outil | Code d'origine |
| --- | --- | --- |
| Journal de chaque requête : question, contraintes, événements renvoyés, latence, tokens, coût | Table `rag_requests` (PostgreSQL) | `reference/p14_observabilite/src/api/services/audit.py`, `reference/p5_monitoring/app/services/prediction_logging_service.py` |
| Collecte nocturne : ajoutés, rejetés, expirés, échecs par source, durée | Table `collect_runs` | nœud `H_report` de Chess Agent |
| Tableaux de bord | Grafana | `reference/p14_observabilite/grafana-medical-triage.json` (modèle) |
| Métriques Prometheus de l'API | non repris (voir ci-dessus) | `reference/p14_observabilite/main.py` |
| Dérive | PSI recodé dans `agent/drift.py` (19/10/2026, voir « Dérive ») | `reference/p5_monitoring/app/services/analysis_services/evidently_service.py` |

## CI/CD

- `.github/workflows/ci.yml` : Ruff, tests avec un vrai PostgreSQL (pgvector), dont le contrôle des versions de consignes et les registres MLflow, image Docker, documentation MkDocs, et **évaluation de la recherche** sur une base figée (échec sous 95 %).
- `.github/workflows/surveillance.yml` : toutes les 3 h, le site, l'API, la base et la fraîcheur de la collecte, vus de l'extérieur (pas plus souvent : chaque appel réveille l'API en veille).
- `.github/workflows/cd.yml` : image Docker publiée sur GHCR, puis déploiement sur Azure à chaque push sur `main` (API, collecte de la nuit, interface ; connexion OpenID Connect sans mot de passe), voir `docs/deploiement.md`.
- `docker/Dockerfile.api` : point d'entrée `app.main:app` ; la même image sert à la collecte de la nuit (`python agent`).
