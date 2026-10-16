# Sources d'événements

SortirIA collecte ses événements avec les moteurs de CheckIt.AI (projet 12) :
un **moteur générique** fait le travail commun (requêtes, nouvelles tentatives,
erreurs, validation, dédoublonnage, statuts), et chaque source n'ajoute que ce
qui lui est propre.

| Source | Moteur | Ce qui est propre à la source | Configuration |
| --- | --- | --- | --- |
| OpenAgenda | API (`src/extractors/apis/`) | Adaptateur `openagenda_extractor.py` + conversion `src/event/openagenda_mapper.py` | `api_sources` |
| DATAtourisme | API (`src/extractors/apis/`) | Adaptateur `datatourisme_extractor.py` + conversion `src/event/datatourisme_mapper.py` | `api_sources` |
| Flux RSS d'agenda (lieux, partenaires) | **RSS** (`src/extractors/rss/`) | Adaptateur `rss/source/event_rss_extractor.py` + `src/event/rss_event_mapper.py` | `rss_sources` |
| Agendas en ligne, **avec autorisation de l'éditeur** | **Scraper** (`src/extractors/scrapers/`) | Adaptateur `scrapers/source/agenda_scraper.py` + `src/event/html_agenda_mapper.py` (sélecteurs CSS en configuration) | `scraper_sources` |
| Calendriers des lieux partenaires | **Fichiers** (`src/extractors/files/`) | Lecteur `ical/ical_extractor.py` + `src/event/ical_mapper.py` | `ical_sources` |
| Programmes PDF des collectivités | **Fichiers** (`src/extractors/files/`) | Lecteur `documents/document_extractor.py` (Mistral) + `src/event/document_mapper.py` | `document_sources` |
| Fichiers des partenaires (Excel, CSV, JSON) | **Datasets** (`src/extractors/datasets/`) | Adaptateur `datasets/source/partner_extractor.py` + `src/event/partner_mapper.py` ([détails](partenaires.md)) | `datasets` > `partenaire` |

À partir de l'`Event`, toute la chaîne est commune : validation, dédoublonnage,
chargement en base, embeddings, agent de collecte.

## Le moteur « fichiers » (iCal, PDF)

Écrit le 14/10/2026 pour l'iCal sur le modèle du moteur RSS de CheckIt.AI,
puis rendu générique le même jour quand le PDF est arrivé : une source qui
télécharge **un fichier** et en tire des événements ne diffère que par sa
façon de le lire.

- `src/extractors/files/file_adapter.py` : le contrat (lire les éléments,
  valider, construire un article) ;
- `src/extractors/files/file_engine.py` : l'orchestration de toutes les
  sources d'un type. Une source en panne n'arrête pas les autres ; chacune a
  son statut et ses motifs de rejet.

## Les calendriers iCal

- `src/extractors/ical/ical_extractor.py` : le téléchargement (trois essais en
  cas de coupure) et la lecture ;
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

## Les programmes PDF lus par Mistral

Beaucoup de collectivités ne publient leur agenda culturel qu'en PDF (exemple :
l'« Agenda Culture » de Sète agglopôle, septembre-décembre 2026). Un document
administratif publié se réutilise librement en citant la source et la date
(code des relations entre le public et l'administration, art. L321-1 et
suivants) ; les photos, qui appartiennent à leurs auteurs, ne sont pas reprises.

Fonctionnement (`src/extractors/documents/document_extractor.py`) :

1. téléchargement du PDF, lecture du texte page par page (`pypdf`) ;
2. découpage en paquets de quelques pages (`pages_per_chunk`), chaque page
   marquée `[Page N]` ;
3. Mistral (`DOCUMENT_EXTRACTION_MODEL`, par défaut `mistral-small-latest`,
   température 0, sortie JSON imposée) relève les événements selon le prompt
   versionné de `src/event/document_mapper.py` ;
4. **garde-fous**, car un modèle peut inventer :
   - champs validés par Pydantic (dates au format AAAA-MM-JJ, âges plausibles)
     → motif `champs_llm_invalides` ;
   - le titre doit se retrouver dans le texte de la page
     → `titre_absent_du_texte` ;
   - la date doit tomber dans la période annoncée par le document
     → `hors_periode` ;
5. puis la chaîne commune (validation, dédoublonnage, base, embeddings).

Réglages faits après lecture du vrai texte de l'Agenda Culture (64 pages,
108 000 caractères), tous dans `config/sources.yaml` sauf la consigne :

| Constat dans le PDF | Réglage |
| --- | --- |
| Sommaire, édito, horaires d'ouverture | `pages: "4-59,63"` : seules les pages de programme sont lues |
| Une liste de séances continue page suivante sans son titre | `pages_overlap: 1` ; les doublons du recouvrement sont retirés |
| « Visites guidées tous les mercredis à 10h » | Mistral rend `jours_semaine` ; le code déroule les séances (`expand_dates`) |
| « Les jeudis (hors vacances scolaires) » | `school_holidays` (provisoire, en attendant le calendrier scolaire) |
| « MÉDIATHÈQUE MONTAIGNE » sans commune | `venues` : commune de chaque lieu, d'après les pages pratiques |
| Public donné par la rubrique (« Jeunesse et famille (0-12 ans) ») | Consigne : âge déduit de la rubrique |
| « Sur inscription » | Ajouté à la description |

