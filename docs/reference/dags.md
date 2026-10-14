# Orchestration Airflow

Les DAG ne sont pas un package Python : chaque fichier est documenté séparément. La collecte nocturne de SortirIA est `sortiria_nightly_dag` ; les DAG `checkit_*` viennent de CheckIt.AI et restent en lancement manuel.

::: sortiria_nightly_dag

::: checkit_cleanup_dag

::: checkit_database_setup

::: checkit_extract_dag

::: checkit_load_dag

::: checkit_master_pipeline

::: checkit_quality_dag

::: checkit_transform_dag

::: test_checkit_postgres
