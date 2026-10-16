# Sources d'événements

SortirIA collecte ses événements avec les moteurs de CheckIt.AI (projet 12) :
un **moteur générique** fait le travail commun (requêtes, nouvelles tentatives,
erreurs, validation, dédoublonnage, statuts), et chaque source n'ajoute que ce
qui lui est propre.

| Source | Moteur | Ce qui est propre à la source | Configuration | État |
| --- | --- | --- | --- | --- |
| OpenAgenda | API (`src/extractors/apis/`) | Adaptateur `openagenda_extractor.py` + conversion `src/event/openagenda_mapper.py` | `api_sources` | chaque nuit |
| DATAtourisme | API (`src/extractors/apis/`) | Adaptateur `datatourisme_extractor.py` + conversion `src/event/datatourisme_mapper.py` | `api_sources` | chaque nuit |
| Flux RSS d'agenda (lieux, partenaires) | **RSS** (`src/extractors/rss/`) | Adaptateur `rss/source/event_rss_extractor.py` + `src/event/rss_event_mapper.py` | `rss_sources` | prêt, aucune source |
| Agendas en ligne, **avec autorisation de l'éditeur** | **Scraper** (`src/extractors/scrapers/`) | Adaptateur `scrapers/source/agenda_scraper.py` + `src/event/html_agenda_mapper.py` (sélecteurs CSS en configuration) | `scraper_sources` | prêt, `enabled: false` |
| Calendriers des lieux partenaires | **Fichiers** (`src/extractors/files/`) | Lecteur `ical/ical_extractor.py` + `src/event/ical_mapper.py` | `ical_sources` | prêt, aucune source |
| Programmes PDF des collectivités | **Fichiers** (`src/extractors/files/`) | Lecteur `documents/document_extractor.py` (Mistral) + `src/event/document_mapper.py` | `document_sources` | lu depuis un poste de travail (coupé sur Azure) |
| Fichiers des partenaires (Excel, CSV en UTF-8 ou Windows-1252, JSON) | **Datasets** (`src/extractors/datasets/`) | Adaptateur `datasets/source/partner_extractor.py` + `src/event/partner_mapper.py` ([détails](partenaires.md)) | `datasets` > `partenaire` | chaque nuit (exemples fictifs) |

Les sept extracteurs sont déclarés dans `src/extractor_execution_service.py` ;
l'agent ne lance que ceux qui ont une configuration active dans
`config/sources.yaml` (sinon un moteur sans source remonterait comme une panne
chaque nuit).

## Le moteur commun et l'adaptateur de chaque source

Les moteurs de CheckIt.AI sont repris avec leurs tests :
`src/extractors/core` (exécution, statuts, bilans), `apis` (pagination,
quotas, erreurs HTTP), `datasets` (tableurs par morceaux), `rss`, `scrapers`
(robots.txt, délais). Ils gèrent pour toutes les sources ce qui est pénible et
déjà éprouvé : nouvel essai réseau, pagination, quota (429), et un bilan par
extracteur (éléments analysés, gardés, rejetés avec leurs motifs, requêtes,
durée). Chaque source n'ajoute qu'un **adaptateur** de trois fonctions :

| Fonction | Rôle | Exemple OpenAgenda |
| --- | --- | --- |
| `iter_items` | parcourt la source et rend des éléments bruts | lit les agendas page par page, écarte les événements hors zone avant le plafond |
| `validate_item` | refuse un élément avec un motif, compté dans le bilan | titre absent, ligne sans date |
| `build_article` | transforme l'élément en « article » du moteur, l'`Event` rangé dans ses métadonnées | `map_openagenda_event`, puis `build_standard_article` |

Les conversions (`src/event/*_mapper.py`) portent la connaissance de chaque
format : codes de statut d'OpenAgenda, horaires en texte libre de
DATAtourisme, règles `RRULE` de l'iCal, alias de colonnes des tableurs
partenaires. Chaque source produit le même objet `Event`
(`src/event/event_schema.py`) : la base, la recherche et l'interface ne
connaissent pas les sources, et en ajouter une ne les touche pas.

Le statut d'un extracteur est `success`, `partial_success` (des événements,
mais une partie de la lecture a échoué), `empty` ou `failed` ; l'agent en tire
ses décisions (réessayer, garder les événements de la veille). Toutes les
requêtes partent avec un `User-Agent` identifiable (`SortirIA/0.1`), et
l'attribution de la source (`source_credit`, `source_updated_at`) est
conservée jusqu'à l'affichage.

