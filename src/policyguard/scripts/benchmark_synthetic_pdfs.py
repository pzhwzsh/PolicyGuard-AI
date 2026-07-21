import json
from pathlib import Path

from policyguard.application.synthetic_pdf_benchmark import run_synthetic_pdf_benchmark


def main() -> None:
    root = Path(__file__).parents[3]
    print(json.dumps(run_synthetic_pdf_benchmark(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
