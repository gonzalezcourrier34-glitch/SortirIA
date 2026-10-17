# Suivi : incidents et résolutions

Chaque problème rencontré depuis le début du projet, comment il a été vu, sa
cause, sa correction et ce qui l'empêche de revenir. Journal tenu au fil de
l'eau (du plus récent au plus ancien). Version illustrée :
[dossier du projet, page Suivi](dossier/suivi.html).

## Comment un incident est traité

1. **Détecter** : mail de bilan de la nuit, workflow GitHub « Surveillance »
   toutes les 3 h, CI (tests et évaluation de la recherche à 95 %), juge de
   qualité de la nuit, tableaux Grafana, budget Azure.
2. **Comprendre** : journaux Log Analytics, historique de la tâche Azure,
   rejeu en local (`uv run python -m agent --essai --source <nom>`).
3. **Corriger** la donnée ou la configuration, puis le code pour que le cas
   suivant passe seul.
4. **Prouver** : un test qui rejoue la panne ; commit, CI, CD.
5. **Noter** ici, et dans les procédures quand un geste est à refaire
   ([dossier, page Procédures](dossier/procedures.html), section 08).

## Vue d'ensemble

| Date | Domaine | Incident | Vu par |
| --- | --- | --- | --- |
| 17/10/2026 | Documentation | Le calendrier de la feuille de route en erreur | Lecture de la doc publiée sur GitHub Pages |
| 17/10/2026 | Collecte | Une nuit sans bilan ni mail | Workflow GitHub « Surveillance » à 6 h 17 |
| 16/10/2026 | Code | Relecture indépendante du code de la journée | Relecture par une seconde instance qui n'avait pas écrit le code |
| 16/10/2026 | Interface | Des réponses biaisées par des filtres cochés d'avance | Revue du site en ligne, comme un visiteur |
| 16/10/2026 | Documentation | La publication sur GitHub Pages échoue en 20 secondes | Onglet Actions |
| 16/10/2026 | Monitoring | Grafana : « No data » et une base de production invisible | Ouverture des tableaux après la mise en place |
| 16/10/2026 | Sécurité | Sept secrets inutiles en production | Relecture des variables de la tâche de collecte |
| 16/10/2026 | CI | « hashFiles » refusé dans une condition de job | CI rouge au push |
| 16/10/2026 | Dépendances | 34 alertes de sécurité | Dependabot |
| 16/10/2026 | Évaluation | Un juge de pertinence qui prenait des initiatives | Accord du juge Mistral avec mon annotation |
| 16/10/2026 | Recherche | La zone oubliée quand une ville est nommée | Évaluation de la recherche |
| 16/10/2026 | Déploiement | Connexion OIDC refusée par Azure | CD rouge à l'étape « Azure login » |
| 15/10/2026 | Collecte | Le PDF de Sète agglopôle bloqué depuis Azure | Première collecte sur Azure « PARTIEL » |
| 15/10/2026 | Collecte | Une erreur d'embeddings annulait toute la nuit | Bilan de la nuit |
| 15/10/2026 | Qualité | Ce que le juge de qualité a relevé | Juge de qualité de la nuit (20 fiches tirées au hasard) |
| 15/10/2026 | Déploiement | Les erreurs de la première journée sur Azure | Ligne de commande Azure |
| 14/10/2026 | Collecte | DATAtourisme à 184 séances au lieu de près de 1 000 | Première collecte complète |
| 14/10/2026 | Collecte | Le programme PDF a changé d'adresse | Source document en échec |
| 14/10/2026 | Mistral | Erreurs 429 à la lecture des PDF | Journal de la collecte |
| 14/10/2026 | Enrichissement | Plus de la moitié des lots d'enrichissement perdus | Bilan de l'enrichissement |

## Le détail

### 17/10/2026 · Le calendrier de la feuille de route en erreur

