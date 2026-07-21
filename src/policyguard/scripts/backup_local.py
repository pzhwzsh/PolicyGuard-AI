import argparse
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from policyguard.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/backups")
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
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = (root / args.output / timestamp).resolve()
    destination.mkdir(parents=True)
    database_path = (root / settings.database_url.removeprefix("sqlite:///" )).resolve()
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
    print(destination)


if __name__ == "__main__":
    main()
