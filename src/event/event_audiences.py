"""Publics d'un événement (« famille », « soiree ») déduits par des règles simples.

**Provenance** : déplacé le 23/10/2026 depuis `src/event/openagenda_mapper.py`,
où `infer_audiences` avait été écrite le 14/10/2026. Tous les convertisseurs
(OpenAgenda, DATAtourisme, iCal, RSS, pages HTML, documents, partenaires)
l'utilisent : elle est maintenant dans un module commun, que chacun importe.

Règles plutôt qu'un modèle : la plupart des sources n'ont pas de champ
« public », et des règles se testent et s'expliquent. L'enrichissement par
Mistral ajoute ensuite `ados` et `seniors`, sans retirer ces publics.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.event.event_normalizer import PARIS, normalize_for_comparison, parse_event_datetime

# Mots qui signalent un public. Comparés sans accents ni majuscules, mot
# entier (« bal » ne doit pas reconnaître « balade »).
FAMILY_WORDS: tuple[str, ...] = (
    "jeune public", "enfant", "enfants", "famille", "familles", "familial",
    "kids", "bebe", "bebes", "marionnette", "marionnettes", "conte", "contes",
)
NIGHT_WORDS: tuple[str, ...] = (
    "soiree", "soirees", "clubbing", "club", "dj", "electro", "techno", "bal",
)

# Un événement qui commence à 21 h ou plus tard (ou avant 5 h) est une soirée.
NIGHT_START_HOUR = 21
NIGHT_END_HOUR = 5
# Âge maximal en dessous duquel un événement est jugé « famille ».
FAMILY_MAX_AGE = 12
# Âge minimal à partir duquel un événement est réservé aux adultes.
ADULT_MIN_AGE = 18


def _contains_word(text: str, words: Sequence[str]) -> bool:
    """Dire si le texte contient l'un des mots (ou groupes de mots), en mot entier.

    Parameters
    ----------
    text : str
        Texte déjà normalisé (`normalize_for_comparison`).
    words : Sequence[str]
        Mots cherchés, normalisés eux aussi.

    Returns
    -------
    bool
        Vrai si au moins un mot est présent.
    """
    padded = f" {text} "
    return any(f" {word} " in padded for word in words)


def infer_audiences(
    texts: Sequence[str],
    start_at: str,
    time_known: bool,
    age_min: int | None,
    age_max: int | None,
) -> list[str]:
    """Déduit les publics (« famille », « soiree ») d'un événement.

    Un partenaire peut, lui, fournir ses publics directement.

    - famille : âge maximal <= 12 ans, ou un mot comme « jeune public » ;
      jamais si l'âge minimal est 18 ans ou plus ;
    - soiree : début à 21 h ou plus tard, ou un mot comme « soirée », « DJ ».

    Parameters
    ----------
    texts : Sequence[str]
        Textes où chercher les mots (titre, catégorie, mots-clés...).
    start_at : str
        Début de l'événement, en ISO 8601.
    time_known : bool
        Vrai si l'heure de début est connue (sinon elle n'est pas prise en compte).
    age_min : int or None
        Âge minimal, s'il est connu.
    age_max : int or None
        Âge maximal, s'il est connu.

    Returns
    -------
    list[str]
        Les publics déduits, parmi « famille » et « soiree ».
    """

    text = normalize_for_comparison(" ".join(texts))
    audiences: list[str] = []

    # Famille : âge maximal bas ou mot du jeune public, sauf événement réservé aux adultes
    adults_only = age_min is not None and age_min >= ADULT_MIN_AGE
    if not adults_only and (
        (age_max is not None and age_max <= FAMILY_MAX_AGE) or _contains_word(text, FAMILY_WORDS)
    ):
        audiences.append("famille")

    # Soirée : début tardif (heure de Paris) ou mot de la nuit
    start = parse_event_datetime(start_at)
    hour = start.astimezone(PARIS).hour if time_known and start else None
    late_start = hour is not None and (hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR)
    if late_start or _contains_word(text, NIGHT_WORDS):
        audiences.append("soiree")
    return audiences
