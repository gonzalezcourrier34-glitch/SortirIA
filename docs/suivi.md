# Suivi : incidents et résolutions

Chaque problème rencontré depuis le début du projet, comment il a été vu, sa
cause, sa correction et ce qui l'empêche de revenir. Journal tenu au fil de
l'eau (du plus récent au plus ancien). Version illustrée :
[dossier du projet, page Suivi](dossier/suivi.html).

## Comment un incident est traité

1. **Détecter** : mail de bilan de la nuit, workflow GitHub « Surveillance »
   toutes les 3 h, CI (tests et évaluation de la recherche à 95 %), juge de
   qualité de la nuit, rapport de dérive du lundi (questions, réponses,
   collecte), tableaux Grafana, budget Azure.
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
| 23/10/2026 | Recherche | Mistral indisponible pour la rédaction | Vérification du site en ligne après le déploiement : toutes les réponses « simplifiées » depuis 11 h environ |
| 23/10/2026 | Évaluation | L'horloge de la base de test n'était pas figée | Rejeu des 4 jeux d'évaluation sur la base figée, en préparant les corrections de la relecture : « faire soi-même » à 90 % (98 % le 19/10) sans changement de code |
| 23/10/2026 | Code | Relecture du code : défauts corrigés | Relecture complète du code pendant l'écriture des docstrings |
| 19/10/2026 | Monitoring | La dérive ajoutée : un test et une colonne en base | Test `test_full_night_success` rouge sur le poste, avant le push |
| 19/10/2026 | Recherche | Des mots de la phrase pris pour des sujets | Mail de bilan du lundi 19/10 : 64 % des questions sans réponse exacte ; l'agent propose « bonjour » → « salut » et « voire » → « ou-encore » |
| 18/10/2026 | Agent | Deux propositions de l'agent en conflit | GitHub : « This branch has conflicts » sur la pull request de la proposition 2 |
| 18/10/2026 | Recherche | Le bouton « Enfant » gardait une exposition de peintres | Mesure du nouveau bouton : 6 recherches, 90 cartes jugées « pour l'enfant » oui, limite ou non |
| 18/10/2026 | Interface | Les retours des premiers testeurs | Tests d'usage : des amis sur ordinateur et téléphone |
| 17/10/2026 | Recherche | « Un spectacle de danse » proposait un bal folk | Un témoin du jeu d'évaluation « faire soi-même », sur la vraie base |
| 17/10/2026 | Recherche | « Je veux chanter » proposait des concerts | Essai du site par l'utilisateur |
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

## Retours des tests d'usage

Ce que disent les premiers visiteurs, et ce qui en est fait. Les recherches
ratées et les pouces bas arrivent aussi, chaque lundi, dans le bilan de
l'agent.

| Date | Qui | Retour | Suite |
| --- | --- | --- | --- |
| 17/10/2026 | Moi, au téléphone | Toucher une carte ne mène pas au détail | Le détail défile sous la carte touchée ; « Retour aux résultats » |
| 17/10/2026 | Moi | « Je veux chanter » propose des concerts | Verbes de pratique, mesurés (61 % → 98 %) |
| 18/10/2026 | Amis testeurs | « Partout » peu clair | « Les deux » |
| 18/10/2026 | Amis testeurs | Pas de choix « Tout public » | Bouton « Tout public », puis Qui ? : Tout public, Famille, Enfant, Ado, Senior |
| 18/10/2026 | Amis testeurs | Offres partenaires en double sur ordinateur | « À ne pas manquer » sans les offres déjà affichées |
| 18/10/2026 | Amis testeurs | Changer le nom du site pour « sortiria-thau » | Décision (19/10) : nom gardé, le site couvre Montpellier et le bassin de Thau |
| 18/10/2026 | Moi | Offres fictives prises pour de vraies | Étiquette « Démonstration », sans faux lien |

## Le détail

### 23/10/2026 · Mistral indisponible pour la rédaction

