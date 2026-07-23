"""Runtime evidence for queue idempotency, concurrent claims, and stale recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

from policyguard.application.jobs import PersistentJobQueue
from policyguard.infrastructure.database import Database


def evaluate_job_queue(database_url: str, *, jobs: int = 100, workers: int = 8) -> dict:
    if "benchmark" not in database_url.casefold():
        raise ValueError("benchmark_database_url_must_contain_benchmark")
    database = Database(database_url)
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        created = [
            queue.enqueue("benchmark", {"index": i}, idempotency_key=f"benchmark:{i}")
            for i in range(jobs)
        ]
        duplicates = [
            queue.enqueue("benchmark", {"index": i}, idempotency_key=f"benchmark:{i}")
            for i in range(jobs)
        ]
    idempotency_pass = [item.id for item in created] == [item.id for item in duplicates]

    def claim_and_complete(_: int) -> tuple[str | None, str | None]:
        try:
            with database.session_factory() as session:
                queue = PersistentJobQueue(session)
                job = queue.claim({"benchmark"})
                if job is None:
                    return None, None
                queue.complete(job.id, {"ok": True})
                return job.id, None
        except Exception as exc:  # contention is benchmark output, not hidden
            return None, type(exc).__name__

    started = perf_counter()
    claimed: list[str] = []
    errors: list[str] = []
    while len(claimed) < jobs:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            batch = list(executor.map(claim_and_complete, range(workers)))
        claimed.extend(job_id for job_id, _ in batch if job_id)
        errors.extend(error for _, error in batch if error)
        if not any(job_id for job_id, _ in batch):
            break
    elapsed = perf_counter() - started

    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        stale = queue.enqueue("stale", {}, idempotency_key="benchmark:stale")
        queue.claim({"stale"})
        recovered_count = queue.requeue_stale(max_running_seconds=-1)
        recovered = queue.get(stale.id)
    database.engine.dispose()
    return {
        "traffic_type": "synthetic_deterministic_not_production",
        "backend": database_url.split(":", 1)[0],
        "job_count": jobs,
        "workers": workers,
        "unique_claim_count": len(set(claimed)),
        "duplicate_claim_count": len(claimed) - len(set(claimed)),
        "completed_rate": round(len(set(claimed)) / max(jobs, 1), 4),
        "idempotent_enqueue": idempotency_pass,
        "stale_recovery_pass": recovered_count == 1 and recovered is not None
        and recovered.status == "retry",
        "wall_time_seconds": round(elapsed, 3),
        "throughput_jobs_per_second": round(len(set(claimed)) / max(elapsed, 0.000001), 2),
        "contention_errors": dict((name, errors.count(name)) for name in sorted(set(errors))),
    }
