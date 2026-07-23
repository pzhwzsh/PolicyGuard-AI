import io
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from policyguard.api.main import create_app
from policyguard.application import creative_image_provider
from policyguard.application.creative_image_provider import ProductSceneEditProvider
from policyguard.application.creative_studio import (
    CreativeStudioWorkspace,
    generate_grounded_ad_copy,
    validate_ad_copy,
    validate_creative_payload,
)
from policyguard.application.tools import GenerateGroundedAdCopyTool


def payload() -> dict:
    return {
        "external_id": "PRODUCT-1",
        "product_name": "Light Bottle",
        "category": "all",
        "brand": "Demo",
        "platform": "amazon",
        "market": "US",
        "verified_facts": [
            {"name": "Capacity", "value": "500ml", "evidence_reference": "SPEC-1"}
        ],
        "skus": [
            {"sku_id": "WHITE-500", "label": "White 500ml", "attributes": {"size": "500ml"}}
        ],
        "copy_count": 3,
    }


def image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (600, 800), "#dfe8e2").save(buffer, format="PNG")
    return buffer.getvalue()


def sized_image_bytes(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "#dfe8e2").save(buffer, format="PNG")
    return buffer.getvalue()


def test_scene_provider_validates_output_and_reports_attempts(monkeypatch) -> None:
    encoded = __import__("base64").b64encode(image_bytes()).decode("ascii")
    response = httpx.Response(200, json={"image_base64": encoded, "seed": "42"})
    response.extensions["policyguard_attempts"] = 2
    observed = {}

    def fake_post(url, **kwargs):
        observed.update({"url": url, **kwargs})
        return response

    monkeypatch.setattr(creative_image_provider, "post_with_retry", fake_post)
    content, metadata = ProductSceneEditProvider(
        "http://image-provider", "secret", "scene-edit-v1"
    ).edit(
        source=image_bytes(), prompt="clean scene", negative_prompt="text",
        width=1200, height=1200,
    )

    assert content == image_bytes()
    assert metadata == {
        "provider_model": "scene-edit-v1",
        "provider_attempts": 2,
        "provider_seed": "42",
    }
    assert observed["url"] == "http://image-provider/product-scene-edit"
    assert observed["json"]["preserve_product_identity"] is True
    assert observed["headers"] == {"Authorization": "Bearer secret"}


def test_scene_provider_rejects_malformed_image(monkeypatch) -> None:
    response = httpx.Response(200, json={"image_base64": "not-base64"})
    monkeypatch.setattr(
        creative_image_provider, "post_with_retry", lambda *args, **kwargs: response
    )

    with pytest.raises(ValueError, match="creative_provider_image_invalid"):
        ProductSceneEditProvider("http://provider", "", "model").edit(
            source=image_bytes(), prompt="scene", negative_prompt="text",
            width=1200, height=1200,
        )


def test_grounded_copy_blocks_unverified_numbers_and_sensitive_facts() -> None:
    item = payload()
    assert validate_ad_copy("Light Bottle 500ml", item) == []
    assert validate_ad_copy("Light Bottle 750ml", item) == ["unverified_number:750"]
    candidates = generate_grounded_ad_copy(item, 3)
    assert all(row["status"] == "passed" for row in candidates)
    assert GenerateGroundedAdCopyTool().execute(
        {"creative_payload": item, "count": 2}
    ).output["external_side_effect"] is False

    item["verified_facts"] = [
        {"name": "官方认证", "value": "已认证", "evidence_reference": None}
    ]
    with pytest.raises(ValueError, match="creative_sensitive_fact_requires_evidence"):
        validate_creative_payload(item)


def test_creative_workspace_renders_reviews_and_packages_assets(tmp_path: Path) -> None:
    workspace = CreativeStudioWorkspace(tmp_path / "uploads")
    created = workspace.create(payload())
    rendered = workspace.add_source_image(
        created["project_id"], content=image_bytes(), suffix=".png",
        expected_revision=0, reviewer="alice",
    )
    assert rendered["status"] == "review_required"
    assert {item["type"] for item in rendered["assets"]} == {"main_image", "sku_card"}
    reviewed = workspace.review(
        created["project_id"], expected_revision=1, reviewer="alice",
        decision="approve", approved_copy_indexes=[0], comment="checked",
    )
    assert reviewed["status"] == "approved"
    package = workspace.package(created["project_id"])
    with zipfile.ZipFile(package) as archive:
        assert "approved-copy.json" in archive.namelist()
        assert any(name.startswith("images/") for name in archive.namelist())


def test_scene_candidate_requires_platform_dimensions(tmp_path: Path) -> None:
    workspace = CreativeStudioWorkspace(tmp_path / "uploads")
    created = workspace.create(payload())
    rendered = workspace.add_source_image(
        created["project_id"], content=image_bytes(), suffix=".png",
        expected_revision=0, reviewer="alice",
    )
    with pytest.raises(ValueError, match="creative_scene_image_dimensions_invalid"):
        workspace.add_scene_candidate(
            created["project_id"], content=image_bytes(), expected_revision=1,
            provider_metadata={"provider_model": "test"},
        )
    generated = workspace.add_scene_candidate(
        created["project_id"], content=sized_image_bytes((2000, 2000)),
        expected_revision=rendered["revision"],
        provider_metadata={"provider_model": "test"},
    )
    assert generated["scene_request"]["status"] == (
        "generated_pending_identity_review"
    )


def test_creative_api_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{(tmp_path / 'creative.db').as_posix()}")
    request = payload()
    with TestClient(app) as client:
        created = client.post("/api/v1/creatives", json=request)
        assert created.status_code == 201
        project = created.json()
        uploaded = client.post(
            f"/api/v1/creatives/{project['project_id']}/source-image",
            params={"expected_revision": 0, "reviewer": "alice"},
            files={"file": ("product.png", image_bytes(), "image/png")},
        )
        assert uploaded.status_code == 200
        reviewed = client.post(
            f"/api/v1/creatives/{project['project_id']}/review",
            json={
                "expected_revision": 1, "reviewer": "alice", "decision": "approve",
                "approved_copy_indexes": [0], "comment": "checked",
            },
        )
        package = client.get(f"/api/v1/creatives/{project['project_id']}/package")
    assert reviewed.status_code == 200
    assert package.status_code == 200
    assert package.headers["content-type"] == "application/zip"
