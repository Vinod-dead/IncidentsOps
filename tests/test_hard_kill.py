"""Real process kill (os._exit(137)), then resume in a NEW process from the SQLite checkpoint."""

import json
import os
import subprocess
import sys


def test_hard_kill_then_resume_does_not_redo_finished_work(tmp_path):
    env = {**os.environ, "INCIDENTOPS_HOME": str(tmp_path)}
    cli = [sys.executable, "-m", "incidentops.cli"]
    p = subprocess.run(
        [
            *cli,
            "run",
            "--scenario",
            "3",
            "--live",
            "--auto-approve",
            "--incident-id",
            "k1",
            "--crash-after",
            "5",
            "--hard-kill",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 137
    p = subprocess.run(
        [*cli, "resume", "k1", "--auto-approve"], env=env, capture_output=True, text=True
    )
    assert "resolved" in p.stdout, p.stderr
    events = [
        json.loads(line) for line in (tmp_path / "traces" / "k1.jsonl").read_text().splitlines()
    ]
    findings = [e["node"] for e in events if e["kind"] == "finding"]
    assert sorted(findings) == [
        "log_analyst",
        "metrics_analyst",
        "runbook_retriever",
    ]  # each exactly once
