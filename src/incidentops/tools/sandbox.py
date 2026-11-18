"""Mock infrastructure. Executes catalog actions with real idempotency (sqlite-backed)."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..state import ExecutionRecord
from .catalog import ACTIONS
from .mock import Scenario


def idempotency_key(incident_id: str, action: str, params: dict[str, Any], scope: str) -> str:
    blob = json.dumps([incident_id, action, params, scope], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class Sandbox:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS applied (key TEXT PRIMARY KEY, action TEXT, ts REAL)"
        )
        self.conn.commit()

    def execute(
        self, key: str, action: str, params: dict[str, Any], risk: str, dry_run: bool
    ) -> ExecutionRecord:
        base: dict[str, Any] = dict(
            idempotency_key=key, action=action, params=params, risk=risk, dry_run=dry_run
        )
        if action not in ACTIONS:
            return ExecutionRecord(**base, status="failed", detail="unknown action")
        if dry_run:
            return ExecutionRecord(**base, status="dry_run", detail=f"would run {action}")
        try:
            self.conn.execute("INSERT INTO applied VALUES (?,?,?)", (key, action, time.time()))
            self.conn.commit()
        except sqlite3.IntegrityError:
            return ExecutionRecord(**base, status="skipped_duplicate", detail="already applied")
        return ExecutionRecord(**base, status="ok", detail=f"applied {action} in sandbox")

    @staticmethod
    def verify(scenario: Scenario, log: list[dict[str, Any]]) -> bool:
        """Healthy iff a real (non-dry-run) action that fixes the true root cause was applied."""
        return any(
            r["status"] in ("ok", "skipped_duplicate")
            and not r["dry_run"]
            and scenario.root_cause in ACTIONS.get(r["action"], {}).get("fixes", [])
            for r in log
        )
