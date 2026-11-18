"""Shared dependencies injected into every graph node (keeps nodes pure and testable)."""

import os
from dataclasses import dataclass, field

from .config import Settings
from .llm.base import LLM
from .resilience import Resilient, SimulatedCrash
from .tools.mock import ToolBox, load_scenario
from .tools.runbooks import RunbookRetriever
from .tools.sandbox import Sandbox
from .trace import TraceWriter


@dataclass
class Deps:
    settings: Settings
    llm: LLM
    trace: TraceWriter
    resilience: Resilient
    retriever: RunbookRetriever
    sandbox: Sandbox
    crash_after: int | None = None  # crash-injection (tests / demo)
    hard_kill: bool = False  # os._exit instead of raising: simulates `kill -9`
    _boxes: dict[int, ToolBox] = field(default_factory=dict)
    _node_calls: int = 0

    def toolbox(self, scenario_id: int) -> ToolBox:
        if scenario_id not in self._boxes:
            self._boxes[scenario_id] = ToolBox(
                load_scenario(self.settings, scenario_id), self.retriever
            )
        return self._boxes[scenario_id]

    def maybe_crash(self, node: str) -> None:
        self._node_calls += 1
        if self.crash_after is not None and self._node_calls > self.crash_after:
            self.crash_after = None
            if self.hard_kill:
                os._exit(137)
            raise SimulatedCrash(f"simulated crash before node '{node}'")
