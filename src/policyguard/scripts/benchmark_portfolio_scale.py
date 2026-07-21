import json
from pathlib import Path

from policyguard.application.portfolio_benchmark import run_portfolio_benchmark


def main() -> None:
    root = Path(__file__).parents[3]
    print(json.dumps(run_portfolio_benchmark(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
