"""Valider ou refuser les réparations proposées par l'agent.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). C'est le
« humain dans la boucle » de l'agent : il propose (agent/diagnosis.py), un
humain décide. Rien n'est modifié sans cette décision.

    uv run python -m agent.proposals                 # propositions en attente
    uv run python -m agent.proposals --accepter 3    # applique la proposition n° 3
    uv run python -m agent.proposals --refuser 3     # l'écarte

Appliquer « remplacer_url » remplace l'ancienne adresse par la nouvelle dans
`config/sources.yaml`, seulement si l'ancienne y figure exactement une fois
(sinon : rien n'est touché, la proposition reste à traiter à la main).

Appliquer « ajouter_agenda » (découverte du dimanche, 15/10/2026) ajoute
l'agenda à la fin de la liste `agenda_uids` d'OpenAgenda, avec un commentaire
(nombre d'événements, date de la découverte). Le fichier garde ses
commentaires et ses fins de ligne.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.report import describe_action
from config.environment import load_environment
from config.paths import SOURCES_FILE


def replace_url_in_config(old: str, new: str, path: Path = SOURCES_FILE) -> tuple[bool, str]:
    """Remplace une adresse dans la configuration. Renvoie (fait ?, explication)."""

    raw = path.read_bytes()
    text = raw.decode("utf-8")
    count = text.count(old)
    if count != 1:
        return False, f"l'ancienne adresse figure {count} fois dans {path.name} : à corriger à la main."
    path.write_bytes(text.replace(old, new).encode("utf-8"))
    return True, f"{path.name} mis à jour : {old} -> {new}"


AGENDA_LINE = re.compile(r'^(\s*)-\s*["\']?(\d+)["\']?')


def add_agenda_to_config(uid: str, events: Any, found_on: str,
                         path: Path = SOURCES_FILE) -> tuple[bool, str]:
    """Ajoute un agenda à la fin de `agenda_uids`. Renvoie (fait ?, explication)."""

    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    starts = [index for index, line in enumerate(lines) if line.strip() == "agenda_uids:"]
    if len(starts) != 1:
        return False, f"{len(starts)} liste(s) agenda_uids dans {path.name} : à ajouter à la main."
    last, indent = starts[0], None
    for index in range(starts[0] + 1, len(lines)):
        match = AGENDA_LINE.match(lines[index])
        if not match:
            break
        if match.group(2) == uid:
            return False, f"l'agenda {uid} est déjà dans {path.name}."
        last, indent = index, match.group(1)
    if indent is None:
        return False, f"liste agenda_uids vide ou écrite autrement dans {path.name} : à ajouter à la main."
    entry = f'{indent}- "{uid}"'
    lines.insert(last + 1, f"{entry:<20} # {events} (découverte du {found_on})")
    path.write_bytes(newline.join(lines).encode("utf-8"))
    return True, f"{path.name} mis à jour : agenda {uid} ajouté à agenda_uids."


def apply_proposal(proposal: Mapping[str, Any], path: Path = SOURCES_FILE) -> tuple[bool, str]:
    """Applique une proposition acceptée. Renvoie (appliquée ?, explication)."""

    action = proposal.get("action") or {}
    if isinstance(action, str):
        action = json.loads(action)
    if action.get("type") == "remplacer_url" and action.get("old") and action.get("new"):
        return replace_url_in_config(str(action["old"]), str(action["new"]), path)
    if action.get("type") == "ajouter_agenda" and action.get("new"):
        return add_agenda_to_config(str(action["new"]), action.get("events", "?"),
                                    str(action.get("found_on") or "?"), path)
    return False, f"action « {action.get('type')} » à faire à la main."


def describe(proposal: Mapping[str, Any]) -> str:
    """Une ligne lisible par proposition."""

    created = str(proposal.get("created_at") or "")[:16]
    return f"n° {proposal['proposal_id']} ({created}) {describe_action(proposal)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Réparations proposées par l'agent de collecte.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--accepter", type=int, metavar="N", help="Accepte et applique la proposition N.")
    group.add_argument("--refuser", type=int, metavar="N", help="Refuse la proposition N.")
    args = parser.parse_args()

    load_environment()
    from agent.services import connect_postgres
    from src.event import event_repository as repository

    with connect_postgres() as connection:
        try:
            pending = {item["proposal_id"]: item for item in repository.fetch_proposals(connection)}
        except Exception as error:
            if type(error).__name__ != "UndefinedTable":
                raise
            print("La table des propositions n'existe pas encore : lancer d'abord "
                  "« uv run python -m scripts.init_db ».")
            return
        if args.accepter is None and args.refuser is None:
            if not pending:
                print("Aucune proposition en attente.")
            for proposal in pending.values():
                print(describe(proposal))
            return
        number = args.accepter if args.accepter is not None else args.refuser
        proposal = pending.get(number)
        if proposal is None:
            print(f"Pas de proposition en attente n° {number}.")
            return
        if args.refuser is not None:
            repository.set_proposal_status(connection, number, "refusee")
            print(f"Proposition n° {number} refusée.")
            return
        applied, explanation = apply_proposal(proposal)
        repository.set_proposal_status(connection, number, "appliquee" if applied else "acceptee")
        print(f"Proposition n° {number} {'appliquée' if applied else 'acceptée'} : {explanation}")
        if applied:
            print("La modification sera prise en compte à la prochaine collecte.")


if __name__ == "__main__":
    main()
