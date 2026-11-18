"""Generate the 10 deterministic incident fixtures in scenarios/ (seeded)."""

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "scenarios"
N = 12  # samples, 5 minutes apart (index 11 = now)

INFO = [
    "INFO request completed in {n}ms",
    "DEBUG cache lookup key=user:{n}",
    "INFO healthcheck ok",
    "INFO scheduler tick {n}",
    "DEBUG metrics flush ok",
    "INFO connection accepted from 10.0.{n}.4",
]
BENIGN_WARN = [
    "WARN retrying idempotent request (attempt 2)",
    "WARN deprecated API /v1/ping used by legacy-client",
]
DECOY = [
    "ERROR healthcheck probe timeout (single occurrence, auto-recovered)",
    "WARN GC pause 14ms exceeded soft threshold",
    "ERROR transient gRPC CANCELLED on client disconnect",
    "ERROR failed to emit trace span: exporter queue full",
    "WARN slow healthcheck probe 480ms",
]


def flat(rng, base, jitter=0.02):
    return [round(base * (1 + rng.uniform(-jitter, jitter)), 4) for _ in range(N)]


def ramp(a, b, start=0, strict=False):
    out = []
    for i in range(N):
        if i < start:
            out.append(a)
        else:
            frac = (i - start + 1) / (N - start)
            out.append(round(a + (b - a) * frac, 4))
    return out


def healthy(rng):
    return {
        "error_rate": flat(rng, 0.01),
        "p99_latency_ms": flat(rng, 220),
        "memory_rss_mb": flat(rng, 800),
        "db_connections_used": flat(rng, 40),
        "db_connections_max": [100.0] * N,
        "cpu_steal_pct": flat(rng, 1.0),
        "disk_used_pct": flat(rng, 55),
        "cert_days_remaining": [30.0] * N,
        "dns_error_rate": [0.0] * N,
        "cache_hit_ratio": flat(rng, 0.95),
        "upstream_429_rate": [0.0] * N,
        "config_reload_failures": [0.0] * N,
    }


def deploys(recent=None):
    base = [
        {"version": "v2.13.4", "minutes_ago": 4320, "author": "ci", "change": "dependency bumps"},
        {"version": "v2.13.3", "minutes_ago": 9000, "author": "ci", "change": "minor fix"},
    ]
    return ([recent] if recent else []) + base


CAUSES = {
    "bad_deploy": dict(
        sig=[
            "ERROR NullPointerException at CartService.applyDiscount (build v2.14.1)",
            "ERROR 500 POST /checkout - unhandled exception in discount handler",
        ],
        m=lambda m, r: m.update(error_rate=ramp(0.01, 0.14, 7), p99_latency_ms=ramp(220, 2400, 7)),
        dep={
            "version": "v2.14.1",
            "minutes_ago": 18,
            "author": "dev-a",
            "change": "new discount engine",
        },
    ),
    "db_pool_exhaustion": dict(
        sig=[
            "ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms (connection pool exhausted)",
            "WARN db connection pool exhausted, 87 threads waiting",
        ],
        m=lambda m, r: m.update(
            db_connections_used=ramp(40, 100, 2)[:-4] + [100.0] * 4,
            p99_latency_ms=ramp(220, 3500, 4),
            error_rate=ramp(0.01, 0.09, 5),
        ),
    ),
    "memory_leak": dict(
        sig=[
            "WARN GC overhead high: heap 94% used",
            "ERROR java.lang.OutOfMemoryError: Java heap space",
            "ERROR container terminated reason=OOMKilled restartCount=7",
        ],
        m=lambda m, r: m.update(
            memory_rss_mb=ramp(800, 3900, 0),
            p99_latency_ms=ramp(220, 900, 6),
            error_rate=flat(r, 0.02),
        ),
    ),
    "expired_cert": dict(
        sig=[
            "ERROR x509: certificate has expired or is not yet valid (payments-gateway.internal)",
            "ERROR TLS handshake failed: certificate has expired",
        ],
        m=lambda m, r: m.update(
            cert_days_remaining=[5, 4, 3, 2, 1, 1, 0, 0, 0, 0, 0, 0], error_rate=ramp(0.01, 0.35, 6)
        ),
    ),
    "noisy_neighbor": dict(
        sig=[
            "WARN CPU steal time high on node ip-10-0-3-17 (noisy neighbor suspected)",
            "WARN request throttled: CPU steal 36% on shared host",
        ],
        m=lambda m, r: m.update(cpu_steal_pct=ramp(1, 38, 3), p99_latency_ms=ramp(220, 1500, 3)),
    ),
    "disk_full": dict(
        sig=[
            "ERROR write /var/lib/data/wal: No space left on device",
            "ERROR failed to append segment: No space left on device",
        ],
        m=lambda m, r: m.update(
            disk_used_pct=[min(100.0, v) for v in ramp(55, 112, 0)], error_rate=ramp(0.01, 0.2, 8)
        ),
    ),
    "dns_failure": dict(
        sig=[
            "ERROR dial tcp: lookup inventory-db.internal: no such host (NXDOMAIN)",
            "ERROR resolver returned NXDOMAIN for stock-api.internal",
        ],
        m=lambda m, r: m.update(dns_error_rate=ramp(0.0, 0.62, 6), error_rate=ramp(0.01, 0.25, 6)),
    ),
    "cache_stampede": dict(
        sig=[
            "WARN cache miss rate 92% for key prefix product:*, thundering herd on origin",
            "WARN origin request queue depth 1900 after mass cache expiry",
        ],
        m=lambda m, r: m.update(
            cache_hit_ratio=ramp(0.95, 0.12, 6),
            p99_latency_ms=ramp(220, 2100, 6),
            db_connections_used=ramp(40, 72, 6),
            error_rate=flat(r, 0.02),
        ),
    ),
    "upstream_rate_limit": dict(
        sig=[
            "ERROR upstream payments-api returned 429 Too Many Requests",
            "WARN 429 Too Many Requests from payments-api, Retry-After=30",
        ],
        m=lambda m, r: m.update(
            upstream_429_rate=ramp(0.0, 0.45, 5), error_rate=ramp(0.01, 0.12, 5)
        ),
    ),
    "config_error": dict(
        sig=[
            "ERROR config reload failed: invalid config key 'timeout_ms' (expected duration)",
            "WARN falling back to previous config; invalid config key rejected",
        ],
        m=lambda m, r: m.update(
            config_reload_failures=[0] * 9 + [1.0, 2.0, 3.0], error_rate=flat(r, 0.02)
        ),
        dep={
            "version": "cfg-2026-77",
            "minutes_ago": 12,
            "author": "ops-b",
            "change": "config push",
        },
    ),
}

