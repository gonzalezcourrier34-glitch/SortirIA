"""Modèle événement de SortirIA.

**Provenance** : nouveau package écrit pour SortirIA (13/10/2026). Il remplace
progressivement le modèle « article étiqueté » hérité de CheckIt.AI.

Le package est découpé comme `src/article/` de CheckIt.AI :

- `event_schema` : la structure d'un événement (dataclass `Event`) ;
- `event_normalizer` : nettoyage des dates, des prix, des lieux et des textes ;
- `event_validator` : règles qui décident si un événement est exploitable ;
- `event_deduplicator` : fusion d'un même événement vu dans plusieurs sources ;
- `openagenda_mapper` : conversion d'un événement brut OpenAgenda en `Event` ;
- `event_repository` : écriture dans PostgreSQL et retrait des événements passés.
"""
