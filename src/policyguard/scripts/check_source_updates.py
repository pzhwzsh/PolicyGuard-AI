import json
from pathlib import Path

import httpx

from policyguard.application.source_monitor import OfficialSourceMonitor


def main() -> None:
    root = Path(__file__).parents[3]
    registry = json.loads((root / "config/source_registry.json").read_text(encoding="utf-8"))
    sources = [source for source in registry["sources"] if not source.get("cellar_celex")]
    with httpx.Client(
        timeout=45,
        follow_redirects=True,
        headers={"User-Agent": "PolicyGuard-AI source monitor/0.1 (+local research)"},
    ) as client:
        results = OfficialSourceMonitor(
            client,
            root / "data/update-state/source-state.json",
            root / "data/update-state/source-history.jsonl",
            root / "data/update-state/snapshots",
        ).check(sources)
    for result in results:
        print(f"{result.status}: {result.source_url}")
    counts = {status: sum(item.status == status for item in results) for status in {
        "baseline", "unchanged", "changed", "failed"
    }}
    print(f"SUMMARY: registered={len(sources)} statuses={json.dumps(counts, sort_keys=True)}")
    if any(result.status == "changed" for result in results):
        print(
            "ACTION_REQUIRED: snapshots staged; parse, diff, and approve before RAG activation."
        )


if __name__ == "__main__":
    main()
