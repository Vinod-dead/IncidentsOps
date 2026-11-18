"""Supervisor: hierarchical delegation. Deterministic guards decide WHAT is legal;
the LLM only chooses among legal options."""

from typing import Any

from ..deps import Deps
from ..llm.base import LLMOutputError, llm_json
from ..state import IncidentState, build_hypotheses
from .prompts import SYSTEMS, build_prompt

ANALYSTS = ["log_analyst", "metrics_analyst"]


def budget_exhausted(state: IncidentState, deps: Deps) -> str | None:
    s = deps.settings
    if state["step_count"] >= s.max_graph_steps:
        return f"step budget exhausted ({s.max_graph_steps})"
    if state["token_budget_used"] >= s.max_tokens:
        return f"token budget exhausted ({s.max_tokens})"
    if state["active_seconds"] >= s.max_active_seconds:
        return f"time budget exhausted ({s.max_active_seconds}s)"
    return None


def _err_count(state: IncidentState, node: str) -> int:
    return sum(1 for e in state["errors"] if e["node"] == node and e["kind"] != "tool_error")


def allowed_next(state: IncidentState, deps: Deps) -> tuple[list[str], str]:
    """Return (legal next nodes, reason). Pure function of state: easy to unit test."""
    done = set(state["done_agents"])
    pending = [a for a in ANALYSTS if a not in done]
    if pending:
        return pending, "analysts pending"
    if "runbook_retriever" not in done:
        return ["runbook_retriever"], "all analysts done; match runbooks"
    if state["status"] in ("resolved", "dry_run_complete"):
        return ["finalize"], "execution complete"
    if state["status"] == "unresolved":
        return ["escalate"], "verification failed after execution"
    if not state["plan"]:
        if not state["hypotheses"]:
            return ["escalate"], "no actionable hypothesis from any source"
        if _err_count(state, "planner") >= 2:
            return ["escalate"], "planner failed repeatedly"
        return ["planner"], "hypotheses ready"
    if state["critic_verdict"] == "":
        if _err_count(state, "critic") >= 3:
            return ["escalate"], "critic failed repeatedly"
        return ["critic"], "plan needs review"
    if state["critic_verdict"] == "revise":
        if state["revision_count"] >= deps.settings.max_revisions:
            return ["escalate"], f"plan still rejected after {state['revision_count']} revisions"
        return ["planner"], "critic requested revision"
    # critic approved
    ap = state["approval_status"]
    if ap == "not_requested":
        return ["human_approval"], "awaiting human decision"
    if ap in ("approved", "edited"):
        if _err_count(state, "executor") >= 1:
            return ["escalate"], "executor failed"
        return ["executor"], "approved by human"
    return ["escalate"], "plan rejected by human"


def supervisor_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    upd: dict[str, Any] = {
        "step_count": state["step_count"] + 1,
        "hypotheses": build_hypotheses(state["findings"]),
    }
    why = budget_exhausted(state, deps)
    if why:
        nxt, reason = "escalate", why
    else:
        view = {**state, "hypotheses": upd["hypotheses"]}
        allowed, reason = allowed_next(view, deps)  # type: ignore[arg-type]
        nxt = allowed[0]
        if len(allowed) > 1:  # a genuine choice: ask the LLM, validate against the guard
            ctx = {
                "allowed": allowed,
                "done": state["done_agents"],
                "reason": reason,
                "n_findings": len(state["findings"]),
            }
            try:
                choice, tokens = llm_json(
                    deps.llm,
                    SYSTEMS["supervisor"],
                    build_prompt("Route.", ctx),
                    lambda d: d["next"],
                    deps.settings.llm_retries,
                )
                upd["token_budget_used"] = state["token_budget_used"] + tokens
                if choice in allowed:
                    nxt = choice
                else:
                    upd["errors"] = state["errors"] + [
                        {
                            "node": "supervisor",
                            "kind": "invalid_route",
                            "message": f"LLM chose {choice!r}, not in {allowed}",
                        }
                    ]
            except LLMOutputError as e:
                upd["token_budget_used"] = state["token_budget_used"] + e.tokens
    upd["next"] = nxt
    if nxt == "escalate":
        upd["escalation_reason"] = reason
    deps.trace.emit(
        state["incident_id"], "supervisor", "route", next=nxt, reason=reason, step=upd["step_count"]
    )
    return upd
