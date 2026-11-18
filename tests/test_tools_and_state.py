import pytest

from incidentops.llm.base import LLMOutputError, extract_json, llm_json
from incidentops.llm.fake import FakeLLM, diagnose_metrics
from incidentops.state import Finding, ReActStep, build_hypotheses
from incidentops.tools.mock import ToolBox, list_scenarios, load_scenario
from incidentops.tools.runbooks import RunbookRetriever
from incidentops.tools.sandbox import Sandbox, idempotency_key


def test_ten_scenarios_load(settings):
    sc = list_scenarios(settings)
    assert len(sc) == 10 and len({s.root_cause for s in sc}) == 10
    assert sum(bool(s.tool_faults) for s in sc) >= 2


def test_tools_deterministic(settings):
    r = RunbookRetriever(settings.runbooks_dir)
    a = ToolBox(load_scenario(settings, 3), r).query_logs("cart-service")
    b = ToolBox(load_scenario(settings, 3), r).query_logs("cart-service")
    assert a == b and a


def test_noise_mode_hides_signal(settings):
    box = ToolBox(load_scenario(settings, 10), RunbookRetriever(settings.runbooks_dir))
    assert not any("config" in x["msg"] for x in box.query_logs("profile-service"))


@pytest.mark.parametrize("sid", range(1, 11))
def test_metrics_rules_identify_cause(settings, sid):
    sc = load_scenario(settings, sid)
    d = diagnose_metrics(sc.metrics, sc.deploys)
    assert d and d[0] == sc.root_cause


def test_react_step_requires_exactly_one():
    with pytest.raises(ValueError):
        ReActStep.model_validate({"thought": "x"})
    with pytest.raises(ValueError):
        ReActStep.model_validate(
            {
                "thought": "x",
                "action": {"tool": "a"},
                "finding": {"category": "unknown", "summary": "", "confidence": 0},
            }
        )


def test_finding_rejects_bad_category():
    with pytest.raises(ValueError):
        Finding(source="x", category="aliens", summary="", confidence=0.5)


def test_hypotheses_noisy_or():
    f = [
        {"source": "a", "category": "disk_full", "confidence": 0.7, "evidence": ["e"]},
        {"source": "b", "category": "disk_full", "confidence": 0.5, "evidence": ["e"]},
        {"source": "c", "category": "unknown", "confidence": 0.0, "evidence": []},
    ]
    h = build_hypotheses(f)
    assert (
        len(h) == 1 and h[0]["confidence"] == pytest.approx(0.85) and h[0]["sources"] == ["a", "b"]
    )


def test_extract_json_handles_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json")


def test_llm_json_retries_with_feedback_then_fails():
    llm = FakeLLM(garble={"planner": 2})
    sys_ = "ROLE: planner\n"
    prompt = (
        'CONTEXT_JSON: {"hypotheses": [{"category": "disk_full", "confidence": 0.9}], '
        '"catalog": [{"name": "expand_disk_volume", "risk": "medium", "fixes": ["disk_full"]}], '
        '"service": "s"}'
    )
    out, tokens = llm_json(llm, sys_, prompt, lambda d: d["root_cause"], retries=2)
    assert out == "disk_full" and llm.calls["planner"] == 3
    llm2 = FakeLLM(garble={"planner": 5})
    with pytest.raises(LLMOutputError):
        llm_json(llm2, sys_, prompt, lambda d: d["root_cause"], retries=2)


def test_sandbox_idempotent(tmp_path):
    sb = Sandbox(tmp_path / "s.sqlite")
    k = idempotency_key("i", "restart_service", {}, "canary")
    assert sb.execute(k, "restart_service", {}, "low", False).status == "ok"
    assert sb.execute(k, "restart_service", {}, "low", False).status == "skipped_duplicate"
    assert sb.execute("k2", "restart_service", {}, "low", True).status == "dry_run"
    assert sb.execute("k3", "rm_rf", {}, "low", False).status == "failed"
