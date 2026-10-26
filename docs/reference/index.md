# Référence du code

Une page par fichier Python, générée par **mkdocstrings** à partir des docstrings
(style numpy : résumé, `Parameters`, `Returns`, `Raises`). Chaque module garde sa
section **Provenance** : le projet et le fichier d'origine, et ce qui a changé pour
SortirIA. Dans le code, les fonctions longues sont découpées par des commentaires
`#` qui annoncent chaque grande étape.

## API et recherche

| Module | Rôle |
| --- | --- |
| [`app`](app/__init__.md) | Package `app` : l'API FastAPI de SortirIA (routes, limite de débit, recherche). |
| [`app.config`](app/config.md) | Configuration de l'API SortirIA. |
| [`app.interface_api`](app/interface_api.md) | Routes de l'API lues par l'interface Angular (`frontend/`). |
| [`app.main`](app/main.md) | API SortirIA (FastAPI). |
| [`app.query_understanding`](app/query_understanding.md) | Comprendre la question de l'utilisateur avant de chercher. |
| [`app.rate_limit`](app/rate_limit.md) | Limite de débit des routes publiques (16/10/2026). |
| [`app.ratings_api`](app/ratings_api.md) | Routes de l'API pour les notes des clients sur les événements. |
| [`app.search_answer`](app/search_answer.md) | Rédaction de la réponse par Mistral : consigne, appel, lecture, repli. |
| [`app.search_cache`](app/search_cache.md) | Cache des réponses : une même demande, le même jour, sur la même base. |
| [`app.search_cards`](app/search_cards.md) | Mise en forme des événements pour l'interface : cartes, dates, prix, lieux. |
| [`app.search_detail`](app/search_detail.md) | Détail d'un événement et avis des visiteurs sur son lieu. |
| [`app.search_filters`](app/search_filters.md) | Contraintes cochées dans l'interface : public, zone, période, gratuit. |
| [`app.search_log`](app/search_log.md) | Suivi de la recherche : journal des demandes, pouces, santé de la collecte. |
| [`app.search_query`](app/search_query.md) | Requête SQL de la recherche : candidats, filtres, classement, élargissement. |
| [`app.search_service`](app/search_service.md) | Recherche d'événements pour l'interface : question, filtres, réponse sourcée. |
| [`app.security`](app/security.md) | Gestion de la sécurité de l'API. |

## Agent de collecte

| Module | Rôle |
| --- | --- |
| [`agent`](agent/__init__.md) | Agent de collecte nocturne de SortirIA. |
| [`agent.__main__`](agent/__main__.md) | Lancement manuel de la collecte nocturne. |
| [`agent.commentaires`](agent/commentaires.md) | Revue humaine des commentaires des visiteurs (16/10/2026). |
| [`agent.diagnosis`](agent/diagnosis.md) | Diagnostic des pannes et propositions de réparation, validées par un humain. |
| [`agent.drift`](agent/drift.md) | Surveillance de la dérive : ce que les visiteurs demandent, ce que SortirIA répond et ce que la collecte apporte changent-ils ? |
| [`agent.feedback_learning`](agent/feedback_learning.md) | Ce que l'agent tire des pouces bas de la semaine (16/10/2026). |
| [`agent.graph`](agent/graph.md) | Construction et lancement du graphe LangGraph de la collecte nocturne. |
| [`agent.nodes`](agent/nodes.md) | Nœuds de l'agent de collecte nocturne. |
| [`agent.proposals`](agent/proposals.md) | Valider ou refuser les réparations proposées par l'agent. |
| [`agent.pull_requests`](agent/pull_requests.md) | Les propositions de l'agent, validées par pull request (16/10/2026). |
| [`agent.report`](agent/report.md) | Bilan de la collecte envoyé par mail. |
| [`agent.routing`](agent/routing.md) | Décisions de routage de l'agent de collecte. |
| [`agent.search_learning`](agent/search_learning.md) | L'agent apprend de ce que les gens cherchent sans trouver. |
| [`agent.services`](agent/services.md) | Services utilisés par l'agent : extraction, base de données, embeddings. |
| [`agent.state`](agent/state.md) | État partagé par les nœuds de l'agent de collecte. |
| [`agent.tracking`](agent/tracking.md) | Suivi des exécutions de l'agent avec MLflow. |
| [`agent.watch`](agent/watch.md) | Surveillance de la collecte : ce que l'agent vérifie après le chargement. |

