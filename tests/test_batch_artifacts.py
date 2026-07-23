import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from policyguard.application.batch_artifacts import BatchArtifactWorkspace


def test_batch_package_and_two_stage_deletion(tmp_path: Path) -> None:
    upload = tmp_path / "uploads"
    workspace = BatchArtifactWorkspace(upload)
    inbox = upload / "batches" / "inbox"
    output = upload / "batches" / "batch-1"
    inbox.mkdir(parents=True)
    output.mkdir(parents=True)
    input_path = inbox / "input.csv"
    input_path.write_text("external_id,title\n1,test\n", encoding="utf-8")
    (output / "review-results.csv").write_text("workflow_id\nw1\n", encoding="utf-8")
    (output / "results.jsonl").write_text(
        json.dumps({"workflow_id": "w1"}) + "\n", encoding="utf-8"
    )

    package = workspace.package(
        "batch-1", input_path=input_path, workflow_records=[{"workflow_id": "w1"}]
    )
    with zipfile.ZipFile(package) as archive:
        assert "workflow-records.json" in archive.namelist()
        assert "input/input.csv" in archive.namelist()

    staged = workspace.stage_deletion(
        "batch-1", expected_revision=0, reviewer="alice", reason="retention expired"
    )
    assert staged["status"] == "pending_deletion"
    deleted = workspace.confirm_deletion(
        "batch-1", expected_revision=1, reviewer="alice", input_path=input_path
    )
    assert deleted["status"] == "deleted"
    assert not output.exists()
    assert not input_path.exists()
    assert (upload / "batches/deletion-log/batch-1.json").is_file()


def test_batch_retention_is_dry_run_inventory(tmp_path: Path) -> None:
    workspace = BatchArtifactWorkspace(tmp_path / "uploads")
    output = tmp_path / "uploads/batches/old-batch"
    output.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    import os

    os.utime(output, (old, old))
    assert workspace.expired(30)[0]["batch_id"] == "old-batch"

