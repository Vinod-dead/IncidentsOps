import pytest

from incidentops.agents.critic import evaluate_plan
from incidentops.agents.supervisor import allowed_next
from incidentops.llm.fake import FakeLLM
from incidentops.resilience import SimulatedCrash
from incidentops.state import Plan, PlanAction, init_state
from incidentops.tools.mock import load_scenario


@pytest.mark.parametrize("sid", range(1, 11))
def test_every_scenario_resolves_end_to_end(make_ops, settings, sid):
    r = make_ops().start(sid, dry_run=False, auto_approve=True)
    assert r["status"] == "resolved"
    assert r["root_cause"] == load_scenario(settings, sid).root_cause
    assert r["approval"] == "approved"


def test_dry_run_is_default_and_does_not_touch_sandbox(make_ops):
    ops = make_ops()
    r = ops.start(3, auto_approve=True)
    assert r["status"] == "dry_run_complete"
    assert all(x["status"] == "dry_run" for x in r["execution_log"])


def test_human_gate_pauses_and_survives_restart(make_ops, settings):
    ops = make_ops()
    r = ops.start(7, dry_run=False, incident_id="inc-restart")
    assert r["status"] == "awaiting_approval" and r["execution_log"] == []
    ops.close()
    ops2 = make_ops()  # brand-new process-equivalent, same SQLite file
    assert (
        ops2.pending_approval("inc-restart")["plan"]["actions"][0]["action"]
        == "failover_dns_resolver"
    )
    final = ops2.decide("inc-restart", "approve")
    assert final["status"] == "resolved" and final["approval"] == "approved"


def test_reject_escalates_and_executes_nothing(make_ops):
    ops = make_ops()
    ops.start(1, dry_run=False, incident_id="inc-rej")
    r = ops.decide("inc-rej", "reject")
    assert r["status"] == "rejected" and r["execution_log"] == []


def test_human_edit_replaces_plan(make_ops):
    ops = make_ops()
    r = ops.start(3, dry_run=False, incident_id="inc-edit")
    plan = r["plan"]
    plan["actions"][0]["scope"] = "canary"
    out = ops.decide("inc-edit", "edit", plan=plan)
    assert out["approval"] == "edited" and out["status"] == "resolved"


def test_decide_without_pending_raises(make_ops):
    ops = make_ops()
    ops.start(3, auto_approve=True, incident_id="inc-done")
    with pytest.raises(RuntimeError):
        ops.decide("inc-done", "approve")


def test_critic_loop_revises_broad_scope(make_ops):
    r = make_ops().start(2, auto_approve=True)
    assert r["revisions"] == 1 and r["plan"]["actions"][0]["scope"] == "canary"


@pytest.mark.parametrize("crash_after", [1, 3, 5, 8, 11])
def test_crash_then_resume_matches_clean_run(make_ops, settings, crash_after):
    ops = make_ops(crash_after=crash_after)
    with pytest.raises(SimulatedCrash):
        ops.start(4, dry_run=False, auto_approve=True, incident_id="inc-crash")
    ops.close()
    r = make_ops().resume("inc-crash", auto_approve=True)
    clean = make_ops().start(4, dry_run=False, auto_approve=True, incident_id="inc-clean")
    assert (
        (r["status"], r["root_cause"])
        == (clean["status"], clean["root_cause"])
        == ("resolved", "expired_cert")
    )
    assert len([x for x in r["execution_log"] if x["status"] == "ok"]) == 1  # applied exactly once


def test_log_tool_down_routes_around(make_ops):
    r = make_ops().start(9, dry_run=False, auto_approve=True)
    assert r["status"] == "resolved"
    assert any("503" in e["message"] for e in r["errors"])
    assert next(f for f in r["findings"] if f["source"] == "log_analyst")["category"] == "unknown"


def test_flaky_tool_recovered_by_retry(make_ops):
    ops = make_ops()
    r = ops.start(8, auto_approve=True)
    assert r["errors"] == [] and ops.deps.resilience.stats["retries"] == 2


def test_noisy_logs_still_diagnosed_by_other_sources(make_ops):
    r = make_ops().start(10, auto_approve=True)
    assert r["root_cause"] == "config_error"


