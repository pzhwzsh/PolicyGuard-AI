import hmac
import json
import re
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
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
from sqlalchemy import select
from sqlalchemy.orm import Session

from policyguard import __version__
from policyguard.api.schemas import (
    AgentMemoryResponse,
    AgentMemoryReviewRequest,
    BackgroundJobResponse,
    CheckResponse,
    ComplianceWorkflowRequest,
    ComplianceWorkflowResponse,
    DocumentApprovalRequest,
    DocumentApprovalResponse,
    DocumentCorrectionRequest,
    DocumentDeletionRequest,
    DocumentDeletionResponse,
    DocumentParseResponse,
    DocumentWorkspaceResponse,
    DraftCreationRequest,
    EvaluationReviewRequest,
    EvaluationReviewResponse,
    HealthResponse,
    KnowledgeStatsResponse,
    MarketCompareRequest,
    MarketCompareResponse,
    MarketEvidenceResponse,
    ModelEvaluationRequest,
    ModelPromotionRequest,
    OperationsDashboardResponse,
    ProductCheckRequest,
    RemediationPlanRequest,
    RetrieverStatusResponse,
    SearchHitResponse,
    SearchResponse,
    SourceUpdateApprovalRequest,
    SourceUpdateCorrectionRequest,
    SourceUpdateResponse,
    TableCleaningConfirmationRequest,
    TableCleaningConfirmationResponse,
    TableCleaningPreviewResponse,
    WorkflowReviewRequest,
)
from policyguard.application.agent import AgentBudget, ControlledAgent, configured_agent_planner
from policyguard.application.agent_context import AgentContextBuilder
from policyguard.application.automation_handoff import (
    build_dingtalk_preview,
    build_rpa_handoff,
)
from policyguard.application.batch_artifacts import BatchArtifactWorkspace
from policyguard.application.compliance_report import (
    build_compliance_report,
    report_markdown,
    report_pdf,
)
from policyguard.application.cost_control import WorkflowModelBudget
from policyguard.application.document_ingestion import (
    configured_document_router,
    stage_parsed_document,
    validate_pdf_safety,
)
from policyguard.application.document_workspace import (
    DocumentWorkspace,
    document_workspace_payload,
)
from policyguard.application.embeddings import (
    DenseRetriever,
    configured_embedding_chain,
)
from policyguard.application.evaluation_review import EvaluationReviewService
from policyguard.application.evaluation_workbench import validate_evaluation_path
from policyguard.application.evidence_support import configured_evidence_verifier
from policyguard.application.execution_policy import choose_remediation_mode
from policyguard.application.hybrid import FallbackRetriever, HybridRetriever
from policyguard.application.jobs import PersistentJobQueue
from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.llm import configured_claim_extractor
from policyguard.application.media_ingestion import MEDIA_TYPES, validate_media
from policyguard.application.policy_impact import analyze_policy_impact
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
    revise_staged_source_update,
)
from policyguard.application.table_cleaning import TableCleaningWorkspace
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
from policyguard.infrastructure.database import (
    AuditLogRecord,
    Database,
    ResourceOwnershipRecord,
)
from policyguard.infrastructure.repositories import (
    SqlAlchemyAgentMemoryRepository,
    SqlAlchemyComplianceRepository,
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyWorkflowRepository,
)
from policyguard.infrastructure.telemetry import (
    RuntimeMetric,
    RuntimeMetricBuffer,
    estimated_model_cost,
    runtime_summary,
)


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    provider_settings = replace(settings, app_env="test") if database_url else settings
    database = Database(database_url or settings.database_url)
    telemetry = RuntimeMetricBuffer(database.session_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        with database.session_factory() as session:
            ingest_source_directory(
                SqlAlchemyKnowledgeRepository(session), Path(settings.source_dir)
            )
        await telemetry.start()
        try:
            yield
        finally:
            await telemetry.stop()
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
    application.state.telemetry = telemetry
    request_windows: dict[str, deque[float]] = defaultdict(deque)
    try:
        tenant_keys = json.loads(settings.tenant_keys_json) if settings.tenant_keys_json else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("tenant_keys_json_invalid") from exc
    if not isinstance(tenant_keys, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in tenant_keys.items()
    ):
        raise RuntimeError("tenant_keys_json_invalid")
    web_dir = Path(__file__).parents[1] / "web"
    application.mount("/static", StaticFiles(directory=web_dir), name="static")

    @application.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(web_dir / "user.html")

    @application.get("/admin", include_in_schema=False)
    def admin_dashboard() -> FileResponse:
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
        try:
            response = await call_next(request)
        except Exception:
            if not request.url.path.startswith("/static"):
                telemetry.record(RuntimeMetric(
                    trace_id=trace_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=500,
                    duration_ms=(perf_counter() - started) * 1000,
                    created_at=datetime.now(UTC),
                ))
            raise
        response.headers["X-Trace-ID"] = trace_id
        response.headers["Server-Timing"] = f"app;dur={(perf_counter() - started) * 1000:.1f}"
        if not request.url.path.startswith("/static"):
            telemetry.record(RuntimeMetric(
                trace_id=trace_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=(perf_counter() - started) * 1000,
                created_at=datetime.now(UTC),
            ))
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

    def tenant_identity(
        x_tenant_id: str = Header(default=""),
        x_tenant_key: str = Header(default=""),
    ) -> str:
        if not tenant_keys:
            return "default"
        tenant_id = x_tenant_id.strip()
        expected = tenant_keys.get(tenant_id)
        if not expected or not hmac.compare_digest(x_tenant_key, expected):
            raise HTTPException(status_code=401, detail="tenant_credentials_required")
        return tenant_id

    def bind_resource(session: Session, resource_type: str, resource_id: str, tenant: str) -> None:
        ownership = session.get(ResourceOwnershipRecord, (resource_type, resource_id, tenant))
        if ownership is None:
            session.add(ResourceOwnershipRecord(
                resource_type=resource_type, resource_id=resource_id, tenant_id=tenant
            ))
            session.commit()

    def require_resource(
        session: Session, resource_type: str, resource_id: str, tenant: str
    ) -> None:
        ownership = session.get(ResourceOwnershipRecord, (resource_type, resource_id, tenant))
        if ownership is None:
            if tenant == "default" and not tenant_keys:
                return
            raise HTTPException(status_code=404, detail=f"{resource_type}_not_found")

    def require_admin(x_admin_key: str = Header(default="")) -> None:
        if settings.admin_api_key and not hmac.compare_digest(
            x_admin_key, settings.admin_api_key
        ):
            raise HTTPException(status_code=401, detail="admin_key_required")

    def require_admin_reviewer(
        x_admin_key: str = Header(default=""),
        x_reviewer: str = Header(default=""),
    ) -> str:
        if settings.admin_api_key:
            admin_match = hmac.compare_digest(x_admin_key, settings.admin_api_key)
            reviewer_match = bool(settings.reviewer_api_key) and hmac.compare_digest(
                x_admin_key, settings.reviewer_api_key
            )
            if not (admin_match or reviewer_match):
                raise HTTPException(status_code=401, detail="reviewer_or_admin_key_required")
        reviewer = x_reviewer.strip()
        if settings.admin_api_key and not reviewer:
            raise HTTPException(status_code=401, detail="reviewer_identity_required")
        return reviewer or "local-reviewer"

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            ai_enabled=database_url is None
            and settings.app_env != "test"
            and bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model),
        )

    @application.post(
        "/api/v1/documents/parse",
        response_model=DocumentParseResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def parse_document(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> DocumentParseResponse:
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
        bind_resource(session, "document", document_id, tenant)
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
        tenant: str = Depends(tenant_identity),
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
            {
                "path": str(raw_path), "filename": file.filename or "upload.pdf",
                "tenant_id": tenant,
            },
            idempotency_key=f"parse-document:{tenant}:{digest}",
            max_attempts=3,
        )
        bind_resource(session, "job", job.id, tenant)
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
        job_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        require_resource(session, "job", job_id, tenant)
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
        tenant: str = Depends(tenant_identity),
    ) -> list[BackgroundJobResponse]:
        owned_ids = set(session.scalars(
            select(ResourceOwnershipRecord.resource_id).where(
                ResourceOwnershipRecord.resource_type == "job",
                ResourceOwnershipRecord.tenant_id == tenant,
            )
        ).all())
        return [
            BackgroundJobResponse(
                id=job.id, job_type=job.job_type, status=job.status,
                result=job.result, attempts=job.attempts,
                max_attempts=job.max_attempts, error=job.error,
            )
            for job in PersistentJobQueue(session).list(limit)
            if (not tenant_keys and tenant == "default") or job.id in owned_ids
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
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        require_resource(session, "job", job_id, tenant)
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

    @application.get("/api/v1/batches/template", tags=["batch-review"])
    def batch_review_template() -> PlainTextResponse:
        return PlainTextResponse(
            "external_id,category,title,description,markets\n"
            "SKU-001,beauty,商品标题,商品描述,CN\n",
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="policyguard-template.csv"'},
        )

    @application.post(
        "/api/v1/batches/review",
        response_model=BackgroundJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["batch-review"],
    )
    async def enqueue_batch_review(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".csv", ".xlsx"}:
            raise HTTPException(status_code=415, detail="batch_file_type_not_supported")
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="batch_file_too_large")
        digest = sha256(content).hexdigest()
        batch_id = sha256(f"{tenant}:{digest}".encode()).hexdigest()[:20]
        inbox = Path(settings.upload_dir) / "batches" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / f"{batch_id}{suffix}"
        if not path.exists():
            path.write_bytes(content)
        job = PersistentJobQueue(session).enqueue(
            "batch_compliance_review",
            {"batch_id": batch_id, "path": str(path.resolve()), "filename": file.filename},
            idempotency_key=f"batch-review:{tenant}:{digest}",
            max_attempts=5,
        )
        bind_resource(session, "job", job.id, tenant)
        return BackgroundJobResponse(
            id=job.id, job_type=job.job_type, status=job.status,
            result=job.result, attempts=job.attempts,
            max_attempts=job.max_attempts, error=job.error,
        )

    @application.post(
        "/api/v1/media/claims",
        response_model=BackgroundJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["media"],
    )
    async def enqueue_media_claim_extraction(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in MEDIA_TYPES:
            raise HTTPException(status_code=415, detail="media_type_not_supported")
        limit = 10 * 1024 * 1024 if suffix in {".png", ".jpg", ".jpeg"} else 50 * 1024 * 1024
        content = await file.read(limit + 1)
        if len(content) > limit:
            raise HTTPException(status_code=413, detail="media_too_large")
        try:
            validate_media(content, suffix)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        digest = sha256(content).hexdigest()
        media_key = sha256(f"{tenant}:{digest}".encode()).hexdigest()[:24]
        inbox = Path(settings.upload_dir) / "media" / "inbox" / media_key
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / f"original{suffix}"
        if not path.exists():
            path.write_bytes(content)
        job = PersistentJobQueue(session).enqueue(
            "media_claim_extraction",
            {"path": str(path.resolve()), "filename": file.filename, "tenant_id": tenant},
            idempotency_key=f"media-claims:{tenant}:{digest}",
            max_attempts=3,
        )
        bind_resource(session, "job", job.id, tenant)
        return BackgroundJobResponse(
            id=job.id, job_type=job.job_type, status=job.status,
            result=job.result, attempts=job.attempts,
            max_attempts=job.max_attempts, error=job.error,
        )

    @application.post(
        "/api/v1/batches/clean-preview",
        response_model=TableCleaningPreviewResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["batch-review"],
    )
    async def preview_batch_cleaning(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> TableCleaningPreviewResponse:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".csv", ".xlsx"}:
            raise HTTPException(status_code=415, detail="batch_file_type_not_supported")
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="batch_file_too_large")
        try:
            staged = TableCleaningWorkspace(
                Path(settings.upload_dir) / "tables"
            ).stage(content, file.filename or f"upload{suffix}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        analysis = staged["analysis"]
        manifest = staged["manifest"]
        bind_resource(session, "table", staged["table_id"], tenant)
        return TableCleaningPreviewResponse(
            table_id=staged["table_id"],
            revision=manifest["revision"],
            status=manifest["status"],
            summary=analysis["summary"],
            sheets=analysis["sheets"],
            skipped_sheets=analysis["skipped_sheets"],
            rows=analysis["rows"][:50],
            issues=analysis["issues"][:100],
        )

    @application.post(
        "/api/v1/batches/cleaning/{table_id}/confirm",
        response_model=TableCleaningConfirmationResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["batch-review"],
    )
    def confirm_batch_cleaning(
        table_id: str,
        payload: TableCleaningConfirmationRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> TableCleaningConfirmationResponse:
        require_resource(session, "table", table_id, tenant)
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        try:
            confirmed = TableCleaningWorkspace(
                Path(settings.upload_dir) / "tables"
            ).confirm(
                table_id,
                expected_revision=payload.expected_revision,
                reviewer=payload.reviewer,
                field_mappings=payload.field_mappings,
                allow_partial=payload.allow_partial,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        manifest = confirmed["manifest"]
        job = PersistentJobQueue(session).enqueue(
            "batch_compliance_review",
            {
                "batch_id": table_id,
                "path": manifest["cleaned_path"],
                "filename": manifest["filename"],
                "cleaning_revision": manifest["revision"],
            },
            idempotency_key=f"batch-cleaning:{tenant}:{table_id}:{manifest['revision']}",
            max_attempts=5,
        )
        bind_resource(session, "job", job.id, tenant)
        job_response = BackgroundJobResponse(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            result=job.result,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error=job.error,
        )
        return TableCleaningConfirmationResponse(
            table_id=table_id,
            revision=manifest["revision"],
            status=manifest["status"],
            summary=confirmed["analysis"]["summary"],
            report_url=f"/api/v1/batches/cleaning/{table_id}/report",
            job=job_response,
        )

    @application.get(
        "/api/v1/batches/cleaning/{table_id}/report",
        tags=["batch-review"],
    )
    def download_table_cleaning_report(
        table_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> FileResponse:
        require_resource(session, "table", table_id, tenant)
        try:
            manifest, _ = TableCleaningWorkspace(
                Path(settings.upload_dir) / "tables"
            ).load(table_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(manifest.get("report_path", ""))
        table_directory = (
            Path(settings.upload_dir) / "tables" / table_id
        ).resolve()
        if (
            path.resolve().parent != table_directory
            or path.name != "cleaning-report.csv"
            or not path.is_file()
        ):
            raise HTTPException(status_code=409, detail="table_cleaning_not_confirmed")
        return FileResponse(path, media_type="text/csv", filename="cleaning-report.csv")

    @application.get("/api/v1/batches/{job_id}/result", tags=["batch-review"])
    def download_batch_result(
        job_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> FileResponse:
        require_resource(session, "job", job_id, tenant)
        job = PersistentJobQueue(session).get(job_id)
        if job is None or job.job_type != "batch_compliance_review":
            raise HTTPException(status_code=404, detail="batch_job_not_found")
        if job.status != "completed" or not job.result.get("output_path"):
            raise HTTPException(status_code=409, detail="batch_result_not_ready")
        path = Path(job.result["output_path"]).resolve()
        batch_root = (Path(settings.upload_dir) / "batches").resolve()
        if batch_root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="batch_result_not_found")
        return FileResponse(path, media_type="text/csv", filename="policyguard-results.csv")

    def _completed_batch_job(job_id: str, session: Session, tenant: str):
        require_resource(session, "job", job_id, tenant)
        job = PersistentJobQueue(session).get(job_id)
        if job is None or job.job_type != "batch_compliance_review":
            raise HTTPException(status_code=404, detail="batch_job_not_found")
        if job.status != "completed" or not job.result.get("output_path"):
            raise HTTPException(status_code=409, detail="batch_result_not_ready")
        return job

    @application.get("/api/v1/batches/{job_id}/package", tags=["batch-review"])
    def download_batch_package(
        job_id: str,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
    ) -> FileResponse:
        job = _completed_batch_job(job_id, session, tenant)
        records = []
        checkpoint = Path(job.result["output_path"]).parent / "results.jsonl"
        workflow_repository = SqlAlchemyWorkflowRepository(session)
        if checkpoint.is_file():
            for line in checkpoint.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                summary = json.loads(line)
                run = workflow_repository.get(summary["workflow_id"])
                if run:
                    records.append({
                        "workflow_id": run.id,
                        "status": run.status.value,
                        "input": run.input_payload,
                        "result": run.result_payload,
                        "events": [
                            {
                                "sequence": event.sequence,
                                "step": event.step,
                                "status": event.status,
                                "detail": event.detail,
                                "created_at": event.created_at.isoformat(),
                            }
                            for event in run.events
                        ],
                    })
        try:
            package = BatchArtifactWorkspace(Path(settings.upload_dir)).package(
                job.payload["batch_id"],
                input_path=Path(job.payload["path"]),
                workflow_records=records,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(package, media_type="application/zip", filename="review-package.zip")

    @application.get("/api/v1/batches/{job_id}/lifecycle", tags=["batch-review"])
    def batch_artifact_lifecycle(
        job_id: str,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        job = _completed_batch_job(job_id, session, tenant)
        return BatchArtifactWorkspace(Path(settings.upload_dir)).load_manifest(
            job.payload["batch_id"]
        )

    @application.post("/api/v1/batches/{job_id}/deletion", tags=["batch-review"])
    def stage_batch_deletion(
        job_id: str,
        payload: DocumentDeletionRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        job = _completed_batch_job(job_id, session, tenant)
        try:
            return BatchArtifactWorkspace(Path(settings.upload_dir)).stage_deletion(
                job.payload["batch_id"], expected_revision=payload.expected_revision,
                reviewer=payload.reviewer, reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.delete("/api/v1/batches/{job_id}", tags=["batch-review"])
    def confirm_batch_deletion(
        job_id: str,
        payload: DocumentDeletionRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        job = _completed_batch_job(job_id, session, tenant)
        try:
            return BatchArtifactWorkspace(Path(settings.upload_dir)).confirm_deletion(
                job.payload["batch_id"], expected_revision=payload.expected_revision,
                reviewer=payload.reviewer, input_path=Path(job.payload["path"]),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def source_update_response(item: dict) -> SourceUpdateResponse:
        policy = json.loads(Path(item["policy_path"]).read_text(encoding="utf-8"))
        return SourceUpdateResponse(
            source_id=item["source_id"],
            content_hash=item["content_hash"],
            status=item["status"],
            revision=int(item.get("revision", 0)),
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
            temporal_review_status=item.get("temporal_review_status", "missing"),
            generic_heading_rate=item.get("generic_heading_rate"),
            blocking_reasons=item.get("blocking_reasons", []),
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
        "/api/v1/source-updates/{source_id}/{content_hash}/impact",
        tags=["sources"],
    )
    def analyze_source_update_impact(
        source_id: str,
        content_hash: str,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
    ) -> dict:
        if not re.fullmatch(r"[a-z0-9-]{3,100}", source_id) or not re.fullmatch(
            r"[a-f0-9]{64}", content_hash
        ):
            raise HTTPException(status_code=404, detail="source_update_not_found")
        try:
            return analyze_policy_impact(
                session,
                Path("data/update-state/staged"),
                source_id,
                content_hash,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        authenticated_reviewer: str = Depends(require_admin_reviewer),
    ) -> SourceUpdateResponse:
        if not re.fullmatch(r"[a-z0-9-]{3,100}", source_id) or not re.fullmatch(
            r"[a-f0-9]{64}", content_hash
        ):
            raise HTTPException(status_code=404, detail="source_update_not_found")
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
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

    @application.patch(
        "/api/v1/source-updates/{source_id}/{content_hash}/structure",
        response_model=SourceUpdateResponse,
        tags=["sources"],
    )
    def revise_staged_source_structure(
        source_id: str,
        content_hash: str,
        payload: SourceUpdateCorrectionRequest,
        authenticated_reviewer: str = Depends(require_admin_reviewer),
    ) -> SourceUpdateResponse:
        if not re.fullmatch(r"[a-z0-9-]{3,100}", source_id) or not re.fullmatch(
            r"[a-f0-9]{64}", content_hash
        ):
            raise HTTPException(status_code=404, detail="source_update_not_found")
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        try:
            item = revise_staged_source_update(
                Path("data/update-state/staged"), source_id, content_hash,
                reviewer=payload.reviewer,
                expected_revision=payload.expected_revision,
                published_at=payload.published_at,
                effective_from=payload.effective_from,
                heading_overrides=payload.heading_overrides,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        tenant: str = Depends(tenant_identity),
    ) -> DocumentApprovalResponse:
        require_resource(session, "document", document_id, tenant)
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
    def get_document_workspace(
        document_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> DocumentWorkspaceResponse:
        require_resource(session, "document", document_id, tenant)
        try:
            document, manifest = DocumentWorkspace(Path(settings.upload_dir)).load(document_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return DocumentWorkspaceResponse(**document_workspace_payload(document, manifest))

    @application.get(
        "/api/v1/documents/{document_id}/original",
        tags=["documents"],
    )
    def get_original_document(
        document_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> FileResponse:
        require_resource(session, "document", document_id, tenant)
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
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
    ) -> DocumentWorkspaceResponse:
        require_resource(session, "document", document_id, tenant)
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

    @application.delete(
        "/api/v1/documents/{document_id}",
        response_model=DocumentDeletionResponse,
        tags=["documents"],
    )
    def delete_staged_document(
        document_id: str,
        payload: DocumentDeletionRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> DocumentDeletionResponse:
        require_resource(session, "document", document_id, tenant)
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        try:
            event = DocumentWorkspace(Path(settings.upload_dir)).delete_staged(
                document_id,
                expected_revision=payload.expected_revision,
                reviewer=payload.reviewer,
                reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return DocumentDeletionResponse(**event)

    @application.get(
        "/api/v1/operations/dashboard",
        response_model=OperationsDashboardResponse,
        tags=["operations"],
    )
    def operations_dashboard(
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
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
        performance: dict[str, object] = {}
        evidence_path = root / "data/evidence/v1/benchmarks.json"
        if evidence_path.exists():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            portfolio = evidence.get("portfolio_scale", {})
            workflow_metrics = portfolio.get("workflow", {})
            concurrency_metrics = portfolio.get("concurrency", {})
            pdf_metrics = evidence.get("synthetic_pdf_scale", {})
            performance = {
                "as_of": evidence.get("as_of"),
                "traffic_type": portfolio.get("traffic_type"),
                "workflow": {
                    key: workflow_metrics.get(key)
                    for key in (
                        "case_count", "mean_latency_ms", "p50_latency_ms",
                        "p95_latency_ms", "throughput_cases_per_second",
                    )
                },
                "concurrency": {
                    key: concurrency_metrics.get(key)
                    for key in (
                        "case_count", "workers", "success_rate", "mean_latency_ms",
                        "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
                        "throughput_cases_per_second", "scope",
                    )
                },
                "pdf": {
                    key: pdf_metrics.get(key)
                    for key in (
                        "document_count", "page_count", "mean_document_latency_ms",
                        "p95_document_latency_ms", "mean_page_latency_ms",
                    )
                },
            }
        runtime = runtime_summary(session)
        performance["runtime"] = {
            **runtime,
            "dropped_metrics": telemetry.dropped,
        }
        performance["cost"] = estimated_model_cost(
            runtime,
            model_prices_per_million={
                settings.llm_model: settings.llm_input_price_per_million,
                settings.llm_fallback_model: settings.llm_fallback_input_price_per_million,
            },
        )
        owned_workflows = set(session.scalars(
            select(ResourceOwnershipRecord.resource_id).where(
                ResourceOwnershipRecord.resource_type == "workflow",
                ResourceOwnershipRecord.tenant_id == tenant,
            )
        ).all())
        reports = []
        for run in SqlAlchemyWorkflowRepository(session).list_recent(30):
            if tenant_keys and run.id not in owned_workflows:
                continue
            reports.append({
                "workflow_id": run.id,
                "status": run.status.value,
                "title": run.input_payload.get("product", {}).get("title", ""),
                "created_at": run.created_at.isoformat(),
                "report_json": f"/api/v1/workflows/compliance/{run.id}/report?format=json",
                "report_pdf": f"/api/v1/workflows/compliance/{run.id}/report?format=pdf",
            })
        if tenant_keys:
            owned_jobs = set(session.scalars(
                select(ResourceOwnershipRecord.resource_id).where(
                    ResourceOwnershipRecord.resource_type == "job",
                    ResourceOwnershipRecord.tenant_id == tenant,
                )
            ).all())
            tenant_jobs = [
                job for job in PersistentJobQueue(session).list(10_000) if job.id in owned_jobs
            ]
            job_stats = {
                state: sum(job.status == state for job in tenant_jobs)
                for state in {"queued", "running", "retry", "completed", "failed"}
            }
        else:
            job_stats = PersistentJobQueue(session).stats()
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
            jobs=job_stats,
            performance=performance,
            evaluations=evaluations,
            report_history=reports,
        )

    @application.post(
        "/api/v1/evaluations/models",
        response_model=BackgroundJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["model-evaluation"],
    )
    def enqueue_model_evaluation(
        payload: ModelEvaluationRequest,
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        root = Path(__file__).parents[3]
        try:
            dataset_path = validate_evaluation_path(root, payload.dataset)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        allowed = {
            "bm25",
            settings.embedding_model,
            settings.embedding_fallback_model,
        } - {""}
        candidates = list(dict.fromkeys(item.strip() for item in payload.candidates))
        if len(candidates) < 2 or any(item not in allowed for item in candidates):
            raise HTTPException(
                status_code=422,
                detail={"code": "evaluation_candidate_not_allowed", "allowed": sorted(allowed)},
            )
        thresholds = {
            "min_hit_rate_at_k": payload.min_hit_rate_at_k,
            "min_mrr": payload.min_mrr,
            "max_latency_ms": payload.max_latency_ms,
        }
        signature = sha256(json.dumps({
            "tenant": tenant,
            "dataset": str(dataset_path), "candidates": candidates,
            "top_k": payload.top_k, "thresholds": thresholds,
        }, sort_keys=True).encode()).hexdigest()
        job = PersistentJobQueue(session).enqueue(
            "model_evaluation",
            {
                "dataset": payload.dataset,
                "candidates": candidates,
                "top_k": payload.top_k,
                "thresholds": thresholds,
            },
            idempotency_key=f"model-evaluation:{signature}",
            max_attempts=1,
        )
        bind_resource(session, "job", job.id, tenant)
        return BackgroundJobResponse(
            id=job.id, job_type=job.job_type, status=job.status,
            result=job.result, attempts=job.attempts,
            max_attempts=job.max_attempts, error=job.error,
        )

    @application.post(
        "/api/v1/evaluations/models/{job_id}/approve",
        tags=["model-evaluation"],
    )
    def approve_model_evaluation(
        job_id: str,
        payload: ModelPromotionRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        require_resource(session, "job", job_id, tenant)
        job = PersistentJobQueue(session).get(job_id)
        if job is None or job.job_type != "model_evaluation":
            raise HTTPException(status_code=404, detail="model_evaluation_not_found")
        if job.status != "completed":
            raise HTTPException(status_code=409, detail="model_evaluation_not_completed")
        result = job.result
        if result.get("dataset_sha256") != payload.expected_dataset_sha256:
            raise HTTPException(status_code=409, detail="evaluation_dataset_revision_conflict")
        eligible = {
            row["model"] for row in result.get("results", [])
            if row.get("status") == "ok"
            and row.get("hit_rate_at_k", 0) >= result["thresholds"]["min_hit_rate_at_k"]
            and row.get("mean_reciprocal_rank", 0) >= result["thresholds"]["min_mrr"]
            and row.get("latency_ms", float("inf")) <= result["thresholds"]["max_latency_ms"]
        }
        if payload.model not in eligible:
            raise HTTPException(status_code=422, detail="model_does_not_meet_thresholds")
        promotion = {
            "job_id": job_id,
            "model": payload.model,
            "dataset": result["dataset"],
            "dataset_sha256": result["dataset_sha256"],
            "reviewer": payload.reviewer,
            "comment": payload.comment,
            "approved_at": datetime.now(UTC).isoformat(),
            "status": "approved_for_configuration_change",
            "note": "Approval is recorded; environment defaults are never mutated automatically.",
        }
        target = Path(__file__).parents[3] / "data/update-state/model-promotions.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(promotion, ensure_ascii=False) + "\n")
        return promotion

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
        embedding_providers, _ = configured_embedding_chain(provider_settings)
        provider = embedding_providers[0] if embedding_providers else None
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
                reranker = configured_reranker(provider_settings)
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
        embedding_providers, _ = configured_embedding_chain(provider_settings)
        return KnowledgeStatsResponse(
            documents=repository.document_count(),
            chunks=len(repository.list_chunks()),
            retriever="lexical_bm25_cjk_v1",
            embedding_configured=bool(embedding_providers),
        )

    @application.get(
        "/api/v1/knowledge/retrievers",
        response_model=RetrieverStatusResponse,
        tags=["knowledge"],
    )
    def retriever_status() -> RetrieverStatusResponse:
        embedding_providers, initialization_failures = configured_embedding_chain(
            provider_settings
        )
        provider = embedding_providers[0] if embedding_providers else None
        fallback_provider = (
            embedding_providers[1] if len(embedding_providers) > 1 else None
        )
        reranker = configured_reranker(provider_settings)
        query_rewriter = configured_query_rewriter(provider_settings)
        return RetrieverStatusResponse(
            bm25_available=True,
            dense_available=provider is not None,
            dense_provider=provider.provider_name if provider else None,
            dense_model=provider.model_name if provider else None,
            dense_fallback_model=(
                fallback_provider.model_name if fallback_provider else None
            ),
            rerank_available=reranker is not None,
            rerank_provider=reranker.provider_name if reranker else None,
            rerank_model=reranker.model_name if reranker else None,
            query_rewrite_available=query_rewriter is not None,
            query_rewrite_model=query_rewriter.model if query_rewriter else None,
            llm_fallback_model=settings.llm_fallback_model or None,
            provider_max_attempts=settings.provider_max_attempts,
            embedding_initialization_failures=initialization_failures,
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
        tenant: str = Depends(tenant_identity),
    ) -> ComplianceWorkflowResponse:
        knowledge_repository = SqlAlchemyKnowledgeRepository(session)
        workflow_repository = SqlAlchemyWorkflowRepository(session)
        embedding_providers, embedding_failures = configured_embedding_chain(provider_settings)
        retrieval_candidates = [
            HybridRetriever(
                knowledge_repository,
                DenseRetriever(knowledge_repository, provider),
            )
            for provider in embedding_providers
        ]
        retrieval_candidates.append(BM25Retriever(knowledge_repository))
        if embedding_providers or embedding_failures:
            workflow_retriever = FallbackRetriever(
                *retrieval_candidates,
                initial_failures=embedding_failures,
            )
        else:
            workflow_retriever = retrieval_candidates[0]
        run = ComplianceWorkflowService(
            knowledge_repository,
            workflow_repository,
            claim_extractor=configured_claim_extractor(provider_settings),
            retriever=workflow_retriever,
            evidence_verifier=configured_evidence_verifier(provider_settings),
            query_rewriter=configured_query_rewriter(provider_settings),
            query_rewrite_cache=JsonQueryRewriteCache(Path(settings.query_rewrite_cache)),
            model_budget=WorkflowModelBudget(
                max_calls=settings.workflow_model_call_budget,
                max_estimated_input_tokens=settings.workflow_input_token_budget,
            ),
        ).execute(
            product=payload.product.model_dump(),
            markets=payload.markets,
            category=payload.category,
            channel=payload.channel,
            as_of=payload.as_of,
        )
        bind_resource(session, "workflow", run.id, tenant)
        return ComplianceWorkflowResponse.from_domain(run)

    @application.get(
        "/api/v1/workflows/compliance/{run_id}",
        response_model=ComplianceWorkflowResponse,
        tags=["workflows"],
    )
    def get_compliance_workflow(
        run_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> ComplianceWorkflowResponse:
        require_resource(session, "workflow", run_id, tenant)
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
        tenant: str = Depends(tenant_identity),
    ) -> Response:
        require_resource(session, "workflow", run_id, tenant)
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

    @application.get(
        "/api/v1/workflows/compliance/{run_id}/automation-handoff",
        tags=["integrations"],
    )
    def export_automation_handoff(
        run_id: str,
        target: str = Query(pattern="^(rpa|dingtalk)$"),
        session: Session = Depends(get_session),
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "workflow", run_id, tenant)
        run = SqlAlchemyWorkflowRepository(session).get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        if target == "dingtalk":
            return build_dingtalk_preview(
                run, f"/api/v1/workflows/compliance/{run_id}/report?format=pdf"
            )
        try:
            return build_rpa_handoff(run)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post(
        "/api/v1/workflows/compliance/{run_id}/review",
        response_model=ComplianceWorkflowResponse,
        tags=["workflows"],
    )
    def review_compliance_workflow(
        run_id: str,
        payload: WorkflowReviewRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> ComplianceWorkflowResponse:
        require_resource(session, "workflow", run_id, tenant)
        if settings.admin_api_key and payload.reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
        service = ComplianceWorkflowService(
            SqlAlchemyKnowledgeRepository(session),
            SqlAlchemyWorkflowRepository(session),
            claim_extractor=configured_claim_extractor(provider_settings),
            evidence_verifier=configured_evidence_verifier(provider_settings),
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
        tenant: str = Depends(tenant_identity),
    ) -> ComplianceWorkflowResponse:
        require_resource(session, "workflow", run_id, tenant)
        service = RemediationService(
            SqlAlchemyWorkflowRepository(session),
            ToolRegistry([SuggestConservativeRewriteTool()]),
            context_builder=AgentContextBuilder(SqlAlchemyAgentMemoryRepository(session)),
            memory_repository=SqlAlchemyAgentMemoryRepository(session),
        )
        try:
            try:
                execution = choose_remediation_mode(
                    payload.mode,
                    experimental_opt_in=payload.experimental_agent_opt_in,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if execution.mode == "agent":
                planner = configured_agent_planner(provider_settings)
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
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> ComplianceWorkflowResponse:
        require_resource(session, "workflow", run_id, tenant)
        if settings.admin_api_key and payload.approved_by != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")
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
