import argparse
import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from policyguard.application.local_backup import verify_local_backup
from policyguard.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/backups")
    parser.add_argument("--keep", type=int, default=0)
    return parser.parse_args()


def copy_tree_if_exists(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def main() -> None:
    args = parse_args()
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("Use the database-native backup tool for non-SQLite databases.")
    root = Path(__file__).parents[3]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = (root / args.output / timestamp).resolve()
    destination.mkdir(parents=True)
    database_path = (root / settings.database_url.removeprefix("sqlite:///")).resolve()
    if not database_path.is_file():
        raise SystemExit("Configured SQLite database does not exist.")
    backup_path = destination / "policyguard.db"
    with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    copy_tree_if_exists(root / "data/uploads", destination / "uploads")
    copy_tree_if_exists(root / "data/update-state", destination / "update-state")
    files = [path for path in destination.rglob("*") if path.is_file()]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "file_count": len(files),
        "files": [
            {
                "path": str(path.relative_to(destination)),
                "size": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in files
        ],
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    verify_local_backup(destination)
    if args.keep > 0:
        backups = sorted(
            (
                path for path in destination.parent.iterdir()
                if path.is_dir()
                and re.fullmatch(r"\d{8}T\d{6}(?:\d{6})?Z", path.name)
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        retained = {destination.resolve()}
        for path in backups:
            if len(retained) >= args.keep:
                break
            retained.add(path.resolve())
        for expired in (path for path in backups if path.resolve() not in retained):
            if destination.parent.resolve() not in expired.resolve().parents:
                raise RuntimeError("backup_retention_path_invalid")
            shutil.rmtree(expired)
    print(destination)


if __name__ == "__main__":
    main()