## Collecte : événements

| Module | Rôle |
| --- | --- |
| [`src.event`](src/event/__init__.md) | Modèle événement de SortirIA. |
| [`src.event.datatourisme_mapper`](src/event/datatourisme_mapper.md) | Conversion d'un événement de l'API DATAtourisme en `Event` SortirIA. |
| [`src.event.document_mapper`](src/event/document_mapper.md) | Lecture d'un programme (PDF) par Mistral, puis conversion en `Event`. |
| [`src.event.event_audiences`](src/event/event_audiences.md) | Publics d'un événement (« famille », « soiree ») déduits par des règles simples. |
| [`src.event.event_deduplicator`](src/event/event_deduplicator.md) | Fusion d'un même événement annoncé par plusieurs sources. |
| [`src.event.event_embedding`](src/event/event_embedding.md) | Préparation des embeddings des événements. |
| [`src.event.event_enrichment`](src/event/event_enrichment.md) | Enrichissement des événements par Mistral : catégorie unifiée, publics, résumé. |
| [`src.event.event_normalizer`](src/event/event_normalizer.md) | Nettoyage des champs d'un événement : textes, dates, prix et lieux. |
| [`src.event.event_pipeline`](src/event/event_pipeline.md) | Préparation des événements collectés : conversion, validation, déduplication. |
| [`src.event.event_ratings`](src/event/event_ratings.md) | Notes des clients sur les événements : étoiles, commentaire modéré, anonymat. |
| [`src.event.event_repository`](src/event/event_repository.md) | Écriture des événements dans PostgreSQL et retrait des événements passés. |
| [`src.event.event_schema`](src/event/event_schema.md) | Structure d'un événement SortirIA. |
| [`src.event.event_validator`](src/event/event_validator.md) | Règles qui décident si un événement est exploitable par SortirIA. |
| [`src.event.html_agenda_mapper`](src/event/html_agenda_mapper.md) | Lecture d'une page d'agenda HTML (sélecteurs CSS), puis conversion en `Event`. |
| [`src.event.ical_mapper`](src/event/ical_mapper.md) | Lecture d'un calendrier iCal (.ics) et conversion en `Event` SortirIA. |
| [`src.event.openagenda_mapper`](src/event/openagenda_mapper.md) | Conversion d'un événement brut OpenAgenda en `Event` SortirIA. |
| [`src.event.partner_mapper`](src/event/partner_mapper.md) | Conversion d'une ligne du fichier d'un partenaire en `Event`. |
| [`src.event.quality_judge`](src/event/quality_judge.md) | Juge de qualité : Mistral contrôle un échantillon d'événements chaque nuit. |
| [`src.event.rss_event_mapper`](src/event/rss_event_mapper.md) | Conversion d'une entrée de flux RSS d'agenda en `Event`. |

## Collecte : extracteurs

