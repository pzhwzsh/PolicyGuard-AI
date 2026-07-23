import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from policyguard.application.creative_studio import CreativeStudioWorkspace
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


def test_creative_scene_worker_attaches_unapproved_candidate(
    tmp_path: Path, monkeypatch,
) -> None:
    upload_root = tmp_path / "uploads"
    workspace = CreativeStudioWorkspace(upload_root)
    project = workspace.create({
        "external_id": "PRODUCT-1", "product_name": "Bottle", "category": "all",
        "brand": "Demo", "platform": "amazon", "market": "US",
        "verified_facts": [{"name": "Capacity", "value": "500ml",
                            "evidence_reference": "SPEC-1"}],
        "skus": [{"sku_id": "WHITE", "label": "White", "attributes": {}}],
    })
    source = io.BytesIO()
    Image.new("RGB", (600, 600), "white").save(source, format="PNG")
    workspace.add_source_image(
        project["project_id"], content=source.getvalue(), suffix=".png",
        expected_revision=0, reviewer="alice",
    )
    scene = io.BytesIO()
    Image.new("RGB", (2000, 2000), "white").save(scene, format="PNG")

    class FakeProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def edit(self, **kwargs):
            assert kwargs["width"] == 2000
            assert kwargs["source"] == source.getvalue()
            return scene.getvalue(), {"provider_model": self.kwargs["model"]}

    monkeypatch.setattr(job_worker, "ProductSceneEditProvider", FakeProvider)
    settings = SimpleNamespace(
        upload_dir=str(upload_root), image_generation_base_url="http://provider",
        image_generation_api_key="secret", image_generation_model="scene-v1",
        image_generation_timeout_seconds=10,
    )
    result = job_worker.handle_creative_scene_generation(
        settings, {"project_id": project["project_id"], "expected_revision": 1}
    )

    assert result["status"] == "review_required"
    assert result["revision"] == 2
    assert result["assets"][-1]["source_preserved"] == (
        "pending_human_identity_review"
    )
