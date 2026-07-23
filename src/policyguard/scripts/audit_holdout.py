import argparse
import json
from pathlib import Path

from policyguard.application.evaluation_audit import audit_holdout_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--development", nargs="+", required=True)
    parser.add_argument("--output", default="data/benchmarks/holdout-audit.json")
    args = parser.parse_args()
    root = Path(__file__).parents[3]
    report = audit_holdout_dataset(
        root / args.dataset, [root / item for item in args.development]
    )
    target = root / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ready_for_reportable_holdout_metrics"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
