import pytest

from incidentops.config import Settings
from incidentops.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    Resilient,
    ToolError,
    ToolTimeout,
)


def make(**kw):
    sleeps = []
    s = Settings(
        backoff_base_s=0.1, breaker_threshold=3, tool_max_attempts=3, tool_timeout_s=0.2, **kw
    )
    return Resilient(s, sleep=sleeps.append), sleeps


def test_retries_transient_then_succeeds():
    r, sleeps = make()
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        if n["c"] < 3:
            raise ToolError("503")
        return "ok"

    assert r.call("t", flaky) == "ok"
    assert n["c"] == 3 and len(sleeps) == 2
    assert sleeps[1] > sleeps[0] * 0.9  # exponential-ish growth with jitter


def test_gives_up_after_max_attempts_and_opens_breaker():
    r, _ = make()

    def down():
        raise ToolError("503")

    with pytest.raises(ToolError):
        r.call("t", down)
    assert r.breaker("t").state == "open"
    with pytest.raises(CircuitOpenError):
        r.call("t", down)
    assert r.stats["breaker_rejections"] == 1


def test_non_transient_not_retried():
    r, sleeps = make()

    def bad():
        raise ToolError("nope", transient=False)

    with pytest.raises(ToolError):
        r.call("t", bad)
    assert sleeps == []


def test_bad_arguments_do_not_trip_breaker():
    r, _ = make()
    for _ in range(5):
        with pytest.raises(ToolError):
            r.call("t", lambda: 1, bogus=1)
    assert r.breaker("t").state == "closed"


def test_timeout():
    import time

    r, _ = make()
    with pytest.raises(ToolTimeout):
        r.call("slow", lambda: time.sleep(1))
    assert r.stats["timeouts"] >= 1


def test_breaker_half_open_recovers():
    t = {"now": 0.0}
    b = CircuitBreaker(2, 10, clock=lambda: t["now"])
    b.record_failure()
    b.record_failure()
    assert b.state == "open" and not b.allow()
    t["now"] = 11
    assert b.state == "half_open" and b.allow()
    b.record_success()
    assert b.state == "closed"
    b.record_failure()
    b.record_failure()
    t["now"] = 30
    b.record_failure()  # failure in half-open re-opens immediately
    assert b.state == "open"
