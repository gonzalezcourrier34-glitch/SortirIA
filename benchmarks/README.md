# Benchmarks

Résultats gardés de chaque évaluation, pour suivre l'évolution dans le temps.

## `recherche/` : la recherche de l'interface

Rempli par `uv run python -m scripts.eval_recherche` (un lancement = un
rapport JSON + une ligne dans `historique.csv`, aussi envoyés dans MLflow,
expérience `sortiria-evaluation-recherche`, quand le serveur répond).

| Colonne | Sens |
|---|---|
| `mode` | `recherche` (sans rédaction) ou `avec_redaction` (réponse de Mistral) |
| `jeu` | empreinte de `config/eval_recherche.yaml` : deux scores ne se comparent que sur le même jeu |
| `comprehension` | part des questions comprises comme attendu |
| `precision` | part des propositions qui respectent les contraintes attendues (objectif 0,95) |
| `idees_voisines` | questions sans rien d'exact dans la base (la réponse le dit) |
| `latence_*` | temps de réponse, en secondes |
| `note` | ce qui a changé (`--note "..."`) |

Les deux premières lignes (15/10/2026) ont été reconstituées depuis la sortie
du terminal : valeurs arrondies, sans rapport JSON.
