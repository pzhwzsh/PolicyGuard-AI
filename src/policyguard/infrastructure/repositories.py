from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from policyguard.application.ports import ComplianceRepository
from policyguard.domain.models import (
    CheckResult,
    ComplianceRule,
    Finding,
    KnowledgeFilter,
    PolicyChunk,
    PolicyDocument,
    Product,
    Severity,
)
from policyguard.domain.workflow import WorkflowEvent, WorkflowRun, WorkflowStatus
from policyguard.domain.agent_memory import AgentMemory, MemoryQuery
from policyguard.infrastructure.database import (
    CheckRecord,
    EmbeddingRecord,
    FindingRecord,
    PolicyChunkRecord,
    PolicyDocumentRecord,
    PolicyScopeRecord,
    RuleRecord,
    WorkflowEventRecord,
    WorkflowRunRecord,
    AgentMemoryRecord,
)


class SqlAlchemyComplianceRepository(ComplianceRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active_rules(self) -> list[ComplianceRule]:
        records = self.session.scalars(
            select(RuleRecord).where(RuleRecord.active.is_(True)).order_by(RuleRecord.code)
        ).all()
        return [
            ComplianceRule(
                code=record.code,
                version=record.version,
                title=record.title,
                pattern=record.pattern,
                target_field=record.target_field,
                severity=Severity(record.severity),
                suggestion=record.suggestion,
                source_url=record.source_url,
                is_demo=record.is_demo,
            )
            for record in records
        ]

    def save_check(self, product: Product, result: CheckResult) -> CheckResult:
        record = CheckRecord(
            id=result.id,
            external_id=result.external_id,
            status=result.status,
            risk_level=result.risk_level.value if result.risk_level else None,
            product_payload={
                "external_id": product.external_id,
                "title": product.title,
                "description": product.description,
                "category": product.category,
                "attributes": product.attributes,
            },
            created_at=result.created_at,
            findings=[
                FindingRecord(
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
        )
        self.session.add(record)
        self.session.commit()
        return result

    def get_check(self, check_id: str) -> CheckResult | None:
        record = self.session.scalar(
            select(CheckRecord)
            .where(CheckRecord.id == check_id)
            .options(selectinload(CheckRecord.findings))
        )
        if record is None:
            return None
        return CheckResult(
            id=record.id,
            external_id=record.external_id,
            status=record.status,
            risk_level=Severity(record.risk_level) if record.risk_level else None,
            findings=tuple(
                Finding(
                    rule_code=item.rule_code,
                    rule_version=item.rule_version,
                    severity=Severity(item.severity),
                    field_name=item.field_name,
                    matched_text=item.matched_text,
                    evidence=item.evidence,
                    suggestion=item.suggestion,
                    source_url=item.source_url,
                )
                for item in record.findings
            ),
            created_at=record.created_at,
        )


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_document(self, document: PolicyDocument) -> PolicyDocument:
        record = self.session.get(PolicyDocumentRecord, document.id)
        if record is not None and record.content_hash == document.content_hash:
            if not record.scopes:
                record.scopes = [
                    PolicyScopeRecord(
                        jurisdiction=scope.jurisdiction,
                        category=scope.category,
                        channel=scope.channel,
                        legal_level=scope.legal_level,
                        source_language=scope.source_language,
                        translation_status=scope.translation_status,
                        effective_from=scope.effective_from,
                        effective_to=scope.effective_to,
                    )
                    for scope in document.scopes
                ]
                self.session.commit()
            return document
        if record is not None:
            self.session.delete(record)
            self.session.flush()
        self.session.add(
            PolicyDocumentRecord(
                id=document.id,
                title=document.title,
                source_url=document.source_url,
                publisher=document.publisher,
                version=document.version,
                published_at=document.published_at,
                retrieved_at=document.retrieved_at,
                content_hash=document.content_hash,
                active=True,
                chunks=[
                    PolicyChunkRecord(
                        id=sha256(
                            f"{document.id}|{section.section_id}".encode()
                        ).hexdigest(),
                        ordinal=index,
                        section_id=section.section_id,
                        heading=section.heading,
                        text=section.text,
                        content_hash=sha256(section.text.encode("utf-8")).hexdigest(),
                    )
                    for index, section in enumerate(document.sections)
                ],
                scopes=[
                    PolicyScopeRecord(
                        jurisdiction=scope.jurisdiction,
                        category=scope.category,
                        channel=scope.channel,
                        legal_level=scope.legal_level,
                        source_language=scope.source_language,
                        translation_status=scope.translation_status,
                        effective_from=scope.effective_from,
                        effective_to=scope.effective_to,
                    )
                    for scope in document.scopes
                ],
            )
        )
        self.session.commit()
        return document

    def activate_document_version(self, document: PolicyDocument) -> PolicyDocument:
        self.upsert_document(document)
        effective_from = min(
            (scope.effective_from for scope in document.scopes),
            default=document.published_at,
        )
        try:
            previous_effective_to = (
                date.fromisoformat(effective_from) - timedelta(days=1)
            ).isoformat()
        except ValueError:
            previous_effective_to = effective_from
        previous = self.session.scalars(
            select(PolicyDocumentRecord).where(
                PolicyDocumentRecord.source_url == document.source_url,
                PolicyDocumentRecord.id != document.id,
                PolicyDocumentRecord.active.is_(True),
            )
        ).all()
        for record in previous:
            record.active = False
            for scope in record.scopes:
                if scope.effective_to is None:
                    scope.effective_to = previous_effective_to
        current = self.session.get(PolicyDocumentRecord, document.id)
        if current:
            current.active = True
        self.session.commit()
        SqlAlchemyAgentMemoryRepository(self.session).invalidate_for_source(
            document.source_url, document.version
        )
        return document

    def list_chunks(self, scope: KnowledgeFilter | None = None) -> list[PolicyChunk]:
        stmt = (
            select(PolicyChunkRecord)
            .join(PolicyDocumentRecord)
            .options(selectinload(PolicyChunkRecord.document))
            .order_by(PolicyDocumentRecord.id, PolicyChunkRecord.ordinal)
        )
        if scope is None or scope.as_of is None:
            stmt = stmt.where(PolicyDocumentRecord.active.is_(True))
        if scope is not None:
            stmt = stmt.join(PolicyScopeRecord).where(
                PolicyScopeRecord.jurisdiction == scope.jurisdiction,
                PolicyScopeRecord.category.in_([scope.category, "all"]),
                PolicyScopeRecord.channel.in_([scope.channel, "all"]),
            )
            if scope.as_of:
                stmt = stmt.where(
                    PolicyScopeRecord.effective_from <= scope.as_of,
                    (PolicyScopeRecord.effective_to.is_(None))
                    | (PolicyScopeRecord.effective_to >= scope.as_of),
                )
            stmt = stmt.distinct()
        records = self.session.scalars(stmt).all()
        return [
            PolicyChunk(
                id=record.id,
                document_id=record.document_id,
                document_title=record.document.title,
                section_id=record.section_id,
                heading=record.heading,
                text=record.text,
                source_url=record.document.source_url,
                jurisdiction=(
                    scope.jurisdiction if scope is not None else "unspecified"
                ),
            )
            for record in records
        ]

    def document_count(self) -> int:
        return len(
            self.session.scalars(
                select(PolicyDocumentRecord.id).where(PolicyDocumentRecord.active.is_(True))
            ).all()
        )

    def load_embeddings(
        self, chunk_ids: list[str], provider: str, model: str
    ) -> dict[str, list[float]]:
        if not chunk_ids:
            return {}
        rows = self.session.scalars(
            select(EmbeddingRecord).where(
                EmbeddingRecord.chunk_id.in_(chunk_ids),
                EmbeddingRecord.provider == provider,
                EmbeddingRecord.model == model,
            )
        ).all()
        return {row.chunk_id: row.vector for row in rows}

    def save_embeddings(
        self,
        embeddings: dict[str, list[float]],
        provider: str,
        model: str,
    ) -> None:
        for chunk_id, vector in embeddings.items():
            record = self.session.get(
                EmbeddingRecord,
                {"chunk_id": chunk_id, "provider": provider, "model": model},
            )
            if record is None:
                self.session.add(
                    EmbeddingRecord(
                        chunk_id=chunk_id,
                        provider=provider,
                        model=model,
                        vector=vector,
                    )
                )
            else:
                record.vector = vector
                record.updated_at = datetime.now(UTC)
        self.session.commit()


class SqlAlchemyWorkflowRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, run: WorkflowRun) -> WorkflowRun:
        record = self.session.get(WorkflowRunRecord, run.id)
        if record is None:
            record = WorkflowRunRecord(
                id=run.id,
                status=run.status.value,
                current_step=run.current_step,
                input_payload=run.input_payload,
                result_payload=run.result_payload,
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
            self.session.add(record)
        else:
            record.status = run.status.value
            record.current_step = run.current_step
            record.result_payload = run.result_payload
            record.updated_at = run.updated_at
        existing_sequences = {event.sequence for event in record.events}
        record.events.extend(
            WorkflowEventRecord(
                sequence=event.sequence,
                step=event.step,
                status=event.status,
                detail=event.detail,
                created_at=event.created_at,
            )
            for event in run.events
            if event.sequence not in existing_sequences
        )
        self.session.commit()
        return run

    def list_recent(self, limit: int = 50) -> list[WorkflowRun]:
        records = self.session.scalars(
            select(WorkflowRunRecord)
            .options(selectinload(WorkflowRunRecord.events))
            .order_by(WorkflowRunRecord.created_at.desc())
            .limit(limit)
        ).all()
        return [self._domain(record) for record in records]

    @staticmethod
    def _domain(record: WorkflowRunRecord) -> WorkflowRun:
        return WorkflowRun(
            id=record.id,
            status=WorkflowStatus(record.status),
            current_step=record.current_step,
            input_payload=record.input_payload,
            result_payload=record.result_payload,
            events=[
                WorkflowEvent(
                    sequence=event.sequence,
                    step=event.step,
                    status=event.status,
                    detail=event.detail,
                    created_at=event.created_at,
                )
                for event in record.events
            ],
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def get(self, run_id: str) -> WorkflowRun | None:
        record = self.session.get(WorkflowRunRecord, run_id)
        if record is None:
            return None
        return self._domain(record)


class SqlAlchemyAgentMemoryRepository:
    """Reviewed-only episodic memory; retrieval is filtered before lexical scoring."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_confirmed(self, memory: AgentMemory) -> AgentMemory:
        if memory.review_status != "confirmed" or not memory.reviewed_by:
            raise ValueError("agent_memory_requires_human_confirmation")
        record = self.session.get(AgentMemoryRecord, memory.id)
        if record is None:
            record = AgentMemoryRecord(id=memory.id, run_id=memory.run_id)
            self.session.add(record)
        record.task_type = memory.task_type
        record.jurisdictions = list(memory.jurisdictions)
        record.category = memory.category
        record.channel = memory.channel
        record.summary = memory.summary
        record.outcome = memory.outcome
        record.source_versions = memory.source_versions
        record.reviewed_by = memory.reviewed_by
        record.review_status = memory.review_status
        record.invalidated_reason = memory.invalidated_reason
        record.created_at = memory.created_at
        self.session.commit()
        return memory

    def recall(self, query: MemoryQuery) -> list[AgentMemory]:
        candidates = self.session.scalars(
            select(AgentMemoryRecord).where(
                AgentMemoryRecord.task_type == query.task_type,
                AgentMemoryRecord.review_status == "confirmed",
                AgentMemoryRecord.invalidated_reason.is_(None),
                AgentMemoryRecord.category.in_([query.category, "all"]),
                AgentMemoryRecord.channel.in_([query.channel, "all"]),
            ).order_by(AgentMemoryRecord.created_at.desc()).limit(50)
        ).all()
        wanted = set(query.jurisdictions)
        terms = {term.lower() for term in query.query_text.split() if len(term) > 1}
        ranked = []
        for record in candidates:
            overlap = wanted.intersection(record.jurisdictions)
            if wanted and not overlap:
                continue
            haystack = f"{record.summary} {record.outcome}".lower()
            lexical = sum(term in haystack for term in terms)
            ranked.append((len(overlap), lexical, record.created_at, record))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [self._domain(item[3]) for item in ranked[: query.limit]]

    def invalidate_for_source(self, source_url: str, active_version: str) -> int:
        records = self.session.scalars(
            select(AgentMemoryRecord).where(
                AgentMemoryRecord.review_status == "confirmed",
                AgentMemoryRecord.invalidated_reason.is_(None),
            )
        ).all()
        changed = 0
        for record in records:
            remembered = record.source_versions.get(source_url)
            if remembered is not None and remembered != active_version:
                record.invalidated_reason = (
                    f"source_version_changed:{source_url}:{remembered}->{active_version}"
                )
                record.review_status = "invalidated"
                changed += 1
        self.session.commit()
        return changed

    def active_source_versions(self, source_urls: list[str]) -> dict[str, str]:
        if not source_urls:
            return {}
        records = self.session.scalars(
            select(PolicyDocumentRecord).where(
                PolicyDocumentRecord.source_url.in_(source_urls),
                PolicyDocumentRecord.active.is_(True),
            )
        ).all()
        return {record.source_url: record.version for record in records}

    def list_recent(self, limit: int = 50) -> list[AgentMemory]:
        records = self.session.scalars(
            select(AgentMemoryRecord)
            .order_by(AgentMemoryRecord.created_at.desc())
            .limit(limit)
        ).all()
        return [self._domain(record) for record in records]

    def review(
        self, memory_id: str, reviewer: str, decision: str, comment: str
    ) -> AgentMemory:
        record = self.session.get(AgentMemoryRecord, memory_id)
        if record is None:
            raise LookupError("agent_memory_not_found")
        if decision == "invalidate":
            record.review_status = "invalidated"
            record.invalidated_reason = f"human_review:{comment or 'no_comment'}"
        elif decision == "confirm":
            urls = list(record.source_versions)
            current = self.active_source_versions(urls)
            if len(current) != len(urls):
                raise RuntimeError("agent_memory_source_unavailable")
            record.source_versions = current
            record.review_status = "confirmed"
            record.invalidated_reason = None
        else:
            raise ValueError("agent_memory_invalid_review_decision")
        record.reviewed_by = reviewer
        self.session.commit()
        return self._domain(record)

    @staticmethod
    def _domain(record: AgentMemoryRecord) -> AgentMemory:
        return AgentMemory(
            id=record.id, run_id=record.run_id, task_type=record.task_type,
            jurisdictions=tuple(record.jurisdictions), category=record.category,
            channel=record.channel, summary=record.summary, outcome=record.outcome,
            source_versions=record.source_versions, reviewed_by=record.reviewed_by,
            review_status=record.review_status, invalidated_reason=record.invalidated_reason,
            created_at=record.created_at,
        )
