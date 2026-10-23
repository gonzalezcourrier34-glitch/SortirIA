"""Envoi de mails par SMTP (bibliothèque standard, sans dépendance).

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Aucun des
projets repris (CheckIt.AI, RAG OpenAgenda, Chess Agent) n'envoyait de mail.

Configuration dans le fichier `.env` :

    REPORT_EMAIL_TO=moi@exemple.fr          # un ou plusieurs, séparés par des virgules
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587                           # 587 : STARTTLS ; 465 : SSL direct
    SMTP_USER=moi@gmail.com
    SMTP_PASSWORD=...                       # Gmail : un « mot de passe d'application »
    SMTP_FROM=moi@gmail.com                 # facultatif, SMTP_USER par défaut

Le mot de passe n'est jamais écrit dans les journaux.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from src.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SMTP_PORT = 587
SMTP_TIMEOUT_S = 30


@dataclass(frozen=True, slots=True)
class EmailSettings:
    """Paramètres SMTP lus dans l'environnement.

    Le `__repr__` masque le mot de passe, pour qu'il n'apparaisse jamais dans
    les journaux.

    Attributes
    ----------
    recipients : tuple[str, ...]
        Les adresses des destinataires (`REPORT_EMAIL_TO`).
    host : str
        Le serveur SMTP (`SMTP_HOST`).
    port : int
        Le port SMTP : 587 pour STARTTLS, 465 pour SSL direct.
    user : str
        L'identifiant de connexion ; vide, pas d'authentification.
    password : str
        Le mot de passe SMTP.
    sender : str
        L'adresse d'expédition (`SMTP_FROM`, sinon l'identifiant, sinon le
        premier destinataire).
    """

    recipients: tuple[str, ...]
    host: str
    port: int
    user: str
    password: str
    sender: str

    @classmethod
    def from_env(cls) -> EmailSettings | None:
        """Lit le `.env`. Renvoie None si l'envoi n'est pas configuré.

        L'envoi est configuré dès qu'il y a au moins un destinataire et un
        serveur. Un port illisible est remplacé par 587.

        Returns
        -------
        EmailSettings | None
            Les paramètres lus, ou None s'il manque les destinataires ou le serveur.
        """

        recipients = tuple(addr.strip() for addr in os.getenv("REPORT_EMAIL_TO", "").split(",") if addr.strip())
        host = os.getenv("SMTP_HOST", "").strip()
        if not recipients or not host:
            return None
        try:
            port = int(os.getenv("SMTP_PORT", str(DEFAULT_SMTP_PORT)))
        except ValueError:
            port = DEFAULT_SMTP_PORT
        user = os.getenv("SMTP_USER", "").strip()
        return cls(
            recipients=recipients,
            host=host,
            port=port,
            user=user,
            password=os.getenv("SMTP_PASSWORD", ""),
            sender=os.getenv("SMTP_FROM", "").strip() or user or recipients[0],
        )

    def __repr__(self) -> str:  # le mot de passe n'apparaît jamais
        """Décrit les paramètres sans le mot de passe.

        Returns
        -------
        str
            Le serveur, le port, l'identifiant et les destinataires.
        """
        return f"EmailSettings(host={self.host!r}, port={self.port}, user={self.user!r}, recipients={self.recipients!r})"


def send_email(settings: EmailSettings, subject: str, body: str) -> None:
    """Envoie un mail texte. Lève une exception si l'envoi échoue.

    Le port 465 ouvre une connexion SSL directe ; les autres ports passent
    en STARTTLS.

    Parameters
    ----------
    settings : EmailSettings
        Les paramètres SMTP, en général issus de `EmailSettings.from_env`.
    subject : str
        L'objet du mail.
    body : str
        Le corps du mail, en texte brut.

    Raises
    ------
    smtplib.SMTPException
        Laissée remonter si la connexion, l'authentification ou l'envoi échoue
        (de même que `OSError` pour un serveur injoignable).
    """

    # Préparer le message
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content(body)

    # Ouvrir la connexion chiffrée (SSL direct sur 465, STARTTLS ailleurs)
    context = ssl.create_default_context()
    if settings.port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.host, settings.port, timeout=SMTP_TIMEOUT_S, context=context)
    else:
        server = smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_S)
    # S'authentifier si besoin, puis envoyer
    with server:
        if settings.port != 465:
            server.starttls(context=context)
        if settings.user:
            server.login(settings.user, settings.password)
        server.send_message(message)
    logger.info("Mail envoyé à %s : %s", ", ".join(settings.recipients), subject)
