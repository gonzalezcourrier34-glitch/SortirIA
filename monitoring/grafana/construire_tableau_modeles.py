"""Construit le tableau Grafana « SortirIA - Modèles et coûts » (16/10/2026).

**Provenance** : écrit pour SortirIA, même format que les deux tableaux
existants (`provisioning/dashboards/sortiria-*.json`).

Les requêtes sont écrites ici, en Python, plutôt qu'à la main dans le JSON :
elles restent lisibles et testées (`tests/test_api/test_monitoring.py` les
exécute sur un vrai PostgreSQL). Pour régénérer le fichier :

    uv run python -m monitoring.grafana.construire_tableau_modeles

Toutes les données viennent de la base : `collect_runs` (coût et tokens par
modèle, chaque nuit), `rag_requests` (modèle, tokens, coût, version de la
consigne, mode dégradé, à chaque question), `feedback` et `event_ratings`
(avis), `agent_proposals` (propositions de l'agent et leurs pull requests).
Le coût Azure n'est pas dans la base : il se suit dans Azure (budget
avec alerte par mail, voir `monitoring/README.md`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).resolve().parent / "provisioning" / "dashboards" / "sortiria-modeles-couts.json"
DATASOURCE = {"type": "grafana-postgresql-datasource", "uid": "${source}"}

# Coût Mistral du mois, collecte de la nuit et recherche réunies.
MONTH_COST = """SELECT
  (SELECT COALESCE(sum(cost_usd), 0) FROM sortiria.collect_runs WHERE started_at >= date_trunc('month', now()))
+ (SELECT COALESCE(sum(cost_usd), 0) FROM sortiria.rag_requests WHERE asked_at >= date_trunc('month', now()))
  AS "coût du mois\""""

# Projection : le coût du mois rapporté aux jours écoulés, étendu au mois entier.
MONTH_PROJECTION = """WITH mois AS (
  SELECT
    (SELECT COALESCE(sum(cost_usd), 0) FROM sortiria.collect_runs WHERE started_at >= date_trunc('month', now()))
  + (SELECT COALESCE(sum(cost_usd), 0) FROM sortiria.rag_requests WHERE asked_at >= date_trunc('month', now())) AS cout,
    GREATEST(EXTRACT(EPOCH FROM now() - date_trunc('month', now())) / 86400, 1) AS jours_ecoules,
    EXTRACT(DAY FROM date_trunc('month', now()) + interval '1 month' - interval '1 day') AS jours_du_mois
)
SELECT cout / jours_ecoules * jours_du_mois AS "projection fin de mois" FROM mois"""

NIGHT_COST = """SELECT avg(cost_usd) AS "coût moyen d'une nuit"
FROM sortiria.collect_runs
WHERE status <> 'running' AND $__timeFilter(started_at)"""

QUESTION_COST = """SELECT avg(cost_usd) AS "coût moyen d'une question"
FROM sortiria.rag_requests
WHERE NOT from_cache AND cost_usd IS NOT NULL AND $__timeFilter(asked_at)"""

DAILY_COST = """SELECT jour AS time, sum(collecte) AS "collecte (nuit)", sum(recherche) AS "recherche (questions)"
FROM (
  SELECT date_trunc('day', started_at) AS jour, cost_usd AS collecte, 0::numeric AS recherche
  FROM sortiria.collect_runs WHERE $__timeFilter(started_at)
  UNION ALL
  SELECT date_trunc('day', asked_at), 0, COALESCE(cost_usd, 0)
  FROM sortiria.rag_requests WHERE $__timeFilter(asked_at)
) AS couts
GROUP BY 1 ORDER BY 1"""

# Tokens par modèle : la nuit (llm_usage = {modèle: {input, output}}) et à
# chaque question (llm_model, prompt_tokens, completion_tokens).
NIGHT_TOKENS = """SELECT date_trunc('day', r.started_at) AS time, m.key AS metric,
       sum(COALESCE((m.value ->> 'input')::bigint, 0) + COALESCE((m.value ->> 'output')::bigint, 0)) AS value
FROM sortiria.collect_runs AS r, jsonb_each(r.llm_usage) AS m
WHERE $__timeFilter(r.started_at)
GROUP BY 1, 2 ORDER BY 1"""

