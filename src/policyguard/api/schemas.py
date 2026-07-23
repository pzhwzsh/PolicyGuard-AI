from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from policyguard.domain.models import CheckResult
from policyguard.domain.workflow import WorkflowRun


class ProductCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=1000)
    description: str = Field(default="", max_length=20_000)
    category: str = Field(min_length=1, max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)


class FindingResponse(BaseModel):
    rule_code: str
    rule_version: str
    severity: str
    field_name: str
    matched_text: str
    evidence: str
    suggestion: str
    source_url: str | None


class CheckResponse(BaseModel):
    id: str
    external_id: str
    status: str
    risk_level: str | None
    findings: list[FindingResponse]
    created_at: datetime
    baseline: str = "deterministic_rules_v0"

    @classmethod
    def from_domain(cls, result: CheckResult) -> "CheckResponse":
        return cls(
            id=result.id,
            external_id=result.external_id,
            status=result.status,
            risk_level=result.risk_level.value if result.risk_level else None,
            findings=[
                FindingResponse(
                    rule_code=item.rule_code,
                    rule_version=item.rule_version,
                    severity=item.severity.value,
                    field_name=item.field_name,
                    matched_text=item.matched_text,
                    evidence=item.evidence,
                    suggestion=item.suggestion,
                    source_url=item.source_url,
                )
                for item in result.findings
            ],
            created_at=result.created_at,
        )


class HealthResponse(BaseModel):
    status: str
    version: str
    ai_enabled: bool


class SearchHitResponse(BaseModel):
    rank: int
    score: float
    chunk_id: str
    document_title: str
    section_id: str
    heading: str
    text: str
    source_url: str
    jurisdiction: str


class SearchResponse(BaseModel):
    query: str
    retriever: str
    result_count: int
    applied_filters: dict[str, str]
    results: list[SearchHitResponse]


class KnowledgeStatsResponse(BaseModel):
    documents: int
    chunks: int
    retriever: str
    embedding_configured: bool


class RetrieverStatusResponse(BaseModel):
    bm25_available: bool
    dense_available: bool
    dense_provider: str | None
    dense_model: str | None
    rerank_available: bool
    rerank_provider: str | None
    rerank_model: str | None
    query_rewrite_available: bool
    query_rewrite_model: str | None


class MarketCompareRequest(BaseModel):
    claims: list[str] = Field(min_length=1, max_length=10)
    markets: list[str] = Field(min_length=1, max_length=5)
    category: str = Field(default="all", max_length=100)
    channel: str = Field(default="all", max_length=100)
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class MarketEvidenceResponse(BaseModel):
    market: str
    decision: str
    results: list[SearchHitResponse]


class MarketCompareResponse(BaseModel):
    claims: list[str]
    category: str
    channel: str
    as_of: str | None
    evidence_only: bool = True
    markets: list[MarketEvidenceResponse]


class ComplianceWorkflowRequest(BaseModel):
    product: ProductCheckRequest
    markets: list[str] = Field(min_length=1, max_length=5)
    category: str = Field(default="all", max_length=100)
    channel: str = Field(default="all", max_length=100)
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class WorkflowEventResponse(BaseModel):
    sequence: int
    step: str
    status: str
    detail: dict[str, Any]
    created_at: datetime


class ComplianceWorkflowResponse(BaseModel):
    id: str
    status: str
    current_step: str
    input_payload: dict[str, Any]
    result_payload: dict[str, Any]
    events: list[WorkflowEventResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, run: WorkflowRun) -> "ComplianceWorkflowResponse":
        return cls(
            id=run.id,
            status=run.status.value,
            current_step=run.current_step,
            input_payload=run.input_payload,
            result_payload=run.result_payload,
            events=[
                WorkflowEventResponse(
                    sequence=event.sequence,
                    step=event.step,
                    status=event.status,
                    detail=event.detail,
                    created_at=event.created_at,
                )
                for event in run.events
            ],
            created_at=run.created_at,
            updated_at=run.updated_at,
        )


