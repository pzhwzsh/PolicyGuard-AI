"""Failure-injection coverage for the most important degradation boundaries."""

from pathlib import Path

from policyguard.application.embeddings import configured_embedding_chain
from policyguard.application.jobs import PersistentJobQueue
from policyguard.application.llm import FallbackClaimExtractor
from policyguard.infrastructure.database import Database


def test_chaos_malformed_primary_model_output_uses_backup() -> None:
    class Extractor:
        provider_name = "injected"

        def __init__(self, model_name: str, broken: bool) -> None:
            self.model_name = model_name
            self.broken = broken

        def extract(self, *, title: str, description: str):
            if self.broken:
                raise ValueError("malformed_json")
            return [{"text": title, "field": "title", "confidence": 0.9}]

    chain = FallbackClaimExtractor(Extractor("primary", True), Extractor("backup", False))
    assert chain.extract(title="claim", description="")[0]["text"] == "claim"
    assert chain.last_model == "backup"


def test_chaos_embedding_initializers_can_all_fail(monkeypatch) -> None:
    from policyguard.application import embeddings

    def fail(_settings):
        raise RuntimeError("injected_model_load_failure")

    monkeypatch.setattr(embeddings, "configured_embedding_provider", fail)
    monkeypatch.setattr(embeddings, "configured_embedding_fallback_provider", fail)
    providers, failures = configured_embedding_chain(object())
    assert providers == []
    assert [item["retriever"] for item in failures] == [
        "primary_embedding", "fallback_embedding"
    ]


def test_chaos_worker_crash_is_recovered_without_duplicate_job(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'chaos.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        queue = PersistentJobQueue(session)
        original = queue.enqueue("model_evaluation", {}, idempotency_key="chaos:worker")
        queue.claim()
        assert queue.requeue_stale(max_running_seconds=-1) == 1
        repeated = queue.enqueue("model_evaluation", {}, idempotency_key="chaos:worker")
        assert repeated.id == original.id
        assert repeated.status == "retry"
