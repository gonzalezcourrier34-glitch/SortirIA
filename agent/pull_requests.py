"""Les propositions de l'agent, validées par pull request (16/10/2026).

**Provenance** : nouveau module écrit pour SortirIA.

L'agent ne modifie jamais sa configuration : il propose (`agent_proposals`).
Jusqu'ici, un humain décidait en ligne de commande (`agent.proposals`), puis
commitait le fichier modifié. Désormais, chaque nuit, l'agent ouvre **une pull
request par proposition** sur le dépôt :

- la PR contient le changement exact (une ligne de `config/sources.yaml`, de
  `config/synonymes.yaml` ou de `data/partenaires/_documents_valides.json`)
  et le diagnostic de l'agent ;
- la CI la teste (tests, évaluation de la recherche à 95 %) avant la décision ;
- **Merge** = accepter : la CD déploie, la collecte suivante l'utilise ;
  **Close** = refuser : l'agent ne la reproposera plus ;
- la nuit suivante, l'agent relit l'état de ses PR et note la décision dans
  la base (`status`, `decided_at`, `decided_by = 'github'`) ; une PR en
  conflit (le fichier a changé entre-temps) est refaite sur la branche
  principale à jour.

Le mail du matin donne le lien de chaque PR ouverte, et Grafana la liste.

Réglages (`.env`, secrets Container Apps en production) :

- `GITHUB_TOKEN_AGENT` : jeton GitHub « fine-grained » limité au dépôt, droits
  *Contents* et *Pull requests* en écriture. Absent : pas de PR, la ligne de
  commande reste le moyen de décider ;
- `GITHUB_REPO` (`gonzalezcourrier34-glitch/SortirIA` par défaut) et
  `GITHUB_BRANCHE` (`main`).

Rien ici ne doit faire échouer une nuit : chaque erreur est notée dans le
bilan et la proposition reste en attente.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

API = "https://api.github.com"
DEFAULT_REPO = "gonzalezcourrier34-glitch/SortirIA"
BRANCH_PREFIX = "agent/proposition-"
MAX_NEW_PER_NIGHT = 5
SOURCES_PATH = "config/sources.yaml"
SYNONYMS_PATH = "config/synonymes.yaml"
PARTNERS_DIR = "data/partenaires"
APPROVALS_FILE = "_documents_valides.json"


class GitHubError(RuntimeError):
    """Réponse inattendue de l'API GitHub (le message ne contient jamais le jeton)."""


