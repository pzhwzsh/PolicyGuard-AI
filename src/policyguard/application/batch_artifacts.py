"""Batch evidence packages and revision-protected artifact deletion."""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class BatchArtifactWorkspace:
    def __init__(self, upload_root: Path) -> None:
        self.upload_root = upload_root.resolve()
        self.batch_root = (self.upload_root / "batches").resolve()
        self.batch_root.mkdir(parents=True, exist_ok=True)

    def _output_dir(self, batch_id: str) -> Path:
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if not batch_id or any(char not in allowed for char in batch_id.lower()):
            raise ValueError("batch_id_invalid")
        path = (self.batch_root / batch_id).resolve()
        if path.parent != self.batch_root:
            raise ValueError("batch_path_invalid")
        return path

    def _manifest_path(self, batch_id: str) -> Path:
        return self._output_dir(batch_id) / "artifact-manifest.json"

    def load_manifest(self, batch_id: str) -> dict[str, Any]:
        path = self._manifest_path(batch_id)
        if not path.is_file():
            return {"batch_id": batch_id, "revision": 0, "status": "active"}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_manifest(self, batch_id: str, manifest: dict[str, Any]) -> None:
        path = self._manifest_path(batch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def package(
        self,
        batch_id: str,
        *,
        input_path: Path,
        workflow_records: list[dict[str, Any]],
    ) -> Path:
        output_dir = self._output_dir(batch_id)
        result_path = output_dir / "review-results.csv"
        if not result_path.is_file():
            raise RuntimeError("batch_result_not_ready")
        package_path = output_dir / "review-package.zip"
        manifest = self.load_manifest(batch_id)
        if not self._manifest_path(batch_id).is_file():
            self._save_manifest(batch_id, manifest)
        metadata = {
            "batch_id": batch_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "artifact_revision": manifest["revision"],
            "input_filename": input_path.name,
            "workflow_count": len(workflow_records),
            "contents": [
                "input", "review-results.csv", "results.jsonl", "workflow-records.json",
                "artifact-manifest.json",
            ],
        }
        candidates = [result_path, output_dir / "results.jsonl", self._manifest_path(batch_id)]
        resolved_input = input_path.resolve()
        if self.upload_root not in resolved_input.parents or not resolved_input.is_file():
            raise ValueError("batch_input_path_invalid")
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "package-metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "workflow-records.json",
                json.dumps(workflow_records, ensure_ascii=False, indent=2),
            )
            archive.write(resolved_input, f"input/{resolved_input.name}")
            for path in candidates:
                if path.is_file():
                    archive.write(path, path.name)
        return package_path

    def stage_deletion(
        self, batch_id: str, *, expected_revision: int, reviewer: str, reason: str
    ) -> dict[str, Any]:
        output_dir = self._output_dir(batch_id)
        if not output_dir.is_dir():
            raise LookupError("batch_artifacts_not_found")
        manifest = self.load_manifest(batch_id)
        if manifest["revision"] != expected_revision:
            raise RuntimeError("batch_artifact_revision_conflict")
        if manifest["status"] == "deleted":
            raise RuntimeError("batch_artifacts_already_deleted")
        manifest.update({
            "revision": expected_revision + 1,
            "status": "pending_deletion",
            "deletion_requested_by": reviewer,
            "deletion_reason": reason,
            "deletion_requested_at": datetime.now(UTC).isoformat(),
        })
        self._save_manifest(batch_id, manifest)
        return manifest

    def confirm_deletion(
        self,
        batch_id: str,
        *,
        expected_revision: int,
        reviewer: str,
        input_path: Path,
    ) -> dict[str, Any]:
        output_dir = self._output_dir(batch_id)
        manifest = self.load_manifest(batch_id)
        if manifest["revision"] != expected_revision:
            raise RuntimeError("batch_artifact_revision_conflict")
        if manifest["status"] != "pending_deletion":
            raise RuntimeError("batch_deletion_not_staged")
        files = [path for path in output_dir.rglob("*") if path.is_file()]
        deleted_bytes = sum(path.stat().st_size for path in files)
        resolved_input = input_path.resolve()
        if self.upload_root not in resolved_input.parents:
            raise ValueError("batch_input_path_invalid")
        tombstone = {
            **manifest,
            "revision": expected_revision + 1,
            "status": "deleted",
            "deleted_by": reviewer,
            "deleted_at": datetime.now(UTC).isoformat(),
            "deleted_file_count": len(files) + int(resolved_input.is_file()),
            "deleted_bytes": deleted_bytes + (
                resolved_input.stat().st_size if resolved_input.is_file() else 0
            ),
        }
        log_dir = self.batch_root / "deletion-log"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{batch_id}.json"
        log_path.write_text(json.dumps(tombstone, ensure_ascii=False, indent=2), encoding="utf-8")
        if resolved_input.is_file():
            resolved_input.unlink()
        shutil.rmtree(output_dir)
        return tombstone

    def expired(self, retention_days: int, *, now: datetime | None = None) -> list[dict[str, Any]]:
        if retention_days < 1:
            raise ValueError("retention_days_invalid")
        cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
        rows = []
        for directory in self.batch_root.iterdir():
            if not directory.is_dir() or directory.name in {"inbox", "deletion-log"}:
                continue
            modified = datetime.fromtimestamp(directory.stat().st_mtime, UTC)
            if modified < cutoff:
                rows.append({"batch_id": directory.name, "modified_at": modified.isoformat()})
        return rows
