import json
import os
from pathlib import Path

from policyguard.application.external_smoke import run_external_smoke
from policyguard.config import get_settings


def main() -> None:
    root = Path(__file__).parents[3]
    report = run_external_smoke(get_settings(), root)
    target = root / "data/benchmarks/external-smoke.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.getenv("SMOKE_STRICT", "false").casefold() == "true" and not report["strict_success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
