from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from policyguard.api.main import create_app
from policyguard.application.model_rollout import ModelRolloutRegistry
from policyguard.config import get_settings

BASELINE = {
    "success_rate": 0.95,
    "error_rate": 0.01,
    "p95_latency_ms": 1000,
    "human_rejection_rate": 0.05,
}


def test_rollout_advances_with_revision_and_rolls_back_on_drift(tmp_path) -> None:
    registry = ModelRolloutRegistry(tmp_path / "rollout.json")
    state = registry.start(
        expected_revision=0,
        baseline_model="stable", candidate_model="candidate",
        baseline_metrics=BASELINE, candidate_metrics=BASELINE, reviewer="owner",
    )
    assert state["traffic_percent"] == 5
    advanced = registry.advance(state["revision"], BASELINE, "owner")
    assert advanced["traffic_percent"] == 10
    degraded = {**BASELINE, "error_rate": 0.2}
    rolled_back = registry.advance(advanced["revision"], degraded, "owner")
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["traffic_percent"] == 0


def test_rollout_rejects_bad_candidate_and_stale_revision(tmp_path) -> None:
    registry = ModelRolloutRegistry(tmp_path / "rollout.json")
    with pytest.raises(ValueError, match="rollout_quality_gate_failed"):
        registry.start(
            expected_revision=0,
            baseline_model="stable", candidate_model="bad", baseline_metrics=BASELINE,
            candidate_metrics={**BASELINE, "success_rate": 0.2}, reviewer="owner",
        )
    state = registry.start(
        expected_revision=0,
        baseline_model="stable", candidate_model="candidate",
        baseline_metrics=BASELINE, candidate_metrics=BASELINE, reviewer="owner",
    )
    with pytest.raises(ValueError, match="rollout_revision_conflict"):
        registry.advance(state["revision"] - 1, BASELINE, "owner")

    with pytest.raises(ValueError, match="rollout_revision_conflict"):
        registry.start(
            expected_revision=0,
            baseline_model="stable", candidate_model="next",
            baseline_metrics=BASELINE, candidate_metrics=BASELINE, reviewer="owner",
        )


def test_concurrent_advance_allows_only_one_revision_winner(tmp_path) -> None:
    registry = ModelRolloutRegistry(tmp_path / "rollout.json")
    state = registry.start(
        expected_revision=0,
        baseline_model="stable", candidate_model="candidate",
        baseline_metrics=BASELINE, candidate_metrics=BASELINE, reviewer="owner",
    )

    def advance() -> str:
        try:
            registry.advance(state["revision"], BASELINE, "owner")
            return "advanced"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: advance(), range(2)))
    assert outcomes.count("advanced") == 1
    assert outcomes.count("rollout_revision_conflict") == 1


def test_model_rollout_api_enforces_identity_and_revision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    payload = {
        "expected_revision": 0,
        "baseline_model": "stable",
        "candidate_model": "candidate",
        "baseline_metrics": BASELINE,
        "candidate_metrics": BASELINE,
        "reviewer": "owner",
    }
    app = create_app(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    try:
        with TestClient(app) as client:
            denied = client.post(
                "/api/v1/model-rollouts", json=payload,
                headers={"X-Admin-Key": "secret"},
            )
            headers = {"X-Admin-Key": "secret", "X-Reviewer": "owner"}
            started = client.post("/api/v1/model-rollouts", json=payload, headers=headers)
            stale = client.post("/api/v1/model-rollouts", json=payload, headers=headers)
            current = client.get(
                "/api/v1/model-rollouts/current", headers={"X-Admin-Key": "secret"}
            )
        assert denied.status_code == 401
        assert started.status_code == 200
        assert started.json()["traffic_percent"] == 5
        assert stale.status_code == 409
        assert current.json()["revision"] == started.json()["revision"]
    finally:
        get_settings.cache_clear()
