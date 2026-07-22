import json
from datetime import UTC, datetime

from policyguard.application.policy_impact import analyze_policy_impact, section_changes
from policyguard.infrastructure.database import (
    AgentMemoryRecord,
    BackgroundJobRecord,
    Database,
    PolicyChunkRecord,
    PolicyDocumentRecord,
    WorkflowRunRecord,
)


def test_section_changes_classifies_added_removed_and_modified() -> None:
    old = {
        "a": {"section_id": "a", "heading": "A", "text": "old"},
        "b": {"section_id": "b", "heading": "B", "text": "same"},
        "c": {"section_id": "c", "heading": "C", "text": "removed"},
    }
    new = {
        "a": {"section_id": "a", "heading": "A", "text": "new"},
        "b": {"section_id": "b", "heading": "B", "text": "same"},
        "d": {"section_id": "d", "heading": "D", "text": "added"},
    }
    assert [(item["section_id"], item["change_type"]) for item in section_changes(old, new)] == [
        ("a", "modified"), ("c", "removed"), ("d", "added")
    ]


def test_policy_impact_enqueues_idempotent_re_review_jobs(tmp_path) -> None:
    source_url = "https://regulator.example/policy"
    content_hash = "a" * 64
    staged = tmp_path / "staged" / "source-a" / content_hash
    staged.mkdir(parents=True)
    policy_path = staged / "policy.json"
    policy_path.write_text(json.dumps({
        "source_url": source_url,
        "sections": [
            {"section_id": "s1", "heading": "Rule one", "text": "new text"},
            {"section_id": "s2", "heading": "Rule two", "text": "added text"},
        ],
    }), encoding="utf-8")
    (staged / "manifest.json").write_text(json.dumps({
        "source_id": "source-a",
        "content_hash": content_hash,
        "policy_path": str(policy_path),
    }), encoding="utf-8")

    database = Database(f"sqlite:///{(tmp_path / 'impact.db').as_posix()}")
    database.initialize()
    now = datetime.now(UTC)
    with database.session_factory() as session:
        session.add(PolicyDocumentRecord(
            id="old-document",
            title="Old policy",
            source_url=source_url,
            publisher="Regulator",
            version="v1",
            published_at="2025-01-01",
            retrieved_at="2025-01-01",
            content_hash="b" * 64,
            active=True,
            chunks=[PolicyChunkRecord(
                id="chunk-1", ordinal=0, section_id="s1", heading="Rule one",
                text="old text", content_hash="c" * 64,
            )],
        ))
        session.add(WorkflowRunRecord(
            id="run-1", status="review_required", current_step="human_review_route",
            input_payload={"product": {"title": "claim"}},
            result_payload={"markets": [{"candidate_evidence": [{
                "source_url": source_url, "section_id": "s1", "heading": "Rule one",
            }]}]},
            created_at=now, updated_at=now,
        ))
        session.add(AgentMemoryRecord(
            id="memory-1", run_id="run-1", task_type="remediation",
            jurisdictions=["US"], category="all", channel="all", summary="reviewed",
            outcome={}, source_versions={source_url: "v1"}, reviewed_by="reviewer",
            review_status="confirmed", invalidated_reason=None, created_at=now,
        ))
        session.commit()

        first = analyze_policy_impact(session, tmp_path / "staged", "source-a", content_hash)
        second = analyze_policy_impact(session, tmp_path / "staged", "source-a", content_hash)
        jobs = session.query(BackgroundJobRecord).all()

    assert first["changed_section_count"] == 2
    assert first["affected_workflow_count"] == 1
    assert first["affected_memory_ids"] == ["memory-1"]
    assert first["automatic_activation"] is False
    assert second["re_review_job_ids"] == first["re_review_job_ids"]
    assert len(jobs) == 1
    database.engine.dispose()
