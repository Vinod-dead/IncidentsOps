"""Deterministic rule-based LLM stand-in.

It is NOT a language model. It reads the same prompts a real model would and applies
keyword/threshold rules, so the whole pipeline (routing, state, checkpoints, resilience,
evals) can be tested offline and reproducibly. Eval numbers produced with FakeLLM validate the
*plumbing*, not LLM quality; run with a real provider for real numbers.
"""

import json
import re
from collections import Counter
from typing import Any

from ..tools.catalog import ACTIONS
from .base import LLMResult

SIGNATURES = [
    (r"connection pool exhausted", "db_pool_exhaustion", 0.7),
    (r"OutOfMemoryError|heap \d+% used", "memory_leak", 0.7),
    (r"certificate has expired", "expired_cert", 0.7),
    (r"CPU steal", "noisy_neighbor", 0.7),
    (r"No space left on device", "disk_full", 0.7),
    (r"NXDOMAIN|no such host", "dns_failure", 0.7),
    (r"cache miss rate|thundering herd", "cache_stampede", 0.7),
    (r"429 Too Many Requests", "upstream_rate_limit", 0.7),
    (r"invalid config key|config reload failed", "config_error", 0.7),
    (r"NullPointerException", "bad_deploy", 0.55),
]
SUMMARY = {
    "bad_deploy": "error rate and 5xx spike right after a recent deploy, new build regression",
    "db_pool_exhaustion": "db connections used at max, connection pool exhausted, threads waiting",
    "memory_leak": "memory rss rising steadily, heap exhaustion, OutOfMemoryError, pods restarting",
    "expired_cert": "certificate has expired, x509 TLS handshake failures, cert days remaining zero",
    "noisy_neighbor": "cpu steal time high on node, throttled requests, noisy neighbor on shared host",
    "disk_full": "disk used 100 percent, no space left on device, writes failing",
    "dns_failure": "dns error rate high, NXDOMAIN no such host lookup failures",
    "cache_stampede": "cache hit ratio collapsed, cache miss rate high, thundering herd on origin",
    "upstream_rate_limit": "upstream 429 Too Many Requests, rate limit exceeded, Retry-After",
    "config_error": "config reload failures, invalid config key rejected after config push",
}


def _finding(
    cat: str, summary: str, evidence: list[str], conf: float, thought: str
) -> dict[str, Any]:
    return {
        "thought": thought,
        "finding": {"category": cat, "summary": summary, "evidence": evidence, "confidence": conf},
    }


def _unknown(why: str) -> dict[str, Any]:
    return _finding("unknown", why, [], 0.0, "Evidence is insufficient to name a cause.")


def diagnose_metrics(
    m: dict[str, list[float]], deploys: list[dict[str, Any]]
) -> tuple[str, list[str]] | None:
    def last(k: str, n: int = 1) -> list[float]:
        return m.get(k, [])[-n:]

    mem = m.get("memory_rss_mb", [])
    if (
        len(mem) > 3
        and all(b > a for a, b in zip(mem, mem[1:], strict=False))
        and mem[-1] > 1.5 * mem[0]
    ):
        return "memory_leak", [f"memory_rss_mb rose {mem[0]:.0f} -> {mem[-1]:.0f}"]
    used, mx = last("db_connections_used", 3), last("db_connections_max", 3)
    if used and mx and all(u >= x for u, x in zip(used, mx, strict=False)):
        return "db_pool_exhaustion", [f"db_connections_used pinned at max ({mx[-1]:.0f})"]
    if last("cpu_steal_pct")[0:1] and last("cpu_steal_pct")[0] > 20:
        return "noisy_neighbor", [f"cpu_steal_pct={last('cpu_steal_pct')[0]:.0f}"]
    if last("disk_used_pct")[0:1] and last("disk_used_pct")[0] >= 97:
        return "disk_full", [f"disk_used_pct={last('disk_used_pct')[0]:.0f}"]
    if last("cert_days_remaining")[0:1] and last("cert_days_remaining")[0] <= 0:
        return "expired_cert", ["cert_days_remaining=0"]
    if last("dns_error_rate")[0:1] and last("dns_error_rate")[0] > 0.3:
        return "dns_failure", [f"dns_error_rate={last('dns_error_rate')[0]:.2f}"]
    ch = m.get("cache_hit_ratio", [])
    if ch and ch[-1] < 0.3 < 0.8 < ch[0]:
        return "cache_stampede", [f"cache_hit_ratio fell {ch[0]:.2f} -> {ch[-1]:.2f}"]
    if last("upstream_429_rate")[0:1] and last("upstream_429_rate")[0] > 0.2:
        return "upstream_rate_limit", [f"upstream_429_rate={last('upstream_429_rate')[0]:.2f}"]
    if last("config_reload_failures")[0:1] and last("config_reload_failures")[0] >= 1:
        return "config_error", [f"config_reload_failures={last('config_reload_failures')[0]:.0f}"]
    er = m.get("error_rate", [])
    recent = [d for d in deploys if d.get("minutes_ago", 9999) <= 30]
    if er and recent and er[-1] >= 5 * max(er[0], 0.005):
        return "bad_deploy", [
            f"error_rate {er[0]:.3f} -> {er[-1]:.3f}",
            f"deploy {recent[0]['version']} {recent[0]['minutes_ago']}m ago",
        ]
    return None


