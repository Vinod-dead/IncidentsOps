"""Kill -9 style demo: hard-kill the process mid-run, restart, resume from the checkpoint.

python scripts/crash_demo.py
"""

import os
import subprocess
import sys
import tempfile

home = tempfile.mkdtemp(prefix="incidentops-demo-")
env = {**os.environ, "INCIDENTOPS_HOME": home}
cli = [sys.executable, "-m", "incidentops.cli"]
iid = "inc-demo-crash"

print("1) starting incident 3 and hard-killing the process after 5 graph nodes (os._exit(137))...")
p = subprocess.run(
    [
        *cli,
        "run",
        "--scenario",
        "3",
        "--live",
        "--auto-approve",
        "--incident-id",
        iid,
        "--crash-after",
        "5",
        "--hard-kill",
    ],
    env=env,
    capture_output=True,
    text=True,
)
print(f"   process exit code: {p.returncode} (137 = killed)")

print("2) new process: resuming from the SQLite checkpoint...")
p = subprocess.run([*cli, "resume", iid, "--auto-approve"], env=env, capture_output=True, text=True)
print(p.stdout)
assert "resolved" in p.stdout, p.stderr
print("3) trace shows work from both processes:")
p = subprocess.run([*cli, "trace", iid], env=env, capture_output=True, text=True)
print("\n".join(p.stdout.splitlines()[:40]))
