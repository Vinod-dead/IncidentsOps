---
id: RB-003
category: memory_leak
title: Memory leak and heap exhaustion
---
## Symptoms
memory rss rising steadily; heap usage high, GC overhead; OutOfMemoryError; pods OOMKilled and restarting; latency creeping up

## Remediation
Restart the service to reclaim memory immediately, then capture a heap dump and fix the leak.