QUESTION_TOKENS = """SELECT date_trunc('day', asked_at) AS time, COALESCE(NULLIF(llm_model, ''), 'sans modèle') AS metric,
       sum(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS value
FROM sortiria.rag_requests
WHERE NOT from_cache AND $__timeFilter(asked_at)
GROUP BY 1, 2 ORDER BY 1"""

MODELS_TABLE = """SELECT 'collecte (nuit)' AS "où", m.key AS "modèle", count(DISTINCT r.run_id) AS "nuits ou questions",
       sum(COALESCE((m.value ->> 'input')::bigint, 0)) AS "tokens en entrée",
       sum(COALESCE((m.value ->> 'output')::bigint, 0)) AS "tokens en sortie"
FROM sortiria.collect_runs AS r, jsonb_each(r.llm_usage) AS m
WHERE $__timeFilter(r.started_at)
GROUP BY 1, 2
UNION ALL
SELECT 'recherche (questions)', COALESCE(NULLIF(llm_model, ''), 'sans modèle'), count(*),
       sum(COALESCE(prompt_tokens, 0)), sum(COALESCE(completion_tokens, 0))
FROM sortiria.rag_requests
WHERE NOT from_cache AND $__timeFilter(asked_at)
GROUP BY 1, 2
ORDER BY 1, 4 DESC"""

PROMPT_VERSIONS = """SELECT COALESCE(NULLIF(q.prompt_version, ''), 'inconnue') AS "version de la consigne",
       min(q.asked_at) AS "première question", max(q.asked_at) AS "dernière question",
       count(*) AS "questions",
       avg(q.degraded::int) AS "part sans Mistral",
       avg(q.latency_ms) FILTER (WHERE NOT q.from_cache) AS "latence moyenne (ms)",
       avg((f.rating = 1)::int) AS "pouces haut",
       count(f.feedback_id) AS "avis"
FROM sortiria.rag_requests AS q
LEFT JOIN sortiria.feedback AS f ON f.request_id = q.request_id AND f.event_id = ''
WHERE $__timeFilter(q.asked_at)
GROUP BY 1 ORDER BY 2 DESC"""

DEGRADED = """SELECT date_trunc('day', asked_at) AS time,
       avg(degraded::int) AS "réponses sans Mistral",
       avg((status <> 'ok')::int) AS "erreurs"
FROM sortiria.rag_requests
WHERE $__timeFilter(asked_at)
GROUP BY 1 ORDER BY 1"""

JUDGE = """SELECT started_at AS time, quality_score AS "juge de qualité (0 à 1)"
FROM sortiria.collect_runs
WHERE quality_score IS NOT NULL AND $__timeFilter(started_at)
ORDER BY 1"""

ANSWER_FEEDBACK = """SELECT count(*) AS "avis", avg((rating = 1)::int) AS "pouces haut"
FROM sortiria.feedback
WHERE event_id = '' AND $__timeFilter(created_at)"""

EVENT_RATINGS = """SELECT count(*) AS "notes", avg(stars) AS "moyenne (sur 5)"
FROM sortiria.event_ratings
WHERE $__timeFilter(created_at)"""

PENDING_COMMENTS = """SELECT count(*) AS "commentaires en attente"
FROM sortiria.event_ratings
WHERE comment_status = 'en_attente'"""

FEEDBACK_DAILY = """SELECT date_trunc('day', created_at) AS time,
       count(*) FILTER (WHERE rating = 1) AS "pouces haut",
       count(*) FILTER (WHERE rating = -1) AS "pouces bas"
FROM sortiria.feedback
WHERE event_id = '' AND $__timeFilter(created_at)
GROUP BY 1 ORDER BY 1"""

