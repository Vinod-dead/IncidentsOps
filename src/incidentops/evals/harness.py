"""Evaluation: end-to-end scenarios, critic red-team, crash recovery, LLM-as-judge."""

import copy
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agents.critic import evaluate_plan
from ..agents.prompts import SYSTEMS, build_prompt
from ..app import IncidentOps
from ..config import Settings
from ..llm.base import LLM, llm_json
from ..resilience import SimulatedCrash
from ..state import Plan, PlanAction
from ..tools.catalog import ACTIONS
from ..tools.mock import Scenario, list_scenarios


@dataclass
class EvalReport:
    llm: str
    rows: list[dict[str, Any]]
    summary: dict[str, float]
    critic: dict[str, Any]
    crash: dict[str, Any]


def _fast_settings() -> Settings:
    return Settings(home=Path(tempfile.mkdtemp(prefix="incidentops-eval-")), backoff_base_s=0.001)


def judge_plan(llm: LLM, sc: Scenario, plan: dict[str, Any]) -> int:
    ctx = {"alert": sc.alert, "true_root_cause": sc.root_cause, "plan": plan}
    try:
        score, _ = llm_json(
            llm,
            SYSTEMS["judge"],
            build_prompt("Grade the plan.", ctx),
            lambda d: int(d["score"]),
            2,
        )
        return max(1, min(5, score))
    except Exception:  # noqa: BLE001
        return 1


def _good_plan(sc: Scenario) -> tuple[Plan, list[dict[str, Any]]]:
    name = next(n for n, a in ACTIONS.items() if sc.root_cause in a["fixes"])
    a = ACTIONS[name]
    plan = Plan(
        root_cause=sc.root_cause,
        actions=[
            PlanAction(action=name, risk=a["risk"], rollback=a["rollback"], scope="single_instance")
        ],
    )
    hyps = [
        {
            "category": sc.root_cause,
            "confidence": 0.9,
            "sources": ["log_analyst", "metrics_analyst"],
            "evidence": ["synthetic"],
            "text": "",
        }
    ]
    return plan, hyps


def _mutations(sc: Scenario) -> dict[str, tuple[Plan, list[dict[str, Any]]]]:
    """Injected bad plans. The critic must send every one of them back for revision."""
    plan, hyps = _good_plan(sc)
    out: dict[str, tuple[Plan, list[dict[str, Any]]]] = {}
    p = copy.deepcopy(plan)
    p.actions[0].rollback = ""
    out["missing_rollback"] = (p, hyps)
    p = copy.deepcopy(plan)
    p.actions[0].scope = "all_instances"
    p.actions[0].risk = "medium"
    out["broad_blast_radius"] = (p, hyps)
    wrong = next(c for c in ACTIONS if sc.root_cause not in ACTIONS[c]["fixes"])
    p = copy.deepcopy(plan)
    p.actions = [
        PlanAction(action=wrong, risk=ACTIONS[wrong]["risk"], rollback=ACTIONS[wrong]["rollback"])
    ]
    out["irrelevant_action"] = (p, hyps)
    h = copy.deepcopy(hyps)
    h[0]["confidence"] = 0.3
    h[0]["sources"] = ["runbook_retriever"]
    out["weak_evidence"] = (copy.deepcopy(plan), h)
    p = copy.deepcopy(plan)
    p.actions = p.actions * 4
    out["too_many_actions"] = (p, hyps)
    p = copy.deepcopy(plan)
    p.actions = [
        PlanAction(action="failover_dns_resolver", risk="high", rollback="x"),
        PlanAction(action="failover_dns_resolver", risk="high", rollback="x", params={"b": 1}),
    ]
    out["two_high_risk"] = (p, hyps)
    return out


def critic_redteam(scenarios: list[Scenario]) -> dict[str, Any]:
    caught = total = false_pos = 0
    missed: list[str] = []
    for sc in scenarios:
        plan, hyps = _good_plan(sc)
        if evaluate_plan(plan, hyps, 0).verdict != "approve":
            false_pos += 1
        for name, (p, h) in _mutations(sc).items():
            total += 1
            if evaluate_plan(p, h, 0).verdict == "revise":
                caught += 1
            else:
                missed.append(f"{sc.id}:{name}")
    return {
        "injected": total,
        "caught": caught,
        "catch_rate": caught / total,
        "good_plans": len(scenarios),
        "false_positives": false_pos,
        "missed": missed,
    }


def _sig(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r["status"],
        r["root_cause"],
        tuple(a["action"] for a in (r["plan"] or {}).get("actions", [])),
        tuple(x["action"] for x in r["execution_log"]),
    )


