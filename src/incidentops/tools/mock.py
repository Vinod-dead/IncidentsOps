"""Deterministic mock tools backed by scenario fixtures, with fault injection."""

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..config import Settings
from ..resilience import ToolError
from .catalog import ACTIONS
from .runbooks import RunbookRetriever

SEVERITY = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "FATAL": 4}


class Scenario(BaseModel):
    id: int
    name: str
    service: str
    alert: str
    root_cause: str
    logs: list[dict[str, Any]]
    decoy_logs: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, list[float]]
    deploys: list[dict[str, Any]]
    tool_faults: dict[str, dict[str, Any]] = Field(default_factory=dict)


def load_scenario(settings: Settings, scenario_id: int) -> Scenario:
    p = settings.scenarios_dir / f"scenario_{scenario_id:02d}.json"
    if not p.exists():
        raise FileNotFoundError(f"no scenario {scenario_id} in {settings.scenarios_dir}")
    return Scenario.model_validate(json.loads(p.read_text()))


def list_scenarios(settings: Settings) -> list[Scenario]:
    return [
        load_scenario(settings, int(p.stem.split("_")[1]))
        for p in sorted(Path(settings.scenarios_dir).glob("scenario_*.json"))
    ]


class ToolBox:
    """The tool surface agents see. Faults: down | flaky(fail_first=N) | noise (query_logs)."""

    def __init__(self, scenario: Scenario, retriever: RunbookRetriever) -> None:
        self.scenario, self.retriever = scenario, retriever
        self.attempts: Counter[str] = Counter()

    def _fault(self, tool: str) -> dict[str, Any]:
        self.attempts[tool] += 1
        spec = self.scenario.tool_faults.get(tool, {})
        mode = spec.get("mode")
        if mode == "down":
            raise ToolError(f"{tool}: 503 service unavailable")
        if mode == "flaky" and self.attempts[tool] <= spec.get("fail_first", 1):
            raise ToolError(f"{tool}: transient 502 bad gateway")
        return spec

    def query_logs(
        self, service: str, level: str = "WARN", limit: int = 20
    ) -> list[dict[str, Any]]:
        spec = self._fault("query_logs")
        src = self.scenario.decoy_logs if spec.get("mode") == "noise" else self.scenario.logs
        floor = SEVERITY.get(level.upper(), 2)
        rows = [r for r in src if SEVERITY.get(r["level"], 0) >= floor]
        rows.sort(key=lambda r: r["t"])  # most recent (smallest minutes-ago) first
        return [{"minutes_ago": r["t"], "level": r["level"], "msg": r["msg"]} for r in rows[:limit]]

    def get_metrics(self, service: str) -> dict[str, list[float]]:
        self._fault("get_metrics")
        return self.scenario.metrics

    def get_recent_deploys(self, service: str, minutes: int = 120) -> list[dict[str, Any]]:
        self._fault("get_recent_deploys")
        return self.scenario.deploys

    def search_runbooks(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        self._fault("search_runbooks")
        return self.retriever.search(query, k)

    def propose_action(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate an action against the catalog and return its risk + rollback."""
        if action not in ACTIONS:
            raise ToolError(
                f"unknown action '{action}'; allowed: {sorted(ACTIONS)}", transient=False
            )
        a = ACTIONS[action]
        return {"action": action, "risk": a["risk"], "rollback": a["rollback"], "fixes": a["fixes"]}

    def get(self, names: list[str]) -> dict[str, Callable[..., Any]]:
        return {n: getattr(self, n) for n in names}
