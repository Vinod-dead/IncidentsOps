"""Executor: runs approved actions in the sandbox. Dry-run by default; idempotent."""

from typing import Any

from ..deps import Deps
from ..state import IncidentState, Plan
from ..tools.sandbox import Sandbox, idempotency_key


def executor_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    if state["approval_status"] not in ("approved", "edited"):  # defence in depth
        raise PermissionError("executor invoked without human approval")
    plan = Plan.model_validate(state["plan"])
    scenario = deps.toolbox(state["scenario_id"]).scenario
    log = list(state["execution_log"])
    for a in plan.actions:
        key = idempotency_key(state["incident_id"], a.action, a.params, a.scope)
        if any(r["idempotency_key"] == key and r["status"] in ("ok", "dry_run") for r in log):
            continue
        rec = deps.sandbox.execute(key, a.action, a.params, a.risk, state["dry_run"])
        log.append(rec.model_dump())
        deps.trace.emit(state["incident_id"], "executor", "execution", **rec.model_dump())
    if state["dry_run"]:
        status = "dry_run_complete"
    else:
        status = "resolved" if Sandbox.verify(scenario, log) else "unresolved"
    return {"execution_log": log, "executed": True, "status": status}
