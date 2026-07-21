from pathlib import Path

from fastapi.testclient import TestClient

from policyguard.api.main import create_app


def test_evaluation_review_queue_requires_real_decision_and_active_section(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{(tmp_path / 'reviews.db').as_posix()}")
    with TestClient(app) as client:
        queue = client.get("/api/v1/review-queue")
        assert queue.status_code == 200
        assert queue.json()["evaluation"]["pending"] == 22
        assert queue.json()["automatic_approval"] is False

        reviewed = client.post(
            "/api/v1/evaluations/cross-language/reviews/us-pos-01",
            json={"reviewer": "reviewer-alias", "decision": "accept", "comment": "checked"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "human_reviewed"
        assert reviewed.json()["reviewed_section_id"] == "ftc-truth-evidence"

        items = client.get("/api/v1/evaluations/cross-language/reviews").json()
        assert sum(item["review_status"] == "human_reviewed" for item in items) == 1


def test_correction_must_reference_active_section(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{(tmp_path / 'correction.db').as_posix()}")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/evaluations/cross-language/reviews/us-pos-01",
            json={
                "reviewer": "reviewer-alias", "decision": "correct",
                "expected_section_id": "invented-section", "comment": "wrong",
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "evaluation_expected_section_not_active"
