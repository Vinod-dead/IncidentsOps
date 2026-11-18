"""High-level API: start / resume / decide / status. Used by the CLI, evals and tests."""

import random
import sqlite3
import uuid
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import Settings
from .deps import Deps
from .graph import build_graph
from .llm.base import LLM, get_llm
from .resilience import Resilient
from .state import init_state
from .tools.mock import load_scenario
from .tools.runbooks import RunbookRetriever
from .tools.sandbox import Sandbox
from .trace import TraceWriter


class IncidentOps:
    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLM | None = None,
        crash_after: int | None = None,
        hard_kill: bool = False,
    ) -> None:
        self.settings = settings or Settings()
        self.settings.home.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.settings.checkpoint_db, check_same_thread=False)
        self.deps = Deps(
            settings=self.settings,
            llm=llm or get_llm(),
            trace=TraceWriter(self.settings.traces_dir),
            resilience=Resilient(self.settings, rng=random.Random(0)),
            retriever=RunbookRetriever(self.settings.runbooks_dir),
            sandbox=Sandbox(self.settings.sandbox_db),
            crash_after=crash_after,
            hard_kill=hard_kill,
        )
        self.graph = build_graph(self.deps, SqliteSaver(self.conn))

    def close(self) -> None:
        self.conn.close()
        self.deps.sandbox.conn.close()

    @staticmethod
    def _cfg(incident_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": incident_id}, "recursion_limit": 100}

    # ---- lifecycle -------------------------------------------------------------------------
    def start(
        self,
        scenario_id: int,
        *,
        dry_run: bool = True,
        auto_approve: bool = False,
        incident_id: str | None = None,
    ) -> dict[str, Any]:
        sc = load_scenario(self.settings, scenario_id)
        iid = incident_id or f"inc-{scenario_id:02d}-{uuid.uuid4().hex[:6]}"
        self.deps.trace.emit(
            iid, "app", "start", scenario=scenario_id, alert=sc.alert, dry_run=dry_run
        )
        self.graph.invoke(
            init_state(iid, scenario_id, sc.alert, dry_run), self._cfg(iid), durability="sync"
        )
        return self._drive(iid, auto_approve)

    def resume(self, incident_id: str, *, auto_approve: bool = False) -> dict[str, Any]:
        """Continue from the last checkpoint (after a crash or restart)."""
        if not self.graph.get_state(self._cfg(incident_id)).values:
            raise KeyError(f"no checkpoint for incident {incident_id}")
        snap = self.graph.get_state(self._cfg(incident_id))
        if snap.next:
            self.graph.invoke(None, self._cfg(incident_id), durability="sync")
        return self._drive(incident_id, auto_approve)

    def decide(
        self, incident_id: str, decision: str, plan: dict[str, Any] | None = None, by: str = "human"
    ) -> dict[str, Any]:
        if self.pending_approval(incident_id) is None:
            raise RuntimeError(f"incident {incident_id} is not awaiting approval")
        payload: dict[str, Any] = {"decision": decision, "by": by}
        if plan is not None:
            payload["plan"] = plan
        self.graph.invoke(Command(resume=payload), self._cfg(incident_id), durability="sync")
        return self._drive(incident_id, False)

    def _drive(self, iid: str, auto_approve: bool) -> dict[str, Any]:
        while auto_approve and self.pending_approval(iid) is not None:
            self.graph.invoke(
                Command(resume={"decision": "approve", "by": "auto"}),
                self._cfg(iid),
                durability="sync",
            )
        return self.status(iid)

    # ---- inspection ------------------------------------------------------------------------
    def pending_approval(self, incident_id: str) -> dict[str, Any] | None:
        snap = self.graph.get_state(self._cfg(incident_id))
        if tuple(snap.next) == ("human_approval",):
            return {
                "plan": snap.values["plan"],
                "hypotheses": snap.values["hypotheses"][:2],
                "critic": snap.values["critic_feedback"][-1],
            }
        return None

    def status(self, incident_id: str) -> dict[str, Any]:
        snap = self.graph.get_state(self._cfg(incident_id))
        v = snap.values
        if not v:
            raise KeyError(f"unknown incident {incident_id}")
        awaiting = tuple(snap.next) == ("human_approval",)
        plan = v.get("plan")
        return {
            "incident_id": incident_id,
            "status": "awaiting_approval" if awaiting else v["status"],
            "alert": v["alert"],
            "root_cause": plan["root_cause"] if plan else None,
            "plan": plan,
            "approval": v["approval_status"],
            "revisions": v["revision_count"],
            "steps": v["step_count"],
            "tool_calls": v["tool_calls"],
            "tokens": v["token_budget_used"],
            "errors": v["errors"],
            "execution_log": v["execution_log"],
            "escalation_reason": v["escalation_reason"],
            "dry_run": v["dry_run"],
            "findings": v["findings"],
            "hypotheses": v["hypotheses"],
        }
