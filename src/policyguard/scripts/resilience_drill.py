"""Run deterministic fallback, rollback, and verified restore drills without external writes."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from policyguard.application.llm import FallbackClaimExtractor
from policyguard.application.local_backup import restore_local_backup
from policyguard.application.model_rollout import ModelRolloutRegistry

METRICS = {
    "success_rate": 0.98,
    "error_rate": 0.01,
    "p95_latency_ms": 500,
    "human_rejection_rate": 0.04,
}


class Extractor:
    provider_name = "drill"

    def __init__(self, model: str, fail: bool) -> None:
        self.model_name = model
        self.fail = fail

    def extract(self, *, title: str, description: str) -> list[dict[str, object]]:
        if self.fail:
            raise TimeoutError("injected_primary_timeout")
        return [{"text": title or description, "field": "title", "confidence": 1.0}]


def _backup(root: Path) -> Path:
    backup = root / "backup"
    backup.mkdir()
    database = backup / "policyguard.db"
    database.write_bytes(b"verified-drill-database")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    (backup / "manifest.json").write_text(
        json.dumps({
            "file_count": 1,
            "files": [{
                "path": "policyguard.db",
                "size": database.stat().st_size,
                "sha256": digest,
            }],
        }),
        encoding="utf-8",
    )
    return backup


def run_drill() -> dict:
    checks = []
    fallback = FallbackClaimExtractor(Extractor("primary", True), Extractor("backup", False))
    fallback.extract(title="drill", description="")
    checks.append({"name": "provider_fallback", "passed": fallback.last_model == "backup"})
    with tempfile.TemporaryDirectory(prefix="policyguard-drill-") as temporary:
        root = Path(temporary)
        registry = ModelRolloutRegistry(root / "rollout.json")
        rollout = registry.start(
            expected_revision=0,
            baseline_model="stable", candidate_model="candidate",
            baseline_metrics=METRICS, candidate_metrics=METRICS, reviewer="drill",
        )
        degraded = {**METRICS, "error_rate": 0.5}
        rollback = registry.advance(rollout["revision"], degraded, "drill")
        checks.append({"name": "automatic_canary_rollback",
                       "passed": rollback["status"] == "rolled_back"})
        target = root / "target"
        target.mkdir()
        preview = restore_local_backup(_backup(root), target, Path("data/policyguard.db"))
        checks.append({"name": "verified_restore_dry_run", "passed": preview["mode"] == "dry-run"})
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = run_drill()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
