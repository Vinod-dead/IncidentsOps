---
id: RB-008
category: cache_stampede
title: Cache stampede on origin
---
## Symptoms
cache hit ratio collapsed; cache miss rate high; thundering herd on origin; mass cache expiry; origin queue depth rising

## Remediation
Enable request coalescing and cache warmup so only one request refills each key.