- **Domaine** : Documentation
- **Détection** : Lecture de la doc publiée sur GitHub Pages
- **Symptôme** : « Syntax error in text » (Mermaid 11) à la place du diagramme de Gantt.
- **Cause** : Trois tâches avaient un deux-points dans leur nom (« Mesures : pertinence… ») : dans un Gantt, le deux-points sépare le nom des dates. L'analyse passait, le dessin échouait.
- **Résolution** : Deux-points retirés des noms de tâches.
- **Pour que ça ne revienne pas** : Les 7 diagrammes de la doc dessinés avec le Mermaid du site publié : tous corrects.
- **Trace** : `docs/feuille_de_route.md`

### 17/10/2026 · Une nuit sans bilan ni mail

- **Domaine** : Collecte
- **Détection** : Workflow GitHub « Surveillance » à 6 h 17 : dernière collecte réussie il y a 27,4 h (limite 26 h) ; pas de mail le matin
- **Symptôme** : La tâche Azure `sortiria-collecte` sort en « Failed » à 3 h 01, deux fois (essai et nouvel essai). Aucune ligne dans `collect_runs`, aucun mail.
- **Cause** : Une fiche DATAtourisme publiée la veille annonce « de 32 à 29 € » : prix minimum au-dessus du maximum. La base refuse la ligne (contrainte `events_price_order`) et annule la transaction, comme prévu : la base reste celle de la veille. Mais l'erreur sortait du graphe LangGraph : l'agent s'arrêtait avant le bilan.
- **Résolution** : Fourchette inversée remise dans l'ordre avant l'écriture (`event_validator.conform_to_schema`, avec la fin avant le début). `load_events` rattrape toute erreur d'écriture : la nuit continue jusqu'au bilan, notée « échec », avec l'alerte « Écriture en base impossible » et sa cause dans le mail.
- **Pour que ça ne revienne pas** : Un test rejoue la panne (base qui refuse l'écriture : statut « échec », alerte, mail envoyé). Cause trouvée en deux minutes dans Log Analytics.
- **Trace** : commit 38d4df2 ; `tests/test_agent_collecte/test_agent.py`

### 16/10/2026 · Relecture indépendante du code de la journée

- **Domaine** : Code
- **Détection** : Relecture par une seconde instance qui n'avait pas écrit le code
- **Symptôme** : Rien de visible encore : défauts trouvés avant qu'ils ne gênent.
- **Cause** : L'espace avant « : ? ! » disparaissait après un nombre (« le 31: ») ; les relances en double n'étaient pas retirées ; une fourchette « 5.5 à 8.5 € » n'était qu'à moitié convertie ; une erreur en base pendant les pull requests de l'agent pouvait annuler les décisions déjà notées ; un document partenaire pouvait être validé sans que son empreinte soit vérifiée.
- **Résolution** : Typographie et relances corrigées ; prix en fourchette ; un point de sauvegarde par proposition ; empreinte SHA-256 du document comparée à celle que Mistral a lue ; le mot de passe du compte Grafana part vers PostgreSQL déjà chiffré (SCRAM).
- **Pour que ça ne revienne pas** : Tests ajoutés pour chaque cas ; l'empreinte SCRAM est comparée à celle que calcule PostgreSQL lui-même.
- **Trace** : commit 17fb738

### 16/10/2026 · Des réponses biaisées par des filtres cochés d'avance

- **Domaine** : Interface
- **Détection** : Revue du site en ligne, comme un visiteur
- **Symptôme** : « Un concert ce soir à Montpellier » : « Je n'ai pas trouvé de concert… pour un public familial », puis deux concerts de ce soir. Relances qui sont des phrases contradictoires, tutoiement, « 25.5 € », « Montpellier · Montpellier ».
- **Cause** : « Famille » et « Ce week-end » étaient cochés par défaut ; la consigne de rédaction ne cadrait pas assez les relances ni le registre.
- **Résolution** : Aucun filtre par défaut (la question décide) ; relances contrôlées par le code (question courte, sans renvoi à une carte) ; vouvoiement et prix seulement si demandé dans la consigne (`2026-10-16.3`) ; prix au format français ; lieu non répété ; filtres repliés sur téléphone.
- **Pour que ça ne revienne pas** : Tests de mise en forme ; l'évaluation en CI vérifie que la recherche ne régresse pas.
- **Trace** : commit 479af3d

### 16/10/2026 · La publication sur GitHub Pages échoue en 20 secondes

- **Domaine** : Documentation
- **Détection** : Onglet Actions : workflow « Documentation » rouge
- **Symptôme** : Le workflow échoue à l'étape de configuration de Pages, alors que la construction MkDocs passe en CI.
- **Cause** : GitHub Pages n'était pas activé sur le dépôt (Source : « Deploy from a branch », aucune branche).
- **Résolution** : Settings → Pages → Source : « GitHub Actions », puis relance du workflow.
- **Pour que ça ne revienne pas** : Réglage noté dans `.github/workflows/docs.yml`.
- **Trace** : workflow Documentation

### 16/10/2026 · Grafana : « No data » et une base de production invisible

- **Domaine** : Monitoring
- **Détection** : Ouverture des tableaux après la mise en place
- **Symptôme** : Le panneau « Dernière collecte : statut » affiche « No data » ; les tableaux ne montrent que la base locale ; une source Prometheus pointe vers un service qui n'existe pas.
- **Cause** : Un panneau « stat » de Grafana n'affiche que les colonnes numériques si on ne nomme pas la colonne texte ; Grafana n'avait pas d'accès à la production ; la source Prometheus était un reste du projet 14.
- **Résolution** : Colonne `status` nommée ; compte PostgreSQL en lecture seule `grafana_lecture` et source « production » par défaut ; source Prometheus retirée.
- **Pour que ça ne revienne pas** : Tests : chaque panneau lit la base choisie, la production est la base par défaut, le panneau texte nomme sa colonne, le compte ne peut rien écrire.
- **Trace** : commit 6ef3aac

### 16/10/2026 · Sept secrets inutiles en production

- **Domaine** : Sécurité
- **Détection** : Relecture des variables de la tâche de collecte
- **Symptôme** : La tâche et l'API portaient des variables que le code ne lit plus, dont un vrai secret, `HF_TOKEN`.
- **Cause** : Restes de l'ancien RAG FAISS et du projet de départ.
- **Résolution** : Variables retirées, puis secrets (Azure refuse de retirer un secret encore utilisé) ; `API_KEY` gardée pour l'API.
- **Pour que ça ne revienne pas** : Principe du moindre privilège ; contrôle de l'API après le retrait.
- **Trace** : journal de déploiement, étape 13

### 16/10/2026 · « hashFiles » refusé dans une condition de job

- **Domaine** : CI
- **Détection** : CI rouge au push
- **Symptôme** : Le workflow ne démarre pas : expression non autorisée.
- **Cause** : `hashFiles` n'est pas disponible dans le `if` d'un job, seulement dans les étapes.
- **Résolution** : Condition retirée : le jeu de test est toujours commité.
- **Pour que ça ne revienne pas** : —
- **Trace** : commit 2be827c

### 16/10/2026 · 34 alertes de sécurité

- **Domaine** : Dépendances
- **Détection** : Dependabot
- **Symptôme** : 22 alertes npm (dont 3 critiques) sur l'interface, 12 sur Airflow.
- **Cause** : Versions d'Angular et d'Airflow vulnérables ; à la mise à jour, conflit de versions npm (ERESOLVE) à cause de l'ancien fichier de verrouillage.
- **Résolution** : Angular 21.2.25 (fichier de verrouillage régénéré), Airflow 3.3.2 (plancher plutôt que version figée).
- **Pour que ça ne revienne pas** : 0 alerte npm ; procédure dans le dossier (incident n° 7).
- **Trace** : commits 50cef49, 0800e35

### 16/10/2026 · Un juge de pertinence qui prenait des initiatives

- **Domaine** : Évaluation
- **Détection** : Accord du juge Mistral avec mon annotation : 68 % au premier essai
- **Symptôme** : Le juge rejetait des cartes pertinentes : « concert pas pour seniors », délai « implicite » inventé, lieu exigé partout.
- **Cause** : Calcul de dates faux, contraintes inventées, lieu jugé alors que le code le vérifie déjà ; erreurs 429 de mistral-large.
- **Résolution** : Sept versions de consigne, chacune retirant au juge ce que le code vérifie mieux (date, prix, lieu) ; pause entre les appels. v7 figée : 98,6 % d'accord (AC1 0,985), confirmé à 91,9 % sur 135 cartes nouvelles.
- **Pour que ça ne revienne pas** : Le juge ne voit plus que le sujet et le public ; historique des versions dans `scripts/eval_pertinence.py`.
- **Trace** : commits 46a7a12, b3c11a5

### 16/10/2026 · La zone oubliée quand une ville est nommée

- **Domaine** : Recherche
- **Détection** : Évaluation de la recherche : compréhension à 92 %
- **Symptôme** : « un concert à Sète » avec la case « Thau » cochée perdait la zone.
- **Cause** : `merge_with_filters` remplaçait la zone par la ville.
- **Résolution** : La zone est gardée avec la ville ; compréhension 100 %.
- **Pour que ça ne revienne pas** : Évaluation rejouée en CI à chaque push (échec sous 95 %).
- **Trace** : `docs/evaluation.md`

### 16/10/2026 · Connexion OIDC refusée par Azure

- **Domaine** : Déploiement
- **Détection** : CD rouge à l'étape « Azure login »
- **Symptôme** : `AADSTS700213: No matching federated identity record`.
- **Cause** : GitHub présente un sujet qui contient les identifiants numériques du compte et du dépôt ; aucune identité fédérée ne correspondait.
- **Résolution** : Sujet exact lu dans le journal, troisième identité fédérée ajoutée, job relancé.
- **Pour que ça ne revienne pas** : Procédure dans le dossier (incident n° 4).
- **Trace** : commit f719b10

### 15/10/2026 · Le PDF de Sète agglopôle bloqué depuis Azure

- **Domaine** : Collecte
- **Détection** : Première collecte sur Azure « PARTIEL » ; Log Analytics
- **Symptôme** : « Le document téléchargé n'est pas un PDF », en 0,1 s, aux deux essais ; depuis le poste, la même adresse renvoie le PDF.
- **Cause** : Le site sert une page web aux adresses des centres de données (protection anti-robots probable).
- **Résolution** : `SOURCES_DESACTIVEES=document` sur Azure ; programme lu depuis le poste vers la base de production (434 événements).
- **Pour que ça ne revienne pas** : Diagnostic « accès bloqué » dans l'agent ; rappel 21 jours avant la fin du programme ; 4 tests.
- **Trace** : commit 0acd00c

### 15/10/2026 · Une erreur d'embeddings annulait toute la nuit

- **Domaine** : Collecte
- **Détection** : Bilan de la nuit
- **Symptôme** : Une seule erreur d'embeddings faisait perdre toute la nuit, enrichissements payés compris.
- **Cause** : L'erreur levée dans la transaction annulait tout.
- **Résolution** : Un lot d'enrichissement ou d'embeddings en échec est journalisé et passé ; trois lots en échec de suite arrêtent les embeddings de la nuit seulement.
- **Pour que ça ne revienne pas** : Règle écrite dans `agent/README.md` : rien d'évitable ne doit lever dans la transaction.
- **Trace** : `agent/README.md`

### 15/10/2026 · Ce que le juge de qualité a relevé

- **Domaine** : Qualité
- **Détection** : Juge de qualité de la nuit (20 fiches tirées au hasard)
- **Symptôme** : « MHSC vs Nantes » dans un résumé alors que la source annonce Metz ; « ANNULÉ - Monte-Cristo » et « Aide aux devoirs » proposés ; 8 enrichissements refusés chaque nuit.
- **Cause** : Résumé qui invente un nom ; titres d'annulation et services non filtrés ; une catégorie hors liste (« dégustation »).
- **Résolution** : Un résumé qui cite un nom absent de la source est écarté ; ces titres sont rejetés à la préparation ; une catégorie hors liste devient « autre ».
- **Pour que ça ne revienne pas** : Juge chaque nuit, alerte sous 80 %.
- **Trace** : `agent/README.md`

### 15/10/2026 · Les erreurs de la première journée sur Azure

- **Domaine** : Déploiement
- **Détection** : Ligne de commande Azure
- **Symptôme** : Option réservée aux grappes de serveurs, services pas encore enregistrés sur l'abonnement, syntaxe de règle de pare-feu changée, région Europe de l'Ouest fermée aux nouveaux comptes.
- **Cause** : Abonnement neuf et documentation en retard sur la ligne de commande.
- **Résolution** : Chaque erreur notée avec sa solution dans le journal de déploiement.
- **Pour que ça ne revienne pas** : Journal de déploiement ligne par ligne.
- **Trace** : commit df3a370

### 14/10/2026 · DATAtourisme à 184 séances au lieu de près de 1 000

- **Domaine** : Collecte
- **Détection** : Première collecte complète
- **Symptôme** : Beaucoup moins de séances que prévu.
- **Cause** : Le lien « page suivante » perdait ses paramètres.
- **Résolution** : Pagination corrigée : 1 059 événements.
- **Pour que ça ne revienne pas** : Détection des baisses anormales par source (moitié du meilleur total des 7 nuits).
- **Trace** : commit 4286de4

### 14/10/2026 · Le programme PDF a changé d'adresse

- **Domaine** : Collecte
- **Détection** : Source document en échec
- **Symptôme** : Le PDF passe de `uploads/2026/08/` à `uploads/2026/09/`.
- **Cause** : Le site de l'agglopôle range ses fichiers par mois.
- **Résolution** : L'agent cherche et vérifie la nouvelle adresse, puis la propose (`remplacer_url`), appliquée après accord humain.
- **Pour que ça ne revienne pas** : Diagnostic des pannes dans l'agent (`agent/diagnosis.py`).
- **Trace** : `agent/README.md`

### 14/10/2026 · Erreurs 429 à la lecture des PDF

- **Domaine** : Mistral
- **Détection** : Journal de la collecte
- **Symptôme** : 19 appels en 6 s, 19 erreurs « Rate limit exceeded ».
- **Cause** : Quota du plan gratuit de Mistral.
- **Résolution** : Pause entre les appels (`DOCUMENT_CALL_PAUSE_S`, 3 s), nouveaux essais à 10, 20, 40 s, cache des paquets réussis.
- **Pour que ça ne revienne pas** : `scripts/check_mistral.py` pour lire les limites.
- **Trace** : `docs/sources.md`

### 14/10/2026 · Plus de la moitié des lots d'enrichissement perdus

- **Domaine** : Enrichissement
- **Détection** : Bilan de l'enrichissement
- **Symptôme** : Lots refusés.
- **Cause** : Accents et variantes (« Théâtre », « enfants ») hors de la liste fermée des catégories et des publics.
- **Résolution** : Valeurs ramenées à la liste ; une catégorie inconnue devient « autre » sans faire perdre les publics et le résumé.
- **Pour que ça ne revienne pas** : Tests de l'enrichissement.
- **Trace** : `agent/README.md`

## Ce que ces incidents ont changé

- **Être prévenu sans regarder.** Les pannes du 17/10 et du 15/10 ont été vues par une alerte (surveillance GitHub, mail partiel), pas par hasard. Chaque nouveau signal a sa voie : mail de la nuit, surveillance toutes les 3 h, CI, budget Azure.
- **Toujours finir par un bilan.** Une erreur ne doit jamais faire disparaître la trace de la nuit : la base reste celle de la veille, mais le bilan et le mail partent quand même.
- **Corriger la donnée et le code.** Chaque incident de données a donné deux corrections : la donnée remise en forme, et l'agent rendu plus robuste au cas suivant.
- **Un test par panne.** Chaque panne de code corrigée a son test qui la rejoue : elle ne peut pas revenir sans que la CI le dise.
