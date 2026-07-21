from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_SCORE = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


@dataclass(frozen=True, slots=True)
class Product:
    external_id: str
    title: str
    description: str
    category: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComplianceRule:
    code: str
    version: str
    title: str
    pattern: str
    target_field: str
    severity: Severity
    suggestion: str
    source_url: str | None
    is_demo: bool


@dataclass(frozen=True, slots=True)
class Finding:
    rule_code: str
    rule_version: str
    severity: Severity
    field_name: str
    matched_text: str
    evidence: str
    suggestion: str
    source_url: str | None


@dataclass(frozen=True, slots=True)
class CheckResult:
    id: str
    external_id: str
    status: str
    risk_level: Severity | None
    findings: tuple[Finding, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PolicySection:
    section_id: str
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class PolicyScope:
    jurisdiction: str
    category: str
    channel: str
    legal_level: str
    source_language: str
    translation_status: str
    effective_from: str
    effective_to: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeFilter:
    jurisdiction: str
    category: str = "all"
    channel: str = "all"
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    id: str
    title: str
    source_url: str
    publisher: str
    version: str
    published_at: str
    retrieved_at: str
    content_hash: str
    sections: tuple[PolicySection, ...]
    scopes: tuple[PolicyScope, ...]


@dataclass(frozen=True, slots=True)
class PolicyChunk:
    id: str
    document_id: str
    document_title: str
    section_id: str
    heading: str
    text: str
    source_url: str
    jurisdiction: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: PolicyChunk
    score: float
    rank: int