class GitHub:
    """Les quelques appels à l'API REST de GitHub dont l'agent a besoin."""

    def __init__(self, token: str, repo: str = DEFAULT_REPO, base: str = "main", client: Any = None) -> None:
        """Préparer le client HTTP authentifié pour un dépôt.

        Parameters
        ----------
        token : str
            Jeton GitHub de l'agent (jamais recopié dans les messages d'erreur).
        repo : str
            Dépôt au format `propriétaire/nom`. Par défaut `DEFAULT_REPO`.
        base : str
            Branche principale. Par défaut `main`.
        client : Any
            Client HTTP déjà prêt (tests) ; `None` par défaut pour un `httpx.Client`.
        """
        import httpx

        self.repo, self.base = repo, base
        self.client = client or httpx.Client(base_url=API, timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "sortiria-agent"})

    def call(self, method: str, path: str, expected: tuple[int, ...] = (200, 201), **kwargs: Any) -> Any:
        """Appeler l'API du dépôt et contrôler le code de réponse.

        Parameters
        ----------
        method : str
            Méthode HTTP (`GET`, `POST`, `PATCH`, `DELETE`).
        path : str
            Chemin relatif au dépôt (`/pulls`, `/git/refs/...`).
        expected : tuple[int, ...]
            Codes acceptés. Par défaut 200 et 201.
        **kwargs
            Arguments transmis à `client.request` (`json`, `params`...).

        Returns
        -------
        Any
            Le JSON de la réponse, ou `None` si elle est vide.

        Raises
        ------
        GitHubError
            Si le code de réponse n'est pas dans `expected`.
        """
        response = self.client.request(method, f"/repos/{self.repo}{path}", **kwargs)
        if response.status_code not in expected:
            raise GitHubError(f"GitHub {method} {path} : {response.status_code} {response.text[:200]}")
        return response.json() if response.content else None

    def read_file(self, path: str) -> str | None:
        """Contenu d'un fichier texte sur la branche principale (None s'il n'existe pas).

        Parameters
        ----------
        path : str
            Chemin du fichier dans le dépôt.

        Returns
        -------
        str | None
            Le contenu décodé en UTF-8, ou `None` si le fichier n'existe pas.

        Raises
        ------
        GitHubError
            Si GitHub répond autre chose que 200 ou 404.
        """

        data = self.call("GET", f"/contents/{quote(path)}", (200, 404), params={"ref": self.base})
        if not data or "content" not in data:
            return None
        return base64.b64decode(data["content"]).decode("utf-8")

    def file_sha256(self, path: str) -> str | None:
        """Empreinte SHA-256 d'un fichier de la branche principale (None s'il n'existe pas).

        Un document partenaire n'est validé que s'il est celui que Mistral a lu.

        Parameters
        ----------
        path : str
            Chemin du fichier dans le dépôt.

        Returns
        -------
        str | None
            L'empreinte hexadécimale du contenu brut, ou `None` si le fichier n'existe pas.

        Raises
        ------
        GitHubError
            Si GitHub répond autre chose que 200 ou 404.
        """

        response = self.client.request("GET", f"/repos/{self.repo}/contents/{quote(path)}",
                                       params={"ref": self.base},
                                       headers={"Accept": "application/vnd.github.raw"})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubError(f"GitHub GET /contents/{path} : {response.status_code}")
        return hashlib.sha256(response.content).hexdigest()

    def head(self) -> str:
        """Identifiant du dernier commit de la branche principale.

        Returns
        -------
        str
            Le SHA du commit en tête de `base`.
        """
        return self.call("GET", f"/git/ref/heads/{self.base}")["object"]["sha"]

    def commit_on_branch(self, branch: str, path: str, content: str, message: str, *, exists: bool) -> str:
        """Créer un commit qui change un seul fichier et y placer la branche de la PR.

        Un commit sur la branche principale à jour, qui change un seul fichier ;
        la branche de la PR est créée, ou remise sur ce commit (PR en conflit).

        Parameters
        ----------
        branch : str
            Nom de la branche de la PR.
        path : str
            Chemin du fichier modifié dans le dépôt.
        content : str
            Nouveau contenu complet du fichier.
        message : str
            Message du commit.
        exists : bool
            Vrai si la branche existe déjà (elle est alors forcée sur le nouveau commit).

        Returns
        -------
        str
            Le SHA du commit créé.

        Raises
        ------
        GitHubError
            Si la création de la branche échoue pour une autre raison qu'une
            branche déjà existante (422), ou si un appel échoue.
        """

        # Construire le commit à partir de la tête de la branche principale
        parent = self.head()
        base_tree = self.call("GET", f"/git/commits/{parent}")["tree"]["sha"]
        tree = self.call("POST", "/git/trees", json={"base_tree": base_tree, "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": content}]})["sha"]
        commit = self.call("POST", "/git/commits", json={"message": message, "tree": tree, "parents": [parent]})["sha"]
        # Créer la branche ; si elle existe déjà, la forcer sur le nouveau commit
        if not exists:
            created = self.client.request("POST", f"/repos/{self.repo}/git/refs",
                                          json={"ref": f"refs/heads/{branch}", "sha": commit})
            if created.status_code == 201:
                return commit
            if created.status_code != 422:   # 422 : la branche existe déjà (nuit interrompue)
                raise GitHubError(f"GitHub POST /git/refs : {created.status_code} {created.text[:200]}")
        self.call("PATCH", f"/git/refs/heads/{branch}", json={"sha": commit, "force": True})
        return commit

    def open_pull_request(self, branch: str, title: str, body: str) -> dict[str, Any]:
        """Ouvrir la PR d'une branche (ou reprendre celle déjà ouverte) et l'étiqueter « agent ».

        Parameters
        ----------
        branch : str
            Branche de la proposition.
        title : str
            Titre de la PR.
        body : str
            Description de la PR (Markdown).

        Returns
        -------
        dict[str, Any]
            La PR telle que renvoyée par GitHub (`number`, `html_url`...).

        Raises
        ------
        GitHubError
            Si la PR ne peut être ni ouverte ni retrouvée.
        """
        response = self.client.request("POST", f"/repos/{self.repo}/pulls",
                                       json={"title": title, "head": branch, "base": self.base, "body": body})
        if response.status_code == 422:   # déjà ouverte (nuit interrompue) : on la reprend
            owner = self.repo.split("/")[0]
            found = self.call("GET", "/pulls", params={"head": f"{owner}:{branch}", "state": "open"})
            if found:
                return found[0]
        if response.status_code != 201:
            raise GitHubError(f"GitHub POST /pulls : {response.status_code} {response.text[:200]}")
        pull = response.json()
        try:
            self.call("POST", f"/issues/{pull['number']}/labels", json={"labels": ["agent"]})
        except GitHubError:
            logger.warning("Étiquette « agent » non posée sur la PR n° %s.", pull["number"])
        return pull

    def pull_request(self, number: int) -> dict[str, Any]:
        """Lire l'état d'une PR.

        Parameters
        ----------
        number : int
            Numéro de la PR.

        Returns
        -------
        dict[str, Any]
            La PR telle que renvoyée par GitHub (`state`, `merged`, `mergeable_state`...).
        """
        return self.call("GET", f"/pulls/{number}")

    def delete_branch(self, branch: str) -> None:
        """Supprimer une branche (sans erreur si elle n'existe plus).

        Parameters
        ----------
        branch : str
            Nom de la branche à supprimer.
        """
        self.call("DELETE", f"/git/refs/heads/{branch}", (204, 404, 422))


