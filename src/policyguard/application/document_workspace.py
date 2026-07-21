"""Versioned human correction workspace for staged parsed documents."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
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


def document_workspace_payload(document: ParsedDocument, manifest: dict) -> dict:
    return {
        "document": document.canonical_json(),
        "manifest": manifest,
        "correction_rate": round(
            len(manifest.get("corrected_block_ids", [])) / max(len(document.blocks), 1), 4
        ),
    }
