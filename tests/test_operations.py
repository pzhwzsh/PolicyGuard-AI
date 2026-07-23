from pathlib import Path

from fastapi.testclient import TestClient

from policyguard.api.main import create_app
from policyguard.config import get_settings


def test_operations_dashboard_and_jobs_are_readable(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{(tmp_path / 'ops.db').as_posix()}")
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/operations/dashboard")
        jobs = client.get("/api/v1/jobs")
    assert dashboard.status_code == 200
    assert "knowledge" in dashboard.json()
    assert jobs.status_code == 200


def test_admin_key_protects_management_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    app = create_app(f"sqlite:///{(tmp_path / 'admin.db').as_posix()}")
    try:
        with TestClient(app) as client:
            denied = client.post("/api/v1/source-updates/check", json={})
            allowed = client.post(
                "/api/v1/source-updates/check", json={}, headers={"X-Admin-Key": "secret"}
            )
        assert denied.status_code == 401
        assert allowed.status_code == 202
    finally:
        get_settings.cache_clear()


def test_admin_identity_protects_workflow_review_and_draft(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    app = create_app(f"sqlite:///{(tmp_path / 'review-admin.db').as_posix()}")
    try:
        with TestClient(app) as client:
            missing = client.post(
                "/api/v1/workflows/compliance/missing/review",
                json={"decision_id": "d1", "decision": "accept", "reviewer": "alice"},
            )
            no_identity = client.post(
                "/api/v1/workflows/compliance/missing/review",
                headers={"X-Admin-Key": "secret"},
                json={"decision_id": "d1", "decision": "accept", "reviewer": "alice"},
            )
            mismatch = client.post(
                "/api/v1/workflows/compliance/missing/draft",
                headers={"X-Admin-Key": "secret", "X-Reviewer": "alice"},
                json={"execution_id": "e1", "approved_by": "bob"},
            )
        assert missing.status_code == 401
        assert no_identity.status_code == 401
        assert no_identity.json()["detail"] == "reviewer_identity_required"
        assert mismatch.status_code == 403
        assert mismatch.json()["detail"] == "reviewer_identity_mismatch"
    finally:
        get_settings.cache_clear()


def test_admin_identity_protects_source_structure_corrections(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    app = create_app(f"sqlite:///{(tmp_path / 'source-admin.db').as_posix()}")
    payload = {"reviewer": "alice", "expected_revision": 0, "published_at": "2024-01-01"}
    path = f"/api/v1/source-updates/eu-law/{'a' * 64}/structure"
    try:
        with TestClient(app) as client:
            missing = client.patch(path, json=payload)
            mismatch = client.patch(
                path,
                headers={"X-Admin-Key": "secret", "X-Reviewer": "bob"},
                json=payload,
            )
        assert missing.status_code == 401
        assert mismatch.status_code == 403
        assert mismatch.json()["detail"] == "reviewer_identity_mismatch"
    finally:
        get_settings.cache_clear()


def test_reviewer_role_cannot_run_admin_operation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv("REVIEWER_API_KEY", "review-secret")
    get_settings.cache_clear()
    app = create_app(f"sqlite:///{(tmp_path / 'roles.db').as_posix()}")
    try:
        with TestClient(app) as client:
            denied = client.post(
                "/api/v1/source-updates/check",
                json={},
                headers={"X-Admin-Key": "review-secret", "X-Reviewer": "alice"},
            )
        assert denied.status_code == 401
        assert denied.json()["detail"] == "admin_key_required"
    finally:
        get_settings.cache_clear()


def test_tenant_owned_workflow_is_not_visible_to_other_tenant(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TENANT_KEYS_JSON", '{"tenant-a":"key-a","tenant-b":"key-b"}')
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    app = create_app(f"sqlite:///{(tmp_path / 'tenants.db').as_posix()}")
    payload = {
        "product": {
            "external_id": "sku-1", "title": "claim", "description": "",
            "category": "all", "attributes": {},
        },
        "markets": ["CN"], "category": "all", "channel": "all",
    }
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/workflows/compliance", json=payload,
                headers={"X-Tenant-ID": "tenant-a", "X-Tenant-Key": "key-a"},
            )
            run_id = created.json()["id"]
            hidden = client.get(
                f"/api/v1/workflows/compliance/{run_id}",
                headers={"X-Tenant-ID": "tenant-b", "X-Tenant-Key": "key-b"},
            )
            visible = client.get(
                f"/api/v1/workflows/compliance/{run_id}",
                headers={"X-Tenant-ID": "tenant-a", "X-Tenant-Key": "key-a"},
            )
        assert created.status_code == 201
        assert hidden.status_code == 404
        assert visible.status_code == 200
    finally:
        get_settings.cache_clear()
