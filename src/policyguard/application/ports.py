from typing import Protocol

from policyguard.domain.models import (
    CheckResult,
    ComplianceRule,
    PolicyChunk,
    PolicyDocument,
    Product,
    KnowledgeFilter,
)
from policyguard.domain.workflow import WorkflowRun
from policyguard.domain.agent_memory import AgentMemory, MemoryQuery


class ComplianceRepository(Protocol):
    def list_active_rules(self) -> list[ComplianceRule]: ...

    def save_check(self, product: Product, result: CheckResult) -> CheckResult: ...

    def get_check(self, check_id: str) -> CheckResult | None: ...


class KnowledgeRepository(Protocol):
    def upsert_document(self, document: PolicyDocument) -> PolicyDocument: ...

    def list_chunks(self, scope: KnowledgeFilter | None = None) -> list[PolicyChunk]: ...

    def document_count(self) -> int: ...

    def load_embeddings(
        self, chunk_ids: list[str], provider: str, model: str
    ) -> dict[str, list[float]]: ...

    def save_embeddings(
        self,
        embeddings: dict[str, list[float]],
        provider: str,
        model: str,
    ) -> None: ...


class WorkflowRepository(Protocol):
    def save(self, run: WorkflowRun) -> WorkflowRun: ...

    def get(self, run_id: str) -> WorkflowRun | None: ...


class AgentMemoryRepository(Protocol):
    def save_confirmed(self, memory: AgentMemory) -> AgentMemory: ...

    def recall(self, query: MemoryQuery) -> list[AgentMemory]: ...

    def invalidate_for_source(self, source_url: str, active_version: str) -> int: ...

    def active_source_versions(self, source_urls: list[str]) -> dict[str, str]: ...
