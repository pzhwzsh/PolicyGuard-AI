# Runtime observability and persistence benchmark v1

Date: 2026-07-22

## Scope

This change adds batched request telemetry and removes workflow-event delete/reinsert write
amplification. Telemetry stores request path, method, status, duration, trace ID, and timestamp. It
does not store request or response bodies.

The internal console reports actual local requests over 1-hour, 24-hour, and 7-day windows. The
existing fixed-load benchmark remains a separate data source.

## SQLite result

The published pre-change fixed-load result used 100 deterministic cases and eight workers:

- throughput: 44.36 cases/s
- P50: 22.822 ms
- P95: 1,142.924 ms
- P99: 1,776.562 ms

Three repeated post-change runs remained noisy. Throughput ranged from 34.56 to 38.77 cases/s, P95
from 796.991 to 1,058.634 ms, and P99 from 1,523.991 to 2,766.069 ms. This does not establish a
general performance improvement. It confirms that SQLite write serialization remains the dominant
concurrency limitation.

## PostgreSQL comparison

The comparison harness requires a dedicated URL containing `benchmark`:

```powershell
$env:POSTGRES_BENCHMARK_URL='postgresql+psycopg://policyguard:policyguard@127.0.0.1:5432/policyguard_benchmark'
python -m policyguard.scripts.benchmark_database_backends
```

The local Docker image was not available during this pass, so no PostgreSQL performance result is
claimed yet. CI continues to verify PostgreSQL migrations independently.
