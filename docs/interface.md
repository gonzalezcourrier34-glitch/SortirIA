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
│ Réponse de SortirIA avec renvois [1] [2]              │ Avis sur le lieu           │
│ [1] carte  [2] carte  [3] carte  [4] carte            ├───────────────────────────┤
│ Pour continuer : relances proposées                   │ À NE PAS MANQUER           │
│                                                       │ propositions partenaires   │
└──────────────────────────────────────────────────────┴───────────────────────────┘
```

- **En paysage** (ordinateur), l'écran tient dans la fenêtre : la réponse et
  la colonne de droite défilent chacune de leur côté.
- **En portrait** (téléphone, moins de 900 px), tout passe sur une colonne.
- Un clic sur **Chercher** ouvre la fenêtre « SortirIA cherche pour vous » :
  les 4 étapes s'y cochent en direct, puis elle se referme. Le lien « Voir
  comment j'ai cherché » la rouvre.
- Un clic sur **Voir le détail** (ou sur un renvoi [n] de la réponse) affiche
  l'événement dans la colonne de droite.
- L'état de la collecte (alertes, coût, couverture) n'apparaît **pas** ici :
  il est réservé à Grafana et MLflow.

## La recherche en 4 étapes

| Étape | Ce qui se passe | Code |
|---|---|---|
| Comprendre | la question est lue comme un humain la lirait : **quand** (ce soir, samedi, ce week-end, le 24/10, en novembre), **où** (une commune, « l'étang »), **pour qui** (enfants, ados, seniors, soirée), **gratuit**, **le type** (théâtre, concert…) et les **mots-clés** qui restent ; le tout précise les cases cochées | `app/query_understanding.py` |
| Rechercher | **recherche hybride** : par le sens (vecteur mistral-embed, pgvector) et par les mots (plein texte français, sans accents) ; les deux classements sont fusionnés (RRF) ; le type demandé passe devant | `build_search_query` |
| Filtrer | les séances d'un même événement sont regroupées ; les 8 premiers événements sont gardés (deux rangées de 4 cartes) | `group_occurrences` |
| Rédiger | Mistral écrit 2 à 4 phrases qui citent les événements [n], en respectant les contraintes comprises, et propose 3 relances | `write_with_mistral`, `parse_answer` |

Ce qui a changé le 15/10/2026, et pourquoi :

- **la question est comprise avant d'être cherchée** : la première version ne
  comparait que le sens global de la phrase ; « du théâtre samedi à Sète avec
  les enfants » ramenait des événements proches en esprit, mais pas forcément
  samedi, ni à Sète, ni pour les enfants. Date, commune, public et gratuit
  sont maintenant des **filtres stricts** ;
- **les mots comptent** : « échecs », « jazz », « Fabre » sont retrouvés par la
  recherche plein texte, que le vecteur seul classe mal ;
- **budget, météo, distance** (15/10, après-midi) : « moins de 15 € » ou
  « pas cher » (15 €) garde le gratuit et les prix d'entrée sous le seuil ;
  « s'il pleut » écarte les sorties de plein air (balade, marché, sport,
  fête…) et « en plein air » ne garde qu'elles ; « près de Sète » ou « à
  moins de 10 km de Mèze » cherche autour de la commune (centre de ses
  événements géolocalisés), « à Sète » reste dans la commune. Contraintes
  lues dans la question : relâchées si elles ne laissent rien ;
- **diversité** : pas plus de 2 propositions au même lieu tant qu'il y en a
  d'autres ;
- **seulement ce qui répond exactement, quand il y en a** : si des événements
  citent les mots précis de la question (« échecs », « Fabre »), seuls eux
  sont gardés ; parmi eux, seuls ceux du type demandé. Deux concerts à Mèze
  valent mieux que deux concerts et quatre autres choses. S'il n'y a rien
  d'exact, les idées voisines sont proposées et la réponse le dit d'abord
  (`exact: false` dans le résultat) ;
- **jamais de réponse vide sans explication** : si les contraintes écrites ne
  laissent rien, elles sont relâchées (sauf la date) et Mistral dit d'abord
  qu'il n'a rien trouvé d'exact ;
- **le fil de la conversation** : l'API renvoie la demande comprise
  (`context`), l'interface la joint à la question suivante ; « et pour les
  enfants ? » ou « plutôt dimanche » complètent la demande précédente.
  « Nouvelle conversation » repart de zéro.

## Mesurer la précision

```bash
uv run python -m scripts.eval_recherche                   # recherche seule
uv run python -m scripts.eval_recherche --avec-redaction  # avec la réponse de Mistral
```

50 questions (`config/eval_recherche.yaml`) : type de sortie, dates, lieux,
publics, gratuit, budget, météo, distance, noms précis, cases cochées. Mesures : compréhension
correcte, part des propositions qui respectent les contraintes attendues
(objectif 95 %, sur les réponses exactes), idées voisines (rien d'exact dans
la base), questions sans résultat, temps de réponse.

Première mesure, 15/10/2026 : compréhension 100 %, propositions correctes
82 %, réponse en 0,46 s (médiane). Écarts : propositions ajoutées « pour faire
nombre » hors du type demandé, et mesure trop stricte sur les dates
(festivals sur plusieurs jours) et les zones (communes hors liste) ; corrigés
le jour même.

Après corrections (15/10/2026, 12 h 30) : compréhension 100 %, **propositions
correctes 99 %** (objectif 95 % atteint), 2 questions sans rien d'exact dans
la base (réponse « idées voisines » annoncée comme telle), 0 sans résultat,
réponse en 0,54 s (médiane), 95 % sous 1,2 s.

Chaque lancement est gardé dans `benchmarks/recherche/` (`historique.csv` et
un rapport JSON complet) et dans MLflow (`sortiria-evaluation-recherche`) ;
`--note "..."` dit ce qui a changé.

Limite à garder en tête : ce jeu de questions a été écrit en même temps que
les règles de compréhension. Il protège contre les régressions, mais la
vraie mesure viendra des questions réelles des utilisateurs (`rag_requests`). Les questions
d'évaluation ne sont pas tracées dans `rag_requests`.

Garde-fous :

- Mistral ne voit **que** les événements retenus ; un renvoi vers un numéro
  qui n'existe pas est retiré de la réponse ;
- si Mistral ne répond pas, la recherche continue : tri par date au lieu du
  vecteur, réponse simple qui liste les événements (`degraded: true`,
  signalé à l'écran) ;
- chaque demande est tracée dans `sortiria.rag_requests` (cases cochées et
  contraintes comprises, événements proposés, durée, tokens, modèle) : c'est
  la base du suivi du bloc RAG dans Grafana.

## Routes de l'API

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/api/v1/recherche/stream` | recherche diffusée (`event: progress` × 8, puis `event: completed` ou `event: error`) |
| GET | `/api/v1/evenements/{event_id}` | détail d'un événement proposable et derniers avis publiés sur son lieu |
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

