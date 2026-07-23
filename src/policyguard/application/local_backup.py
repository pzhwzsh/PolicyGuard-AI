"""Verification and recoverable restore for local PolicyGuard backups."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("backup_manifest_path_invalid")
    return path


def verify_local_backup(backup_dir: Path) -> dict[str, Any]:
    root = backup_dir.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("backup_manifest_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, list) or manifest.get("file_count") != len(files):
        raise ValueError("backup_manifest_invalid")
    checked = 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("backup_manifest_invalid")
        relative = _safe_relative(str(item.get("path", "")))
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("backup_file_missing")
        if path.stat().st_size != item.get("size"):
            raise ValueError("backup_file_size_mismatch")
        if sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
            raise ValueError("backup_file_hash_mismatch")
        checked += 1
    return {"status": "valid", "file_count": checked, "created_at": manifest.get("created_at")}


def restore_local_backup(
    backup_dir: Path,
    target_root: Path,
    database_relative_path: Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    verification = verify_local_backup(backup_dir)
    root = target_root.resolve()
    database_relative = _safe_relative(str(database_relative_path))
    targets = {
        "policyguard.db": (root / database_relative).resolve(),
        "uploads": (root / "data" / "uploads").resolve(),
        "update-state": (root / "data" / "update-state").resolve(),
    }
    if any(root not in target.parents for target in targets.values()):
        raise ValueError("restore_target_outside_root")
    available = {
        name: target for name, target in targets.items()
        if (backup_dir.resolve() / name).exists()
    }
    result = {
        **verification,
        "mode": "apply" if apply else "dry-run",
        "targets": {name: str(path) for name, path in available.items()},
    }
    if not apply:
        return result

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safety = root / "data" / "restore-safety" / timestamp
    safety.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for name, target in available.items():
            source = backup_dir.resolve() / name
            if target.exists():
                previous = safety / name
                previous.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(previous))
                moved.append((previous, target))
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            created.append(target)
    except Exception:
        for target in reversed(created):
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        for previous, target in reversed(moved):
            shutil.move(str(previous), str(target))
        raise
    result["safety_copy"] = str(safety)
    return result
