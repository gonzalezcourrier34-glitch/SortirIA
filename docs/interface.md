# Interface

L'interface de SortirIA reprend l'architecture de Chess Agent (projet 13) :
une application **Angular 21** (`frontend/`) qui parle à l'API **FastAPI**
(`app/`) par le proxy `/api`. La recherche est diffusée étape par étape en
Server-Sent Events, comme l'analyse d'une position dans Chess Agent.

## L'écran

```
┌─ SortirIA · Que faire autour de Montpellier et de l'étang de Thau ? ── sources ─┐
├──────────────────────────────────────────────────────┬───────────────────────────┤
│ Échange avec SortirIA            [Nouvelle conversation] │ L'ÉVÉNEMENT               │
│ Public · Zone · Quand · Gratuit                       │ titre, date, lieu          │
│ [ question du client ……………………… ] [Chercher]           │ En bref (résumé)           │
├──────────────────────────────────────────────────────┤ Source + lien              │
│ ┌ Réponse de SortirIA avec renvois [1] [2] ┐          │ Avis sur le lieu, le vôtre │
│ └──────────────────────────────────────────┘          ├───────────────────────────┤
│ [1] [2] [3] [4]   cartes (8 au plus)                  │ À NE PAS MANQUER           │
│ [5] [6] [7] [8]                                       │ offres partenaires         │
│ Pour continuer : relances proposées                   │ Vos sorties à noter        │
└──────────────────────────────────────────────────────┴───────────────────────────┘
```

- **En paysage** (ordinateur), l'écran tient dans la fenêtre : la réponse et
  la colonne de droite défilent chacune de leur côté. Jusqu'à 3 lignes de
  réponse, les deux rangées de cartes restent entières à l'écran.
- **En portrait** (téléphone, moins de 900 px), tout passe sur une colonne.
- **Avant la première question**, des tuiles d'offres partenaires remplissent
  l'accueil ; elles disparaissent dès la première question et restent dans
  « À ne pas manquer ».
- Un clic sur **Chercher** ouvre la fenêtre « SortirIA cherche pour vous » :
  les 4 étapes s'y cochent en direct, puis elle se referme. Le lien « Voir
  comment j'ai cherché » la rouvre.
- Un clic sur **une carte** (ou sur un renvoi [n] de la réponse) affiche
  l'événement dans la colonne de droite. Une carte d'un événement à plusieurs
  dates montre la plus proche et « + n dates ».
- L'état de la collecte (alertes, coût, couverture) n'apparaît **pas** ici :
  il est réservé au mail de l'agent, à Grafana et à MLflow.

## La recherche en 4 étapes

