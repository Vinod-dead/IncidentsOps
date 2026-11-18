"""Central settings. Every limit the resilience layer uses lives here."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.getenv("INCIDENTOPS_ROOT", Path(__file__).resolve().parents[2]))


def _home() -> Path:
    return Path(os.getenv("INCIDENTOPS_HOME", ".incidentops"))


@dataclass
class Settings:
    scenarios_dir: Path = ROOT / "scenarios"
    runbooks_dir: Path = ROOT / "runbooks"
    home: Path = field(default_factory=_home)
    # budgets
    max_graph_steps: int = 30
    max_tokens: int = 60_000
    max_active_seconds: float = 300.0  # time spent working; human wait time is excluded
    react_max_steps: int = 6
    max_revisions: int = 2
    llm_retries: int = 2
    # tool resilience
    tool_timeout_s: float = 5.0
    tool_max_attempts: int = 3
    backoff_base_s: float = 0.2
    backoff_max_s: float = 5.0
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 30.0
    # illustrative blended price; override with INCIDENTOPS_USD_PER_MTOK
    usd_per_mtok: float = float(os.getenv("INCIDENTOPS_USD_PER_MTOK", "3.0"))

    @property
    def checkpoint_db(self) -> Path:
        return self.home / "checkpoints.sqlite"

    @property
    def sandbox_db(self) -> Path:
        return self.home / "sandbox.sqlite"

    @property
    def traces_dir(self) -> Path:
        return self.home / "traces"
