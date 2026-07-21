from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_management_ui_exposes_review_and_remediation_workbenches() -> None:
    html = (ROOT / "src/policyguard/web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "src/policyguard/web/app.js").read_text(encoding="utf-8")
    assert 'id="evaluation-review-list"' in html
    assert 'id="memory-review-list"' in html
    assert 'id="remediation-result"' in html
    assert "/api/v1/review-queue" in script
    assert "/remediation-plan" in script
    assert "/draft" in script
