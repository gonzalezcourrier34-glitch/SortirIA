"""Revue humaine des commentaires des visiteurs (16/10/2026).

**Provenance** : nouveau module écrit pour SortirIA, sur le modèle de
`agent/proposals.py`.

Les commentaires sur les sorties sont modérés par Mistral Moderation : publiés
ou refusés sans intervention (`src/event/event_ratings.py`). Une modération
automatique se trompe parfois ; le mail du lundi liste les commentaires
refusés de la semaine, et un humain peut corriger :

    uv run python -m agent.commentaires                 # refusés des 30 derniers jours
    uv run python -m agent.commentaires --publies       # publiés des 30 derniers jours
    uv run python -m agent.commentaires --publier 12    # publie le n° 12, refusé à tort
    uv run python -m agent.commentaires --masquer 12    # masque le n° 12, publié à tort

La décision est gardée dans la colonne `moderation` (clé `revue_humaine` :
décision, statut d'avant, date) ; elle prend effet tout de suite sur le site
(l'API lit les commentaires publiés à chaque fiche).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from config.environment import load_environment


def describe(row: Mapping[str, Any]) -> str:
    moderation = row.get("moderation") or {}
    flagged = ", ".join(moderation.get("flagged") or [])
    human = moderation.get("revue_humaine") or {}
    notes = []
    if flagged:
        notes.append(f"signalé : {flagged}")
    if human:
        notes.append(f"revu le {str(human.get('le', ''))[:16]}")
    where = f", {row['city']}" if row.get("city") else ""
    suffix = f" ({' ; '.join(notes)})" if notes else ""
    return (f"n° {row['rating_id']} ({str(row.get('updated_at') or '')[:16]}) « {row.get('title')} »{where}, "
            f"{row.get('stars')}/5 : « {row.get('comment')} »{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Revue humaine des commentaires modérés par Mistral.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--publies", action="store_true", help="Liste les commentaires publiés (30 jours).")
    group.add_argument("--publier", type=int, metavar="N", help="Publie le commentaire N (refusé à tort).")
    group.add_argument("--masquer", type=int, metavar="N", help="Masque le commentaire N (publié à tort).")
    parser.add_argument("--jours", type=int, default=30, help="Période listée, en jours (30 par défaut).")
    args = parser.parse_args()

    load_environment()
    from agent.services import connect_postgres
    from src.event import event_repository as repository

    with connect_postgres() as connection:
        if args.publier is not None or args.masquer is not None:
            number = args.publier if args.publier is not None else args.masquer
            status = "publie" if args.publier is not None else "refuse"
            if repository.review_comment(connection, number, status):
                print(f"Commentaire n° {number} {'publié' if status == 'publie' else 'masqué'} "
                      "(décision gardée dans « moderation »). Visible tout de suite sur le site.")
            else:
                print(f"Pas de commentaire n° {number}.")
            return
        status = "publie" if args.publies else "refuse"
        rows = repository.fetch_comments(connection, status, args.jours)
        label = "publié" if args.publies else "refusé"
        if not rows:
            print(f"Aucun commentaire {label} ces {args.jours} derniers jours.")
        for row in rows:
            print(describe(row))


if __name__ == "__main__":
    main()
