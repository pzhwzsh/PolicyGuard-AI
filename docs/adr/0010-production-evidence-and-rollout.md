# ADR 0010: Production evidence and model rollout

## Status

Accepted.

## Decision

Claims of production readiness require raw, reproducible load and recovery evidence. Model changes
use deterministic tenant canaries with revision locks, explicit reviewer identity, progressive
5/10/25/50/100 traffic steps, measurable quality/latency gates, and automatic rollback.

## Consequences

An offline benchmark or successful demo cannot authorize full rollout. Metrics must come from the
same candidate and declared environment. Rollout state selects the configured LLM for real workflow,
review, remediation, and Agent planner calls; it does not silently mutate environment defaults.
