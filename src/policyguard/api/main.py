import hmac
import json
import re
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from policyguard import __version__
from policyguard.api.schemas import (
    AgentMemoryResponse,
    AgentMemoryReviewRequest,
    EvaluationReviewRequest,
    EvaluationReviewResponse,
    BackgroundJobResponse,
    CheckResponse,
    ComplianceWorkflowRequest,
    ComplianceWorkflowResponse,
    DocumentApprovalRequest,
    DocumentApprovalResponse,
    DocumentCorrectionRequest,
    DocumentParseResponse,
    DocumentWorkspaceResponse,
    DraftCreationRequest,
    HealthResponse,
    KnowledgeStatsResponse,
    MarketCompareRequest,
    MarketCompareResponse,
    MarketEvidenceResponse,
    OperationsDashboardResponse,
    ProductCheckRequest,
    RemediationPlanRequest,
    RetrieverStatusResponse,
    SearchHitResponse,
    SearchResponse,
    SourceUpdateApprovalRequest,
    SourceUpdateResponse,
    WorkflowReviewRequest,
)
from policyguard.application.agent import AgentBudget, ControlledAgent, configured_agent_planner
from policyguard.application.agent_context import AgentContextBuilder
from policyguard.application.compliance_report import (
    build_compliance_report,
    report_markdown,
    report_pdf,
)
from policyguard.application.document_ingestion import (
    configured_document_router,
    stage_parsed_document,
    validate_pdf_safety,
)
from policyguard.application.document_workspace import (
    DocumentWorkspace,
    document_workspace_payload,
)
from policyguard.application.embeddings import DenseRetriever, configured_embedding_provider
from policyguard.application.evidence_support import configured_evidence_verifier
from policyguard.application.evaluation_review import EvaluationReviewService
from policyguard.application.hybrid import HybridRetriever
from policyguard.application.jobs import PersistentJobQueue
from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.llm import configured_claim_extractor
from policyguard.application.query_rewrite import (
    JsonQueryRewriteCache,
    configured_query_rewriter,
)
from policyguard.application.remediation import RemediationService
from policyguard.application.rerank import RerankedHybridRetriever, configured_reranker
from policyguard.application.service import ComplianceCheckService
from policyguard.application.source_updates import (
    approve_source_update,
    list_staged_source_updates,
)
from policyguard.application.tools import SuggestConservativeRewriteTool, ToolRegistry
from policyguard.application.workflow import ComplianceWorkflowService
from policyguard.config import get_settings
from policyguard.domain.models import (
    KnowledgeFilter,
    PolicyDocument,
    PolicyScope,
    PolicySection,
    Product,
)
from policyguard.infrastructure.database import AuditLogRecord, Database
from policyguard.infrastructure.repositories import (
    SqlAlchemyComplianceRepository,
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyWorkflowRepository,
    SqlAlchemyAgentMemoryRepository,
)


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    database = Database(database_url or settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        with database.session_factory() as session:
            ingest_source_directory(
                SqlAlchemyKnowledgeRepository(session), Path(settings.source_dir)
            )
        yield
        database.engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Evidence-first product compliance API with hybrid retrieval, conditional query "
            "rewriting, staged document ingestion, and human approval boundaries."
        ),
        lifespan=lifespan,
    )
    application.state.database = database
    request_windows: dict[str, deque[float]] = defaultdict(deque)
    web_dir = Path(__file__).parents[1] / "web"
    application.mount("/static", StaticFiles(directory=web_dir), name="static")

    @application.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @application.middleware("http")
    async def request_trace(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or str(uuid4())
        request.state.trace_id = trace_id
        started = perf_counter()
        now = perf_counter()
        client_key = request.client.host if request.client else "local"
        window = request_windows[client_key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= settings.rate_limit_per_minute:
            return JSONResponse(
                {"detail": "rate_limit_exceeded"}, status_code=429,
                headers={"X-Trace-ID": trace_id, "Retry-After": "60"},
            )
        window.append(now)
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        response.headers["Server-Timing"] = f"app;dur={(perf_counter() - started) * 1000:.1f}"
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            try:
                with database.session_factory() as audit_session:
                    audit_session.add(AuditLogRecord(
                        trace_id=trace_id,
                        method=request.method,
                        path=request.url.path,
                        status_code=response.status_code,
                        actor=request.headers.get("x-reviewer", "local-user"),
                    ))
                    audit_session.commit()
            except Exception:
                pass
        return response

    def get_session() -> Iterator[Session]:
        yield from database.sessions()

    def require_admin(x_admin_key: str = Header(default="")) -> None:
        if settings.admin_api_key and not hmac.compare_digest(
            x_admin_key, settings.admin_api_key
        ):
            raise HTTPException(status_code=401, detail="admin_key_required")

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            ai_enabled=settings.app_env != "test"
            and bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model),
        )

    @application.post(
        "/api/v1/documents/parse",
        response_model=DocumentParseResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def parse_document(file: UploadFile = File(...)) -> DocumentParseResponse:
        if file.content_type not in {"application/pdf", "application/octet-stream"}:
            raise HTTPException(status_code=415, detail="pdf_required")
        content = await file.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="pdf_too_large")
        try:
            validate_pdf_safety(content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            parsed, route = configured_document_router(settings).parse(
                content, file.filename or "upload.pdf"
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        document_id, chunks = stage_parsed_document(
            parsed, route, Path(settings.upload_dir)
        )
        original_path = Path(settings.upload_dir) / document_id / "original.pdf"
        if not original_path.exists():
            original_path.write_bytes(content)
        return DocumentParseResponse(
            document_id=document_id,
            filename=parsed.filename,
            parser=parsed.parser,
            page_count=parsed.page_count,
            status=parsed.status,
            warnings=list(parsed.warnings),
            block_count=len(parsed.blocks),
            chunk_count=len(chunks),
            markdown_preview=parsed.markdown()[:2000],
            parser_route=route["route"],
            mean_block_confidence=route["mean_block_confidence"],
        )

    @application.post(
        "/api/v1/documents/parse-async",
        response_model=BackgroundJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["documents"],
    )
    async def parse_document_async(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> BackgroundJobResponse:
        if file.content_type not in {"application/pdf", "application/octet-stream"}:
            raise HTTPException(status_code=415, detail="pdf_required")
        content = await file.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="pdf_too_large")
        try:
            validate_pdf_safety(content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        digest = sha256(content).hexdigest()
        inbox = Path(settings.upload_dir) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        raw_path = inbox / f"{digest}.pdf"
        if not raw_path.exists():
            raw_path.write_bytes(content)
        job = PersistentJobQueue(session).enqueue(
            "parse_document",
            {"path": str(raw_path), "filename": file.filename or "upload.pdf"},
            idempotency_key=f"parse-document:{digest}",
            max_attempts=3,
        )
        return BackgroundJobResponse(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            result=job.result,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error=job.error,
        )

    @application.get(
        "/api/v1/jobs/{job_id}",
        response_model=BackgroundJobResponse,
        tags=["jobs"],
    )
    def get_background_job(
        job_id: str, session: Session = Depends(get_session)
    ) -> BackgroundJobResponse:
        job = PersistentJobQueue(session).get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job_not_found")
        return BackgroundJobResponse(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            result=job.result,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error=job.error,
        )

    @application.get(
        "/api/v1/jobs",
        response_model=list[BackgroundJobResponse],
        tags=["jobs"],
    )
    def list_background_jobs(
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
    ) -> list[BackgroundJobResponse]:
        return [
            BackgroundJobResponse(
                id=job.id, job_type=job.job_type, status=job.status,
                result=job.result, attempts=job.attempts,
                max_attempts=job.max_attempts, error=job.error,
            )
            for job in PersistentJobQueue(session).list(limit)
        ]

    @application.post(
        "/api/v1/jobs/{job_id}/retry",
        response_model=BackgroundJobResponse,
        tags=["jobs"],
    )
    def retry_background_job(
        job_id: str,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> BackgroundJobResponse:
        try:
            job = PersistentJobQueue(session).retry(job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return BackgroundJobResponse(
            id=job.id, job_type=job.job_type, status=job.status,
            result=job.result, attempts=job.attempts,
            max_attempts=job.max_attempts, error=job.error,
        )

    def source_update_response(item: dict) -> SourceUpdateResponse:
        policy = json.loads(Path(item["policy_path"]).read_text(encoding="utf-8"))
        return SourceUpdateResponse(
            source_id=item["source_id"],
            content_hash=item["content_hash"],
            status=item["status"],
            section_count=item["section_count"],
            diff_available=bool(item.get("diff_path")),
            title=policy["title"],
            preview=[
                {"heading": section["heading"], "text": section["text"][:500]}
                for section in policy["sections"][:5]
            ],
            reindex_job_id=item.get("reindex_job_id"),
            eligible_for_activation=item.get("eligible_for_activation", False),
            structural_review_status=item.get("structural_review_status", "blocked"),
            legal_review_status=item.get("legal_review_status", "pending"),
            short_section_rate=float(item.get("short_section_rate", 0)),
        )

    @application.get(
        "/api/v1/source-updates",
        response_model=list[SourceUpdateResponse],
        tags=["sources"],
    )
    def get_source_updates() -> list[SourceUpdateResponse]:
        return [
            source_update_response(item)
            for item in list_staged_source_updates(Path("data/update-state/staged"))
        ]

    @application.get(
        "/api/v1/source-updates/{source_id}/{content_hash}/diff",
        tags=["sources"],
    )
    def get_source_update_diff(source_id: str, content_hash: str) -> PlainTextResponse:
        updates = list_staged_source_updates(
            Path("data/update-state/staged"), latest_only=False
        )
        item = next((
            update for update in updates
            if update["source_id"] == source_id and update["content_hash"] == content_hash
        ), None)
        if item is None or not item.get("diff_path"):
            raise HTTPException(status_code=404, detail="source_update_diff_not_found")
        path = Path(item["diff_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="source_update_diff_not_found")
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/plain")

    @application.post(
        "/api/v1/source-updates/{source_id}/{content_hash}/approve",
        response_model=SourceUpdateResponse,
        tags=["sources"],
    )
    def approve_staged_source_update(
        source_id: str,
        content_hash: str,
        payload: SourceUpdateApprovalRequest,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> SourceUpdateResponse:
        if not re.fullmatch(r"[a-z0-9-]{3,100}", source_id) or not re.fullmatch(
            r"[a-f0-9]{64}", content_hash
        ):
            raise HTTPException(status_code=404, detail="source_update_not_found")
        try:
            item = approve_source_update(
                Path("data/update-state/staged"), source_id, content_hash,
                payload.reviewer, SqlAlchemyKnowledgeRepository(session),
                legal_review_confirmed=payload.legal_review_confirmed,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        reindex_job = PersistentJobQueue(session).enqueue(
            "reindex_embeddings",
            {"source_id": source_id, "content_hash": content_hash},
            idempotency_key=f"reindex-source:{source_id}:{content_hash}",
        )
        item["reindex_job_id"] = reindex_job.id
        return source_update_response(item)

    @application.post(
        "/api/v1/source-updates/check",
        response_model=BackgroundJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["sources"],
    )
    def enqueue_source_check(
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> BackgroundJobResponse:
        bucket = int(datetime.now(UTC).timestamp() // 300)
        job = PersistentJobQueue(session).enqueue(
            "source_monitor", {},
            idempotency_key=f"source-monitor:{bucket}", max_attempts=3,
        )
        return BackgroundJobResponse(
            id=job.id, job_type=job.job_type, status=job.status,
            result=job.result, attempts=job.attempts,
            max_attempts=job.max_attempts, error=job.error,
        )

    @application.post(
        "/api/v1/documents/{document_id}/approve",
        response_model=DocumentApprovalResponse,
        tags=["documents"],
    )
    def approve_document(
        document_id: str,
        payload: DocumentApprovalRequest,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> DocumentApprovalResponse:
        if not re.fullmatch(r"[a-f0-9]{24}", document_id):
            raise HTTPException(status_code=404, detail="document_not_found")
        staged = Path(settings.upload_dir) / document_id
        manifest_path = staged / "manifest.json"
        chunks_path = staged / "chunks.json"
        document_path = staged / "document.json"
        if not all(path.is_file() for path in (manifest_path, chunks_path, document_path)):
            raise HTTPException(status_code=404, detail="document_not_found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        canonical = json.loads(document_path.read_text(encoding="utf-8"))
        if canonical.get("status") != "parsed":
            raise HTTPException(status_code=409, detail="document_requires_structure_review")
        if not chunks:
            raise HTTPException(status_code=409, detail="document_has_no_searchable_chunks")
        sections = tuple(
            PolicySection(
                section_id=item["chunk_id"],
                heading=(item.get("section_path") or [item.get("block_type", "content")])[-1],
                text=item["text"],
            )
            for item in chunks
        )
        policy_document = PolicyDocument(
            id=document_id,
            title=payload.title,
            source_url=payload.source_url,
            publisher=payload.publisher,
            version=payload.version,
            published_at=payload.published_at,
            retrieved_at=datetime.now(UTC).date().isoformat(),
            content_hash=canonical["content_hash"],
            sections=sections,
            scopes=(
                PolicyScope(
                    jurisdiction=payload.jurisdiction.upper(),
                    category=payload.category,
                    channel=payload.channel,
                    legal_level=payload.legal_level,
                    source_language=payload.source_language,
                    translation_status="original",
                    effective_from=payload.effective_from,
                ),
            ),
        )
        SqlAlchemyKnowledgeRepository(session).activate_document_version(policy_document)
        manifest.update(
            {
                "activation_status": "active",
                "approved_at": datetime.now(UTC).isoformat(),
                "reviewer": payload.reviewer,
                "knowledge_document_id": policy_document.id,
                "activated_chunks": len(sections),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return DocumentApprovalResponse(
            document_id=document_id,
            activation_status="active",
            activated_chunks=len(sections),
            reviewer=payload.reviewer,
        )

    @application.get(
        "/api/v1/documents/{document_id}",
        response_model=DocumentWorkspaceResponse,
        tags=["documents"],
    )
    def get_document_workspace(document_id: str) -> DocumentWorkspaceResponse:
        try:
            document, manifest = DocumentWorkspace(Path(settings.upload_dir)).load(document_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return DocumentWorkspaceResponse(**document_workspace_payload(document, manifest))

    @application.get(
        "/api/v1/documents/{document_id}/original",
        tags=["documents"],
    )
    def get_original_document(document_id: str) -> FileResponse:
        try:
            path = (
                DocumentWorkspace(Path(settings.upload_dir)).directory(document_id)
                / "original.pdf"
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="document_original_not_found")
        return FileResponse(path, media_type="application/pdf", filename=f"{document_id}.pdf")

    @application.patch(
        "/api/v1/documents/{document_id}",
        response_model=DocumentWorkspaceResponse,
        tags=["documents"],
    )
    def correct_document(
        document_id: str,
        payload: DocumentCorrectionRequest,
        _: None = Depends(require_admin),
    ) -> DocumentWorkspaceResponse:
        try:
            document, manifest = DocumentWorkspace(Path(settings.upload_dir)).correct(
                document_id,
                expected_revision=payload.expected_revision,
                reviewer=payload.reviewer,
                corrections=[item.model_dump(exclude_none=True) for item in payload.corrections],
                resolved_warnings=payload.resolved_warnings,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return DocumentWorkspaceResponse(**document_workspace_payload(document, manifest))

    @application.get(
        "/api/v1/operations/dashboard",
        response_model=OperationsDashboardResponse,
        tags=["operations"],
    )
    def operations_dashboard(
        session: Session = Depends(get_session),
    ) -> OperationsDashboardResponse:
        root = Path(__file__).parents[3]
        knowledge = SqlAlchemyKnowledgeRepository(session)
        staged_updates = list_staged_source_updates(Path("data/update-state/staged"))
        evaluations = {}
        for name, path in {
            "rag": root / "data/benchmarks/rag-suite-models.json",
            "agent": root / "data/benchmarks/agent-vs-pipeline.json",
            "pdf": root / "data/benchmarks/pdf-quality.json",
        }.items():
            if path.exists():
                evaluations[name] = json.loads(path.read_text(encoding="utf-8"))
        reports = []
        for run in SqlAlchemyWorkflowRepository(session).list_recent(30):
            reports.append({
                "workflow_id": run.id,
                "status": run.status.value,
                "title": run.input_payload.get("product", {}).get("title", ""),
                "created_at": run.created_at.isoformat(),
                "report_json": f"/api/v1/workflows/compliance/{run.id}/report?format=json",
                "report_pdf": f"/api/v1/workflows/compliance/{run.id}/report?format=pdf",
            })
        return OperationsDashboardResponse(
            knowledge={
                "documents": knowledge.document_count(),
                "chunks": len(knowledge.list_chunks()),
            },
            sources={
                "staged": sum(item.get("status") == "staged" for item in staged_updates),
                "structural_passed": sum(
                    item.get("structural_review_status") == "passed" for item in staged_updates
                ),
                "pending_legal_review": sum(
                    item.get("legal_review_status") == "pending" for item in staged_updates
                ),
            },
            jobs=PersistentJobQueue(session).stats(),
            evaluations=evaluations,
            report_history=reports,
        )

    def evaluation_review_service(session: Session) -> EvaluationReviewService:
        return EvaluationReviewService(
            session,
            SqlAlchemyKnowledgeRepository(session),
            Path(__file__).parents[3]
            / "data/evaluation/rag-cross-lingual-zh-en-v1.json",
        )

    @application.get(
        "/api/v1/evaluations/cross-language/reviews",
        response_model=list[EvaluationReviewResponse],
        tags=["evaluation-review"],
    )
    def list_cross_language_reviews(
        session: Session = Depends(get_session),
    ) -> list[EvaluationReviewResponse]:
        return [EvaluationReviewResponse(**item) for item in evaluation_review_service(session).list_items()]

    @application.post(
        "/api/v1/evaluations/cross-language/reviews/{sample_id}",
        response_model=EvaluationReviewResponse,
        tags=["evaluation-review"],
    )
    def review_cross_language_sample(
        sample_id: str,
        payload: EvaluationReviewRequest,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> EvaluationReviewResponse:
        try:
            item = evaluation_review_service(session).review(
                sample_id, payload.decision, payload.reviewer,
                payload.expected_section_id, payload.comment,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return EvaluationReviewResponse(**item)

    @application.get("/api/v1/review-queue", tags=["operations"])
    def review_queue(session: Session = Depends(get_session)) -> dict:
        evaluation_items = evaluation_review_service(session).list_items()
        staged_updates = list_staged_source_updates(Path("data/update-state/staged"))
        memories = SqlAlchemyAgentMemoryRepository(session).list_recent(200)
        return {
            "evaluation": {
                "pending": sum(item["review_status"] == "pending_human_review"
                               for item in evaluation_items),
                "items": evaluation_items,
            },
            "legal_sources": {
                "pending": sum(item.get("legal_review_status") == "pending"
                               for item in staged_updates),
                "items": [source_update_response(item) for item in staged_updates],
            },
            "agent_memories": {
                "invalidated": sum(item.review_status == "invalidated" for item in memories),
            },
            "automatic_approval": False,
        }

    @application.post(
        "/api/v1/checks",
        response_model=CheckResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["checks"],
    )
    def create_check(
        payload: ProductCheckRequest,
        session: Session = Depends(get_session),
    ) -> CheckResponse:
        product = Product(
            external_id=payload.external_id,
            title=payload.title,
            description=payload.description,
            category=payload.category,
            attributes=payload.attributes,
        )
        repository = SqlAlchemyComplianceRepository(session)
        result = ComplianceCheckService(repository).execute(product)
        return CheckResponse.from_domain(result)

    @application.get(
        "/api/v1/checks/{check_id}", response_model=CheckResponse, tags=["checks"]
    )
    def get_check(
        check_id: str,
        session: Session = Depends(get_session),
    ) -> CheckResponse:
        repository = SqlAlchemyComplianceRepository(session)
        result = repository.get_check(check_id)
        if result is None:
            raise HTTPException(status_code=404, detail="check_not_found")
        return CheckResponse.from_domain(result)

    @application.get(
        "/api/v1/knowledge/search", response_model=SearchResponse, tags=["knowledge"]
    )
    def search_knowledge(
        q: str = Query(min_length=2, max_length=500),
        top_k: int = Query(default=5, ge=1, le=20),
        mode: str = Query(default="bm25", pattern="^(bm25|dense|hybrid|hybrid_rerank)$"),
        market: str = Query(default="CN", min_length=2, max_length=20),
        category: str = Query(default="all", max_length=100),
        channel: str = Query(default="all", max_length=100),
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        session: Session = Depends(get_session),
    ) -> SearchResponse:
        repository = SqlAlchemyKnowledgeRepository(session)
        scope = KnowledgeFilter(
            jurisdiction=market.upper(), category=category, channel=channel, as_of=as_of
        )
        provider = configured_embedding_provider(settings)
        if mode in {"dense", "hybrid", "hybrid_rerank"}:
            if provider is None:
                raise HTTPException(status_code=503, detail="embedding_not_configured")
            dense = DenseRetriever(repository, provider)
            if mode == "dense":
                hits = dense.search(q, top_k=top_k, scope=scope)
                retriever_name = f"dense:{provider.provider_name}/{provider.model_name}"
            elif mode == "hybrid":
                hits = HybridRetriever(repository, dense).search(q, top_k=top_k, scope=scope)
                retriever_name = f"hybrid:rrf+{provider.provider_name}/{provider.model_name}"
            else:
                reranker = configured_reranker(settings)
                if reranker is None:
                    raise HTTPException(status_code=503, detail="reranker_not_configured")
                hits = RerankedHybridRetriever(
                    repository, HybridRetriever(repository, dense), reranker
                ).search(q, top_k=top_k, scope=scope)
                retriever_name = (
                    f"hybrid_rrf_rerank:{provider.model_name}+{reranker.model_name}"
                )
        else:
            hits = BM25Retriever(repository).search(q, top_k=top_k, scope=scope)
            retriever_name = "lexical_bm25_cjk_v1"
        return SearchResponse(
            query=q,
            retriever=retriever_name,
            result_count=len(hits),
            applied_filters={
                "market": scope.jurisdiction,
                "category": scope.category,
                "channel": scope.channel,
                "as_of": scope.as_of or "current",
            },
            results=[
                SearchHitResponse(
                    rank=hit.rank,
                    score=hit.score,
                    chunk_id=hit.chunk.id,
                    document_title=hit.chunk.document_title,
                    section_id=hit.chunk.section_id,
                    heading=hit.chunk.heading,
                    text=hit.chunk.text,
                    source_url=hit.chunk.source_url,
                    jurisdiction=hit.chunk.jurisdiction,
                )
                for hit in hits
            ],
        )

    @application.get(
        "/api/v1/knowledge/stats",
        response_model=KnowledgeStatsResponse,
        tags=["knowledge"],
    )
    def knowledge_stats(
        session: Session = Depends(get_session),
    ) -> KnowledgeStatsResponse:
        repository = SqlAlchemyKnowledgeRepository(session)
        return KnowledgeStatsResponse(
            documents=repository.document_count(),
            chunks=len(repository.list_chunks()),
            retriever="lexical_bm25_cjk_v1",
            embedding_configured=configured_embedding_provider(settings) is not None,
        )

    @application.get(
        "/api/v1/knowledge/retrievers",
        response_model=RetrieverStatusResponse,
        tags=["knowledge"],
    )
    def retriever_status() -> RetrieverStatusResponse:
        provider = configured_embedding_provider(settings)
        reranker = configured_reranker(settings)
        query_rewriter = configured_query_rewriter(settings)
        return RetrieverStatusResponse(
            bm25_available=True,
            dense_available=provider is not None,
            dense_provider=provider.provider_name if provider else None,
            dense_model=provider.model_name if provider else None,
            rerank_available=reranker is not None,
            rerank_provider=reranker.provider_name if reranker else None,
            rerank_model=reranker.model_name if reranker else None,
            query_rewrite_available=query_rewriter is not None,
            query_rewrite_model=query_rewriter.model if query_rewriter else None,
        )

    @application.post(
        "/api/v1/knowledge/compare",
        response_model=MarketCompareResponse,
        tags=["knowledge"],
    )
    def compare_markets(
        payload: MarketCompareRequest,
        session: Session = Depends(get_session),
    ) -> MarketCompareResponse:
        repository = SqlAlchemyKnowledgeRepository(session)
        retriever = BM25Retriever(repository)
        query = " ".join(payload.claims)
        market_results: list[MarketEvidenceResponse] = []
        for market in payload.markets:
            scope = KnowledgeFilter(
                jurisdiction=market.upper(),
                category=payload.category,
                channel=payload.channel,
                as_of=payload.as_of,
            )
            hits = retriever.search(query, top_k=5, scope=scope)
            market_results.append(
                MarketEvidenceResponse(
                    market=scope.jurisdiction,
                    decision="evidence_only_manual_review",
                    results=[
                        SearchHitResponse(
                            rank=hit.rank,
                            score=hit.score,
                            chunk_id=hit.chunk.id,
                            document_title=hit.chunk.document_title,
                            section_id=hit.chunk.section_id,
                            heading=hit.chunk.heading,
                            text=hit.chunk.text,
                            source_url=hit.chunk.source_url,
                            jurisdiction=hit.chunk.jurisdiction,
                        )
                        for hit in hits
                    ],
                )
            )
        return MarketCompareResponse(
            claims=payload.claims,
            category=payload.category,
            channel=payload.channel,
            as_of=payload.as_of,
            markets=market_results,
        )

    @application.post(
        "/api/v1/workflows/compliance",
        response_model=ComplianceWorkflowResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["workflows"],
    )
    def start_compliance_workflow(
        payload: ComplianceWorkflowRequest,
        session: Session = Depends(get_session),
    ) -> ComplianceWorkflowResponse:
        knowledge_repository = SqlAlchemyKnowledgeRepository(session)
        workflow_repository = SqlAlchemyWorkflowRepository(session)
        embedding_provider = configured_embedding_provider(settings)
        workflow_retriever = (
            HybridRetriever(
                knowledge_repository,
                DenseRetriever(knowledge_repository, embedding_provider),
            )
            if embedding_provider
            else BM25Retriever(knowledge_repository)
        )
        run = ComplianceWorkflowService(
            knowledge_repository,
            workflow_repository,
            claim_extractor=configured_claim_extractor(settings),
            retriever=workflow_retriever,
            evidence_verifier=configured_evidence_verifier(settings),
            query_rewriter=configured_query_rewriter(settings),
            query_rewrite_cache=JsonQueryRewriteCache(Path(settings.query_rewrite_cache)),
        ).execute(
            product=payload.product.model_dump(),
            markets=payload.markets,
            category=payload.category,
            channel=payload.channel,
            as_of=payload.as_of,
        )
        return ComplianceWorkflowResponse.from_domain(run)

    @application.get(
        "/api/v1/workflows/compliance/{run_id}",
        response_model=ComplianceWorkflowResponse,
        tags=["workflows"],
    )
    def get_compliance_workflow(
        run_id: str,
        session: Session = Depends(get_session),
    ) -> ComplianceWorkflowResponse:
        run = SqlAlchemyWorkflowRepository(session).get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        return ComplianceWorkflowResponse.from_domain(run)

    @application.get(
        "/api/v1/workflows/compliance/{run_id}/report",
        tags=["workflows"],
    )
    def export_compliance_report(
        run_id: str,
        format: str = Query(default="json", pattern="^(json|markdown|pdf)$"),
        session: Session = Depends(get_session),
    ) -> Response:
        run = SqlAlchemyWorkflowRepository(session).get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        report = build_compliance_report(run)
        filename = f"policyguard-{run_id}"
        if format == "markdown":
            return PlainTextResponse(
                report_markdown(report),
                media_type="text/markdown",
                headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
            )
        if format == "pdf":
            try:
                content = report_pdf(report)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return Response(
                content,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
            )
        return JSONResponse(
            report,
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    @application.post(
        "/api/v1/workflows/compliance/{run_id}/review",
        response_model=ComplianceWorkflowResponse,
        tags=["workflows"],
    )
    def review_compliance_workflow(
        run_id: str,
        payload: WorkflowReviewRequest,
        session: Session = Depends(get_session),
    ) -> ComplianceWorkflowResponse:
        service = ComplianceWorkflowService(
            SqlAlchemyKnowledgeRepository(session),
            SqlAlchemyWorkflowRepository(session),
            claim_extractor=configured_claim_extractor(settings),
            evidence_verifier=configured_evidence_verifier(settings),
        )
        try:
            run = service.review(
                run_id=run_id,
                decision_id=payload.decision_id,
                decision=payload.decision,
                reviewer=payload.reviewer,
                comment=payload.comment,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ComplianceWorkflowResponse.from_domain(run)

    @application.post(
        "/api/v1/workflows/compliance/{run_id}/remediation-plan",
        response_model=ComplianceWorkflowResponse,
        tags=["workflows"],
    )
    def create_remediation_plan(
        run_id: str,
        payload: RemediationPlanRequest,
        session: Session = Depends(get_session),
    ) -> ComplianceWorkflowResponse:
        service = RemediationService(
            SqlAlchemyWorkflowRepository(session),
            ToolRegistry([SuggestConservativeRewriteTool()]),
            context_builder=AgentContextBuilder(SqlAlchemyAgentMemoryRepository(session)),
            memory_repository=SqlAlchemyAgentMemoryRepository(session),
        )
        try:
            if payload.mode == "agent":
                planner = configured_agent_planner(settings)
                if planner is None:
                    raise HTTPException(status_code=503, detail="agent_planner_not_configured")
                run = service.plan_with_agent(
                    run_id=run_id,
                    plan_id=payload.plan_id,
                    agent=ControlledAgent(
                        planner,
                        ToolRegistry([SuggestConservativeRewriteTool()]),
                        AgentBudget(max_steps=3, max_tool_calls=1),
                    ),
                )
            else:
                run = service.plan(run_id=run_id, plan_id=payload.plan_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ComplianceWorkflowResponse.from_domain(run)

    @application.post(
        "/api/v1/workflows/compliance/{run_id}/draft",
        response_model=ComplianceWorkflowResponse,
        tags=["workflows"],
    )
    def create_internal_draft(
        run_id: str,
        payload: DraftCreationRequest,
        session: Session = Depends(get_session),
    ) -> ComplianceWorkflowResponse:
        service = RemediationService(
            SqlAlchemyWorkflowRepository(session),
            ToolRegistry([SuggestConservativeRewriteTool()]),
            memory_repository=SqlAlchemyAgentMemoryRepository(session),
        )
        try:
            run = service.create_draft(
                run_id=run_id,
                execution_id=payload.execution_id,
                approved_by=payload.approved_by,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ComplianceWorkflowResponse.from_domain(run)

    @application.get(
        "/api/v1/agent-memories",
        response_model=list[AgentMemoryResponse],
        tags=["agent-memory"],
    )
    def list_agent_memories(
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
    ) -> list[AgentMemoryResponse]:
        return [
            AgentMemoryResponse(
                id=item.id, run_id=item.run_id, task_type=item.task_type,
                jurisdictions=list(item.jurisdictions), category=item.category,
                channel=item.channel, summary=item.summary, outcome=item.outcome,
                source_versions=item.source_versions, reviewed_by=item.reviewed_by,
                review_status=item.review_status,
                invalidated_reason=item.invalidated_reason, created_at=item.created_at,
            )
            for item in SqlAlchemyAgentMemoryRepository(session).list_recent(limit)
        ]

    @application.post(
        "/api/v1/agent-memories/{memory_id}/review",
        response_model=AgentMemoryResponse,
        tags=["agent-memory"],
    )
    def review_agent_memory(
        memory_id: str,
        payload: AgentMemoryReviewRequest,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> AgentMemoryResponse:
        try:
            item = SqlAlchemyAgentMemoryRepository(session).review(
                memory_id, payload.reviewer, payload.decision, payload.comment
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AgentMemoryResponse(
            id=item.id, run_id=item.run_id, task_type=item.task_type,
            jurisdictions=list(item.jurisdictions), category=item.category,
            channel=item.channel, summary=item.summary, outcome=item.outcome,
            source_versions=item.source_versions, reviewed_by=item.reviewed_by,
            review_status=item.review_status,
            invalidated_reason=item.invalidated_reason, created_at=item.created_at,
        )

    return application


app = create_app()
