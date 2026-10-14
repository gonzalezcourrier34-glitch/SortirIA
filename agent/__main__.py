"""Lancement manuel de la collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026).

    uv run python -m agent            # collecte complète (PostgreSQL requis)
    uv run python -m agent --essai    # sans écriture en base, pour tester
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
    args = parser.parse_args()

    load_environment()

    state = run_nightly_collection(sources=args.source, dry_run=args.essai)
    summary = {
        key: state.get(key)
        for key in ("status", "sources", "attempts", "preparation", "events_upserted",
                    "events_expired", "events_embedded", "failed_sources", "alerts", "errors", "run_id")
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
