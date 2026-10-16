# Feuille de route

*Mise à jour le 16/10/2026.* Le détail, tâche par tâche, avec le code repris
de chaque projet, est dans [Provenance du code](provenance.md).

## Jalons

| Date | Jalon | État |
| --- | --- | --- |
| 13/10/2026 | Démarrage : reprise de CheckIt.AI, du RAG OpenAgenda, de Chess Agent, du triage médical | ✅ |
| 14/10/2026 | Première collecte complète, schéma définitif, interface | ✅ |
| 15/10/2026 | Recherche évaluée (98,7 %), **mise en production sur Azure** | ✅ |
| 16/10/2026 | Déploiement automatique, suivi de la recherche, limite de débit, mesure du coût | ✅ |
| 4/11/2026 | **Gel du code** | ⏳ |
| 6/11/2026 | **Soutenance** | ⏳ |

## Les blocs

```mermaid
gantt
    title SortirIA, du 13/10 au 6/11/2026
    dateFormat YYYY-MM-DD
    axisFormat %d/%m
    section Fait
    Bloc 1 Collecte multi-sources         :done, b1, 2026-10-13, 3d
    Bloc 2 Base et agent nocturne         :done, b2, 2026-10-14, 2d
    Bloc 3 Recherche et interface         :done, b3, 2026-10-14, 3d
    Bloc 4 Production et suivi            :done, b4, 2026-10-15, 2d
    section À faire
    Mesures : pertinence, petit modèle    :done, m1, 2026-10-16, 1d
    Corrections : partenaires, ville      :active, c1, 2026-10-17, 3d
    Évaluation rejouée en CI              :m2, 2026-10-19, 2d
    Fiabilisation d'après l'usage réel    :f1, 2026-10-20, 7d
    Nettoyage et documentation            :d1, 2026-10-24, 6d
    Soutenance : diapos et répétitions    :s1, 2026-10-27, 8d
    Gel du code                           :milestone, g1, 2026-11-04, 0d
    Soutenance                            :milestone, g2, 2026-11-06, 0d
```

### Bloc 1 : collecte multi-sources (fait)

Moteur multi-sources de CheckIt.AI adapté aux événements ; OpenAgenda (12
agendas utiles, découverte des nouveaux le dimanche), DATAtourisme (API
officielle), programme PDF de Sète agglopôle lu par Mistral, fichiers des
partenaires ; moteurs iCal, RSS et agendas en ligne prêts. Validation,
normalisation, déduplication, fusion entre sources. **≈ 2 450 événements**.

### Bloc 2 : base et agent nocturne (fait)

PostgreSQL + pgvector, schéma versionné ; agent LangGraph : réessai ciblé,
enrichissement et juge Mistral, surveillance (fraîcheur, couverture, coût),
diagnostic et réparations proposées, bilan par mail, MLflow, Grafana ;
apprentissage des recherches ratées (synonymes, agendas proposés, validation
humaine).

### Bloc 3 : recherche et interface (fait)

Compréhension par règles, recherche hybride sous filtres SQL, élargissement
au sujet, offres partenaires en tête quand elles répondent, rédaction sourcée
par Mistral, cache ; interface Angular (réponse au fil de l'eau, cartes,
détail, avis, pouce haut / bas) ; évaluation sur 50 questions et 125
recherches courtes.

### Bloc 4 : production et suivi (fait, sauf le rejeu en CI)

Azure (Static Web Apps, Container Apps, PostgreSQL Flexible Server), domaine
`www.sortiria-montpellier.fr`, CI/CD avec déploiement à chaque push, limite
de débit, journal des questions, tableau Grafana de la recherche, mesure du
coût et du temps de réponse. **Reste** : rejouer l'évaluation en CI.

### Bloc 5 : sources non structurées (commencé le 16/10)

**Fait** : les PDF et documents Word des partenaires, mis en page librement,
sont lus par Mistral, rangés dans les colonnes du modèle, contrôlés, et
publiés après validation humaine ([Partenaires](partenaires.md)).

**Après la soutenance** : lecture par Mistral des pages HTML des lieux (même principe que les PDF :
titre et date vérifiés dans la page), quand une source le demandera ; base de
lieux ; fine-tuning quand il y aura des données réelles
([Évolutions possibles](evolutions.md)).

## D'ici le gel du code (4/11)

Priorité : **un moteur fiable, mesuré et démontrable**. Plus de nouvelle
fonction ; seulement ce qui mesure, fiabilise ou explique.

| Semaine | Tâches | Livrable |
| --- | --- | --- |
| 16/10 ✅ | Grille de pertinence annotée (50 questions × 3 cartes), juge calibré (v7, figé) ; mesure avec `ministral-8b` (non retenu) | pertinence@3 97,8 %, accord juge / humain 98,6 % |
| 16/10 ✅ | Offres partenaires en tête seulement si type et public correspondent ; « à Montpellier » = la ville | corrections testées (3 631 tests) |
| 17-19/10 | `eval_recherche` rejoué ; seconde grille de pertinence pour confirmer le juge | contraintes ≥ 95 %, accord du juge sur des cartes nouvelles |
| 19-21/10 | Évaluation des contraintes rejouée en CI (sans rédaction, une base de test peuplée) | job CI qui échoue sous 95 % |
| 20-26/10 | Lecture des premiers pouces bas et des recherches ratées ; corrections ciblées ; restes du modèle « article » de CheckIt.AI | bilan d'usage, corrections testées |
| 24-30/10 | Relecture de la documentation, schémas, captures | documentation à jour |
| 27/10-3/11 | Diapositives, scénario de démonstration, vidéo de secours, répétitions ; décision sur les partenaires d'exemple (présentés comme fictifs) | support de soutenance |
| 4/11 | Gel du code : dernier déploiement, étiquette git `v1.0` | version présentée |

## Ce qui est volontairement hors du MVP

Voir [MVP et arbitrages](mvp.md) : extraction des contraintes par un modèle
(abandonnée : les règles suffisent), scraping sans autorisation, météo,
Prometheus, base de lieux, fine-tuning.
