# SortirIA

**SortirIA recommande des sorties à Montpellier, dans ses alentours et sur le bassin de Thau à partir d'une demande en langage naturel**,
par exemple : « un concert pas trop cher samedi soir, en extérieur ».

C'est mon projet personnel de fin de formation Ingénieur IA (OpenClassrooms), à présenter
le 6 novembre 2026. Plutôt que de repartir de zéro, il **industrialise deux projets précédents** :

| Brique | Projet d'origine | Ce qu'elle apporte |
| --- | --- | --- |
| Collecte des données | CheckIt.AI (projet 12) | Moteur multi-sources réutilisable : un moteur par type de source, un adaptateur et une configuration YAML par fournisseur, validation, déduplication, PostgreSQL, Airflow |
| Recherche et réponse | Assistant RAG OpenAgenda (projet 6) | Préfiltrage structuré, recherche sémantique, classement explicable, génération par Mistral, API FastAPI (interface Streamlit remplacée par Angular) |
| Suivi en production | Scoring crédit (projet 5), triage médical (projet 14) | Journalisation des requêtes, Evidently, Prometheus, Grafana, CI/CD |
| Agent de collecte | Chess Agent (projet 13) | Graphe LangGraph pour la collecte nocturne |

!!! info "État du projet au 13/10/2026"
    Tout ce qui servait à la détection de fake news a été retiré (sources de presse,
    datasets étiquetés, scrapers de fact-checking). Les moteurs de collecte génériques
    sont conservés ; leur modèle de données « article » doit maintenant devenir un
    modèle **événement**. Les étapes sont planifiées semaine par semaine dans
    [Provenance du code](provenance.md). Chaque module indique son origine dans sa docstring
    (voir la [référence du code](reference/index.md)).

## Ce que le MVP doit prouver

1. Les données restent à jour sans intervention : collecte chaque nuit, événements passés retirés, doublons fusionnés.
2. Les réponses respectent les contraintes demandées (date, lieu, budget) et citent leurs sources.
3. Le service tourne en production : déployé sur Azure, testé, journalisé et surveillé.
