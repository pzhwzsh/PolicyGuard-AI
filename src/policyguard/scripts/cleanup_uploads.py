"""Delete expired staged document workspaces without touching activated knowledge."""

import argparse
import json
from pathlib import Path

from policyguard.application.document_workspace import DocumentWorkspace
from policyguard.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Retention age in days (default: 30)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform deletion. Without this flag the command only reports eligible workspaces.",
    )
    args = parser.parse_args()
    workspace = DocumentWorkspace(Path(get_settings().upload_dir))
    if not args.apply:
        expired = workspace.expired_staged(args.days)
        print(json.dumps({
            "status": "dry_run",
            "retention_days": args.days,
            "eligible_count": len(expired),
            "documents": [item["document_id"] for item in expired],
            "note": "Re-run with --apply to delete expired staged uploads.",
        }))
        return
    deleted = workspace.purge_staged_older_than(args.days)
    print(json.dumps({
        "status": "completed",
        "retention_days": args.days,
        "deleted_count": len(deleted),
        "deleted_bytes": sum(item["deleted_bytes"] for item in deleted),
        "documents": [item["document_id"] for item in deleted],
    }))


if __name__ == "__main__":
    main()
