"""Tests du bilan par mail (sans serveur SMTP réel)."""

from __future__ import annotations

from typing import Any, ClassVar, Self

import pytest

import src.utils.mail_utils as mail
from agent import report as report_module
from agent.report import build_report_email, send_report_by_email, should_send
from agent.state import STATUS_FAILED, STATUS_PARTIAL, STATUS_SUCCESS

REPORT: dict[str, Any] = {
    "status": STATUS_PARTIAL,
    "started_at": "2026-10-15T02:00:03+02:00",
    "preparation": {"events_extracted": 1064, "events_rejected": 5, "rejection_reasons": {"pas_une_sortie": 5}},
    "events_upserted": 1059,
    "events_removed": 1,
    "events_expired": 0,
    "events_embedded": 12,
    "source_rows": [
        {"source_id": "openagenda", "status": "ok", "events_kept": 73, "attempts": 1},
        {"source_id": "datatourisme", "status": "ok", "events_kept": 986, "attempts": 1},
    ],
    "skipped_sources": ["ical"],
    "alerts": ["Baisse anormale pour openagenda : 2 événement(s) contre 80 la dernière fois."],
    "purged": {"requests": 0, "cache": 0, "runs": 0},
    "run_id": 8,
}

SMTP_ENV = {
    "REPORT_EMAIL_TO": "moi@exemple.fr, tuteur@exemple.fr",
    "SMTP_HOST": "smtp.exemple.fr",
    "SMTP_PORT": "587",
    "SMTP_USER": "moi@exemple.fr",
    "SMTP_PASSWORD": "secret-qui-ne-doit-pas-fuiter",
}


def test_subject_and_body() -> None:
    subject, body = build_report_email(REPORT)
    assert subject == "[SortirIA] Collecte PARTIEL : 1059 événement(s), 1 alerte(s)"
    assert "Collecte du 2026-10-15 à 02:00" in body
    assert "- datatourisme : ok, 986 événement(s), 1 essai(s)" in body
    assert "- non lancées (pas de configuration) : ical" in body
    assert "Baisse anormale pour openagenda" in body
    assert "Bilan n° 8" in body


def test_success_without_alert() -> None:
    subject, body = build_report_email({**REPORT, "status": STATUS_SUCCESS, "alerts": []})
    assert subject == "[SortirIA] Collecte succès : 1059 événement(s)"
    assert "- aucune" in body


@pytest.mark.parametrize(("status", "alerts", "when", "expected"), [
    (STATUS_SUCCESS, [], "toujours", True),
    (STATUS_SUCCESS, [], "alertes", False),
    (STATUS_SUCCESS, ["x"], "alertes", True),
    (STATUS_FAILED, [], "alertes", True),
])
def test_should_send(status, alerts, when, expected) -> None:
    assert should_send({"status": status, "alerts": alerts}, when) is expected


class FakeSMTP:
    """Serveur SMTP simulé : garde ce qu'on lui envoie."""

    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: int = 0, **kwargs: Any) -> None:
        self.host, self.port, self.calls, self.messages = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, message: Any) -> None:
        self.messages.append(message)


def test_mail_is_sent_with_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)
    FakeSMTP.instances = []
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)

    assert send_report_by_email(REPORT) is True
    (server,) = FakeSMTP.instances
    assert (server.host, server.port) == ("smtp.exemple.fr", 587)
    assert server.calls == ["starttls", ("login", "moi@exemple.fr")]
    (message,) = server.messages
    assert message["To"] == "moi@exemple.fr, tuteur@exemple.fr"
    assert message["Subject"].startswith("[SortirIA] Collecte PARTIEL")


def test_password_never_in_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)
    assert "secret" not in repr(mail.EmailSettings.from_env())


def test_not_configured_means_no_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert send_report_by_email(REPORT) is False


def test_smtp_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("serveur injoignable")

    monkeypatch.setattr(report_module, "send_email", broken)
    assert send_report_by_email(REPORT) is False


def test_weekly_search_report_section() -> None:
    from datetime import date

    from agent import watch
    from agent.report import search_report_lines

    lines = search_report_lines({"days": 7, "questions": 40, "not_exact": 6, "no_answer": 2,
                                 "unknown_words": [("zumba", 4), ("escalade", 2)],
                                 "unanswered": [("de la zumba", 3)]})
    text = "\n".join(lines)
    assert "sans réponse exacte : 6 (15%)" in text and "zumba (4), escalade (2)" in text
    assert "« de la zumba » (3)" in text
    assert search_report_lines({}) == [] and search_report_lines({"questions": 0}) == []
    assert watch.search_report_due(date(2026, 10, 19)) and not watch.search_report_due(date(2026, 10, 15))
