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
    dense_fallback_model: str | None
    rerank_available: bool
    rerank_provider: str | None
    rerank_model: str | None
    query_rewrite_available: bool
    query_rewrite_model: str | None
    llm_fallback_model: str | None
    provider_max_attempts: int
    embedding_initialization_failures: list[dict[str, str]]


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


class DocumentDeletionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class DocumentDeletionResponse(BaseModel):
    document_id: str
    revision: int
    reviewer: str
    reason: str
    deleted_at: str
    deleted_file_count: int
    deleted_bytes: int


class BackgroundJobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    result: dict[str, Any]
    attempts: int
    max_attempts: int
    error: str | None


class TableCleaningPreviewResponse(BaseModel):
    table_id: str
    revision: int
    status: str
    summary: dict[str, int]
    sheets: list[dict[str, Any]]
    skipped_sheets: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]]
    issues: list[dict[str, Any]]


class TableCleaningConfirmationRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=100)
    field_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    allow_partial: bool = False


class TableCleaningConfirmationResponse(BaseModel):
    table_id: str
    revision: int
    status: str
    summary: dict[str, int]
    report_url: str
    job: BackgroundJobResponse


class OperationsDashboardResponse(BaseModel):
    knowledge: dict[str, Any]
    sources: dict[str, Any]
    jobs: dict[str, int]
    performance: dict[str, Any]
    evaluations: dict[str, Any]
    report_history: list[dict[str, Any]]


class ModelEvaluationRequest(BaseModel):
    dataset: str = Field(default="data/evaluation/rag-hard-v1.json", max_length=300)
    candidates: list[str] = Field(min_length=2, max_length=5)
    top_k: int = Field(default=5, ge=1, le=20)
    min_hit_rate_at_k: float = Field(default=0.8, ge=0, le=1)
    min_mrr: float = Field(default=0.65, ge=0, le=1)
    max_latency_ms: float = Field(default=60_000, gt=0, le=3_600_000)


class ModelPromotionRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    expected_dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str = Field(min_length=1, max_length=300)
    comment: str = Field(default="", max_length=1000)


class VerifiedProductFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)
    evidence_reference: str | None = Field(default=None, max_length=1000)


class CreativeSkuInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    label: str = Field(min_length=1, max_length=120)
    attributes: dict[str, str] = Field(default_factory=dict)


class CreativeProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    product_name: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    brand: str = Field(default="", max_length=100)
    platform: str = Field(
        default="generic",
        pattern=r"^(amazon|tiktok_shop|shopee|temu|taobao|generic)$",
    )
    market: str = Field(default="CN", pattern=r"^(CN|US|EU)$")
    tone: str = Field(default="clear", max_length=100)
    brand_primary_color: str = Field(default="#173e2c", pattern=r"^#[0-9A-Fa-f]{6}$")
    verified_facts: list[VerifiedProductFact] = Field(default_factory=list, max_length=30)
    skus: list[CreativeSkuInput] = Field(min_length=1, max_length=50)
    copy_count: int = Field(default=3, ge=1, le=5)


class CreativeProjectResponse(BaseModel):
    project_id: str
    revision: int
    status: str
    created_at: str
    payload: dict[str, Any]
    platform_spec: dict[str, Any]
    copy_candidates: list[dict[str, Any]]
    scene_request: dict[str, Any]
    assets: list[dict[str, Any]]
    review: dict[str, Any] | None
    source_image: str | None = None
    source_image_sha256: str | None = None
    source_uploaded_by: str | None = None


class CreativeReviewRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=100)
    decision: str = Field(pattern=r"^(approve|reject)$")
    approved_copy_indexes: list[int] = Field(default_factory=list, max_length=5)
    comment: str = Field(default="", max_length=1000)


class ProductWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    name: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    brand: str = Field(default="", max_length=100)
    markets: list[str] = Field(default_factory=lambda: ["CN"], min_length=1, max_length=10)
    platforms: list[str] = Field(default_factory=lambda: ["generic"], min_length=1, max_length=10)
    verified_facts: list[VerifiedProductFact] = Field(default_factory=list, max_length=100)
    skus: list[CreativeSkuInput] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=5000)
    expected_revision: int | None = Field(default=None, ge=0)


class PublishPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    platform: str = Field(
        default="generic",
        pattern=r"^(amazon|tiktok_shop|shopee|temu|taobao|generic)$",
    )
    ad_copy: str = Field(default="", max_length=5000, alias="copy")
    product_name: str = Field(min_length=1, max_length=300)
    brand: str = Field(default="", max_length=100)
    verified_facts: list[VerifiedProductFact] = Field(default_factory=list, max_length=100)
    skus: list[CreativeSkuInput] = Field(default_factory=list, max_length=200)
    policy_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    assets: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class TextSafetyRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class HarnessStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern=r"^(tool|finish)$")
    tool: str | None = Field(default=None, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    permission: str | None = Field(default=None, max_length=100)
    result: dict[str, Any] = Field(default_factory=dict)


class HarnessRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)
    plan: list[HarnessStepRequest] = Field(default_factory=list, max_length=24)
    skill: str | None = Field(default=None, max_length=64)
    max_steps: int = Field(default=8, ge=1, le=24)
    max_tool_calls: int = Field(default=6, ge=1, le=20)
    max_tokens: int = Field(default=12_000, ge=256, le=128_000)
    max_cost_microusd: int = Field(default=100_000, ge=0, le=10_000_000)


class HarnessRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="user_requested", max_length=500)


class HarnessPermissionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    permission: str = Field(min_length=1, max_length=100)
    reviewer: str = Field(min_length=1, max_length=100)


class HarnessMemoryRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    tier: str = Field(pattern=r"^(working|episodic|long_term)$")
    key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    value: Any
    reviewed: bool = False


class MultiAgentNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    role: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=1000)
    depends_on: list[str] = Field(default_factory=list, max_length=4)


class MultiAgentRunRequest(BaseModel):
    nodes: list[MultiAgentNodeRequest] = Field(min_length=1, max_length=4)
    max_messages: int = Field(default=24, ge=1, le=100)
    max_tokens: int = Field(default=24_000, ge=256, le=128_000)


class SourceUpdateResponse(BaseModel):
    source_id: str
    content_hash: str
    status: str
    revision: int = 0
    section_count: int
    diff_available: bool
    title: str
    preview: list[dict[str, str]]
    reindex_job_id: str | None = None
    eligible_for_activation: bool
    structural_review_status: str
    legal_review_status: str
    short_section_rate: float
    temporal_review_status: str = "missing"
    generic_heading_rate: float | None = None
    blocking_reasons: list[str] = Field(default_factory=list)


class SourceUpdateApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    legal_review_confirmed: bool


class SourceUpdateCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    published_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    heading_overrides: dict[str, str] = Field(default_factory=dict)
