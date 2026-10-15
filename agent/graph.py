"""Construction et lancement du graphe LangGraph de la collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `graph.py` de Chess Agent (projet 13) : ce module ne contient aucune
logique métier, seulement l'enchaînement des nœuds.

    plan_sources -> extract_sources --(échec ou rien, essai restant)--> extract_sources
                                    --(éléments extraits)--> prepare -> load_events -> judge_quality -> watch -> write_report
                                    --(rien, dernier essai)--> watch -> write_report
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent import nodes
from agent.routing import (
    DEFAULT_MAX_ATTEMPTS,
    ROUTE_PREPARE,
    ROUTE_REPORT,
    ROUTE_RETRY,
    route_after_extraction,
)
from agent.services import CollectionServices, build_default_services
from agent.state import CollectionState
from agent.tracking import run_params

PLAN = "plan_sources"
EXTRACT = "extract_sources"
PREPARE = "prepare"
LOAD = "load_events"
JUDGE = "judge_quality"
WATCH = "watch"
REPORT = "write_report"


def timed(name: str, node: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Mesure la durée d'un nœud et l'ajoute à `node_durations` (suivi de l'agent).

    Un nœud joué plusieurs fois (nouvel essai d'extraction) cumule ses durées.
    """

    def run(state: CollectionState, config: RunnableConfig) -> dict[str, Any]:
        started = perf_counter()
        update = dict(node(state, config) or {})
        durations = dict(state.get("node_durations", {}))
        durations[name] = round(durations.get(name, 0.0) + perf_counter() - started, 3)
        update["node_durations"] = durations
        return update

    return run


def build_graph():
    """Assemble les nœuds et les transitions, puis compile le graphe."""

    builder = StateGraph(CollectionState)
    builder.add_node(PLAN, timed(PLAN, nodes.plan_sources))
    builder.add_node(EXTRACT, timed(EXTRACT, nodes.extract_sources))
    builder.add_node(PREPARE, timed(PREPARE, nodes.prepare))
    builder.add_node(LOAD, timed(LOAD, nodes.load_events))
    builder.add_node(JUDGE, timed(JUDGE, nodes.judge_quality))
    builder.add_node(WATCH, timed(WATCH, nodes.watch_collection))
    builder.add_node(REPORT, nodes.write_report)   # il enregistre les durées : pas mesuré lui-même

    builder.add_edge(START, PLAN)
    builder.add_edge(PLAN, EXTRACT)
    builder.add_conditional_edges(
        EXTRACT,
        route_after_extraction,
        {ROUTE_RETRY: EXTRACT, ROUTE_PREPARE: PREPARE, ROUTE_REPORT: WATCH},
    )
    builder.add_edge(PREPARE, LOAD)
    builder.add_edge(LOAD, JUDGE)
    builder.add_edge(JUDGE, WATCH)
    builder.add_edge(WATCH, REPORT)
    builder.add_edge(REPORT, END)
    return builder.compile()


def run_nightly_collection(
    services: CollectionServices | None = None,
    *,
    sources: list[str] | None = None,
    dry_run: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    discover_agendas: bool = False,
) -> CollectionState:
    """Lance une collecte complète et renvoie l'état final (statut, chiffres, alertes).

    La nuit est suivie de bout en bout par le traceur des services (MLflow en
    production) : ouvert avant le graphe, fermé avec l'état final.
    """

    services = services or build_default_services()
    initial: CollectionState = {"dry_run": dry_run, "max_attempts": max_attempts}
    if sources:
        initial["sources"] = sources
    if discover_agendas:
        initial["discover_agendas"] = True
    services.tracker.start({
        **run_params(services, dry_run=dry_run, max_attempts=max_attempts),
        "started_at": services.now().isoformat(timespec="minutes"),
    })
    state = build_graph().invoke(initial, config={"configurable": {"services": services}})
    services.tracker.finish(state)
    return state
