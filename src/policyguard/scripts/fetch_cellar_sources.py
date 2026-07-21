import json
from pathlib import Path

import httpx

from policyguard.application.cellar import CellarClient
from policyguard.application.source_monitor import OfficialSourceMonitor
from policyguard.application.source_registry import load_source_registry
from policyguard.application.source_updates import stage_source_snapshot


def main() -> None:
    root = Path(__file__).parents[3]
    sources = [
        item for item in load_source_registry(root / "config/source_registry.json")
        if item.get("cellar_celex")
    ]
    state_path = root / "data/update-state/cellar-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    staged = []
    with httpx.Client(
        timeout=90, follow_redirects=True,
        headers={"User-Agent": "PolicyGuard-AI CELLAR client/0.1"},
    ) as client:
        cellar = CellarClient(client)
        for source in sources:
            document = cellar.fetch_xhtml(source["cellar_celex"])
            synthetic = {**source, "source_url": source["source_url"], "format": "html"}
            result = OfficialSourceMonitor(
                httpx.Client(transport=httpx.MockTransport(
                    lambda request, d=document: httpx.Response(
                        200, content=d.content,
                        headers={"etag": d.etag or "", "last-modified": d.last_modified or ""},
                    )
                )),
                state_path,
                root / "data/update-state/cellar-history.jsonl",
                root / "data/update-state/snapshots",
            ).check([synthetic])[0]
            state = json.loads(state_path.read_text(encoding="utf-8"))
            staged.append(stage_source_snapshot(
                source, state[source["source_url"]], root / "data/update-state/staged"
            ))
            print(f"{result.status}: {source['id']} sections={staged[-1]['section_count']}")


if __name__ == "__main__":
    main()