def crash_recovery(llm: LLM, scenarios: list[Scenario]) -> dict[str, Any]:
    """Crash at a different node per scenario, restart with a NEW app on the same SQLite file, resume."""
    ok = 0
    detail: list[str] = []
    for i, sc in enumerate(scenarios):
        base_ops = IncidentOps(_fast_settings(), llm)
        base = base_ops.start(sc.id, dry_run=False, auto_approve=True, incident_id=f"base-{sc.id}")
        base_ops.close()
        s = _fast_settings()
        crashed = IncidentOps(s, llm, crash_after=2 + (i * 3) % 12)
        try:
            crashed.start(sc.id, dry_run=False, auto_approve=True, incident_id=f"crash-{sc.id}")
            detail.append(f"{sc.id}: crash did not trigger")
            continue
        except SimulatedCrash:
            pass
        finally:
            crashed.close()
        fresh = IncidentOps(s, llm)  # "restarted process"
        rec = fresh.resume(f"crash-{sc.id}", auto_approve=True)
        fresh.close()
        if _sig(rec) == _sig(base):
            ok += 1
        else:
            detail.append(f"{sc.id}: {_sig(rec)} != {_sig(base)}")
    return {
        "scenarios": len(scenarios),
        "recovered": ok,
        "rate": ok / len(scenarios),
        "failures": detail,
    }


def run_eval(llm: LLM) -> EvalReport:
    scenarios = list_scenarios(Settings())
    rows: list[dict[str, Any]] = []
    for sc in scenarios:
        ops = IncidentOps(_fast_settings(), llm)
        r = ops.start(sc.id, dry_run=False, auto_approve=True)
        ops.close()
        executed_high = [x for x in r["execution_log"] if x["risk"] == "high" and not x["dry_run"]]
        safe = (not executed_high or r["approval"] in ("approved", "edited")) and all(
            r["approval"] in ("approved", "edited") for x in r["execution_log"]
        )
        rows.append(
            {
                "id": sc.id,
                "cause": sc.root_cause,
                "predicted": r["root_cause"],
                "status": r["status"],
                "correct": r["root_cause"] == sc.root_cause,
                "resolved": r["status"] == "resolved",
                "safe": safe,
                "steps": r["steps"],
                "tool_calls": r["tool_calls"],
                "tokens": r["tokens"],
                "revisions": r["revisions"],
                "errors": len(r["errors"]),
                "judge": judge_plan(llm, sc, r["plan"]) if r["plan"] else 1,
                "faults": ",".join(f"{k}:{v['mode']}" for k, v in sc.tool_faults.items()),
            }
        )
    n = len(rows)
    price = Settings().usd_per_mtok
    mean = lambda k: sum(float(r[k]) for r in rows) / n  # noqa: E731
    summary = {
        "root_cause_accuracy": mean("correct"),
        "resolved_rate": mean("resolved"),
        "plan_safety": mean("safe"),
        "avg_tool_calls": mean("tool_calls"),
        "avg_steps": mean("steps"),
        "avg_tokens": mean("tokens"),
        "cost_per_incident_usd": mean("tokens") / 1e6 * price,
        "avg_revisions": mean("revisions"),
        "judge_score": mean("judge"),
    }
    return EvalReport(
        llm.name, rows, summary, critic_redteam(scenarios), crash_recovery(llm, scenarios)
    )


def to_markdown(rep: EvalReport) -> str:
    s, c, k = rep.summary, rep.critic, rep.crash
    note = (
        "> Produced with the offline **FakeLLM** (rule-based). These numbers validate the pipeline "
        "(routing, state, resilience, checkpoints), **not** LLM quality. Re-run with `--llm anthropic|openai|gemini`."
        if rep.llm == "fake"
        else f"> Produced with LLM provider `{rep.llm}`."
    )
    lines = [
        f"# IncidentOps eval results (llm = `{rep.llm}`)",
        "",
        note,
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Root-cause accuracy | {s['root_cause_accuracy']:.0%} |",
        f"| Resolved (verified in sandbox) | {s['resolved_rate']:.0%} |",
        f"| Plan safety (no execution without approval) | {s['plan_safety']:.0%} |",
        f"| Avg tool calls / incident | {s['avg_tool_calls']:.1f} |",
        f"| Avg supervisor steps / incident | {s['avg_steps']:.1f} |",
        f"| Avg tokens / incident | {s['avg_tokens']:.0f} |",
        f"| Est. cost / incident (illustrative $/Mtok) | ${s['cost_per_incident_usd']:.4f} |",
        f"| Avg plan revisions / incident | {s['avg_revisions']:.2f} |",
        f"| LLM-as-judge plan score (1-5) | {s['judge_score']:.2f} |",
        f"| Critic catch rate on injected bad plans | {c['caught']}/{c['injected']} ({c['catch_rate']:.0%}) |",
        f"| Critic false positives on good plans | {c['false_positives']}/{c['good_plans']} |",
        f"| Recovery after crash (resume from checkpoint) | {k['recovered']}/{k['scenarios']} ({k['rate']:.0%}) |",
        "",
        "## Per scenario",
        "",
        "| # | True cause | Predicted | Status | Steps | Tools | Revs | Errors | Faults |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rep.rows:
        lines.append(
            f"| {r['id']} | {r['cause']} | {r['predicted']} | {r['status']} | {r['steps']} | "
            f"{r['tool_calls']} | {r['revisions']} | {r['errors']} | {r['faults'] or '-'} |"
        )
    if c["missed"] or k["failures"]:
        lines += ["", f"Critic misses: {c['missed']}", f"Crash failures: {k['failures']}"]
    return "\n".join(lines) + "\n"
