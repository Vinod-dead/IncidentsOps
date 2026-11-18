"""Google ADK port (Milestone 9) -- SCAFFOLD, NOT TESTED in this repo.

Install:  pip install google-adk     (check the current ADK docs: class names/args evolve)

Mapping from the LangGraph version:
  supervisor + specialists  -> LlmAgent(sub_agents=[...]) hierarchy (LLM-driven delegation)
  deterministic guards      -> before_agent_callback / custom BaseAgent for the routing rules
  ReAct loops               -> LlmAgent with tools=[plain python functions] (ADK runs the loop)
  critic revise loop        -> LoopAgent([planner, critic], max_iterations=2) + escalate on approve
  checkpointed state        -> SessionService (DatabaseSessionService for persistence)
  human approval            -> long-running tool / tool confirmation instead of graph interrupt

Reuse incidentops.tools.* as the tool layer so both ports run the same scenarios and evals.
"""

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent  # type: ignore[import-not-found]

from ..config import Settings
from ..tools.catalog import ACTIONS
from ..tools.mock import ToolBox, load_scenario
from ..tools.runbooks import RunbookRetriever

_s = Settings()
_box = ToolBox(
    load_scenario(_s, 3), RunbookRetriever(_s.runbooks_dir)
)  # TODO: per-incident toolbox
MODEL = "gemini-2.5-flash"


def query_logs(service: str, level: str = "WARN") -> list[dict]:
    """Recent log lines for a service at or above `level`."""
    return _box.query_logs(service, level)


def get_metrics(service: str) -> dict:
    """Last 12 metric samples for a service."""
    return _box.get_metrics(service)


def search_runbooks(query: str) -> list[dict]:
    """Best-matching runbooks."""
    return _box.search_runbooks(query)


log_analyst = LlmAgent(
    name="log_analyst",
    model=MODEL,
    tools=[query_logs],
    instruction="Investigate using logs only. Output a finding as JSON.",
    output_key="log_finding",
)
metrics_analyst = LlmAgent(
    name="metrics_analyst",
    model=MODEL,
    tools=[get_metrics],
    instruction="Investigate using metrics only. Output a finding as JSON.",
    output_key="metrics_finding",
)
runbook_retriever = LlmAgent(
    name="runbook_retriever",
    model=MODEL,
    tools=[search_runbooks],
    instruction="Match findings in state to runbooks.",
    output_key="runbook_finding",
)
planner = LlmAgent(
    name="planner",
    model=MODEL,
    output_key="plan",
    instruction=f"Write a plan from the findings. Allowed actions: {sorted(ACTIONS)}.",
)
critic = LlmAgent(
    name="critic",
    model=MODEL,
    output_key="critique",
    instruction="Score the plan: evidence, reversibility, safety, blast radius. approve|revise.",
)
refine = LoopAgent(name="plan_refine", sub_agents=[planner, critic], max_iterations=2)

root_agent = SequentialAgent(
    name="incident_supervisor",
    sub_agents=[log_analyst, metrics_analyst, runbook_retriever, refine],
)
# TODO: human approval (long-running tool), executor agent, persistence, same eval harness.
