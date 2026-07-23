"""Versioned human correction workspace for staged parsed documents."""

import json
import re
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from policyguard.application.document_ingestion import (
    ParsedDocument,
    parsed_document_from_json,
    rag_chunks,
)


class DocumentWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def directory(self, document_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{24}", document_id):
            raise LookupError("document_not_found")
        return self.root / document_id

    def load(self, document_id: str) -> tuple[ParsedDocument, dict[str, Any]]:
        directory = self.directory(document_id)
        document_path = directory / "document.json"
        manifest_path = directory / "manifest.json"
        if not document_path.is_file() or not manifest_path.is_file():
            raise LookupError("document_not_found")
        return (
            parsed_document_from_json(json.loads(document_path.read_text(encoding="utf-8"))),
            json.loads(manifest_path.read_text(encoding="utf-8")),
        )

    def correct(
        self,
        document_id: str,
        *,
        expected_revision: int,
        reviewer: str,
        corrections: list[dict[str, Any]],
        resolved_warnings: list[str],
    ) -> tuple[ParsedDocument, dict[str, Any]]:
        document, manifest = self.load(document_id)
        revision = int(manifest.get("revision", 0))
        if revision != expected_revision:
            raise RuntimeError("document_revision_conflict")
        by_id = {block.block_id: block for block in document.blocks}
        touched: list[str] = []
        for correction in corrections:
            block_id = str(correction.get("block_id", ""))
            if block_id not in by_id:
                raise ValueError("document_block_not_found")
            current = by_id[block_id]
            text = str(correction.get("text", current.text)).strip()
            if len(text) > 100_000:
                raise ValueError("document_block_too_large")
            section_path = tuple(
                str(value)[:500] for value in correction.get("section_path", current.section_path)
            )
            by_id[block_id] = replace(
                current,
                text=text,
                markdown=str(correction.get("markdown", text)).strip(),
                section_path=section_path,
                confidence=1.0,
                metadata={**current.metadata, "human_corrected": True},
            )
            touched.append(block_id)
        unknown_warnings = set(resolved_warnings) - set(document.warnings)
        if unknown_warnings:
            raise ValueError("document_unknown_warning")
        warnings = tuple(item for item in document.warnings if item not in resolved_warnings)
        corrected = replace(
            document,
            status="parsed" if not warnings else "review_required",
            warnings=warnings,
            blocks=tuple(by_id[block.block_id] for block in document.blocks),
        )
        directory = self.directory(document_id)
        revisions = directory / "revisions"
        revisions.mkdir(parents=True, exist_ok=True)
        (revisions / f"revision-{revision}.json").write_text(
            json.dumps(document.canonical_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "document.json").write_text(
            json.dumps(corrected.canonical_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "document.md").write_text(corrected.markdown(), encoding="utf-8")
        (directory / "chunks.json").write_text(
            json.dumps(rag_chunks(corrected), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest.update(
            {
                "revision": revision + 1,
                "last_corrected_at": datetime.now(UTC).isoformat(),
                "last_corrected_by": reviewer,
                "corrected_block_ids": sorted(
                    set(manifest.get("corrected_block_ids", [])) | set(touched)
                ),
                "activation_status": "staged",
            }
        )
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with (directory / "corrections.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "revision": revision + 1,
                "reviewer": reviewer,
                "corrected_at": manifest["last_corrected_at"],
                "blocks": touched,
                "resolved_warnings": resolved_warnings,
            }, ensure_ascii=False) + "\n")
        return corrected, manifest

    def delete_staged(
        self,
        document_id: str,
        *,
        expected_revision: int,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any]:
        """Delete a staged upload and all derived files while retaining a minimal audit event."""
        _, manifest = self.load(document_id)
        if manifest.get("activation_status", "staged") != "staged":
            raise RuntimeError("document_is_active")
        revision = int(manifest.get("revision", 0))
        if revision != expected_revision:
            raise RuntimeError("document_revision_conflict")
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise ValueError("document_deletion_reason_required")
        directory = self.directory(document_id)
        files = [path for path in directory.rglob("*") if path.is_file()]
        deleted_bytes = sum(path.stat().st_size for path in files)
        event = {
            "document_id": document_id,
            "revision": revision,
            "reviewer": reviewer,
            "reason": cleaned_reason,
            "deleted_at": datetime.now(UTC).isoformat(),
            "deleted_file_count": len(files),
            "deleted_bytes": deleted_bytes,
        }
        shutil.rmtree(directory)
        audit_root = self.root / ".deletion-audit"
        audit_root.mkdir(parents=True, exist_ok=True)
        with (audit_root / "documents.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def expired_staged(self, days: int) -> list[dict[str, Any]]:
        if days < 1:
            raise ValueError("retention_days_out_of_range")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        expired: list[dict[str, Any]] = []
        if not self.root.exists():
            return expired
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or not re.fullmatch(r"[a-f0-9]{24}", directory.name):
                continue
            manifest_path = directory / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("activation_status", "staged") != "staged":
                continue
            try:
                created_at = datetime.fromisoformat(str(manifest["created_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at > cutoff:
                continue
            expired.append({
                "document_id": directory.name,
                "revision": int(manifest.get("revision", 0)),
                "created_at": created_at.isoformat(),
            })
        return expired

    def purge_staged_older_than(self, days: int) -> list[dict[str, Any]]:
        deleted = []
        for item in self.expired_staged(days):
            deleted.append(self.delete_staged(
                item["document_id"],
                expected_revision=item["revision"],
                reviewer="retention-policy",
                reason=f"staged upload exceeded {days}-day retention period",
            ))
        return deleted


def document_workspace_payload(document: ParsedDocument, manifest: dict) -> dict:
    return {
        "document": document.canonical_json(),
        "manifest": manifest,
        "correction_rate": round(
            len(manifest.get("corrected_block_ids", [])) / max(len(document.blocks), 1), 4
        ),
    }
