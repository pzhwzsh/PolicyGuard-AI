from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    task_type: str
    jurisdictions: tuple[str, ...]
    category: str
    channel: str
    query_text: str = ""
    limit: int = 3


@dataclass(slots=True)
class AgentMemory:
    id: str
    run_id: str
    task_type: str
    jurisdictions: tuple[str, ...]
    category: str
    channel: str
    summary: str
    outcome: dict[str, Any]
    source_versions: dict[str, str]
    reviewed_by: str
    review_status: str = "confirmed"
    invalidated_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