# Propositions de l'agent (16/10/2026) : en attente de décision, avec le lien
# de la pull request ouverte par l'agent (agent/pull_requests.py).
PROPOSALS_COUNTS = """SELECT
  count(*) FILTER (WHERE status = 'en_attente') AS "en attente",
  count(*) FILTER (WHERE status = 'appliquee' AND $__timeFilter(decided_at)) AS "appliquées",
  count(*) FILTER (WHERE status = 'refusee' AND $__timeFilter(decided_at)) AS "refusées"
FROM sortiria.agent_proposals"""

PROPOSALS_PENDING = """SELECT proposal_id AS "n°", created_at AS "proposée le",
       action ->> 'type' AS "type", diagnosis AS "ce que l'agent a vu",
       round((EXTRACT(EPOCH FROM now() - created_at) / 86400)::NUMERIC, 1) AS "attente (jours)",
       NULLIF(pr_url, '') AS "pull request"
FROM sortiria.agent_proposals
WHERE status = 'en_attente'
ORDER BY proposal_id DESC"""

PROPOSALS_DECIDED = """SELECT decided_at AS "décidée le", proposal_id AS "n°", action ->> 'type' AS "type",
       status AS "décision", NULLIF(decided_by, '') AS "par",
       round((EXTRACT(EPOCH FROM decided_at - created_at) / 3600)::NUMERIC, 1) AS "délai (heures)",
       NULLIF(pr_url, '') AS "pull request"
FROM sortiria.agent_proposals
WHERE status <> 'en_attente' AND decided_at IS NOT NULL AND $__timeFilter(decided_at)
ORDER BY decided_at DESC
LIMIT 50"""

#: Toutes les requêtes du tableau (les tests les exécutent sur PostgreSQL).
QUERIES: dict[str, str] = {
    "MONTH_COST": MONTH_COST, "MONTH_PROJECTION": MONTH_PROJECTION, "NIGHT_COST": NIGHT_COST,
    "QUESTION_COST": QUESTION_COST, "DAILY_COST": DAILY_COST, "NIGHT_TOKENS": NIGHT_TOKENS,
    "QUESTION_TOKENS": QUESTION_TOKENS, "MODELS_TABLE": MODELS_TABLE, "PROMPT_VERSIONS": PROMPT_VERSIONS,
    "DEGRADED": DEGRADED, "JUDGE": JUDGE, "ANSWER_FEEDBACK": ANSWER_FEEDBACK, "EVENT_RATINGS": EVENT_RATINGS,
    "PENDING_COMMENTS": PENDING_COMMENTS, "FEEDBACK_DAILY": FEEDBACK_DAILY,
    "PROPOSALS_COUNTS": PROPOSALS_COUNTS, "PROPOSALS_PENDING": PROPOSALS_PENDING,
    "PROPOSALS_DECIDED": PROPOSALS_DECIDED,
}

SOURCE_VARIABLE = {
    "name": "source", "label": "Base", "type": "datasource", "query": "grafana-postgresql-datasource",
    # Production par défaut (16/10/2026) : c'est elle qu'on surveille ; la base
    # locale reste au choix pour le développement.
    "current": {"selected": True, "text": "SortirIA production (Azure)", "value": "sortiria-prod"},
    "description": "Production (Azure, lecture seule) par défaut, ou base locale (docker compose).",
    "hide": 0, "refresh": 1, "regex": "", "skipUrlSync": False, "options": [],
}


class Layout:
    """Place les panneaux ligne après ligne (grille Grafana de 24 colonnes)."""

    def __init__(self) -> None:
        self.panels: list[dict[str, Any]] = []
        self.y = 0
        self.x = 0
        self.row_height = 0

    def _next_id(self) -> int:
        return len(self.panels) + 1

    def row(self, title: str) -> None:
        self._newline()
        self.panels.append({"id": self._next_id(), "type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.y += 1

    def _newline(self) -> None:
        if self.x:
            self.y += self.row_height
            self.x, self.row_height = 0, 0

    def add(self, panel: dict[str, Any], width: int, height: int) -> None:
        if self.x + width > 24:
            self._newline()
        panel["id"] = self._next_id()
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": width, "h": height}
        self.panels.append(panel)
        self.x += width
        self.row_height = max(self.row_height, height)


def target(sql: str, fmt: str = "table") -> list[dict[str, Any]]:
    return [{"refId": "A", "datasource": DATASOURCE, "editorMode": "code", "format": fmt,
             "rawQuery": True, "rawSql": sql}]


def stat(title: str, description: str, sql: str, unit: str, decimals: int = 2,
         steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"type": "stat", "title": title, "description": description, "datasource": DATASOURCE,
            "targets": target(sql),
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "none", "textMode": "auto"},
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": {
                "mode": "absolute", "steps": steps or [{"color": "green", "value": None}]}}, "overrides": []}}


