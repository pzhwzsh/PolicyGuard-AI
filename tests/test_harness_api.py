from pathlib import Path

from fastapi.testclient import TestClient

from policyguard.api.main import create_app


def test_harness_api_streams_events_and_resumes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{tmp_path / 'harness.db'}")
    with TestClient(app) as client:
        created = client.post("/api/v1/harness/runs", json={
            "objective": "Inspect context",
            "context": {"content": "safe"},
            "plan": [
                {"type": "tool", "tool": "inspect_context", "arguments": {}},
                {"type": "finish", "result": {"ok": True}},
            ],
        })
        assert created.status_code == 201
        run = created.json()
        completed = client.post(
            f"/api/v1/harness/runs/{run['run_id']}/run",
            json={"expected_revision": run["revision"]},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        events = client.get(f"/api/v1/harness/runs/{run['run_id']}/events")
        assert events.status_code == 200
        assert events.headers["content-type"].startswith("text/event-stream")
        assert "event: tool.completed" in events.text
        assert "event: run.completed" in events.text


def test_harness_skill_permission_memory_multi_agent_and_evaluation(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{tmp_path / 'harness-2.db'}")
    with TestClient(app) as client:
        skills = client.get("/api/v1/harness/skills")
        assert skills.status_code == 200
        assert {item["name"] for item in skills.json()["skills"]} >= {
            "compliance_review", "sandbox_analysis"
        }
        created = client.post("/api/v1/harness/runs", json={
            "objective": "Request sandbox boundary", "skill": "sandbox_analysis"
        }).json()
        paused = client.post(
            f"/api/v1/harness/runs/{created['run_id']}/advance",
            json={"expected_revision": 0},
        )
        assert paused.json()["pending_permission"] == "sandbox:execute"
        approved = client.post(
            f"/api/v1/harness/runs/{created['run_id']}/permissions",
            json={
                "expected_revision": paused.json()["revision"],
                "permission": "sandbox:execute", "reviewer": "alice",
            },
        )
        assert approved.status_code == 200
        memory = client.post(
            f"/api/v1/harness/runs/{created['run_id']}/memory",
            json={
                "expected_revision": approved.json()["revision"],
                "tier": "long_term", "key": "reviewed-fact",
                "value": {"fact": "verified"}, "reviewed": True,
            },
        )
        assert memory.status_code == 200
        multi = client.post("/api/v1/harness/multi-agent", json={"nodes": [
            {"agent_id": "research", "role": "researcher", "task": "Find evidence"},
            {
                "agent_id": "verify", "role": "verifier", "task": "Verify evidence",
                "depends_on": ["research"],
            },
        ]})
        assert multi.status_code == 200
        assert multi.json()["usage"]["agents"] == 2
        evaluation = client.post("/api/v1/harness/evaluations/run")
        assert evaluation.status_code == 200
        assert evaluation.json()["metrics"]["case_count"] == 2
