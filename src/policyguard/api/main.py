import hmac
import json
import re
import tempfile
from asyncio import sleep
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
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from policyguard import __version__
from policyguard.api.schemas import (
    AgentMemoryResponse,
    AgentMemoryReviewRequest,
    BackgroundJobResponse,
    CheckResponse,
    ComplianceWorkflowRequest,
    ComplianceWorkflowResponse,
    CreativeProjectRequest,
    CreativeProjectResponse,
    CreativeReviewRequest,
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
    HarnessMemoryRequest,
    HarnessPermissionRequest,
    HarnessRevisionRequest,
    HarnessRunRequest,
    HealthResponse,
    KnowledgeStatsResponse,
    MarketCompareRequest,
    MarketCompareResponse,
    MarketEvidenceResponse,
    ModelEvaluationRequest,
    ModelPromotionRequest,
    ModelRolloutAdvanceRequest,
    ModelRolloutRollbackRequest,
    ModelRolloutStartRequest,
    MultiAgentRunRequest,
    OperationsDashboardResponse,
    ProductCheckRequest,
    ProductWorkspaceRequest,
    PublishPreflightRequest,
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
    TextSafetyRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    VerificationCodeRequest,
    WorkflowReviewRequest,
)
from policyguard.application.agent import AgentBudget, ControlledAgent, configured_agent_planner
from policyguard.application.agent_context import AgentContextBuilder
from policyguard.application.auth import OIDCAuthenticator, bearer_token
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
from policyguard.application.creative_studio import CreativeStudioWorkspace
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
from policyguard.application.harness_context import estimate_tokens
from policyguard.application.harness_evaluation import evaluate_harness
from policyguard.application.harness_mcp import MCPClientRegistry, MCPServerConfig
from policyguard.application.harness_multi_agent import AgentNode, MultiAgentCoordinator
from policyguard.application.harness_runtime import HarnessRuntime
from policyguard.application.harness_sandbox import DockerSandboxExecutor, SandboxPolicy
from policyguard.application.harness_skills import SkillRegistry
from policyguard.application.hybrid import FallbackRetriever, HybridRetriever
from policyguard.application.jobs import PersistentJobQueue
from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.llm import configured_claim_extractor
from policyguard.application.media_ingestion import MEDIA_TYPES, validate_media
from policyguard.application.model_rollout import ModelRolloutRegistry
from policyguard.application.policy_impact import analyze_policy_impact
from policyguard.application.product_experience import (
    ProductWorkspace,
    publish_preflight,
    scan_text_safety,
    scan_upload,
    system_readiness,
)
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
from policyguard.application.user_auth import LocalAuthService, MailSettings, QQEmailSender
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
    BackgroundJobRecord,
    Database,
    ResourceOwnershipRecord,
    UserRecord,
)
from policyguard.infrastructure.observability import (
    PrometheusRegistry,
    configure_logging,
    configure_otel,
    log_request,
    request_span,
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
    if settings.app_env == "production":
        if settings.auth_code_pepper in {"", "development-only-change-me"}:
            raise RuntimeError("production_auth_code_pepper_required")
        if not settings.session_cookie_secure:
            raise RuntimeError("production_secure_session_cookie_required")
    provider_settings = replace(settings, app_env="test") if database_url else settings
    database = Database(database_url or settings.database_url)
    telemetry = RuntimeMetricBuffer(database.session_factory)
    configure_logging(settings.log_level, json_logs=settings.app_env == "production")
    prometheus = PrometheusRegistry()
    tracer = configure_otel(settings.otel_exporter_otlp_endpoint, "policyguard-api")
    oidc = (
        OIDCAuthenticator(
            settings.oidc_issuer_url,
            settings.oidc_audience,
            roles_claim=settings.oidc_roles_claim,
            tenant_claim=settings.oidc_tenant_claim,
        )
        if settings.oidc_issuer_url
        else None
    )
    model_rollouts = ModelRolloutRegistry(
        Path(settings.upload_dir) / "governance" / "model-rollout.json"
    )

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
    application.state.prometheus = prometheus
    request_windows: dict[str, deque[float]] = defaultdict(deque)
    try:
        tenant_keys = json.loads(settings.tenant_keys_json) if settings.tenant_keys_json else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("tenant_keys_json_invalid") from exc
    if not isinstance(tenant_keys, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in tenant_keys.items()
    ):
        raise RuntimeError("tenant_keys_json_invalid")
    web_dir = Path(__file__).parents[1] / "web"
    application.mount("/static", StaticFiles(directory=web_dir), name="static")

    @application.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(web_dir / "portal.html")

    @application.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(web_dir / "login.html")

    @application.get("/admin", include_in_schema=False)
    def admin_dashboard() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @application.middleware("http")
    async def request_trace(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or uuid4().hex
        request.state.trace_id = trace_id
        started = perf_counter()
        prometheus.begin()
        now = perf_counter()
        client_key = request.client.host if request.client else "local"
        window = request_windows[client_key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= settings.rate_limit_per_minute:
            duration = perf_counter() - started
            prometheus.finish(request.method, "__rate_limited__", 429, duration)
            return JSONResponse(
                {"detail": "rate_limit_exceeded"},
                status_code=429,
                headers={
                    "X-Trace-ID": trace_id,
                    "X-Request-ID": trace_id,
                    "Retry-After": "60",
                },
            )
        if (
            request.method in {"POST", "PATCH", "PUT", "DELETE"}
            and request.cookies.get("pg_session")
            and request.headers.get("origin")
        ):
            origin = request.headers["origin"].rstrip("/")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
            if not hmac.compare_digest(origin, expected_origin):
                return JSONResponse(
                    {"detail": "cross_site_request_rejected"},
                    status_code=403,
                    headers={"X-Trace-ID": trace_id, "X-Request-ID": trace_id},
                )
        window.append(now)
        try:
            with request_span(tracer, f"{request.method} {request.url.path}"):
                response = await call_next(request)
        except Exception:
            duration = perf_counter() - started
            route = getattr(request.scope.get("route"), "path", "__unmatched__")
            prometheus.finish(request.method, route, 500, duration)
            if not request.url.path.startswith("/static"):
                telemetry.record(
                    RuntimeMetric(
                        trace_id=trace_id,
                        method=request.method,
                        path=request.url.path,
                        status_code=500,
                        duration_ms=(perf_counter() - started) * 1000,
                        created_at=datetime.now(UTC),
                    )
                )
            log_request(
                event="http_request",
                trace_id=trace_id,
                method=request.method,
                route=route,
                status=500,
                duration_ms=round(duration * 1000, 3),
            )
            raise
        duration = perf_counter() - started
        route = getattr(request.scope.get("route"), "path", "__unmatched__")
        prometheus.finish(request.method, route, response.status_code, duration)
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Request-ID"] = trace_id
        response.headers["traceparent"] = (
            f"00-{sha256(trace_id.encode()).hexdigest()[:32]}-{uuid4().hex[:16]}-01"
        )
        response.headers["Server-Timing"] = f"app;dur={duration * 1000:.1f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        if not request.url.path.startswith("/static"):
            telemetry.record(
                RuntimeMetric(
                    trace_id=trace_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration * 1000,
                    created_at=datetime.now(UTC),
                )
            )
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            try:
                with database.session_factory() as audit_session:
                    audit_session.add(
                        AuditLogRecord(
                            trace_id=trace_id,
                            method=request.method,
                            path=request.url.path,
                            status_code=response.status_code,
                            actor=request.headers.get("x-reviewer", "local-user"),
                        )
                    )
                    audit_session.commit()
            except Exception:
                pass
        log_request(
            event="http_request",
            trace_id=trace_id,
            method=request.method,
            route=route,
            status=response.status_code,
            duration_ms=round(duration * 1000, 3),
        )
        return response

    def get_session() -> Iterator[Session]:
        yield from database.sessions()

    def local_auth_service(session: Session) -> LocalAuthService:
        sender = QQEmailSender(
            MailSettings(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                authorization_code=settings.smtp_authorization_code,
                from_email=settings.smtp_from_email or settings.smtp_username,
            )
        )
        return LocalAuthService(session, settings.auth_code_pepper, sender)

    def user_response(user: UserRecord) -> UserResponse:
        return UserResponse(
            id=user.id,
            email=user.email,
            role=user.role,
            workflow_uses=user.workflow_uses,
            workflow_limit=user.workflow_limit,
            workflow_remaining=max(user.workflow_limit - user.workflow_uses, 0),
        )

    def authenticated_local_user(request: Request, session: Session) -> UserRecord | None:
        return local_auth_service(session).authenticate(request.cookies.get("pg_session", ""))

    def consume_workflow_quota(request: Request, session: Session) -> None:
        user = authenticated_local_user(request, session)
        if user is None:
            return
        result = session.execute(
            update(UserRecord)
            .where(
                UserRecord.id == user.id,
                UserRecord.workflow_uses < UserRecord.workflow_limit,
            )
            .values(workflow_uses=UserRecord.workflow_uses + 1)
        )
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(status_code=429, detail="workflow_quota_exhausted")
        session.commit()

    def tenant_identity(
        request: Request,
        session: Session = Depends(get_session),
        x_tenant_id: str = Header(default=""),
        x_tenant_key: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> str:
        if oidc is not None:
            try:
                principal = oidc.authenticate(bearer_token(authorization))
            except Exception as exc:
                raise HTTPException(status_code=401, detail="oidc_token_invalid") from exc
            if x_tenant_id.strip() and x_tenant_id.strip() != principal.tenant:
                raise HTTPException(status_code=403, detail="tenant_claim_mismatch")
            return principal.tenant
        local_user = authenticated_local_user(request, session)
        if local_user is not None:
            if x_tenant_id.strip() and x_tenant_id.strip() != local_user.id:
                raise HTTPException(status_code=403, detail="tenant_claim_mismatch")
            return local_user.id
        if settings.app_env == "production":
            raise HTTPException(status_code=401, detail="authentication_required")
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
            session.add(
                ResourceOwnershipRecord(
                    resource_type=resource_type, resource_id=resource_id, tenant_id=tenant
                )
            )
            session.commit()

    def require_resource(
        session: Session, resource_type: str, resource_id: str, tenant: str
    ) -> None:
        ownership = session.get(ResourceOwnershipRecord, (resource_type, resource_id, tenant))
        if ownership is None:
            if tenant == "default" and not tenant_keys:
                return
            raise HTTPException(status_code=404, detail=f"{resource_type}_not_found")

    def ensure_job_capacity(session: Session, tenant: str) -> None:
        owned_ids = select(ResourceOwnershipRecord.resource_id).where(
            ResourceOwnershipRecord.resource_type == "job",
            ResourceOwnershipRecord.tenant_id == tenant,
        )
        active = session.scalar(
            select(func.count())
            .select_from(BackgroundJobRecord)
            .where(
                BackgroundJobRecord.status.in_(["queued", "retry", "running"]),
                BackgroundJobRecord.id.in_(owned_ids),
            )
        )
        if (active or 0) >= settings.max_active_jobs_per_tenant:
            raise HTTPException(status_code=429, detail="tenant_active_job_limit_reached")

    def mcp_registry() -> MCPClientRegistry:
        try:
            payload = json.loads(settings.mcp_servers_json)
            if not isinstance(payload, list):
                raise ValueError("mcp_servers_json_invalid")
            servers = [
                MCPServerConfig(
                    name=item["name"],
                    url=item["url"],
                    api_key=item.get("api_key", ""),
                    timeout_seconds=float(item.get("timeout_seconds", 20)),
                    max_result_bytes=int(item.get("max_result_bytes", 256_000)),
                    allowed_hosts=tuple(item.get("allowed_hosts", [])),
                )
                for item in payload
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("mcp_servers_json_invalid") from exc
        return MCPClientRegistry(servers)

    def harness_runtime(tenant: str) -> tuple[HarnessRuntime, SkillRegistry, DockerSandboxExecutor]:
        sandbox = DockerSandboxExecutor(
            Path(settings.upload_dir) / "harness-sandbox",
            SandboxPolicy(image=settings.harness_sandbox_image),
            enabled=settings.harness_sandbox_enabled,
        )

        def inspect_context(arguments: dict, runtime_context: dict) -> dict:
            return {
                "objective": runtime_context["run"]["objective"],
                "metrics": runtime_context["context"]["metrics"],
                "requested_fields": arguments.get("fields", []),
                "external_side_effect": False,
            }

        def policy_preflight_tool(arguments: dict, runtime_context: dict) -> dict:
            payload = arguments.get("payload") or runtime_context["run"]["context"].get(
                "creative_payload"
            )
            if not payload:
                raise ValueError("harness_creative_payload_required")
            return publish_preflight(payload)

        def sandbox_python(arguments: dict, _: dict) -> dict:
            return sandbox.execute_python(arguments.get("code", ""))

        def mcp_call(arguments: dict, _: dict) -> dict:
            return mcp_registry().call_tool(
                arguments["server"], arguments["tool"], arguments.get("arguments", {})
            )

        tools = {
            "inspect_context": inspect_context,
            "policy_preflight": policy_preflight_tool,
            "sandbox_python": sandbox_python,
            "mcp_call": mcp_call,
        }
        runtime = HarnessRuntime(Path(settings.upload_dir), tenant, tools)
        skills = SkillRegistry(Path(__file__).parents[3] / "config" / "skills", set(tools))
        return runtime, skills, sandbox

    def rollout_provider_settings(tenant: str):
        selected = model_rollouts.select(tenant, provider_settings.llm_model)
        return replace(provider_settings, llm_model=selected) if selected else provider_settings

    def require_admin(
        x_admin_key: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> None:
        if oidc is not None:
            try:
                principal = oidc.authenticate(bearer_token(authorization))
            except Exception as exc:
                raise HTTPException(status_code=401, detail="oidc_token_invalid") from exc
            if not principal.permits("admin"):
                raise HTTPException(status_code=403, detail="admin_role_required")
            return
        if settings.admin_api_key and not hmac.compare_digest(x_admin_key, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="admin_key_required")

    def require_admin_reviewer(
        request: Request,
        session: Session = Depends(get_session),
        x_admin_key: str = Header(default=""),
        x_reviewer: str = Header(default=""),
        authorization: str = Header(default=""),
    ) -> str:
        if oidc is not None:
            try:
                principal = oidc.authenticate(bearer_token(authorization))
            except Exception as exc:
                raise HTTPException(status_code=401, detail="oidc_token_invalid") from exc
            if not principal.permits("reviewer"):
                raise HTTPException(status_code=403, detail="reviewer_role_required")
            return principal.subject
        if settings.admin_api_key:
            admin_match = hmac.compare_digest(x_admin_key, settings.admin_api_key)
            reviewer_match = bool(settings.reviewer_api_key) and hmac.compare_digest(
                x_admin_key, settings.reviewer_api_key
            )
            if not (admin_match or reviewer_match):
                raise HTTPException(status_code=401, detail="reviewer_or_admin_key_required")
        else:
            local_user = authenticated_local_user(request, session)
            if local_user is not None:
                return local_user.email
        reviewer = x_reviewer.strip()
        if settings.admin_api_key and not reviewer:
            raise HTTPException(status_code=401, detail="reviewer_identity_required")
        return reviewer or "local-reviewer"

    def ensure_reviewer_identity(reviewer: str, authenticated_reviewer: str) -> None:
        identity_enforced = (
            settings.admin_api_key or oidc is not None or settings.app_env == "production"
        )
        if identity_enforced and reviewer != authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_mismatch")

    @application.get("/metrics", response_class=PlainTextResponse, tags=["system"])
    def metrics(
        authorization: str = Header(default=""),
        x_metrics_key: str = Header(default=""),
    ) -> PlainTextResponse:
        if settings.metrics_api_key:
            supplied = x_metrics_key
            if authorization:
                try:
                    supplied = bearer_token(authorization)
                except ValueError:
                    supplied = ""
            if not hmac.compare_digest(supplied, settings.metrics_api_key):
                raise HTTPException(status_code=401, detail="metrics_key_required")
        elif settings.app_env == "production":
            raise HTTPException(status_code=503, detail="metrics_key_not_configured")
        return PlainTextResponse(prometheus.render(), media_type="text/plain; version=0.0.4")

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            ai_enabled=database_url is None
            and settings.app_env != "test"
            and bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model),
        )

    @application.post("/api/v1/auth/verification-code", status_code=202, tags=["auth"])
    def request_verification_code(
        payload: VerificationCodeRequest, session: Session = Depends(get_session)
    ) -> dict:
        try:
            local_auth_service(session).request_code(payload.email)
        except ValueError as exc:
            code = str(exc)
            status_code = 429 if code == "verification_code_cooldown" else 400
            raise HTTPException(status_code=status_code, detail=code) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"accepted": True}

    @application.post("/api/v1/auth/register", response_model=UserResponse, tags=["auth"])
    def register_user(
        payload: UserRegisterRequest,
        response: Response,
        session: Session = Depends(get_session),
    ) -> UserResponse:
        try:
            user, token = local_auth_service(session).register(payload.email, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response.set_cookie(
            "pg_session",
            token,
            max_age=7 * 24 * 3600,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="lax",
            path="/",
        )
        return user_response(user)

    @application.post("/api/v1/auth/login", response_model=UserResponse, tags=["auth"])
    def login_user(
        payload: UserLoginRequest,
        response: Response,
        session: Session = Depends(get_session),
    ) -> UserResponse:
        try:
            user, token = local_auth_service(session).login(payload.email, payload.password)
        except ValueError as exc:
            code = str(exc)
            status_code = 423 if code == "account_temporarily_locked" else 401
            raise HTTPException(status_code=status_code, detail=code) from exc
        response.set_cookie(
            "pg_session",
            token,
            max_age=7 * 24 * 3600,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="lax",
            path="/",
        )
        return user_response(user)

    @application.get("/api/v1/auth/me", response_model=UserResponse, tags=["auth"])
    def current_user(request: Request, session: Session = Depends(get_session)) -> UserResponse:
        user = authenticated_local_user(request, session)
        if user is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        return user_response(user)

    @application.get("/api/v1/auth/status", tags=["auth"])
    def authentication_status(
        request: Request, session: Session = Depends(get_session)
    ) -> dict[str, bool]:
        return {
            "authenticated": authenticated_local_user(request, session) is not None,
            "required": settings.app_env == "production" and oidc is None,
        }

    @application.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
    def logout_user(request: Request, response: Response, session: Session = Depends(get_session)):
        local_auth_service(session).logout(request.cookies.get("pg_session", ""))
        response.delete_cookie("pg_session", path="/", secure=settings.session_cookie_secure)
        response.status_code = 204
        return response

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
        intake = scan_upload(file.filename or "upload.pdf", content, max_bytes=20 * 1024 * 1024)
        if intake["status"] == "blocked":
            raise HTTPException(status_code=422, detail=intake)
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
        document_id, chunks = stage_parsed_document(parsed, route, Path(settings.upload_dir))
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
        ensure_job_capacity(session, tenant)
        job = PersistentJobQueue(session).enqueue(
            "parse_document",
            {
                "path": str(raw_path),
                "filename": file.filename or "upload.pdf",
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
        owned_ids = set(
            session.scalars(
                select(ResourceOwnershipRecord.resource_id).where(
                    ResourceOwnershipRecord.resource_type == "job",
                    ResourceOwnershipRecord.tenant_id == tenant,
                )
            ).all()
        )
        return [
            BackgroundJobResponse(
                id=job.id,
                job_type=job.job_type,
                status=job.status,
                result=job.result,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                error=job.error,
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
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            result=job.result,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error=job.error,
        )

    @application.post(
        "/api/v1/jobs/{job_id}/cancel",
        response_model=BackgroundJobResponse,
        tags=["jobs"],
    )
    def cancel_background_job(
        job_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        require_resource(session, "job", job_id, tenant)
        try:
            job = PersistentJobQueue(session).cancel(job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        "/api/v1/dead-letter-jobs",
        response_model=list[BackgroundJobResponse],
        tags=["jobs"],
    )
    def list_dead_letter_jobs(
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> list[BackgroundJobResponse]:
        owned_ids = set(
            session.scalars(
                select(ResourceOwnershipRecord.resource_id).where(
                    ResourceOwnershipRecord.resource_type == "job",
                    ResourceOwnershipRecord.tenant_id == tenant,
                )
            ).all()
        )
        return [
            BackgroundJobResponse(
                id=job.id,
                job_type=job.job_type,
                status=job.status,
                result=job.result,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                error=job.error,
            )
            for job in PersistentJobQueue(session).list(200)
            if job.status == "failed"
            and ((not tenant_keys and tenant == "default") or job.id in owned_ids)
        ]

    @application.get("/api/v1/batches/template", tags=["batch-review"])
    def batch_review_template() -> PlainTextResponse:
        return PlainTextResponse(
            "external_id,category,title,description,markets\nSKU-001,beauty,商品标题,商品描述,CN\n",
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
        intake = scan_upload(file.filename or f"upload{suffix}", content, max_bytes=5 * 1024 * 1024)
        if intake["status"] == "blocked":
            raise HTTPException(status_code=422, detail=intake)
        digest = sha256(content).hexdigest()
        batch_id = sha256(f"{tenant}:{digest}".encode()).hexdigest()[:20]
        inbox = Path(settings.upload_dir) / "batches" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        path = inbox / f"{batch_id}{suffix}"
        if not path.exists():
            path.write_bytes(content)
        ensure_job_capacity(session, tenant)
        job = PersistentJobQueue(session).enqueue(
            "batch_compliance_review",
            {"batch_id": batch_id, "path": str(path.resolve()), "filename": file.filename},
            idempotency_key=f"batch-review:{tenant}:{digest}",
            max_attempts=5,
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
        intake = scan_upload(file.filename or f"upload{suffix}", content, max_bytes=limit)
        if intake["status"] == "blocked":
            raise HTTPException(status_code=422, detail=intake)
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
        ensure_job_capacity(session, tenant)
        job = PersistentJobQueue(session).enqueue(
            "media_claim_extraction",
            {"path": str(path.resolve()), "filename": file.filename, "tenant_id": tenant},
            idempotency_key=f"media-claims:{tenant}:{digest}",
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

    @application.get("/api/v1/readiness", tags=["experience"])
    def readiness(
        session: Session = Depends(get_session),
        _: str = Depends(tenant_identity),
    ) -> dict:
        return system_readiness(settings, session)

    @application.get("/api/v1/workspace/summary", tags=["experience"])
    def workspace_summary(
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        products = ProductWorkspace(Path(settings.upload_dir), tenant).list()
        jobs = PersistentJobQueue(session).list(limit=100)
        owned_job_ids = set(
            session.scalars(
                select(ResourceOwnershipRecord.resource_id).where(
                    ResourceOwnershipRecord.resource_type == "job",
                    ResourceOwnershipRecord.tenant_id == tenant,
                )
            ).all()
        )
        tenant_jobs = [job for job in jobs if job.id in owned_job_ids]
        return {
            "products": len(products),
            "active_jobs": sum(job.status in {"queued", "retry", "running"} for job in tenant_jobs),
            "failed_jobs": sum(job.status == "failed" for job in tenant_jobs),
            "recent_products": products[:5],
            "capacity": {
                "active_jobs": sum(
                    job.status in {"queued", "retry", "running"} for job in tenant_jobs
                ),
                "active_jobs_limit": settings.max_active_jobs_per_tenant,
                "max_upload_bytes": settings.max_upload_bytes,
            },
        }

    @application.get("/api/v1/products", tags=["product-center"])
    def list_products(tenant: str = Depends(tenant_identity)) -> list[dict]:
        return ProductWorkspace(Path(settings.upload_dir), tenant).list()

    @application.get("/api/v1/products/{external_id}", tags=["product-center"])
    def get_product(
        external_id: str,
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        try:
            return ProductWorkspace(Path(settings.upload_dir), tenant).get(external_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.put("/api/v1/products/{external_id}", tags=["product-center"])
    def save_product(
        external_id: str,
        payload: ProductWorkspaceRequest,
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        if external_id != payload.external_id:
            raise HTTPException(status_code=422, detail="product_identifier_mismatch")
        content = payload.model_dump(exclude={"expected_revision"})
        safety = scan_text_safety(content)
        if safety["prompt_injection"]:
            raise HTTPException(status_code=422, detail="product_prompt_injection_detected")
        try:
            return ProductWorkspace(Path(settings.upload_dir), tenant).save(
                content, payload.expected_revision
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.delete("/api/v1/products/{external_id}", tags=["product-center"])
    def delete_product(
        external_id: str,
        expected_revision: int = Query(ge=1),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        try:
            return ProductWorkspace(Path(settings.upload_dir), tenant).delete(
                external_id, expected_revision
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/v1/safety/text-scan", tags=["safety"])
    def text_safety_scan(
        payload: TextSafetyRequest,
        _: str = Depends(tenant_identity),
    ) -> dict:
        return scan_text_safety(payload.content)

    @application.post("/api/v1/safety/upload-scan", tags=["safety"])
    async def upload_safety_scan(
        file: UploadFile = File(...),
        _: str = Depends(tenant_identity),
    ) -> dict:
        content = await file.read(settings.max_upload_bytes + 1)
        result = scan_upload(
            file.filename or "upload", content, max_bytes=settings.max_upload_bytes
        )
        if result["status"] == "blocked":
            raise HTTPException(status_code=422, detail=result)
        return result

    @application.post("/api/v1/publish-preflight", tags=["creative-studio"])
    def run_publish_preflight(
        payload: PublishPreflightRequest,
        _: str = Depends(tenant_identity),
    ) -> dict:
        try:
            return publish_preflight(payload.model_dump(by_alias=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/v1/harness/skills", tags=["agent-harness"])
    def list_harness_skills(
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        _, registry, _ = harness_runtime(tenant)
        skills, failures = registry.discover()
        return {
            "skills": [
                {
                    "name": skill.name,
                    "version": skill.version,
                    "description": skill.description,
                    "permissions": list(skill.permissions),
                    "step_count": len(skill.plan),
                }
                for skill in skills
            ],
            "failures": failures,
        }

    @application.get("/api/v1/harness/sandbox/readiness", tags=["agent-harness"])
    def harness_sandbox_readiness(
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        _, _, sandbox = harness_runtime(tenant)
        return sandbox.readiness()

    @application.get("/api/v1/harness/mcp/servers", tags=["agent-harness"])
    def list_harness_mcp_servers(_: str = Depends(tenant_identity)) -> list[dict]:
        registry = mcp_registry()
        return [
            {
                "name": server.name,
                "url": server.url,
                "configured": True,
                "max_result_bytes": server.max_result_bytes,
            }
            for server in registry.servers.values()
        ]

    @application.get("/api/v1/harness/runs", tags=["agent-harness"])
    def list_harness_runs(
        limit: int = Query(default=30, ge=1, le=100),
        tenant: str = Depends(tenant_identity),
    ) -> list[dict]:
        runtime, _, _ = harness_runtime(tenant)
        return runtime.list(limit)

    @application.post(
        "/api/v1/harness/runs",
        status_code=status.HTTP_201_CREATED,
        tags=["agent-harness"],
    )
    def create_harness_run(
        payload: HarnessRunRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        runtime, registry, _ = harness_runtime(tenant)
        if payload.skill:
            try:
                plan = list(registry.get(payload.skill).plan)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        else:
            plan = [step.model_dump(exclude_none=True) for step in payload.plan] or [
                {"type": "tool", "tool": "inspect_context", "arguments": {}},
                {"type": "finish", "result": {"human_review_required": True}},
            ]
        try:
            run = runtime.create(
                objective=payload.objective,
                context=payload.context,
                plan=plan,
                budgets={
                    "max_steps": payload.max_steps,
                    "max_tool_calls": payload.max_tool_calls,
                    "max_tokens": payload.max_tokens,
                    "max_cost_microusd": payload.max_cost_microusd,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        bind_resource(session, "harness", run["run_id"], tenant)
        return run

    @application.get("/api/v1/harness/runs/{run_id}", tags=["agent-harness"])
    def get_harness_run(
        run_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        try:
            return harness_runtime(tenant)[0].load(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def harness_action(
        action: str,
        run_id: str,
        expected_revision: int,
        tenant: str,
        reason: str = "user_requested",
    ) -> dict:
        runtime = harness_runtime(tenant)[0]
        try:
            if action == "advance":
                return runtime.advance(run_id, expected_revision)
            if action == "run":
                return runtime.run_until_boundary(run_id, expected_revision)
            if action == "pause":
                return runtime.pause(run_id, expected_revision, reason)
            if action == "resume":
                return runtime.resume(run_id, expected_revision)
            return runtime.cancel(run_id, expected_revision)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/v1/harness/runs/{run_id}/advance", tags=["agent-harness"])
    def advance_harness_run(
        run_id: str,
        payload: HarnessRevisionRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        return harness_action("advance", run_id, payload.expected_revision, tenant)

    @application.post("/api/v1/harness/runs/{run_id}/run", tags=["agent-harness"])
    def execute_harness_run(
        run_id: str,
        payload: HarnessRevisionRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        return harness_action("run", run_id, payload.expected_revision, tenant)

    @application.post("/api/v1/harness/runs/{run_id}/pause", tags=["agent-harness"])
    def pause_harness_run(
        run_id: str,
        payload: HarnessRevisionRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        return harness_action("pause", run_id, payload.expected_revision, tenant, payload.reason)

    @application.post("/api/v1/harness/runs/{run_id}/resume", tags=["agent-harness"])
    def resume_harness_run(
        run_id: str,
        payload: HarnessRevisionRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        return harness_action("resume", run_id, payload.expected_revision, tenant)

    @application.post("/api/v1/harness/runs/{run_id}/cancel", tags=["agent-harness"])
    def cancel_harness_run(
        run_id: str,
        payload: HarnessRevisionRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        return harness_action("cancel", run_id, payload.expected_revision, tenant)

    @application.post("/api/v1/harness/runs/{run_id}/permissions", tags=["agent-harness"])
    def approve_harness_permission(
        run_id: str,
        payload: HarnessPermissionRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            return harness_runtime(tenant)[0].approve_permission(
                run_id,
                payload.expected_revision,
                payload.permission,
                payload.reviewer,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/v1/harness/runs/{run_id}/memory", tags=["agent-harness"])
    def update_harness_memory(
        run_id: str,
        payload: HarnessMemoryRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        require_resource(session, "harness", run_id, tenant)
        if payload.tier == "long_term" and settings.admin_api_key and not authenticated_reviewer:
            raise HTTPException(status_code=403, detail="reviewer_identity_required")
        try:
            return harness_runtime(tenant)[0].put_memory(
                run_id,
                payload.expected_revision,
                payload.tier,
                payload.key,
                payload.value,
                payload.reviewed,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/v1/harness/runs/{run_id}/events", tags=["agent-harness"])
    async def stream_harness_events(
        request: Request,
        run_id: str,
        after: int = Query(default=0, ge=0),
        follow: bool = Query(default=False),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> StreamingResponse:
        require_resource(session, "harness", run_id, tenant)
        runtime = harness_runtime(tenant)[0]

        async def event_stream():
            cursor = after
            polls = 0
            while True:
                events = runtime.events(run_id, cursor)
                for event in events:
                    cursor = event["sequence"]
                    yield (
                        f"id: {cursor}\nevent: {event['type']}\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
                if not follow or await request.is_disconnected() or polls >= 120:
                    break
                polls += 1
                if not events:
                    yield ": keepalive\n\n"
                await sleep(0.5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/api/v1/harness/multi-agent", tags=["agent-harness"])
    def run_multi_agent(
        payload: MultiAgentRunRequest,
        _: str = Depends(tenant_identity),
    ) -> dict:
        coordinator = MultiAgentCoordinator(
            max_agents=4,
            max_messages=payload.max_messages,
            max_tokens=payload.max_tokens,
        )
        nodes = [
            AgentNode(item.agent_id, item.role, item.task, tuple(item.depends_on))
            for item in payload.nodes
        ]

        def deterministic_agent(node: AgentNode, messages: list[dict], remaining: int) -> dict:
            content = {
                "role": node.role,
                "task": node.task,
                "received": [item["from"] for item in messages],
                "human_review_required": True,
            }
            return {**content, "tokens": min(estimate_tokens(content), remaining)}

        try:
            return coordinator.execute(nodes, deterministic_agent)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/api/v1/harness/evaluations/run", tags=["agent-harness"])
    def run_harness_evaluation(
        _: None = Depends(require_admin),
        tenant: str = Depends(tenant_identity),
    ) -> dict:
        dataset = json.loads(
            (Path(__file__).parents[3] / "data" / "evaluation" / "harness-v1.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="harness-eval-", dir=Path(settings.upload_dir).resolve()
        ) as temporary:

            def runner(case: dict) -> dict:
                runtime = harness_runtime(tenant)[0]
                isolated = HarnessRuntime(Path(temporary), tenant, runtime.tools)
                run = isolated.create(
                    objective=case["objective"],
                    context={"evaluation": True},
                    plan=case["plan"],
                    budgets={"max_steps": 8, "max_tool_calls": 6, "max_tokens": 12_000},
                )
                return isolated.run_until_boundary(run["run_id"], run["revision"])

            return {"dataset": dataset["version"], **evaluate_harness(dataset["cases"], runner)}

    @application.post(
        "/api/v1/creatives",
        response_model=CreativeProjectResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["creative-studio"],
    )
    def create_creative_project(
        payload: CreativeProjectRequest,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> CreativeProjectResponse:
        project_payload = payload.model_dump()
        query = " ".join(
            [payload.product_name, payload.category, "advertising claims"]
            + [fact.value for fact in payload.verified_facts]
        )
        hits = BM25Retriever(SqlAlchemyKnowledgeRepository(session)).search(
            query,
            top_k=3,
            scope=KnowledgeFilter(
                jurisdiction=payload.market,
                category=payload.category,
                channel="all",
            ),
        )
        project_payload["policy_evidence"] = [
            {
                "section_id": hit.chunk.section_id,
                "heading": hit.chunk.heading,
                "quote": hit.chunk.text[:800],
                "source_url": hit.chunk.source_url,
                "status": "retrieval_candidate_requires_human_review",
            }
            for hit in hits
        ]
        try:
            project = CreativeStudioWorkspace(Path(settings.upload_dir)).create(project_payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        bind_resource(session, "creative", project["project_id"], tenant)
        return CreativeProjectResponse(**project)

    @application.get(
        "/api/v1/creatives/{project_id}",
        response_model=CreativeProjectResponse,
        tags=["creative-studio"],
    )
    def get_creative_project(
        project_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> CreativeProjectResponse:
        require_resource(session, "creative", project_id, tenant)
        try:
            project = CreativeStudioWorkspace(Path(settings.upload_dir)).load(project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return CreativeProjectResponse(**project)

    @application.post(
        "/api/v1/creatives/{project_id}/source-image",
        response_model=CreativeProjectResponse,
        tags=["creative-studio"],
    )
    async def upload_creative_source_image(
        project_id: str,
        file: UploadFile = File(...),
        expected_revision: int = Query(ge=0),
        reviewer: str = Query(min_length=1, max_length=100),
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> CreativeProjectResponse:
        require_resource(session, "creative", project_id, tenant)
        ensure_reviewer_identity(reviewer, authenticated_reviewer)
        suffix = Path(file.filename or "").suffix.lower()
        content = await file.read(15 * 1024 * 1024 + 1)
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="creative_source_image_too_large")
        try:
            project = CreativeStudioWorkspace(Path(settings.upload_dir)).add_source_image(
                project_id,
                content=content,
                suffix=suffix,
                expected_revision=expected_revision,
                reviewer=reviewer,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return CreativeProjectResponse(**project)

    @application.post(
        "/api/v1/creatives/{project_id}/review",
        response_model=CreativeProjectResponse,
        tags=["creative-studio"],
    )
    def review_creative_project(
        project_id: str,
        payload: CreativeReviewRequest,
        session: Session = Depends(get_session),
        authenticated_reviewer: str = Depends(require_admin_reviewer),
        tenant: str = Depends(tenant_identity),
    ) -> CreativeProjectResponse:
        require_resource(session, "creative", project_id, tenant)
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            project = CreativeStudioWorkspace(Path(settings.upload_dir)).review(
                project_id,
                expected_revision=payload.expected_revision,
                reviewer=payload.reviewer,
                decision=payload.decision,
                approved_copy_indexes=payload.approved_copy_indexes,
                comment=payload.comment,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return CreativeProjectResponse(**project)

    @application.post(
        "/api/v1/creatives/{project_id}/scene",
        response_model=BackgroundJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["creative-studio"],
    )
    def enqueue_creative_scene(
        project_id: str,
        expected_revision: int = Query(ge=0),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> BackgroundJobResponse:
        require_resource(session, "creative", project_id, tenant)
        if not (settings.image_generation_base_url and settings.image_generation_model):
            raise HTTPException(status_code=503, detail="image_generation_provider_not_configured")
        try:
            project = CreativeStudioWorkspace(Path(settings.upload_dir)).load(project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if project["revision"] != expected_revision:
            raise HTTPException(status_code=409, detail="creative_project_revision_conflict")
        if project["status"] != "review_required" or not project.get("source_image"):
            raise HTTPException(status_code=409, detail="creative_source_image_required")
        ensure_job_capacity(session, tenant)
        job = PersistentJobQueue(session).enqueue(
            "creative_scene_generation",
            {"project_id": project_id, "expected_revision": expected_revision},
            idempotency_key=(
                f"creative-scene:{tenant}:{project_id}:{expected_revision}:"
                f"{settings.image_generation_model}"
            ),
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
        "/api/v1/creatives/{project_id}/assets/{filename}",
        tags=["creative-studio"],
    )
    def get_creative_asset(
        project_id: str,
        filename: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> FileResponse:
        require_resource(session, "creative", project_id, tenant)
        workspace = CreativeStudioWorkspace(Path(settings.upload_dir))
        project = workspace.load(project_id)
        allowed = {item["filename"] for item in project["assets"]}
        if filename not in allowed or Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="creative_asset_not_found")
        path = workspace.directory(project_id) / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="creative_asset_not_found")
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media_type, filename=filename)

    @application.get(
        "/api/v1/creatives/{project_id}/package",
        tags=["creative-studio"],
    )
    def download_creative_package(
        project_id: str,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> FileResponse:
        require_resource(session, "creative", project_id, tenant)
        try:
            package = CreativeStudioWorkspace(Path(settings.upload_dir)).package(project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(package, media_type="application/zip", filename="approved-assets.zip")

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
            staged = TableCleaningWorkspace(Path(settings.upload_dir) / "tables").stage(
                content, file.filename or f"upload{suffix}"
            )
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            confirmed = TableCleaningWorkspace(Path(settings.upload_dir) / "tables").confirm(
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
        ensure_job_capacity(session, tenant)
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
            manifest, _ = TableCleaningWorkspace(Path(settings.upload_dir) / "tables").load(
                table_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(manifest.get("report_path", ""))
        table_directory = (Path(settings.upload_dir) / "tables" / table_id).resolve()
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
                    records.append(
                        {
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
                        }
                    )
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        job = _completed_batch_job(job_id, session, tenant)
        try:
            return BatchArtifactWorkspace(Path(settings.upload_dir)).stage_deletion(
                job.payload["batch_id"],
                expected_revision=payload.expected_revision,
                reviewer=payload.reviewer,
                reason=payload.reason,
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        job = _completed_batch_job(job_id, session, tenant)
        try:
            return BatchArtifactWorkspace(Path(settings.upload_dir)).confirm_deletion(
                job.payload["batch_id"],
                expected_revision=payload.expected_revision,
                reviewer=payload.reviewer,
                input_path=Path(job.payload["path"]),
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
        updates = list_staged_source_updates(Path("data/update-state/staged"), latest_only=False)
        item = next(
            (
                update
                for update in updates
                if update["source_id"] == source_id and update["content_hash"] == content_hash
            ),
            None,
        )
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            item = approve_source_update(
                Path("data/update-state/staged"),
                source_id,
                content_hash,
                payload.reviewer,
                SqlAlchemyKnowledgeRepository(session),
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            item = revise_staged_source_update(
                Path("data/update-state/staged"),
                source_id,
                content_hash,
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
            "source_monitor",
            {},
            idempotency_key=f"source-monitor:{bucket}",
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
                DocumentWorkspace(Path(settings.upload_dir)).directory(document_id) / "original.pdf"
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
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
                        "case_count",
                        "mean_latency_ms",
                        "p50_latency_ms",
                        "p95_latency_ms",
                        "throughput_cases_per_second",
                    )
                },
                "concurrency": {
                    key: concurrency_metrics.get(key)
                    for key in (
                        "case_count",
                        "workers",
                        "success_rate",
                        "mean_latency_ms",
                        "p50_latency_ms",
                        "p95_latency_ms",
                        "p99_latency_ms",
                        "throughput_cases_per_second",
                        "scope",
                    )
                },
                "pdf": {
                    key: pdf_metrics.get(key)
                    for key in (
                        "document_count",
                        "page_count",
                        "mean_document_latency_ms",
                        "p95_document_latency_ms",
                        "mean_page_latency_ms",
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
        owned_workflows = set(
            session.scalars(
                select(ResourceOwnershipRecord.resource_id).where(
                    ResourceOwnershipRecord.resource_type == "workflow",
                    ResourceOwnershipRecord.tenant_id == tenant,
                )
            ).all()
        )
        reports = []
        for run in SqlAlchemyWorkflowRepository(session).list_recent(30):
            if tenant_keys and run.id not in owned_workflows:
                continue
            reports.append(
                {
                    "workflow_id": run.id,
                    "status": run.status.value,
                    "title": run.input_payload.get("product", {}).get("title", ""),
                    "created_at": run.created_at.isoformat(),
                    "report_json": f"/api/v1/workflows/compliance/{run.id}/report?format=json",
                    "report_pdf": f"/api/v1/workflows/compliance/{run.id}/report?format=pdf",
                }
            )
        if tenant_keys:
            owned_jobs = set(
                session.scalars(
                    select(ResourceOwnershipRecord.resource_id).where(
                        ResourceOwnershipRecord.resource_type == "job",
                        ResourceOwnershipRecord.tenant_id == tenant,
                    )
                ).all()
            )
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
            "BAAI/bge-m3",
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
        signature = sha256(
            json.dumps(
                {
                    "tenant": tenant,
                    "dataset": str(dataset_path),
                    "candidates": candidates,
                    "top_k": payload.top_k,
                    "thresholds": thresholds,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        ensure_job_capacity(session, tenant)
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
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            result=job.result,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error=job.error,
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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
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
            row["model"]
            for row in result.get("results", [])
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

    @application.get("/api/v1/model-rollouts/current", tags=["model-rollout"])
    def get_model_rollout(_: None = Depends(require_admin)) -> dict:
        return model_rollouts.load()

    @application.post("/api/v1/model-rollouts", tags=["model-rollout"])
    def start_model_rollout(
        payload: ModelRolloutStartRequest,
        authenticated_reviewer: str = Depends(require_admin_reviewer),
    ) -> dict:
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        if payload.baseline_model == payload.candidate_model:
            raise HTTPException(status_code=422, detail="rollout_models_must_differ")
        try:
            return model_rollouts.start(
                expected_revision=payload.expected_revision,
                baseline_model=payload.baseline_model,
                candidate_model=payload.candidate_model,
                baseline_metrics=payload.baseline_metrics.model_dump(),
                candidate_metrics=payload.candidate_metrics.model_dump(),
                reviewer=payload.reviewer,
            )
        except ValueError as exc:
            code = 409 if "revision_conflict" in str(exc) else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @application.post("/api/v1/model-rollouts/advance", tags=["model-rollout"])
    def advance_model_rollout(
        payload: ModelRolloutAdvanceRequest,
        authenticated_reviewer: str = Depends(require_admin_reviewer),
    ) -> dict:
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            return model_rollouts.advance(
                payload.expected_revision,
                payload.metrics.model_dump(),
                payload.reviewer,
            )
        except ValueError as exc:
            code = 409 if "revision_conflict" in str(exc) else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @application.post("/api/v1/model-rollouts/rollback", tags=["model-rollout"])
    def rollback_model_rollout(
        payload: ModelRolloutRollbackRequest,
        authenticated_reviewer: str = Depends(require_admin_reviewer),
    ) -> dict:
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        try:
            return model_rollouts.rollback(
                payload.expected_revision, payload.reviewer, payload.reason
            )
        except ValueError as exc:
            code = 409 if "revision_conflict" in str(exc) else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    def evaluation_review_service(session: Session) -> EvaluationReviewService:
        return EvaluationReviewService(
            session,
            SqlAlchemyKnowledgeRepository(session),
            Path(__file__).parents[3] / "data/evaluation/rag-cross-lingual-zh-en-v1.json",
        )

    @application.get(
        "/api/v1/evaluations/cross-language/reviews",
        response_model=list[EvaluationReviewResponse],
        tags=["evaluation-review"],
    )
    def list_cross_language_reviews(
        session: Session = Depends(get_session),
    ) -> list[EvaluationReviewResponse]:
        return [
            EvaluationReviewResponse(**item)
            for item in evaluation_review_service(session).list_items()
        ]

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
                sample_id,
                payload.decision,
                payload.reviewer,
                payload.expected_section_id,
                payload.comment,
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
                "pending": sum(
                    item["review_status"] == "pending_human_review" for item in evaluation_items
                ),
                "items": evaluation_items,
            },
            "legal_sources": {
                "pending": sum(
                    item.get("legal_review_status") == "pending" for item in staged_updates
                ),
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

    @application.get("/api/v1/checks/{check_id}", response_model=CheckResponse, tags=["checks"])
    def get_check(
        check_id: str,
        session: Session = Depends(get_session),
    ) -> CheckResponse:
        repository = SqlAlchemyComplianceRepository(session)
        result = repository.get_check(check_id)
        if result is None:
            raise HTTPException(status_code=404, detail="check_not_found")
        return CheckResponse.from_domain(result)

    @application.get("/api/v1/knowledge/search", response_model=SearchResponse, tags=["knowledge"])
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
                retriever_name = f"hybrid_rrf_rerank:{provider.model_name}+{reranker.model_name}"
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
        embedding_providers, initialization_failures = configured_embedding_chain(provider_settings)
        provider = embedding_providers[0] if embedding_providers else None
        fallback_provider = embedding_providers[1] if len(embedding_providers) > 1 else None
        reranker = configured_reranker(provider_settings)
        query_rewriter = configured_query_rewriter(provider_settings)
        return RetrieverStatusResponse(
            bm25_available=True,
            dense_available=provider is not None,
            dense_provider=provider.provider_name if provider else None,
            dense_model=provider.model_name if provider else None,
            dense_fallback_model=(fallback_provider.model_name if fallback_provider else None),
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
        request: Request,
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> ComplianceWorkflowResponse:
        consume_workflow_quota(request, session)
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
            claim_extractor=configured_claim_extractor(rollout_provider_settings(tenant)),
            retriever=workflow_retriever,
            evidence_verifier=configured_evidence_verifier(rollout_provider_settings(tenant)),
            query_rewriter=configured_query_rewriter(rollout_provider_settings(tenant)),
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
        "/api/v1/workflows/compliance",
        response_model=list[ComplianceWorkflowResponse],
        tags=["workflows"],
    )
    def list_compliance_workflows(
        limit: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(get_session),
        tenant: str = Depends(tenant_identity),
    ) -> list[ComplianceWorkflowResponse]:
        owned_ids = set(
            session.scalars(
                select(ResourceOwnershipRecord.resource_id).where(
                    ResourceOwnershipRecord.resource_type == "workflow",
                    ResourceOwnershipRecord.tenant_id == tenant,
                )
            ).all()
        )
        return [
            ComplianceWorkflowResponse.from_domain(run)
            for run in SqlAlchemyWorkflowRepository(session).list_recent(limit=limit)
            if run.id in owned_ids
        ]

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
        ensure_reviewer_identity(payload.reviewer, authenticated_reviewer)
        service = ComplianceWorkflowService(
            SqlAlchemyKnowledgeRepository(session),
            SqlAlchemyWorkflowRepository(session),
            claim_extractor=configured_claim_extractor(rollout_provider_settings(tenant)),
            evidence_verifier=configured_evidence_verifier(rollout_provider_settings(tenant)),
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
                planner = configured_agent_planner(rollout_provider_settings(tenant))
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
        if (
            settings.admin_api_key or oidc is not None or settings.app_env == "production"
        ) and payload.approved_by != authenticated_reviewer:
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
                id=item.id,
                run_id=item.run_id,
                task_type=item.task_type,
                jurisdictions=list(item.jurisdictions),
                category=item.category,
                channel=item.channel,
                summary=item.summary,
                outcome=item.outcome,
                source_versions=item.source_versions,
                reviewed_by=item.reviewed_by,
                review_status=item.review_status,
                invalidated_reason=item.invalidated_reason,
                created_at=item.created_at,
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
            id=item.id,
            run_id=item.run_id,
            task_type=item.task_type,
            jurisdictions=list(item.jurisdictions),
            category=item.category,
            channel=item.channel,
            summary=item.summary,
            outcome=item.outcome,
            source_versions=item.source_versions,
            reviewed_by=item.reviewed_by,
            review_status=item.review_status,
            invalidated_reason=item.invalidated_reason,
            created_at=item.created_at,
        )

    return application


app = create_app()
