import json
from pathlib import Path

from policyguard.application.evidence_readiness import build_evidence_readiness


def main() -> None:
    root = Path(__file__).parents[3]
    report = build_evidence_readiness(
        root / "data/evidence/v1/source-inventory.json",
        root / "data/evidence/v1/runtime-snapshot.json",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
