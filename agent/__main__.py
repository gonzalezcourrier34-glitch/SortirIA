"""Lancement manuel de la collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026).

    uv run python -m agent            # collecte complète (PostgreSQL requis)
    uv run python -m agent --essai    # sans écriture en base, pour tester
    uv run python -m agent --decouverte   # avec la découverte des agendas OpenAgenda (~6 min de plus)
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
    parser = argparse.ArgumentParser(description="Collecte nocturne SortirIA.")
    parser.add_argument("--essai", action="store_true", help="N'écrit rien en base.")
    parser.add_argument("--source", action="append", help="Source à collecter (répétable).")
    parser.add_argument("--decouverte", action="store_true",
                        help="Cherche aussi les nouveaux agendas OpenAgenda (fait d'office le dimanche).")
    args = parser.parse_args()

    load_environment()

    state = run_nightly_collection(sources=args.source, dry_run=args.essai,
                                   discover_agendas=args.decouverte)
    summary = {
        key: state.get(key)
        for key in ("status", "sources", "attempts", "source_attempts", "preparation", "events_upserted",
                    "events_removed", "events_expired", "events_embedded", "source_drops", "purged",
                    "coverage", "cost_usd", "llm_usage", "failed_sources", "alerts", "errors", "run_id")
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
