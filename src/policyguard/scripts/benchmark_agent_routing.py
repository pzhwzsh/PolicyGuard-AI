import json
from pathlib import Path

from policyguard.application.agent_routing import deterministic_route, evaluate_router


def main() -> None:
    root = Path(__file__).parents[3]
    cases = json.loads(
        (root / "data/evaluation/agent-routing-v1.json").read_text(encoding="utf-8")
    )["samples"]
    report = {"deterministic_router": evaluate_router(cases, deterministic_route)}
    target = root / "data/benchmarks/agent-routing.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
