# Provenance du code et travail d'adaptation

Copie effectuée le 13/10/2026 depuis les dépôts locaux (fichiers suivis par git uniquement :
aucune donnée, aucun `.env`, aucun secret).

| Élément | Source | Commit d'origine |
| --- | --- | --- |
| `src/`, `config/`, `dags/`, `sql/`, `tests/` | `projet_12/checkit_ai` | 93632c5 |
| `app/`, `dashboard/`, `tests/rag/` | `Projet6_RAG/pocrag` | 18f7ce1 |
| `monitoring/` (Prometheus, Grafana), `docker/Dockerfile.api`, `.github/workflows/cd.yml` | `projet_14/medical_triage_agent` | 8f45135 |
| `.github/workflows/ci.yml` | `projet_13/chess_agent` | 5606117 |
| `reference/p5_monitoring/` : journalisation des prédictions, Evidently, tables et pages de suivi | `projet_5/pret_a_depenser` | 1c99b0a |
| `reference/p14_observabilite/` : métriques Prometheus, journal d'audit, routes santé et version | `projet_14/medical_triage_agent` | 8f45135 |
| `reference/p13_agent/` : graphe LangGraph, état, routage, nœuds | `projet_13/chess_agent` | 5606117 |

Le dossier `reference/` n'est pas importé par l'application : c'est du code source à reprendre et adapter
(ses imports pointent vers les projets d'origine). Le supprimer une fois tout repris.

Modifications faites au code copié (13/10/2026) :

- `dags/checkit_master_pipeline.py` : planifié chaque nuit à 2 h, heure de Paris (`schedule="0 2 * * *"`).
- `src/extractors/datasets/source/isot_extractor.py` : recherche des CSV insensible à la casse sous Linux (bug révélé par les tests hors Windows).
- `config/constants.py`, `config/settings.py` : logger « sortiria », User-Agent « SortirIA/0.1 ».
- `tests/conftest.py` : secrets factices, pour que les tests tournent sans clé d'API (poste neuf, CI).
- `tests/extractors/rss/` : dans CheckIt.AI, les vrais tests RSS étaient dans un dossier « rss copy » et le dossier « rss » contenait d'anciennes copies du code source. Les tests sont remis à leur place (152 tests réactivés) et les copies supprimées.
- Chaque module Python porte une mention **Provenance** dans sa docstring (projet, fichier et commit d'origine), reprise dans la documentation MkDocs.
- `.github/workflows/ci.yml` réécrit pour SortirIA ; `cd.yml` et `docker/Dockerfile.api` adaptés (branche `main`, point d'entrée `app.main:app`).

Le code est copié tel quel : il tourne encore sur des **articles de presse**. Les tâches ci-dessous
le transforment en pipeline d'**événements**.

## Semaine 1 (13-19 oct.) : collecte d'événements

- [x] Créer `src/event/` (fait le 13/10) : schéma `Event`, normalisation (textes, dates à l'heure de Paris, prix chiffrés, villes), validation avec motifs de rejet, déduplication titre + jour + lieu, préparation d'une collecte avec bilan chiffré, écriture PostgreSQL
- [x] Extracteur OpenAgenda (fait le 13/10) : `src/extractors/apis/source/openagenda_extractor.py`, branché sur le moteur API de CheckIt.AI, enregistré dans le registre et déclaré dans `config/sources.yaml`. **À tester avec ta vraie clé** : `uv run python -m src.extractors.apis.source.openagenda_extractor`
- [ ] Déclarer les sources d'événements dans `config/sources.yaml` (vidé des sources de presse ; un exemple OpenAgenda y est commenté), puis OpenAgenda, open data Montpellier, 1-2 sites autorisés
- [x] Retirer tout ce qui servait aux fake news et à la presse (fait le 13/10) : 22 extracteurs (API de presse, Google Fact Check, scrapers de fact-checking, Reddit, Mastodon, datasets ISOT, Fakeddit, FakeNewsNet, CoAID), le moteur réseaux sociaux, la classification des verdicts (`fact_check_labels.py`), les sources de presse de `config/sources.yaml`, le jeu de démonstration `sql/06_seed.sql` et la dépendance `praw`. Les moteurs génériques (API, RSS, scraper, datasets ouverts) sont conservés.
- [ ] Remplacer le modèle « article étiqueté » par le modèle « événement ». Il reste des traces du vrai / faux dans : `src/article/schema/article_schema.py` (types et valeurs d'étiquettes), `config/constants.py` (correspondance des étiquettes), `config/source_config.py` et `src/utils/filter_utils.py` (rôles `*_reference`), `src/transformers/database_transformer.py`, `src/storage/postgres/postgres_storage.py` (table `article_labels`), `sql/02_image_labels.sql` et `sql/03_features_predictions.sql`, et les DAG de transformation, chargement et qualité
- [ ] Corriger les défauts relevés à l'audit : texte complet (trafilatura), langue en code ISO, rôle des datasets
- [x] Schéma `sql/events/01_events.sql` (fait le 13/10) : tables `events` (avec pgvector, 1 024 dimensions), `collect_runs` et `rag_requests`
- [x] Schéma complet `sql/sortiria/` (fait le 14/10) : sources et licences, partenaires et imports, détail des collectes par source, avis, cache, publics (famille, soirée), statut OpenAgenda, vues pour le RAG et Grafana, purge RGPD ; script `scripts/init_db.py` ; 17 tests sur un vrai PostgreSQL. Les fichiers SQL de CheckIt.AI sont archivés dans `reference/sql_checkit/`
- [x] Retrait des événements passés (fait le 13/10) : `event_repository.delete_past_events`
- [x] Chaîne extraction → préparation → chargement → expiration → embeddings → bilan, portée par l'agent (fait le 13/10)
- [x] Filtre géographique OpenAgenda (fait le 13/10) : rayon de 20 km autour de Montpellier, après un premier audit où 2,5 % seulement des événements étaient locaux
- [x] Première collecte complète en base (14/10) : 80 événements, 80 embeddings Mistral, 6 min 09 s. Collecte de nuit limitée aux 12 agendas utiles (`agenda_uids`) au lieu de la recherche par termes (~10 000 événements lus pour 83 gardés) ; recherche conservée en mode découverte (`audit_openagenda --decouverte`). Offres d'emploi écartées (motif `pas_une_sortie`)
- [x] Zone élargie (13/10) : plusieurs zones dans `areas` — Montpellier et sa métropole (20 km) + bassin de Thau (15 km autour de 43,42 / 3,62 : Sète, Frontignan, Balaruc, Bouzigues, Mèze, Marseillan) ; termes de recherche « Sète » et « Thau » ajoutés

- [x] Deuxième source : DATAtourisme (fait le 14/10) par l'API officielle `api.datatourisme.fr/v1/entertainmentAndEvent` (le CSV de data.gouv.fr n'a ni dates ni lieux, la plateforme Diffuseur ferme). Exploration : 735 événements autour de Montpellier, 296 sur le bassin de Thau. Un `Event` par séance, heure relue dans `openingDetails`, producteur et date de mise à jour cités (`source_credit`, `source_updated_at`). Fichiers : `src/event/datatourisme_mapper.py`, `src/extractors/apis/source/datatourisme_extractor.py`, `scripts/explore_datatourisme.py`
- [x] Moteur iCal générique (fait le 14/10) : `src/extractors/ical/` (adaptateur, moteur, lecteur, sur le modèle du moteur RSS) et `src/event/ical_mapper.py` (lecteur iCal sans dépendance). Section `ical_sources` : un lieu partenaire se branche sans code. L'agent ne lance que les sources configurées. Corrections : dédoublonnage par URL qui fusionnait les séances (DATAtourisme aussi touché), longueur minimale de texte héritée de la presse (voir `docs/sources.md`)
- [ ] Passer l'extracteur OpenAgenda sur l'API v2 des événements (`/v2/agendas/{uid}/events`) après vérification dans la documentation officielle

## Semaine 2 (20-26 oct.) : RAG, API, déploiement

- [ ] Attribution des sources (Licence Ouverte 2.0) : chaque recommandation affiche « Source : OpenAgenda / DATAtourisme » et le lien de l'événement

- [x] Tout Mistral (fait le 13/10) : branche OpenAI retirée de `app/rag_service.py` et de `pyproject.toml`, modèles configurés dans `.env` (`EMBEDDING_MODEL`, `LLM_MODEL`, `EXTRACTION_MODEL`, `MODERATION_MODEL`)

- [ ] Remplacer FAISS par pgvector dans `app/rag_service.py` (19 occurrences) et `app/retrieval_service.py` (6)
- [ ] Brancher `app/filter_service.py` sur un filtre SQL plutôt que sur le corpus en mémoire
- [ ] Remplacer le chargement OpenAgenda direct de `app/document_service.py` par une lecture de la table `events`
- [ ] Extraction des contraintes par Ministral 3 8B (`EXTRACTION_MODEL`) en sortie JSON validée par Pydantic, avec règles de secours pour les dates relatives ; mesurer sa précision sur le jeu de test (cible ≥ 95 %)
- [ ] Cache exact des réponses : clé = question normalisée + date du jour + version de l'index, vidé après chaque collecte nocturne
- [ ] Mesurer coût et latence P95 pour 1 000 requêtes avec et sans cache / petit modèle (chiffres pour l'axe 5)
- [ ] Pouce haut / pouce bas : table `feedback` (créée le 14/10), route `POST /feedback`, boutons Streamlit, taux de satisfaction dans Grafana
- [ ] Journaliser chaque requête (latence, tokens, événements renvoyés) à la place de `trace_service.py` (fichier JSONL)
- [ ] Dockerfile, CI/CD GitHub Actions, déploiement Azure Container Apps
- [ ] Jeu d'évaluation de 50 questions annotées, rejoué en CI

## Semaine 3 (27 oct.-2 nov.) : production

- [x] Agent de collecte nocturne (fait le 13/10) : graphe LangGraph dans `agent/`, DAG `dags/sortiria_nightly_dag.py` (2 h chaque nuit), lancement manuel `uv run python -m agent --essai`
- [ ] Nœud d'extraction par Mistral des pages d'agenda sans données structurées (quand une source « scraper » sera branchée)
- [ ] Tâche planifiée Azure Container Apps Job (`python -m agent`)
- [ ] Écrire dans `rag_requests` depuis l'API (tables créées le 14/10), métriques Prometheus (voir `monitoring/README.md`)
- [ ] Tableau de bord : latence, coût, fraîcheur, échecs par source
- [ ] Météo (Open-Meteo) pour les sorties en extérieur
- [ ] Mise à jour de `dashboard/streamlit_app.py`

## Non repris

- Tableau de bord KPI de CheckIt.AI (`dashboard/` d'origine), notebooks, documentation MkDocs, livrables
- MLflow et RAGAS du projet RAG : à réintégrer si utile pour l'évaluation