Quota du plan gratuit de Mistral : le premier essai (19 appels en 6 s) a reçu
19 erreurs 429. D'où une pause entre les appels (`DOCUMENT_CALL_PAUSE_S`,
3 s), de nouveaux essais espacés (10 s, 20 s, 40 s...) en cas de 429, et un
cache qui ne garde que les paquets réussis : une lecture interrompue reprend
où elle s'est arrêtée. Si aucun paquet n'a pu être lu, la source est en échec
(visible dans le mail de bilan) au lieu de paraître vide.

Une lecture complète : 19 paquets de 4 pages, environ 50 000 tokens en entrée,
1 à 2 centimes avec `mistral-small-latest`.

Le lien de chaque événement pointe vers sa page (`…pdf#page=21`), sauf si le
programme donne un lien de réservation. Les réponses brutes de Mistral sont
gardées en cache (`data/cache/documents/`, clé = empreinte du PDF + version du
prompt + modèle) : tant que le PDF ne change pas, la collecte de nuit ne
rappelle pas Mistral. Coût d'une lecture complète : quelques centimes.

Pour voir le texte que reçoit Mistral sans l'appeler :
`uv run python -m src.extractors.documents.document_extractor --texte perso/agglopole.txt`.

Correction faite en écrivant ce lecteur : l'URL technique de dédoublonnage
ajoutait `?occurrence=…` **après** le `#page=N`, là où la normalisation des URL
coupe tout ; deux événements d'un même PDF devenaient des doublons. Le
paramètre passe désormais avant le fragment.

## Les flux RSS d'agenda

Le moteur RSS de CheckIt.AI lisait des flux de presse (une entrée = un article
daté de sa publication). L'adaptateur « agenda »
(`src/extractors/rss/source/event_rss_extractor.py`) garde le moteur et le
téléchargement, mais tire de chaque entrée un événement :

- date de l'événement : balises du module RSS « événements » (`ev:startdate`,
  `ev:enddate`, `ev:location`) si le flux en a ; sinon la date de
  publication, **seulement** si la configuration le dit
  (`event_date: "published"`, cas des agendas WordPress « The Events
  Calendar ») ;
- lieu et commune : ceux de l'entrée, sinon ceux de la configuration ;
- motifs de rejet : `date_evenement_absente`, `titre_absent`,
  `commune_absente`, `hors_fenetre` (passé, ou au-delà de `days_forward`).

Recherche du 14/10/2026 : pas encore de flux d'agenda à la fois actif et
autorisé dans la zone (Démosphère Hérault : flux vide ; Agenda du Libre :
interdit par son robots.txt). Le moteur est prêt et testé ; il sera branché
avec le premier lieu ou partenaire qui publie un flux.

## Les agendas en ligne (scraper)

**Règle du projet : un site n'est lu qu'avec l'autorisation écrite de son
éditeur** (ou une licence qui le permet). Recherche du 14/10/2026 : aucun site
de la zone ne l'autorise d'emblée (robots.txt fermé à Sète et Balaruc,
« reproduction interdite sans autorisation » à Montpellier, Mèze, Frontignan,
l'Opéra, Victoire 2 ; offices de tourisme : usage personnel seulement, et
doublon de DATAtourisme).

Frontignan a été choisi : 114 événements propres à la ville, robots.txt
ouvert, page de liste très lisible (dates au format schema.org). Une demande
d'autorisation a été envoyée le 14/10/2026 ; la source reste `enabled: false`
jusqu'à la réponse.

Office de tourisme Archipel de Thau (`archipel-thau.com`, agenda le plus
complet du bassin) : ses mentions légales limitent les contenus à un usage
personnel. Demande envoyée le 16/10/2026 à `partenariats@archipel-thau.com` :
un flux réservé aux partenaires, ou l'autorisation de lire la page de liste une
fois par nuit (titre, date, lieu, prix, lien), ou la confirmation que ses
événements passent déjà par DATAtourisme (sa saisie se fait dans le Tourinsoft
de l'Hérault, qui alimente DATAtourisme). Hérault Tourisme, Tourisme Occitanie,
Petit Futé et Envie de Thau ont été écartés le même jour : reproduction
interdite et pas de flux, ou données déjà reçues par DATAtourisme.

Fonctionnement (`src/extractors/scrapers/source/agenda_scraper.py`) :

- le téléchargement est celui de CheckIt.AI (`get_html`) : robots.txt vérifié
  avant chaque page, pause entre deux pages (`request_delay_seconds`, 2 s),
  détection des pages anti-robots ;
- seule la **page de liste** est lue (10 pages, 12 fiches chacune) : la page
  de chaque événement n'est pas ouverte, soit 10 requêtes par nuit au lieu
  de plus de 100 ;
- les fiches sont lues avec les **sélecteurs CSS de la configuration** : un
  autre site autorisé se branche sans code ;
- jamais d'image ; titre, dates, thème, court résumé et lien vers la page
  de l'événement, avec « Source : Ville de Frontignan ».

Vérifié sur la page enregistrée le 14/10/2026 (12 fiches lues) ; corrections
faites à cette occasion : symboles en tête de titre retirés, lieu tiré du
résumé (« 📍 O'studio, ... »), et option `day_only` : la liste affiche
« 06:00 » par défaut pour les événements sur plusieurs jours, ce n'est pas une
vraie heure.

Essai sans aucune requête au site :
`uv run python -m src.extractors.scrapers.source.agenda_scraper --fichier perso/frontignan_agenda.html`.