def series(title: str, description: str, sql: str, unit: str, bars: bool = False,
           stacked: bool = False) -> dict[str, Any]:
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 30 if bars else 10,
              "pointSize": 5, "showPoints": "always", "spanNulls": True,
              "stacking": {"mode": "normal" if stacked else "none", "group": "A"}}
    return {"type": "timeseries", "title": title, "description": description, "datasource": DATASOURCE,
            "targets": target(sql, "time_series"),
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "fieldConfig": {"defaults": {"unit": unit, "custom": custom}, "overrides": []}}


def table(title: str, description: str, sql: str) -> dict[str, Any]:
    return {"type": "table", "title": title, "description": description, "datasource": DATASOURCE,
            "targets": target(sql), "options": {"showHeader": True, "cellHeight": "sm"},
            "fieldConfig": {"defaults": {}, "overrides": []}}


def with_pull_request_link(panel: dict[str, Any]) -> dict[str, Any]:
    """La colonne « pull request » devient un lien cliquable vers GitHub."""

    panel["fieldConfig"]["overrides"].append({
        "matcher": {"id": "byName", "options": "pull request"},
        "properties": [{"id": "links", "value": [
            {"title": "Ouvrir la pull request", "url": "${__value.raw}", "targetBlank": True}]}]})
    return panel


