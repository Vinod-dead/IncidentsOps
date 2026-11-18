"""Critic: deterministic rubric (cannot be talked out of it) + LLM second opinion."""

from typing import Any

from pydantic import BaseModel, Field

from ..deps import Deps
from ..llm.base import LLMOutputError, llm_json
from ..state import CriticReview, IncidentState, Plan
from ..tools.catalog import ACTIONS
from .prompts import SYSTEMS, build_prompt

MIN_CONFIDENCE, MIN_SOURCES, MAX_ACTIONS = 0.7, 2, 3


def evaluate_plan(plan: Plan, hypotheses: list[dict[str, Any]], round_: int) -> CriticReview:
    reasons: list[str] = []
    hyp = next((h for h in hypotheses if h["category"] == plan.root_cause), None)
    evidence_ok = (
        hyp is not None
        and hyp["confidence"] >= MIN_CONFIDENCE
        and len(hyp["sources"]) >= MIN_SOURCES
    )
    if not evidence_ok:
        reasons.append(
            f"evidence: root cause '{plan.root_cause}' needs confidence >= {MIN_CONFIDENCE} "
            f"from >= {MIN_SOURCES} independent sources"
        )
    relevant = any(
        plan.root_cause in ACTIONS.get(a.action, {}).get("fixes", []) for a in plan.actions
    )
    if not relevant:
        reasons.append(f"relevance: no action in the plan addresses '{plan.root_cause}'")
    reversible = all(a.rollback.strip() for a in plan.actions)
    if not reversible:
        reasons.append("reversibility: every action needs a rollback step")
    safe = (
        all(a.action in ACTIONS for a in plan.actions)
        and sum(a.risk == "high" for a in plan.actions) <= 1
    )
    if not safe:
        reasons.append("safety: unknown action or more than one high-risk action")
    blast = len(plan.actions) <= MAX_ACTIONS and all(
        a.scope != "all_instances" for a in plan.actions if a.risk != "low"
    )
    if not blast:
        reasons.append(
            "blast_radius: medium/high-risk actions must use scope 'canary' or "
            f"'single_instance' (max {MAX_ACTIONS} actions)"
        )
    checks = {
        "evidence_backed": evidence_ok,
        "relevant": relevant,
        "reversible": reversible,
        "safe": safe,
        "blast_radius": blast,
    }
    return CriticReview(
        round=round_,
        verdict="approve" if all(checks.values()) else "revise",
        checks=checks,
        reasons=reasons,
    )


class _LLMReview(BaseModel):
    verdict: str
    reasons: list[str] = Field(default_factory=list)


def critic_node(state: IncidentState, deps: Deps) -> dict[str, Any]:
    plan = Plan.model_validate(state["plan"])
    review = evaluate_plan(plan, state["hypotheses"], state["revision_count"])
    tokens, errors = 0, state["errors"]
    ctx = {
        "plan": plan.model_dump(),
        "hypotheses": state["hypotheses"][:3],
        "rubric_result": review.checks,
    }
    try:
        op, tokens = llm_json(
            deps.llm,
            SYSTEMS["critic"],
            build_prompt("Review the plan.", ctx),
            _LLMReview.model_validate,
            deps.settings.llm_retries,
        )
        if (
            op.verdict == "revise"
        ):  # the LLM may add concerns; it can never override a rubric failure
            review.verdict = "revise"
            review.reasons += [f"llm: {r}" for r in op.reasons]
    except LLMOutputError as e:  # degrade to rubric-only
        tokens = e.tokens
        errors = errors + [{"node": "critic", "kind": "llm_output", "message": str(e)}]
    deps.trace.emit(state["incident_id"], "critic", "review", **review.model_dump())
    return {
        "critic_feedback": state["critic_feedback"] + [review.model_dump()],
        "critic_verdict": review.verdict,
        "errors": errors,
        "token_budget_used": state["token_budget_used"] + tokens,
    }
