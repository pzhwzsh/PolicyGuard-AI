from pathlib import Path
from types import SimpleNamespace

import pytest

from policyguard.scripts import job_worker


def test_cleaned_table_path_is_accepted_by_batch_worker(
    tmp_path: Path, monkeypatch,
) -> None:
    upload_root = tmp_path / "uploads"
    cleaned = upload_root / "tables" / "table-1" / "cleaned-input.csv"
    cleaned.parent.mkdir(parents=True)
    cleaned.write_text(
        "external_id,category,title,description,markets\nSKU-1,all,Title,,CN\n",
        encoding="utf-8",
    )
    observed = {}

    def fake_process(session, input_path: Path, output_dir: Path) -> dict:
        observed.update({"session": session, "input": input_path, "output": output_dir})
        return {"row_count": 1}

    monkeypatch.setattr(job_worker, "process_batch_review", fake_process)
    settings = SimpleNamespace(upload_dir=str(upload_root))

    result = job_worker.handle_batch_review(
        settings,
        "session",
        {"path": str(cleaned), "batch_id": "table-1", "cleaning_revision": 1},
    )

    assert result == {"row_count": 1}
    assert observed["input"] == cleaned.resolve()
    assert observed["output"] == (upload_root / "batches" / "table-1").resolve()


def test_batch_worker_rejects_paths_outside_upload_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("content", encoding="utf-8")
    settings = SimpleNamespace(upload_dir=str(tmp_path / "uploads"))

    with pytest.raises(ValueError, match="batch_path_invalid"):
        job_worker.handle_batch_review(
            settings, None, {"path": str(outside), "batch_id": "outside"}
        )