def test_llm_garbage_falls_back_and_escalates_when_planner_dead(make_ops):
    r = make_ops(llm=FakeLLM(garble={"planner": 99})).start(3, auto_approve=True)
    assert r["status"] == "escalated" and "planner" in r["escalation_reason"]


def test_all_analysts_garbage_escalates_with_no_hypothesis(make_ops):
    llm = FakeLLM(garble={"log_analyst": 99, "metrics_analyst": 99, "runbook_retriever": 99})
    r = make_ops(llm=llm).start(3, auto_approve=True)
    assert r["status"] == "escalated" and "no actionable hypothesis" in r["escalation_reason"]


def test_step_budget_escalates(settings, make_ops):
    settings.max_graph_steps = 2
    r = make_ops().start(3, auto_approve=True)
    assert r["status"] == "escalated" and "step budget" in r["escalation_reason"]


def test_token_budget_escalates(settings, make_ops):
    settings.max_tokens = 100
    r = make_ops().start(3, auto_approve=True)
    assert r["status"] == "escalated" and "token budget" in r["escalation_reason"]


def test_specialist_exception_does_not_crash_graph(make_ops):
    ops = make_ops()

    def boom(*a, **k):
        raise RuntimeError("tool exploded")

    ops.deps.toolbox(3).get_metrics = boom  # not retried: unknown failure -> non-transient
    r = ops.start(3, auto_approve=True)
    assert r["status"] == "dry_run_complete"  # logs + runbooks still produced a cause


def test_executor_refuses_without_approval(make_ops, settings):
    from incidentops.agents.executor import executor_node

    ops = make_ops()
    st = init_state("x", 3, "a")
    st["plan"] = {"root_cause": "memory_leak", "actions": [{"action": "restart_service"}]}
    with pytest.raises(PermissionError):
        executor_node(st, ops.deps)


def test_supervisor_guard_never_skips_critic_or_human(make_ops):
    ops = make_ops()
    st = init_state("x", 3, "a")
    st["done_agents"] = ["log_analyst", "metrics_analyst", "runbook_retriever"]
    st["hypotheses"] = [
        {"category": "memory_leak", "confidence": 0.9, "sources": ["a", "b"], "evidence": []}
    ]
    assert allowed_next(st, ops.deps)[0] == ["planner"]
    st["plan"] = {"x": 1}
    assert allowed_next(st, ops.deps)[0] == ["critic"]
    st["critic_verdict"] = "approve"
    assert allowed_next(st, ops.deps)[0] == [
        "human_approval"
    ]  # executor unreachable before approval
    st["critic_verdict"] = "revise"
    st["revision_count"] = 2
    assert allowed_next(st, ops.deps)[0] == ["escalate"]


def test_critic_rubric_each_check_can_fail():
    hyp = [{"category": "memory_leak", "confidence": 0.9, "sources": ["a", "b"], "evidence": []}]
    good = Plan(
        root_cause="memory_leak",
        actions=[
            PlanAction(action="restart_service", risk="low", rollback="r", scope="single_instance")
        ],
    )
    assert evaluate_plan(good, hyp, 0).verdict == "approve"
    bad = good.model_copy(deep=True)
    bad.actions[0].rollback = ""
    assert not evaluate_plan(bad, hyp, 0).checks["reversible"]
    assert not evaluate_plan(good, [], 0).checks["evidence_backed"]


def test_trace_records_react_steps(make_ops):
    ops = make_ops()
    r = ops.start(3, auto_approve=True)
    kinds = {e["kind"] for e in ops.deps.trace.read(r["incident_id"])}
    assert {
        "thought",
        "action",
        "observation",
        "finding",
        "route",
        "plan",
        "review",
        "execution",
    } <= kinds


def test_eval_harness_smoke():
    from incidentops.evals.harness import run_eval, to_markdown

    rep = run_eval(FakeLLM())
    assert rep.critic["catch_rate"] == 1.0 and rep.crash["rate"] == 1.0
    assert "Root-cause accuracy" in to_markdown(rep)
