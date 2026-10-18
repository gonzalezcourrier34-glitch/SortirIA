# Fiabilité des données et des réponses

*Mise à jour le 16/10/2026.* Trois risques, et pour chacun ce que fait
SortirIA : des **informations manquantes**, des **données périmées**, des
**inventions** (hallucinations) du modèle de langage.

## 1. Informations manquantes : ne jamais les inventer

Les sources ne donnent pas toujours le prix, l'heure ou l'âge. La règle :
**une information absente reste absente, et la recherche n'en suppose rien
de favorable**.

| Information | Ce qui est affiché | Ce que font les filtres |
| --- | --- | --- |
| **Prix** inconnu | « Prix non communiqué » | « Gratuit » ne garde que les événements **déclarés** gratuits ; « moins de 15 € » ne garde que les gratuits ou ceux dont le prix d'entrée **connu** est sous le seuil. Un prix inconnu n'est jamais pris pour gratuit. |
| **Heure** inconnue (« le samedi ») | la date seule, sans heure inventée (`time_known = false`) | « ce soir » garde aussi les événements sans heure : la journée entière peut convenir, la réponse ne promet pas d'heure |
| **Âges** | affichés seulement s'ils sont dans la source | le public « famille » vient de la source, de mots-clés du texte ou de l'enrichissement par Mistral ; chez un partenaire, « tout public » vaut « famille », sauf âge minimal de 18 ans |
| **Lieu** sans coordonnées GPS (PDF, partenaires) | la commune | rangé par sa commune pour la zone (Montpellier, bassin de Thau) |
| **Résumé** | jamais un résumé non contrôlé (voir plus bas) | un événement sans résumé reste proposable |

La validation de la collecte écarte, avec un motif compté chaque nuit, ce qui
ne peut pas être proposé : pas de date, événement passé, annulé ou reporté,
pas une sortie (offre d'emploi, collecte de sang…). Les âges incohérents ou
les publics inconnus sont ramenés dans les clous avant l'écriture.

## 2. Fraîcheur : une base à jour sans intervention

- **Collecte chaque nuit** (tâche planifiée Azure) ; le mail du matin donne
  le statut, les chiffres et les alertes.
- **Événements passés retirés** chaque nuit (gardés 7 jours, sans être
  proposés, pour que les visiteurs puissent les noter).
- **Événements disparus de leur source retirés**, mais seulement pour une
  source lue en entier et sans échec : une source en panne ou en baisse
  anormale garde ses événements de la veille plutôt que de vider la base.
- **Fenêtre de 3 mois** : la recherche ne propose que les 3 prochains mois.
- **Date de mise à jour de la source** affichée sur chaque fiche
  (« Source : DATAtourisme (producteur), mise à jour le… »).
- **Surveillance** : alerte si une source échoue, baisse de moitié, si la
  couverture famille ou soirée d'une zone tombe sous 10 sorties dans la
  semaine, si un programme PDF arrive à sa fin (21 jours avant) ou si un
  partenaire n'a plus de séance à venir.
- **Grafana** : heures depuis la dernière collecte réussie.

## 3. Hallucinations : le modèle ne décide jamais seul de ce qui est vrai

Mistral intervient à quatre endroits ; chacun a son garde-fou.

| Où | Le risque | Le garde-fou |
| --- | --- | --- |
| **Réponse** à la question | citer un événement qui n'existe pas, ou inventer un détail | Mistral ne reçoit **que** les événements trouvés en base, numérotés ; une citation [n] qui ne renvoie à aucun d'eux est **retirée** ; les cartes affichées viennent de la base, pas du texte ; consigne : dire d'abord ce qui manque (« rien de gratuit, mais… ») ; sans Mistral, une réponse simple listant les événements (signalée à l'écran) |
| **Enrichissement** (catégorie, publics, résumé) | un résumé qui invente un nom, une date, un prix | catégorie dans une **liste fermée** (sinon « autre ») ; un titre explicite fixe la catégorie ; un résumé qui cite un nom ou un nombre **absent de la source** est écarté (cas réel : « Cyrano de Bergerac » pour un spectacle nommé « CYRANA ») |
| **Lecture des PDF** | un événement inventé | le titre doit figurer dans la page lue, la date dans la période du programme ; sinon rejeté, et compté (`pdf/inventions_rattrapees` dans MLflow) |
| **Commentaires des visiteurs** | contenu injurieux | Mistral Moderation avant publication |

Et un contrôle d'ensemble : chaque nuit, un **autre modèle** (`mistral-medium`)
juge 20 fiches tirées au hasard ; sous 80 %, une alerte part avec des
exemples. Les réparations proposées par l'agent (adresse d'un PDF, synonyme,
agenda) ne s'appliquent **qu'après validation humaine**.

## 4. Sources, licences et données personnelles

- **Sources ouvertes** (OpenAgenda, DATAtourisme : Licence Ouverte 2.0) :
  source et lien de l'organisateur sur chaque proposition ; le scraping n'est
  fait qu'avec une autorisation écrite.
- **Partenaires** : leurs offres sont toujours signalées comme telles.
- **Données personnelles** : les visiteurs sont anonymes (identifiant
  aléatoire du navigateur, jamais stocké en clair : empreinte salée) ; le
  journal des questions est purgé après 90 jours, les bilans de collecte
  après un an ; aucune clé ni mot de passe dans le code ou l'image.
