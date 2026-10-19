# Déploiement sur Azure

SortirIA est en ligne depuis le 15/10/2026 : **https://www.sortiria-montpellier.fr**.

## Ce qui tourne où

| Morceau | Service Azure | Région | Réglages |
| --- | --- | --- | --- |
| Interface Angular | Static Web Apps, offre Standard (`sortiria-web`) | East US 2 (pages servies au plus près du visiteur, depuis l'Europe) | domaine `www.sortiria-montpellier.fr`, certificat HTTPS fourni par Azure |
| API FastAPI | Container Apps (`sortiria-api`) | France Central | 0,5 processeur, 1 Go, de 0 à 1 copie (mise en veille sans visiteur) ; reliée au site |
| Collecte de la nuit | Container Apps Job (`sortiria-collecte`) | France Central | même image que l'API, `python agent`, chaque nuit à 1 h UTC (2 h à Paris en hiver), 1 h au plus, un nouvel essai |
| Base | Azure Database for PostgreSQL, Flexible Server, B1ms (`sortiria-db-gonza`) | France Central | PostgreSQL 16, pgvector, 32 Go, sauvegarde 7 jours |
| Image Docker | GitHub Container Registry | | publiée par la CD, une image par commit (`sha-…`) |
| Journaux | Log Analytics | France Central | journaux de l'API et de la collecte |

**Site et API reliés** (« linked backend » de Static Web Apps) : le site
transmet à l'API toute adresse qui commence par `/api` (`/api/v1/...`, chemin
complet). L'interface n'a donc rien à configurer, et l'API n'accepte que les
appels passés par le site. Conséquence : `/docs` et la route des notes des
clients (`/events/{id}/notes`) ne sont pas joignables directement.

**Secrets** : les clés (Mistral, OpenAgenda, DATAtourisme, SMTP) et l'adresse
de la base sont des secrets Container Apps, lus par l'API et la collecte
(`secretref:`). Aucun secret dans l'image ni dans le dépôt.

## Coût

| Morceau | Coût |
| --- | --- |
| Container Apps (API, collecte) | gratuit : 180 000 secondes de processeur et 2 millions de requêtes par mois offertes ; la collecte en prend environ 16 000 au plus |
| PostgreSQL B1ms, 32 Go | gratuit les 12 premiers mois (offre du compte), environ 15 $ par mois ensuite |
| Static Web Apps Standard | environ 9 $ par mois (l'offre gratuite ne sait pas relier une Container App) |
| Mistral | à l'usage : 0,14 $ la première collecte (base vide), moins d'un centime une nuit ordinaire |
| Nom de domaine (OVHcloud) | un abonnement annuel |

## Déploiement continu

Un push sur `main` lance la **CI** (Ruff, tests, documentation, construction
de l'image), puis la **CD** (`.github/workflows/cd.yml`) :

1. construction et publication de l'image sur GHCR (`sha-<commit>`) ;
2. connexion à Azure sans mot de passe (OpenID Connect : Azure fait confiance
   à ce dépôt et à sa branche `main`, avec le rôle Contributeur sur le seul
   groupe de ressources `sortiria-rg`) ;
3. nouvelle révision de l'API et image de la collecte de la nuit mises à jour
   (secrets et réglages gardés) ;
4. interface compilée et envoyée sur Static Web Apps ;
5. contrôle : le site et `/api/v1/sources` répondent.

Le déploiement n'est actif que si la variable de dépôt `DEPLOY_ENABLED` vaut
`true`.

## Surveiller la production (16/10/2026)

| Quoi | Comment | Prévient |
| --- | --- | --- |
| Collecte de la nuit | mail de bilan (`agent/report.py`) | chaque matin, statut dans le sujet |
| Site, API, base, fraîcheur de la collecte | workflow GitHub « Surveillance » toutes les 3 h (`.github/workflows/surveillance.yml`, route `/api/v1/sante`) | mail de GitHub si une vérification échoue ou si la dernière collecte réussie a plus de 26 h |
| Dérive des questions, des réponses et de la collecte | rapport de chaque nuit (`agent/drift.py`, `collect_runs.drift`) | le lundi, dans le mail ; alerte si elle est forte |
| Modèles, coûts Mistral, versions de consigne, avis, dérive | Grafana sur le poste, qui lit la base de production avec un compte en **lecture seule** (`grafana_lecture`, `scripts/creer_lecteur_grafana.py`) | consulté (pas d'envoi) |
| Facture Azure | budget `sortiria-mensuel` sur `sortiria-rg` : 20 € par mois, mails à 50, 80 et 100 % du coût réel et à 100 % du coût prévu | mail d'Azure (8 à 24 h de retard) |
| Pannes | journaux Log Analytics, historique des exécutions du job | consultés |

Pourquoi la surveillance ne passe que toutes les 3 h : l'API se met en veille
sans visiteur, et chaque appel la réveille quelques minutes ; 8 réveils par
jour restent dans l'offre gratuite de Container Apps.

Le pare-feu de la base n'accepte que l'adresse IP du poste (et les services
Azure) : si l'adresse de la box change, Grafana ne lit plus la production ;
mettre à jour la règle (procédure dans le [README du monitoring](monitoring.md)).

## Une source lue hors d'Azure

Le site de Sète agglopôle renvoie une page web au lieu de son programme PDF
aux serveurs d'Azure (protection anti-robots probable), alors qu'il le sert
depuis une connexion ordinaire. La source `document` est donc coupée sur
Azure (`SOURCES_DESACTIVEES=document`) et lue depuis un poste de travail,
vers la base de production : `uv run python -m agent --source document`.
Ses événements ne sont pas balayés par les collectes de la nuit. Le
programme couvre septembre à décembre ; l'agent rappelle de chercher le
suivant 21 jours avant la fin.
