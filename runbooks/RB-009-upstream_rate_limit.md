---
id: RB-009
category: upstream_rate_limit
title: Upstream API rate limiting
---
## Symptoms
upstream 429 Too Many Requests; upstream rate limit exceeded; payment provider calls failing; Retry-After header

## Remediation
Enable client-side backoff and request smoothing; request a higher quota from the provider.
