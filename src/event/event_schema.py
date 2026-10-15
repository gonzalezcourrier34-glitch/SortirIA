"""Structure d'un événement SortirIA.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `src/article/schema/article_schema.py` de CheckIt.AI.

Un `Event` est ce que la base stocke et ce que le RAG recherche. Les champs
suivent la table `sortiria.events` (voir `sql/sortiria/02_events.sql`).

Choix de conception :

- les dates sont des chaînes ISO 8601 avec fuseau (« 2026-10-24T20:30:00+02:00 ») :
  elles passent telles quelles en JSON, en base et dans les logs ;
- `time_known` vaut False quand la source ne donne qu'un jour sans heure :
  on évite ainsi d'afficher « 00:00 » comme si c'était l'heure réelle ;
- les prix sont en euros ; `is_free` est séparé car « gratuit » est l'un des
  filtres les plus demandés ;
- `audiences` dit à qui l'événement convient (« famille », « soiree ») : c'est
  ce qui permet de ne jamais proposer une soirée en boîte à une famille.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Champs sans lesquels un événement ne sert à rien à l'utilisateur.
REQUIRED_EVENT_FIELDS: tuple[str, ...] = ("title", "start_at", "url")


@dataclass(slots=True)
class Event:
    """Un événement (concert, spectacle, exposition...) prêt à être stocké."""

    # Identité
    event_id: str
    source: str
    source_event_id: str

    # Contenu
    title: str
    description: str = ""
    category: str = ""
    keywords: list[str] = field(default_factory=list)
    language: str = "fr"

    # Quand
    start_at: str = ""
    end_at: str = ""
    time_known: bool = True

    # Où
    venue_name: str = ""
    address: str = ""
    city: str = ""
    postal_code: str = ""
    latitude: float | None = None
    longitude: float | None = None

    # Combien
    price_text: str = ""
    price_min: float | None = None
    price_max: float | None = None
    is_free: bool | None = None

    # Pour qui, et dans quelles conditions (ajouté le 14/10/2026)
    status: str = "programme"            # programme, complet, annule, reporte...
    attendance: str = "sur_place"        # sur_place, en_ligne, mixte
    age_min: int | None = None
    age_max: int | None = None
    audiences: list[str] = field(default_factory=list)      # "famille", "soiree"
    accessibility: list[str] = field(default_factory=list)  # "moteur", "visuel"...

    # Partenaires : un événement mis en avant est toujours signalé comme tel.
    partner_id: int | None = None
    is_sponsored: bool = False
    # Fichier d'un partenaire : qui il est et d'où vient la ligne (nom, secteur,
    # fichier, empreinte, bilan du fichier). Sert au chargement en base (tables
    # partners et partner_imports) ; n'est pas une colonne de `events`.
    partner_info: dict[str, Any] = field(default_factory=dict)

    # Liens
    url: str = ""
    image_url: str = ""

    # Traçabilité
    extracted_at: str = ""
    # Producteur de la fiche et date de sa mise à jour, à citer avec la
    # source (Licence Ouverte 2.0) : ex. « ADT34 », « 2026-10-06 ».
    source_credit: str = ""
    source_updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convertit l'événement en dictionnaire sérialisable en JSON."""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Recrée un événement à partir d'un dictionnaire (champs inconnus ignorés)."""

        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})
