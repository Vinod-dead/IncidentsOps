# IncidentOps eval results (llm = `fake`)

> Produced with the offline **FakeLLM** (rule-based). These numbers validate the pipeline (routing, state, resilience, checkpoints), **not** LLM quality. Re-run with `--llm anthropic|openai|gemini`.

| Metric | Value |
|---|---|
| Root-cause accuracy | 100% |
| Resolved (verified in sandbox) | 100% |
| Plan safety (no execution without approval) | 100% |
| Avg tool calls / incident | 4.0 |
| Avg supervisor steps / incident | 9.4 |
| Avg tokens / incident | 4938 |
| Est. cost / incident (illustrative $/Mtok) | $0.0148 |
| Avg plan revisions / incident | 0.70 |
| LLM-as-judge plan score (1-5) | 5.00 |
| Critic catch rate on injected bad plans | 60/60 (100%) |
| Critic false positives on good plans | 0/10 |
| Recovery after crash (resume from checkpoint) | 10/10 (100%) |

## Per scenario

| # | True cause | Predicted | Status | Steps | Tools | Revs | Errors | Faults |
|---|---|---|---|---|---|---|---|---|
| 1 | bad_deploy | bad_deploy | resolved | 10 | 4 | 1 | 0 | - |
| 2 | db_pool_exhaustion | db_pool_exhaustion | resolved | 10 | 4 | 1 | 0 | - |
| 3 | memory_leak | memory_leak | resolved | 8 | 4 | 0 | 0 | - |
| 4 | expired_cert | expired_cert | resolved | 10 | 4 | 1 | 0 | - |
| 5 | noisy_neighbor | noisy_neighbor | resolved | 10 | 4 | 1 | 0 | - |
| 6 | disk_full | disk_full | resolved | 10 | 4 | 1 | 0 | - |
| 7 | dns_failure | dns_failure | resolved | 10 | 4 | 1 | 0 | - |
| 8 | cache_stampede | cache_stampede | resolved | 8 | 4 | 0 | 0 | query_logs:flaky |
| 9 | upstream_rate_limit | upstream_rate_limit | resolved | 8 | 4 | 0 | 1 | query_logs:down |
| 10 | config_error | config_error | resolved | 10 | 4 | 1 | 0 | query_logs:noise |
