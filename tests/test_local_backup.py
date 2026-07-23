import json
from hashlib import sha256
from pathlib import Path

import pytest

from policyguard.application.local_backup import (
    restore_local_backup,
    verify_local_backup,
)


def create_backup(root: Path) -> Path:
    backup = root / "backup"
    (backup / "uploads").mkdir(parents=True)
    (backup / "update-state").mkdir()
    files = {
        "policyguard.db": b"database",
        "uploads/document.txt": b"upload",
        "update-state/state.json": b"{}",
    }
    manifest_files = []
    for relative, content in files.items():
        path = backup / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        manifest_files.append({
            "path": relative,
            "size": len(content),
            "sha256": sha256(content).hexdigest(),
        })
    (backup / "manifest.json").write_text(json.dumps({
        "created_at": "2026-07-23T00:00:00+00:00",
        "file_count": len(manifest_files),
        "files": manifest_files,
    }), encoding="utf-8")
    return backup


def test_backup_verification_and_dry_run_restore(tmp_path: Path) -> None:
    backup = create_backup(tmp_path)

    verified = verify_local_backup(backup)
    preview = restore_local_backup(
        backup, tmp_path / "target", Path("data/policyguard.db")
    )

    assert verified["status"] == "valid"
    assert verified["file_count"] == 3
    assert preview["mode"] == "dry-run"
    assert not (tmp_path / "target" / "data" / "policyguard.db").exists()


def test_restore_applies_backup_and_preserves_previous_state(tmp_path: Path) -> None:
    backup = create_backup(tmp_path)
    target = tmp_path / "target"
    database = target / "data" / "policyguard.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"previous")

    result = restore_local_backup(
        backup, target, Path("data/policyguard.db"), apply=True
    )

    assert database.read_bytes() == b"database"
    safety = Path(result["safety_copy"])
    assert (safety / "policyguard.db").read_bytes() == b"previous"
    assert (target / "data" / "uploads" / "document.txt").read_bytes() == b"upload"


def test_backup_verification_rejects_corruption_and_traversal(tmp_path: Path) -> None:
    backup = create_backup(tmp_path)
    (backup / "policyguard.db").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="mismatch"):
        verify_local_backup(backup)

    backup = create_backup(tmp_path / "second")
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside"
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="path_invalid"):
        verify_local_backup(backup)