| Étape | Ce qui se passe | Code |
|---|---|---|
| Comprendre | la question est lue comme un humain la lirait : **quand** (ce soir, samedi, ce week-end, le 24/10, en novembre), **où** (une commune, « l'étang », « près de Sète »), **pour qui** (enfants, ados, seniors, soirée), **gratuit** ou **budget**, **intérieur / plein air**, le **type** (théâtre, concert…) et les **mots-clés** qui restent ; le tout précise les cases cochées. Par règles, sans appel à un modèle | `app/query_understanding.py` |
| Rechercher | **recherche hybride** : par le sens (vecteur mistral-embed, pgvector) et par les mots (plein texte français, sans accents, par préfixe) ; les deux classements sont fusionnés (RRF) ; ce qui a les mots précis et le type demandé passe devant | `build_search_query` |
| Filtrer | ce qui répond exactement, sinon élargissement au sujet (voir plus bas) ; séances regroupées par événement ; 2 cartes au plus par lieu ; 8 cartes ; offres partenaires qui répondent en tête | `keep_exact_matches`, `widen_for_subject`, `group_occurrences`, `partners_first` |
| Rédiger | Mistral écrit 2 à 4 phrases qui citent les événements [n], en respectant les contraintes comprises, et propose 3 relances ; les cartes suivent ensuite l'ordre de la réponse | `write_with_mistral`, `parse_answer`, `order_cards` |

## Comment la recherche décide

**Comprendre la question.**

- Date, commune ou zone, public, gratuit, budget (« moins de 15 € », « pas
  cher » = 15 €), météo (« s'il pleut » écarte le plein air) et distance
  (« à moins de 10 km de Mèze ») sont des **filtres stricts**. Écrits dans la
  question, ils sont relâchés (sauf la date) s'ils ne laissent rien, et
  Mistral le dit d'abord.
- **Le fil de la conversation** : l'API renvoie la demande comprise
  (`context`), l'interface la joint à la question suivante. Une relance
  (« et pour les enfants ? », « plutôt dimanche », « à Sète ? ») complète la
  demande précédente ; une question courte qui nomme un nouveau sujet
  (« photo », « du jazz ») repart de zéro. « Nouvelle conversation » efface
  le fil.
- **Synonymes appris** (`config/synonymes.yaml`) : « zumba » cherche aussi
  « fitness » une fois le synonyme validé (voir « Ce qui a manqué » plus bas).

**Le type de sortie et les mots précis.**

- « J'aimerais aller au théâtre » : le mot est reconnu (avec ou sans accent,
  au pluriel aussi) et désigne un ou plusieurs types (`QUESTION_CATEGORIES`) ;
  le théâtre accepte aussi les spectacles, où beaucoup de sources rangent
  leurs pièces.
- Un événement est **du type demandé** si sa catégorie l'est, ou si le type
  est écrit dans son titre ou dans la catégorie de sa source : « Atelier jeux
  en famille » (rangé en atelier) répond à « jeux », « Soirée DJ » (catégorie
  d'un bar) à « soirée ». Racines exactes : « jeux » ne trouve pas « jeudi ».
  Un événement pas encore classé par Mistral garde la catégorie de sa source
  (`EVENT_CATEGORY`).
- **Les mots précis comptent** : « échecs », « Fabre », « photo » sont
  cherchés dans le texte (par préfixe : « photo » trouve « photographies »).
  Les genres rangés dans un type (`PRECISE_SUBJECTS` : cirque est un
  « spectacle », jazz un « concert ») aussi : un spectacle de danse n'est pas
  du cirque. Un mot précis trouvé suffit, même si l'événement n'est pas du
  type : un stage de cirque répond à « cirque ».

**Seulement ce qui répond exactement, quand il y en a.** Si des événements
ont les mots précis, seuls eux sont gardés ; parmi eux, seuls ceux du type
demandé s'il y en a. Deux concerts à Mèze valent mieux que deux concerts et
quatre autres choses.

**Sujet ou ambiance : appris de la base.** Un mot de la question est un
**sujet** s'il figure dans au moins un titre d'événement de la base
(« photo », « poterie », « escape ») ; écrit dans aucun titre (« romantique »,
« insolite »), c'est une **ambiance**. Le vocabulaire des titres
(`title_vocabulary`) est recalculé par l'agent après chaque collecte : aucune
liste à tenir à la main.

**Quand rien ne parle du sujet** (`widen_for_subject`). Plutôt que 8 idées
sans rapport (« cirque » → ateliers et jeux), le sujet est cherché en
relâchant une étape à la fois : public, gratuit, budget, soirée et cadre ;
puis les 3 prochains mois ; puis toute la zone. Mistral dit d'abord ce qui
diffère (« pas gratuit », « dans 3 semaines »). Un genre absent (des
concerts, mais pas de jazz) est aussi cherché ainsi avant de se rabattre sur
le type. Puis :

- un sujet qui n'existe **nulle part** : la réponse le dit, sans carte ;
- une **ambiance** introuvable : les idées les plus proches par le sens
  restent proposées, et la réponse le dit (`exact: false`).

**Diversité et ordre des cartes.** Pas plus de 2 propositions au même lieu
tant qu'il y en a d'autres. Ordre final (`order_cards`) : les offres
partenaires qui répondent, puis les événements cités dans la réponse (dans
l'ordre où elle les cite), puis les autres ; les numéros sont refaits de 1 à 8
et les citations suivent (« [5], [7] ou [8] » devient « [2], [3] ou [4] »).

**Ce qui a manqué est gardé.** Chaque demande est tracée dans
`sortiria.rag_requests` (cases cochées, contraintes comprises, événements
proposés, durée, tokens, modèle) avec, sous `constraints -> 'recherche'`, la
réponse exacte ou non, les mots inconnus de la base, les contraintes
relâchées et le sujet introuvable. Chaque lundi, l'agent en fait un bilan et
en tire des propositions : synonyme (« zumba » → « fitness »), correction
(« cirqe » → « cirque »), agenda OpenAgenda qui traite un sujet absent. Rien
n'est appliqué sans validation humaine (voir [Agent de collecte](agent.md)).

## Offres partenaires

- **Toujours signalées** : une offre d'un partenaire est dans un encadré
  terracotta doux (`--partenaire-*` dans `styles.scss`) avec l'étiquette
  « Offre partenaire », partout : cartes de la réponse, détail, encadré des
  offres proches, tuiles de l'accueil et « À ne pas manquer ».
- **En tête des cartes quand elles répondent** (`partners_first`) : une
  séance de partenaire retenue respecte toutes les contraintes (période,
  lieu, public, gratuit) et, si la question précise un type ou des mots
  (« concert », « jazz »), elle les a aussi ; sans type ni mot précis, elle
  doit en plus être parmi les 40 plus proches de la question par le sens.
  Mistral les cite en premier, simplement. C'est vrai aussi quand le sujet a
  dû être élargi : un stage de cirque partenaire payant passe en tête pour
  « cirque » avec « Gratuit » coché, la réponse disant qu'il est payant.
- **Sinon, seulement à droite** : une offre qui ne répond pas n'est jamais
  mêlée aux cartes.
- **Encadré « Pas exactement ce que vous cherchiez ? Nos partenaires proposent
  aussi »** (`partner_offers`) : seulement pour une demande sans sujet précis
  qui n'a pas de réponse exacte (« une sortie à Frontignan » quand il n'y a
  rien) ; jusqu'à 3 offres de la même période, les plus proches par le sens,
  au même endroit d'abord. Pas pour un sujet précis : celles qui en parlent
  sont déjà dans les cartes, les autres seraient sans rapport.
- **« Tout public »** écrit par un partenaire compte comme « famille » (sauf
  âge minimal de 18 ans) : l'offre sort avec la case « Famille » cochée.
- Contrôle du 15/10/2026 : les 15 offres des 3 fichiers d'exemple, 27
  questions de visiteur (« jeux », « cirque », « contes », « escalade »,
  « karaoké », « soirée », « une sortie à Frontignan »…) : chaque offre qui
  répond est en tête des cartes.

## Mesurer la précision

```bash
uv run python -m scripts.eval_recherche                   # recherche seule
uv run python -m scripts.eval_recherche --avec-redaction  # avec la réponse de Mistral
```

50 questions (`config/eval_recherche.yaml`) : type de sortie, dates, lieux,
publics, gratuit, budget, météo, distance, noms précis, cases cochées.
Mesures : compréhension correcte, part des propositions qui respectent les
contraintes attendues (objectif 95 %, sur les réponses exactes), idées
voisines (rien d'exact dans la base), questions sans résultat, temps de
réponse.

Première mesure, 15/10/2026 : compréhension 100 %, propositions correctes
82 %, réponse en 0,46 s (médiane). Écarts : propositions ajoutées « pour faire
nombre » hors du type demandé, et mesure trop stricte sur les dates
(festivals sur plusieurs jours) et les zones (communes hors liste) ; corrigés
le jour même.

Après corrections (15/10/2026, 12 h 30) : compréhension 100 %, **propositions
correctes 99 %** (objectif 95 % atteint), 2 questions sans rien d'exact dans
la base (réponse « idées voisines » annoncée comme telle), 0 sans résultat,
réponse en 0,54 s (médiane), 95 % sous 1,2 s. À remesurer après les
changements de l'après-midi (élargissement au sujet, type lu dans le titre,
recherche par préfixe).

**Les 125 recherches courantes** (15/10/2026) : un second jeu,
`config/eval_mots.yaml`, avec des recherches courtes comme les tapent les
visiteurs (« concert », « blind test », « escape game », « vide-grenier »,
« huîtres », « s'il pleut »…), dans toutes les rubriques d'un agenda de
sorties. La liste ne vient pas d'une mesure (SortirIA n'a pas encore
d'utilisateurs) : elle reprend les rubriques courantes des agendas de sorties
et quelques spécialités locales, plus une liste d'activités de loisirs et de
sorties du soir (bowling, laser game, padel, rooftop…). Beaucoup de ces
dernières sont des **lieux ouverts toute l'année**, pas des événements datés :
la base (des agendas) n'en parle que lors d'un événement, et la réponse le
dit (voir [Évolutions possibles](evolutions.md) : une base de lieux). La liste sera complétée par les mots réellement tapés (bilan du lundi de
l'agent).

Mesures du 15/10/2026 sur les 100 premières : 81 % de propositions correctes,
puis 93 % après correction (mot visible sur la carte avant la seule
description, « spectacle » sans la danse, publics complets dans les données) ;
corrigé ensuite : la carte montre la séance trouvée (pas la suivante, qui n'a
pas forcément le même résumé ni le même public), et un mot trouvé seulement
dans la description, ou une expression incomplète (« feu » pour « feu
d'artifice »), donne des idées voisines annoncées comme telles.

Mesure suivante (15/10/2026, 18 h 25), après ces corrections : **98 %** de
propositions correctes sur les 125 recherches courantes et **99 %** sur les
50 questions, compréhension 100 % sur les deux, réponse en moins d'une
seconde (médiane). Les 19 « idées voisines » des 125 recherches sont surtout
des lieux (bowling, padel, karting, piscine…), absents d'une base
d'événements : la réponse le dit (voir [Évolutions possibles](evolutions.md)).
Limite connue : la recherche par préfixe peut rapprocher deux mots de même
début (« tapas » et « tapissiers ») ; c'est rare, et visible dans l'évaluation. Sa compréhension est vérifiée à chaque
`pytest`, sans base (`tests/test_api/test_eval_mots.py`) ; la pertinence des
propositions se mesure sur la vraie base :

```bash
uv run python -m scripts.eval_recherche --fichier config/eval_mots.yaml --note "100 mots"
```

Chaque lancement est gardé dans `benchmarks/recherche/` (`historique.csv` et
un rapport JSON complet) et dans MLflow (`sortiria-evaluation-recherche`) ;
`--note "..."` dit ce qui a changé.

Limite à garder en tête : ce jeu de questions a été écrit en même temps que
les règles de compréhension. Il protège contre les régressions ; la vraie
mesure vient des questions réelles des utilisateurs (`rag_requests`, bilan du
lundi de l'agent). Les questions d'évaluation ne sont pas tracées dans
`rag_requests`.

Garde-fous :

- Mistral ne voit **que** les événements retenus ; un renvoi vers un numéro
  qui n'existe pas est retiré de la réponse ;
- si Mistral ne répond pas, la recherche continue : tri par date au lieu du
  vecteur, réponse simple qui liste les événements (`degraded: true`,
  signalé à l'écran).

## Routes de l'API

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/api/v1/recherche/stream` | recherche diffusée (`event: progress` × 8, puis `event: completed` ou `event: error`) |
| GET | `/api/v1/evenements/{event_id}?visiteur=…` | détail d'un événement proposable, derniers avis publiés sur son lieu, nombre d'avis du lieu et, avec `visiteur`, l'avis de ce visiteur |
| GET | `/api/v1/partenaires/a-ne-pas-manquer?limit=3` | prochains événements des partenaires (mis en avant d'abord) |
| GET | `/api/v1/sources` | sources qui ont des événements proposables (en-tête) |
| POST | `/api/v1/evenements/{event_id}/avis` | avis d'un visiteur : 1 à 5 étoiles, commentaire modéré (409 hors de la fenêtre d'avis) |
| POST | `/api/v1/avis/a-donner` | « Vos sorties à noter » : parmi les événements consultés, ceux notables maintenant et pas encore notés |

Ces routes sont publiques (pas de clé API : c'est le navigateur qui les
appelle). Les deux routes d'avis écrivent : une note par visiteur et par
événement (il peut la modifier), commentaire modéré par Mistral avant
publication. La protection contre les abus (limite de débit) est à faire
avant l'ouverture au public. La route `/events/{event_id}/notes`
(notes envoyées par un autre service) reste protégée par la clé API.

## Donner son avis

- Dans le détail d'un événement : 5 étoiles, un commentaire facultatif
  (300 caractères), « Donner mon avis ». Possible **du début de l'événement
  à 7 jours après sa fin** ; avant, le détail dit à partir de quand.
- Le visiteur est anonyme : son navigateur garde un identifiant aléatoire
  (`localStorage`), dont la base ne garde qu'une empreinte salée.
- Après l'envoi, le détail se recharge : le visiteur revoit **son avis**
  (« Vous avez noté 4/5 le 15/10 », son commentaire et son état : publié,
  en relecture, ou refusé par la modération), avec « Modifier mon avis ».
- La **moyenne d'un lieu** ne s'affiche qu'à partir de 3 notes ; en dessous,
  le détail indique le nombre d'avis (« 1 avis · moyenne affichée à partir
  de 3 »). Les commentaires n'apparaissent qu'une fois publiés ; si la
  modération ne répond pas, l'agent les reprend la nuit suivante.
- Le navigateur retient aussi les événements consultés : une fois passés,
  ils apparaissent dans **« Vos sorties à noter »** (colonne de droite),
  jusqu'à ce qu'ils soient notés. Un événement passé depuis moins de 7 jours
  reste affichable pour être noté.

## Cache des réponses

Une même demande (question normalisée, contexte, cases cochées), le même
jour, sur la même base, reçoit la réponse déjà rédigée, sans nouvel appel à
Mistral (`query_cache`). La clé contient la date (« ce soir » change de sens
chaque jour), le numéro de la dernière collecte (une nouvelle nuit invalide
tout), la version de la recherche (`PROMPT_VERSION`) et les synonymes appris
(un synonyme validé sert tout de suite). Une entrée vit 6 h au plus, jamais
après minuit ; la purge de la nuit les supprime. Les réponses dégradées
(Mistral absent) ne sont pas gardées. `rag_requests.from_cache` compte les
réponses servies par le cache. L'évaluation (`scripts/eval_recherche.py`)
contourne le cache.

## Un événement à plusieurs dates

Une activité répétée (« Pause jeux » chaque samedi) n'occupe qu'une carte :
les séances de même titre, même lieu et même commune sont regroupées
(`group_occurrences`). La carte montre la prochaine séance dans la période
demandée et, en haut à droite, « + n dates » (toutes les autres séances à
venir, liste au survol) ; le détail affiche les mêmes dates
(`OTHER_DATES_SQL`). Un événement déjà commencé et pas fini (exposition)
affiche « En cours · jusqu'au … » au lieu de sa date de début.

## Lancer l'interface

En développement (deux terminaux, depuis la racine du projet) :

```bash
uv run uvicorn app.main:app --reload --port 8010   # API sur http://localhost:8010
cd frontend && npm install && npm start            # interface sur http://localhost:4210
```

Ports 8010 et 4210 : 8000 et 4200 sont déjà pris par les projets triage médical
et Chess Agent quand ils tournent.

Avec Docker (base, API et interface) :

```bash
docker compose --profile interface up -d --build
```

Le modèle de rédaction se règle dans `.env` : `SEARCH_MODEL`
(`mistral-small-latest` par défaut). L'ancien RAG du projet d'origine (index
FAISS, routes `/ask` et `/rebuild`, tableau Streamlit) a été retiré le
15/10/2026 : l'API démarre en une seconde, sans appels Mistral.
