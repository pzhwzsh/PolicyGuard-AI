import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from policyguard.application.document_ingestion import DocumentBlock, ParsedDocument
from policyguard.application.document_workspace import DocumentWorkspace


def staged_document(root: Path) -> str:
    content_hash = sha256(b"document").hexdigest()
    document_id = content_hash[:24]
    directory = root / document_id
    directory.mkdir(parents=True)
    document = ParsedDocument(
        "scan.pdf", content_hash, "test", 1, "review_required",
        ("page_1:scan_requires_ocr",),
        (DocumentBlock("p1", 1, "paragraph", "wr0ng", "wr0ng", None),),
    )
    (directory / "document.json").write_text(
        json.dumps(document.canonical_json()), encoding="utf-8"
    )
    (directory / "manifest.json").write_text(
        json.dumps({"document_id": document_id, "activation_status": "staged", "revision": 0}),
        encoding="utf-8",
    )
    return document_id


def test_human_correction_is_versioned_and_rebuilds_chunks(tmp_path: Path) -> None:
    document_id = staged_document(tmp_path)
    workspace = DocumentWorkspace(tmp_path)
    corrected, manifest = workspace.correct(
        document_id,
        expected_revision=0,
        reviewer="reviewer",
        corrections=[{"block_id": "p1", "text": "correct"}],
        resolved_warnings=["page_1:scan_requires_ocr"],
    )
    assert corrected.status == "parsed"
    assert corrected.blocks[0].text == "correct"
    assert manifest["revision"] == 1
    assert (tmp_path / document_id / "revisions/revision-0.json").exists()
    chunks = json.loads((tmp_path / document_id / "chunks.json").read_text(encoding="utf-8"))
    assert chunks[0]["text"] == "correct"


def test_human_correction_rejects_stale_revision(tmp_path: Path) -> None:
    document_id = staged_document(tmp_path)
    with pytest.raises(RuntimeError, match="revision_conflict"):
        DocumentWorkspace(tmp_path).correct(
            document_id,
            expected_revision=1,
            reviewer="reviewer",
            corrections=[],
            resolved_warnings=[],
        )


def test_staged_document_deletion_removes_all_content_and_keeps_minimal_audit(
    tmp_path: Path,
) -> None:
    document_id = staged_document(tmp_path)
    directory = tmp_path / document_id
    (directory / "original.pdf").write_bytes(b"private")

    event = DocumentWorkspace(tmp_path).delete_staged(
        document_id,
        expected_revision=0,
        reviewer="reviewer",
        reason="user requested deletion",
    )

    assert not directory.exists()
    assert event["deleted_file_count"] == 3
    audit = (tmp_path / ".deletion-audit/documents.jsonl").read_text(encoding="utf-8")
    assert document_id in audit
    assert "private" not in audit


def test_active_document_cannot_be_deleted(tmp_path: Path) -> None:
    document_id = staged_document(tmp_path)
    manifest_path = tmp_path / document_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["activation_status"] = "active"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="document_is_active"):
        DocumentWorkspace(tmp_path).delete_staged(
            document_id,
            expected_revision=0,
            reviewer="reviewer",
            reason="should fail",
        )


def test_retention_purge_only_deletes_expired_staged_documents(tmp_path: Path) -> None:
    expired_id = staged_document(tmp_path)
    manifest_path = tmp_path / expired_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    deleted = DocumentWorkspace(tmp_path).purge_staged_older_than(30)

    assert [item["document_id"] for item in deleted] == [expired_id]
    assert not (tmp_path / expired_id).exists()
