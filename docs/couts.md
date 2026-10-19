# Coûts et projection financière

*Mise à jour le 19/10/2026. Prix publics relevés le 16/10/2026, en dollars US
(Azure facture en euros au taux du jour) ; à vérifier sur les pages officielles
avant toute décision. Sources en bas de page.*

## Ce que coûte SortirIA aujourd'hui

| Poste | Service | Prix unitaire | Coût mensuel aujourd'hui |
| --- | --- | --- | --- |
| Interface | Azure Static Web Apps, offre Standard | 9 $ par application et par mois (100 Go de trafic inclus) | **9 $** |
| API | Azure Container Apps (consommation, mise en veille sans visiteur) | 0,000024 $ par seconde de processeur active, 0,000003 $ au repos, 0,000003 $ par Gio-seconde, 0,40 $ le million de requêtes ; **offerts chaque mois** : 180 000 s de processeur, 360 000 Gio-s, 2 millions de requêtes | 0 $ (dans l'offert) |
| Collecte de la nuit | Container Apps Job (1 processeur, 2 Gio, 5 à 15 min par nuit) | mêmes tarifs, pas de requêtes facturées | 0 $ (≈ 10 % de l'offert) |
| Base | PostgreSQL Flexible Server, Burstable B1ms, 32 Go | **gratuit 12 mois** (750 h par mois, 32 Go de stockage et de sauvegarde), puis ≈ 14 $ par mois en France Central, plus le stockage | 0 $ la 1re année |
| Recherche (Mistral) | `mistral-small` (rédaction), `mistral-embed` (question) | mesuré : **0,000118 $ par question** | ≈ 0 $ (peu de visiteurs) |
| Collecte (Mistral) | enrichissement, embeddings, juge | mesuré : 0,14 $ la première collecte (base vide), **≈ 0,005 $ une nuit ordinaire** | ≈ 0,2 $ |
| Évaluation (Mistral) | juge de pertinence `mistral-large` (`scripts/eval_pertinence.py --juge`), **lancé à la main**, jamais à chaque question ni chaque nuit | 0,50 $ le million de tokens en entrée, 1,50 $ en sortie ; ≈ 50 appels de 700 tokens : **≈ 0,03 $ par passage** | < 0,1 $ (un passage avant chaque version) |
| Journaux | Log Analytics | 5 Go par mois offerts | 0 $ |
| Image Docker | GitHub Container Registry (paquet public) | gratuit | 0 $ |
| Nom de domaine | `sortiria-montpellier.fr` (OVHcloud) | abonnement annuel, quelques euros par an | < 1 $ |
| **Total** | | | **≈ 10 $ par mois** |

Le premier mois est couvert par le crédit de bienvenue d'Azure (200 $ sur 30 jours).

## Projection selon l'usage

Trois niveaux d'usage, par mois. Hypothèses : 3 s de traitement par question
(mesuré), l'API reste éveillée 5 minutes après la dernière question avant de
se mettre en veille, environ 4 requêtes HTTP par question (recherche, détail,
sources, avis), 30 % de questions déjà posées le même jour (servies par le
cache), prix Mistral entre la grille actuelle (0,12 $ pour 1 000 questions,
mesuré) et une grille plus chère (0,20 $).

| Poste (par mois) | Démonstration : 1 000 questions | Lancement local : 10 000 | Succès : 100 000 |
| --- | --- | --- | --- |
| Interface (Static Web Apps) | 9 $ | 9 $ | 9 $ |
| API et collecte (Container Apps) | 0 $ (dans l'offert) | ≈ 6 $ (éveillée ~14 h par jour) | ≈ 14 $ (éveillée en continu) |
| Mistral, recherche (avec cache) | 0,1 $ | 0,8 à 1,4 $ | 8 à 14 $ |
| Mistral, collecte de la nuit | 0,2 à 0,5 $ | 0,2 à 0,5 $ | 0,2 à 0,5 $ |
| Domaine | < 1 $ | < 1 $ | < 1 $ |
| **Total, 1re année** (base offerte) | **≈ 10 $** | **≈ 17 $** | **≈ 32 à 38 $** |
| Base PostgreSQL après 12 mois | + ≈ 18 $ | + ≈ 18 $ | + ≈ 18 $ |
| **Total ensuite** | **≈ 28 $** | **≈ 35 $** | **≈ 50 à 56 $** |
| Coût complet par question | 0,010 à 0,028 $ | 0,0017 à 0,0035 $ | 0,0003 à 0,0006 $ |

À 100 000 questions par mois, une seule copie de l'API suffit encore (environ
2 questions par minute en moyenne, chacune attendant surtout Mistral et la
base) ; au-delà, passer à 2 copies demande un compteur partagé pour la limite
de débit (voir [Interface](interface.md)).

## Ce qui borne la facture

- **Limite de débit** : 12 recherches par minute et par visiteur, et surtout
  **1 000 recherches par heure pour tout le service**. Même un robot qui
  change d'adresse à chaque question ne peut pas dépasser environ
  720 000 questions par mois, soit **au plus 85 à 145 $ de Mistral**.
- **Cache** : une question déjà posée le même jour ne coûte rien et répond en
  0,1 s.
- **Mise en veille** de l'API sans visiteur (0 à 1 copie) : pas de coût fixe
  de calcul.
- **Budget de la nuit** (`MISTRAL_BUDGET_NUIT_USD`, 0,50 $) : au-delà, une
  alerte part dans le mail du matin.
- **Alerte de budget Azure**, en place depuis le 16/10 : 20 € par mois (le coût attendu est d’environ 10 $), mail à 50, 80 et 100 % ; réglage dans le [README du monitoring](monitoring.md).

## Les leviers pour réduire

| Levier | Gain | Ce qu'on perd |
| --- | --- | --- |
| Static Web Apps gratuit, l'API sert aussi l'interface (un seul conteneur) | − 9 $ par mois | une adresse de plus à gérer pour l'API ; quelques lignes de code |
| Petit modèle de rédaction (`ministral-8b`) | mesuré le 16/10 : 0,097 $ au lieu de 0,117 $ pour 1 000 questions (− 17 %) | rédaction plus lente (1,9 s au lieu de 1,3 s), qualité non mesurée : **non retenu** |
| Base arrêtée la nuit hors collecte | ≈ − 30 % sur la base après 12 mois | complexité, réveil à prévoir |
| Réservation de la base sur 1 an | ≈ − 30 à 40 % | engagement |

## Une piste de recettes (hypothèse)

SortirIA met déjà en avant les **offres des partenaires** quand elles
répondent à la demande, toujours signalées comme telles. Un abonnement de
**10 à 20 € par mois** par lieu partenaire (bar, salle, ludothèque, festival)
couvrirait le service avec **2 à 4 partenaires** au niveau « lancement
local ». C'est une hypothèse, pas une étude de marché : à valider auprès de
quelques lieux, et à encadrer (une offre partenaire ne passe devant que si
elle répond à la demande).

## Les mesures qui fondent ces chiffres

- coût et temps de la recherche : `benchmarks/cout_latence/` (16/10/2026 :
  0,000118 $ par question, P95 6,0 à 6,4 s sans cache, 0,11 s avec cache) ;
- coût de la collecte : mails de bilan et `collect_runs.cost_usd` (0,14 $ la
  première nuit, 0,0055 $ la suivante) ;
- la grille de prix Mistral utilisée par le code est dans `agent/watch.py`
  (`PRICES_USD_PER_MILLION`). Elle date de 2026 et donne un ordre de
  grandeur : certaines pages publiques donnent des prix plus élevés pour
  `mistral-medium` (juge de qualité), ce qui porterait la collecte à environ
  0,5 $ par mois. À vérifier sur la page des tarifs de Mistral.

**Sources (16/10/2026)** :
[tarifs Azure Container Apps](https://azure.microsoft.com/en-us/pricing/details/container-apps/),
[détail des tarifs Container Apps](https://www.devzero.io/blog/azure-container-apps-pricing),
[prix de la B1ms](https://www.bytebase.com/dbcost/azure-flexible/instance/B1ms/),
[offres de Static Web Apps](https://learn.microsoft.com/en-us/azure/static-web-apps/plans),
[prix de Static Web Apps Standard](https://subger.com/en/service/azure-static-web-apps),
[tarifs Mistral](https://developer.puter.com/tutorials/mistral-api-pricing/),
[autre relevé des tarifs Mistral](https://www.aipricing.guru/mistral-pricing/),
[prix de Mistral Large 3](https://anotherwrapper.com/llm-pricing/mistral-large-latest),
[compte gratuit Azure](https://azure.microsoft.com/pricing/purchase-options/azure-account).