- **Domaine** : Recherche
- **Détection** : Vérification du site en ligne après le déploiement : toutes les réponses « simplifiées » depuis 11 h environ
- **Symptôme** : La recherche trouve les bonnes cartes (8 concerts sur 8 pour « un concert ce week-end »), mais Mistral ne rédige plus : phrase toute faite, aucun token consommé. La réponse de repli disait aussi « Voici ce que j'ai trouvé » devant des idées voisines.
- **Cause** : Panne chez Mistral : `mistral-small-latest` répond « 503 Service unavailable » (`scripts/check_mistral.py`), alors que la clé, la liste des modèles et les embeddings répondent. L'erreur était avalée sans trace : rien dans les journaux pour le voir.
- **Résolution** : Échecs de la rédaction et du vecteur journalisés avec leur cause ; réponse de repli qui dit « Je n'ai rien trouvé d'exact » quand c'est le cas ; modèle de secours `SEARCH_FALLBACK_MODEL` (`mistral-medium-latest` par défaut) essayé une fois avant la réponse simplifiée, le modèle qui a répondu est écrit dans le journal.
- **Pour que ça ne revienne pas** : Tests : l'échec est journalisé avec sa cause ; le second modèle répond quand le premier renvoie 503 ; erreur remontée si aucun ne répond ; réponse de repli honnête. Diagnostic en une commande : `uv run python -m scripts.check_mistral`.
- **Trace** : `app/search_answer.py` (`write_with_mistral`, `writer_models`), `app/search_service.py`

### 23/10/2026 · L'horloge de la base de test n'était pas figée

- **Domaine** : Évaluation
- **Détection** : Rejeu des 4 jeux d'évaluation sur la base figée, en préparant les corrections de la relecture : « faire soi-même » à 90 % (98 % le 19/10) sans changement de code
- **Symptôme** : « Où danser samedi soir » comprend « sam. 17 oct. » mais ne trouve rien ; « un concert ce week-end » ne donne aucune réponse exacte. Les scores baissent d'un jour à l'autre sans changement de code.
- **Cause** : La vue `sortiria.v_upcoming_events` filtrait avec `now()`, l'heure réelle. Le mode `--jeu-test` règle l'horloge de la recherche sur l'instant de l'export (16/10/2026), mais pas celle de la base. Chaque jour, des sorties disparaissaient de la base de test. La CI aurait fini par échouer sans aucun changement de code.
- **Résolution** : Fonction SQL `sortiria.reference_now()` : le réglage de session `sortiria.maintenant` s'il existe, sinon `now()`. Elle sert à la vue et au classement de « À ne pas manquer ». En mode `--jeu-test`, `freeze_clock()` règle `sortiria.maintenant` sur l'instant de l'export. En production rien ne change. À appliquer en production par `uv run python -m scripts.init_db` avant le déploiement. Base figée, avant → après : faire soi-même 89,9 % → 98,3 % ; 125 recherches courtes 98,3 % → 98,6 % ; moments 98,9 % → 98,9 % ; 50 questions 99,3 % → 98,7 % (des sorties revenues dans la vue : écarts de données, pas de code) ; compréhension 100 % partout. « Où danser samedi soir » : 0/1 → 4/4 exact ; « un concert ce week-end » : 0/8 → 8/8 exact.
- **Pour que ça ne revienne pas** : Test `test_upcoming_view_follows_the_frozen_clock` : la vue suit l'horloge figée. Détail : [Évaluation](evaluation.md).
- **Trace** : `sql/sortiria/05_views.sql`, `scripts/jeu_test_recherche.py`

### 23/10/2026 · Relecture du code : défauts corrigés

