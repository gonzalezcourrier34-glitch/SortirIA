# Évaluation : comment chaque indicateur est mesuré

*Mise à jour le 16/10/2026.* Chaque mesure est **rejouable** par un script, et
chaque lancement est **gardé** (`benchmarks/`, MLflow quand le serveur répond).

## Vue d'ensemble

| Indicateur | Objectif | Résultat | Outil |
| --- | --- | --- | --- |
| Compréhension des questions | ≥ 95 % | **100 %** (50 questions) | `scripts/eval_recherche.py` |
| Respect des contraintes par les propositions | ≥ 95 % | **98,7 %** (50 questions) ; **97,6 %** (125 recherches courtes) | `scripts/eval_recherche.py` |
| Pertinence des 3 premières propositions (sujet et public) | ≥ 70 % | **97,8 %** (50 questions, 139 cartes, jugées à la main) ; **83,7 %** sur 50 recherches courtes tirées au hasard (sport et loisirs en lieux : limite voulue) | `scripts/eval_pertinence.py` |
| Accord du juge Mistral avec l'humain | ≥ 85 % et AC1 ≥ 0,6 | **98,6 %**, AC1 0,985 (grille de calibrage) ; **91,9 %**, AC1 0,889 sur 135 cartes nouvelles | `scripts/eval_pertinence.py --juge` |
| Temps de réponse (sans cache) | P95 ≤ 8 s | médiane 3,0 à 3,3 s, P95 6,0 à 6,4 s | `scripts/mesure_cout_latence.py` |
| Temps de réponse (cache) | P95 ≤ 1 s | médiane 0,10 s, P95 0,11 s | `scripts/mesure_cout_latence.py --cache` |
| Coût Mistral de la recherche | ≤ 1 $ / 1 000 | **0,12 $ / 1 000** questions | `scripts/mesure_cout_latence.py` |
| Qualité des fiches enrichies | ≥ 80 % | 89 à 94 % chaque nuit | juge Mistral (`src/event/quality_judge.py`) |
| Satisfaction des visiteurs | suivie | pouces haut / bas depuis le 16/10 | tableau Grafana « Recherche » |

## 1. Compréhension et respect des contraintes (automatique)

**Le jeu de test** : `config/eval_recherche.yaml`, 50 questions écrites à la
main, réparties en 8 familles (type de sortie ; quand ; où ; pour qui,
combien ; noms et sujets précis ; cases cochées et question ; budget, météo,
distance ; questions du quotidien). Pour chacune, on écrit **ce que la
recherche doit comprendre** (`compris` : période, communes, public, gratuit,
soir, types, budget, cadre) et, parfois, **des mots qui doivent figurer**
dans les propositions (`mots`).

**La mesure** (`uv run python -m scripts.eval_recherche`) : chaque question
passe par la vraie recherche, sur la vraie base, sans être tracée dans le
journal des vraies demandes.

1. **Compréhension** : la compréhension de la question est comparée à
   `compris`, champ par champ. Une question est comprise si **tous** les
   champs attendus sont justes.
2. **Respect des contraintes** : **chaque carte proposée** est contrôlée :
   date dans la période comprise, commune ou zone, public, gratuité, budget,
   type (catégorie, ou mot de type dans le titre), mots attendus visibles
   (titre, résumé ou lieu). Le score est la part des cartes **sans aucune
   erreur**. C'est le « 95 % de respect des contraintes ».
3. **Idées voisines** : quand la base n'a **rien d'exact** (« du théâtre
   gratuit en famille ce soir »), la recherche propose des idées voisines et
   la réponse le dit. Ces questions sont comptées à part, pas dans le score :
   la recherche n'a pas à inventer ce que la base n'a pas.

**Second jeu** : `config/eval_mots.yaml`, 125 recherches courtes comme les
tapent les visiteurs (« jazz », « brocante », « bowling »), même mesure. Il
mesure aussi ce que la base **ne couvre pas** : 19 recherches sur 125 sont des
lieux ouverts toute l'année (bowling, padel…), absents d'un agenda
d'événements ([Évolutions](evolutions.md)).

**Historique** (`benchmarks/recherche/historique.csv`), le 15/10/2026 :

| Étape | Jeu | Respect des contraintes |
| --- | --- | --- |
| Première mesure | 32 questions | 82 % |
| Réponses exactes seulement, dates sur plusieurs jours | 32 questions | 99 % |
| Jeu complet | 50 questions | 95,7 % puis **98,7 %** |
| Recherches courtes, première mesure | 100 recherches | 80,8 % |
| Type lu dans le titre, élargissement au sujet | 125 recherches | **97,6 %** |

