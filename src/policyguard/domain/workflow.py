from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    REVIEW_REQUIRED = "review_required"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    FAILED = "failed"
    REVIEW_ACCEPTED = "review_accepted"
    REVIEW_REJECTED = "review_rejected"
    REMEDIATION_PLANNED = "remediation_planned"
    DRAFT_READY = "draft_ready"


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    sequence: int
    step: str
    status: str
    detail: dict[str, Any]
    created_at: datetime


@dataclass(slots=True)
class WorkflowRun:
    id: str
    status: WorkflowStatus
    current_step: str
    input_payload: dict[str, Any]
    result_payload: dict[str, Any] = field(default_factory=dict)
    events: list[WorkflowEvent] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
