from datetime import UTC, datetime
from pathlib import Path

from policyguard.application.agent_context import AgentContextBuilder
from policyguard.domain.agent_memory import AgentMemory, MemoryQuery
from policyguard.domain.workflow import WorkflowRun, WorkflowStatus
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyAgentMemoryRepository


def _memory(run_id: str, jurisdiction: str = "US") -> AgentMemory:
    return AgentMemory(
        id=f"memory-{run_id}", run_id=run_id, task_type="remediation",
        jurisdictions=(jurisdiction,), category="beauty", channel="all",
        summary="Reviewed truthful advertising remediation",
        outcome={"operations": [{"operation": "replace_field"}]},
        source_versions={"https://example.test/rule": "v1"},
        reviewed_by="human-reviewer", created_at=datetime.now(UTC),
    )


def test_memory_is_reviewed_filtered_and_invalidated(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{(tmp_path / 'memory.db').as_posix()}")
    db.initialize()
    with db.session_factory() as session:
        repository = SqlAlchemyAgentMemoryRepository(session)
        repository.save_confirmed(_memory("run-us"))
        assert repository.recall(MemoryQuery(
            task_type="remediation", jurisdictions=("US",), category="beauty",
            channel="all", query_text="truthful", limit=3,
        ))[0].run_id == "run-us"
        assert repository.recall(MemoryQuery(
            task_type="remediation", jurisdictions=("CN",), category="beauty",
            channel="all", limit=3,
        )) == []
        assert repository.invalidate_for_source("https://example.test/rule", "v2") == 1
        assert repository.recall(MemoryQuery(
            task_type="remediation", jurisdictions=("US",), category="beauty",
            channel="all", limit=3,
        )) == []


def test_context_builder_exposes_memory_provenance_and_budget(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{(tmp_path / 'context.db').as_posix()}")
    db.initialize()
    with db.session_factory() as session:
        repository = SqlAlchemyAgentMemoryRepository(session)
        repository.save_confirmed(_memory("run-source"))
        run = WorkflowRun(
            id="run-current", status=WorkflowStatus.REVIEW_ACCEPTED,
            current_step="review_completed",
            input_payload={
                "product": {"title": "truthful cream", "description": "claim"},
                "markets": ["US"], "category": "beauty", "channel": "all",
            },
            result_payload={"markets": [{"market": "US", "candidate_evidence": []}]},
        )
        context = AgentContextBuilder(repository).build_remediation_context(run)
        assert context["reviewed_case_memory"][0]["source_run_id"] == "run-source"
        assert context["context_metadata"]["recalled_memory_ids"] == ["memory-run-source"]
        assert context["context_metadata"]["estimated_tokens"] <= 6000
        assert "higher authority" in context["instruction"]


def test_human_memory_review_can_invalidate_and_requires_live_sources(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{(tmp_path / 'review.db').as_posix()}")
    db.initialize()
    with db.session_factory() as session:
        repository = SqlAlchemyAgentMemoryRepository(session)
        memory = _memory("run-review")
        repository.save_confirmed(memory)
        invalidated = repository.review(memory.id, "reviewer-2", "invalidate", "bad case")
        assert invalidated.review_status == "invalidated"
        assert invalidated.invalidated_reason == "human_review:bad case"
        assert repository.list_recent()[0].id == memory.id
        try:
            repository.review(memory.id, "reviewer-3", "confirm", "checked")
        except RuntimeError as exc:
            assert str(exc) == "agent_memory_source_unavailable"
        else:
            raise AssertionError("missing active source must block memory confirmation")
