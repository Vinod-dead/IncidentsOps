"""Remediation action catalog: the only actions the system may propose or execute."""

from typing import Any

ACTIONS: dict[str, dict[str, Any]] = {
    "rollback_deploy": {
        "risk": "medium",
        "fixes": ["bad_deploy"],
        "rollback": "Re-deploy the rolled-back version once the fix is ready",
    },
    "scale_db_pool": {
        "risk": "medium",
        "fixes": ["db_pool_exhaustion"],
        "rollback": "Restore the previous pool size setting",
    },
    "restart_service": {
        "risk": "low",
        "fixes": ["memory_leak"],
        "rollback": "Stateless restart; redeploy the same version if it fails to start",
    },
    "renew_certificate": {
        "risk": "medium",
        "fixes": ["expired_cert"],
        "rollback": "Re-install the previous secret version",
    },
    "cordon_and_drain_node": {
        "risk": "medium",
        "fixes": ["noisy_neighbor"],
        "rollback": "Uncordon the node",
    },
    "expand_disk_volume": {
        "risk": "medium",
        "fixes": ["disk_full"],
        "rollback": "Restore from the pre-change snapshot",
    },
    "failover_dns_resolver": {
        "risk": "high",
        "fixes": ["dns_failure"],
        "rollback": "Switch back to the primary resolver",
    },
    "enable_request_coalescing": {
        "risk": "low",
        "fixes": ["cache_stampede"],
        "rollback": "Disable the feature flag",
    },
    "enable_client_backoff": {
        "risk": "low",
        "fixes": ["upstream_rate_limit"],
        "rollback": "Disable the feature flag",
    },
    "revert_config": {
        "risk": "medium",
        "fixes": ["config_error"],
        "rollback": "Re-apply the reverted config version",
    },
}


def catalog_summary() -> list[dict[str, Any]]:
    return [{"name": n, "risk": a["risk"], "fixes": a["fixes"]} for n, a in ACTIONS.items()]
