"""LogAnalyst, MetricsAnalyst, RunbookRetriever: one ReAct loop each, restricted toolsets."""

from typing import Any

from ..deps import Deps
from ..state import IncidentState
from .prompts import SYSTEMS, TOOL_DOCS
from .react import run_react

TOOLSETS = {
    "log_analyst": ["query_logs"],
    "metrics_analyst": ["get_metrics", "get_recent_deploys"],
    "runbook_retriever": ["search_runbooks"],
}


def analyst_node(role: str, state: IncidentState, deps: Deps) -> dict[str, Any]:
    box = deps.toolbox(state["scenario_id"])
    tools = box.get(TOOLSETS[role])  # least privilege: each role sees only its own tools

    def call(name: str, args: dict[str, Any]) -> Any:
        return deps.resilience.call(name, tools[name], **args)

    base_ctx: dict[str, Any] = {
        "alert": state["alert"],
        "service": box.scenario.service,
        "tools": [TOOL_DOCS[t] for t in TOOLSETS[role]],
    }
    if role == "runbook_retriever":
        base_ctx["prior_findings"] = [
            {"category": f["category"], "summary": f["summary"]} for f in state["findings"]
        ]
    r = run_react(
        role=role,
        llm=deps.llm,
        system=SYSTEMS[role],
        base_ctx=base_ctx,
        tools=tools,
        call=call,
        max_steps=deps.settings.react_max_steps,
        llm_retries=deps.settings.llm_retries,
        trace=deps.trace,
        incident_id=state["incident_id"],
    )
    return {
        "findings": state["findings"] + [r.finding.model_dump()],
        "errors": state["errors"] + r.errors,
        "done_agents": state["done_agents"] + [role],
        "token_budget_used": state["token_budget_used"] + r.tokens,
        "tool_calls": state["tool_calls"] + r.tool_calls,
    }
