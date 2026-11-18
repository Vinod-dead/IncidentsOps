"""RemediationPlanner: findings/hypotheses -> ordered actions with risk + rollback."""

from typing import Any

from pydantic import BaseModel, Field

from ..deps import Deps
from ..llm.base import LLMOutputError, llm_json
from ..resilience import ToolError
from ..state import IncidentState, Plan, PlanAction, Scope
from ..tools.catalog import ACTIONS, catalog_summary
from .prompts import SYSTEMS, build_prompt


class PlanActionOut(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    scope: Scope = "single_instance"
    rationale: str = ""


class PlanOut(BaseModel):
    root_cause: str
    summary: str = ""
    actions: list[PlanActionOut] = Field(min_length=1)


def _validate(d: dict[str, Any]) -> PlanOut:
    out = PlanOut.model_validate(d)
    for a in out.actions:
        if a.action not in ACTIONS:
            raise ValueError(f"unknown action '{a.action}'; allowed: {sorted(ACTIONS)}")
    return out


def planner_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    box = deps.toolbox(state["scenario_id"])
    revising = state["critic_verdict"] == "revise"
    prev = state["plan"]
    ctx = {
        "alert": state["alert"],
        "service": box.scenario.service,
        "hypotheses": state["hypotheses"][:3],
        "catalog": catalog_summary(),
        "critic_feedback": state["critic_feedback"][-1]["reasons"] if revising else [],
        "revision_count": state["revision_count"] + (1 if revising else 0),
        "previous_plan": prev if revising else None,
    }
    try:
        out, tokens = llm_json(
            deps.llm,
            SYSTEMS["planner"],
            build_prompt("Write the plan.", ctx),
            _validate,
            deps.settings.llm_retries,
        )
    except LLMOutputError as e:
        err = {"node": "planner", "kind": "llm_output", "message": str(e)}
        return {
            "errors": state["errors"] + [err],
            "token_budget_used": state["token_budget_used"] + e.tokens,
        }
    actions: list[PlanAction] = []
    for a in out.actions:
        try:
            meta = box.propose_action(a.action, a.params)  # catalog-enforced risk + rollback
        except ToolError as e:
            err = {"node": "planner", "kind": "tool_error", "message": str(e)}
            return {
                "errors": state["errors"] + [err],
                "token_budget_used": state["token_budget_used"] + tokens,
            }
        actions.append(
            PlanAction(
                action=a.action,
                params=a.params,
                risk=meta["risk"],
                rollback=meta["rollback"],
                scope=a.scope,
                rationale=a.rationale,
            )
        )
    version = (prev["version"] + 1) if prev else 1
    plan = Plan(root_cause=out.root_cause, summary=out.summary, actions=actions, version=version)
    deps.trace.emit(state["incident_id"], "planner", "plan", **plan.model_dump())
    return {
        "plan": plan.model_dump(),
        "critic_verdict": "",
        "revision_count": state["revision_count"] + (1 if revising else 0),
        "token_budget_used": state["token_budget_used"] + tokens,
    }
