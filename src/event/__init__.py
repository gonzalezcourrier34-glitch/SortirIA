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

S'y sont ajoutés les convertisseurs des autres sources (`datatourisme_mapper`,
`document_mapper`, `ical_mapper`, `rss_event_mapper`, `html_agenda_mapper`,
`partner_mapper`), les publics déduits par règles et communs à toutes les sources
(`event_audiences`), la préparation des événements de la nuit (`event_pipeline`),
le texte des embeddings (`event_embedding`), l'enrichissement et le juge Mistral
(`event_enrichment`, `quality_judge`) et les notes des clients (`event_ratings`).
"""
