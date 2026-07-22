import asyncio
from datetime import UTC, datetime, timedelta

from policyguard.infrastructure.database import Database
from policyguard.infrastructure.telemetry import (
    RuntimeMetric,
    RuntimeMetricBuffer,
    runtime_summary,
)


def test_runtime_metrics_are_batched_and_summarized(tmp_path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'telemetry.db').as_posix()}")
    database.initialize()
    now = datetime.now(UTC)
    buffer = RuntimeMetricBuffer(
        database.session_factory, batch_size=10, flush_interval_seconds=0.01
    )

    async def record_metrics() -> None:
        await buffer.start()
        for index, latency in enumerate((10.0, 20.0, 30.0, 100.0)):
            buffer.record(RuntimeMetric(
                trace_id=f"trace-{index}",
                method="GET",
                path="/api/v1/example",
                status_code=500 if index == 3 else 200,
                duration_ms=latency,
                created_at=now - timedelta(minutes=index),
            ))
        await buffer.stop()

    asyncio.run(record_metrics())
    with database.session_factory() as session:
        report = runtime_summary(session, now=now)

    one_hour = report["windows"]["1h"]
    assert one_hour["request_count"] == 4
    assert one_hour["success_rate"] == 0.75
    assert one_hour["p50_latency_ms"] == 20.0
    assert one_hour["p95_latency_ms"] == 100.0
    assert one_hour["p99_latency_ms"] == 100.0
    assert buffer.dropped == 0
    database.engine.dispose()
