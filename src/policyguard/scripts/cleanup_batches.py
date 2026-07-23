"""Dry-run-first cleanup report for expired batch artifacts."""

import argparse
import json
from pathlib import Path

from policyguard.application.batch_artifacts import BatchArtifactWorkspace
from policyguard.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    expired = BatchArtifactWorkspace(Path(get_settings().upload_dir)).expired(args.days)
    print(json.dumps({
        "status": "dry_run",
        "retention_days": args.days,
        "eligible_count": len(expired),
        "batches": expired,
        "note": "Deletion requires the authenticated two-stage API workflow.",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