Limite assumée de la reprise : des comportements pensés pour la presse ont dû
être corrigés ou désactivés (voir plus bas le dédoublonnage et la longueur
minimale du texte).

## Réglages communs (`config/sources.yaml`)

| Réglage | Valeur | Pourquoi |
| --- | --- | --- |
| `defaults.common.days_forward` | 90 jours | la fenêtre de lecture de toutes les sources : les 3 prochains mois, ce que la recherche peut proposer |
| `defaults.common.max_articles` | 500 | plafond par source, sauf valeur propre |
| `areas` (ancre YAML `&zones`) | Montpellier 20 km, bassin de Thau 15 km | les deux zones, centre et rayon, partagées par OpenAgenda et DATAtourisme |
| `filters.acquisition` | titre et lien obligatoires, texte facultatif | un événement peut n'avoir qu'un titre |
| `filters.acquisition.remove_duplicates` | `false` (15/10/2026) | le dédoublonnage hérité de CheckIt.AI (« même titre, même jour », pensé pour la presse) fusionnait « Marché » à Mèze et à Marseillan. Les extracteurs dédoublonnent par identifiant de source (uid, uuid), puis la chaîne commune par titre + jour + heure + lieu |

## OpenAgenda

- **Agendas lus** : la nuit ne lit que les 12 agendas choisis dans
  `agenda_uids`. L'audit du 14/10/2026 a montré que la recherche par termes
  (`search_terms` : Montpellier, Hérault, Sète, Thau…) lisait environ 10 000
  événements de 111 agendas, souvent nationaux, pour en garder 83 dans la
  zone, en 6 minutes. Cette recherche complète ne sert plus qu'à la découverte
  hebdomadaire des nouveaux agendas (`discovery_weekday`, `max_agendas: 50`),
  dont l'agent fait des propositions.
- **Zone** : un événement est gardé s'il tombe dans l'une des deux zones, ou,
  sans coordonnées, si sa commune est dans la liste `cities`. Ce filtre passe
  **avant** le plafond : les événements lointains ne consomment pas
  `max_articles`.
- **Réglages** : `page_size: 100`, `max_pages: 10`, `max_articles: 2000`.
- **Pannes** : un agenda supprimé, privé ou en quota (404, 403, 429) ne prive
  plus la nuit des agendas suivants (15/10/2026) ; l'extracteur est alors noté
  en réussite partielle, et l'agent garde les événements non relus.
- **À faire** : les événements sont lus par l'export JSON des agendas
  (`/agendas/{uid}/events.json`), annoncé comme obsolète par OpenAgenda ; la
  migration vers l'API v2 est à prévoir.

## DATAtourisme

La source la plus riche (offices de tourisme, Licence Ouverte 2.0) : une
séance = un événement, avec le producteur de la fiche (« 34 - ADT34 ») et sa
date de mise à jour, cités sous chaque proposition.

- **Réglages** : les deux zones communes (`areas: *zones`), `page_size: 100`,
  `max_pages: 20`, `max_articles: 8000` (trois mois de séances).
- **Pagination** par les liens `meta.next`. Correction du 14/10/2026 : le
  moteur d'API effaçait les paramètres du lien « page suivante », qui perdait
  ainsi ses filtres (DATAtourisme était tombé à 184 séances au lieu de près de
  1 000).
- **Limites** : peu de noms de lieux (le contact porte souvent le nom de
  l'événement et n'est alors pas repris comme lieu) ; horaires lus dans un
  texte libre (« Mercredi 14 octobre 2026 de 19h30 à 21h10 »).