**Rejouée à chaque push (CI, 16/10/2026).** Le job « Évaluation de la
recherche » de `.github/workflows/ci.yml` charge une base **figée**
(`tests/data/jeu_recherche.json.gz` : les sorties à venir, leurs sources et
les partenaires d'exemple, exportés par `scripts/jeu_test_recherche.py`) dans
un PostgreSQL avec pgvector, place l'horloge à l'instant de l'export et lance
les 50 questions **sans vecteur ni Mistral** (aucune clé, aucun coût). Il
échoue sous **95 %** de précision ou de compréhension, et le déploiement
attend la CI. Le jeu exclut le programme PDF de Sète agglopôle (publié avec
le code, il ne garde que des données sous licence ouverte) ; on le
réexporte quand la base a beaucoup changé :
`uv run python -m scripts.jeu_test_recherche --exporter`.

**Limite, dite franchement** : ce score dit si la recherche **se trompe de
cible**. Il ne dit pas si les propositions sont **les meilleures** : c'est la
pertinence, mesurée à part.

## 2. Pertinence des propositions (jugement humain, puis juge calibré)

« Pertinent » veut dire : *est-ce que je proposerais cette sortie à la
personne qui a posé cette question ?* C'est un jugement, donc **humain
d'abord**.

**Qui mesure quoi.** Le moment, le lieu et le budget sont des **contraintes** :
du code les vérifie (filtres SQL, et § 1 : 98,7 %). La pertinence mesure ce
que le code ne sait pas juger : **le sujet** (une lecture de contes pour « une
lecture de contes ») et **le public** (rien pour tout-petits quand on demande
« pour des ados »). Le même partage vaut pour l'humain et pour le juge.

**Protocole** (`scripts/eval_pertinence.py`) :

1. `--preparer` : les 50 questions passent par la vraie recherche (sans
   journal ni cache) ; les **3 premières cartes** de chaque réponse sont
   écrites dans une grille CSV (titre, date, lieu, catégorie, prix, public,
   résumé).
2. **Annotation** : `o` ou `n` dans la colonne `pertinent`, un commentaire
   court pour chaque `n`. Une question sans proposition compte comme 0 carte
   pertinente sur 3. Environ 140 cartes, une heure.
3. `--calculer grille.csv` : **pertinence@3** = part des cartes pertinentes
   (objectif **70 %**) ; **questions satisfaites** = part des questions avec
   au moins une carte pertinente ; détail par famille de questions.
4. `--juge` : Mistral Large juge les mêmes cartes, **une question par appel**,
   avec **une raison courte par verdict** (colonne `raison_juge`). On mesure
   l'**accord** avec l'humain, le **kappa de Cohen**, l'**AC1 de Gwet** et la
   part des rejets humains que le juge retrouve. Seuil retenu : accord ≥ 85 %
   et AC1 ≥ 0,6 ; le juge peut alors **rejouer** la mesure sans annotation,
   après chaque changement de la recherche.

**Résultat du 16/10/2026** (grille `benchmarks/pertinence/grille_2026-10-16_09h50.csv`,
annotée par l'auteur, juge v7) :

| Mesure | Humain | Juge Mistral |
| --- | --- | --- |
| pertinence@3 | **97,8 %** (136 / 139) | 96,4 % |
| questions avec au moins une carte pertinente | 100 % | — |
| accord avec l'humain | — | **98,6 %** (137 cartes sur 139) |
| AC1 de Gwet / kappa de Cohen | — | **0,985** / 0,74 |
| rejets humains retrouvés | — | **3 sur 3** |

Les **trois cartes non pertinentes** viennent toutes des **offres
partenaires** mises en tête sur un critère trop large : un spectacle de
marionnettes pour « un spectacle de danse » et pour « j'aimerais aller au
théâtre », une initiation à l'escalade **pour enfants** pour « un truc à
faire avec des ados ». Correction prévue : une offre partenaire ne passe en
tête que si son type précis et son public correspondent.

Les **deux désaccords** restants sont des cas limites, où le juge est plus
sévère que l'humain : une visite guidée **d'un musée** de Sète pour « une
visite guidée de Sète » ; « Lyrica », spectacle lyrique tout public, pour
« un spectacle pour les enfants dimanche ».

**Trouvaille hors pertinence.** Pendant l'annotation, deux cartes situées à
Mauguio et à Palavas-les-Flots sont apparues pour « des spectacles gratuits
**à Montpellier** » : quand la question nomme une ville, la recherche prend
toute la zone « Montpellier et environs ». C'est une contrainte de lieu,
notée dans la grille et ajoutée aux corrections de la recherche.

### Comment le juge a été calibré

Le juge n'a pas été réglé carte par carte : chaque version corrige une erreur
**systématique** que ses raisons rendaient visible, puis on mesure à nouveau
(`benchmarks/pertinence/historique.csv`).

| Version | Changement | Accord | AC1 |
| --- | --- | --- | --- |
| v1 | lots de 15 cartes, sans date ni prix ni public | 68 % | — |
| v2 | + date du jour, prix, public | 55 % | — |
| v3 | une question par appel, une raison par verdict | 67 % | 0,53 |
| v4 | ce que la demande ne précise pas n'est pas une contrainte | 78 % | 0,71 |
| v5 | dates converties par le code, règle « public = qui convient, pas réservé » | 90 % | 0,88 |
| v6 | le juge ne voit plus ni la date ni le prix ; Mistral Large | 88 % | 0,86 |
| **v7** | le juge ne voit plus la commune : **sujet et public seulement** | **98,6 %** | **0,985** |

Ce que les raisons ont montré : un modèle de langage **calcule mal les dates**
(« 31 oct. < 16 oct. 2026 »), **invente des contraintes** (un « délai
implicite », un lieu « Montpellier » non demandé) et lit un **prix absent**
comme payant. Ces vérifications sont donc faites par du code ; le juge garde ce
qu'il fait mieux que le code : comprendre un sujet et un public.

Entre v5 et v7, la grille humaine a aussi été **relue** à la lumière des
raisons du juge, puis **alignée** sur le même partage : 6 cartes notées « n »
pour le prix ou la commune ont été repassées à « o », la commune étant gardée
en commentaire. Ces 6 cartes relèvent des contraintes, mesurées au § 1 : les 4
cartes « payantes » viennent de 2 questions (« des spectacles gratuits à
Montpellier », « du théâtre » gratuit en famille) où la base n'a **rien de
gratuit** ; la recherche propose alors des idées voisines, **le dit** dans sa
réponse, et `eval_recherche` les compte à part (« idées voisines : 2 »).
Les deux grilles sont conservées (`…_09h50.avant_alignement.csv`).

**Limites, dites franchement.**

- L'annotateur est l'auteur du projet : biais possible vers l'indulgence. Le
  juge, qui n'a pas vu le code et se montre plutôt plus sévère, en est la
  contre-mesure.
- Avec 3 « non » sur 139, le kappa de Cohen est instable (il passe de 0,05 à
  0,74 pour quelques cartes) : l'accord brut et l'AC1 de Gwet, insensible à
  ce déséquilibre, sont plus parlants.
- Le juge a été calibré sur **cette** grille. Sa fiabilité devra être
  confirmée sur une **seconde grille** (nouvelles cartes, après les
  corrections de la recherche) ; il est figé d'ici là.
- Coût : environ 0,03 $ par passage ([Coûts](couts.md)), lancé à la main,
  jamais pendant une démonstration (il partage le quota Mistral du site).

### Seconde grille : le juge sur des cartes nouvelles (16/10/2026)

Le juge a été calibré sur la première grille ; il fallait le vérifier sur des
cartes **qu'il n'avait jamais vues**, sans le modifier (v7 figé). Deux
grilles, annotées avec la même règle (sujet et public) :

| Grille | Humain : pertinence@3 | Juge : accord | AC1 | Rejets humains retrouvés |
| --- | --- | --- | --- | --- |
| 50 recherches courtes tirées au hasard dans `config/eval_mots.yaml` (135 cartes, toutes nouvelles) | **83,7 %** (88 % des questions satisfaites) | **91,9 %** | **0,889** | 16 sur 22 |
| Les 50 questions de départ, après les corrections (139 cartes, 22 nouvelles, notes reprises) | **96,4 %** | 92,8 % | 0,922 | 1 sur 5 |

- **Le juge tient** : au-dessus du seuil (85 %, AC1 0,6) sur des cartes
  nouvelles. Ses 11 désaccords sur les recherches courtes vont dans les deux
  sens : plus indulgent sur 6 (un « Paint & Drink » pour « peinture », un
  marché à Carnon-Plage pour « plage »), plus sévère sur 5 (des expositions
  nocturnes pour « marché nocturne », un concert intitulé « Salon de musique »
  pour « salon »).
- **Les corrections ont marché** : les 3 offres partenaires jugées « non »
  ne sont plus dans les 3 premières cartes (deux vraies pièces pour
  « théâtre », un atelier théâtre ados pour « ados »).
- **Les 5 « non » de la seconde grille de départ** portent sur le **prix** :
  ce sont les idées voisines payantes des deux questions « gratuit », que la
  réponse annonce. Le juge ne voit pas le prix (contrainte vérifiée par le
  code), d'où 4 des 10 désaccords : écart attendu, pas une erreur du juge.
- **La faiblesse mesurée est voulue** : 0 à 57 % sur le sport et les loisirs
  ouverts toute l'année (piscine, VTT, billard, trampoline park). Ce sont des
  **lieux**, pas des événements datés ; SortirIA est un agenda
  ([Évolutions](evolutions.md), § 1). Les sports datés (matchs, courses,
  randonnées) sont trouvés.

Pourquoi ce protocole : un juge LLM seul se note lui-même sans garantie ; une
annotation humaine seule ne se rejoue pas. Calibrer l'un sur l'autre donne
une mesure à la fois **fondée** et **rejouable**.

## 3. Temps de réponse et coût

**La mesure** (`uv run python -m scripts.mesure_cout_latence`) : les 175
questions des deux jeux, tirées dans un ordre fixe, passent par la vraie
recherche, avec la rédaction par Mistral, sans être tracées. Pour chaque
question : temps de bout en bout, temps de chaque étape, tokens et coût.

**Résultats du 16/10/2026** (`benchmarks/cout_latence/`) :

| Mesure | Sans cache (100 puis 175 questions) | Avec cache (même question le même jour) |
| --- | --- | --- |
| Médiane | 3,0 à 3,3 s | 0,10 s |
| Rédaction (`mistral-small`) en moyenne | 1,2 à 1,3 s | — |
| P95 | 6,0 à 6,4 s | 0,11 s |
| Coût Mistral pour 1 000 questions | 0,12 $ | 0 $ |
| Réponses dégradées, sans carte | 0, 0 | 0, 0 |

**Un modèle de rédaction plus petit ?** Même mesure avec `ministral-8b`
(100 questions) : médiane 3,8 s, P95 6,4 s (maximum 13,8 s), rédaction
1,9 s en moyenne contre 1,3 s ; **0,097 $** pour 1 000 questions contre
0,117 $. Soit 17 % d'économie, pour des réponses plus lentes et une qualité
de rédaction non mesurée : **`mistral-small` est gardé**. À ce volume, 0,02 $
pour 1 000 questions ne justifie pas le risque.

**Où passe le temps** (moyennes, sans cache) : comprendre 1 ms (des
règles, pas de modèle) ; rechercher 1,5 s (embedding de la question par
Mistral et requêtes SQL) ; filtrer 0,5 s (autres dates de chaque événement,
une requête par carte) ; rédiger 1,2 s (Mistral).

**Limite** : mesuré depuis un poste de travail, chaque requête fait
l'aller-retour jusqu'à la base, à Paris. En production, l'API est dans le
même centre de données que la base : rechercher et filtrer y sont plus
rapides. Ces chiffres sont donc **un majorant** ; le tableau Grafana
« Recherche » donne la latence réelle de production.

**Le cache** : une question déjà posée le même jour, sur la même collecte,
répond en 0,1 s et ne coûte rien. Avec 30 % de questions répétées, le coût
pour 1 000 questions passe de 0,12 $ à 0,08 $.

## 4. Qualité des données, chaque nuit

- **Juge de qualité** : chaque nuit, 20 événements tirés au hasard sont notés
  par `mistral-medium` (un autre modèle que celui qui enrichit) sur quatre
  questions fermées : cohérence, est une sortie, publics justes, catégorie
  juste. Alerte sous 80 %. Résultats : 89 % puis 94 %.
- **Collecte** : extraits, rejetés (et pourquoi), enregistrés, disparus,
  par source ; couverture famille / soirée par zone ; coût ; dans le mail, en
  base (`collect_runs`) et dans Grafana.

## 5. En production

- **Pouce haut / pouce bas** sur chaque réponse, avec « ce qui manquait » ;
- **journal** de chaque question : exacte, élargie, idées voisines, sujet
  introuvable, mots absents de la base ;
- **tableau Grafana « Recherche »** : satisfaction, latence P95, coût,
  réponses exactes ou non, ce qui manque ;
- **bilan du lundi** : l'agent lit les recherches ratées et propose synonymes
  et agendas.

## Ce qui reste à faire

- **fait le 16/10** : offres partenaires en tête seulement si leur type
  précis et leur public correspondent ; « à Montpellier » = la ville
  elle-même ; `eval_recherche` rejoué : compréhension 100 %, précision
  98,7 % ; évaluation rejouée en CI (§ 1) ;
- **fait le 16/10** : seconde grille de pertinence (§ 2, « Seconde grille »).
- à surveiller : les désaccords du juge sur les recherches d'un mot
  (« marché nocturne », « salon ») avant de le laisser seul juge d'un rejeu.
