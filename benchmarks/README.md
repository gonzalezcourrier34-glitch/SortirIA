# Benchmarks

Résultats gardés de chaque évaluation, pour suivre l'évolution dans le temps.

## `recherche/` : la recherche de l'interface

Rempli par `uv run python -m scripts.eval_recherche` (un lancement = un
rapport JSON + une ligne dans `historique.csv`, aussi envoyés dans MLflow,
expérience `sortiria-evaluation-recherche`, quand le serveur répond).

| Colonne | Sens |
|---|---|
| `mode` | `recherche` (sans rédaction) ou `avec_redaction` (réponse de Mistral) |
| `jeu` | empreinte du jeu de questions : deux scores ne se comparent que sur le même jeu (`config/eval_recherche.yaml`, les 50 questions ; `config/eval_mots.yaml`, les 125 recherches courtes) |
| `comprehension` | part des questions comprises comme attendu |
| `precision` | part des propositions qui respectent les contraintes attendues (objectif 0,95) |
| `idees_voisines` | questions sans rien d'exact dans la base (la réponse le dit) |
| `latence_*` | temps de réponse, en secondes |
| `note` | ce qui a changé (`--note "..."`) |

Les deux premières lignes (15/10/2026) ont été reconstituées depuis la sortie
du terminal : valeurs arrondies, sans rapport JSON.

## `cout_latence/` : coût et temps de réponse (16/10/2026)

Rempli par `uv run python -m scripts.mesure_cout_latence` : les questions des
deux jeux d'évaluation (175), répétées jusqu'à `--n`, passent par la vraie
recherche, **sans être tracées** dans `rag_requests`. Options : `--n 1000 --oui`
(au-delà de 100, il faut confirmer : environ 0,0006 $ par question),
`--cache` (second passage servi par le cache), `--modele ministral-8b-latest`
(autre modèle de rédaction), `--sans-redaction` (recherche seule).

| Colonne | Sens |
|---|---|
| `mediane_ms`, `p95_ms` | temps de bout en bout (question -> réponse complète), en millisecondes |
| `cout_1000_usd` | coût Mistral pour 1 000 questions (rédaction et embedding de la question) |
| `p95_ms_cache`, `cout_1000_usd_cache` | les mêmes au second passage, servi par le cache (`--cache`) |

Le rapport JSON donne aussi le temps moyen de chaque étape (comprendre,
rechercher, filtrer, rédiger) et, avec `--cache`, le coût pour 1 000
questions selon la part de questions déjà posées le même jour. Mesuré depuis
un poste de travail, le temps inclut l'aller-retour vers la base ; en
production, l'API est à côté de la base.
