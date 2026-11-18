---
id: RB-002
category: db_pool_exhaustion
title: Database connection pool exhausted
---
## Symptoms
db connections used at max; connection pool exhausted, threads waiting for connection; request timed out; latency climbs as pool saturates

## Remediation
Increase the pool size within database limits and find leaking or slow queries holding connections.
