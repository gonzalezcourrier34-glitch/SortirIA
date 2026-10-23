"""Valider ou refuser les réparations proposées par l'agent.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). C'est le
« humain dans la boucle » de l'agent : il propose (agent/diagnosis.py), un
humain décide. Rien n'est modifié sans cette décision.

    uv run python -m agent.proposals                 # propositions en attente
    uv run python -m agent.proposals --accepter 3    # applique la proposition n° 3
    uv run python -m agent.proposals --refuser 3     # l'écarte

Depuis le 16/10/2026, l'agent ouvre aussi une pull request par proposition
(`agent/pull_requests.py`, si `GITHUB_TOKEN_AGENT` est réglé) : on décide alors
sur GitHub (Merge ou Close), et cette commande renvoie vers la PR.

Appliquer « remplacer_url » remplace l'ancienne adresse par la nouvelle dans
`config/sources.yaml`, seulement si l'ancienne y figure exactement une fois
(sinon : rien n'est touché, la proposition reste à traiter à la main).

Appliquer « valider_document_partenaire » (16/10/2026) inscrit le document
(PDF, Word) et son empreinte dans `_documents_valides.json`, dans le dossier
des partenaires : il est importé à la collecte suivante. Voir d'abord ce que
Mistral y a lu :
`uv run python -m src.extractors.datasets.source.partner_extractor --verifier data/partenaires/<fichier>`.

Appliquer « ajouter_agenda » (découverte du dimanche, 15/10/2026) ajoute
l'agenda à la fin de la liste `agenda_uids` d'OpenAgenda, avec un commentaire
(nombre d'événements, date de la découverte). Le fichier garde ses
commentaires et ses fins de ligne.

Appliquer « ajouter_synonyme » (apprentissage de la recherche, 19/10/2026)
ajoute « mot tapé : mot connu » à la fin de `config/synonymes.yaml`, avec un
commentaire (nombre de recherches, date de la proposition) ; la recherche relit
ce fichier dès qu'il change.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.environment import load_environment
from config.paths import SOURCES_FILE


def replace_url_in_config(old: str, new: str, path: Path = SOURCES_FILE) -> tuple[bool, str]:
    """Remplace une adresse dans la configuration. Renvoie (fait ?, explication).

    Le remplacement n'a lieu que si l'ancienne adresse figure exactement une
    fois ; le fichier est lu et écrit en octets pour garder ses fins de ligne.

    Parameters
    ----------
    old : str
        Adresse actuelle, à remplacer.
    new : str
        Nouvelle adresse.
    path : Path
        Fichier de configuration. Par défaut `config/sources.yaml` (`SOURCES_FILE`).

    Returns
    -------
    tuple[bool, str]
        Vrai si le fichier a été modifié, et une explication lisible.
    """

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
    """Ajoute un agenda à la fin de `agenda_uids`. Renvoie (fait ?, explication).

    La ligne ajoutée reprend l'indentation des précédentes et porte un
    commentaire (nombre d'événements, date de la découverte) ; les fins de
    ligne du fichier sont conservées.

    Parameters
    ----------
    uid : str
        Identifiant de l'agenda OpenAgenda.
    events : Any
        Nombre d'événements trouvés, recopié dans le commentaire.
    found_on : str
        Date de la découverte, recopiée dans le commentaire.
    path : Path
        Fichier de configuration. Par défaut `config/sources.yaml` (`SOURCES_FILE`).

    Returns
    -------
    tuple[bool, str]
        Vrai si l'agenda a été ajouté, et une explication lisible.
    """

    # Lire le fichier en gardant ses fins de ligne, et trouver l'unique liste agenda_uids
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(newline)
    starts = [index for index, line in enumerate(lines) if line.strip() == "agenda_uids:"]
    if len(starts) != 1:
        return False, f"{len(starts)} liste(s) agenda_uids dans {path.name} : à ajouter à la main."
    # Parcourir la liste jusqu'à sa dernière ligne (et refuser un doublon)
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
    # Insérer la nouvelle ligne après la dernière et réécrire le fichier
    entry = f'{indent}- "{uid}"'
    lines.insert(last + 1, f"{entry:<20} # {events} (découverte du {found_on})")
    path.write_bytes(newline.join(lines).encode("utf-8"))
    return True, f"{path.name} mis à jour : agenda {uid} ajouté à agenda_uids."


def apply_proposal(proposal: Mapping[str, Any], path: Path = SOURCES_FILE,
                   synonyms_path: Path | None = None) -> tuple[bool, str]:
    """Applique une proposition acceptée. Renvoie (appliquée ?, explication).

    Types reconnus : `remplacer_url`, `ajouter_agenda`,
    `valider_document_partenaire` et `ajouter_synonyme` ; les autres sont à
    faire à la main.

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition, dont `action` (dictionnaire ou JSON) décrit la modification.
    path : Path
        Fichier de configuration des sources (`remplacer_url`, `ajouter_agenda`).
        Par défaut `SOURCES_FILE`. Ne sert pas pour `valider_document_partenaire`.
    synonyms_path : Path | None
        Fichier des synonymes (`ajouter_synonyme`) ; `None` par défaut pour
        `config/synonymes.yaml` (`SYNONYMS_FILE`).

    Returns
    -------
    tuple[bool, str]
        Vrai si la modification a été appliquée, et une explication lisible.
    """

    # Lire l'action (stockée en JSON dans la base)
    action = proposal.get("action") or {}
    if isinstance(action, str):
        action = json.loads(action)
    # Appliquer selon le type d'action
    if action.get("type") == "remplacer_url" and action.get("old") and action.get("new"):
        return replace_url_in_config(str(action["old"]), str(action["new"]), path)
    if action.get("type") == "ajouter_agenda" and action.get("new"):
        return add_agenda_to_config(str(action["new"]), action.get("events", "?"),
                                    str(action.get("found_on") or "?"), path)
    if action.get("type") == "valider_document_partenaire" and action.get("file") and action.get("sha256"):
        from src.extractors.datasets.source.partner_documents import approve_document

        directory = partner_directory()
        if directory is None:
            return False, "dossier des partenaires introuvable dans la configuration : à valider à la main."
        return approve_document(directory, str(action["file"]), str(action["sha256"]))
    if action.get("type") == "ajouter_synonyme" and action.get("word") and action.get("target"):
        return add_synonym_to_config(str(action["word"]), str(action["target"]), action.get("times", "?"),
                                     str(action.get("found_on") or "?"), synonyms_path)
    return False, f"action « {action.get('type')} » à faire à la main."


def add_synonym_to_config(word: str, target: str, times: Any, found_on: str,
                          path: Path | None = None) -> tuple[bool, str]:
    """Ajoute « mot tapé : mot connu » à config/synonymes.yaml. Renvoie (fait ?, explication).

    La recherche relit ce fichier dès qu'il change : le synonyme sert à la
    question suivante, sans redémarrer l'API.

    Parameters
    ----------
    word : str
        Mot tapé par les visiteurs.
    target : str
        Mot connu de la base qu'il doit aussi chercher.
    times : Any
        Nombre de recherches, recopié dans le commentaire.
    found_on : str
        Date de la proposition, recopiée dans le commentaire.
    path : Path | None
        Fichier des synonymes ; `None` par défaut pour `config/synonymes.yaml`
        (`SYNONYMS_FILE`).

    Returns
    -------
    tuple[bool, str]
        Vrai si le synonyme a été ajouté, et une explication lisible.
    """

    from app.query_understanding import SYNONYMS_FILE, learned_synonyms, plain

    # Normaliser les deux mots et refuser ce qui est illisible ou déjà connu
    path = path or SYNONYMS_FILE
    word, target = plain(word), plain(target)
    if not word or not target or not re.fullmatch(r"[a-z0-9 ]+", f"{word} {target}"):
        return False, f"synonyme « {word} » -> « {target} » illisible : à ajouter à la main."
    if word in learned_synonyms(path):
        return False, f"« {word} » est déjà dans {path.name}."
    # Lire le fichier (ou partir d'un fichier vide) en gardant ses fins de ligne
    raw = path.read_bytes().decode("utf-8") if path.exists() else "synonymes:\n"
    newline = "\r\n" if "\r\n" in raw else "\n"
    if not re.search(r"^synonymes:", raw, re.MULTILINE):
        return False, f"clé « synonymes: » absente de {path.name} : à ajouter à la main."
    # Ajouter l'entrée commentée à la fin et réécrire le fichier
    entry = f"  {word}: {target}"
    raw = raw.rstrip("\r\n") + newline + f"{entry:<30} # cherché {times} fois (proposé le {found_on})" + newline
    path.write_bytes(raw.encode("utf-8"))
    return True, f"{path.name} mis à jour : « {word} » cherche aussi « {target} »."


def partner_directory() -> Path | None:
    """Dossier des fichiers des partenaires, d'après `config/sources.yaml`.

    Returns
    -------
    Path | None
        Le dossier de la source `partenaire`, ou `None` si elle est absente ou
        si son dossier ne peut pas être résolu.
    """

    from config.source_config import load_source_registry
    from src.extractors.datasets.dataset_file_utils import resolve_dataset_directory

    for source in load_source_registry(SOURCES_FILE, require_secrets=False).sources:
        if source.source_id == "partenaire":
            data = source.to_dict()
            try:
                return resolve_dataset_directory(data, str(data.get("name")))
            except (OSError, ValueError):
                return None
    return None


def describe_action(proposal: Mapping[str, Any]) -> str:
    """Une proposition en une ligne (mail de bilan, pull request, ligne de commande).

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition (`source_id`, `action` en dictionnaire ou en JSON).

    Returns
    -------
    str
        La description selon le type d'action ; par défaut, un remplacement d'adresse.
    """

    action = proposal.get("action") or {}
    if isinstance(action, str):
        action = json.loads(action)
    source = proposal.get("source_id") or "?"
    if action.get("type") == "ajouter_synonyme":
        return (f"recherche : « {action.get('word')} » cherchera aussi "
                f"« {action.get('target')} » ({action.get('nature', 'synonyme')}, "
                f"cherché {action.get('times', '?')} fois sans réponse)")
    if action.get("type") == "ajouter_agenda":
        wanted = f" ; cherché : « {action['word']} »" if action.get("word") else ""
        return (f"{source} : ajouter l'agenda {action.get('new')} "
                f"({action.get('events')} événement(s) dans la zone{wanted}) "
                f"- {action.get('url')}")
    if action.get("type") == "valider_document_partenaire":
        preview = "".join(f"\n      - {line}" for line in action.get("preview") or [])
        return (f"partenaire {action.get('partner')} : importer {action.get('file')} "
                f"({action.get('events')} séance(s) lue(s) par Mistral){preview}")
    return f"{source} : remplacer {action.get('old')} par {action.get('new')}"


def describe(proposal: Mapping[str, Any]) -> str:
    """Une ligne lisible par proposition.

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition (`proposal_id`, `created_at`, `action`, `pr_url`).

    Returns
    -------
    str
        Numéro, date et description de l'action, suivis du lien de la pull request s'il y en a une.
    """

    created = str(proposal.get("created_at") or "")[:16]
    link = f"\n      pull request : {proposal['pr_url']}" if proposal.get("pr_url") else ""
    return f"n° {proposal['proposal_id']} ({created}) {describe_action(proposal)}{link}"


def main() -> None:
    """Lister les propositions en attente, ou en accepter / refuser une.

    Une proposition qui a une pull request se décide sur GitHub : la commande
    renvoie alors vers la PR sans rien changer.
    """
    # Déclarer les options (une seule décision à la fois)
    parser = argparse.ArgumentParser(description="Réparations proposées par l'agent de collecte.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--accepter", type=int, metavar="N", help="Accepte et applique la proposition N.")
    group.add_argument("--refuser", type=int, metavar="N", help="Refuse la proposition N.")
    args = parser.parse_args()

    load_environment()
    from agent.services import connect_postgres
    from src.event import event_repository as repository

    with connect_postgres() as connection:
        # Lire les propositions en attente (la table peut ne pas encore exister)
        try:
            pending = {item["proposal_id"]: item for item in repository.fetch_proposals(connection)}
        except Exception as error:
            if type(error).__name__ != "UndefinedTable":
                raise
            print("La table des propositions n'existe pas encore : lancer d'abord "
                  "« uv run python -m scripts.init_db ».")
            return
        # Sans décision : lister les propositions
        if args.accepter is None and args.refuser is None:
            if not pending:
                print("Aucune proposition en attente.")
            for proposal in pending.values():
                print(describe(proposal))
            return
        # Retrouver la proposition visée
        number = args.accepter if args.accepter is not None else args.refuser
        proposal = pending.get(number)
        if proposal is None:
            print(f"Pas de proposition en attente n° {number}.")
            return
        if proposal.get("pr_url"):
            # Une pull request est ouverte : la décision se prend là-bas (Merge
            # ou Close), sinon la PR et la base diraient deux choses différentes.
            print(f"La proposition n° {number} a une pull request : {proposal['pr_url']}\n"
                  "Décider sur GitHub (Merge pour accepter, Close pour refuser) ; "
                  "la décision est notée la nuit suivante.")
            return
        if args.refuser is not None:
            repository.set_proposal_status(connection, number, "refusee")
            print(f"Proposition n° {number} refusée.")
            return
        # Accepter : appliquer, puis noter si c'est fait ou à faire à la main
        applied, explanation = apply_proposal(proposal)
        repository.set_proposal_status(connection, number, "appliquee" if applied else "acceptee")
        print(f"Proposition n° {number} {'appliquée' if applied else 'acceptée'} : {explanation}")
        if applied:
            print("La modification sera prise en compte à la prochaine collecte.")


if __name__ == "__main__":
    main()
