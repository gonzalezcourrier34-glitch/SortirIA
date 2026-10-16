"""Propositions de l'agent validées par pull request (agent/pull_requests.py).

GitHub et la base sont remplacés par des faux : aucun appel réseau.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from agent import pull_requests as prs
from agent.report import build_report_email, proposal_lines

SOURCES = 'openagenda:\n    agenda_uids:\n      - "9969608"    # 30\n      - "4838751"    # 12\n    other: 1\n'
SYNONYMS = "# appris\nsynonymes:\n  cirqe: cirque\n"


def proposal(number: int, action: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"proposal_id": number, "created_at": "2026-10-19 02:10", "run_id": 12, "source_id": "openagenda",
            "kind": "test", "diagnosis": "vu par l'agent", "action": action, "status": "en_attente",
            "pr_number": None, "pr_url": "", **extra}


SYNONYM = {"type": "ajouter_synonyme", "word": "zumba", "target": "danse", "times": 4,
           "new": "zumba -> danse", "found_on": "2026-10-19"}
AGENDA = {"type": "ajouter_agenda", "new": "123456", "events": 23, "found_on": "2026-10-18"}


def files(path: str) -> str | None:
    return {prs.SOURCES_PATH: SOURCES, prs.SYNONYMS_PATH: SYNONYMS}.get(path)


# --- Le changement proposé ---------------------------------------------------

def test_synonym_change_is_what_the_command_line_would_write() -> None:
    path, content = prs.proposed_change(proposal(1, SYNONYM), files, lambda path: True)
    assert path == "config/synonymes.yaml"
    assert content.startswith(SYNONYMS) and "zumba: danse" in content and "cherché 4 fois" in content


def test_agenda_change_keeps_the_file_and_adds_one_line() -> None:
    path, content = prs.proposed_change(proposal(2, AGENDA), files, lambda path: True)
    assert path == "config/sources.yaml"
    added = [line for line in content.splitlines() if line not in SOURCES.splitlines()]
    assert len(added) == 1 and '"123456"' in added[0] and "découverte du 2026-10-18" in added[0]
    assert content.index('"123456"') < content.index("other: 1")


def test_partner_document_needs_the_file_in_the_repository() -> None:
    action = {"type": "valider_document_partenaire", "file": "plaquette.pdf", "sha256": "abc", "partner": "Le Bar",
              "events": 3, "new": "plaquette.pdf@abc"}
    path, content = prs.proposed_change(proposal(3, action), lambda path: '{\n  "a.pdf": "1"\n}\n',
                                        lambda path: True)
    assert path == "data/partenaires/_documents_valides.json"
    assert json.loads(content) == {"a.pdf": "1", "plaquette.pdf": "abc"} and content.endswith("}\n")
    reason = prs.proposed_change(proposal(3, action), lambda path: None, lambda path: False)
    assert isinstance(reason, str) and "commiter" in reason


def test_what_cannot_become_a_pull_request_says_why() -> None:
    assert "à la main" in prs.proposed_change(proposal(4, {"type": "autre"}), files, lambda path: True)
    url = {"type": "remplacer_url", "old": "https://absente.pdf", "new": "https://nouvelle.pdf"}
    assert "à corriger à la main" in prs.proposed_change(proposal(5, url), files, lambda path: True)
    known = {**SYNONYM, "word": "cirqe", "target": "cirque", "new": "cirqe -> cirque"}
    assert "déjà" in prs.proposed_change(proposal(6, known), files, lambda path: True)


def test_title_and_body_explain_how_to_decide() -> None:
    item = proposal(7, SYNONYM)
    assert prs.short_title(item) == "Agent n° 7 : synonyme « zumba » → « danse »"
    body = prs.pull_request_body(item, "config/synonymes.yaml")
    assert "vu par l'agent" in body and "**Merge**" in body and "**Close**" in body and "collecte n° 12" in body


# --- La nuit : décisions relues, PR ouvertes ---------------------------------

class FakeGitHub:
    def __init__(self, pulls: dict[int, dict[str, Any]] | None = None, fail_on: int | None = None) -> None:
        self.pulls = pulls or {}
        self.commits: list[tuple[str, str, bool]] = []
        self.deleted: list[str] = []
        self.fail_on = fail_on

    read_file = staticmethod(files)

    def file_exists(self, path: str) -> bool:
        return True

    def commit_on_branch(self, branch: str, path: str, content: str, message: str, *, exists: bool) -> str:
        if self.fail_on and branch.endswith(str(self.fail_on)):
            raise prs.GitHubError("GitHub POST /git/trees : 500")
        self.commits.append((branch, path, exists))
        return "sha"

    def open_pull_request(self, branch: str, title: str, body: str) -> dict[str, Any]:
        number = 100 + len(self.pulls)
        self.pulls[number] = {"number": number, "html_url": f"https://github.com/x/pull/{number}", "state": "open"}
        return self.pulls[number]

    def pull_request(self, number: int) -> dict[str, Any]:
        return self.pulls[number]

    def delete_branch(self, branch: str) -> None:
        self.deleted.append(branch)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[int, dict[str, Any]]:
    from src.event import event_repository as repository

    rows: dict[int, dict[str, Any]] = {}

    def set_status(connection: Any, number: int, status: str, *, decided_by: str = "", decided_at: Any = None) -> int:
        rows[number].update(status=status, decided_by=decided_by, decided_at=decided_at)
        return 1

    def set_pull(connection: Any, number: int, pr: int, url: str) -> int:
        rows[number].update(pr_number=pr, pr_url=url)
        return 1

    monkeypatch.setattr(repository, "fetch_proposals",
                        lambda connection, status="en_attente": [dict(r) for r in rows.values() if r["status"] == status])
    monkeypatch.setattr(repository, "set_proposal_status", set_status)
    monkeypatch.setattr(repository, "set_proposal_pull_request", set_pull)
    return rows


def test_a_pending_proposal_becomes_a_pull_request(store: dict) -> None:
    store[1] = proposal(1, SYNONYM)
    github = FakeGitHub()
    report = prs.sync_pull_requests(None, github)
    assert github.commits == [("agent/proposition-1", "config/synonymes.yaml", False)]
    assert store[1]["pr_number"] == 100 and store[1]["pr_url"].endswith("/pull/100")
    assert [item["proposal_id"] for item in report.opened] == [1] and report.waiting == report.opened
    # La nuit suivante : déjà ouverte, rien de nouveau.
    again = prs.sync_pull_requests(None, github)
    assert len(github.commits) == 1 and again.opened == [] and len(again.waiting) == 1


def test_merge_and_close_are_written_back_as_decisions(store: dict) -> None:
    store[1] = proposal(1, SYNONYM, pr_number=100, pr_url="u1")
    store[2] = proposal(2, AGENDA, pr_number=101, pr_url="u2")
    github = FakeGitHub({
        100: {"number": 100, "state": "closed", "merged": True, "merged_at": "2026-10-19T08:12:00Z",
              "html_url": "u1"},
        101: {"number": 101, "state": "closed", "merged": False, "closed_at": "2026-10-19T08:15:00Z",
              "html_url": "u2"}})
    report = prs.sync_pull_requests(None, github)
    assert store[1]["status"] == "appliquee" and store[1]["decided_by"] == "github"
    assert store[1]["decided_at"].hour == 8 and store[2]["status"] == "refusee"
    assert [item["status"] for item in report.decided] == ["appliquee", "refusee"]
    assert github.deleted == ["agent/proposition-1", "agent/proposition-2"]


def test_a_conflicting_pull_request_is_rebuilt_on_the_current_main(store: dict) -> None:
    store[1] = proposal(1, SYNONYM, pr_number=100, pr_url="u1")
    github = FakeGitHub({100: {"number": 100, "state": "open", "mergeable_state": "dirty", "html_url": "u1"}})
    report = prs.sync_pull_requests(None, github)
    assert github.commits == [("agent/proposition-1", "config/synonymes.yaml", True)]
    assert store[1]["status"] == "en_attente" and len(report.waiting) == 1


def test_errors_and_limits_never_stop_the_night(store: dict) -> None:
    store[1] = proposal(1, SYNONYM)
    store[2] = proposal(2, AGENDA)
    store[3] = proposal(3, {"type": "autre"})
    store[4] = proposal(4, {**SYNONYM, "word": "pilates", "target": "yoga", "new": "pilates -> yoga"})
    report = prs.sync_pull_requests(None, FakeGitHub(fail_on=1), max_new=1)
    assert report.errors == ["proposition n° 1 : GitHubError"]
    assert [item["proposal_id"] for item in report.opened] == [2]
    assert [item["proposal_id"] for item in report.manual] == [3]
    assert store[4]["pr_number"] is None   # au-delà du plafond de la nuit : la nuit suivante


# --- Le client GitHub (API simulée) ------------------------------------------

def test_github_client_commits_one_file_on_a_new_branch_and_reuses_an_open_pull_request() -> None:
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        path = request.url.path.removeprefix("/repos/moi/depot")
        routes = {
            ("GET", "/git/ref/heads/main"): (200, {"object": {"sha": "main1"}}),
            ("GET", "/git/commits/main1"): (200, {"tree": {"sha": "tree1"}}),
            ("POST", "/git/trees"): (201, {"sha": "tree2"}),
            ("POST", "/git/commits"): (201, {"sha": "commit2"}),
            ("POST", "/git/refs"): (201, {}),
            ("POST", "/pulls"): (422, {"message": "A pull request already exists"}),
            ("GET", "/pulls"): (200, [{"number": 7, "html_url": "https://github.com/moi/depot/pull/7"}]),
            ("POST", "/issues/7/labels"): (200, []),
            ("GET", "/contents/config/synonymes.yaml"): (
                200, {"type": "file", "content": base64.b64encode(SYNONYMS.encode()).decode()}),
            ("GET", "/contents/absent.txt"): (404, {"message": "Not Found"}),
        }
        status, payload = routes[(request.method, path)]
        return httpx.Response(status, json=payload)

    client = httpx.Client(base_url=prs.API, transport=httpx.MockTransport(handler))
    github = prs.GitHub("jeton", "moi/depot", client=client)
    assert github.read_file("config/synonymes.yaml") == SYNONYMS and github.read_file("absent.txt") is None
    assert github.commit_on_branch("agent/proposition-7", "config/synonymes.yaml", "x", "msg", exists=False) == "commit2"
    tree = next(body for method, path, body in calls if path.endswith("/git/trees"))
    assert tree["base_tree"] == "tree1" and tree["tree"][0]["path"] == "config/synonymes.yaml"
    commit = next(body for method, path, body in calls if path.endswith("/git/commits"))
    assert commit["parents"] == ["main1"]
    assert github.open_pull_request("agent/proposition-7", "titre", "corps")["number"] == 7


def test_no_token_no_pull_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN_AGENT", raising=False)
    assert prs.github_from_env() is None
    monkeypatch.setenv("GITHUB_TOKEN_AGENT", "jeton")
    monkeypatch.setenv("GITHUB_REPO", "moi/depot")
    github = prs.github_from_env()
    assert github is not None and github.repo == "moi/depot" and github.base == "main"


# --- Le mail du matin ---------------------------------------------------------

def test_the_morning_mail_links_each_pull_request() -> None:
    pulls = {"ouvertes": [{"proposal_id": 7, "title": "Agent n° 7 : synonyme", "url": "https://gh/pull/7"}],
             "en_attente": [{"proposal_id": 7, "title": "Agent n° 7 : synonyme", "url": "https://gh/pull/7"},
                            {"proposal_id": 5, "title": "Agent n° 5 : agenda", "url": "https://gh/pull/5"}],
             "decisions": [{"proposal_id": 3, "title": "Agent n° 3 : agenda", "status": "refusee"}],
             "a_la_main": [], "erreurs": []}
    text = "\n".join(proposal_lines({"pull_requests": pulls}))
    assert "2 pull request(s) à relire" in text and "https://gh/pull/7" in text and "(nouvelle)" in text
    assert "Agent n° 3 : agenda : refusée" in text and "agent.proposals" not in text
    subject, _ = build_report_email({"status": "success", "pull_requests": pulls})
    assert subject.endswith(", 1 proposition(s) à relire")
    without = "\n".join(proposal_lines({"proposals": [{"source_id": "x", "action": SYNONYM}]}))
    assert "uv run python -m agent.proposals" in without
