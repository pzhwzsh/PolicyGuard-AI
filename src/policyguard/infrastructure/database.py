from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


class RuleRecord(Base):
    __tablename__ = "compliance_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    pattern: Mapped[str] = mapped_column(String(200))
    target_field: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20))
    suggestion: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CheckRecord(Base):
    __tablename__ = "compliance_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(40))
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    product_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    findings: Mapped[list["FindingRecord"]] = relationship(
        back_populates="check", cascade="all, delete-orphan"
    )


class FindingRecord(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(ForeignKey("compliance_checks.id"), index=True)
    rule_code: Mapped[str] = mapped_column(String(100))
    rule_version: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20))
    field_name: Mapped[str] = mapped_column(String(100))
    matched_text: Mapped[str] = mapped_column(String(500))
    evidence: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    check: Mapped[CheckRecord] = relationship(back_populates="findings")


class PolicyDocumentRecord(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (UniqueConstraint("source_url", "version", name="uq_source_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(1000))
    publisher: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(100))
    published_at: Mapped[str] = mapped_column(String(40))
    retrieved_at: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    chunks: Mapped[list["PolicyChunkRecord"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    scopes: Mapped[list["PolicyScopeRecord"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class PolicyChunkRecord(Base):
    __tablename__ = "policy_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("policy_documents.id"), index=True)
    ordinal: Mapped[int]
    section_id: Mapped[str] = mapped_column(String(100), index=True)
    heading: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    document: Mapped[PolicyDocumentRecord] = relationship(back_populates="chunks")


class PolicyScopeRecord(Base):
    __tablename__ = "policy_scopes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("policy_documents.id"), index=True)
    jurisdiction: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    channel: Mapped[str] = mapped_column(String(100), index=True)
    legal_level: Mapped[str] = mapped_column(String(40))
    source_language: Mapped[str] = mapped_column(String(20))
    translation_status: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[str] = mapped_column(String(40))
    effective_to: Mapped[str | None] = mapped_column(String(40), nullable=True)
    document: Mapped[PolicyDocumentRecord] = relationship(back_populates="scopes")


class EmbeddingRecord(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), primary_key=True)
    model: Mapped[str] = mapped_column(String(200), primary_key=True)
    vector: Mapped[list[float]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    current_step: Mapped[str] = mapped_column(String(100))
    input_payload: Mapped[dict] = mapped_column(JSON)
    result_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    events: Mapped[list["WorkflowEventRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="WorkflowEventRecord.sequence"
    )


class WorkflowEventRecord(Base):
    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    sequence: Mapped[int]
    step: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run: Mapped[WorkflowRunRecord] = relationship(back_populates="events")


class AgentMemoryRecord(Base):
    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(60), index=True)
    jurisdictions: Mapped[list[str]] = mapped_column(JSON)
    category: Mapped[str] = mapped_column(String(100), index=True)
    channel: Mapped[str] = mapped_column(String(100), index=True)
    summary: Mapped[str] = mapped_column(Text)
    outcome: Mapped[dict] = mapped_column(JSON)
    source_versions: Mapped[dict] = mapped_column(JSON)
    reviewed_by: Mapped[str] = mapped_column(String(100))
    review_status: Mapped[str] = mapped_column(String(30), default="confirmed", index=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvaluationReviewRecord(Base):
    __tablename__ = "evaluation_reviews"
    __table_args__ = (UniqueConstraint("dataset", "sample_id", name="uq_evaluation_review_sample"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(200), index=True)
    sample_id: Mapped[str] = mapped_column(String(100), index=True)
    decision: Mapped[str] = mapped_column(String(30), index=True)
    expected_section_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BackgroundJobRecord(Base):
    __tablename__ = "background_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(1000), index=True)
    status_code: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class RuntimeMetricRecord(Base):
    __tablename__ = "runtime_metrics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(500), index=True)
    status_code: Mapped[int] = mapped_column(Integer, index=True)
    duration_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class ResourceOwnershipRecord(Base):
    __tablename__ = "resource_ownership"

    resource_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(30), default="user", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    workflow_uses: Mapped[int] = mapped_column(Integer, default=0)
    workflow_limit: Mapped[int] = mapped_column(Integer, default=10)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class EmailVerificationRecord(Base):
    __tablename__ = "email_verifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    purpose: Mapped[str] = mapped_column(String(30), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class UserSessionRecord(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


DEMO_RULES = (
    {
        "code": "DEMO-ABSOLUTE-001",
        "version": "1.0.0",
        "title": "演示：绝对化表达",
        "pattern": "100%安全",
        "target_field": "title",
        "severity": "high",
        "suggestion": "删除无法客观证明的绝对化承诺，并改为可验证描述。",
    },
    {
        "code": "DEMO-AUTHORITY-001",
        "version": "1.0.0",
        "title": "演示：权威级别表达",
        "pattern": "国家级",
        "target_field": "title",
        "severity": "medium",
        "suggestion": "提供真实资质来源；无法证明时删除该表述。",
    },
)


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine_options = {"connect_args": connect_args, "pool_pre_ping": True}
        if not url.startswith("sqlite"):
            engine_options.update({"pool_size": 10, "max_overflow": 20})
        self.engine = create_engine(url, **engine_options)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session_factory() as session:
            if session.query(RuleRecord).count() == 0:
                session.add_all(
                    RuleRecord(**rule, source_url=None, is_demo=True) for rule in DEMO_RULES
                )
                session.commit()

    def sessions(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session
