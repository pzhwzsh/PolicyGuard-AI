"""Persistent at-least-once background jobs with idempotency and bounded retries."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from policyguard.infrastructure.database import BackgroundJobRecord


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    id: str
    job_type: str
    status: str
    payload: dict
    result: dict
    attempts: int
    max_attempts: int
    idempotency_key: str
    available_at: datetime
    error: str | None


def _domain(record: BackgroundJobRecord) -> BackgroundJob:
    return BackgroundJob(
        id=record.id,
        job_type=record.job_type,
        status=record.status,
        payload=record.payload,
        result=record.result or {},
        attempts=record.attempts,
        max_attempts=record.max_attempts,
        idempotency_key=record.idempotency_key,
        available_at=record.available_at,
        error=record.error,
    )


class PersistentJobQueue:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        job_type: str,
        payload: dict,
        *,
        idempotency_key: str,
        max_attempts: int = 3,
    ) -> BackgroundJob:
        existing = self.session.scalar(
            select(BackgroundJobRecord).where(
                BackgroundJobRecord.idempotency_key == idempotency_key
            )
        )
        if existing:
            return _domain(existing)
        now = datetime.now(UTC)
        record = BackgroundJobRecord(
            id=str(uuid4()),
            job_type=job_type,
            status="queued",
            payload=payload,
            result={},
            attempts=0,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.commit()
        return _domain(record)

    def get(self, job_id: str) -> BackgroundJob | None:
        record = self.session.get(BackgroundJobRecord, job_id)
        return _domain(record) if record else None

    def claim(self, allowed_types: set[str] | None = None) -> BackgroundJob | None:
        now = datetime.now(UTC)
        stmt = (
            select(BackgroundJobRecord)
            .where(
                BackgroundJobRecord.status.in_(["queued", "retry"]),
                BackgroundJobRecord.available_at <= now,
            )
            .order_by(BackgroundJobRecord.created_at)
        )
        if allowed_types:
            stmt = stmt.where(BackgroundJobRecord.job_type.in_(allowed_types))
        record = self.session.scalar(stmt.limit(1))
        if record is None:
            return None
        claimed = self.session.execute(
            update(BackgroundJobRecord)
            .where(
                BackgroundJobRecord.id == record.id,
                BackgroundJobRecord.status.in_(["queued", "retry"]),
            )
            .values(
                status="running",
                attempts=BackgroundJobRecord.attempts + 1,
                locked_at=now,
                updated_at=now,
            )
        )
        if claimed.rowcount != 1:
            self.session.rollback()
            return None
        self.session.commit()
        self.session.expire_all()
        return _domain(self._required(record.id))

    def complete(self, job_id: str, result: dict) -> BackgroundJob:
        record = self._required(job_id)
        if record.status != "running":
            raise RuntimeError("job_not_running")
        record.status = "completed"
        record.result = result
        record.error = None
        record.updated_at = datetime.now(UTC)
        self.session.commit()
        return _domain(record)

    def fail(self, job_id: str, error: str, *, retryable: bool = True) -> BackgroundJob:
        record = self._required(job_id)
        if record.status != "running":
            raise RuntimeError("job_not_running")
        now = datetime.now(UTC)
        record.error = error[:2000]
        record.updated_at = now
        if not retryable or record.attempts >= record.max_attempts:
            record.status = "failed"
        else:
            record.status = "retry"
            record.available_at = now + timedelta(seconds=min(2 ** record.attempts, 300))
        self.session.commit()
        return _domain(record)

    def requeue_stale(self, max_running_seconds: int = 900) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=max_running_seconds)
        records = self.session.scalars(
            select(BackgroundJobRecord).where(
                BackgroundJobRecord.status == "running",
                BackgroundJobRecord.locked_at < cutoff,
            )
        ).all()
        now = datetime.now(UTC)
        for record in records:
            record.status = "failed" if record.attempts >= record.max_attempts else "retry"
            record.error = "stale_worker_recovered"
            record.available_at = now
            record.updated_at = now
        self.session.commit()
        return len(records)

    def stats(self) -> dict[str, int]:
        records = self.session.scalars(select(BackgroundJobRecord)).all()
        statuses = {"queued", "running", "retry", "completed", "failed"}
        return {status: sum(record.status == status for record in records) for status in statuses}

    def list(self, limit: int = 100) -> list[BackgroundJob]:
        records = self.session.scalars(
            select(BackgroundJobRecord)
            .order_by(BackgroundJobRecord.created_at.desc())
            .limit(limit)
        ).all()
        return [_domain(record) for record in records]

    def retry(self, job_id: str) -> BackgroundJob:
        record = self._required(job_id)
        if record.status != "failed":
            raise RuntimeError("job_not_failed")
        record.status = "retry"
        record.attempts = 0
        record.error = None
        record.available_at = datetime.now(UTC)
        record.updated_at = datetime.now(UTC)
        self.session.commit()
        return _domain(record)

    def _required(self, job_id: str) -> BackgroundJobRecord:
        record = self.session.get(BackgroundJobRecord, job_id)
        if record is None:
            raise LookupError("job_not_found")
        return record
