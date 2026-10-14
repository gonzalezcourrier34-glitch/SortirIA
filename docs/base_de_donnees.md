# Base de données

SortirIA utilise **PostgreSQL 16 avec l'extension pgvector** : les filtres
stricts (date, zone, budget, public) et la recherche sémantique se font dans la
même base, et souvent dans la même requête.

Le schéma est dans `sql/sortiria/`, en fichiers numérotés exécutés dans l'ordre.
Il a été écrit pour SortirIA le 13/10/2026 (`events`, `collect_runs`,
`rag_requests`) puis complété le 14/10/2026 (sources, partenaires, avis, cache,
publics, vues). Les anciens fichiers de CheckIt.AI, qui décrivaient des articles
de presse étiquetés, sont rangés dans `reference/sql_checkit/`.

## Créer ou mettre à jour la base

```bash
docker compose up -d db                      # base vide : le schéma est créé tout seul
uv run python -m scripts.init_db             # base existante : mise à jour
uv run python -m scripts.init_db --verifier  # liste les tables et vues
```

Les fichiers sont réexécutables : relancer `init_db` après une modification du
schéma conserve les données.

## Les tables et leurs liens

```mermaid
erDiagram
    sources ||--o{ events : "fournit"
    partners ||--o{ events : "propose"
    partners ||--o{ partner_imports : "envoie"
    collect_runs ||--o{ collect_run_sources : "détaille"
    sources ||--o{ collect_run_sources : "lue pendant"
    collect_runs ||--o{ partner_imports : "traite"
    rag_requests ||--o{ feedback : "reçoit"
```

| Fichier | Table | Une ligne par | À quoi elle sert |
| --- | --- | --- | --- |
| `01_sources.sql` | `sources` | source déclarée | Licence et texte d'attribution affiché sous chaque proposition. Un événement d'une source non déclarée est refusé. |
| | `partners` | professionnel partenaire | Organisation seulement : les coordonnées des personnes restent hors de la base (RGPD). |
| | `partner_imports` | fichier reçu d'un partenaire | Bilan de l'import ; l'empreinte SHA-256 empêche d'importer deux fois le même fichier. |
| `02_events.sql` | `events` | événement unique (une séance), après déduplication | Ce que le RAG filtre et recherche (embedding Mistral de 1 024 dimensions). `source_credit` et `source_updated_at` gardent le producteur de la fiche et sa date de mise à jour, à citer d'après la Licence Ouverte. |
| `03_collect.sql` | `collect_runs` | collecte nocturne | Statut et compteurs de la nuit. |
| | `collect_run_sources` | source lue pendant une collecte | Les décisions de l'agent : essais, échec, événements apportés. |
| `04_rag.sql` | `rag_requests` | question posée | Contraintes extraites, latence, tokens, coût, versions. |
| | `feedback` | avis sur une proposition | Pouce en haut ou en bas ; reste après la suppression de l'événement. |
| | `query_cache` | réponse en cache | Même question, même jour, mêmes données : pas d'appel à Mistral. |

## Les règles que la base garantit

Ces règles sont des contraintes PostgreSQL : même un bug dans le code ne peut
pas les contourner.

- Un événement mis en avant (`is_sponsored`) a forcément un partenaire.
- Les publics possibles sont `famille` et `soiree` ; l'âge minimal ne dépasse
  pas l'âge maximal ; la fin ne précède pas le début.
- Un avis vaut `-1` ou `1`, une seule fois par proposition.

## Les vues

| Vue | Contenu |
| --- | --- |
| `v_upcoming_events` | Les événements proposables : ni annulés, ni reportés, ni passés, ni uniquement en ligne. **Le RAG lit cette vue, pas la table.** |
| `v_coverage` | Nombre d'événements par zone (Montpellier, bassin de Thau, hors zone), dont gratuits, famille, soirée. |
| `v_collect_health` | Historique des collectes, pour le tableau de bord. |
| `v_daily_quality` | Par jour : requêtes, taux de cache, latence P95, coût, part d'avis positifs. |

## Publics et statuts OpenAgenda

`src/event/openagenda_mapper.py` remplit les nouvelles colonnes :

- `status` et `attendance` viennent des codes OpenAgenda (`status` 1 à 6,
  `attendanceMode` 1 à 3). Un événement annulé, reporté ou uniquement en ligne
  est rejeté dès la validation (`annule_ou_reporte`, `en_ligne_uniquement`).
- `age_min`, `age_max` et `accessibility` viennent des champs `age` et
  `accessibility`.
- `audiences` est déduit par des règles simples, faute de champ « public »
  dans OpenAgenda : `famille` si l'âge maximal est de 12 ans ou moins ou si un
  mot comme « jeune public » apparaît (jamais si l'âge minimal est de 18 ans) ;
  `soiree` si l'événement commence à 21 h ou plus tard, ou si un mot comme
  « soirée » ou « DJ » apparaît.

## Événements disparus de leur source

À chaque collecte, l'agent met à jour `last_seen_at` des événements revus,
puis supprime ceux de la même source qui ne l'ont pas été (retirés de
l'agenda, ou désormais refusés par la validation). Il ne le fait que pour une
source lue sans échec et qui a renvoyé au moins un événement : une API en
panne ne vide jamais la base. Le nombre est noté dans `collect_runs.events_removed`.

## Conservation des données

`SELECT * FROM sortiria.purge_old_data();` supprime les questions de plus de
90 jours (et leurs avis), les entrées de cache expirées et les collectes de
plus d'un an. L'agent l'appellera à la fin de chaque collecte nocturne.

## Tests

`tests/event/test_schema_postgres.py` vérifie le schéma sur un vrai
PostgreSQL : réexécution, contraintes, vues, purge, et aller-retour avec le code
Python. Ces tests ne tournent que si `SORTIRIA_TEST_DATABASE_URL` pointe vers
une base dont le nom contient « test » :

```bash
docker compose exec db createdb -U sortiria sortiria_test
SORTIRIA_TEST_DATABASE_URL=postgresql://sortiria:<mot de passe>@localhost:5432/sortiria_test \
    uv run pytest tests/event/test_schema_postgres.py
```
