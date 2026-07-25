# Production validation

## Load test

Install `.[loadtest]`, deploy an isolated test environment, then run:

```powershell
locust -f tests/load/locustfile.py --headless -u 10 -r 2 -t 5m --host https://staging.example
locust -f tests/load/locustfile.py --headless -u 50 -r 5 -t 10m --host https://staging.example
locust -f tests/load/locustfile.py --headless -u 100 -r 10 -t 10m --host https://staging.example
```

The process fails when request failure rate exceeds 1% or P95 exceeds 2 seconds. Save Locust CSV,
Prometheus CPU/memory, PostgreSQL connection, queue age, and model-provider metrics. Do not publish
numbers until the declared environment and raw report are committed as evidence.

## Recovery drill

`python -m policyguard.scripts.resilience_drill --output data/benchmarks/resilience.json`
proves deterministic provider fallback, automatic canary rollback, and verified restore dry-run.
For staging, additionally terminate one worker, interrupt the primary model route, and restore a
backup into a new database. Record RPO, RTO, lost jobs, duplicate side effects, and reviewer.

## Canary gates

The rollout API requires the current revision, starts at 5%, then allows 10/25/50/100%. Each step
requires fresh success, error, P95 latency, and human-rejection metrics. More than 2 percentage
points of success regression,
1 point of error regression, 20% latency regression, or 3 points of human-rejection regression
causes automatic rollback. Tenant IDs are deterministically bucketed so repeated calls stay stable.

## User trial evidence

Use `docs/templates/USER_TRIAL.md`. Record task duration, completion, corrections, severity, consent,
and a hashed participant identifier. Never place names, contact details, customer content, or private
documents in the repository.
