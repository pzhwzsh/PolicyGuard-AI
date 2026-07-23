"""Verify a local backup and optionally restore it with a pre-restore safety copy."""

import argparse
import json
from pathlib import Path

from policyguard.application.local_backup import restore_local_backup
from policyguard.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup")
    parser.add_argument("--target-root", default=".")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.target_root).resolve()
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("Use the database-native restore tool for non-SQLite databases.")
    configured = Path(settings.database_url.removeprefix("sqlite:///"))
    database = configured.resolve() if configured.is_absolute() else (root / configured).resolve()
    try:
        database_relative = database.relative_to(root)
    except ValueError as exc:
        raise SystemExit("Configured SQLite database is outside target root.") from exc
    result = restore_local_backup(
        Path(args.backup), root, database_relative, apply=args.apply
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
