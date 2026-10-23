"""Contraintes cochées dans l'interface : public, zone, période, gratuit.

**Provenance** : découpé de `app/search_service.py` le 23/10/2026 (un
fichier, un usage) ; code écrit pour SortirIA à partir du 14/10/2026.

Valeurs permises (`PUBLICS`, `ZONES`, `PERIODS`), leurs libellés, la classe
`SearchFilters` et les bornes d'une période (`period_bounds`). Module de base
de la recherche : il n'importe aucun autre module `app.search_*`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

# « enfant » (18/10/2026) : pas un public de la base, mais une partie de
# « famille » : ce qui est pour l'enfant (âge, « jeune public », stage ou atelier
# enfants), quand « Famille » est ce qui se fait ensemble. « soiree » reste
# compris dans la phrase (« un bar », « une fête »), sans bouton.
PUBLICS = ("famille", "enfant", "soiree", "ados", "seniors")
ZONES = {"montpellier": "montpellier", "thau": "bassin_de_thau"}
PERIODS = ("aujourdhui", "weekend", "7jours", "3mois")

PUBLIC_LABELS = {"famille": "famille", "enfant": "enfant", "soiree": "soirée", "ados": "ados",
                 "seniors": "seniors"}
ZONE_LABELS = {"montpellier": "Montpellier et sa métropole", "thau": "bassin de Thau"}
PERIOD_LABELS = {"aujourdhui": "aujourd'hui", "weekend": "ce week-end",
                 "7jours": "les 7 prochains jours", "3mois": "les 3 prochains mois"}


@dataclass(slots=True)
class SearchFilters:
    """Contraintes choisies dans l'interface (toutes facultatives).

    Attributes
    ----------
    public : str | None
        Public coché (une valeur de `PUBLICS`), ou aucun.
    zone : str | None
        Zone cochée (une clé de `ZONES`), ou aucune.
    period : str
        Période cochée (une valeur de `PERIODS`) ; par défaut les 3 prochains mois.
    free_only : bool
        Vrai pour ne garder que le gratuit.
    """

    public: str | None = None
    zone: str | None = None
    period: str = "3mois"
    free_only: bool = False

    def __post_init__(self) -> None:
        """Vérifie que les valeurs cochées sont connues.

        Raises
        ------
        ValueError
            Si le public, la zone ou la période n'est pas une valeur prévue.
        """
        if self.public not in (None, *PUBLICS):
            raise ValueError(f"public inconnu : {self.public}")
        if self.zone not in (None, *ZONES):
            raise ValueError(f"zone inconnue : {self.zone}")
        if self.period not in PERIODS:
            raise ValueError(f"période inconnue : {self.period}")

    def describe(self) -> str:
        """Résumé lisible, affiché à l'étape « comprendre ».

        Returns
        -------
        str
            La période, puis le public, la zone et « gratuit » s'ils sont cochés,
            séparés par « · ».
        """

        parts = [PERIOD_LABELS[self.period]]
        if self.public:
            parts.append(PUBLIC_LABELS[self.public])
        if self.zone:
            parts.append(ZONE_LABELS[self.zone])
        if self.free_only:
            parts.append("gratuit")
        return " · ".join(parts)


def period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
    """Début et fin (exclue) de la période, heure de Paris.

    « weekend » : du samedi 0 h au lundi 0 h ; le samedi ou le dimanche, ce
    week-end-ci (à partir de maintenant).

    Parameters
    ----------
    period : str
        Période cochée : « aujourdhui », « weekend », « 7jours » ou « 3mois »
        (toute autre valeur vaut 90 jours).
    now : datetime
        Heure de référence : le début n'est jamais avant elle.

    Returns
    -------
    tuple[datetime, datetime]
        Le début et la fin exclue de la période.
    """

    now = now.astimezone(PARIS)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "aujourdhui":
        return now, midnight + timedelta(days=1)
    if period == "weekend":
        saturday = midnight + timedelta(days=(5 - now.weekday()) % 7)
        if now.weekday() == 6:                      # dimanche : le week-end en cours
            saturday = midnight - timedelta(days=1)
        return max(now, saturday), saturday + timedelta(days=2)
    if period == "7jours":
        return now, now + timedelta(days=7)
    return now, now + timedelta(days=90)
