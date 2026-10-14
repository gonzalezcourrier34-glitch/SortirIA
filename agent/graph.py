"""Construction et lancement du graphe LangGraph de la collecte nocturne.

**Provenance** : nouveau module écrit pour SortirIA (13/10/2026), sur le modèle
de `graph.py` de Chess Agent (projet 13) : ce module ne contient aucune
logique métier, seulement l'enchaînement des nœuds.

    plan_sources -> extract_sources --(rien, essai restant)--> extract_sources
                                    --(éléments extraits)----> prepare -> load_events -> write_report
                                    --(rien, dernier essai)--> write_report
"""

from __future__ import annotations

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

PLAN = "plan_sources"
EXTRACT = "extract_sources"
PREPARE = "prepare"
LOAD = "load_events"
REPORT = "write_report"


def build_graph():
    """Assemble les nœuds et les transitions, puis compile le graphe."""

    builder = StateGraph(CollectionState)
    builder.add_node(PLAN, nodes.plan_sources)
    builder.add_node(EXTRACT, nodes.extract_sources)
    builder.add_node(PREPARE, nodes.prepare)
    builder.add_node(LOAD, nodes.load_events)
    builder.add_node(REPORT, nodes.write_report)

    builder.add_edge(START, PLAN)
    builder.add_edge(PLAN, EXTRACT)
    builder.add_conditional_edges(
        EXTRACT,
        route_after_extraction,
        {ROUTE_RETRY: EXTRACT, ROUTE_PREPARE: PREPARE, ROUTE_REPORT: REPORT},
    )
    builder.add_edge(PREPARE, LOAD)
    builder.add_edge(LOAD, REPORT)
    builder.add_edge(REPORT, END)
    return builder.compile()


def run_nightly_collection(
    services: CollectionServices | None = None,
    *,
    sources: list[str] | None = None,
    dry_run: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> CollectionState:
    """Lance une collecte complète et renvoie l'état final (statut, chiffres, alertes)."""

    initial: CollectionState = {"dry_run": dry_run, "max_attempts": max_attempts}
    if sources:
        initial["sources"] = sources
    return build_graph().invoke(
        initial,
        config={"configurable": {"services": services or build_default_services()}},
    )
