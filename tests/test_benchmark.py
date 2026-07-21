import json
from pathlib import Path

from policyguard.application.benchmark import (
    TokenEstimate,
    compress_context,
    estimate_cost,
    estimate_tokens,
    run_embedding_benchmark,
)
from policyguard.application.knowledge import ingest_source_directory
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository

ROOT = Path(__file__).parents[1]


class BenchmarkProvider:
    provider_name = "fake"
    model_name = "candidate"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float("truthful" in text.casefold()), float("广告" in text), 1.0] for text in texts
        ]


def test_token_estimator_and_cost_are_explicitly_estimates() -> None:
    assert estimate_tokens("广告 claims must be truthful") > 0
    assert (
        estimate_cost(
            TokenEstimate(1000, 200), input_price_per_million=1, output_price_per_million=2
        )
        == 0.0014
    )


def test_context_compression_deduplicates_and_caps_text() -> None:
    class Chunk:
        def __init__(self, text):
            self.text = text
            self.heading = "h"

    class Hit:
        def __init__(self, text):
            self.chunk = Chunk(text)

    compressed = compress_context(
        [Hit("same text"), Hit("same text"), Hit("other text")], max_chars=15, max_chunks=5
    )
    assert len(compressed) == 2
    assert sum(len(item.chunk.text) for item in compressed) <= 15


def test_embedding_benchmark_runs_same_fixture_for_each_candidate(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'benchmark.db').as_posix()}")
    database.initialize()
    matrix = tmp_path / "models.json"
    matrix.write_text(
        json.dumps(
            {
                "embedding": [
                    {"provider": "fake", "model": "one"},
                    {"provider": "fake", "model": "two"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, ROOT / "data" / "sources")
        results = run_embedding_benchmark(
            repository,
            lambda candidate: BenchmarkProvider(),
            matrix,
            ROOT / "data" / "evaluation" / "rag-baseline.json",
        )
    assert [result.model for result in results] == ["one", "two"]
    assert all(result.status == "ok" for result in results)
    assert all(result.sample_count == 15 for result in results)
