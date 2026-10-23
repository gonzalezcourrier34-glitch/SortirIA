"""Lancement manuel de la collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026).

    uv run python -m agent            # collecte complète (PostgreSQL requis)
    uv run python -m agent --essai    # sans écriture en base, pour tester
    uv run python -m agent --decouverte   # avec la découverte des agendas OpenAgenda (~6 min de plus)
    uv run python -m agent --bilan-recherche   # ce que les utilisateurs ont cherché sans trouver (7 j)
    uv run python -m agent --bilan-recherche --apprendre   # + propositions de l'agent (fait d'office le lundi)
"""

from __future__ import annotations

import argparse
import json

from agent.graph import run_nightly_collection

# Charge le fichier .env (DATABASE_URL, MISTRAL_API_KEY, OPENAGENDA_API_KEY)
# avant toute chose : sans cette ligne, la connexion à la base dépendait de
# l'ordre des imports du moteur d'extraction.
from config.environment import load_environment


def main() -> None:
    """Lire les options de la ligne de commande et lancer la collecte ou le bilan.

    Avec `--bilan-recherche`, n'affiche que le bilan de la recherche (voir
    `print_search_report`) ; sinon lance la collecte nocturne complète et
    affiche son résumé en JSON.
    """
    # Déclarer les options de la ligne de commande
    parser = argparse.ArgumentParser(description="Collecte nocturne SortirIA.")
    parser.add_argument("--essai", action="store_true", help="N'écrit rien en base.")
    parser.add_argument("--source", action="append", help="Source à collecter (répétable).")
    parser.add_argument("--decouverte", action="store_true",
                        help="Cherche aussi les nouveaux agendas OpenAgenda (fait d'office le dimanche).")
    parser.add_argument("--bilan-recherche", action="store_true",
                        help="Affiche seulement le bilan de la recherche (envoyé d'office le lundi).")
    parser.add_argument("--apprendre", action="store_true",
                        help="Avec --bilan-recherche : l'agent en tire des propositions (synonymes, agendas).")
    args = parser.parse_args()

    load_environment()

    # Mode bilan seul : pas de collecte
    if args.bilan_recherche:
        print_search_report(learn=args.apprendre)
        return

    # Lancer la collecte, puis n'afficher que les clés utiles de l'état final
    state = run_nightly_collection(sources=args.source, dry_run=args.essai,
                                   discover_agendas=args.decouverte)
    summary = {
        key: state.get(key)
        for key in ("status", "sources", "attempts", "source_attempts", "preparation", "events_upserted",
                    "events_removed", "events_expired", "events_embedded", "source_drops", "purged",
                    "coverage", "cost_usd", "llm_usage", "failed_sources", "alerts", "errors", "run_id")
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def print_search_report(days: int = 7, learn: bool = False) -> None:
    """Afficher le bilan de la recherche à la demande.

    Bilan de la recherche à la demande : vocabulaire recalculé, puis ce que
    les utilisateurs ont cherché sans trouver ces `days` derniers jours.

    `learn` : l'agent en tire aussi ses propositions (synonymes, agendas à
    lire), enregistrées en attente de validation comme celles de la nuit.

    Parameters
    ----------
    days : int
        Période examinée, en jours. Par défaut 7 jours.
    learn : bool
        Si vrai, l'agent tire de ce bilan des propositions et les enregistre en
        attente de validation. Par défaut faux.
    """

    from datetime import datetime

    from agent.proposals import describe_action
    from agent.report import search_report_lines
    from agent.services import connect_postgres, learn_from_week
    from src.event import event_repository as repository
    from src.event.event_normalizer import PARIS

    # Recalculer le vocabulaire et lire les recherches sans réponse et les pouces bas
    with connect_postgres() as connection:
        size = repository.refresh_title_vocabulary(connection)
        gaps = repository.fetch_search_gaps(connection, days)
        gaps["thumbs_down"] = repository.fetch_thumbs_down(connection, days)
    print(f"Vocabulaire des titres : {size} mot(s) reconnus par la recherche.")
    print("\n".join(search_report_lines(gaps)).strip() or f"Aucune question ces {days} derniers jours.")
    if not learn:
        return
    # Tirer les propositions de la semaine et les enregistrer en attente de validation
    learning = learn_from_week(gaps, datetime.now(PARIS).date())
    with connect_postgres() as connection:
        repository.record_proposals(connection, None, learning.proposals)
    # Afficher propositions et remarques
    print("\nCe que l'agent en tire")
    for proposal in learning.proposals:
        print(f"- proposition : {describe_action(proposal)}")
    for note in learning.notes:
        print(f"- {note}")
    for note in getattr(learning, "feedback_notes", []) or []:
        print(f"- pouces bas : {note}")
    if not learning.proposals and not learning.notes:
        print("- rien à proposer (aucun mot cherché au moins 2 fois et absent de la base).")
    if learning.proposals:
        print("Pour décider : uv run python -m agent.proposals (puis --accepter N ou --refuser N)")


if __name__ == "__main__":
    main()
