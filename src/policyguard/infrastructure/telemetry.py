import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from policyguard.infrastructure.database import (
    BackgroundJobRecord,
    RuntimeMetricRecord,
    WorkflowEventRecord,
)

FALLBACK_STEPS = {
    "claim_extraction_fallback",
    "retrieval_fallback",
    "query_rewrite_fallback",
    "evidence_support_fallback",
}


@dataclass(frozen=True, slots=True)
class RuntimeMetric:
    trace_id: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    created_at: datetime


class RuntimeMetricBuffer:
    """Bounded, batched persistence so observability does not add one commit per request."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        batch_size: int = 50,
        flush_interval_seconds: float = 0.5,
        queue_size: int = 10_000,
    ) -> None:
        self.session_factory = session_factory
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self.queue: asyncio.Queue[RuntimeMetric | None] = asyncio.Queue(maxsize=queue_size)
        self.task: asyncio.Task | None = None
        self.dropped = 0

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    def record(self, metric: RuntimeMetric) -> None:
        try:
            self.queue.put_nowait(metric)
        except asyncio.QueueFull:
            self.dropped += 1

    async def stop(self) -> None:
        if self.task is None:
            return
        await self.queue.put(None)
        await self.task
        self.task = None

    async def _run(self) -> None:
        batch: list[RuntimeMetric] = []
        stopping = False
        while not stopping:
            try:
                item = await asyncio.wait_for(
                    self.queue.get(), timeout=self.flush_interval_seconds
                )
                if item is None:
                    stopping = True
                else:
                    batch.append(item)
            except TimeoutError:
                pass
            if batch and (stopping or len(batch) >= self.batch_size or self.queue.empty()):
                self._flush(batch)
                batch.clear()

    def _flush(self, batch: list[RuntimeMetric]) -> None:
        with self.session_factory() as session:
            session.add_all(
                RuntimeMetricRecord(
                    trace_id=item.trace_id,
                    method=item.method,
                    path=item.path,
                    status_code=item.status_code,
                    duration_ms=round(item.duration_ms, 3),
                    created_at=item.created_at,
                )
                for item in batch
            )
            session.commit()


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1)
    return round(ordered[index], 3)


def _window_summary(
    metrics: list[RuntimeMetricRecord],
    events: list[WorkflowEventRecord],
    *,
    since: datetime,
    hours: int,
) -> dict:
    selected = [item for item in metrics if _utc(item.created_at) >= since]
    selected_events = [item for item in events if _utc(item.created_at) >= since]
    latencies = [item.duration_ms for item in selected]
    successes = sum(item.status_code < 500 for item in selected)
    token_total = 0
    cache_hits = 0
    cache_misses = 0
    fallback_events = 0
    degraded_events = 0
    failed_events = 0
    model_calls = 0
    provider_attempts = 0
    models: dict[str, dict[str, int]] = {}
    for event in selected_events:
        detail = event.detail or {}
        token_total += int(detail.get("total_tokens", 0) or 0)
        cache_hits += int(detail.get("cache_hits", 0) or 0)
        cache_misses += int(detail.get("cache_misses", 0) or 0)
        fallback_events += int(event.step in FALLBACK_STEPS or detail.get("fallback_used") is True)
        degraded_events += int(event.status == "degraded")
        failed_events += int(event.status == "failed")
        model = str(detail.get("model") or detail.get("selected_model") or "").strip()
        if model:
            model_calls += 1
            provider_attempts += int(detail.get("provider_attempts", 1) or 1)
            bucket = models.setdefault(model, {"calls": 0, "tokens": 0, "fallback_calls": 0})
            bucket["calls"] += 1
            bucket["tokens"] += int(detail.get("total_tokens", 0) or 0)
            bucket["fallback_calls"] += int(detail.get("fallback_used") is True)
    return {
        "hours": hours,
        "request_count": len(selected),
        "success_rate": round(successes / len(selected), 4) if selected else None,
        "mean_latency_ms": round(mean(latencies), 3) if latencies else None,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "p99_latency_ms": _percentile(latencies, 0.99),
        "requests_per_minute": round(len(selected) / max(hours * 60, 1), 3),
        "total_tokens": token_total,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "model_calls": model_calls,
        "provider_attempts": provider_attempts,
        "provider_retry_count": max(provider_attempts - model_calls, 0),
        "provider_retry_rate": round(
            max(provider_attempts - model_calls, 0) / provider_attempts, 4
        ) if provider_attempts else 0.0,
        "fallback_events": fallback_events,
        "fallback_rate": round(fallback_events / model_calls, 4) if model_calls else 0.0,
        "degraded_events": degraded_events,
        "failed_events": failed_events,
        "models": models,
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def runtime_summary(session: Session, *, now: datetime | None = None) -> dict:
    current = now or datetime.now(UTC)
    earliest = current - timedelta(days=7)
    metrics = list(
        session.scalars(
            select(RuntimeMetricRecord)
            .where(RuntimeMetricRecord.created_at >= earliest)
            .order_by(RuntimeMetricRecord.created_at.desc())
            .limit(50_000)
        ).all()
    )
    events = list(
        session.scalars(
            select(WorkflowEventRecord)
            .where(WorkflowEventRecord.created_at >= earliest)
            .order_by(WorkflowEventRecord.created_at.desc())
            .limit(50_000)
        ).all()
    )
    jobs = list(session.scalars(select(BackgroundJobRecord)).all())
    waiting = [item for item in jobs if item.status in {"queued", "retry"}]
    retry_count = sum(max(item.attempts - 1, 0) for item in jobs)
    attempted = sum(item.attempts for item in jobs)
    failures = [item for item in jobs if item.status == "failed"]
    recent_failures = [
        {
            "job_id": item.id,
            "job_type": item.job_type,
            "attempts": item.attempts,
            "error": item.error,
            "updated_at": _utc(item.updated_at).isoformat(),
        }
        for item in sorted(failures, key=lambda value: _utc(value.updated_at), reverse=True)[:10]
    ]
    queue = {
        "depth": len(waiting),
        "oldest_waiting_seconds": round(
            max((current - _utc(item.created_at)).total_seconds() for item in waiting), 1
        ) if waiting else 0.0,
        "retry_count": retry_count,
        "retry_rate": round(retry_count / attempted, 4) if attempted else 0.0,
        "final_failure_count": len(failures),
        "recent_failures": recent_failures,
    }
    windows = {
        "1h": _window_summary(metrics, events, since=current - timedelta(hours=1), hours=1),
        "24h": _window_summary(metrics, events, since=current - timedelta(hours=24), hours=24),
        "7d": _window_summary(metrics, events, since=current - timedelta(days=7), hours=168),
    }
    alerts = []
    if queue["oldest_waiting_seconds"] > 300:
        alerts.append({"severity": "warning", "code": "queue_wait_excessive"})
    if queue["final_failure_count"]:
        alerts.append({"severity": "critical", "code": "background_jobs_failed"})
    if windows["1h"]["fallback_rate"] > 0.25:
        alerts.append({"severity": "warning", "code": "provider_fallback_rate_high"})
    return {
        "generated_at": current.isoformat(),
        "windows": windows,
        "queue": queue,
        "alerts": alerts,
    }


def estimated_model_cost(
    runtime: dict,
    *,
    model_prices_per_million: dict[str, float],
) -> dict:
    models = runtime.get("windows", {}).get("24h", {}).get("models", {})
    estimated = 0.0
    unknown = []
    breakdown = []
    for model, usage in models.items():
        price = model_prices_per_million.get(model)
        if price is None:
            unknown.append(model)
            cost = None
        else:
            cost = usage["tokens"] * price / 1_000_000
            estimated += cost
        breakdown.append({"model": model, **usage, "estimated_cost_usd": cost})
    return {
        "window": "24h",
        "estimated_cost_usd": round(estimated, 6),
        "method": "provider_total_tokens_priced_as_input_tokens",
        "unknown_price_models": unknown,
        "breakdown": breakdown,
    }