| Module | Rôle |
| --- | --- |
| [`src.extractor_execution_service`](src/extractor_execution_service.md) | Service d'orchestration de l'exécution des extracteurs CheckIt.AI. |
| [`src.extractors`](src/extractors/__init__.md) | Package `src/extractors`. |
| [`src.extractors.apis`](src/extractors/apis/__init__.md) | Package `src/extractors/apis`. |
| [`src.extractors.apis.api_adapter`](src/extractors/apis/api_adapter.md) | Contrat commun des adaptateurs d'API CheckIt.AI. |
| [`src.extractors.apis.api_extractor`](src/extractors/apis/api_extractor.md) | Moteur commun utilisé par les extracteurs d'API. |
| [`src.extractors.apis.source`](src/extractors/apis/source/__init__.md) | Package `src/extractors/apis/source`. |
| [`src.extractors.apis.source.datatourisme_extractor`](src/extractors/apis/source/datatourisme_extractor.md) | Extracteur des événements DATAtourisme pour SortirIA. |
| [`src.extractors.apis.source.openagenda_extractor`](src/extractors/apis/source/openagenda_extractor.md) | Extracteur OpenAgenda : récupère les événements à venir d'une zone. |
| [`src.extractors.core`](src/extractors/core/__init__.md) | Package `src/extractors/core`. |
| [`src.extractors.core.extractor_configuration`](src/extractors/core/extractor_configuration.md) | Gestion commune des configurations des extracteurs CheckIt.AI. |
| [`src.extractors.core.extractor_executor`](src/extractors/core/extractor_executor.md) | Moteur d'exécution commun des extracteurs CheckIt.AI. |
| [`src.extractors.core.extractor_models`](src/extractors/core/extractor_models.md) | Modèles de données communs utilisés par les extracteurs. |
| [`src.extractors.core.extractor_results`](src/extractors/core/extractor_results.md) | Résultats métier communs utilisés par les extracteurs. |
| [`src.extractors.core.extractor_runner`](src/extractors/core/extractor_runner.md) | Façade d'exécution des extracteurs. |
| [`src.extractors.core.extractor_service`](src/extractors/core/extractor_service.md) | Façade publique du moteur d'extraction. |
| [`src.extractors.datasets`](src/extractors/datasets/__init__.md) | Package `src/extractors/datasets`. |
| [`src.extractors.datasets.dataset_adapter`](src/extractors/datasets/dataset_adapter.md) | Contrat commun des adaptateurs de datasets CheckIt.AI. |
| [`src.extractors.datasets.dataset_engine`](src/extractors/datasets/dataset_engine.md) | Moteur commun utilisé par les extracteurs de datasets CheckIt.AI. |
| [`src.extractors.datasets.dataset_file_utils`](src/extractors/datasets/dataset_file_utils.md) | Utilitaires de fichiers pour les extracteurs de datasets CheckIt.AI. |
| [`src.extractors.datasets.source`](src/extractors/datasets/source/__init__.md) | Adaptateurs de datasets propres à SortirIA. |
| [`src.extractors.datasets.source.partner_documents`](src/extractors/datasets/source/partner_documents.md) | Documents des partenaires (PDF, Word) lus par Mistral, validés par un humain. |
| [`src.extractors.datasets.source.partner_extractor`](src/extractors/datasets/source/partner_extractor.md) | Fichiers des partenaires (tableur Excel ou CSV, PDF ou Word), importés chaque nuit. |
| [`src.extractors.documents`](src/extractors/documents/__init__.md) | Lecture de programmes PDF par Mistral (ajout SortirIA, 14/10/2026). |
| [`src.extractors.documents.document_extractor`](src/extractors/documents/document_extractor.md) | Lecteur de programmes publiés en PDF, structurés par Mistral. |
| [`src.extractors.files`](src/extractors/files/__init__.md) | Moteur commun des sources « un fichier par source » (calendriers iCal, programmes PDF). |
| [`src.extractors.files.file_adapter`](src/extractors/files/file_adapter.md) | Contrat commun des lecteurs de « sources-fichiers » (un document par source). |
| [`src.extractors.files.file_engine`](src/extractors/files/file_engine.md) | Moteur commun des « sources-fichiers » : un document téléchargé par source. |
| [`src.extractors.ical`](src/extractors/ical/__init__.md) | Lecteur générique de calendriers iCal (ajout SortirIA, 14/10/2026). |
| [`src.extractors.ical.ical_extractor`](src/extractors/ical/ical_extractor.md) | Lecteur générique de calendriers iCal (.ics). |
| [`src.extractors.rss`](src/extractors/rss/__init__.md) | Package `src/extractors/rss`. |
| [`src.extractors.rss.rss_adapter`](src/extractors/rss/rss_adapter.md) | Contrat commun des adaptateurs RSS CheckIt.AI. |
| [`src.extractors.rss.rss_engine`](src/extractors/rss/rss_engine.md) | Moteur commun d'orchestration des extractions RSS et Atom. |
| [`src.extractors.rss.rss_extractor`](src/extractors/rss/rss_extractor.md) | Implémentation technique de l'extraction RSS et Atom. |
| [`src.extractors.rss.source`](src/extractors/rss/source/__init__.md) | Adaptateurs RSS propres à SortirIA. |
| [`src.extractors.rss.source.event_rss_extractor`](src/extractors/rss/source/event_rss_extractor.md) | Flux RSS d'agenda : adaptateur « événements » pour le moteur RSS de CheckIt.AI. |
| [`src.extractors.scrapers`](src/extractors/scrapers/__init__.md) | Extracteurs HTML CheckIt.AI. |
| [`src.extractors.scrapers.scraper_adapter`](src/extractors/scrapers/scraper_adapter.md) | Contrats et adaptateur communs aux extracteurs HTML CheckIt.AI. |
| [`src.extractors.scrapers.scraper_article_utils`](src/extractors/scrapers/scraper_article_utils.md) | Extraction et construction des articles issus des scrapers CheckIt.AI. |
| [`src.extractors.scrapers.scraper_engine`](src/extractors/scrapers/scraper_engine.md) | Moteur commun utilisé par les extracteurs HTML CheckIt.AI. |
| [`src.extractors.scrapers.scraper_extractor`](src/extractors/scrapers/scraper_extractor.md) | Implémentation HTML standard des scrapers CheckIt.AI. |
| [`src.extractors.scrapers.scraper_http_utils`](src/extractors/scrapers/scraper_http_utils.md) | Outils HTTP communs aux scrapers CheckIt.AI. |
| [`src.extractors.scrapers.scraper_metadata_utils`](src/extractors/scrapers/scraper_metadata_utils.md) | Extraction des métadonnées HTML communes aux scrapers. |
| [`src.extractors.scrapers.scraper_selector_utils`](src/extractors/scrapers/scraper_selector_utils.md) | Outils BeautifulSoup communs aux scrapers CheckIt.AI. |
| [`src.extractors.scrapers.scraper_url_utils`](src/extractors/scrapers/scraper_url_utils.md) | Outils URL communs aux scrapers CheckIt.AI. |
| [`src.extractors.scrapers.source`](src/extractors/scrapers/source/__init__.md) | Adaptateurs scraper propres à SortirIA. |
| [`src.extractors.scrapers.source.agenda_scraper`](src/extractors/scrapers/source/agenda_scraper.md) | Pages d'agenda HTML : adaptateur « événements » pour le moteur scraper de CheckIt.AI. |

