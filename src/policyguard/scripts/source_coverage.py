import json
from dataclasses import asdict
from pathlib import Path

from policyguard.application.source_registry import load_source_registry, source_coverage


def main() -> None:
    root = Path(__file__).parents[3]
    sources = load_source_registry(root / "config/source_registry.json")
    print(json.dumps(asdict(source_coverage(sources)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