def _react_log(ctx: dict[str, Any]) -> dict[str, Any]:
    pad = ctx["scratchpad"]
    if not pad:
        return {
            "thought": "Start with warnings and errors from the service logs.",
            "action": {
                "tool": "query_logs",
                "args": {"service": ctx["service"], "level": "WARN", "limit": 30},
            },
        }
    obs = pad[-1]["observation"]
    if isinstance(obs, dict) and "error" in obs:
        return _unknown(f"logs unavailable: {obs['error']}")
    hits: Counter[str] = Counter()
    ev: dict[str, list[str]] = {}
    conf: dict[str, float] = {}
    for row in obs:
        for pat, cat, c in SIGNATURES:
            if re.search(pat, row["msg"]):
                hits[cat] += 1
                ev.setdefault(cat, []).append(row["msg"][:120])
                conf[cat] = c
    if not hits:
        return _unknown("no known failure signature in logs (only generic warnings)")
    cat = hits.most_common(1)[0][0]
    return _finding(
        cat,
        SUMMARY[cat],
        list(dict.fromkeys(ev[cat]))[:3],
        conf[cat],
        f"{hits[cat]} log lines match the {cat} signature.",
    )


def _react_metrics(ctx: dict[str, Any]) -> dict[str, Any]:
    pad = ctx["scratchpad"]
    if len(pad) == 0:
        return {
            "thought": "Pull service metrics first.",
            "action": {"tool": "get_metrics", "args": {"service": ctx["service"]}},
        }
    if len(pad) == 1:
        return {
            "thought": "Check recent deploys to correlate with metric changes.",
            "action": {"tool": "get_recent_deploys", "args": {"service": ctx["service"]}},
        }
    metrics, deploys = pad[0]["observation"], pad[1]["observation"]
    if isinstance(metrics, dict) and "error" in metrics:
        return _unknown(f"metrics unavailable: {metrics['error']}")
    if isinstance(deploys, dict):
        deploys = []
    d = diagnose_metrics(metrics, deploys)
    if not d:
        return _unknown("metrics show no clear anomaly signature")
    cat, ev = d
    return _finding(cat, SUMMARY[cat], ev, 0.65, f"Metric pattern matches {cat}.")


def _react_runbook(ctx: dict[str, Any]) -> dict[str, Any]:
    pad = ctx["scratchpad"]
    if not pad:
        q = (
            ctx["alert"]
            + " "
            + " ".join(
                f["summary"] for f in ctx.get("prior_findings", []) if f["category"] != "unknown"
            )
        )
        return {
            "thought": "Search runbooks using the alert and findings so far.",
            "action": {"tool": "search_runbooks", "args": {"query": q, "k": 3}},
        }
    obs = pad[-1]["observation"]
    if isinstance(obs, dict) or not obs or obs[0]["score"] < 1.0:
        return _unknown("no runbook matches the symptoms")
    top = obs[0]
    return _finding(
        top["category"],
        f"Runbook match: {top['title']}",
        [f"{top['id']} {top['title']}"],
        0.5,
        "Top runbook hit is consistent with the symptoms.",
    )


def _plan(ctx: dict[str, Any]) -> dict[str, Any]:
    top = ctx["hypotheses"][0]
    action = next(c for c in ctx["catalog"] if top["category"] in c["fixes"])
    revised = bool(ctx.get("critic_feedback")) or ctx.get("revision_count", 0) > 0
    scope = (
        "single_instance" if action["risk"] == "low" else "canary" if revised else "all_instances"
    )  # first draft is deliberately broad
    return {
        "root_cause": top["category"],
        "summary": f"Apply {action['name']} for {top['category']}",
        "actions": [
            {
                "action": action["name"],
                "params": {"service": ctx["service"]},
                "scope": scope,
                "rationale": f"Top hypothesis {top['category']} (conf {top['confidence']})",
            }
        ],
    }


def _judge(ctx: dict[str, Any]) -> dict[str, Any]:
    plan, truth = ctx["plan"], ctx["true_root_cause"]
    ok = plan["root_cause"] == truth and any(
        truth in ACTIONS.get(a["action"], {}).get("fixes", []) for a in plan["actions"]
    )
    safe = all(a["scope"] != "all_instances" or a["risk"] == "low" for a in plan["actions"])
    return {"score": 5 if ok and safe else 4 if ok else 1, "rationale": "heuristic (FakeLLM)"}


HANDLERS = {
    "log_analyst": _react_log,
    "metrics_analyst": _react_metrics,
    "runbook_retriever": _react_runbook,
    "planner": _plan,
    "judge": _judge,
    "critic": lambda ctx: {"verdict": "approve", "reasons": []},
    "supervisor": lambda ctx: {"next": ctx["allowed"][0], "reason": "first allowed"},
}


class FakeLLM:
    name = "fake"

    def __init__(self, garble: dict[str, int] | None = None) -> None:
        """garble={'planner': 2} makes the first 2 planner replies invalid (tests retry-with-feedback)."""
        self.garble = dict(garble or {})
        self.calls: Counter[str] = Counter()

    def complete(self, system: str, prompt: str) -> LLMResult:
        role = system.split("\n", 1)[0].removeprefix("ROLE: ").strip()
        self.calls[role] += 1
        if self.garble.get(role, 0) > 0:
            self.garble[role] -= 1
            text = "Sure! Here is my analysis (no JSON, sorry)."
        else:
            ctx = json.loads(prompt.split("CONTEXT_JSON:", 1)[1].split("\n", 1)[0])
            text = json.dumps(HANDLERS[role](ctx))
        return LLMResult(text, (len(system) + len(prompt)) // 4, len(text) // 4)
