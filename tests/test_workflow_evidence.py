from pathlib import Path

from policyguard.application.evidence_support import EvidenceDecision
from policyguard.application.knowledge import ingest_source_directory
from policyguard.application.query_rewrite import JsonQueryRewriteCache, RewrittenQuery
from policyguard.application.workflow import ComplianceWorkflowService
from policyguard.domain.workflow import WorkflowStatus
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import (
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyWorkflowRepository,
)

ROOT = Path(__file__).parents[1]


class RejectingVerifier:
    def verify_batch(self, cases: list[dict]):
        return (
            [
                EvidenceDecision(
                    case_id=item["case_id"],
                    supported=False,
                    quote="",
                    reason="specific rule is absent",
                    quote_valid=True,
                )
                for item in cases
            ],
            {"total_tokens": 42},
        )


class FixedRewriter:
    model = "test-rewriter"

    def __init__(self, *, fail: bool = False, drift: bool = False) -> None:
        self.fail = fail
        self.drift = drift
        self.calls = 0

    def rewrite_batch(self, items: list[dict]):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider_unavailable")
        return (
            [
                RewrittenQuery(
                    query_id=item["query_id"],
                    original_query=item["query"],
                    canonical_query=(
                        "advertiser liability fine 1000"
                        if self.drift
                        else "广告 最高级 绝对化用语"
                    ),
                    alternatives=(),
                    legal_terms=(),
                    jurisdiction=item["jurisdiction"],
                )
                for item in items
            ],
            {"total_tokens": 25},
        )


def test_workflow_routes_unsupported_evidence_to_more_evidence(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'workflow.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        knowledge = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(knowledge, ROOT / "data/sources")
        run = ComplianceWorkflowService(
            knowledge,
            SqlAlchemyWorkflowRepository(session),
            evidence_verifier=RejectingVerifier(),
        ).execute(
            product={"title": "国家级产品", "description": ""},
            markets=["CN"],
            category="all",
            channel="all",
            as_of=None,
        )
    assert run.status == WorkflowStatus.NEEDS_MORE_EVIDENCE
    assert run.result_payload["evidence_supported"] is False
    assert run.result_payload["markets"][0]["evidence_support"]["supported"] is False
    assert any(event.step == "evidence_support_llm" for event in run.events)


def test_workflow_uses_cached_rewrite_and_records_metrics(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'rewrite.db').as_posix()}")
    database.initialize()
    rewriter = FixedRewriter()
    cache = JsonQueryRewriteCache(tmp_path / "rewrites.json")
    with database.session_factory() as session:
        knowledge = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(knowledge, ROOT / "data/sources")
        service = ComplianceWorkflowService(
            knowledge,
            SqlAlchemyWorkflowRepository(session),
            query_rewriter=rewriter,
            query_rewrite_cache=cache,
        )
        first = service.execute(
            product={"title": "Can an ad claim best?", "description": ""},
            markets=["CN"], category="all", channel="all", as_of=None,
        )
        second = service.execute(
            product={"title": "Can an ad claim best?", "description": ""},
            markets=["CN"], category="all", channel="all", as_of=None,
        )
    assert rewriter.calls == 1
    first_event = next(event for event in first.events if event.step == "query_rewrite")
    second_event = next(event for event in second.events if event.step == "query_rewrite")
    assert first_event.detail["cache_misses"] == 1
    assert first_event.detail["total_tokens"] == 25
    assert second_event.detail["cache_hits"] == 1
    assert second_event.detail["total_tokens"] == 0


def test_workflow_rejects_drift_and_survives_rewriter_failure(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'fallback.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        knowledge = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(knowledge, ROOT / "data/sources")
        drifted = ComplianceWorkflowService(
            knowledge,
            SqlAlchemyWorkflowRepository(session),
            query_rewriter=FixedRewriter(drift=True),
            query_rewrite_cache=JsonQueryRewriteCache(tmp_path / "drift.json"),
        ).execute(
            product={"title": "Can this claim be used?", "description": ""},
            markets=["CN"], category="all", channel="all", as_of=None,
        )
        failed = ComplianceWorkflowService(
            knowledge,
            SqlAlchemyWorkflowRepository(session),
            query_rewriter=FixedRewriter(fail=True),
            query_rewrite_cache=JsonQueryRewriteCache(tmp_path / "failed.json"),
        ).execute(
            product={"title": "Can this claim be used?", "description": ""},
            markets=["CN"], category="all", channel="all", as_of=None,
        )
    assert any(event.step == "query_rewrite_drift" for event in drifted.events)
    assert any(event.step == "query_rewrite_fallback" for event in failed.events)
    assert failed.status != WorkflowStatus.FAILED
