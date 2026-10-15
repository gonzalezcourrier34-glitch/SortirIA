# Orchestration Airflow

Les DAG ne sont pas un package Python : chaque fichier est documenté séparément.
La collecte nocturne de SortirIA est `sortiria_nightly_dag`, qui lance l'agent
de collecte (`agent/`). Les DAG de CheckIt.AI ont été retirés le 15/10/2026
(l'agent les remplace).

::: sortiria_nightly_dag
