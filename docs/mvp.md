# MVP et arbitrages

*Mis à jour le 16/10/2026. Gel du code le 4/11/2026, soutenance le 6/11/2026.*

## La promesse

> Une personne décrit son envie de sortie en français (« un concert pas trop
> cher samedi soir, en extérieur ») ; SortirIA lui propose des événements
> **réels, à venir, autour de Montpellier et du bassin de Thau**, qui
> **respectent sa demande** et **citent leur source**.

Trois conditions la rendent tenable, et ce sont elles que le MVP doit prouver :

1. **Des données fraîches sans intervention** : collecte chaque nuit,
   événements passés retirés, doublons fusionnés, pannes gérées.
2. **Des réponses justes et sourcées** : contraintes respectées, aucun
   événement inventé, source et lien sur chaque proposition.
3. **Un service en production** : déployé, testé, journalisé, surveillé, au
   coût maîtrisé.

## Le périmètre du MVP

### Indispensable : sans cela, pas de produit

| Fonction | État | Preuve |
| --- | --- | --- |
| Collecte de 2 sources ouvertes au moins (OpenAgenda, DATAtourisme) | **Fait** | 1 980 événements par nuit environ ([Sources](sources.md)) |
| Normalisation, validation, déduplication, retrait des événements passés | **Fait** | motifs de rejet comptés chaque nuit, fusion entre sources |
| Base PostgreSQL + pgvector, schéma versionné | **Fait** | `sql/sortiria/`, tests sur un vrai PostgreSQL |
| Collecte planifiée chaque nuit, sans intervention | **Fait** | Container Apps Job, bilan par mail |
| Compréhension de la question (quand, où, pour qui, budget, type) | **Fait** | 100 % de compréhension sur le jeu de 50 questions |
| Recherche hybride (sens + mots) sous filtres stricts | **Fait** | 98,7 % de propositions qui respectent les contraintes |
| Réponse rédigée qui cite ses événements, sans en inventer | **Fait** | citations vérifiées, repli sans Mistral ([Fiabilité](fiabilite.md)) |
| Interface web | **Fait** | https://www.sortiria-montpellier.fr |
| Source et lien de chaque événement affichés | **Fait** | carte et détail |
| Tests automatiques et CI | **Fait** | 3 600 tests environ, CI GitHub Actions |
| Déploiement | **Fait** | Azure, déployé à chaque push sur `main` ([Déploiement](deploiement.md)) |
| Mesure de la qualité et des coûts | **Fait** | [Évaluation](evaluation.md), [Coûts](couts.md) |

### Important : fait, mais le produit vivrait sans

| Fonction | État | Pourquoi l'avoir fait |
| --- | --- | --- |
| Agent LangGraph : enrichissement, juge de qualité, diagnostic, propositions | Fait | les décisions qui demandent un modèle de langage ([Agent](agent.md)) |
| Programme PDF lu par Mistral | Fait | première source **non structurée** : 434 événements du bassin de Thau |
| Fichiers des partenaires, offres en tête quand elles répondent | Fait | piste de modèle économique ([Coûts](couts.md)) |
| PDF et documents Word des partenaires, lus par Mistral, publiés après validation humaine | Fait (16/10) | un partenaire n'a pas à remplir le modèle ([Partenaires](partenaires.md)) |
| Avis des visiteurs sur les événements, pouce haut / bas sur les réponses | Fait | mesure de satisfaction réelle |
| Apprentissage des recherches ratées (synonymes, agendas proposés) | Fait | la base progresse avec l'usage |
| Cache des réponses, limite de débit | Fait | coût et latence bornés |
| MLflow, deux tableaux Grafana | Fait | suivi de la collecte et de la recherche |

### Abandonné ou reporté, sans dommage pour le MVP