# (id, cause, service, alert, tool_faults)
PLAN = [
    (
        1,
        "bad_deploy",
        "checkout-service",
        "checkout-service: HTTP 5xx rate above 5% for 10 minutes",
        {},
    ),
    (
        2,
        "db_pool_exhaustion",
        "orders-service",
        "orders-service: p99 latency above 3s for 10 minutes",
        {},
    ),
    (3, "memory_leak", "cart-service", "cart-service: pod restarts and rising latency", {}),
    (
        4,
        "expired_cert",
        "payments-gateway",
        "payments-gateway: TLS handshake failures from clients",
        {},
    ),
    (
        5,
        "noisy_neighbor",
        "search-service",
        "search-service: latency degradation on a single node pool",
        {},
    ),
    (6, "disk_full", "ingest-service", "ingest-service: writes failing, error rate climbing", {}),
    (
        7,
        "dns_failure",
        "inventory-service",
        "inventory-service: upstream lookups failing intermittently",
        {},
    ),
    (
        8,
        "cache_stampede",
        "catalog-service",
        "catalog-service: origin load and latency spike",
        {"query_logs": {"mode": "flaky", "fail_first": 2}},
    ),
    (
        9,
        "upstream_rate_limit",
        "billing-service",
        "billing-service: payment provider calls failing",
        {"query_logs": {"mode": "down"}},
    ),
    (
        10,
        "config_error",
        "profile-service",
        "profile-service: new settings not taking effect",
        {"query_logs": {"mode": "noise"}},
    ),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for sid, cause, svc, alert, faults in PLAN:
        rng = random.Random(1000 + sid)
        spec = CAUSES[cause]
        metrics = healthy(rng)
        spec["m"](metrics, rng)
        logs = []
        for _ in range(24):
            raw = rng.choice(INFO).format(n=rng.randint(5, 90))
            logs.append(
                {"t": rng.randint(1, 60), "level": raw.split()[0], "msg": raw.split(" ", 1)[1]}
            )
        for m in BENIGN_WARN:
            logs.append({"t": rng.randint(20, 60), "level": "WARN", "msg": m.split(" ", 1)[1]})
        for i in range(6):
            line = spec["sig"][i % len(spec["sig"])]
            logs.append(
                {"t": rng.randint(1, 20), "level": line.split()[0], "msg": line.split(" ", 1)[1]}
            )
        decoy = [
            {"t": rng.randint(1, 60), "level": d.split()[0], "msg": d.split(" ", 1)[1]}
            for d in DECOY
            for _ in range(2)
        ]
        doc = {
            "id": sid,
            "name": f"{cause} on {svc}",
            "service": svc,
            "alert": alert,
            "root_cause": cause,
            "logs": logs,
            "decoy_logs": decoy,
            "metrics": metrics,
            "deploys": deploys(spec.get("dep")),
            "tool_faults": faults,
        }
        (OUT / f"scenario_{sid:02d}.json").write_text(json.dumps(doc, indent=1))
    print("wrote", len(PLAN), "scenarios")


if __name__ == "__main__":
    main()
