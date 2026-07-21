from pathlib import Path

from fastapi.testclient import TestClient

from policyguard.api.main import create_app


def test_health_and_check_round_trip(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    app = create_app(database_url)

    with TestClient(app) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "PolicyGuard AI" in dashboard.text

        health = client.get("/health")
        assert health.status_code == 200
        assert health.headers["x-trace-id"]
        assert health.headers["server-timing"].startswith("app;dur=")
        assert health.json()["ai_enabled"] is False

        created = client.post(
            "/api/v1/checks",
            json={
                "external_id": "SKU-001",
                "title": "国家级护肤品，100%安全",
                "description": "演示商品",
                "category": "beauty",
                "attributes": {"brand": "Demo"},
            },
        )
        assert created.status_code == 201
        result = created.json()
        assert result["status"] == "review_required"
        assert result["risk_level"] == "high"
        assert len(result["findings"]) == 2
        assert result["baseline"] == "deterministic_rules_v0"

        fetched = client.get(f"/api/v1/checks/{result['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == result["id"]

        stats = client.get("/api/v1/knowledge/stats")
        assert stats.status_code == 200
        assert stats.json()["documents"] == 3
        assert stats.json()["chunks"] == 13

        search = client.get(
            "/api/v1/knowledge/search",
            params={"q": "国家级广告是否允许", "top_k": 3, "market": "CN"},
        )
        assert search.status_code == 200
        assert search.json()["results"][0]["section_id"] == "article-9"

        compare = client.post(
            "/api/v1/knowledge/compare",
            json={
                "claims": ["truthful and evidence-based advertising claims"],
                "markets": ["CN", "US", "EU"],
                "category": "all",
                "channel": "all",
            },
        )
        assert compare.status_code == 200
        assert compare.json()["evidence_only"] is True
        assert [item["market"] for item in compare.json()["markets"]] == ["CN", "US", "EU"]

        retrievers = client.get("/api/v1/knowledge/retrievers")
        assert retrievers.status_code == 200
        assert retrievers.json()["bm25_available"] is True
        assert retrievers.json()["dense_available"] is False
        assert retrievers.json()["rerank_available"] is False

        dense = client.get(
            "/api/v1/knowledge/search",
            params={"q": "国家级广告", "market": "CN", "mode": "dense"},
        )
        assert dense.status_code == 503
        assert dense.json()["detail"] == "embedding_not_configured"

        workflow = client.post(
            "/api/v1/workflows/compliance",
            json={
                "product": {
                    "external_id": "SKU-WF-001",
                    "title": "国家级护肤品",
                    "description": "跨市场证据工作流演示",
                    "category": "beauty",
                    "attributes": {},
                },
                "markets": ["CN", "US", "EU"],
                "category": "all",
                "channel": "all",
            },
        )
        assert workflow.status_code == 201
        workflow_result = workflow.json()
        assert workflow_result["status"] == "review_required"
        assert workflow_result["current_step"] == "human_review_route"
        assert [event["step"] for event in workflow_result["events"]] == [
            "validate_input",
            "claim_extraction_baseline",
            "claim_normalization",
            "collect_evidence",
            "collect_evidence",
            "collect_evidence",
            "human_review_route",
        ]

        workflow_get = client.get(
            f"/api/v1/workflows/compliance/{workflow_result['id']}"
        )
        assert workflow_get.status_code == 200
        assert workflow_get.json()["id"] == workflow_result["id"]

        review_payload = {
            "decision_id": "review-001",
            "decision": "accept",
            "reviewer": "demo-reviewer",
            "comment": "Evidence reviewed; proceed to the next controlled stage.",
        }
        reviewed = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/review",
            json=review_payload,
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "review_accepted"
        event_count = len(reviewed.json()["events"])

        repeated = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/review",
            json=review_payload,
        )
        assert repeated.status_code == 200
        assert len(repeated.json()["events"]) == event_count

        conflict = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/review",
            json={
                "decision_id": "review-002",
                "decision": "reject",
                "reviewer": "another-reviewer",
                "comment": "Conflicting terminal decision",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "workflow_review_conflict"

        plan_payload = {"plan_id": "plan-001"}
        planned = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/remediation-plan",
            json=plan_payload,
        )
        assert planned.status_code == 200
        planned_result = planned.json()
        assert planned_result["status"] == "remediation_planned"
        plan = planned_result["result_payload"]["remediation_plan"]
        assert plan["external_side_effect"] is False
        assert plan["operations"][0]["field"] == "title"
        assert "国家级" not in plan["operations"][0]["after"]

        planned_repeat = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/remediation-plan",
            json=plan_payload,
        )
        assert planned_repeat.status_code == 200
        assert len(planned_repeat.json()["events"]) == len(planned_result["events"])

        draft_payload = {"execution_id": "draft-001", "approved_by": "demo-reviewer"}
        draft = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/draft",
            json=draft_payload,
        )
        assert draft.status_code == 200
        draft_result = draft.json()
        assert draft_result["status"] == "draft_ready"
        assert draft_result["result_payload"]["draft"]["external_side_effect"] is False
        assert "国家级" not in draft_result["result_payload"]["draft"]["product"]["title"]
        assert draft_result["input_payload"]["product"]["title"] == "国家级护肤品"

        draft_conflict = client.post(
            f"/api/v1/workflows/compliance/{workflow_result['id']}/draft",
            json={"execution_id": "draft-002", "approved_by": "other-reviewer"},
        )
        assert draft_conflict.status_code == 409
        assert draft_conflict.json()["detail"] == "remediation_execution_conflict"


def test_rejects_unknown_fields(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    app = create_app(database_url)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/checks",
            json={
                "external_id": "SKU-002",
                "title": "普通商品",
                "description": "",
                "category": "demo",
                "unknown": True,
            },
        )
    assert response.status_code == 422