- **Domaine** : Code
- **Détection** : Relecture complète du code pendant l'écriture des docstrings
- **Symptôme** : Rien de visible pour la plupart : défauts trouvés avant qu'ils ne gênent.
- **Cause** : Recherche : `QueryPlan.relaxed` effaçait `period_from_question`, la case « date » n'était plus cochée après relâchement ; `Understanding.is_empty`, jamais appelée. Scraper : `respect_robots_txt` et `request_delay_seconds` ignorés au téléchargement des articles (mauvais argument) ; robots.txt téléchargé même quand on ne le respecte pas. OpenAgenda : au plafond d'événements, les erreurs d'agenda étaient perdues (« réussite » au lieu de « réussite partielle »). Agent : points de sauvegarde des pull requests jamais libérés sur certains chemins ; l'arrêt des embeddings sur « 3 lots en échec de suite » comptait les échecs de toute la nuit ; un synonyme accepté ne pouvait pas être écrit hors du fichier par défaut ; erreur d'agenda pendant la découverte avalée sans trace. DATAtourisme : catégorie vide quand le premier type précis n'avait pas de libellé ; doublons : `{}` traité différemment par la fusion et par le calcul de complétude. RSS : erreur d'une source ajoutée aux erreurs courantes hors de sa portée ; chaque entrée d'agenda convertie deux fois. Sources : une source sans `enabled` était désactivée par le moteur et active pour l'exécuteur ; une configuration vide `{}` acceptée puis refusée. Code mort et doublons : `get_dataset_files`, `file_sha256`, `ApiExtractionResult`, `_to_float`, `PRICE_BANDS`, `REQUIRED_EVENT_FIELDS`, `ARTICLE_CATEGORIES` ; `JUDGE_VERSION` jamais écrite. Évaluation : la version de la consigne du juge de pertinence absente de l'historique ; `load_dataset` ne comptait pas les lignes réellement insérées.
- **Résolution** : Chaque défaut corrigé ; `Understanding.is_empty` et le code mort retirés (`to_float` de `src/utils/geo_utils.py` remplace `_to_float`) ; erreur d'agenda de la découverte journalisée ; `JUDGE_VERSION` écrite dans le bilan du juge (`collect_runs.quality`, champ `version`) ; colonne `juge_consigne` dans l'historique de la pertinence. Dans le même temps : docstrings numpy partout, une page de référence par fichier, `app/search_service.py` découpé en 7 modules.
- **Pour que ça ne revienne pas** : Chaque correction a son test ; suite complète au vert (3 856 tests ce jour-là). Les 4 jeux d'évaluation donnent les mêmes cartes qu'avant les corrections (hors effet de l'horloge).
- **Trace** : `app/query_understanding.py`, `src/extractors/`, `agent/`, `src/event/quality_judge.py`, `scripts/eval_pertinence.py`, `scripts/jeu_test_recherche.py`

### 19/10/2026 · La dérive ajoutée : un test et une colonne en base

- **Domaine** : Monitoring
- **Détection** : Test `test_full_night_success` rouge sur le poste, avant le push
- **Symptôme** : Après l'ajout du suivi de la dérive, un test de la nuit complète échoue : il attendait 14 requêtes en base, il en voit 16. Et le code ajoute une colonne `drift` à `collect_runs`, que la base Azure n'a pas encore.
- **Cause** : Le test vérifie la liste exacte des requêtes d'une nuit ; le rapport de dérive en ajoute deux. Pour la colonne : la CD déploie le code, mais ne touche jamais au schéma de la base. Sans la colonne, la nuit suivante n'aurait pas pu écrire son bilan.
- **Résolution** : Liste attendue du test mise à jour (le rapport de dérive est dans une transaction à part : la collecte continue même s'il échoue). Schéma appliqué sur Azure (procédure 7.2, « Schéma complet. ») avant le push, puis CI et CD vertes.
- **Pour que ça ne revienne pas** : L'ordre est écrit dans les procédures : d'abord le schéma sur Azure, ensuite le push. 19 tests pour la dérive, dont un sur un vrai PostgreSQL.
- **Trace** : `agent/drift.py`, `tests/test_agent_collecte/test_agent.py`, `sql/sortiria/03_collect.sql` ; commit 13f6171

### 19/10/2026 · Des mots de la phrase pris pour des sujets

- **Domaine** : Recherche
- **Détection** : Mail de bilan du lundi 19/10 : 64 % des questions sans réponse exacte ; l'agent propose « bonjour » → « salut » et « voire » → « ou-encore »
- **Symptôme** : « Quels sont les prochaines expositions de peinture ? » (posée 9 fois) répondait par des idées voisines, quand « une exposition de peinture » trouvait des expositions. « Bonjour », « propositions », « voire », « savoir » apparaissaient dans les mots cherchés absents des titres.
- **Cause** : Ces mots n'étaient pas dans la liste des mots vides : ils restaient des sujets à trouver dans les titres, aucun titre ne les contient, et la réponse n'était plus exacte. L'agent, qui apprend de ces mots absents, demandait des synonymes pour des mots de politesse.
- **Résolution** : Mots de la phrase ajoutés aux mots vides (bonjour, sont, propositions, savoir, donner…) ; « voire » lu comme « voir » ; « tableaux » désigne une exposition. L'agent écarte ces mots des recherches passées et n'accepte que des synonymes d'un seul mot simple. Les 4 pull requests de l'agent (#7 à #10) ont été fermées.
- **Pour que ça ne revienne pas** : Tests : les questions du bilan rejouées (« quels sont… », « voire des tableaux »), l'agent qui ignore « bonjour » et refuse « ou-encore ». Les quatre jeux d'évaluation donnent les mêmes résultats ; les 50 questions et les 125 recherches, les mêmes cartes.
- **Trace** : `app/query_understanding.py` (`STOPWORDS`), `agent/search_learning.py` (`subject_words`)

### 18/10/2026 · Deux propositions de l'agent en conflit

- **Domaine** : Agent
- **Détection** : GitHub : « This branch has conflicts » sur la pull request de la proposition 2
- **Symptôme** : La pull request ne peut pas être fusionnée : `config/sources.yaml` montre les marques `<<<<<<<` et `>>>>>>>`.
- **Cause** : Deux propositions de découverte d'agendas OpenAgenda, ouvertes le même matin, ajoutent chacune une ligne à la même liste (`agenda_uids`). La première fusionnée, Git ne sait pas s'il faut garder l'une, l'autre ou les deux.
- **Résolution** : Les deux lignes gardées (« 889555 », 7 événements ; « 4243496 », 3 événements) dans l'éditeur de conflits de GitHub, CI verte, puis fusion.
- **Pour que ça ne revienne pas** : Procédure : fusionner les propositions une par une, `git pull` entre deux ; un conflit sur cette liste se résout toujours en gardant les deux lignes.
- **Trace** : pull requests #5 et #6

### 18/10/2026 · Le bouton « Enfant » gardait une exposition de peintres

- **Domaine** : Recherche
- **Détection** : Mesure du nouveau bouton : 6 recherches, 90 cartes jugées « pour l'enfant » oui, limite ou non
- **Symptôme** : « Joyeux enfants de la Bourgogne, exposent » (une exposition de peintres) et « La confession d'un enfant du siècle » (une pièce) proposés avec « Enfant » coché.
- **Cause** : La règle « Enfant » acceptait le mot « enfant » dans le titre, au singulier et dans une exposition.
- **Résolution** : Le mot au pluriel seulement, pas d'exposition sans âge (« de 0 à 6 ans »), pas de « gratuit pour les enfants ». Après : 38 cartes sur 42 pour l'enfant, 4 limites (« pour enfants et adultes »), aucune erreur.
- **Pour que ça ne revienne pas** : Un test sur une vraie base garde le stage 4-11 ans et écarte l'exposition et le concert « gratuit pour les enfants » ; grille de mesure dans `benchmarks/recherche/2026-10-18_grille_enfant.csv`.
- **Trace** : `app/search_query.py` (`CHILD_CONDITION`), `docs/evaluation.md`

### 18/10/2026 · Les retours des premiers testeurs

- **Domaine** : Interface
- **Détection** : Tests d'usage : des amis sur ordinateur et téléphone
- **Symptôme** : Une case cochée par une question restait cochée pour la suivante (« avec les enfants » puis « un concert ce soir » cherchait encore pour les familles) ; offres partenaires en double (« Offres partenaires » et « À ne pas manquer ») ; « Partout » peu clair ; pas de « Tout public » ; offres fictives prises pour de vraies ; « Gratuit seulement » coupé ; au téléphone, 4 lignes de filtres et un cadre vide.
- **Cause** : L'interface ne distinguait pas une case cochée par le visiteur d'une case cochée par sa question ; le reste : libellés et mise en page pensés par le développeur, pas par un visiteur.
- **Résolution** : Une case cochée par la question ne vaut que pour elle ; Qui ? (Tout public, Famille, Enfant, Ado, Senior), Où ? (Les deux), Quand ?, Gratuit ; offres fictives annoncées « Démonstration », sans faux lien ; « À ne pas manquer » sans les offres déjà affichées ; téléphone : filtres repliés, pas de cadre vide.
- **Pour que ça ne revienne pas** : Les retours sont notés ci-dessous (« Retours des tests d'usage ») avec leur suite ; offres de démonstration reconnues par l'API (`is_demo_link`, testé).
- **Trace** : commits b0a6087 et 9b2b7a2

### 17/10/2026 · « Un spectacle de danse » proposait un bal folk

- **Domaine** : Recherche
- **Détection** : Un témoin du jeu d'évaluation « faire soi-même », sur la vraie base
- **Symptôme** : Pour « un spectacle de danse » : le Bal folk en tête ; pour « voir un spectacle de danse ce week-end » : un stage de théâtre, un atelier d'impro et une exposition, annoncés comme réponse exacte.
- **Cause** : Les bals sont rangés en « danse », comme les spectacles ; et « spectacle » acceptait n'importe quel spectacle (« Lyrica »).
- **Résolution** : « Regarder » : avec « spectacle », « voir », « assister à », un bal, un cours, un stage ne sont plus du type demandé ; « spectacle » s'efface devant un type précis. Vraie base : 67 % → 93 % de cartes correctes sur ces recherches.
- **Pour que ça ne revienne pas** : 4 recherches « regarder » dans `config/eval_pratique.yaml`, rejoué en CI ; 125 recherches et 50 questions sans régression.
- **Trace** : commits 579ba71 et 917ebb7 ; `docs/evaluation.md`

### 17/10/2026 · « Je veux chanter » proposait des concerts

- **Domaine** : Recherche
- **Détection** : Essai du site par l'utilisateur
- **Symptôme** : Concerts de chant, chorales en concert, spectacles lyriques : rien pour chanter soi-même.
- **Cause** : « chanter » n'était qu'un mot-clé : sa racine « chant » est partout dans les concerts, et le sens rapproche « chanter » de « concert ». Rien ne distinguait faire et regarder.
- **Résolution** : Verbes de pratique (chanter, danser, jouer, peindre, dessiner, sculpter, cuisiner, faire du théâtre) : le verbe devient le sujet, ce qui y répond est défini (karaoké, cours ou stage de chant…), les concerts ne comptent plus sauf « Stage de chant ». Vraie base : 61 % → 98 % de cartes correctes sur ces recherches, compréhension 35 % → 100 %.
- **Pour que ça ne revienne pas** : Jeu d'évaluation `config/eval_pratique.yaml` (avant / après par ablation, `--sans-pratique`), rejoué en CI à chaque push.
- **Trace** : commits f452b7d et d529606 ; `docs/evaluation.md`

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
- **Suite** : rattrapée ; les collectes suivantes (nuits du 18/10) ont réussi, `/api/v1/sante` dit « success ».

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
