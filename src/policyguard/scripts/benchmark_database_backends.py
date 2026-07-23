import json
import os
from pathlib import Path

from policyguard.application.portfolio_benchmark import (
    build_marketing_cases,
    evaluate_concurrent_workflows,
    write_json,
)
from policyguard.application.runtime_benchmark import evaluate_job_queue


def main() -> None:
    root = Path(__file__).parents[3]
    postgres_url = os.getenv("POSTGRES_BENCHMARK_URL", "")
    if not postgres_url:
        raise SystemExit("POSTGRES_BENCHMARK_URL is required")
    cases = build_marketing_cases()
    worker_counts = (1, 4, 8)
    report = {
        "schema_version": "1.0",
        "traffic_type": "synthetic_deterministic_not_production",
        "case_count": len(cases),
        "worker_counts": list(worker_counts),
        "sqlite": [
            evaluate_concurrent_workflows(root, cases, workers=workers)
            for workers in worker_counts
        ],
        "postgresql": [
            evaluate_concurrent_workflows(
                root, cases, workers=workers, database_url=postgres_url,
                backend="postgresql",
            )
            for workers in worker_counts
        ],
        "postgresql_job_queue": evaluate_job_queue(postgres_url, jobs=100, workers=8),
    }
    write_json(root / "data/benchmarks/database-backend-comparison.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
