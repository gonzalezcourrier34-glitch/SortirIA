# Évolutions possibles

Ce qui pourrait prolonger SortirIA après la soutenance. Rien de tout cela n'est
dans le périmètre du MVP (gel du code le 4/11/2026) : chaque piste dit ce
qu'elle apporterait, ce qu'elle demande, et pourquoi elle attend.

Les travaux déjà prévus pour le MVP (CI complète, suivi Grafana de la
recherche, limite de débit des routes publiques) sont suivis
dans [Provenance du code](provenance.md), pas ici.

## 1. Une base de lieux, à côté des événements

**Le constat** (évaluation des 125 recherches courantes, 15/10/2026) : une
partie des recherches désigne des **lieux ouverts toute l'année**, pas des
événements datés : bowling, laser game, trampoline park, padel, karting,
accrobranche, piscine, patinoire, restaurant, rooftop, bar à cocktails.
SortirIA collecte des agendas : ils n'en parlent que lors d'un événement
(tournoi, soirée). Pour ces recherches, la réponse est aujourd'hui « rien sur
ce sujet » ou des idées voisines, annoncées comme telles.

**Ce qu'il faudrait :**

- une **source de lieux**. DATAtourisme, déjà utilisé pour les événements,
  publierait aussi des lieux de loisirs, de sport et de restauration (à
  vérifier : périmètre, fraîcheur et qualité des données pour Montpellier et
  le bassin de Thau) ; OpenStreetMap en est une autre (licence ODbL, à citer) ;
- une **table `places`** (nom, type, adresse, coordonnées, horaires
  d'ouverture, contact, source et licence), avec ses embeddings et son
  vocabulaire, collectée par l'agent comme les événements, mais moins souvent
  (un lieu change peu) ;
- une **recherche sur les deux bases** : un événement quand la question en
  demande un (« ce soir », « un concert »), un lieu quand elle désigne une
  activité permanente (« un bowling ») ; la compréhension des questions dit
  déjà si un mot est un sujet de la base ;
- une **carte « lieu »** dans l'interface, sans date, avec les horaires
  d'ouverture et « ouvert maintenant » ;
- des **tests** : les recherches de lieux de `config/eval_mots.yaml` passent
  d'« idées voisines » à des réponses exactes.

**Pourquoi après :** c'est un second produit dans le premier (source, schéma,
recherche, interface, évaluation), à trois semaines du gel du code. Les offres
des **partenaires** couvrent en partie le besoin : un bowling partenaire peut
déjà publier ses soirées et tournois dans son fichier.

## 2. Le fine-tuning d'un modèle

Aujourd'hui, SortirIA n'entraîne aucun modèle : il utilise des modèles Mistral
tels quels, guidés par des consignes (enrichissement, juge, rédaction,
lecture des PDF, lecture des recherches ratées), et des règles pour comprendre
les questions. C'est un choix : sans utilisateurs ni données annotées, des
consignes testées et mesurées font mieux qu'un modèle entraîné sur trop peu
d'exemples.

**Ce qui pourrait être affiné, et avec quelles données :**

| Tâche | Aujourd'hui | Ce qu'apporterait un modèle affiné | Données d'entraînement |
| --- | --- | --- | --- |
| Enrichissement des événements (catégorie, publics, résumé) | `mistral-small` avec une consigne et des contrôles (`src/event/event_enrichment.py`) | Un modèle plus petit et moins cher, plus constant sur la liste fermée des catégories, moins de réponses refusées | Les enrichissements jugés corrects par le juge de qualité, les corrections faites à la main |
| Compréhension des questions | Règles (`app/query_understanding.py`), synonymes validés | Fautes de frappe, tournures imprévues, questions longues ; les règles restent le filet de sécurité | Les questions réelles (`rag_requests`), leur compréhension par les règles, corrigée quand elle se trompe |
| Rédaction de la réponse | `mistral-small` avec une consigne | Ton et format plus réguliers, réponses plus courtes | Réponses bien notées par les utilisateurs (avis), réponses corrigées |

**Les conditions avant de s'y mettre :**

1. **Des données réelles en quantité** : quelques centaines d'exemples au
   moins par tâche, venant des vrais utilisateurs et de l'agent, pas
   seulement des jeux d'évaluation (qui servent à mesurer, pas à entraîner).
2. **Une mesure avant / après** : elle existe déjà. Les 50 questions, les 125
   recherches courantes et le juge de qualité, avec l'historique dans
   `benchmarks/` et MLflow, permettent de comparer le modèle affiné au modèle
   actuel sur les mêmes cas.
3. **Un gain qui vaut le coût** : entraînement, hébergement ou appel du
   modèle affiné, et maintenance (le réentraîner quand les données changent).
   Le service de fine-tuning de Mistral, ses modèles et ses prix sont à
   vérifier au moment de s'y mettre.

**Pourquoi après :** SortirIA n'a pas encore d'utilisateurs ; les consignes
actuelles atteignent 93 à 99 % sur les jeux d'évaluation ; et un modèle affiné
sans données réelles apprendrait surtout les jeux d'évaluation eux-mêmes.

## 3. D'autres pistes repérées en route

- **Les fautes de frappe** (« cirqe ») : aujourd'hui rattrapées par la
  recherche par le sens et, chaque lundi, proposées comme correction par
  l'agent. Une recherche approchée (trigrammes PostgreSQL, `pg_trgm`) les
  rattraperait dès la première question.
- **Le diagnostic des pannes par Mistral** : aujourd'hui des règles ; Mistral
  pourrait lire l'erreur et choisir entre réessayer, ignorer la source cette
  nuit ou proposer une réparation, toujours validée par un humain.
- **La lecture des pages HTML des lieux** (programmes publiés en page web) :
  même principe que les PDF, rien sans adresse ni date, tout validé avant
  d'entrer en base.
- **OpenAgenda par l'API v2 et sa recherche transverse** (expérimentale,
  accès à demander) : chercher un sujet dans toute la zone en un appel, au
  lieu d'un appel par agenda.
