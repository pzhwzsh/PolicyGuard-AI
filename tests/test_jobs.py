from pathlib import Path

from policyguard.application.jobs import PersistentJobQueue
from policyguard.infrastructure.database import Database


def test_job_queue_is_idempotent_and_completes(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        first = queue.enqueue("parse_document", {"path": "x.pdf"}, idempotency_key="parse:x")
        repeated = queue.enqueue("parse_document", {"path": "x.pdf"}, idempotency_key="parse:x")
        assert repeated.id == first.id
        claimed = queue.claim({"parse_document"})
        assert claimed and claimed.id == first.id and claimed.attempts == 1
        completed = queue.complete(first.id, {"document_id": "doc-1"})
        assert completed.status == "completed"
        assert completed.result["document_id"] == "doc-1"


def test_job_queue_retries_then_moves_to_failed(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'retry.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        job = queue.enqueue("parse_document", {}, idempotency_key="parse:y", max_attempts=1)
        queue.claim()
        failed = queue.fail(job.id, "parser failed")
        assert failed.status == "failed"
        assert failed.error == "parser failed"


def test_job_queue_does_not_retry_deterministic_failure(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'deterministic.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        job = queue.enqueue("parse_document", {}, idempotency_key="invalid", max_attempts=5)
        queue.claim()
        failed = queue.fail(job.id, "invalid input", retryable=False)

        assert failed.status == "failed"
        assert failed.attempts == 1


def test_job_queue_recovers_stale_running_job(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'stale.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        job = queue.enqueue("parse_document", {}, idempotency_key="parse:stale")
        queue.claim()
        assert queue.requeue_stale(max_running_seconds=-1) == 1
        recovered = queue.get(job.id)
        assert recovered and recovered.status == "retry"
        assert recovered.error == "stale_worker_recovered"


def test_failed_job_can_be_manually_retried(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'manual-retry.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        job = queue.enqueue("parse_document", {}, idempotency_key="manual", max_attempts=1)
        queue.claim()
        queue.fail(job.id, "failure")
        retried = queue.retry(job.id)
        assert retried.status == "retry"
        assert retried.attempts == 0
