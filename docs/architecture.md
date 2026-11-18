# Architecture

## Control flow
Hub-and-spoke LangGraph: every node returns to `supervisor`, which writes `state["next"]`.
`allowed_next(state)` (agents/supervisor.py) is the single source of truth for what is legal:

1. both analysts (log, metrics) -> runbook retriever
2. no hypotheses -> escalate; else planner
3. plan unreviewed -> critic
4. critic says revise -> planner (max 2 revisions, then escalate)
5. critic approves -> human_approval (graph `interrupt`)
6. approved or edited -> executor; rejected -> escalate
7. executed -> finalize (or escalate if sandbox verification fails)

Budgets (steps, tokens, active seconds) are checked before every routing decision.

## State (`state.py`)
`incident_id, scenario_id, alert, findings[], hypotheses[], plan, critic_feedback[], revision_count,
approval_status, execution_log[], step_count, token_budget_used, errors[], status` plus bookkeeping
(`done_agents`, `tool_calls`, `active_seconds`, `dry_run`). Hypotheses merge findings per category with
noisy-OR confidence.

## ReAct (`agents/react.py`)
Thought -> Action -> Observation, up to `react_max_steps`. LLM output is validated with pydantic; invalid
output is retried twice with the validation error fed back, then the agent returns an `unknown` finding and
records the error. Every thought/action/observation goes to `.incidentops/traces/<id>.jsonl`.

## Failure handling
| Failure | Handling |
|---|---|
| Tool timeout / 5xx | timeout, retry with exponential backoff + jitter, per-tool circuit breaker |
| Tool permanently down | breaker opens, analyst returns `unknown`, supervisor routes on |
| Tool returns noise | analyst returns `unknown`; other sources still produce a hypothesis |
| LLM invalid JSON | retry with error feedback x2, then safe default / escalate |
| Specialist raises | recorded in `errors[]`, specialist marked done, graph continues |
| Process killed | checkpoint (sync) + `resume`; idempotent executor |
| Human never answers | state stays `awaiting_approval`; no time budget is consumed |

## Persistence and resume
`SqliteSaver` checkpoints every super-step with `durability="sync"`. `IncidentOps.resume(id)` calls
`invoke(None, config)`. The human gate uses `interrupt()` and `Command(resume=...)`, so approval can happen
in a different process, days later.

## Google ADK port (scaffold)
See `src/incidentops/adk_port/agent.py` for the mapping (sub-agents, LoopAgent for the critic loop,
SessionService for state, long-running tool for approval). Not tested. Comparison notes belong here once built.