À partir de l'`Event`, toute la chaîne est commune : validation, dédoublonnage,
chargement en base, embeddings, agent de collecte (voir
[plus bas](#la-chaine-commune-apres-lextraction)).

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
2. découpage en paquets de quelques pages (`pages_per_chunk`, 15 000
   caractères au plus par paquet), chaque page marquée `[Page N]` pour
   retrouver la page de chaque événement ;
3. Mistral (`DOCUMENT_EXTRACTION_MODEL`, par défaut `mistral-small-latest`,
   température 0, sortie JSON imposée) relève les événements selon le prompt
   versionné de `src/event/document_mapper.py` (`PROMPT_VERSION`) : la
   consigne décrit la mise en page (titre en capitales, lieu, dates), demande
   une entrée par date, une seule pour une exposition qui dure,
   `jours_semaine` pour une activité répétée, et interdit d'inventer (`null`
   si ce n'est pas écrit) ;
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
1 à 2 centimes avec `mistral-small-latest`. Premier passage sur le programme
réel (14/10/2026) : 506 événements bruts, 303 retenus après le retrait des
doublons du recouvrement et les garde-fous (7 champs invalides, 1 titre absent
du texte : une invention rattrapée). Les motifs de rejet sont comptés dans le
bilan de l'extracteur et suivis dans MLflow (`pdf/inventions_rattrapees`).

Le lien de chaque événement pointe vers sa page (`…pdf#page=21`), sauf si le
programme donne un lien de réservation. Les réponses brutes de Mistral sont
gardées en cache par paquet (`data/cache/documents/`), sous une clé qui mêle
l'empreinte du PDF, la version **et le texte** de la consigne (période
comprise), le découpage (pages lues, taille des paquets, recouvrement) et le
modèle : tant que le PDF et ses réglages ne changent pas, la collecte de nuit
ne rappelle pas Mistral. Correction du 15/10/2026 : une clé incomplète servait
une lecture faite avec d'autres réglages (une autre période). Le cache est
écrit de façon atomique ; un cache illisible (écriture coupée) est ignoré, et
les paquets sont redemandés. Coût d'une lecture complète : quelques centimes.

Pour voir le texte que reçoit Mistral sans l'appeler :
`uv run python -m src.extractors.documents.document_extractor --texte <texte_du_pdf>.txt`.

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
`uv run python -m src.extractors.scrapers.source.agenda_scraper --fichier <page_enregistree>.html`.

## La chaîne commune après l'extraction

Quelle que soit la source, `src/event/event_pipeline.prepare_events` applique
à chaque événement des étapes déterministes et testées, sans modèle de
langage : rapides, gratuites et reproductibles.

| Étape | Module | Ce qu'elle fait | Motifs de rejet comptés |
| --- | --- | --- | --- |
| Normaliser | `event_normalizer` | dates en ISO, fuseau Europe/Paris, avec un indicateur `time_known` (un événement sans heure ne disparaît pas de « ce soir » à minuit une) ; prix lus dans le texte (`price_min`, `price_max`, `is_free` d'après « gratuit », « entrée libre »…) ; commune en casse lisible ; texte sans balises | — |
| Mettre en conformité | `event_validator.conform_to_schema` | âges hors de 0-120 ou incohérents oubliés, publics inconnus retirés, statut et présence inconnus ramenés à leur valeur par défaut. Sans cela, un seul événement hors contrainte faisait échouer l'écriture de toute la nuit (une seule transaction) | — |
| Valider | `event_validator.validate_event` | titre, date de début, fin après début, un lieu ou une commune, lien valide ; refuse les annulés et reportés (statut, ou titre « ANNULÉ… »), les événements uniquement en ligne, les passés, et ce qui n'est pas une sortie (offre d'emploi, recrutement, aide aux devoirs) | `titre_absent`, `date_absente`, `date_illisible`, `fin_avant_debut`, `lieu_absent`, `url_invalide`, `evenement_passe`, `annule_ou_reporte`, `en_ligne_uniquement`, `pas_une_sortie` |
| Identifier et dédoublonner | `event_normalizer.build_dedup_key`, `event_deduplicator` | `event_id` = empreinte du titre + jour + heure de début (si elle est connue) + lieu, normalisés (minuscules, sans accents ni ponctuation) : deux sources qui annoncent le même concert donnent le même identifiant ; le doublon le plus complet est gardé, les champs manquants sont fusionnés | doublons comptés dans `duplicates_merged` |
| Fusionner entre sources | `event_deduplicator.merge_cross_source` | même jour, même commune, heures à moins de 30 minutes, titres presque identiques (75 % de mots communs) : la fiche du partenaire, sinon d'OpenAgenda, est gardée et complétée par l'autre | `cross_source_merged` |

L'heure entre dans l'identifiant depuis le 15/10/2026 : sans elle, deux
séances d'un film le même jour au même cinéma n'en faisaient qu'une. Un
événement sans heure garde une clé par jour. Cette règle ne doit plus changer
après l'ouverture du service : les notes des visiteurs sont attachées à
l'identifiant.

Les **publics** `famille` et `soiree` viennent d'abord de règles simples
(`openagenda_mapper.infer_audiences`) : famille si l'âge maximal est de
12 ans ou moins, ou si un mot comme « jeune public », « enfants », « conte »
apparaît, jamais si l'âge minimal est de 18 ans ; soirée si l'événement
commence à 21 h ou plus tard, ou si un mot comme « soirée », « DJ », « club »
apparaît. Un partenaire peut donner ses publics directement. L'enrichissement
par Mistral ajoute ensuite `ados` et `seniors`, sans jamais retirer un public
donné par la source.

