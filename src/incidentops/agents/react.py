"""Generic ReAct loop: Thought -> Action(tool) -> Observation -> ... -> Finding."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..llm.base import LLM, LLMOutputError, llm_json
from ..resilience import ToolError
from ..state import Finding, ReActStep
from ..trace import TraceWriter, clip
from .prompts import build_prompt


@dataclass
class ReactResult:
    finding: Finding
    tokens: int = 0
    tool_calls: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def _unknown(role: str, why: str) -> Finding:
    return Finding(source=role, category="unknown", summary=why, evidence=[], confidence=0.0)


def run_react(
    *,
    role: str,
    llm: LLM,
    system: str,
    base_ctx: dict[str, Any],
    tools: dict[str, Callable[..., Any]],
    call: Callable[[str, dict[str, Any]], Any],
    max_steps: int,
    llm_retries: int,
    trace: TraceWriter,
    incident_id: str,
) -> ReactResult:
    scratch: list[dict[str, Any]] = []
    res = ReactResult(finding=_unknown(role, "max steps reached without a conclusion"))
    for i in range(max_steps):
        prompt = build_prompt("Decide the next step.", {**base_ctx, "scratchpad": scratch})
        try:
            step, tokens = llm_json(llm, system, prompt, ReActStep.model_validate, llm_retries)
        except LLMOutputError as e:
            res.tokens += e.tokens
            res.errors.append({"node": role, "kind": "llm_output", "message": str(e)})
            res.finding = _unknown(
                role, "model produced invalid output; escalating to other sources"
            )
            return res
        res.tokens += tokens
        trace.emit(incident_id, role, "thought", step_no=i, text=step.thought)
        if step.finding is not None:
            res.finding = Finding(source=role, **step.finding.model_dump())
            trace.emit(incident_id, role, "finding", **res.finding.model_dump())
            return res
        assert step.action is not None
        name, args = step.action.tool, step.action.args
        trace.emit(incident_id, role, "action", tool=name, args=args)
        if name not in tools:
            obs: Any = {"error": f"unknown tool '{name}'; available: {sorted(tools)}"}
        else:
            res.tool_calls += 1
            try:
                obs = call(name, args)
            except ToolError as e:
                obs = {"error": str(e)}
                res.errors.append({"node": role, "kind": "tool_error", "message": str(e)})
        trace.emit(incident_id, role, "observation", tool=name, observation=clip(obs))
        scratch.append(
            {"thought": step.thought, "action": step.action.model_dump(), "observation": obs}
        )
    res.errors.append({"node": role, "kind": "max_steps", "message": f"hit max_steps={max_steps}"})
    return res
