---
id: RB-005
category: noisy_neighbor
title: Noisy neighbor on shared host
---
## Symptoms
cpu steal time high on node; throttled requests; latency degradation on one node pool; noisy neighbor suspected; shared host contention

## Remediation
Cordon and drain the affected node so workloads reschedule onto healthy hosts.
