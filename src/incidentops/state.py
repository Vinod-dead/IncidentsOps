"""Typed incident state.

The LangGraph state is a TypedDict of plain JSON values (so any checkpointer can
serialise it). Pydantic models validate every structured object before it is
stored via ``model_dump()``.
"""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

CATEGORIES = (
    "bad_deploy",
    "db_pool_exhaustion",
    "memory_leak",
    "expired_cert",
    "noisy_neighbor",
    "disk_full",
    "dns_failure",
    "cache_stampede",
    "upstream_rate_limit",
    "config_error",
    "unknown",
)
Risk = Literal["low", "medium", "high"]
Scope = Literal["single_instance", "canary", "all_instances"]


def _check_category(v: str) -> str:
    if v not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}, got {v!r}")
    return v


class FindingOut(BaseModel):
    """What an analyst LLM emits."""

    category: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    _cat = field_validator("category")(_check_category)


class Finding(FindingOut):
    source: str


class Hypothesis(BaseModel):
    text: str
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    _cat = field_validator("category")(_check_category)


class ToolAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ReActStep(BaseModel):
    """One Thought -> (Action | Finding) step."""

    thought: str
    action: ToolAction | None = None
    finding: FindingOut | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ReActStep":
        if (self.action is None) == (self.finding is None):
            raise ValueError("provide exactly one of 'action' or 'finding'")
        return self


class PlanAction(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: Risk = "low"
    rollback: str = ""
    scope: Scope = "single_instance"
    rationale: str = ""


class Plan(BaseModel):
    root_cause: str
    summary: str = ""
    actions: list[PlanAction] = Field(min_length=1)
    version: int = 1

    _cat = field_validator("root_cause")(_check_category)


class CriticReview(BaseModel):
    round: int
    verdict: Literal["approve", "revise"]
    checks: dict[str, bool]
    reasons: list[str] = Field(default_factory=list)


class ExecutionRecord(BaseModel):
    idempotency_key: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: str = "low"
    dry_run: bool = True
    status: str  # ok | dry_run | skipped_duplicate | failed
    detail: str = ""


class IncidentError(BaseModel):
    node: str
    kind: str
    message: str


class IncidentState(TypedDict, total=False):
    incident_id: str
    scenario_id: int
    alert: str
    dry_run: bool
    findings: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    plan: dict[str, Any] | None
    critic_feedback: list[dict[str, Any]]
    critic_verdict: str  # "" (not reviewed) | approve | revise
    revision_count: int
    approval_status: str  # not_requested | approved | rejected | edited
    approval_by: str
    execution_log: list[dict[str, Any]]
    executed: bool
    done_agents: list[str]
    step_count: int
    token_budget_used: int
    tool_calls: int
    active_seconds: float
    errors: list[dict[str, Any]]
    status: str
    escalation_reason: str
    next: str


def init_state(
    incident_id: str, scenario_id: int, alert: str, dry_run: bool = True
) -> IncidentState:
    return IncidentState(
        incident_id=incident_id,
        scenario_id=scenario_id,
        alert=alert,
        dry_run=dry_run,
        findings=[],
        hypotheses=[],
        plan=None,
        critic_feedback=[],
        critic_verdict="",
        revision_count=0,
        approval_status="not_requested",
        approval_by="",
        execution_log=[],
        executed=False,
        done_agents=[],
        step_count=0,
        token_budget_used=0,
        tool_calls=0,
        active_seconds=0.0,
        errors=[],
        status="triaging",
        escalation_reason="",
        next="",
    )


def build_hypotheses(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge findings per category. Independent sources combine with noisy-OR."""
    groups: dict[str, dict[str, list[Any]]] = {}
    for f in findings:
        if f["category"] == "unknown":
            continue
        g = groups.setdefault(f["category"], {"conf": [], "evidence": [], "sources": []})
        g["conf"].append(f["confidence"])
        g["evidence"].extend(f.get("evidence", []))
        if f["source"] not in g["sources"]:
            g["sources"].append(f["source"])
    out = []
    for cat, g in groups.items():
        miss = 1.0
        for c in g["conf"]:
            miss *= 1 - c
        ev = list(dict.fromkeys(g["evidence"]))[:6]
        out.append(
            Hypothesis(
                text=f"Root cause is {cat} (reported by {', '.join(g['sources'])})",
                category=cat,
                confidence=round(1 - miss, 3),
                evidence=ev,
                sources=g["sources"],
            ).model_dump()
        )
    return sorted(out, key=lambda h: -h["confidence"])
