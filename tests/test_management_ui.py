from pathlib import Path

from fastapi.testclient import TestClient

from policyguard.api.main import create_app

ROOT = Path(__file__).parents[1]


def test_management_ui_exposes_review_and_remediation_workbenches() -> None:
    html = (ROOT / "src/policyguard/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "src/policyguard/web/app.js").read_text(encoding="utf-8")
    assert 'id="evaluation-review-list"' in html
    assert 'id="memory-review-list"' in html
    assert 'id="remediation-result"' in html
    assert 'id="perf-p95"' in html
    assert 'id="perf-p99"' in html
    assert 'id="live-p99"' in html
    assert 'id="runtime-window"' in html
    assert 'data-panel="overview"' in html
    assert "/api/v1/review-queue" in script
    assert "/impact" in script
    assert "/structure" in script
    assert "expected_revision" in script
    assert "heading_overrides" in script
    assert "/remediation-plan" in script
    assert "/draft" in script
    assert "dashboard.performance?.concurrency" in script


def test_user_ui_hides_management_and_runtime_controls(tmp_path: Path) -> None:
    html = (ROOT / "src/policyguard/web/user.html").read_text(encoding="utf-8")
    script = (ROOT / "src/policyguard/web/user.js").read_text(encoding="utf-8")

    assert "开始检查" in html
    assert 'id="market-results"' in html
    assert "法规监控" not in html
    assert "审批并激活" not in html
    assert "评测真值" not in html
    assert "当前模型" not in html
    assert "执行轨迹" not in html
    assert "/api/v1/source-updates" not in script
    assert "/api/v1/operations/dashboard" not in script
    assert "/api/v1/review-queue" not in script

    with TestClient(create_app(f"sqlite:///{tmp_path / 'ui.db'}")) as client:
        user_page = client.get("/")
        admin_page = client.get("/admin")

    assert user_page.status_code == 200
    assert "开始检查" in user_page.text
    assert admin_page.status_code == 200
    assert "管理控制台" in admin_page.text
