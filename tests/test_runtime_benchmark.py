from pathlib import Path

from policyguard.application.runtime_benchmark import evaluate_job_queue


def test_job_runtime_benchmark_proves_idempotency_and_recovery(tmp_path: Path) -> None:
    report = evaluate_job_queue(
        f"sqlite:///{(tmp_path / 'benchmark-jobs.db').as_posix()}", jobs=8, workers=2
    )
    assert report["idempotent_enqueue"] is True
    assert report["duplicate_claim_count"] == 0
    assert report["completed_rate"] == 1.0
    assert report["stale_recovery_pass"] is True