class WorkflowReviewRequest(BaseModel):
    decision_id: str = Field(min_length=1, max_length=100)
    decision: str = Field(pattern="^(accept|reject)$")
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(default="", max_length=2000)


class RemediationPlanRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=100)
    mode: str = Field(default="pipeline", pattern="^(pipeline|agent)$")
    experimental_agent_opt_in: bool = False


class DraftCreationRequest(BaseModel):
    execution_id: str = Field(min_length=1, max_length=100)
    approved_by: str = Field(min_length=1, max_length=100)


class AgentMemoryResponse(BaseModel):
    id: str
    run_id: str
    task_type: str
    jurisdictions: list[str]
    category: str
    channel: str
    summary: str
    outcome: dict[str, Any]
    source_versions: dict[str, str]
    reviewed_by: str
    review_status: str
    invalidated_reason: str | None
    created_at: datetime


class AgentMemoryReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    decision: str = Field(pattern="^(confirm|invalidate)$")
    comment: str = Field(default="", max_length=2000)


class EvaluationReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    decision: str = Field(pattern="^(accept|correct|reject)$")
    expected_section_id: str | None = Field(default=None, max_length=200)
    comment: str = Field(default="", max_length=2000)


class EvaluationReviewResponse(BaseModel):
    dataset: str
    sample_id: str
    query: str
    jurisdiction: str
    answerable: bool
    proposed_section_id: str | None
    review_status: str
    decision: str | None
    reviewed_section_id: str | None
    reviewer: str | None
    comment: str
    reviewed_at: datetime | None


class DocumentParseResponse(BaseModel):
    document_id: str
    filename: str
    parser: str
    page_count: int
    status: str
    activation_status: str = "staged"
    warnings: list[str]
    block_count: int
    chunk_count: int
    markdown_preview: str
    parser_route: str
    mean_block_confidence: float


class DocumentApprovalRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    source_url: str = Field(pattern=r"^https?://", max_length=2000)
    version: str = Field(min_length=1, max_length=200)
    published_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    jurisdiction: str = Field(pattern=r"^[A-Za-z]{2,10}$")
    category: str = Field(default="all", max_length=100)
    channel: str = Field(default="all", max_length=100)
    legal_level: str = Field(default="guidance", max_length=100)
    source_language: str = Field(default="en", max_length=30)
    effective_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reviewer: str = Field(min_length=1, max_length=100)


class DocumentApprovalResponse(BaseModel):
    document_id: str
    activation_status: str
    activated_chunks: int
    reviewer: str


class DocumentCorrectionBlock(BaseModel):
    block_id: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, max_length=100_000)
    markdown: str | None = Field(default=None, max_length=120_000)
    section_path: list[str] | None = Field(default=None, max_length=20)


class DocumentCorrectionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=100)
    corrections: list[DocumentCorrectionBlock] = Field(default_factory=list, max_length=500)
    resolved_warnings: list[str] = Field(default_factory=list, max_length=500)


class DocumentWorkspaceResponse(BaseModel):
    document: dict[str, Any]
    manifest: dict[str, Any]
    correction_rate: float


class BackgroundJobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    result: dict[str, Any]
    attempts: int
    max_attempts: int
    error: str | None


class OperationsDashboardResponse(BaseModel):
    knowledge: dict[str, Any]
    sources: dict[str, Any]
    jobs: dict[str, int]
    performance: dict[str, Any]
    evaluations: dict[str, Any]
    report_history: list[dict[str, Any]]


class SourceUpdateResponse(BaseModel):
    source_id: str
    content_hash: str
    status: str
    section_count: int
    diff_available: bool
    title: str
    preview: list[dict[str, str]]
    reindex_job_id: str | None = None
    eligible_for_activation: bool
    structural_review_status: str
    legal_review_status: str
    short_section_rate: float


class SourceUpdateApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    legal_review_confirmed: bool