## Collecte : articles, nettoyage, robots

| Module | Rôle |
| --- | --- |
| [`src`](src/__init__.md) | Package `src`. |
| [`src.article`](src/article/__init__.md) | Package `src/article`. |
| [`src.article.article_utils`](src/article/article_utils.md) | Fonctions utilitaires communes aux articles. |
| [`src.article.processing`](src/article/processing/__init__.md) | Package `src/article/processing`. |
| [`src.article.processing.article_cleaner`](src/article/processing/article_cleaner.md) | Nettoyage des champs textuels des articles. |
| [`src.article.processing.article_deduplicator`](src/article/processing/article_deduplicator.md) | Déduplication des articles CheckIt.AI. |
| [`src.article.processing.article_transformer`](src/article/processing/article_transformer.md) | Transformations métier appliquées aux articles CheckIt.AI. |
| [`src.article.schema`](src/article/schema/__init__.md) | Package `src/article/schema`. |
| [`src.article.schema.article_schema`](src/article/schema/article_schema.md) | Contrat métier commun des articles CheckIt.AI. |
| [`src.article.schema.article_schema_normalizer`](src/article/schema/article_schema_normalizer.md) | Normalisation des articles vers le schéma métier CheckIt.AI. |
| [`src.article.validation`](src/article/validation/__init__.md) | Package `src/article/validation`. |
| [`src.article.validation.article_validation_policy`](src/article/validation/article_validation_policy.md) | Construction des politiques de validation des articles. |
| [`src.article.validation.article_validator`](src/article/validation/article_validator.md) | Validation métier centralisée des articles. |
| [`src.cleaner`](src/cleaner/__init__.md) | Package `src/cleaner`. |
| [`src.cleaner.html_cleaner`](src/cleaner/html_cleaner.md) | Nettoyage du contenu HTML. |
| [`src.robot`](src/robot/__init__.md) | Package `src/robot`. |
| [`src.robot.robots_utils`](src/robot/robots_utils.md) | Gestion centralisée du protocole robots.txt pour CheckIt.AI. |

## Utilitaires

