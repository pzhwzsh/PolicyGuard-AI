import json
from pathlib import Path

from policyguard.application.remediation_evaluation import evaluate_remediation_dataset


def main() -> None:
    root = Path(__file__).parents[3]
    report = evaluate_remediation_dataset(
        root / "data/evaluation/remediation-quality-v1.json"
    )
    target = root / "data/benchmarks/remediation-quality-v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
