import json
from pathlib import Path

from policyguard.application.source_registry import load_source_registry
from policyguard.application.source_updates import stage_source_snapshot


def main() -> None:
    root = Path(__file__).parents[3]
    sources = load_source_registry(root / "config/source_registry.json")
    state = json.loads((root / "data/update-state/source-state.json").read_text(encoding="utf-8"))
    staged = []
    for source in sources:
        current = state.get(source["source_url"])
        if not current or not current.get("snapshot_path"):
            continue
        staged.append(stage_source_snapshot(
            source, current, root / "data/update-state/staged"
        ))
    print(json.dumps({
        "staged_count": len(staged),
        "updates": [
            {"source_id": item["source_id"], "sections": item["section_count"]}
            for item in staged
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
