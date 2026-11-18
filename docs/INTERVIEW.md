# Presenting IncidentOps in an interview

## 30-second pitch
"I built a multi-agent incident-triage system. A supervisor delegates to specialist agents for logs, metrics and
runbook retrieval; a planner proposes a fix; a critic reviews it; a human approves before anything executes. State is
checkpointed in SQLite, so the workflow survives a hard kill and can pause for days awaiting approval. I evaluated it
on 10 synthetic incidents, including broken and noisy tools, and scaffolded a Google ADK port to compare frameworks."

## 2-minute walkthrough
1. **Problem:** on-call triage is repetitive, but autonomous remediation is risky.
2. **Design:** supervisor + least-privilege specialists. The supervisor's legal moves are a pure function of
   state; the LLM only chooses among legal options.
3. **Reasoning:** analysts use ReAct. The critic is a deterministic rubric plus an LLM second opinion that cannot
   override a rubric failure. Revisions are capped at 2.
4. **State:** typed state, checkpointed every step; the human gate is a graph interrupt and resumes in any process.
5. **Failures:** retry with backoff + jitter, per-tool circuit breakers, step/token/active-time budgets,
   validated LLM output with retry-with-feedback, idempotent executor, escalate on anything unrecoverable.
6. **Evaluation:** accuracy, safety, cost, critic red-team on injected bad plans, crash-recovery.

## Say this honestly
- The offline eval uses a rule-based FakeLLM, so the 100% numbers prove the pipeline and not LLM quality.
  "Next step is running the same harness against a real model and reporting where it fails."
- The ADK port is a scaffold. Only say you compared frameworks after you have built and tested it.
- A real bug the kill test caught: LangGraph's default async checkpointing lost the last step on `kill -9`;
  `durability="sync"` fixed it. This is a great "what went wrong" story.

## Likely questions
- **Why multi-agent vs one agent with all tools?** Focused context, tool isolation (least privilege), per-role evals.
  The cost is more tokens and coordination failure modes, which the guards and budgets address.
- **How does the supervisor decide?** `allowed_next` computes legal options from state; LLM picks among them only
  when there is a genuine choice. Executor is unreachable before critic + human approval.
- **How do you stop the critic rubber-stamping?** The rubric is code. The LLM can only add objections. The red-team
  eval injects 6 bad-plan types across 10 scenarios (60 cases).
- **What if the human never responds?** The checkpoint persists as awaiting_approval; active-time budget excludes waiting.
  Production: add an SLA timer that escalates.
- **What if the executor crashes mid-action?** Idempotency key in sqlite; a resumed run skips applied actions.
- **Where does it fail?** Be specific after running a real LLM: ambiguous alerts, multi-cause incidents, noisy real logs.
- **How would you productionize?** Postgres checkpointer, real tool integrations with RBAC, audit log, prompt
  versioning, shadow mode (no execute rights) first, shared circuit-breaker state, embedding retrieval.
- **Why mocked tools?** Deterministic evals and safe by construction; same `ToolBox` interface as real integrations.
- **LangGraph vs ADK?** Fill in from your own build. Expected: LangGraph = explicit graph, native interrupt/resume
  and checkpointers; ADK = agent hierarchy, faster scaffolding, human-in-loop via tool confirmation/long-running tools.

## Demo script (90 seconds)
1. `incidentops run --scenario 7 --live` -> shows the plan paused for approval (high-risk DNS failover).
2. `incidentops approve <id>` -> resolves. `incidentops trace <id>` -> thoughts and routing.
3. `python scripts/crash_demo.py` -> process killed (exit 137), resumed, no step repeated.
4. Show `evals/RESULTS.md`.
