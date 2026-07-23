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
