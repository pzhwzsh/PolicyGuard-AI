import json
import os
from pathlib import Path

from policyguard.application.portfolio_benchmark import (
    build_marketing_cases,
    evaluate_concurrent_workflows,
    write_json,
)


def main() -> None:
    root = Path(__file__).parents[3]
    postgres_url = os.getenv("POSTGRES_BENCHMARK_URL", "")
    if not postgres_url:
        raise SystemExit("POSTGRES_BENCHMARK_URL is required")
    cases = build_marketing_cases()
    report = {
        "schema_version": "1.0",
        "traffic_type": "synthetic_deterministic_not_production",
        "case_count": len(cases),
        "workers": 8,
        "sqlite": evaluate_concurrent_workflows(root, cases, workers=8),
        "postgresql": evaluate_concurrent_workflows(
            root,
            cases,
            workers=8,
            database_url=postgres_url,
            backend="postgresql",
        ),
    }
    write_json(root / "data/benchmarks/database-backend-comparison.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