| Module | Rôle |
| --- | --- |
| [`src.logger`](src/logger.md) | Configuration centralisée du système de journalisation CheckIt.AI. |
| [`src.utils`](src/utils/__init__.md) | Package `src/utils`. |
| [`src.utils.date_utils`](src/utils/date_utils.md) | Utilitaires de normalisation des dates CheckIt.AI. |
| [`src.utils.extractor_utils`](src/utils/extractor_utils.md) | Fonctions générales communes aux extracteurs CheckIt.AI. |
| [`src.utils.filter_utils`](src/utils/filter_utils.md) | Fonctions utilitaires liées aux filtres d'extraction. |
| [`src.utils.geo_utils`](src/utils/geo_utils.md) | Fonctions géographiques communes : distance GPS et zones de collecte. |
| [`src.utils.mail_utils`](src/utils/mail_utils.md) | Envoi de mails par SMTP (bibliothèque standard, sans dépendance). |
| [`src.utils.parsing_utils`](src/utils/parsing_utils.md) | Fonctions communes de conversion des valeurs. |
| [`src.utils.path_utils`](src/utils/path_utils.md) | Fonctions utilitaires liées aux chemins et noms de fichiers. |
| [`src.utils.url_utils`](src/utils/url_utils.md) | Fonctions utilitaires liées aux URL. |
| [`src.utils.value_utils`](src/utils/value_utils.md) | Utilitaires génériques de normalisation des valeurs textuelles. |
| [`src.utils.yaml_utils`](src/utils/yaml_utils.md) | Fonctions utilitaires de lecture des fichiers YAML. |

## Configuration

| Module | Rôle |
| --- | --- |
| [`config`](config/__init__.md) | Package `config`. |
| [`config.constants`](config/constants.md) | Constantes techniques partagées du projet CheckIt.AI. |
| [`config.environment`](config/environment.md) | Gestion centralisée de l'environnement et des secrets CheckIt.AI. |
| [`config.paths`](config/paths.md) | Centralise tous les chemins utilisés par le projet CheckIt.AI. Construit les chemins à partir de la racine du projet. Autorise certains répertoires à être redéfinis par l'environnement. Expose des constantes Path… |
| [`config.settings`](config/settings.md) | Centralise les paramètres opérationnels globaux de CheckIt.AI. Transforme les valeurs de l'environnement en constantes prêtes à l'emploi. Applique les bornes minimales nécessaires à leur utilisation sûre. Ne charge… |
| [`config.source_config`](config/source_config.md) | Validation centralisée de la configuration des sources CheckIt.AI. |

## Scripts

| Module | Rôle |
| --- | --- |
| [`scripts`](scripts/__init__.md) | Scripts utilitaires de SortirIA (audit, maintenance). |
| [`scripts.audit_openagenda`](scripts/audit_openagenda.md) | Audit d'une collecte OpenAgenda : les chiffres de la partie 2 du rapport. |
| [`scripts.check_mistral`](scripts/check_mistral.md) | Diagnostic du compte Mistral : une seule petite requête, limites affichées. |
| [`scripts.creer_lecteur_grafana`](scripts/creer_lecteur_grafana.md) | Crée le compte en lecture seule qu'utilise Grafana sur la base de production. |
| [`scripts.creer_modele_partenaire`](scripts/creer_modele_partenaire.md) | Crée le modèle de fichier à envoyer aux partenaires (Excel et CSV). |
| [`scripts.eval_pertinence`](scripts/eval_pertinence.md) | Mesure de la pertinence des propositions (16/10/2026). |
| [`scripts.eval_recherche`](scripts/eval_recherche.md) | Évaluation de la recherche SortirIA sur un jeu de questions. |
| [`scripts.explore_datatourisme`](scripts/explore_datatourisme.md) | Explore l'API DATAtourisme (événements) avant de la brancher. |
| [`scripts.init_db`](scripts/init_db.md) | Crée ou met à jour le schéma PostgreSQL de SortirIA. |
| [`scripts.jeu_test_recherche`](scripts/jeu_test_recherche.md) | Base de test de la recherche, pour rejouer l'évaluation en CI. |
| [`scripts.mesure_cout_latence`](scripts/mesure_cout_latence.md) | Mesure du coût et du temps de réponse de la recherche (16/10/2026). |
| [`scripts.suivi_mlflow`](scripts/suivi_mlflow.md) | Envoi d'un lancement d'évaluation dans MLflow, sans jamais faire échouer l'évaluation. |
| [`scripts.versionnage`](scripts/versionnage.md) | Versionnage des consignes, des modèles et des jeux de données dans MLflow (26/10/2026). |

## Orchestration Airflow

| Module | Rôle |
| --- | --- |
| [`sortiria_nightly_dag`](dags/sortiria_nightly_dag.md) | DAG Airflow de la collecte nocturne de SortirIA. |