## Offres partenaires

- **Toujours signalées** : une offre d'un partenaire est dans un encadré
  terracotta doux (`--partenaire-*` dans `styles.scss`) avec l'étiquette
  « Offre partenaire », partout : cartes de la réponse, détail, offres
  proches, et en tuiles sous l'accueil (avant la première question) et dans
  « À ne pas manquer » (colonne de droite).
- **Quand rien ne répond exactement** (`exact: false`), la réponse est suivie
  de l'encadré « Pas exactement ce que vous cherchiez ? Nos partenaires
  proposent aussi » : jusqu'à 3 offres partenaires de la même période, les
  plus proches de la demande par le sens (au même endroit d'abord, sinon dans
  toute la zone), sans répéter celles déjà proposées (`partner_offers`).

## Donner son avis

- Dans le détail d'un événement : 5 étoiles, un commentaire facultatif
  (300 caractères), « Donner mon avis ». Possible **du début de l'événement
  à 7 jours après sa fin** ; avant, le détail dit à partir de quand.
- Le visiteur est anonyme : son navigateur garde un identifiant aléatoire
  (`localStorage`), dont la base ne garde qu'une empreinte salée.
- Le navigateur retient aussi les événements consultés : une fois passés,
  ils apparaissent dans **« Vos sorties à noter »** (colonne de droite),
  jusqu'à ce qu'ils soient notés. Un événement passé depuis moins de 7 jours
  reste affichable pour être noté.
- Message après l'envoi selon la modération : publié, refusé, ou relu plus
  tard si la modération ne répond pas.

## Cache des réponses

Une même demande (question normalisée, contexte, cases cochées), le même
jour, sur la même base, reçoit la réponse déjà rédigée, sans nouvel appel à
Mistral (`query_cache`). La clé contient la date (« ce soir » change de sens
chaque jour) et le numéro de la dernière collecte : une nouvelle nuit
invalide tout. Une entrée vit 6 h au plus, jamais après minuit ; la purge
de la nuit les supprime. Les réponses dégradées (Mistral absent) ne sont pas
gardées. `rag_requests.from_cache` compte les réponses servies par le cache.
L'évaluation (`scripts/eval_recherche.py`) contourne le cache.

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

## Un événement à plusieurs dates

Une activité répétée (« Pause jeux » chaque samedi) n'occupe qu'une carte :
les séances de même titre, même lieu et même commune sont regroupées
(`group_occurrences`). La carte montre la date la plus proche et, en haut à
droite, « + n dates » (liste au survol) ; le détail affiche toutes les autres
dates (`OTHER_DATES_SQL`). Un événement déjà commencé et pas fini
(exposition) affiche « En cours · jusqu'au … » au lieu de sa date de début.

## Type de sortie nommé dans la question

« J'aimerais aller au théâtre » : le mot est reconnu (avec ou sans accent,
au pluriel aussi) et les événements de ce type passent devant les autres
(`wanted_categories`, `QUESTION_CATEGORIES`). Le théâtre accepte aussi les
spectacles, où beaucoup de sources rangent leurs pièces. S'il n'y en a
aucun avec les filtres choisis, Mistral le dit d'abord, puis propose les
autres événements comme idées voisines (`writer_question`).
