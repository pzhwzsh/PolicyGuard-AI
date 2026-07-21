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
