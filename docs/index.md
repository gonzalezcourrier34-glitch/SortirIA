# SortirIA

**SortirIA recommande des sorties à Montpellier, dans ses alentours et sur le bassin de Thau à partir d'une demande en langage naturel**,
par exemple : « un concert pas trop cher samedi soir, en extérieur ».

C'est mon projet personnel de fin de formation Ingénieur IA (OpenClassrooms), à présenter
le 6 novembre 2026. Plutôt que de repartir de zéro, il **industrialise deux projets précédents** :

| Brique | Projet d'origine | Ce qu'elle apporte |
| --- | --- | --- |
| Collecte des données | CheckIt.AI (projet 12) | Moteur multi-sources réutilisable : un moteur par type de source, un adaptateur et une configuration YAML par fournisseur, validation, déduplication, PostgreSQL, Airflow |
| Recherche et réponse | Assistant RAG OpenAgenda (projet 6) | Point de départ de l'API FastAPI ; remplacé par une recherche hybride PostgreSQL + pgvector (sens et mots), une compréhension des questions par règles, une rédaction sourcée par Mistral et une interface Angular |
| Suivi en production | Scoring crédit (projet 5), triage médical (projet 14) | Journalisation de chaque requête (`rag_requests`), tableaux Grafana, MLflow, CI/CD, dérive des questions, des réponses et de la collecte (`agent/drift.py` : PSI, la méthode d'Evidently, recodé ; Prometheus n'est pas repris) |
| Agent de collecte | Chess Agent (projet 13) | Graphe LangGraph : collecte nocturne, surveillance, propositions de réparation, apprentissage des recherches ratées |

!!! tip "Le dossier du projet, en site"
    Une présentation complète, page par page, pour le jury : le besoin, l'architecture,
    l'agent, la recherche, l'interface, les résultats, le déploiement, les limites et
    les évolutions. Site HTML autonome, publié avec cette documentation :
    [ouvrir le dossier du projet](dossier/index.html) (dans le dépôt :
    `docs/dossier/`).

**Le dossier du projet** : [MVP et arbitrages](mvp.md) ·
[Feuille de route](feuille_de_route.md) · [Architecture](architecture.md) ·
[Évaluation](evaluation.md) · [Fiabilité des données](fiabilite.md) ·
[Coûts et projection](couts.md) · [Déploiement](deploiement.md).

!!! info "État du projet au 16/10/2026"
    - **Collecte** : 7 types de sources (OpenAgenda, DATAtourisme, RSS, iCal, PDF lus
      par Mistral, fichiers des partenaires, agendas en ligne), pilotées chaque nuit par
      l'[agent](agent.md), qui enrichit, contrôle, surveille et propose ses réparations.
    - **Recherche** : compréhension de la question, recherche hybride, offres partenaires
      en tête quand elles répondent, élargissement au sujet plutôt que des idées sans
      rapport ; 98,7 % de propositions qui respectent les contraintes sur le jeu de
      50 questions ([Évaluation](evaluation.md)).
    - **Boucle d'apprentissage** : ce que les gens cherchent sans trouver est gardé ;
      chaque lundi, l'agent en tire des synonymes et des agendas à lire, validés par un
      humain.
    - **En production** sur Azure depuis le 15/10/2026 : https://www.sortiria-montpellier.fr,
      déployé à chaque push sur `main` ([Déploiement Azure](deploiement.md)).
    - **Suivi** : pouce haut / pouce bas sur chaque réponse, tableau Grafana de la
      recherche, limite de débit des routes publiques.
    - **Mesuré** : 0,12 $ de Mistral pour 1 000 questions, réponse en 3 s (médiane),
      0,1 s depuis le cache ([Coûts et projection](couts.md)).
    - **Pertinence** : 97,8 % des 3 premières propositions jugées pertinentes
      (139 cartes, jugement humain) ; un juge Mistral d'accord à 98,6 % avec
      l'humain la rejoue sans annotation ([Évaluation](evaluation.md)).
    - **À venir** : rejeu de l'évaluation en CI. Après la soutenance : une base de lieux
      (bowling, padel, restaurants…) et le fine-tuning d'un modèle
      ([Évolutions possibles](evolutions.md)).

    Le détail, étape par étape, est dans [Provenance du code](provenance.md). Chaque
    module indique son origine dans sa docstring (voir la [référence du code](reference/index.md)).

## Ce que le MVP doit prouver

1. Les données restent à jour sans intervention : collecte chaque nuit, événements passés retirés, doublons fusionnés.
2. Les réponses respectent les contraintes demandées (date, lieu, budget) et citent leurs sources.
3. Le service tourne en production : déployé sur Azure, testé, journalisé et surveillé.
