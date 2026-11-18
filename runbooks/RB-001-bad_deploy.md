---
id: RB-001
category: bad_deploy
title: Elevated errors right after a deployment
---
## Symptoms
error rate and 5xx spike shortly after a new release; NullPointerException and unhandled exception in new build; recent deploy version regression; latency rises after deploy

## Remediation
Roll back to the previous known-good version, then investigate the failing change. Confirm error rate returns to baseline.
