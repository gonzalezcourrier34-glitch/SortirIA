# Sources d'événements

SortirIA collecte ses événements avec les moteurs de CheckIt.AI (projet 12) :
un **moteur générique** fait le travail commun (requêtes, nouvelles tentatives,
erreurs, validation, dédoublonnage, statuts), et chaque source n'ajoute que ce
qui lui est propre.

| Source | Moteur | Ce qui est propre à la source | Configuration |
| --- | --- | --- | --- |
| OpenAgenda | API (`src/extractors/apis/`) | Adaptateur `openagenda_extractor.py` + conversion `src/event/openagenda_mapper.py` | `api_sources` |
| DATAtourisme | API (`src/extractors/apis/`) | Adaptateur `datatourisme_extractor.py` + conversion `src/event/datatourisme_mapper.py` | `api_sources` |
| Calendriers des lieux partenaires | **iCal** (`src/extractors/ical/`) | Rien : un seul lecteur pour tous les calendriers | `ical_sources` |

À partir de l'`Event`, toute la chaîne est commune : validation, dédoublonnage,
chargement en base, embeddings, agent de collecte.

## Le moteur iCal

Ajouté le 14/10/2026, sur le modèle du moteur RSS de CheckIt.AI :

- `ical_adapter.py` : le contrat (lire les éléments, construire un article) ;
- `ical_engine.py` : l'orchestration de tous les calendriers de `ical_sources`.
  Un calendrier en panne n'arrête pas les autres ; chacun a son statut ;
- `ical_extractor.py` : le téléchargement (trois essais en cas de coupure) et
  la lecture ;
- `src/event/ical_mapper.py` : le format iCal (lignes continuées, fuseaux,
  journées entières, événements répétés chaque jour ou chaque semaine, dates
  exclues, événements annulés).

C'est la voie prévue pour les partenaires : beaucoup de lieux publient leur
programmation en iCal, souvent via un agenda Google. **Brancher un lieu ne
demande aucun code**, seulement une entrée dans `config/sources.yaml` :

```yaml
ical_sources:
  - name: "Nom du lieu"
    source_id: "nom_du_lieu"
    type: "ical"
    url: "https://exemple.fr/agenda.ics"
    venue_name: "Nom du lieu"       # lieu par défaut des événements
    city: "Montpellier"
    latitude: 43.61
    longitude: 3.87
    website: "https://exemple.fr"   # lien si un événement n'en a pas
    credit: "Nom du lieu"           # cité sous chaque proposition
```

Les événements arrivent en base sous la source `ical` ; le lieu est cité dans
`source_credit` (« Source : agenda du lieu (Nom du lieu) »).

L'agent ne lance une source que si elle a au moins une configuration active :
tant qu'aucun calendrier n'est déclaré, le moteur iCal n'est pas lancé (il
remonterait sinon comme une panne chaque nuit).

## Deux corrections faites en écrivant ce moteur

- **Dédoublonnage** : le moteur CheckIt.AI tenait pour doublons deux articles
  de même URL. Or les séances d'un film, ou tous les événements d'un
  calendrier, partagent souvent la même (le site du lieu). L'article reçoit
  donc une URL technique unique par séance (`occurrence_article_url`) ; le
  lien montré à l'utilisateur ne change pas.
- **Longueur minimale du texte** : héritée de la presse (10 caractères,
  titre + texte), elle refusait un « Hamlet » sans description. Elle passe à 3
  pour le rôle `acquisition`.
