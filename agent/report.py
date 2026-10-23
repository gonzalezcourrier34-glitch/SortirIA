"""Bilan de la collecte envoyé par mail.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026).

Chaque nuit, l'agent envoie son bilan à l'adresse configurée dans le `.env`
(`REPORT_EMAIL_TO`, voir `src/utils/mail_utils.py`). Le sujet donne le statut
d'un coup d'œil ; le corps reprend les chiffres, le détail par source et les
alertes, comme la ligne écrite dans `collect_runs`.

`REPORT_EMAIL_WHEN` choisit quand envoyer :

- `toujours` (par défaut) : un bilan chaque nuit ;
- `alertes` : seulement si la collecte n'est pas un succès complet ou s'il y a
  une alerte.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from agent.proposals import describe_action
from agent.search_learning import subject_words
from agent.state import STATUS_FAILED, STATUS_PARTIAL, STATUS_SUCCESS
from src.logger import get_logger
from src.utils.mail_utils import EmailSettings, send_email

logger = get_logger(__name__)

STATUS_LABELS = {
    STATUS_SUCCESS: "succès",
    STATUS_PARTIAL: "PARTIEL",
    STATUS_FAILED: "ÉCHEC",
}


def should_send(report: Mapping[str, Any], when: str | None = None) -> bool:
    """Décide s'il faut envoyer le bilan (voir `REPORT_EMAIL_WHEN`).

    Parameters
    ----------
    report : Mapping[str, Any]
        Le bilan de la nuit (`status`, `alerts`).
    when : str | None
        `toujours` ou `alertes` ; `None` par défaut pour lire `REPORT_EMAIL_WHEN`
        (`toujours` si elle n'est pas réglée).

    Returns
    -------
    bool
        Vrai s'il faut envoyer le mail.
    """

    when = (when or os.getenv("REPORT_EMAIL_WHEN", "toujours")).strip().lower()
    if when == "alertes":
        return report.get("status") != STATUS_SUCCESS or bool(report.get("alerts"))
    return True


def build_report_email(report: Mapping[str, Any]) -> tuple[str, str]:
    """(sujet, corps) du mail de bilan.

    Le corps enchaîne les chiffres, les sources, les alertes, les propositions,
    les notes des clients, la qualité, les bilans du lundi (recherche, pouces
    bas, dérive), la couverture, le coût Mistral et les références du suivi.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le bilan de la nuit, tel qu'écrit par le nœud de bilan.

    Returns
    -------
    tuple[str, str]
        Le sujet du mail et son corps en texte brut.
    """

    # Rédiger le sujet : statut, nombre d'événements, alertes, PR à relire
    status = str(report.get("status") or "")
    label = STATUS_LABELS.get(status, status or "inconnu")
    started = str(report.get("started_at") or "")[:16].replace("T", " à ")
    upserted = report.get("events_upserted", 0)
    alerts = list(report.get("alerts") or [])

    subject = f"[SortirIA] Collecte {label} : {upserted} événement(s)"
    if alerts:
        subject += f", {len(alerts)} alerte(s)"
    new_pulls = len((report.get("pull_requests") or {}).get("ouvertes") or [])
    if new_pulls:
        subject += f", {new_pulls} proposition(s) à relire"

    # Chiffres de la nuit
    preparation = report.get("preparation") or {}
    lines = [
        f"Collecte du {started}" if started else "Collecte nocturne",
        f"Statut : {label}",
        "",
        "Chiffres",
        f"- extraits : {preparation.get('events_extracted', 0)}",
        f"- rejetés : {preparation.get('events_rejected', 0)} {dict(preparation.get('rejection_reasons') or {}) or ''}".rstrip(),
        f"- enregistrés : {upserted}",
        f"- disparus de leur source : {report.get('events_removed', 0)}",
        f"- passés retirés : {report.get('events_expired', 0)}",
        f"- en base : {report.get('events_total', '?')} (plafond : retirés {report.get('events_capped', 0)})",
        f"- enrichis par Mistral : {report.get('events_enriched', 0)}"
        + (f" (dont {(report.get('enrichment') or {}).get('not_outing', 0)} signalé(s) « pas une sortie »)"
           if (report.get("enrichment") or {}).get("not_outing") else ""),
        f"- embeddings calculés : {report.get('events_embedded', 0)}",
    ]
    if report.get("vocabulary_size"):
        lines.append(f"- vocabulaire des titres : {report['vocabulary_size']} mot(s) reconnus par la recherche")
    # Détail par source, y compris les sources non lancées ou coupées
    lines += ["", "Sources"]
    for row in report.get("source_rows") or []:
        lines.append(f"- {row['source_id']} : {row['status']}, {row['events_kept']} événement(s), {row['attempts']} essai(s)")
    skipped = report.get("skipped_sources") or []
    if skipped:
        lines.append(f"- non lancées (pas de configuration) : {', '.join(skipped)}")
    disabled = report.get("disabled_sources") or []
    if disabled:
        lines.append(f"- coupées sur ce serveur (SOURCES_DESACTIVEES) : {', '.join(disabled)}")

    # Alertes et propositions de l'agent
    lines += ["", "Alertes"]
    lines += [f"- {alert}" for alert in alerts] or ["- aucune"]

    lines += proposal_lines(report)

    # Retours des visiteurs et qualité jugée par Mistral
    ratings = report.get("ratings") or {}
    if ratings.get("total"):
        average = ratings.get("average_24h")
        lines += ["", f"Notes des clients : {ratings.get('new_24h', 0)} nouvelle(s) en 24 h"
                      + (f" (moyenne {float(average):.1f}/5)" if average is not None else "")
                      + f", {ratings['total']} au total"]

    quality = report.get("quality") or {}
    if quality.get("score") is not None:
        heading = (f"Qualité (juge Mistral, {quality.get('checked', 0)} événements tirés au hasard) : "
                   f"{float(quality['score']):.0%}")
        lines += ["", heading]
        lines += [f"- {problem}" for problem in quality.get("problems", [])]

    # Bilans du lundi : recherche, apprentissage, pouces bas
    lines += search_report_lines(report.get("search_report") or {})
    learning = report.get("search_learning") or []
    if learning:
        lines += ["", "Ce que l'agent en tire (les propositions sont plus haut)"]
        lines += [f"- {note}" for note in learning]
    lines += feedback_lines(report)
    # Le lundi : dérive des questions, des réponses et de la collecte (19/10/2026).
    from agent.drift import report_lines as drift_lines

    lines += drift_lines(report.get("drift_report") or {})

    # Couverture des 7 prochains jours, coût, purge et références du suivi
    coverage = report.get("coverage") or {}
    if coverage:
        lines += ["", "Sorties des 7 prochains jours (famille / soirée)"]
        for zone, label in (("montpellier", "Montpellier"), ("bassin_de_thau", "Bassin de Thau")):
            counts = coverage.get(zone, {})
            lines.append(f"- {label} : {counts.get('famille', 0)} / {counts.get('soiree', 0)}")

    usage = report.get("llm_usage") or {}
    if usage or report.get("cost_usd"):
        lines += ["", f"Coût Mistral estimé : {float(report.get('cost_usd') or 0):.4f} $"]
        for model, tokens in sorted(usage.items()):
            lines.append(f"- {model} : {tokens.get('input', 0)} tokens en entrée, {tokens.get('output', 0)} en sortie")

    purged = report.get("purged") or {}
    if any(purged.values()):
        lines += ["", f"Purge RGPD : {purged}"]
    if report.get("run_id") is not None:
        lines += ["", f"Bilan n° {report['run_id']} (tables collect_runs et collect_run_sources)."]
    if report.get("mlflow_run_id"):
        lines.append(f"Suivi MLflow : expérience « sortiria-collecte », exécution {report['mlflow_run_id']}.")
    return subject, "\n".join(lines) + "\n"


def search_report_lines(gaps: Mapping[str, Any]) -> list[str]:
    """Rédiger le bilan hebdomadaire de la recherche pour le mail.

    Bilan hebdomadaire de la recherche : ce que les utilisateurs ont cherché
    sans trouver. Des pistes à examiner, rien n'est changé automatiquement.

    Parameters
    ----------
    gaps : Mapping[str, Any]
        Bilan de `fetch_search_gaps` (`questions`, `not_exact`, `no_answer`,
        `unknown_words`, `unanswered`, `days`).

    Returns
    -------
    list[str]
        Les lignes du bilan (commençant par une ligne vide), ou une liste vide
        s'il n'y a eu aucune question.
    """

    # Chiffres globaux de la période
    if not gaps or not gaps.get("questions"):
        return []
    total, not_exact = int(gaps["questions"]), int(gaps.get("not_exact", 0))
    lines = ["", f"Recherche des {gaps.get('days', 7)} derniers jours",
             f"- questions posées : {total}",
             f"- sans réponse exacte : {not_exact} ({not_exact / total:.0%})",
             f"- sans aucune proposition sur le sujet : {gaps.get('no_answer', 0)}"]
    # Mots inconnus de la base (sans les mots vides), puis questions sans réponse exacte
    words = subject_words(gaps.get("unknown_words") or [])
    if words:
        lines.append("- mots cherchés qui ne figurent dans aucun titre (une source, un partenaire "
                     "ou un synonyme à envisager) :")
        lines.append("  " + ", ".join(f"{word} ({times})" for word, times in words))
    unanswered = gaps.get("unanswered") or []
    if unanswered:
        lines.append("- questions sans réponse exacte les plus fréquentes :")
        lines += [f"  « {question} » ({times})" for question, times in unanswered]
    return lines


def send_report_by_email(report: Mapping[str, Any]) -> bool:
    """Envoie le bilan si l'envoi est configuré et voulu. Renvoie True si envoyé.

    Un échec d'envoi est journalisé mais ne fait jamais échouer la collecte.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le bilan de la nuit.

    Returns
    -------
    bool
        Vrai si le mail est parti ; faux s'il n'est pas configuré, pas voulu,
        ou si l'envoi a échoué.
    """

    settings = EmailSettings.from_env()
    if settings is None:
        logger.info("Envoi du bilan par mail non configuré (REPORT_EMAIL_TO, SMTP_HOST).")
        return False
    if not should_send(report):
        logger.info("Collecte sans alerte : pas de mail (REPORT_EMAIL_WHEN=alertes).")
        return False
    subject, body = build_report_email(report)
    try:
        send_email(settings, subject, body)
    except Exception:
        logger.exception("Impossible d'envoyer le bilan par mail à %s.", ", ".join(settings.recipients))
        return False
    return True


def proposal_lines(report: Mapping[str, Any]) -> list[str]:
    """Les propositions de l'agent dans le mail.

    Avec les pull requests (16/10/2026, agent/pull_requests.py) : le lien de
    chaque PR à relire, et les décisions prises depuis la veille. Sans jeton
    GitHub : la commande à lancer, comme avant.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le bilan de la nuit (`proposals`, `pull_requests`).

    Returns
    -------
    list[str]
        Les lignes de la section, ou une liste vide s'il n'y a rien à décider.
    """

    # Sans pull requests : la liste des propositions et la commande à lancer
    pulls = report.get("pull_requests") or {}
    proposals = report.get("proposals") or []
    command_line = ["", "Propositions de l'agent (à valider)",
                    *[f"- {describe_action(proposal)}" for proposal in proposals],
                    "  Pour décider : uv run python -m agent.proposals (puis --accepter N ou --refuser N)"]
    if not pulls:
        return command_line if proposals else []
    lines: list[str] = []
    # GitHub injoignable cette nuit : les propositions restent listées, avec la commande.
    if proposals and not (pulls.get("en_attente") or pulls.get("a_la_main")):
        lines += command_line
    # PR à relire (les nouvelles de la nuit sont signalées)
    waiting = pulls.get("en_attente") or []
    opened = {item["proposal_id"] for item in pulls.get("ouvertes") or []}
    if waiting:
        lines += ["", f"Propositions de l'agent : {len(waiting)} pull request(s) à relire"]
        for item in waiting:
            new = " (nouvelle)" if item["proposal_id"] in opened else ""
            lines += [f"- {item['title']}{new}", f"  {item['url']}"]
        lines.append("  Merge pour accepter, Close pour refuser : la décision est notée la nuit suivante.")
    # Décisions prises, propositions sans PR possible, erreurs GitHub
    decided = pulls.get("decisions") or []
    if decided:
        lines += ["", "Décisions prises sur GitHub depuis la dernière nuit"]
        lines += [f"- {item['title']} : {'appliquée' if item['status'] == 'appliquee' else 'refusée'}"
                  for item in decided]
    manual = pulls.get("a_la_main") or []
    if manual:
        lines += ["", "Propositions à décider à la main (pas de pull request possible)"]
        lines += [f"- {item['title']} : {item['reason']}" for item in manual]
        lines.append("  uv run python -m agent.proposals (puis --accepter N ou --refuser N)")
    errors = pulls.get("erreurs") or []
    if errors:
        lines += ["", "Pull requests de l'agent : erreurs", *[f"- {error}" for error in errors]]
    return lines


def feedback_lines(report: Mapping[str, Any]) -> list[str]:
    """Le lundi : pouces bas rangés par cause, commentaires refusés à revoir (16/10/2026).

    Parameters
    ----------
    report : Mapping[str, Any]
        Le bilan de la nuit (`feedback_learning`, `refused_comments`).

    Returns
    -------
    list[str]
        Les lignes des deux sections, ou une liste vide si rien n'est à signaler.
    """

    # Pouces bas : résumé par cause, puis le détail
    lines: list[str] = []
    thumbs = report.get("feedback_learning") or []
    if thumbs:
        lines += ["", "Pouces bas de la semaine (cause lue par Mistral)", f"- {thumbs[0]}"]
        lines += [f"  {note}" for note in thumbs[1:]]
    # Commentaires refusés par la modération, raccourcis, avec la commande pour les publier
    refused = report.get("refused_comments") or []
    if refused:
        lines += ["", f"Commentaires refusés par la modération (7 jours) : {len(refused)}"]
        for item in refused:
            flagged = ", ".join(item.get("flagged") or []) or "?"
            comment = str(item.get("comment") or "")
            comment = comment if len(comment) <= 120 else comment[:117] + "..."
            lines.append(f"- n° {item['rating_id']} « {item.get('title')} », {item.get('stars')}/5 : "
                         f"« {comment} » (signalé : {flagged})")
        lines.append("  Refusé à tort ? uv run python -m agent.commentaires --publier N")
    return lines