def github_from_env() -> GitHub | None:
    """Client GitHub si `GITHUB_TOKEN_AGENT` est réglé, sinon None (pas de PR).

    Returns
    -------
    GitHub | None
        Le client, réglé par `GITHUB_REPO` et `GITHUB_BRANCHE`, ou `None` sans jeton.
    """

    token = os.getenv("GITHUB_TOKEN_AGENT", "").strip()
    if not token:
        return None
    return GitHub(token, os.getenv("GITHUB_REPO", DEFAULT_REPO).strip() or DEFAULT_REPO,
                  os.getenv("GITHUB_BRANCHE", "main").strip() or "main")


# ---------------------------------------------------------------------------
# Le changement proposé : quel fichier, quel nouveau contenu
# ---------------------------------------------------------------------------

def action_of(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Lire l'action d'une proposition, stockée en dictionnaire ou en JSON.

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition.

    Returns
    -------
    dict[str, Any]
        L'action (vide si absente).
    """
    action = proposal.get("action") or {}
    return json.loads(action) if isinstance(action, str) else dict(action)


def target_path(action: Mapping[str, Any]) -> str | None:
    """Fichier du dépôt que modifie une action.

    Parameters
    ----------
    action : Mapping[str, Any]
        L'action de la proposition (clé `type`).

    Returns
    -------
    str | None
        Le chemin du fichier dans le dépôt, ou `None` pour un type qui ne
        peut pas devenir une PR.
    """
    kind = action.get("type")
    if kind in ("remplacer_url", "ajouter_agenda"):
        return SOURCES_PATH
    if kind == "ajouter_synonyme":
        return SYNONYMS_PATH
    if kind == "valider_document_partenaire":
        return f"{PARTNERS_DIR}/{APPROVALS_FILE}"
    return None


def approvals_text(current: str | None, file_name: str, sha256: str) -> str:
    """Ajouter un document au contenu de `_documents_valides.json`.

    `_documents_valides.json` avec ce document en plus (même format que
    `partner_documents.approve_document`).

    Parameters
    ----------
    current : str | None
        Contenu actuel du fichier (`None` ou vide s'il n'existe pas).
    file_name : str
        Nom du document validé.
    sha256 : str
        Empreinte du document.

    Returns
    -------
    str
        Le nouveau contenu JSON, trié par nom de document.

    Raises
    ------
    TypeError
        Si le contenu actuel n'est pas un objet JSON.
    """

    approvals = json.loads(current) if current and current.strip() else {}
    if not isinstance(approvals, dict):
        raise TypeError(f"{APPROVALS_FILE} n'est pas un objet JSON")
    approvals[file_name] = sha256
    return json.dumps(dict(sorted(approvals.items())), ensure_ascii=False, indent=2) + "\n"


def proposed_change(proposal: Mapping[str, Any], read_file: Any, file_sha256: Any) -> tuple[str, str] | str:
    """(chemin, nouveau contenu) du fichier modifié, ou la raison pour laquelle
    la proposition ne peut pas devenir une PR (elle se décide alors à la main).

    Les fonctions d'`agent.proposals` (celles de la ligne de commande) sont
    rejouées sur une copie du fichier de la branche principale : la PR
    contient exactement ce qu'aurait fait `--accepter`.

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition en attente.
    read_file : Any
        Lit un fichier de la branche principale (voir `GitHub.read_file`).
    file_sha256 : Any
        Calcule l'empreinte d'un fichier de la branche principale (voir
        `GitHub.file_sha256`).

    Returns
    -------
    tuple[str, str] | str
        (chemin, nouveau contenu) si la PR est possible, sinon la raison en clair.
    """

    from agent import proposals as cli

    # Trouver le fichier visé et lire sa version sur la branche principale
    action = action_of(proposal)
    path = target_path(action)
    if path is None:
        return f"action « {action.get('type')} » : à décider à la main."
    current = read_file(path)
    # Document partenaire : vérifier que le fichier du dépôt est celui que Mistral a lu
    if action["type"] == "valider_document_partenaire":
        if not action.get("file") or not action.get("sha256"):
            return "document sans nom ou sans empreinte : à décider à la main."
        found = file_sha256(f"{PARTNERS_DIR}/{action['file']}")
        if found is None:
            return f"{action['file']} n'est pas dans {PARTNERS_DIR} sur le dépôt : le commiter d'abord."
        if found != action["sha256"]:
            return (f"{action['file']} sur le dépôt n'est pas celui que Mistral a lu (empreinte différente) : "
                    "il sera relu et reproposé.")
        try:
            content = approvals_text(current, str(action["file"]), str(action["sha256"]))
        except (ValueError, TypeError) as error:
            return f"{APPROVALS_FILE} illisible ({error}) : à corriger à la main."
        if json.loads(content).get(str(action["file"])) == (json.loads(current) if current and current.strip()
                                                              else {}).get(str(action["file"])):
            # Déjà sur main (pas encore déployé) : une PR vide serait refusée par GitHub.
            return f"{action['file']} est déjà validé sur le dépôt : rien à proposer."
        return path, content
    if current is None and action["type"] != "ajouter_synonyme":
        return f"{path} introuvable sur le dépôt."
    # Autres actions : rejouer la commande `--accepter` sur une copie temporaire du fichier
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / Path(path).name
        if current is not None:
            copy.write_bytes(current.encode("utf-8"))
        if action["type"] == "remplacer_url":
            done, why = cli.replace_url_in_config(str(action.get("old")), str(action.get("new")), copy)
        elif action["type"] == "ajouter_agenda":
            done, why = cli.add_agenda_to_config(str(action.get("new")), action.get("events", "?"),
                                                 str(action.get("found_on") or "?"), copy)
        else:
            done, why = cli.add_synonym_to_config(str(action.get("word")), str(action.get("target")),
                                                  action.get("times", "?"), str(action.get("found_on") or "?"), copy)
        if not done:
            return why
        return path, copy.read_bytes().decode("utf-8")


def short_title(proposal: Mapping[str, Any]) -> str:
    """Titre court d'une proposition, pour la PR, son commit et le mail.

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition (`proposal_id`, `source_id`, `action`).

    Returns
    -------
    str
        « Agent n° N : ... », coupé à 120 caractères.
    """
    action = action_of(proposal)
    kind = action.get("type")
    if kind == "ajouter_synonyme":
        text = f"synonyme « {action.get('word')} » → « {action.get('target')} »"
    elif kind == "ajouter_agenda":
        text = f"ajouter l'agenda OpenAgenda {action.get('new')} ({action.get('events')} événements)"
    elif kind == "valider_document_partenaire":
        text = f"importer le document de {action.get('partner')} ({action.get('events')} séances)"
    else:
        text = f"nouvelle adresse pour {proposal.get('source_id') or 'une source'}"
    return f"Agent n° {proposal['proposal_id']} : {text}"[:120]


def pull_request_body(proposal: Mapping[str, Any], path: str) -> str:
    """Description Markdown de la PR : diagnostic, proposition, fichier, mode de décision.

    Parameters
    ----------
    proposal : Mapping[str, Any]
        La proposition (`proposal_id`, `diagnosis`, `created_at`, `run_id`, `action`).
    path : str
        Fichier modifié dans le dépôt.

    Returns
    -------
    str
        Le texte de la PR.
    """
    from agent.report import describe_action

    created = str(proposal.get("created_at") or "")[:16]
    run = proposal.get("run_id")
    return "\n".join([
        f"## Proposition n° {proposal['proposal_id']} de l'agent de collecte",
        "",
        f"**Ce que l'agent a vu** : {proposal.get('diagnosis', '')}",
        "",
        f"**Ce qu'il propose** : {describe_action(proposal)}",
        "",
        f"**Fichier modifié** : `{path}`" + (f" · nuit du {created}" if created else "")
        + (f", collecte n° {run}" if run else ""),
        "",
        "### Pour décider",
        "- **Merge** : appliquer. La CD déploie ; la collecte suivante l'utilise.",
        "- **Close** : refuser. L'agent ne le reproposera plus.",
        "",
        "La CI (tests, évaluation de la recherche à 95 %) tourne sur ce changement avant la décision.",
        "",
        "_Ouverte par l'agent de collecte de SortirIA (`agent/pull_requests.py`)._",
    ])


# ---------------------------------------------------------------------------
# La nuit : relire les décisions, ouvrir les nouvelles PR
# ---------------------------------------------------------------------------

@dataclass
class PullRequestReport:
    """Ce que le mail du matin et le bilan en disent.

    Attributes
    ----------
    opened : list[dict[str, Any]]
        PR ouvertes cette nuit.
    waiting : list[dict[str, Any]]
        PR en attente de décision (y compris celles de la nuit).
    decided : list[dict[str, Any]]
        PR fusionnées ou fermées depuis la nuit précédente, avec la décision notée.
    manual : list[dict[str, Any]]
        Propositions qui ne peuvent pas devenir une PR, avec la raison.
    errors : list[str]
        Erreurs rencontrées, une ligne par proposition.
    """

    opened: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    decided: list[dict[str, Any]] = field(default_factory=list)
    manual: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Le bilan avec les clés en français attendues par le mail et la base.

        Returns
        -------
        dict[str, Any]
            Clés `ouvertes`, `en_attente`, `decisions`, `a_la_main`, `erreurs`.
        """
        return {"ouvertes": self.opened, "en_attente": self.waiting, "decisions": self.decided,
                "a_la_main": self.manual, "erreurs": self.errors}


def decision_time(pull: Mapping[str, Any]) -> datetime | None:
    """Date de la décision sur une PR (fusion, sinon fermeture).

    Parameters
    ----------
    pull : Mapping[str, Any]
        La PR telle que renvoyée par GitHub.

    Returns
    -------
    datetime | None
        La date de fusion ou de fermeture, ou `None` si la PR est encore ouverte.
    """
    value = pull.get("merged_at") or pull.get("closed_at")
    return datetime.fromisoformat(str(value)) if value else None


def sync_pull_requests(connection: Any, github: GitHub, max_new: int = MAX_NEW_PER_NIGHT) -> PullRequestReport:
    """Relit les PR de l'agent, note les décisions, ouvre les PR manquantes.

    Chaque proposition est traitée sous un point de sauvegarde : une erreur est
    notée dans le bilan sans faire échouer la nuit ni annuler les autres.

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL.
    github : GitHub
        Client GitHub de l'agent.
    max_new : int
        Nombre maximal de PR ouvertes par nuit. Par défaut `MAX_NEW_PER_NIGHT` (5).

    Returns
    -------
    PullRequestReport
        Les PR ouvertes, en attente, décidées, les propositions à la main et les erreurs.
    """

    from agent.report import describe_action
    from src.event import event_repository as repository

    report = PullRequestReport()
    pending = repository.fetch_proposals(connection)
    for proposal in pending:
        number = proposal.get("pr_number")
        line = {"proposal_id": proposal["proposal_id"], "title": short_title(proposal),
                "description": describe_action(proposal)}
        # Un point de sauvegarde par proposition : une écriture ratée en base
        # n'annule pas les décisions déjà notées pour les autres.
        savepoint(connection, "SAVEPOINT agent_pr")
        try:
            # PR déjà ouverte : noter la décision (fusion, fermeture) ou la refaire si conflit
            if number:
                pull = github.pull_request(int(number))
                if pull.get("merged") or pull.get("merged_at"):
                    repository.set_proposal_status(connection, proposal["proposal_id"], "appliquee",
                                                   decided_by="github", decided_at=decision_time(pull))
                    report.decided.append({**line, "status": "appliquee", "url": pull.get("html_url", "")})
                    github.delete_branch(f"{BRANCH_PREFIX}{proposal['proposal_id']}")
                elif pull.get("state") == "closed":
                    repository.set_proposal_status(connection, proposal["proposal_id"], "refusee",
                                                   decided_by="github", decided_at=decision_time(pull))
                    report.decided.append({**line, "status": "refusee", "url": pull.get("html_url", "")})
                    github.delete_branch(f"{BRANCH_PREFIX}{proposal['proposal_id']}")
                else:
                    if pull.get("mergeable_state") == "dirty":
                        refresh_branch(github, proposal)
                    report.waiting.append({**line, "url": pull.get("html_url", "")})
                continue
            # Pas encore de PR : calculer le changement (ou la raison de décider à la main)
            change = proposed_change(proposal, github.read_file, github.file_sha256)
            if isinstance(change, str):
                report.manual.append({**line, "reason": change})
                continue
            if len(report.opened) >= max_new:   # le reste, la nuit suivante
                continue
            # Commit, ouverture de la PR, et lien noté dans la base
            path, content = change
            branch = f"{BRANCH_PREFIX}{proposal['proposal_id']}"
            github.commit_on_branch(branch, path, content, short_title(proposal), exists=False)
            pull = github.open_pull_request(branch, short_title(proposal), pull_request_body(proposal, path))
            repository.set_proposal_pull_request(connection, proposal["proposal_id"], int(pull["number"]),
                                                 str(pull.get("html_url", "")))
            entry = {**line, "url": pull.get("html_url", "")}
            report.opened.append(entry)
            report.waiting.append(entry)
        except Exception as error:  # noqa: BLE001 (une PR ratée ne fait jamais échouer la nuit)
            savepoint(connection, "ROLLBACK TO SAVEPOINT agent_pr")
            logger.warning("Proposition n° %s : PR impossible (%s).", proposal["proposal_id"], error)
            report.errors.append(f"proposition n° {proposal['proposal_id']} : {type(error).__name__}")
        else:
            savepoint(connection, "RELEASE SAVEPOINT agent_pr")
    return report


def savepoint(connection: Any, statement: str) -> None:
    """Point de sauvegarde PostgreSQL (rien sans vraie connexion, dans les tests).

    Parameters
    ----------
    connection : Any
        Connexion PostgreSQL, ou un objet sans `cursor` dans les tests.
    statement : str
        Instruction à exécuter (`SAVEPOINT`, `RELEASE SAVEPOINT`, `ROLLBACK TO SAVEPOINT`).
    """

    if connection is not None and hasattr(connection, "cursor"):
        with connection.cursor() as cursor:
            cursor.execute(statement)


def refresh_branch(github: GitHub, proposal: Mapping[str, Any]) -> None:
    """PR en conflit : le changement est refait sur la branche principale à jour.

    Si le changement n'est plus possible, la PR est laissée telle quelle et un
    avertissement est journalisé.

    Parameters
    ----------
    github : GitHub
        Client GitHub de l'agent.
    proposal : Mapping[str, Any]
        La proposition dont la PR est en conflit.
    """

    change = proposed_change(proposal, github.read_file, github.file_sha256)
    if isinstance(change, str):
        logger.warning("Proposition n° %s en conflit, non refaite : %s", proposal["proposal_id"], change)
        return
    path, content = change
    github.commit_on_branch(f"{BRANCH_PREFIX}{proposal['proposal_id']}", path, content,
                            short_title(proposal) + " (refaite sur main à jour)", exists=True)
