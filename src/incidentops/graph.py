"""LangGraph wiring: supervisor hub + specialist spokes, checkpointed, with a human gate."""

import time
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .agents.analysts import analyst_node
from .agents.critic import critic_node
from .agents.executor import executor_node
from .agents.planner import planner_node
from .agents.supervisor import supervisor_node
from .deps import Deps
from .resilience import SimulatedCrash
from .state import IncidentState, Plan

SPECIALISTS = [
    "log_analyst",
    "metrics_analyst",
    "runbook_retriever",
    "planner",
    "critic",
    "executor",
]
ROUTES = [*SPECIALISTS, "human_approval", "finalize", "escalate"]


def human_approval_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    """Pauses the graph. State is checkpointed; resume works from any process."""
    decision = interrupt(
        {
            "incident_id": state["incident_id"],
            "plan": state["plan"],
            "hypotheses": state["hypotheses"][:2],
            "critic": state["critic_feedback"][-1],
            "dry_run": state["dry_run"],
        }
    )
    kind = decision.get("decision")
    by = decision.get("by", "human")
    deps.trace.emit(state["incident_id"], "human_approval", "decision", decision=kind, by=by)
    if kind == "approve":
        return {"approval_status": "approved", "approval_by": by}
    if kind == "edit":
        edited = Plan.model_validate(decision["plan"])  # a bad edit raises -> caller sees the error
        return {"approval_status": "edited", "approval_by": by, "plan": edited.model_dump()}
    return {"approval_status": "rejected", "approval_by": by}


def escalate_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    status = "rejected" if state["approval_status"] == "rejected" else "escalated"
    deps.trace.emit(
        state["incident_id"],
        "escalate",
        "escalate",
        status=status,
        reason=state["escalation_reason"],
    )
    return {"status": status}


def finalize_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    deps.trace.emit(state["incident_id"], "finalize", "done", status=state["status"])
    return {}


def _instrument(
    name: str, fn: Callable[[IncidentState], dict[str, Any]], deps: Deps, safe: bool
) -> Any:
    """Crash hook + active-time accounting + 'a specialist failure never crashes the graph'."""

    def wrapped(state: IncidentState) -> dict[str, Any]:
        deps.maybe_crash(name)
        t0 = time.monotonic()
        try:
            upd = fn(state)
        except (GraphBubbleUp, SimulatedCrash):
            raise
        except Exception as e:  # noqa: BLE001
            if not safe:
                raise
            err = {"node": name, "kind": "exception", "message": f"{type(e).__name__}: {e}"}
            deps.trace.emit(state["incident_id"], name, "error", **err)
            upd = {"errors": state["errors"] + [err]}
            if name in ("log_analyst", "metrics_analyst", "runbook_retriever"):
                upd["done_agents"] = state["done_agents"] + [
                    name
                ]  # route around the failed specialist
        upd["active_seconds"] = state["active_seconds"] + (time.monotonic() - t0)
        return upd

    return wrapped


def _analyst_fn(role: str, deps: Deps) -> Any:
    return lambda s: analyst_node(role, s, deps)


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver[Any]) -> Any:
    g = StateGraph(IncidentState)

    g.add_node(
        "supervisor", _instrument("supervisor", lambda s: supervisor_node(s, deps), deps, True)
    )
    for role in ("log_analyst", "metrics_analyst", "runbook_retriever"):
        g.add_node(role, _instrument(role, _analyst_fn(role, deps), deps, True))
    g.add_node("planner", _instrument("planner", lambda s: planner_node(s, deps), deps, True))
    g.add_node("critic", _instrument("critic", lambda s: critic_node(s, deps), deps, True))
    g.add_node("executor", _instrument("executor", lambda s: executor_node(s, deps), deps, True))
    g.add_node(
        "human_approval",
        _instrument("human_approval", lambda s: human_approval_node(s, deps), deps, False),
    )
    g.add_node("finalize", _instrument("finalize", lambda s: finalize_node(s, deps), deps, True))
    g.add_node("escalate", _instrument("escalate", lambda s: escalate_node(s, deps), deps, True))

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["next"], {r: r for r in ROUTES})
    for n in [*SPECIALISTS, "human_approval"]:
        g.add_edge(n, "supervisor")
    g.add_edge("finalize", END)
    g.add_edge("escalate", END)
    return g.compile(checkpointer=checkpointer)