def build() -> dict[str, Any]:
    layout = Layout()
    layout.row("01 / Coûts Mistral (collecte de la nuit + recherche)")
    budget: list[dict[str, Any]] = [{"color": "green", "value": None}, {"color": "orange", "value": 5},
                                    {"color": "red", "value": 10}]
    layout.add(stat("Coût Mistral du mois", "Depuis le 1er du mois : collect_runs.cost_usd + rag_requests.cost_usd "
                    "(tarifs de agent/watch.py, estimation).", MONTH_COST, "currencyUSD", 2, budget), 6, 4)
    layout.add(stat("Projection fin de mois", "Coût du mois ramené aux jours écoulés, étendu au mois entier.",
                    MONTH_PROJECTION, "currencyUSD", 2, budget), 6, 4)
    layout.add(stat("Coût moyen d'une nuit", "Collectes terminées sur la période (budget d'alerte de l'agent : "
                    "MISTRAL_BUDGET_NUIT_USD, 0,50 $ par défaut).", NIGHT_COST, "currencyUSD", 4), 6, 4)
    layout.add(stat("Coût moyen d'une question", "Hors cache (une réponse en cache coûte 0 $).", QUESTION_COST,
                    "currencyUSD", 6), 6, 4)
    layout.add(series("Coût Mistral par jour", "Collecte de la nuit et recherche, empilées.", DAILY_COST,
                      "currencyUSD", bars=True, stacked=True), 24, 8)

    layout.row("02 / Modèles : qui consomme quoi")
    layout.add(series("Tokens par modèle, collecte de la nuit", "collect_runs.llm_usage : enrichissement "
                      "(small), embeddings (embed), juge (medium), PDF (small). Les tokens d'embedding sont "
                      "estimés (4 caractères par token).", NIGHT_TOKENS, "short", bars=True, stacked=True), 12, 8)
    layout.add(series("Tokens par modèle, recherche", "rag_requests : rédaction de la réponse, hors cache.",
                      QUESTION_TOKENS, "short", bars=True, stacked=True), 12, 8)
    layout.add(table("Modèles utilisés sur la période", "Une ligne par modèle et par usage.", MODELS_TABLE), 24, 7)

    layout.row("03 / Versions et fiabilité des modèles")
    layout.add(table("Versions de la consigne de rédaction en service", "PROMPT_VERSION de app/search_service.py "
                     ": comparer deux versions sur la latence, le mode dégradé et la satisfaction.",
                     PROMPT_VERSIONS), 24, 7)
    layout.add(series("Réponses sans Mistral et erreurs (part par jour)", "Mode dégradé : Mistral indisponible, "
                      "réponse simplifiée affichée.", DEGRADED, "percentunit"), 12, 8)
    layout.add(series("Juge de qualité de la nuit", "20 fiches enrichies notées par mistral-medium ; alerte "
                      "sous 0,8.", JUDGE, "percentunit"), 12, 8)

    layout.row("04 / Avis des visiteurs")
    layout.add(stat("Avis sur les réponses", "Pouces haut ou bas sur la période.", ANSWER_FEEDBACK, "short", 0), 6, 4)
    layout.add(stat("Notes des sorties", "Notes de 1 à 5 laissées après un événement.", EVENT_RATINGS, "short", 1),
               6, 4)
    layout.add(stat("Commentaires à modérer", "Modération Mistral en attente (repris la nuit suivante).",
                    PENDING_COMMENTS, "short", 0,
                    [{"color": "green", "value": None}, {"color": "orange", "value": 1}]), 6, 4)
    layout.add(series("Pouces haut et bas par jour", "Avis sur la réponse entière (feedback.event_id vide).",
                      FEEDBACK_DAILY, "short", bars=True, stacked=True), 24, 8)

    layout.row("05 / Propositions de l'agent (validation par pull request)")
    layout.add(stat("Propositions", "En attente (toutes) ; appliquées et refusées sur la période. L'agent ouvre "
                    "une pull request par proposition : Merge = appliquée, Close = refusée.", PROPOSALS_COUNTS,
                    "short", 0), 24, 4)
    layout.add(with_pull_request_link(table(
        "En attente de décision", "Ce que l'agent propose et attend ; cliquer sur la pull request pour décider.",
        PROPOSALS_PENDING)), 24, 7)
    layout.add(with_pull_request_link(table(
        "Décisions prises", "Qui a décidé (github ou ligne de commande) et en combien de temps.",
        PROPOSALS_DECIDED)), 24, 7)

    return {
        "uid": "sortiria-modeles-couts", "title": "SortirIA - Modèles et coûts", "tags": ["sortiria", "modeles", "couts"],
        "timezone": "Europe/Paris", "schemaVersion": 39, "version": 1, "editable": True, "refresh": "15m",
        "time": {"from": "now-30d", "to": "now"},
        "description": "Ce que coûte chaque modèle, qui le consomme, quelle version de consigne est en service et "
                       "ce qu'en pensent les visiteurs ; ce que l'agent propose et ce qui en a été décidé (collect_runs, "
                       "rag_requests, feedback, event_ratings, agent_proposals).",
        "templating": {"list": [SOURCE_VARIABLE]}, "annotations": {"list": []}, "panels": layout.panels,
    }


def add_source_variable(path: Path) -> None:
    """Ajoute le choix de la base (locale ou production) à un tableau existant."""

    dashboard = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(dashboard, ensure_ascii=False)
    text = text.replace('"uid": "sortiria-pg"', '"uid": "${source}"')
    dashboard = json.loads(text)
    variables = [item for item in dashboard.get("templating", {}).get("list", []) if item.get("name") != "source"]
    dashboard["templating"] = {"list": [SOURCE_VARIABLE, *variables]}
    path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in ("sortiria-collecte.json", "sortiria-recherche.json"):
        add_source_variable(OUTPUT.parent / name)
    print(f"Tableau écrit : {OUTPUT.name} ; choix de la base ajouté aux deux autres tableaux.")


if __name__ == "__main__":
    main()