| Fonction | Décision | Raison |
| --- | --- | --- |
| Extraction des contraintes par un petit modèle (Ministral) | **Abandonné** | les règles font 100 % sur le jeu de test, en 1 ms et sans coût ; un modèle se justifiera avec des questions réelles en nombre |
| Scraping d'agendas en ligne | **Reporté** | moteur prêt, mais aucun site sans autorisation écrite (demandes envoyées à Frontignan le 14/10 et à l'office de tourisme Archipel de Thau le 16/10) |
| Flux RSS d'agenda | **Reporté** | moteur prêt ; les flux testés sont vides ou interdits par leur `robots.txt` |
| Lecture des pages HTML des lieux par un LLM | **Reporté** (après soutenance) | même principe que le PDF ; à faire quand une source l'exige |
| Métriques Prometheus de l'API | **Reporté** | le journal `rag_requests` et Grafana couvrent le besoin |
| Météo pour les sorties en extérieur | **Abandonné** pour le MVP | utile mais hors de la promesse |
| Base de lieux : sport et loisirs ouverts toute l'année (piscine, bowling, padel, karting), restaurants | **Après soutenance**, limite voulue | un second produit ; mesuré le 16/10 : 0 à 57 % de pertinence sur ces recherches, contre 83,7 % au total ([Évolutions](evolutions.md)) |
| Fine-tuning d'un modèle | **Après soutenance** | pas encore de données réelles ([Évolutions](evolutions.md)) |
| Sur chaque carte : ajouter à l'agenda (`.ics`), itinéraire, partage, distance « à 8 km » | **Après soutenance** | du confort, pas la promesse ; court à faire ([Évolutions](evolutions.md)) |

## Critères d'acceptation

| Critère | Seuil | Mesuré | État |
| --- | --- | --- | --- |
| Propositions qui respectent les contraintes (50 questions annotées) | ≥ 95 % | 98,7 % | ✅ |
| Compréhension des questions (50 questions) | ≥ 95 % | 100 % | ✅ |
| Recherches courtes avec une réponse exacte (125 recherches) | — | 97,6 % de propositions correctes, 19 « idées voisines » annoncées comme telles | ✅ |
| Pertinence des 3 premières propositions (sujet et public, jugement humain, 50 questions) | ≥ 70 % | 97,8 % (136 cartes sur 139) | ✅ |
| Accord du juge Mistral avec l'humain (pour rejouer la pertinence sans annotation) | ≥ 85 %, AC1 ≥ 0,6 | 98,6 %, AC1 0,985 ; confirmé sur 135 cartes nouvelles : 91,9 %, AC1 0,889 | ✅ |
| Temps de réponse P95 (sans cache) | ≤ 8 s | 6,0 à 6,4 s mesuré depuis un poste ; le tableau Grafana suit la production | ✅ |
| Temps de réponse P95 (réponse en cache) | ≤ 1 s | 0,11 s | ✅ |
| Coût Mistral de la recherche | ≤ 1 $ pour 1 000 questions | 0,12 $ | ✅ |
| Fraîcheur : collecte réussie depuis moins de 24 h | chaque jour | suivi Grafana et mail | ✅ |
| Aucun événement inventé dans une réponse | 0 | citations limitées aux événements fournis, vérifiées | ✅ |
| Qualité des fiches (juge Mistral, 20 fiches par nuit) | ≥ 80 % | 89 à 94 % | ✅ |

Comment chaque mesure est faite : [Évaluation](evaluation.md).

## Risques et parades

| Risque | Probabilité | Parade en place |
| --- | --- | --- |
| Une source change de format ou d'adresse | Forte | l'agent réessaie, garde les événements de la veille, diagnostique, propose la réparation |
| Une source bloque les serveurs (cas réel : PDF de Sète agglopôle sur Azure) | Moyenne | source coupée sur le serveur (`SOURCES_DESACTIVEES`), lue depuis un poste |
| Mistral indisponible ou en quota | Moyenne | recherche sans vecteur, réponse simplifiée sans Mistral, signalée à l'écran |
| Hallucination dans la réponse ou un résumé | Moyenne | citations vérifiées, résumés contrôlés, juge de qualité ([Fiabilité](fiabilite.md)) |
| Coût qui dérape (robot, abus) | Faible | limite de débit par visiteur et plafond global : au plus 1 000 recherches par heure |
| Couverture faible (peu de sorties « soirée » à Thau) | Réelle | alerte de couverture chaque nuit ; PDF, partenaires, agendas proposés par l'agent |
| Manque de temps avant le gel | Moyenne | MVP déjà en production le 15/10 ; jusqu'au 4/11 : mesure, fiabilisation, documentation |
